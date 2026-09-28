using System.Diagnostics;
using System.Text.Json;

namespace Snuetl.Windows;

public sealed class ClientController : IAsyncDisposable
{
    private readonly SettingsStore store;
    private readonly BackendClient backend;
    private readonly SemaphoreSlim refreshLock = new(1, 1);
    private readonly CancellationTokenSource lifetime = new();
    private CloudFilesProvider? provider;
    private Task? scheduler;
    private Task? updateCheck;
    private readonly SemaphoreSlim updateLock = new(1,1);
    public string UpdateStatus { get; private set; } = "Checks GitHub weekly";
    public bool IsRefreshing { get; private set; }
    private PeriodicTimer? refreshTimer;
    private int itemCount;
    private DateTimeOffset? nextRefresh;
    private string? error;
    private string? lastLoggedError;
    private AccountStatus account = new();

    public ActivityStore Activity { get; }
    public AcademicSnapshot Academic { get; private set; } = new();
    public event EventHandler? AcademicChanged;
    public event EventHandler? AppearanceChanged;
    public AppSettings Settings { get; private set; }
    public ClientStatus Status => BuildStatus();
    public event EventHandler<ClientStatus>? StatusChanged;

    public ClientController(SettingsStore store)
    {
        this.store = store;
        Settings = store.Load();
        if (Settings.AvailableUpdate is { } update) UpdateStatus = $"Version {update.Version} is available · {update.Size / 1048576.0:F1} MB";
        else if (Settings.LastUpdateCheck is { } checkedAt) UpdateStatus = $"Checks GitHub weekly · Last checked {checkedAt.LocalDateTime:g}";
        Activity = new ActivityStore(store.DataDirectory);
        backend = new BackendClient(store.DataDirectory);
    }

    public async Task StartAsync()
    {
        // Configure per-user startup even when first-run authentication is postponed.
        if (Settings.IconChoice == "SNU" && Settings.IconStyleVersion < 2 && File.Exists(Branding.IconPath))
        {
            try { await Branding.ApplyAsync("SNU"); Settings = Settings with { IconStyleVersion = 2 }; }
            catch (Exception e) when (e is System.Net.Http.HttpRequestException or TaskCanceledException) { Activity.Add("SNU icon update postponed until online"); }
        }
        else if (!File.Exists(Branding.IconPath)) await Branding.ApplyAsync("Default");
        if (Settings.LastRunVersion != Branding.Version) Activity.Add($"Updated to version {Branding.Version}");
        Settings = Settings with { LastRunVersion = Branding.Version };
        store.Save(Settings);
        NativeNotifications.Register();
        account = await backend.InvokeAsync<AccountStatus>("auth.status", new { verify = false });
        if (account.Ready)
        {
            try { Academic = await backend.InvokeAsync<AcademicSnapshot>("academic.snapshot"); } catch (BackendException) { }
            await EnsureProviderAsync();
            _ = RefreshAsync();
        }
        scheduler = RunSchedulerAsync(lifetime.Token);
        updateCheck = CheckForUpdatesAsync();
        Publish();
    }

    public async Task ConnectAutomaticallyAsync()
    {
        error = null;
        Publish("Signing in…");
        try
        {
            if (!SignInAddon.Installed) await InstallSignInAsync();
            Publish("Complete sign-in in the browser…");
            await backend.InvokeAsync<JsonElement>("auth.auto");
            account = await backend.InvokeAsync<AccountStatus>("auth.status", new { verify = true });
            if (!account.Ready) throw new BackendException("NOT_CONNECTED", "Canvas access could not be verified. Reconnect your account.");
            await EnsureProviderAsync();
            await RefreshAsync();
        }
        catch (BackendException exception)
        {
            if (exception.Code is "AUTHENTICATION_REQUIRED" or "TOKEN_EXPIRED" or "NOT_CONNECTED")
            {
                account = account with { Ready = false };
            }
            error = exception.Message;
            Publish("Sign-in needs attention");
            throw;
        }
        catch (Exception exception)
        {
            error = exception.Message;
            Publish("Sign-in needs attention");
            throw;
        }
    }

    public async Task InstallSignInAsync()
    {
        Publish("Downloading automatic login…");
        try { await SignInAddon.InstallAsync(); }
        finally { Publish(); }
    }

    public async Task ConnectManualAsync(string token)
    {
        error = null;
        Publish("Validating API token…");
        try
        {
            await backend.InvokeAsync<JsonElement>(
                "auth.manual",
                new { token, origin = "https://myetl.snu.ac.kr" });
            account = await backend.InvokeAsync<AccountStatus>("auth.status", new { verify = true });
            if (!account.Ready) throw new BackendException("NOT_CONNECTED", "Canvas access could not be verified. Check your API token.");
            await EnsureProviderAsync();
            await RefreshAsync();
        }
        catch (Exception exception)
        {
            error = exception.Message;
            Publish("Token needs attention");
            throw;
        }
    }

    public async Task DisconnectAsync()
    {
        try
        {
            await backend.InvokeAsync<JsonElement>("auth.disconnect");
            account = new AccountStatus();
            Academic = new(); AcademicChanged?.Invoke(this, EventArgs.Empty);
            error = null;
        }
        catch (Exception exception)
        {
            error = exception.Message;
        }
        Publish("Disconnected");
    }

    public async Task RefreshAsync(bool manual = false)
    {
        if (!account.Ready || provider is null || !await refreshLock.WaitAsync(0))
        {
            return;
        }
        try
        {
            error = null; IsRefreshing = true;
            Publish("Checking SNU eTL…");
            Academic = await backend.InvokeAsync<AcademicSnapshot>("academic.snapshot", new { refresh = true, preferences = Settings.Notifications }, lifetime.Token);
            AcademicChanged?.Invoke(this, EventArgs.Empty);
            NativeNotifications.Show(Academic.NewEvents.Where(e => Settings.Notifications.Courses.GetValueOrDefault(e.CourseId, Settings.Notifications.Categories).Contains(e.Category)).ToArray());
            var manifest = await backend.InvokeAsync<Manifest>(
                "manifest.refresh",
                cancellationToken: lifetime.Token);
            var activitySince = DateTimeOffset.Now;
            var reconciler = new ManifestReconciler(new ActivityPlaceholderStore(provider, Activity));
            var summary = await reconciler.ReconcileAsync(manifest, lifetime.Token);
            if (manual || summary.Created + summary.Updated + summary.Removed > 0) Activity.Add($"{(manual ? "Manual sync complete" : "Sync complete")} · {summary.Created} added · {summary.Updated} updated · {summary.Removed} removed");
            if (Settings.LastRefresh is not null)
            {
                var updates = Activity.Snapshot().Where(e => e.Time >= activitySince && e.EventType == "file"
                    && Settings.Notifications.Courses.GetValueOrDefault(e.CourseId ?? "", Settings.Notifications.Categories).Contains("files"))
                    .Select(e => new AcademicEvent { Title = e.Name, Detail = e.Action, Category = "files", Time = e.Time }).ToArray();
                NativeNotifications.Show(updates);
            }
            itemCount = manifest.Entries.Count;
            Settings = Settings with { LastRefresh = DateTimeOffset.Now };
            store.Save(Settings);
            nextRefresh = DateTimeOffset.Now.AddMinutes(Settings.RefreshMinutes);
            lastLoggedError = null;
            Publish("Up to date");
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested)
        {
        }
        catch (BackendException exception)
        {
            if (exception.Code is "AUTHENTICATION_REQUIRED" or "TOKEN_EXPIRED" or "NOT_CONNECTED")
            {
                account = account with { Ready = false };
            }
            error = exception.Message;
            if (lastLoggedError != error) Activity.Add("Sync failed — open settings for details");
            lastLoggedError = error;
            nextRefresh = DateTimeOffset.Now.AddMinutes(Settings.RefreshMinutes);
            Publish("Refresh failed");
        }
        catch (Exception exception)
        {
            error = exception.Message;
            if (lastLoggedError != error) Activity.Add("Sync failed — open settings for details");
            lastLoggedError = error;
            nextRefresh = DateTimeOffset.Now.AddMinutes(Settings.RefreshMinutes);
            Publish("Refresh failed");
        }
        finally
        {
            IsRefreshing = false; refreshLock.Release(); Publish();
        }
    }

    public async Task ChangeRootAsync(string root)
    {
        var full = Path.GetFullPath(Environment.ExpandEnvironmentVariables(root));
        if (CleanupPaths.Overlaps(full, store.DataDirectory))
            throw new InvalidOperationException("Choose a sync folder outside SNUETL’s application data folder.");
        if (string.Equals(full, Settings.SyncRoot, StringComparison.OrdinalIgnoreCase))
        {
            return;
        }
        if (Directory.Exists(Settings.SyncRoot)
            && Directory.EnumerateFileSystemEntries(Settings.SyncRoot).Any())
        {
            throw new InvalidOperationException(
                "Changing an initialized root is not automatic. Uninstall or empty the existing root first.");
        }
        if (provider is not null)
        {
            await provider.DisposeAsync();
            provider = null;
            CloudFilesProvider.Unregister(Settings.SyncRoot);
        }
        Settings = Settings with { SyncRoot = full };
        store.Save(Settings);
        if (account.Ready)
        {
            await EnsureProviderAsync();
            await RefreshAsync();
        }
        Publish();
    }

    public async Task SetAppearanceAsync(string choice, string? source = null)
    {
        await Branding.ApplyAsync(choice, source);
        Settings = Settings with { IconChoice = choice, IconStyleVersion = 2 };
        store.Save(Settings);
        AppearanceChanged?.Invoke(this, EventArgs.Empty);
        if (provider is not null) await ExplorerSyncRoot.RegisterAsync(Settings.SyncRoot);
    }

    public void SavePreferences(bool startup, int? refreshMinutes = null)
    {
        var interval = refreshMinutes ?? Settings.RefreshMinutes;
        if (interval is < 1 or > 1440) throw new ArgumentOutOfRangeException(nameof(refreshMinutes), "Choose an interval from 1 to 1,440 minutes.");
        if (interval != Settings.RefreshMinutes)
        {
            if (refreshTimer is not null) refreshTimer.Period = TimeSpan.FromMinutes(interval);
            if (account.Ready) nextRefresh = DateTimeOffset.Now.AddMinutes(interval);
        }
        Settings = Settings with { StartWithWindows = startup, SetupCompleted = true, RefreshMinutes = interval };
        store.Save(Settings);
        Publish();
    }

    public async Task CheckForUpdatesAsync(bool force = false)
    {
        if (!await updateLock.WaitAsync(0)) return;
        try
        {
            var now = DateTimeOffset.UtcNow;
            if (!force && !UpdateDue(Settings,now)) return;
            Settings = Settings with { LastUpdateAttempt = now }; store.Save(Settings);
            UpdateStatus = "Checking GitHub releases…"; Publish();
            using var github = new GitHubReleases(Settings.ReleaseRepository, token: GitHubCredential.Read());
            var asset = await github.FindAsync(false,lifetime.Token);
            var newer = asset is not null && GitHubReleases.ParseVersion(asset.Version) > GitHubReleases.ParseVersion(Branding.Version);
            Settings = Settings with { LastUpdateCheck = now, AvailableUpdate = newer ? asset : null }; store.Save(Settings);
            UpdateStatus = asset is null ? "No Windows installer was found in the published releases." : newer ? $"Version {asset!.Version} is available · {asset.Size / 1048576.0:F1} MB" : $"You're up to date · Checked {now.LocalDateTime:g}";
        }
        catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { }
        catch (Exception e) { UpdateStatus = "Update check unavailable: " + e.Message; }
        finally { updateLock.Release(); Publish(); }
    }
    internal static bool UpdateDue(AppSettings settings, DateTimeOffset now) =>
        (settings.LastUpdateCheck is null || now - settings.LastUpdateCheck >= TimeSpan.FromDays(7))
        && (settings.LastUpdateAttempt is null || now - settings.LastUpdateAttempt >= TimeSpan.FromDays(1));
    public async Task InstallUpdateAsync(IProgress<double>? progress = null)
    {
        var asset = Settings.AvailableUpdate ?? throw new IOException("Check for a newer release first.");
        var version = GitHubReleases.ParseVersion(asset.Version) ?? throw new IOException("Invalid update version. Check GitHub again.");
        var target = Path.Combine(store.DataDirectory,"updates",$"SNUETLSetup-{version.ToString(3)}.exe");
        using var github = new GitHubReleases(Settings.ReleaseRepository,token:GitHubCredential.Read());
        await github.DownloadAsync(asset,target,progress,lifetime.Token);
        Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
    }

    public IReadOnlyList<ActivityEntry> ActivitySnapshot() => Activity.Snapshot().Select(e =>
        e with { Unavailable = !string.IsNullOrEmpty(e.Path) && !File.Exists(Path.Combine(Settings.SyncRoot,e.Path)) }).ToArray();

    public void SaveNotifications(NotificationPreferences preferences)
    {
        Settings = Settings with { Notifications = preferences, NotificationSetupCompleted = true };
        store.Save(Settings);
    }
    public static void OpenEtl(string? url = null)
    {
        if (!Uri.TryCreate(url ?? "https://myetl.snu.ac.kr", UriKind.Absolute, out var uri)
            || uri.Scheme != "https" || uri.Host is not ("myetl.snu.ac.kr" or "etl.snu.ac.kr"))
            throw new InvalidOperationException("This item does not have a valid SNU eTL link.");
        Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
    }
    public void OpenActivity(ActivityEntry entry, bool directory)
    {
        if (string.IsNullOrEmpty(entry.Path)) return;
        var root = Path.GetFullPath(Settings.SyncRoot);
        var path = Path.GetFullPath(Path.Combine(root, entry.Path));
        if (!path.StartsWith(root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new IOException("This activity is outside the sync folder.");
        if (File.Exists(path))
        {
            if (directory) Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{path}\"") { UseShellExecute = true });
            else Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
            return;
        }
        var parent = Path.GetDirectoryName(path);
        while (parent is not null && !Directory.Exists(parent)) parent = Path.GetDirectoryName(parent);
        if (directory && parent is not null) Process.Start(new ProcessStartInfo("explorer.exe", $"\"{parent}\"") { UseShellExecute = true });
        else throw new FileNotFoundException("This file is unavailable. Select its directory to open the nearest existing folder.");
    }

    public void OpenFolder()
    {
        Directory.CreateDirectory(Settings.SyncRoot);
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{Settings.SyncRoot}\"")
        {
            UseShellExecute = true,
        });
    }

    public static void OpenTokenSettings()
    {
        Process.Start(new ProcessStartInfo("https://myetl.snu.ac.kr/profile/settings")
        {
            UseShellExecute = true,
        });
    }

    private async Task EnsureProviderAsync()
    {
        if (provider is not null)
        {
            return;
        }
        var candidate = new CloudFilesProvider(Settings.SyncRoot, store.DataDirectory, backend);
        candidate.FileDownloaded += path => Activity.Add("Downloaded to this device", path);
        candidate.FileActivity += (action, path) => Activity.Add(action, path);
        try
        {
            await candidate.RegisterAndConnectAsync();
            provider = candidate;
            store.Save(Settings);
        }
        catch
        {
            await candidate.DisposeAsync();
            throw;
        }
    }

    private async Task RunSchedulerAsync(CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromMinutes(Settings.RefreshMinutes));
        refreshTimer = timer;
        try
        {
            while (await timer.WaitForNextTickAsync(cancellationToken))
            {
                await RefreshAsync();
                await CheckForUpdatesAsync();
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        finally { refreshTimer = null; }
    }

    private ClientStatus BuildStatus(string? state = null) => new(
        state ?? (error is null ? (account.Ready ? "Up to date" : "Setup required") : "Attention needed"),
        account.Ready ? $"Connected: {account.UserId}" : "Not connected",
        Settings.SyncRoot,
        Settings.LastRefresh,
        nextRefresh,
        itemCount,
        error);

    private void Publish(string? state = null) => StatusChanged?.Invoke(this, BuildStatus(state));

    public async ValueTask DisposeAsync()
    {
        lifetime.Cancel();
        if (scheduler is not null)
        {
            try { await scheduler; } catch (OperationCanceledException) { }
        }
        if (updateCheck is not null) await updateCheck;
        await updateLock.WaitAsync(); updateLock.Release();
        await refreshLock.WaitAsync();
        if (provider is not null)
        {
            await provider.DisposeAsync();
        }
        await backend.DisposeAsync();
        Activity.Dispose();
        refreshLock.Release();
        refreshLock.Dispose();
        lifetime.Dispose();
    }
}
