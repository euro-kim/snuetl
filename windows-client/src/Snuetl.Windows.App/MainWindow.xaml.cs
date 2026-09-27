using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media.Imaging;

namespace Snuetl.Windows;

public partial class MainWindow : Window
{
    private readonly ClientController controller;
    private readonly ObservableCollection<ActivityEntry> entries = [];
    public MainWindow(ClientController controller)
    {
        InitializeComponent(); this.controller = controller; ActivityList.ItemsSource = entries;
        Render(controller.Status); ReloadActivity(); UpdateIcon();
        controller.Activity.Changed += (_, _) => Dispatcher.BeginInvoke(new Action(() => { if (IsVisible) ReloadActivity(); }));
        controller.AcademicChanged += (_, _) => Dispatcher.BeginInvoke(new Action(() => { if (IsVisible && AcademicList.Visibility == Visibility.Visible) ShowAlerts(); }));
        IsVisibleChanged += (_, _) => { if (IsVisible) ReloadActivity(); };
        Closing += (_, args) => { args.Cancel = true; Hide(); };
        Deactivated += (_, _) => Hide();
        PreviewKeyDown += (_, e) => { if (e.Key == Key.Escape) Hide(); };
    }
    private void ReloadActivity()
    {
        var scroll = FindScrollViewer(ActivityList);
        var offset = scroll?.VerticalOffset ?? 0;
        var extent = scroll?.ExtentHeight ?? 0;
        var snapshot = controller.ActivitySnapshot();
        // Insert only new rows: preserve the user's scroll position while syncing.
        var unseen = entries.Count == 0 ? snapshot : snapshot.TakeWhile(e => e.Time != entries[0].Time || e.Path != entries[0].Path || e.Action != entries[0].Action).ToArray();
        foreach (var entry in unseen.Reverse()) entries.Insert(0, entry);
        while (entries.Count > 500) entries.RemoveAt(entries.Count - 1);
        if (offset > 0) { ActivityList.UpdateLayout(); scroll?.ScrollToVerticalOffset(offset + Math.Max(0, scroll.ExtentHeight - extent)); }
        if (AcademicList.Visibility == Visibility.Visible) ShowAlerts();
        else EmptyText.Visibility = entries.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }
    internal void UpdateIcon()
    {
        if (!File.Exists(Branding.IconPath)) return;
        using var icon = Branding.LoadIcon(64);
        var source = Imaging.CreateBitmapSourceFromHIcon(icon.Handle, Int32Rect.Empty, BitmapSizeOptions.FromEmptyOptions());
        source.Freeze(); Logo.Source = source; Icon = source;
    }
    public void Render(ClientStatus status)
    {
        SyncButton.IsEnabled = !controller.IsRefreshing;
        SyncLabel.Text = controller.IsRefreshing ? "Syncing…" : "Sync now";
        StateText.Text = status.State;
        AccountText.Text = $"{status.ItemCount} course files · {status.Account}";
        RefreshText.Text = status.LastRefresh is null ? "Waiting for the first sync" : $"Last checked {status.LastRefresh.Value.LocalDateTime:g}";
        ErrorText.Text = status.Error ?? "";
        ErrorText.Visibility = status.Error is null ? Visibility.Collapsed : Visibility.Visible;
    }
    private void Activity_MouseWheel(object sender, MouseWheelEventArgs e)
    {
        var scroll = FindScrollViewer(ActivityList);
        if (scroll is null) return;
        scroll.ScrollToVerticalOffset(scroll.VerticalOffset - e.Delta / 120.0 * 80);
        e.Handled = true;
    }
    internal static System.Windows.Controls.ScrollViewer? FindScrollViewer(DependencyObject parent)
    {
        for (var i = 0; i < System.Windows.Media.VisualTreeHelper.GetChildrenCount(parent); i++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(parent, i);
            if (child is System.Windows.Controls.ScrollViewer found) return found;
            var nested = FindScrollViewer(child);
            if (nested is not null) return nested;
        }
        return null;
    }
    private void File_Click(object sender, RoutedEventArgs e) => OpenEntry(sender, false);
    private void Directory_Click(object sender, RoutedEventArgs e) => OpenEntry(sender, true);
    private void OpenEntry(object sender, bool directory)
    {
        if (sender is not System.Windows.Controls.Button { Tag: ActivityEntry entry }) return;
        try { controller.OpenActivity(entry, directory); }
        catch (Exception ex) { ErrorText.Text = ex.Message; ErrorText.Visibility = Visibility.Visible; }
    }
    private void Dashboard_Click(object sender, RoutedEventArgs e) => ((App)System.Windows.Application.Current).ShowDashboard();
    private void Alerts_Click(object sender, RoutedEventArgs e) => ShowAlerts();
    private void ShowAlerts()
    {
        ActivityList.Visibility = Visibility.Collapsed; AcademicList.Visibility = Visibility.Visible;
        AcademicList.ItemsSource = controller.Academic.Events;
        EmptyText.Text = "No academic alerts yet. Choose notification categories in Settings.";
        EmptyText.Visibility = controller.Academic.Events.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
    }
    private void FilesTab_Click(object sender, RoutedEventArgs e)
    {
        AcademicList.Visibility = Visibility.Collapsed; ActivityList.Visibility = Visibility.Visible;
        EmptyText.Text = "Your file activity will appear here."; ReloadActivity();
    }
    private void AcademicItem_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not System.Windows.Controls.Button { Tag: AcademicEvent item }) return;
        try { ClientController.OpenEtl(item.Url); } catch (Exception ex) { ErrorText.Text = ex.Message; ErrorText.Visibility = Visibility.Visible; }
    }
    private void Settings_Click(object sender, RoutedEventArgs e) => ((App)System.Windows.Application.Current).ShowSettings();
    private void Close_Click(object sender, RoutedEventArgs e) => Hide();
    private void Open_Click(object sender, RoutedEventArgs e) => controller.OpenFolder();
    private async void Refresh_Click(object sender, RoutedEventArgs e) => await controller.RefreshAsync(manual: true);
    private void Header_Drag(object sender, MouseButtonEventArgs e) { if (e.ChangedButton == MouseButton.Left) DragMove(); }
}
