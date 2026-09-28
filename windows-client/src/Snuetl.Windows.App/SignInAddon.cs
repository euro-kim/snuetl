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
    internal static async Task InstallAsync(string? archive = null, string? dataDirectory = null, Descriptor? component = null)
    {
        await Gate.WaitAsync();
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
                var settings = new SettingsStore().Load();
                using var github = new GitHubReleases(settings.ReleaseRepository,token:GitHubCredential.Read());
                var asset = await github.FindAsync(true) ?? throw new IOException("No SNUETL sign-in ZIP was found in GitHub releases. Manual-token setup is still available.");
                descriptor = new(asset.Version,asset.Url,asset.Sha256,asset.Size,0);
                var installedHash = Path.Combine(target,"component-sha256.txt");
                if (File.Exists(Path.Combine(target,"snuetl-signin.exe")) && File.Exists(installedHash)
                    && (await File.ReadAllTextAsync(installedHash)).Trim().Equals(asset.Sha256,StringComparison.OrdinalIgnoreCase)) return;
                await github.DownloadAsync(asset,download);
                archive = download;
            }
            else descriptor = component ?? Manifest ?? throw new IOException("Component metadata is unavailable. Download the latest sign-in ZIP from GitHub using this client.");
            await using (var stream = File.OpenRead(archive))
            {
                var hash = Convert.ToHexString(await SHA256.HashDataAsync(stream));
                if (stream.Length != descriptor.DownloadBytes || !hash.Equals(descriptor.Sha256,StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Component integrity check failed. The core client is still available.");
            }
            await Task.Run(() => ZipFile.ExtractToDirectory(archive,stage));
            if (!File.Exists(Path.Combine(stage,"snuetl-signin.exe"))) throw new IOException("The downloaded component is incomplete.");
            await File.WriteAllTextAsync(Path.Combine(stage,"component-version.txt"),descriptor.Version);
            await File.WriteAllTextAsync(Path.Combine(stage,"component-sha256.txt"),descriptor.Sha256);
            var old = target + ".old";
            DeleteOwned(old,parent);
            if (Directory.Exists(target)) Directory.Move(target,old);
            try { Directory.Move(stage,target); }
            catch { if (Directory.Exists(old)) Directory.Move(old,target); throw; }
            DeleteOwned(old,parent);
        }
        finally { DeleteOwned(stage,parent); if (File.Exists(download)) File.Delete(download); Gate.Release(); }
    }
    internal static Task RemoveAsync() => RemoveAtAsync(new SettingsStore().DataDirectory);
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
