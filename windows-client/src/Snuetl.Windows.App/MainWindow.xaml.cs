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
        controller.AcademicChanged += (_, _) => Dispatcher.BeginInvoke(new Action(() => { if (IsVisible) ReloadAcademic(); }));
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
        var snapshot = controller.ActivitySnapshot().Where(e => e.EventType == "file" && !e.IsRoutineGeneratedCopy).ToArray();
        // Insert only new rows: preserve the user's scroll position while syncing.
        var unseen = entries.Count == 0 ? snapshot : snapshot.TakeWhile(e => e.Time != entries[0].Time || e.Path != entries[0].Path || e.Action != entries[0].Action).ToArray();
        foreach (var entry in Enumerable.Reverse(unseen)) entries.Insert(0, entry);
        while (entries.Count > 500) entries.RemoveAt(entries.Count - 1);
        if (offset > 0) { ActivityList.UpdateLayout(); scroll?.ScrollToVerticalOffset(offset + Math.Max(0, scroll.ExtentHeight - extent)); }
        if (AcademicList.Visibility == Visibility.Visible) ReloadAcademic();
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
        UpdateBanner.Visibility = controller.Settings.AvailableUpdate is null ? Visibility.Collapsed : Visibility.Visible;
        UpdateBannerText.Text = controller.Settings.AvailableUpdate is { } update ? $"SNUETL {update.Version} is available" : "";
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
    private void FeedTab_Changed(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (controller is null || ActivityList is null || AcademicList is null) return;
        var files = FeedTabs.SelectedIndex == 3;
        ActivityList.Visibility = files ? Visibility.Visible : Visibility.Collapsed;
        AcademicList.Visibility = files ? Visibility.Collapsed : Visibility.Visible;
        if (files) { EmptyText.Text = "No file activity yet. Saved announcement copies are kept out of this feed."; ReloadActivity(); }
        else ReloadAcademic(resetScroll:true);
    }
    private void ReloadAcademic(bool resetScroll = false)
    {
        if (FeedTabs.SelectedIndex == 3) return;
        var now = DateTimeOffset.Now;
        var items = FeedTabs.SelectedIndex switch
        {
            1 => MiniPanelFeed.Announcements(controller.Academic),
            2 => MiniPanelFeed.Deadlines(controller.Academic,now),
            _ => MiniPanelFeed.Alerts(controller.Academic,now)
        };
        var scroll = FindScrollViewer(AcademicList);
        var offset = scroll?.VerticalOffset ?? 0;
        var extent = scroll?.ExtentHeight ?? 0;
        if (resetScroll || AcademicList.ItemsSource is not IReadOnlyList<MiniPanelItem> previous || !previous.SequenceEqual(items))
        {
            AcademicList.ItemsSource = items;
            AcademicList.UpdateLayout();
            scroll = FindScrollViewer(AcademicList);
            scroll?.ScrollToVerticalOffset(resetScroll ? 0 : offset > 0 ? Math.Max(0,offset + scroll.ExtentHeight - extent) : 0);
        }
        EmptyText.Text = FeedTabs.SelectedIndex switch
        {
            1 => "No announcements yet. Sync to check your courses.",
            2 => "No pending deadlines. You're caught up!",
            _ => "No recent course alerts. Announcements and approaching deadlines will appear here."
        };
        EmptyText.Visibility = items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    }
    private void AcademicItem_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not System.Windows.Controls.Button { Tag: MiniPanelItem item } || !item.CanOpen) return;
        try { ClientController.OpenEtl(item.Url); } catch (Exception ex) { ErrorText.Text = ex.Message; ErrorText.Visibility = Visibility.Visible; }
    }
    private void Settings_Click(object sender, RoutedEventArgs e) => ((App)System.Windows.Application.Current).ShowSettings();
    private void Update_Click(object sender, RoutedEventArgs e) => ((App)System.Windows.Application.Current).ShowUpdateSettings();
    private void Close_Click(object sender, RoutedEventArgs e) => Hide();
    private void Open_Click(object sender, RoutedEventArgs e) => controller.OpenFolder();
    private async void Refresh_Click(object sender, RoutedEventArgs e) => await controller.RefreshAsync(manual: true);
    private void Header_Drag(object sender, MouseButtonEventArgs e) { if (e.ChangedButton == MouseButton.Left) DragMove(); }
}
