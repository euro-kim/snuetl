using System.Text.Json;
using Microsoft.Win32;

namespace Snuetl.Windows;

public sealed class SettingsStore
{
    private const string StartupKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    private readonly bool configureStartup;
    private bool? lastStartup;
    private readonly object saveLock = new();
    public string DataDirectory { get; }
    public SettingsStore(string? dataDirectory = null, bool configureStartup = true)
    {
        DataDirectory = dataDirectory ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SNUETL");
        this.configureStartup = configureStartup;
    }

    public string SettingsPath => Path.Combine(DataDirectory, "settings.json");

    public AppSettings Load()
    {
        try
        {
            var settings = JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(SettingsPath), JsonOptions) ?? new AppSettings();
            return settings with { RefreshMinutes = Math.Clamp(settings.RefreshMinutes, 1, 1440),
                SchemaVersion = 4, IconChoice = settings.SchemaVersion < 4 && settings.IconChoice == "Default" ? "SNU" : settings.IconChoice,
                IconStyleVersion = settings.SchemaVersion < 4 && settings.IconChoice == "Default" ? 0 : settings.IconStyleVersion };
        }
        catch (IOException exception) when (exception is FileNotFoundException or DirectoryNotFoundException)
        {
            return new AppSettings();
        }
        catch (JsonException)
        {
            return new AppSettings();
        }
    }

    public void Save(AppSettings settings)
    {
        lock (saveLock)
        {
        Directory.CreateDirectory(DataDirectory);
        var temporary = SettingsPath + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(settings, JsonOptions));
        File.Move(temporary, SettingsPath, true);
        if (configureStartup && lastStartup != settings.StartWithWindows)
        { ConfigureStartup(settings.StartWithWindows); lastStartup = settings.StartWithWindows; }
        }
    }

    public static void ConfigureStartup(bool enabled)
    {
        using var key = Registry.CurrentUser.CreateSubKey(StartupKey, writable: true);
        if (enabled)
        {
            var executable = InstalledExecutable;
            if (executable is null) return; // Development runs must never become logon targets.
            key.SetValue("SNUETL", $"\"{executable}\" --background", RegistryValueKind.String);
        }
        else
        {
            key.DeleteValue("SNUETL", throwOnMissingValue: false);
        }
    }

    public static string? InstalledExecutable
    {
        get
        {
            using var installed = Registry.CurrentUser.OpenSubKey(@"Software\SNUETL");
            var path = installed?.GetValue("Executable") as string;
            var expected = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SNUETL", "app", "SNUETL.exe");
            return path is not null && File.Exists(path) ? path : File.Exists(expected) ? expected : null;
        }
    }
    public static string StartupStatus
    {
        get
        {
            using var approved = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run");
            if (approved?.GetValue("SNUETL") is byte[] bytes && bytes.Length > 0 && bytes[0] is 3 or 7)
                return "Disabled by Windows — enable SNUETL in Windows Startup apps.";
            using var run = Registry.CurrentUser.OpenSubKey(StartupKey);
            var path = InstalledExecutable;
            return path is null ? "Available after installation" :
                run?.GetValue("SNUETL") as string == $"\"{path}\" --background" ? "Enabled — starts quietly after sign-in" : "Not registered — use Repair startup";
        }
    }

    public static void RemoveStartup()
    {
        ConfigureStartup(enabled: false);
        using var approved = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run", writable: true);
        approved?.DeleteValue("SNUETL", false);
    }
}
