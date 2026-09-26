using System.Drawing;
using System.Threading;
using System.Windows;
using Forms = System.Windows.Forms;

namespace Snuetl.Windows;

public partial class App : System.Windows.Application
{
    private Mutex? singleInstance;
    private ClientController? controller;
    private MainWindow? window;
    private Forms.NotifyIcon? tray;

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        singleInstance = new Mutex(initiallyOwned: true, "Local\\SNUETL-Windows-Client", out var created);
        if (!created)
        {
            Shutdown();
            return;
        }

        if (e.Args.Contains("--unregister", StringComparer.OrdinalIgnoreCase))
        {
            var settingsStore = new SettingsStore();
            var settings = settingsStore.Load();
            try
            {
                SettingsStore.RemoveStartup();
                CloudFilesProvider.PrepareForUninstall(settings.SyncRoot, settingsStore.DataDirectory);
            }
            catch (Exception)
            {
                // MSI removal must remain possible even if the root is offline or locked.
            }
            Shutdown();
            return;
        }

        try
        {
            controller = new ClientController(new SettingsStore());
            window = new MainWindow(controller);
            ConfigureTray();
            controller.StatusChanged += OnStatusChanged;
            await controller.StartAsync();
            if (!e.Args.Contains("--background", StringComparer.OrdinalIgnoreCase)
                || !controller.Status.Account.StartsWith("Connected", StringComparison.Ordinal))
            {
                ShowWindow();
            }
        }
        catch (Exception exception)
        {
            System.Windows.MessageBox.Show(
                exception.Message,
                "SNUETL could not start",
                MessageBoxButton.OK,
                MessageBoxImage.Error);
            Shutdown();
        }
    }

    private void ConfigureTray()
    {
        tray = new Forms.NotifyIcon
        {
            Text = "SNUETL",
            Icon = CreateTrayIcon(),
            Visible = true,
            ContextMenuStrip = new Forms.ContextMenuStrip(),
        };
        tray.DoubleClick += (_, _) => Dispatcher.Invoke(ShowWindow);
        tray.MouseClick += (_, args) =>
        {
            if (args.Button == Forms.MouseButtons.Left)
            {
                Dispatcher.Invoke(ShowWindow);
            }
        };
        tray.ContextMenuStrip.Items.Add("Open SNUETL", null, (_, _) => Dispatcher.Invoke(ShowWindow));
        tray.ContextMenuStrip.Items.Add("Open folder", null, (_, _) => controller?.OpenFolder());
        tray.ContextMenuStrip.Items.Add("Refresh now", null, async (_, _) =>
        {
            if (controller is not null) await controller.RefreshAsync();
        });
        tray.ContextMenuStrip.Items.Add(new Forms.ToolStripSeparator());
        tray.ContextMenuStrip.Items.Add("Quit", null, async (_, _) => await QuitAsync());
    }

    private static Icon CreateTrayIcon()
    {
        using var bitmap = new Bitmap(32, 32);
        using var graphics = Graphics.FromImage(bitmap);
        graphics.Clear(Color.FromArgb(36, 87, 214));
        using var font = new Font("Segoe UI", 19, System.Drawing.FontStyle.Bold, GraphicsUnit.Pixel);
        using var brush = new SolidBrush(Color.White);
        var format = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };
        graphics.DrawString("S", font, brush, new RectangleF(0, 0, 32, 30), format);
        var handle = bitmap.GetHicon();
        try
        {
            return (Icon)Icon.FromHandle(handle).Clone();
        }
        finally
        {
            DestroyIcon(handle);
        }
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool DestroyIcon(IntPtr handle);

    private void ShowWindow()
    {
        if (window is null) return;
        window.Show();
        window.WindowState = WindowState.Normal;
        window.Activate();
    }

    private void OnStatusChanged(object? sender, ClientStatus status)
    {
        Dispatcher.Invoke(() =>
        {
            window?.Render(status);
            if (tray is not null)
            {
                tray.Text = status.Error is null
                    ? $"SNUETL — {status.State}"
                    : "SNUETL — attention needed";
            }
        });
    }

    public async Task QuitAsync()
    {
        tray!.Visible = false;
        tray.Dispose();
        tray = null;
        if (controller is not null)
        {
            await controller.DisposeAsync();
        }
        singleInstance?.ReleaseMutex();
        singleInstance?.Dispose();
        singleInstance = null;
        Shutdown();
    }
}
