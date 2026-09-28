using System.Diagnostics;
using System.Threading;
using System.Windows;
using Forms = System.Windows.Forms;

namespace Snuetl.Windows;

public partial class App : System.Windows.Application
{
    private const string MutexName = "Local\\SNUETL-Windows-Client";
    private Mutex? singleInstance;
    private ClientController? controller;
    private MainWindow? window;
    private DashboardWindow? dashboard;
    private Forms.NotifyIcon? tray;
    private readonly CancellationTokenSource lifetime = new();
    private bool quitting;
    internal bool SuppressStartupForTests { get; init; }

    protected override async void OnStartup(StartupEventArgs e)
    {
        if (SuppressStartupForTests) return;
        base.OnStartup(e);
        // 0.7.x invokes this command during major upgrades. The new installer uses
        // --uninstall exclusively for removal, so a late old-MSI removal is harmless.
        if (e.Args.Contains("--unregister")) { Shutdown(); return; }
        try
        {
            if (e.Args.Contains("--stop") || e.Args.Contains("--uninstall"))
            {
                await StopExistingAsync();
                if (e.Args.Contains("--uninstall")) UninstallCleanup.Run();
                Shutdown(); return;
            }
            singleInstance = new Mutex(true, MutexName, out var created);
            if (!created) { await InstanceChannel.SendAsync(ActivationRoute(e.Args)); singleInstance.Dispose(); singleInstance = null; Shutdown(); return; }
            controller = new ClientController(new SettingsStore());
            // Build windows only on request; quiet startup needs only the tray icon.
            _ = InstanceChannel.ServeAsync(command => Dispatcher.BeginInvoke(new Action(async () =>
            {
                if (command == "shutdown") await QuitAsync(); else if (command is "dashboard" or "notifications" or "files") ShowDashboard(command == "notifications", command == "files"); else ShowWindow();
            })), lifetime.Token);
            controller.StatusChanged += OnStatusChanged;
            controller.AppearanceChanged += (_, _) => UpdateIcon();
            await controller.StartAsync();
            ConfigureTray();
            if (e.Args.Contains("--install-signin=1"))
            {
                ShowSettings();
                try { await controller.InstallSignInAsync(); }
                catch (Exception ex) { System.Windows.MessageBox.Show("The core client is installed and ready. Optional automatic sign-in could not be downloaded. For private releases, save GitHub access in Settings → About & updates, then retry in Components. Manual Canvas token setup remains available.\n\n" + ex.Message, "SNUETL optional component"); }
            }
            if (!e.Args.Contains("--background"))
            {
                if (!controller.Settings.SetupCompleted || !controller.Status.Account.StartsWith("Connected", StringComparison.Ordinal)) ShowSettings();
                else ShowDashboard(ActivationRoute(e.Args) == "notifications", ActivationRoute(e.Args) == "files");
            }
        }
        catch (Exception exception)
        {
            System.Windows.MessageBox.Show(exception.Message, "SNUETL needs attention", MessageBoxButton.OK, MessageBoxImage.Error);
            if (controller is not null) { try { await controller.DisposeAsync(); } catch { /* Preserve the startup error. */ } }
            Shutdown(1);
        }
    }

    private static async Task StopExistingAsync()
    {
        var signaled = await InstanceChannel.SendAsync("shutdown");
        // Compatibility with pre-IPC 0.7.x: only stop the old binary at this exact
        // installation path. Never terminate an unrelated process by name alone.
        if (!signaled)
        {
            foreach (var process in Process.GetProcessesByName("SNUETL"))
            {
                using (process)
                {
                    if (process.Id == Environment.ProcessId) continue;
                    try
                    {
                        if (string.Equals(process.MainModule?.FileName, Environment.ProcessPath, StringComparison.OrdinalIgnoreCase)
                            && Version.TryParse(process.MainModule?.FileVersionInfo.FileVersion, out var version)
                            && version < new Version(0, 8, 0))
                        { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(); }
                    }
                    catch (InvalidOperationException) { }
                }
            }
        }
        // Wait on the UI thread: mutex ownership must be released by its acquiring thread.
        using var mutex = new Mutex(false, MutexName);
        for (var i = 0; i < 150; i++)
        {
            bool acquired;
            try { acquired = mutex.WaitOne(0); } catch (AbandonedMutexException) { acquired = true; }
            if (acquired) { mutex.ReleaseMutex(); return; }
            await Task.Delay(200);
        }
        throw new IOException("SNUETL is still finishing a file operation. Quit it from the tray and retry setup.");
    }

    private void ConfigureTray()
    {
        tray = new Forms.NotifyIcon { Text = "SNUETL", Icon = Branding.LoadIcon(), Visible = true, ContextMenuStrip = new Forms.ContextMenuStrip() };
        tray.MouseClick += (_, args) => { if (args.Button == Forms.MouseButtons.Left) Dispatcher.Invoke(ShowWindow); };
        tray.ContextMenuStrip.Items.Add("Dashboard", null, (_, _) => Dispatcher.Invoke(() => ShowDashboard()));
        tray.ContextMenuStrip.Items.Add("Open eTL", null, (_, _) => ClientController.OpenEtl());
        tray.ContextMenuStrip.Items.Add("Recent activity", null, (_, _) => Dispatcher.Invoke(ShowWindow));
        tray.ContextMenuStrip.Items.Add("Open SNUETL folder", null, (_, _) => controller?.OpenFolder());
        tray.ContextMenuStrip.Items.Add("Settings", null, (_, _) => Dispatcher.Invoke(ShowSettings));
        tray.ContextMenuStrip.Items.Add(new Forms.ToolStripSeparator());
        tray.ContextMenuStrip.Items.Add("Quit SNUETL", null, async (_, _) => await QuitAsync());
        UpdateIcon();
    }
    private void UpdateIcon()
    {
        if (tray is not null) { var old = tray.Icon; tray.Icon = Branding.LoadIcon(); old?.Dispose(); }
        window?.UpdateIcon(); dashboard?.UpdateIcon();
    }
    private void ShowWindow()
    {
        if (controller is null) return;
        window ??= new MainWindow(controller);
        window.Show(); window.WindowState = WindowState.Normal;
        var area = SystemParameters.WorkArea;
        window.Left = Math.Max(area.Left, area.Right - window.ActualWidth - 16);
        window.Top = Math.Max(area.Top, area.Bottom - window.ActualHeight - 16);
        window.Activate();
    }
    private static string ActivationRoute(IEnumerable<string> args) => args.Any(a => a.TrimEnd('/').Equals("snuetl://files", StringComparison.OrdinalIgnoreCase)) ? "files" : args.Any(a => a.TrimEnd('/').Equals("snuetl://notifications", StringComparison.OrdinalIgnoreCase)) ? "notifications" : "dashboard";
    internal void ShowDashboard(bool notifications = false, bool files = false)
    {
        if (controller is null) return;
        if (dashboard is null) { dashboard = new DashboardWindow(controller); dashboard.Closed += (_, _) => { if (ReferenceEquals(MainWindow,dashboard)) MainWindow = null; dashboard = null; }; }
        window?.Hide(); dashboard.Show(); dashboard.WindowState = WindowState.Normal;
        if (notifications) dashboard.SelectNotifications();
        if (files) dashboard.SelectFiles();
        dashboard.Activate();
    }
    internal void ShowSettings()
    {
        ShowDashboard(); dashboard?.SelectSettings();
    }
    private void OnStatusChanged(object? sender, ClientStatus status) => Dispatcher.BeginInvoke(new Action(() =>
    {
        window?.Render(status);
        if (tray is not null)
        {
            var tooltip = status.Error is null ? $"SNUETL — {status.State}" : "SNUETL — attention needed";
            tray.Text = tooltip[..Math.Min(63, tooltip.Length)];
        }
    }));
    public async Task QuitAsync()
    {
        if (quitting) return;
        quitting = true; lifetime.Cancel();
        if (tray is not null) { tray.Visible = false; tray.Icon?.Dispose(); tray.Dispose(); tray = null; }
        if (controller is not null) await controller.DisposeAsync();
        singleInstance?.ReleaseMutex(); singleInstance?.Dispose(); singleInstance = null;
        Shutdown();
    }
}
