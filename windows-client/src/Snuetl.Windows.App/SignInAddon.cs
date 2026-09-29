using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;
namespace Snuetl.Windows;

internal static class SignInAddon
{
    internal sealed record Descriptor(string Version, string Url, string Sha256, long DownloadBytes, long InstalledBytes);
    private static readonly SemaphoreSlim Gate = new(1,1);
    private static string Parent => Path.Combine(new SettingsStore().DataDirectory, "addons");
    private static string Target => Path.Combine(Parent,"signin");
    internal static bool Installed => File.Exists(Path.Combine(Target,"snuetl-signin.exe"));
    internal static Descriptor? Manifest
    {
        get { try { return JsonSerializer.Deserialize<Descriptor>(File.ReadAllText(Path.Combine(AppContext.BaseDirectory,"signin-component.json")), new JsonSerializerOptions { PropertyNameCaseInsensitive = true }); } catch (Exception e) when (e is IOException or JsonException) { return null; } }
    }
    internal static string Description => Manifest is { } m ? $"Optional automatic sign-in · {m.DownloadBytes / 1048576.0:F1} MB download · {m.InstalledBytes / 1048576.0:F1} MB installed. Manual tokens need no add-on." : "Automatic sign-in is optional. Component download information is not available in this build.";
    internal static Task InstallAsync(string? archive = null, string? dataDirectory = null, Descriptor? component = null,
        IProgress<LoginProgress>? progress = null, CancellationToken ct = default) =>
        Task.Run(() => InstallCoreAsync(archive, dataDirectory, component, progress, ct), ct);

    private static async Task InstallCoreAsync(string? archive, string? dataDirectory, Descriptor? component,
        IProgress<LoginProgress>? progress, CancellationToken ct)
    {
        progress?.Report(new("Waiting for automatic-login installation…"));
        await Gate.WaitAsync(ct);
        var parent = dataDirectory is null ? Parent : Path.Combine(dataDirectory,"addons");
        var target = Path.Combine(parent,"signin");
        var stage = Path.Combine(parent,"staging-" + Guid.NewGuid().ToString("N"));
        var download = stage + ".zip";
        try
        {
            Descriptor descriptor;
            Directory.CreateDirectory(parent);
            if (archive is null)
            {
                progress?.Report(new("Finding the latest automatic-login package on GitHub…"));
                var settings = new SettingsStore().Load();
                using var github = new GitHubReleases(settings.ReleaseRepository,token:GitHubCredential.Read());
                var asset = await github.FindAsync(true,ct) ?? throw new IOException("No SNUETL sign-in ZIP was found in GitHub releases. Manual-token setup is still available.");
                descriptor = new(asset.Version,asset.Url,asset.Sha256,asset.Size,0);
                var installedHash = Path.Combine(target,"component-sha256.txt");
                if (File.Exists(Path.Combine(target,"snuetl-signin.exe")) && File.Exists(installedHash)
                    && (await File.ReadAllTextAsync(installedHash)).Trim().Equals(asset.Sha256,StringComparison.OrdinalIgnoreCase)) return;
                progress?.Report(new($"Downloading automatic login · 0 / {asset.Size / 1048576.0:F1} MB. The browser opens after installation.",0));
                await github.DownloadAsync(asset,download,new InlineProgress<double>(fraction => progress?.Report(new(
                    $"Downloading automatic login · {fraction:P0} · {asset.Size * fraction / 1048576.0:F1} / {asset.Size / 1048576.0:F1} MB",fraction))),ct);
                archive = download;
            }
            else descriptor = component ?? Manifest ?? throw new IOException("Component metadata is unavailable. Download the latest sign-in ZIP from GitHub using this client.");
            progress?.Report(new("Verifying the automatic-login package…"));
            await using (var stream = File.OpenRead(archive))
            {
                var hash = Convert.ToHexString(await SHA256.HashDataAsync(stream,ct));
                if (stream.Length != descriptor.DownloadBytes || !hash.Equals(descriptor.Sha256,StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Component integrity check failed. The core client is still available.");
            }
            progress?.Report(new("Installing the sign-in browser…",0));
            await ExtractAsync(archive,stage,progress,ct);
            if (!File.Exists(Path.Combine(stage,"snuetl-signin.exe"))) throw new IOException("The downloaded component is incomplete.");
            await File.WriteAllTextAsync(Path.Combine(stage,"component-version.txt"),descriptor.Version);
            await File.WriteAllTextAsync(Path.Combine(stage,"component-sha256.txt"),descriptor.Sha256);
            ct.ThrowIfCancellationRequested();
            progress?.Report(new("Finishing automatic-login installation…"));
            var old = target + ".old";
            DeleteOwned(old,parent);
            if (Directory.Exists(target)) Directory.Move(target,old);
            try { Directory.Move(stage,target); }
            catch { if (Directory.Exists(old)) Directory.Move(old,target); throw; }
            DeleteOwned(old,parent);
        }
        finally
        {
            try { DeleteOwned(stage,parent); if (File.Exists(download)) File.Delete(download); }
            finally { Gate.Release(); }
        }
    }
    private static async Task ExtractAsync(string archive, string stage, IProgress<LoginProgress>? progress, CancellationToken ct)
    {
        using var zip = ZipFile.OpenRead(archive);
        var total = zip.Entries.Sum(e => e.Length);
        long installed = 0;
        var updates = System.Diagnostics.Stopwatch.StartNew();
        var root = Path.GetFullPath(stage) + Path.DirectorySeparatorChar;
        Directory.CreateDirectory(stage);
        foreach (var entry in zip.Entries)
        {
            ct.ThrowIfCancellationRequested();
            var destination = Path.GetFullPath(Path.Combine(stage,entry.FullName));
            if (!destination.StartsWith(root,StringComparison.OrdinalIgnoreCase)) throw new IOException("The sign-in ZIP contains an invalid path.");
            if (entry.FullName.EndsWith('/') || entry.FullName.EndsWith('\\')) { Directory.CreateDirectory(destination); continue; }
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            await using var input = entry.Open();
            await using var output = new FileStream(destination,FileMode.CreateNew,FileAccess.Write,FileShare.None,131072,true);
            var buffer = new byte[131072]; int read;
            while ((read = await input.ReadAsync(buffer,ct)) > 0)
            {
                await output.WriteAsync(buffer.AsMemory(0,read),ct);
                installed += read;
                if (updates.ElapsedMilliseconds >= 100 || installed == total)
                {
                    var fraction = total > 0 ? (double)installed / total : 1;
                    progress?.Report(new($"Installing the sign-in browser · {fraction:P0}",fraction));
                    updates.Restart();
                }
            }
        }
    }
    internal static Task RemoveAsync() => RemoveAtAsync(new SettingsStore().DataDirectory);
    internal static async Task<bool> RemoveAfterLoginAsync(string dataDirectory)
    {
        // The browser process has exited and the saved token has been verified.
        // Cleanup must neither block WPF nor turn a successful login into a failure.
        try { await Task.Run(() => RemoveAtAsync(dataDirectory)); return true; }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { return false; }
    }
    internal static async Task RemoveAtAsync(string dataDirectory)
    {
        await Gate.WaitAsync();
        try { var parent = Path.Combine(dataDirectory,"addons"); DeleteOwned(Path.Combine(parent,"signin"),parent); } finally { Gate.Release(); }
    }
    private static void DeleteOwned(string path, string parent)
    {
        var full = Path.GetFullPath(path);
        if (!full.StartsWith(Path.GetFullPath(parent) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new IOException("Invalid component directory.");
        if (Directory.Exists(full)) Directory.Delete(full,true);
    }
}
