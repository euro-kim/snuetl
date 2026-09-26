using System.Windows;
using Forms = System.Windows.Forms;

namespace Snuetl.Windows;

public partial class MainWindow : Window
{
    private readonly ClientController controller;
    private bool busy;

    public MainWindow(ClientController controller)
    {
        InitializeComponent();
        this.controller = controller;
        RootText.Text = controller.Settings.SyncRoot;
        Render(controller.Status);
        Closing += (_, args) =>
        {
            args.Cancel = true;
            Hide();
        };
    }

    public void Render(ClientStatus status)
    {
        StateText.Text = status.State;
        AccountText.Text = $"{status.Account}  •  {status.ItemCount} items";
        RootText.Text = status.Root;
        RefreshText.Text = status.LastRefresh is null
            ? "Not refreshed yet"
            : $"Last checked {status.LastRefresh.Value.LocalDateTime:g}"
              + (status.NextRefresh is null ? string.Empty : $"  •  next {status.NextRefresh.Value.LocalDateTime:t}");
        ErrorText.Text = status.Error ?? string.Empty;
        ErrorText.Visibility = status.Error is null ? Visibility.Collapsed : Visibility.Visible;
        SignInPanel.Visibility = status.Account.StartsWith("Connected", StringComparison.Ordinal)
            ? Visibility.Collapsed
            : Visibility.Visible;
    }

    private async void Connect_Click(object sender, RoutedEventArgs e) =>
        await RunAsync(controller.ConnectAutomaticallyAsync);

    private async void ManualConnect_Click(object sender, RoutedEventArgs e)
    {
        var token = TokenBox.Password;
        TokenBox.Clear();
        await RunAsync(() => controller.ConnectManualAsync(token));
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) =>
        await RunAsync(controller.RefreshAsync);

    private void Open_Click(object sender, RoutedEventArgs e) => controller.OpenFolder();

    private void TokenSettings_Click(object sender, RoutedEventArgs e) =>
        ClientController.OpenTokenSettings();

    private async void SignOut_Click(object sender, RoutedEventArgs e) =>
        await RunAsync(controller.DisconnectAsync);

    private async void Browse_Click(object sender, RoutedEventArgs e)
    {
        using var dialog = new Forms.FolderBrowserDialog
        {
            Description = "Choose the local NTFS folder for SNUETL",
            InitialDirectory = RootText.Text,
            UseDescriptionForTitle = true,
        };
        if (dialog.ShowDialog() == Forms.DialogResult.OK)
        {
            await RunAsync(() => controller.ChangeRootAsync(dialog.SelectedPath));
        }
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Hide();

    private async Task RunAsync(Func<Task> operation)
    {
        if (busy) return;
        busy = true;
        IsEnabled = false;
        try
        {
            await operation();
        }
        catch (Exception exception)
        {
            System.Windows.MessageBox.Show(
                exception.Message,
                "SNUETL",
                MessageBoxButton.OK,
                MessageBoxImage.Warning);
        }
        finally
        {
            IsEnabled = true;
            busy = false;
        }
    }
}
