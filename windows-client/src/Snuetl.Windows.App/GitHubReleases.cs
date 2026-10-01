using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Snuetl.Windows;

public sealed record ReleaseDownload(string Version, string Name, string Url, long Size, string Sha256, string? ApiUrl = null);

public sealed class GitHubReleases(string repository, HttpClient? client = null, string? token = null) : IDisposable
{
    public const string DefaultRepository = "euro-kim/snuetl";
    private readonly HttpClient http = client ?? new() { Timeout = TimeSpan.FromMinutes(20) };
    private readonly bool ownsClient = client is null;
    internal TimeSpan DownloadIdleTimeout { get; init; } = TimeSpan.FromSeconds(45);
    private string Repository => Regex.IsMatch(repository, @"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") ? repository : throw new IOException("Use a GitHub owner/repository name.");
    public static Version? ParseVersion(string value)
    {
        var match = Regex.Match(value, @"(?:^|[^0-9])v?(\d+\.\d+\.\d+)(?:[^0-9]|$)");
        return match.Success && Version.TryParse(match.Groups[1].Value, out var version) ? version : null;
    }
    private async Task<HttpResponseMessage> GetAsync(string url, CancellationToken ct, bool binary = false)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get,url);
        if (Uri.TryCreate(url,UriKind.Absolute,out var uri) && uri.Host == "api.github.com" && uri.Scheme == "https" && uri.AbsolutePath.StartsWith($"/repos/{Repository}/",StringComparison.OrdinalIgnoreCase))
        {
            if (!string.IsNullOrEmpty(token)) request.Headers.Authorization = new("Bearer",token);
            request.Headers.Accept.ParseAdd(binary ? "application/octet-stream" : "application/vnd.github+json");
            request.Headers.Add("X-GitHub-Api-Version","2022-11-28");
        }
        request.Headers.UserAgent.ParseAdd("SNUETL-Windows/0.9.6");
        var response = await http.SendAsync(request,HttpCompletionOption.ResponseHeadersRead,ct);
        if (response.StatusCode == HttpStatusCode.NotFound)
        { response.Dispose(); throw new IOException($"GitHub releases for {Repository} are unavailable. For a private repository, save a GitHub token with Contents: read access in Settings."); }
        if (response.StatusCode is HttpStatusCode.Forbidden or HttpStatusCode.TooManyRequests)
        { response.Dispose(); throw new IOException("GitHub is limiting update checks. Please try again later."); }
        if (response.StatusCode == HttpStatusCode.Unauthorized) { response.Dispose(); throw new IOException("GitHub rejected the release-access token. Update it in Settings."); }
        response.EnsureSuccessStatusCode();
        return response;
    }
    private bool OwnedUrl(string url) => Uri.TryCreate(url,UriKind.Absolute,out var uri) && uri.Scheme == "https" && uri.Host == "github.com" && uri.AbsolutePath.StartsWith($"/{Repository}/releases/download/",StringComparison.OrdinalIgnoreCase);
    public async Task<ReleaseDownload?> FindAsync(bool addon, CancellationToken ct = default)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromSeconds(45)); ct = timeout.Token;
        var candidates = new List<(Version Version, JsonElement Asset, JsonElement[] Assets)>();
        // Search older releases too: core-only releases must not hide a reusable ZIP.
        for (var page = 1; page <= 20; page++)
        {
            using var response = await GetAsync($"https://api.github.com/repos/{Repository}/releases?per_page=100&page={page}",ct);
            using var json = await JsonDocument.ParseAsync(await response.Content.ReadAsStreamAsync(ct),cancellationToken:ct);
            var releases = json.RootElement.EnumerateArray().ToArray();
            foreach (var release in releases)
            {
                if (release.GetProperty("draft").GetBoolean() || release.GetProperty("prerelease").GetBoolean()) continue;
                var tag = release.GetProperty("tag_name").GetString() ?? "";
                var version = ParseVersion(tag);
                var assets = release.GetProperty("assets").EnumerateArray().ToArray();
                var matches = assets.Where(a => IsAsset(a.GetProperty("name").GetString() ?? "",addon)).OrderByDescending(a => a.GetProperty("name").GetString() == "SNUETLSetup.exe");
                foreach (var asset in matches)
                {
                    var name = asset.GetProperty("name").GetString()!;
                    var foundVersion = ParseVersion(name) ?? version;
                    if (foundVersion is null) continue;
                    candidates.Add((foundVersion, asset.Clone(), assets.Select(a => a.Clone()).ToArray()));
                }
            }
            if (releases.Length < 100) break;
        }
        // GitHub's release order is not a semantic version order. Validate only
        // the selected asset so a historical release without hashes cannot block it.
        if (candidates.Count == 0) return null;
        var selected = candidates.OrderByDescending(c => c.Version).First();
        var chosen = selected.Asset;
        var chosenName = chosen.GetProperty("name").GetString()!;
        var url = chosen.GetProperty("browser_download_url").GetString() ?? "";
        if (!OwnedUrl(url)) throw new IOException("The release asset does not belong to the configured GitHub repository.");
        var digest = chosen.TryGetProperty("digest",out var d) ? d.GetString() : null;
        var hash = digest?.StartsWith("sha256:",StringComparison.OrdinalIgnoreCase) == true ? digest[7..] : await ReadChecksumAsync(selected.Assets,chosenName,ct);
        if (hash is null || !Regex.IsMatch(hash,@"\A[0-9a-fA-F]{64}\z")) throw new IOException($"The release is missing a SHA-256 digest for {chosenName}. Upload SHA256SUMS.txt or an asset with a GitHub digest.");
        return new(selected.Version.ToString(3),chosenName,url,chosen.GetProperty("size").GetInt64(),hash, chosen.TryGetProperty("url",out var api) ? api.GetString() : null);
    }
    private bool OwnedApi(string? url) => url is not null && Uri.TryCreate(url,UriKind.Absolute,out var uri) && uri.Scheme == "https" && uri.Host == "api.github.com" && uri.AbsolutePath.StartsWith($"/repos/{Repository}/releases/assets/",StringComparison.OrdinalIgnoreCase);
    public static bool IsAsset(string name, bool addon) => addon
        ? Regex.IsMatch(name,@"\ASNUETL-SignIn-\d+\.\d+\.\d+-win-x64\.zip\z",RegexOptions.IgnoreCase)
        : Regex.IsMatch(name,@"\ASNUETLSetup(?:-\d+\.\d+\.\d+)?\.exe\z",RegexOptions.IgnoreCase);
    private async Task<string?> ReadChecksumAsync(JsonElement[] assets,string name,CancellationToken ct)
    {
        var checksum = assets.FirstOrDefault(a => a.GetProperty("name").GetString() == "SHA256SUMS.txt");
        if (checksum.ValueKind == JsonValueKind.Undefined) return null;
        var url = checksum.GetProperty("browser_download_url").GetString() ?? "";
        if (!OwnedUrl(url) || checksum.GetProperty("size").GetInt64() > 1024*1024) return null;
        var apiUrl = checksum.TryGetProperty("url",out var api) ? api.GetString() : null;
        using var response = await GetAsync(!string.IsNullOrEmpty(token) && OwnedApi(apiUrl) ? apiUrl! : url,ct,binary:true);
        var text = await response.Content.ReadAsStringAsync(ct);
        foreach (var line in text.Split('\n'))
        {
            var parts = line.Trim().Split((char[]?)null,2,StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 2 && parts[1].TrimStart('*').Equals(name,StringComparison.Ordinal)) return parts[0];
        }
        return null;
    }
    public async Task DownloadAsync(ReleaseDownload asset,string target,IProgress<double>? progress = null,CancellationToken ct = default)
    {
        if (!OwnedUrl(asset.Url) || asset.Size <= 0 || !Regex.IsMatch(asset.Sha256,@"\A[0-9a-fA-F]{64}\z")) throw new IOException("Invalid release asset metadata.");
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(target))!);
        var temp = target + ".partial";
        using var idle = CancellationTokenSource.CreateLinkedTokenSource(ct);
        try
        {
            idle.CancelAfter(DownloadIdleTimeout);
            using var response = await GetAsync(!string.IsNullOrEmpty(token) && OwnedApi(asset.ApiUrl) ? asset.ApiUrl! : asset.Url,idle.Token,binary:true).ConfigureAwait(false);
            await using (var input = await response.Content.ReadAsStreamAsync(ct))
            await using (var output = File.Create(temp))
            {
                var buffer = new byte[131072]; long length = 0; int read;
                var updates = System.Diagnostics.Stopwatch.StartNew();
                while (true)
                {
                    idle.CancelAfter(DownloadIdleTimeout);
                    read = await input.ReadAsync(buffer,idle.Token).ConfigureAwait(false);
                    idle.CancelAfter(Timeout.InfiniteTimeSpan);
                    if (read == 0) break;
                    length += read;
                    if (length > asset.Size) throw new IOException("The download size does not match GitHub's release metadata.");
                    await output.WriteAsync(buffer.AsMemory(0,read),ct).ConfigureAwait(false);
                    if (updates.ElapsedMilliseconds >= 100 || length == asset.Size)
                    { progress?.Report((double)length / asset.Size); updates.Restart(); }
                }
                if (length != asset.Size) throw new IOException("The download was interrupted. Please try again.");
            }
            await using (var stream = File.OpenRead(temp))
                if (!Convert.ToHexString(await SHA256.HashDataAsync(stream,ct)).Equals(asset.Sha256,StringComparison.OrdinalIgnoreCase)) throw new IOException("The download failed its SHA-256 integrity check.");
            File.Move(temp,target,true);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested && idle.IsCancellationRequested)
        { throw new IOException("The GitHub download stopped responding for 45 seconds. Check your connection and try again."); }
        finally { if (File.Exists(temp)) File.Delete(temp); }
    }
    public void Dispose() { if (ownsClient) http.Dispose(); }
}
