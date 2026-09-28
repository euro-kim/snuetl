using System.Diagnostics;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media.Imaging;
using Forms = System.Windows.Forms;

namespace Snuetl.Windows;

public partial class SettingsView : System.Windows.Controls.UserControl
{
    private readonly ClientController controller;
    private bool busy;
    private string notificationCourse = "*";
    private readonly Dictionary<string,string[]> selections = [];
    private static readonly (string Key,string Label)[] Categories = [("announcements","Announcements"),("assignments","Assignments and deadline changes"),("reminders","Deadline reminders"),("grades","Grades and feedback"),("discussions","Discussion activity"),("files","File updates")];
    public SettingsView(ClientController controller)
    {
        InitializeComponent(); this.controller = controller;
        StartupCheck.IsChecked = controller.Settings.StartWithWindows;
        IntervalBox.Text = controller.Settings.RefreshMinutes.ToString(System.Globalization.CultureInfo.InvariantCulture);
        WelcomeText.Text = controller.Settings.SetupCompleted ? "SNUETL settings" : "Welcome to SNUETL";
        VersionText.Text = $"Version {Branding.Version}";
        selections["*"] = controller.Settings.Notifications.Categories;
        foreach (var pair in controller.Settings.Notifications.Courses) selections[pair.Key] = pair.Value;
        NotificationCourse.ItemsSource = new[] { new AcademicCourse { Id = "*", Name = "All courses (default)" } }.Concat(controller.Academic.Courses);
        ReminderHours.Text = string.Join(", ",controller.Settings.Notifications.ReminderHours);
        NotificationCourse.SelectedIndex = 0;
        StartupStatusText.Text = SettingsStore.StartupStatus;
        NotificationStatus.Text = NativeNotifications.Status;
        ComponentDescription.Text = SignInAddon.Description;
        ComponentStatus.Text = SignInAddon.Installed ? "Installed" : "Not installed — core client ready";
        RefreshGitHubAccess();
        Render(controller.Status); UpdateIcon();
        Loaded += (_, _) => { controller.LoginProgressChanged += LoginProgressChanged; RenderLoginProgress(); };
        Unloaded += (_, _) => controller.LoginProgressChanged -= LoginProgressChanged;
        SettingsTabs.SelectionChanged += (_, _) => Dispatcher.BeginInvoke(new Action(RenderLoginProgress));
    }
    public void Render(ClientStatus status)
    {
        ComponentStatus.Text = SignInAddon.Installed ? "Installed — ready for automatic login" : "Not installed — manual-token setup remains available";
        AccountText.Text = status.Account; RootText.Text = status.Root;
        var connected = status.Account.StartsWith("Connected", StringComparison.Ordinal);
        SignInPanel.Visibility = connected ? Visibility.Collapsed : Visibility.Visible;
        SignOutButton.Visibility = connected ? Visibility.Visible : Visibility.Collapsed;
        if (status.Error is not null) FeedbackText.Text = status.Error;
        UpdateStatusText.Text = controller.UpdateStatus;
        InstallUpdateButton.IsEnabled = controller.Settings.AvailableUpdate is not null;
        RenderLoginProgress();
    }
    private void LoginProgressChanged(object? sender, EventArgs e) => Dispatcher.BeginInvoke(new Action(RenderLoginProgress));
    internal void RenderLoginProgress()
    {
        var progress = controller.AutomaticLoginProgress;
        LoginPanel.Visibility = progress is null ? Visibility.Collapsed : Visibility.Visible;
        if (progress is not null)
        {
            LoginProgressText.Text = progress.Message;
            LoginProgressBar.IsIndeterminate = controller.IsSigningIn && progress.Fraction is null;
            LoginProgressBar.Value = (progress.Fraction ?? 0) * 100;
            LoginProgressBar.Visibility = controller.IsSigningIn ? Visibility.Visible : Visibility.Collapsed;
            CancelLoginButton.Visibility = controller.IsSigningIn ? Visibility.Visible : Visibility.Collapsed;
            if (controller.IsSigningIn) ComponentStatus.Text = progress.Message;
        }
        SetActionButtons(SettingsTabs,!busy && !controller.IsSigningIn);
        InstallUpdateButton.IsEnabled = !busy && !controller.IsSigningIn && controller.Settings.AvailableUpdate is not null;
    }
    private static void SetActionButtons(DependencyObject parent, bool enabled)
    {
        for (var i = 0; i < System.Windows.Media.VisualTreeHelper.GetChildrenCount(parent); i++)
        {
            var child = System.Windows.Media.VisualTreeHelper.GetChild(parent,i);
            if (child is System.Windows.Controls.Button button) button.IsEnabled = enabled;
            else SetActionButtons(child,enabled);
        }
    }
    private void CancelLogin_Click(object sender, RoutedEventArgs e) => controller.CancelAutomaticLogin();
    internal void UpdateIcon()
    {
        if (!File.Exists(Branding.IconPath)) return;
        using var icon = Branding.LoadIcon(64);
        var source = Imaging.CreateBitmapSourceFromHIcon(icon.Handle, Int32Rect.Empty, BitmapSizeOptions.FromEmptyOptions()); source.Freeze();
        Logo.Source = source; IconPreview.Source = source;
        IconChoiceText.Text = controller.Settings.IconChoice switch { "SNU" => "SNU logo", "Custom" => "Your custom image", _ => "SNUETL default" };
    }
    private async Task RunAsync(Func<Task> operation, string progress, string success)
    {
        if (busy || controller.IsSigningIn) return; busy = true; FeedbackText.Text = progress; RenderLoginProgress();
        try { await operation(); FeedbackText.Text = success; UpdateIcon(); }
        catch (OperationCanceledException) { FeedbackText.Text = "Cancelled. You can retry when ready."; }
        catch (Exception e) { FeedbackText.Text = e.Message; }
        finally { busy = false; Render(controller.Status); }
    }
    private async void Connect_Click(object sender, RoutedEventArgs e) => await RunAsync(controller.ConnectAutomaticallyAsync, "Preparing automatic login…", "Connected. Your courses are syncing in the background.");
    private async void ManualConnect_Click(object sender, RoutedEventArgs e)
    { var token = TokenBox.Password; TokenBox.Clear(); await RunAsync(() => controller.ConnectManualAsync(token), "Checking your token…", "Account connected."); }
    private async void SignOut_Click(object sender, RoutedEventArgs e) => await RunAsync(controller.DisconnectAsync, "Signing out…", "Signed out.");
    private void Open_Click(object sender, RoutedEventArgs e) => controller.OpenFolder();
    private void TokenSettings_Click(object sender, RoutedEventArgs e) => ClientController.OpenTokenSettings();
    private async void Browse_Click(object sender, RoutedEventArgs e)
    {
        using var dialog = new Forms.FolderBrowserDialog { Description = "Choose your SNUETL sync folder", UseDescriptionForTitle = true, InitialDirectory = controller.Settings.SyncRoot };
        if (dialog.ShowDialog() == Forms.DialogResult.OK) await RunAsync(() => controller.ChangeRootAsync(dialog.SelectedPath), "Setting up your folder…", "Sync folder saved.");
    }
    private async void DefaultIcon_Click(object sender, RoutedEventArgs e) => await RunAsync(() => controller.SetAppearanceAsync("Default"), "Updating your icon…", "Default icon applied to Explorer and the tray.");
    private async void SnuIcon_Click(object sender, RoutedEventArgs e) => await RunAsync(() => controller.SetAppearanceAsync("SNU"), "Downloading the SNU logo…", "SNU logo applied to Explorer and the tray.");
    private async void CustomIcon_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Microsoft.Win32.OpenFileDialog { Title = "Choose your SNUETL icon", Filter = "Images|*.png;*.jpg;*.jpeg;*.bmp;*.ico", CheckFileExists = true };
        if (dialog.ShowDialog(Window.GetWindow(this)) == true) await RunAsync(() => controller.SetAppearanceAsync("Custom", dialog.FileName), "Preparing your icon…", "Custom icon applied to Explorer and the tray.");
    }
    internal bool SavePreferences()
    {
        if (busy && !controller.IsSigningIn) { FeedbackText.Text = "Please wait for the current operation to finish."; return false; }
        if (!int.TryParse(IntervalBox.Text, out var minutes) || minutes is < 1 or > 1440)
        {
            FeedbackText.Text = "Enter a whole number from 1 to 1,440 minutes.";
            IntervalBox.Focus();
            return false;
        }
        var offsets = new List<double>();
        foreach (var part in ReminderHours.Text.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            if (!double.TryParse(part, System.Globalization.NumberStyles.Number, System.Globalization.CultureInfo.InvariantCulture, out var hours) || !double.IsFinite(hours) || hours <= 0 || hours > 720)
            { FeedbackText.Text = "Reminder offsets must be hours greater than 0 and no more than 720."; return false; }
            offsets.Add(hours);
        }
        CaptureCategories();
        controller.SaveNotifications(new NotificationPreferences { Categories = selections["*"], Courses = selections.Where(p => p.Key != "*").ToDictionary(p => p.Key,p => p.Value), ReminderHours = offsets.Distinct().OrderDescending().ToArray() });
        controller.SavePreferences(StartupCheck.IsChecked == true, minutes);
        return true;
    }
    private void CaptureCategories()
    {
        if (NotificationChoices.Children.Count > 0)
            selections[notificationCourse] = NotificationChoices.Children.OfType<System.Windows.Controls.CheckBox>().Where(c => c.IsChecked == true).Select(c => (string)c.Tag).ToArray();
    }
    private void NotificationCourse_Changed(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (controller is null || NotificationCourse.SelectedItem is not AcademicCourse course) return;
        CaptureCategories(); notificationCourse = course.Id;
        var selected = selections.GetValueOrDefault(course.Id, selections.GetValueOrDefault("*", []));
        NotificationChoices.Children.Clear();
        foreach (var (key,label) in Categories) NotificationChoices.Children.Add(new System.Windows.Controls.CheckBox { Content = label, Tag = key, IsChecked = selected.Contains(key), Margin = new Thickness(0,6,0,6) });
    }
    private void RepairStartup_Click(object sender, RoutedEventArgs e)
    { SettingsStore.ConfigureStartup(StartupCheck.IsChecked == true); StartupStatusText.Text = SettingsStore.StartupStatus; }
    private async void InstallAddon_Click(object sender, RoutedEventArgs e)
    {
        if (DownloadConsent.IsChecked != true) { FeedbackText.Text = "Select the optional download checkbox first."; return; }
        await RunAsync(controller.InstallSignInAsync, "Downloading and verifying automatic sign-in…", "Automatic sign-in installed.");
        ComponentStatus.Text = SignInAddon.Installed ? "Installed" : "Not installed — manual-token setup remains available";
    }
    private async void LocalAddon_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Microsoft.Win32.OpenFileDialog { Filter = "SNUETL sign-in component|SNUETL-SignIn-*.zip", CheckFileExists = true };
        if (dialog.ShowDialog(Window.GetWindow(this)) == true) await RunAsync(() => controller.InstallSignInFromArchiveAsync(dialog.FileName), "Verifying sign-in component…", "Automatic sign-in installed.");
        ComponentStatus.Text = SignInAddon.Installed ? "Installed" : "Not installed";
    }
    private async void RemoveAddon_Click(object sender, RoutedEventArgs e)
    { await RunAsync(SignInAddon.RemoveAsync, "Removing optional component…", "Automatic sign-in removed. Your account is unchanged."); ComponentStatus.Text = SignInAddon.Installed ? "Installed" : "Not installed"; }
    private void Done_Click(object sender, RoutedEventArgs e) { if (SavePreferences()) FeedbackText.Text = "Settings saved."; }
    private void RefreshGitHubAccess()
    {
        try { GitHubAccessStatus.Text = string.IsNullOrEmpty(GitHubCredential.Read()) ? "No GitHub release token saved" : "Private GitHub release access is saved"; }
        catch (Exception ex) { GitHubAccessStatus.Text = "Credential storage unavailable: " + ex.Message; }
    }
    private async void SaveGitHub_Click(object sender, RoutedEventArgs e)
    {
        var token = GitHubTokenBox.Password; GitHubTokenBox.Clear();
        await RunAsync(async () => { GitHubCredential.Save(token); await controller.CheckForUpdatesAsync(true); }, "Saving GitHub access…", "Release access saved in Windows Credential Manager.");
        RefreshGitHubAccess(); Render(controller.Status);
    }
    private void RemoveGitHub_Click(object sender, RoutedEventArgs e)
    { try { GitHubCredential.Remove(); GitHubAccessStatus.Text = "Release access removed"; } catch (Exception ex) { FeedbackText.Text = ex.Message; } }
    private async void CheckUpdates_Click(object sender, RoutedEventArgs e)
    { await RunAsync(() => controller.CheckForUpdatesAsync(true), "Checking GitHub releases…", "Update check complete."); Render(controller.Status); }
    private async void InstallUpdate_Click(object sender, RoutedEventArgs e)
    {
        if (!SavePreferences()) return;
        UpdateProgress.Visibility = Visibility.Visible;
        var progress = new Progress<double>(value => { UpdateProgress.Value = value * 100; FeedbackText.Text = $"Downloading update… {value:P0}"; });
        await RunAsync(() => controller.InstallUpdateAsync(progress), "Downloading and verifying the update…", "Setup is open. SNUETL will close when installation begins.");
        UpdateProgress.Visibility = Visibility.Collapsed;
    }
    private void Uninstall_Click(object sender, RoutedEventArgs e)
    {
        if (!SavePreferences()) return;
        try { InstalledProduct.Uninstall(); FeedbackText.Text = "The Windows uninstaller is open. Downloaded files and local folders will be preserved."; }
        catch (Exception ex) { FeedbackText.Text = ex.Message; }
    }
    private void Update_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Microsoft.Win32.OpenFileDialog { Title = "Choose a newer SNUETL setup file", Filter = "SNUETL setup|SNUETLSetup*.exe", CheckFileExists = true };
        if (dialog.ShowDialog(Window.GetWindow(this)) != true) return;
        try
        {
            if (!SavePreferences()) return;
            Process.Start(new ProcessStartInfo(dialog.FileName) { UseShellExecute = true });
            FeedbackText.Text = "Setup is open. SNUETL will close automatically when the update begins.";
        }
        catch (Exception exception) { FeedbackText.Text = exception.Message; }
    }
}
