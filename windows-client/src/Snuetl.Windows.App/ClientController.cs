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
    private int itemCount;
    private DateTimeOffset? nextRefresh;
    private string? error;
    private AccountStatus account = new();

    public AppSettings Settings { get; private set; }
    public ClientStatus Status => BuildStatus();
    public event EventHandler<ClientStatus>? StatusChanged;

    public ClientController(SettingsStore store)
    {
        this.store = store;
        Settings = store.Load();
        backend = new BackendClient(store.DataDirectory);
    }

    public async Task StartAsync()
    {
        // Configure per-user startup even when first-run authentication is postponed.
        store.Save(Settings);
        account = await backend.InvokeAsync<AccountStatus>("auth.status", new { verify = false });
        if (account.Ready)
        {
            await EnsureProviderAsync();
            _ = RefreshAsync();
        }
        scheduler = RunSchedulerAsync(lifetime.Token);
        Publish();
    }

    public async Task ConnectAutomaticallyAsync()
    {
        error = null;
        Publish("Signing in…");
        try
        {
            await backend.InvokeAsync<JsonElement>("auth.auto");
            account = await backend.InvokeAsync<AccountStatus>("auth.status", new { verify = true });
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
            error = null;
        }
        catch (Exception exception)
        {
            error = exception.Message;
        }
        Publish("Disconnected");
    }

    public async Task RefreshAsync()
    {
        if (!account.Ready || provider is null || !await refreshLock.WaitAsync(0))
        {
            return;
        }
        try
        {
            error = null;
            Publish("Checking SNU eTL…");
            var manifest = await backend.InvokeAsync<Manifest>(
                "manifest.refresh",
                cancellationToken: lifetime.Token);
            var reconciler = new ManifestReconciler(provider);
            await reconciler.ReconcileAsync(manifest, lifetime.Token);
            itemCount = manifest.Entries.Count;
            Settings = Settings with { LastRefresh = DateTimeOffset.Now };
            store.Save(Settings);
            nextRefresh = DateTimeOffset.Now.AddMinutes(Settings.RefreshMinutes);
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
            nextRefresh = DateTimeOffset.Now.AddMinutes(Settings.RefreshMinutes);
            Publish("Refresh failed");
        }
        catch (Exception exception)
        {
            error = exception.Message;
            nextRefresh = DateTimeOffset.Now.AddMinutes(Settings.RefreshMinutes);
            Publish("Refresh failed");
        }
        finally
        {
            refreshLock.Release();
        }
    }

    public async Task ChangeRootAsync(string root)
    {
        var full = Path.GetFullPath(Environment.ExpandEnvironmentVariables(root));
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

    private Task EnsureProviderAsync()
    {
        if (provider is not null)
        {
            return Task.CompletedTask;
        }
        provider = new CloudFilesProvider(Settings.SyncRoot, store.DataDirectory, backend);
        provider.RegisterAndConnect();
        store.Save(Settings);
        return Task.CompletedTask;
    }

    private async Task RunSchedulerAsync(CancellationToken cancellationToken)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromMinutes(Settings.RefreshMinutes));
        try
        {
            while (await timer.WaitForNextTickAsync(cancellationToken))
            {
                await RefreshAsync();
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
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
        if (provider is not null)
        {
            await provider.DisposeAsync();
        }
        await backend.DisposeAsync();
        refreshLock.Dispose();
        lifetime.Dispose();
    }
}
