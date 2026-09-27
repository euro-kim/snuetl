using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using Button = System.Windows.Controls.Button;
using Application = System.Windows.Application;
namespace Snuetl.Windows;

public partial class DashboardWindow : Window
{
    private readonly ClientController controller;
    private SettingsView? settings;
    private int previousPage;
    private sealed record Row(string Title, string Label, string Detail, string Action, string? Url = null, ActivityEntry? File = null)
    {
        public string Badge => File?.Badge ?? (Label.Contains("ANNOUNCEMENT",StringComparison.OrdinalIgnoreCase) ? "📣" : Label.Contains("COURSE") ? "▣" : "◷");
        public string BadgeColor => File?.BadgeColor ?? "#4262B8";
        public string BadgeBackground => File?.BadgeBackground ?? "#EAF0FF";
    }
    public DashboardWindow(ClientController controller)
    {
        InitializeComponent(); this.controller = controller;
        controller.AcademicChanged += Changed;
        controller.Activity.Changed += Changed;
        controller.StatusChanged += StatusChanged;
        Closed += (_, _) => { controller.AcademicChanged -= Changed; controller.Activity.Changed -= Changed; controller.StatusChanged -= StatusChanged; };
        Closing += (_,e) => { if (settings is not null && !settings.SavePreferences()) e.Cancel = true; };
        IsVisibleChanged += (_, _) => { if (IsVisible) Render(); };
        Navigation.SelectedIndex = 0;
    }
    private void Changed(object? sender, EventArgs e) => Dispatcher.BeginInvoke(new Action(() => { if (IsVisible) Render(); }));
    private void StatusChanged(object? sender, ClientStatus e) => Changed(sender, EventArgs.Empty);
    internal void SelectSettings() { Navigation.SelectedIndex = 5; Render(); }
    internal void UpdateIcon() => settings?.UpdateIcon();
    internal SettingsView SettingsPage { get { SelectSettings(); return settings!; } }
    internal void SelectFiles() { Navigation.SelectedIndex = 4; Render(); }
    internal void SelectNotifications() { Navigation.SelectedIndex = 3; Render(); }
    private void Navigate(object sender, SelectionChangedEventArgs e)
    {
        if (controller is null) return;
        if (previousPage == 5 && Navigation.SelectedIndex != 5 && settings is not null && !settings.SavePreferences()) { Navigation.SelectedIndex = 5; return; }
        previousPage = Navigation.SelectedIndex;
        Render();
    }
    private static string Value(JsonElement row, string key) => row.TryGetProperty(key, out var value) && value.ValueKind != JsonValueKind.Null ? value.ToString() : "";
    private static string Detail(JsonElement row, string key)
    {
        var value = Value(row,key);
        if (value.Length == 0 || value == "False") return "";
        if (key.EndsWith("_at",StringComparison.Ordinal) && DateTimeOffset.TryParse(value,out var time)) value = time.LocalDateTime.ToString("MMM d, HH:mm");
        if (key == "comments" && row.GetProperty(key).ValueKind == JsonValueKind.Array)
            return string.Join(" · ", row.GetProperty(key).EnumerateArray().Select(c => Value(c,"author") + ": " + Value(c,"text")));
        return key is "course_name" or "summary" ? value : key.Replace('_',' ') + ": " + value;
    }
    internal void Render()
    {
        SyncButton.IsEnabled = !controller.IsRefreshing; SyncLabel.Text = controller.IsRefreshing ? "Syncing…" : "Sync now";
        var page = Navigation.SelectedIndex;
        if (page == 5)
        {
            settings ??= new SettingsView(controller);
            SettingsHost.Content = settings;
            DashboardContent.Visibility = Visibility.Collapsed; SettingsHost.Visibility = Visibility.Visible;
            settings.Render(controller.Status); return;
        }
        DashboardContent.Visibility = Visibility.Visible; SettingsHost.Visibility = Visibility.Collapsed;
        Heading.Text = (Navigation.SelectedItem as ListBoxItem)?.Content?.ToString() ?? "Overview";
        var snapshot = controller.Academic;
        StatusText.Text = snapshot.GeneratedAt is null ? controller.Status.State : $"{controller.Status.State} · Updated {snapshot.GeneratedAt.Value.LocalDateTime:g} · Every {controller.Settings.RefreshMinutes} minutes";
        ErrorText.Text = string.Join("\n", new[] { controller.Status.Error }.Where(s => !string.IsNullOrEmpty(s)).Concat(snapshot.Errors));
        var rows = new List<Row>();
        if (page == 1)
            rows.AddRange(snapshot.Courses.Select(c => new Row(c.Name, "COURSE", $"Course {c.Id}", "Open on eTL ↗", c.Url)));
        else if (page == 3)
            rows.AddRange(snapshot.Events.Select(e => new Row(e.Title, $"{e.Category.ToUpperInvariant()} · {e.Time.LocalDateTime:g}", e.Detail, "Open on eTL ↗", e.Url)));
        else if (page == 4)
            rows.AddRange(controller.ActivitySnapshot().Select(e => new Row(e.Name, e.TimeLabel, e.StatusLabel, string.IsNullOrEmpty(e.DirectoryLabel) ? "" : e.DirectoryLabel + "  ↗", File:e)));
        else
        {
            var keys = page == 2 ? new[] { "upcoming", "missing", "calendar" } : new[] { "announcements", "upcoming", "submissions", "feedback", "grades", "calendar", "discussions" };
            foreach (var kind in keys)
            {
                if (!snapshot.Datasets.TryGetValue(kind, out var items)) continue;
                foreach (var item in items)
                {
                    var title = Value(item, "title");
                    if (title.Length == 0) title = Value(item, "course_name");
                    var detail = string.Join(" · ", new[] { "course_name", "summary", "due_at", "start_at", "workflow_state", "grade", "current_grade", "current_score", "submitted_at", "graded_at", "last_reply_at", "missing", "late", "comments", "unread_count" }.Select(k => Detail(item,k)).Where(v => v.Length > 0));
                    rows.Add(new Row(title, kind.ToUpperInvariant(), detail, "Open on eTL ↗", Value(item,"url")));
                }
            }
        }
        Rows.ItemsSource = rows;
        Empty.Visibility = rows.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        if (page == 3) Empty.Text = "No notifications yet. Choose categories in Settings. Existing announcements are silently baselined.";
        else Empty.Text = "No items to show. Connect your account in Settings, then refresh.";
    }
    private void OpenRow(object sender, RoutedEventArgs e) => Open((Row)((Button)sender).Tag, false);
    private void OpenDirectory(object sender, RoutedEventArgs e) => Open((Row)((Button)sender).Tag, true);
    private void Open(Row row, bool directory)
    {
        try { if (row.File is not null) controller.OpenActivity(row.File,directory); else ClientController.OpenEtl(row.Url); }
        catch (Exception ex) { ErrorText.Text = ex.Message; }
    }
    private void OpenEtl(object sender, RoutedEventArgs e) => ClientController.OpenEtl();
    private async void Refresh(object sender, RoutedEventArgs e) => await controller.RefreshAsync(manual: true);
}
