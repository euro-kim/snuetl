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
    private static readonly (string Key,string Label)[] Categories = [("announcements",L.T("공지사항")),("assignments",L.T("과제 및 마감 변경")),("reminders",L.T("마감 사전 알림")),("grades",L.T("성적 및 피드백")),("discussions",L.T("토론 활동")),("files",L.T("파일 업데이트"))];
    public SettingsView(ClientController controller)
    {
        InitializeComponent(); this.controller = controller;
        LanguageChoice.SelectedValue = controller.Settings.Language;
        StartupCheck.IsChecked = controller.Settings.StartWithWindows;
        IntervalBox.Text = controller.Settings.RefreshMinutes.ToString(System.Globalization.CultureInfo.InvariantCulture);
        WelcomeText.Text = controller.Settings.SetupCompleted ? L.T("SNUETL 설정") : L.T("SNUETL 시작하기");
        VersionText.Text = L.Pair($"버전 {Branding.Version}", $"Version {Branding.Version}");
        selections["*"] = controller.Settings.Notifications.Categories;
        foreach (var pair in controller.Settings.Notifications.Courses) selections[pair.Key] = pair.Value;
        NotificationCourse.ItemsSource = new[] { new AcademicCourse { Id = "*", Name = L.T("모든 강좌 기본값") } }.Concat(controller.Academic.Courses);
        ReminderHours.Text = string.Join(", ",controller.Settings.Notifications.ReminderHours);
        NotificationCourse.SelectedIndex = 0;
        StartupStatusText.Text = KoreanText.Status(SettingsStore.StartupStatus);
        NotificationStatus.Text = KoreanText.Status(NativeNotifications.Status);
        ComponentDescription.Text = SignInAddon.Description;
        ComponentStatus.Text = SignInAddon.Installed ? L.T("설치됨") : L.T("자동 로그인 시 설치됩니다");
        RefreshGitHubAccess();
        Render(controller.Status); UpdateIcon();
        Loaded += (_, _) => { controller.LoginProgressChanged += LoginProgressChanged; RenderLoginProgress(); };
        Unloaded += (_, _) => controller.LoginProgressChanged -= LoginProgressChanged;
        SettingsTabs.SelectionChanged += (_, _) => Dispatcher.BeginInvoke(new Action(RenderLoginProgress));
    }
    public void Render(ClientStatus status)
    {
        ComponentStatus.Text = SignInAddon.Installed ? L.T("설치됨 · 자동 로그인 사용 가능") : L.T("자동 로그인 시 설치됩니다");
        AccountText.Text = KoreanText.Status(status.Account); RootText.Text = status.Root;
        AccountExpiryText.Text = controller.AccountExpiryDescription;
        var connected = status.Account.StartsWith("Connected", StringComparison.Ordinal);
        AccountExpiryText.Visibility = connected ? Visibility.Visible : Visibility.Collapsed;
        SignInPanel.Visibility = connected ? Visibility.Collapsed : Visibility.Visible;
        SignOutButton.Visibility = connected ? Visibility.Visible : Visibility.Collapsed;
        if (status.Error is not null) FeedbackText.Text = L.Status(status.Error);
        UpdateStatusText.Text = controller.UpdateStatus;
        InstallUpdateButton.IsEnabled = controller.Settings.AvailableUpdate is not null;
        RenderLoginProgress();
    }
    private void LoginProgressChanged(object? sender, EventArgs e) => Dispatcher.BeginInvoke(new Action(RenderLoginProgress));
    internal void SelectUpdates() => SettingsTabs.SelectedIndex = 4;
    internal void RenderLoginProgress()
    {
        var progress = controller.AutomaticLoginProgress;
        LoginPanel.Visibility = progress is null ? Visibility.Collapsed : Visibility.Visible;
        if (progress is not null)
        {
            LoginProgressText.Text = L.Status(progress.Message);
            LoginProgressBar.IsIndeterminate = controller.IsSigningIn && progress.Fraction is null;
            LoginProgressBar.Value = (progress.Fraction ?? 0) * 100;
            LoginProgressBar.Visibility = controller.IsSigningIn ? Visibility.Visible : Visibility.Collapsed;
            CancelLoginButton.Visibility = controller.IsSigningIn ? Visibility.Visible : Visibility.Collapsed;
            if (controller.IsSigningIn) ComponentStatus.Text = L.Status(progress.Message);
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
        IconChoiceText.Text = controller.Settings.IconChoice switch { "SNU" => L.T("SNU 로고 기본값"), "Custom" => L.T("사용자 지정 이미지"), _ => L.T("S 문자 아이콘") };
    }
    private async Task RunAsync(Func<Task> operation, string progress, string success)
    {
        if (busy || controller.IsSigningIn) return; busy = true; FeedbackText.Text = progress; RenderLoginProgress();
        try { await operation(); FeedbackText.Text = success; UpdateIcon(); }
        catch (OperationCanceledException) { FeedbackText.Text = L.T("취소했습니다. 다시 시도할 수 있습니다."); }
        catch (Exception e) { FeedbackText.Text = L.Error(e); }
        finally { busy = false; Render(controller.Status); }
    }
    private async void Connect_Click(object sender, RoutedEventArgs e)
    {
        await RunAsync(controller.ConnectAutomaticallyAsync, L.T("자동 로그인 준비 중…"), L.T("연결했습니다. 수업 자료를 동기화합니다."));
        Render(controller.Status);
    }
    private void Language_Changed(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (controller is not null && LanguageChoice.SelectedValue is string language)
            controller.SaveLanguage(language);
    }
    private void PasteToken_Click(object sender, RoutedEventArgs e)
    {
        try { if (System.Windows.Clipboard.ContainsText()) TokenBox.Password = System.Windows.Clipboard.GetText().Trim(); TokenBox.Focus(); }
        catch (System.Runtime.InteropServices.ExternalException) { FeedbackText.Text = L.Pair("클립보드가 사용 중입니다. 잠시 후 다시 붙여 넣으세요.", "Clipboard is busy. Please try pasting again."); }
    }
    private async void ManualConnect_Click(object sender, RoutedEventArgs e)
    {
        if (busy || controller.IsSigningIn) return;
        var token = TokenBox.Password.Trim();
        if (token.Length == 0) { FeedbackText.Text = L.T("먼저 API 키를 붙여 넣으세요."); TokenBox.Focus(); return; }
        var connected = false;
        await RunAsync(async () => { await controller.ConnectManualAsync(token); connected = true; },
            L.T("API 키 확인 중…"), L.T("연결 확인 완료. 계정이 연결되었습니다."));
        if (connected) TokenBox.Clear();
    }
    private async void SignOut_Click(object sender, RoutedEventArgs e) => await RunAsync(controller.DisconnectAsync, L.T("로그아웃 중…"), L.T("로그아웃했습니다."));
    private void Open_Click(object sender, RoutedEventArgs e) => controller.OpenFolder();
    private void TokenSettings_Click(object sender, RoutedEventArgs e) => ClientController.OpenTokenSettings();
    private async void Browse_Click(object sender, RoutedEventArgs e)
    {
        using var dialog = new Forms.FolderBrowserDialog { Description = L.T("동기화 폴더 선택"), UseDescriptionForTitle = true, InitialDirectory = controller.Settings.SyncRoot };
        if (dialog.ShowDialog() == Forms.DialogResult.OK) await RunAsync(() => controller.ChangeRootAsync(dialog.SelectedPath), L.T("폴더 설정 중…"), L.T("동기화 폴더를 저장했습니다."));
    }
    private async void DefaultIcon_Click(object sender, RoutedEventArgs e) => await RunAsync(() => controller.SetAppearanceAsync("Default"), L.T("아이콘 적용 중…"), L.T("S 문자 아이콘을 적용했습니다."));
    private async void SnuIcon_Click(object sender, RoutedEventArgs e) => await RunAsync(() => controller.SetAppearanceAsync("SNU"), L.T("SNU 로고 적용 중…"), L.T("SNU 로고를 적용했습니다."));
    private async void CustomIcon_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Microsoft.Win32.OpenFileDialog { Title = L.T("아이콘 이미지 선택"), Filter = "Images|*.png;*.jpg;*.jpeg;*.bmp;*.ico", CheckFileExists = true };
        if (dialog.ShowDialog(Window.GetWindow(this)) == true) await RunAsync(() => controller.SetAppearanceAsync("Custom", dialog.FileName), L.T("아이콘 준비 중…"), L.T("사용자 지정 아이콘을 적용했습니다."));
    }
    internal bool SavePreferences()
    {
        if (busy && !controller.IsSigningIn) { FeedbackText.Text = L.T("진행 중인 작업이 끝날 때까지 기다려 주세요."); return false; }
        if (!int.TryParse(IntervalBox.Text, out var minutes) || minutes is < 1 or > 1440)
        {
            FeedbackText.Text = L.T("1~1,440 사이의 정수를 입력하세요.");
            IntervalBox.Focus();
            return false;
        }
        var offsets = new List<double>();
        foreach (var part in ReminderHours.Text.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            if (!double.TryParse(part, System.Globalization.NumberStyles.Number, System.Globalization.CultureInfo.InvariantCulture, out var hours) || !double.IsFinite(hours) || hours <= 0 || hours > 720)
            { FeedbackText.Text = L.T("알림 시간은 0보다 크고 720 이하인 값으로 입력하세요."); return false; }
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
    { SettingsStore.ConfigureStartup(StartupCheck.IsChecked == true); StartupStatusText.Text = KoreanText.Status(SettingsStore.StartupStatus); }
    private async void InstallAddon_Click(object sender, RoutedEventArgs e)
    {
        if (DownloadConsent.IsChecked != true) { FeedbackText.Text = L.T("구성 요소 다운로드 항목을 선택하세요."); return; }
        await RunAsync(controller.InstallSignInAsync, L.T("자동 로그인 구성 요소 다운로드 중…"), L.T("자동 로그인 구성 요소를 설치했습니다."));
        ComponentStatus.Text = SignInAddon.Installed ? L.T("설치됨") : L.T("자동 로그인 시 설치됩니다");
    }
    private async void LocalAddon_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Microsoft.Win32.OpenFileDialog { Filter = "SNUETL sign-in component|SNUETL-SignIn-*.zip", CheckFileExists = true };
        if (dialog.ShowDialog(Window.GetWindow(this)) == true) await RunAsync(() => controller.InstallSignInFromArchiveAsync(dialog.FileName), L.T("구성 요소 확인 중…"), L.T("자동 로그인 구성 요소를 설치했습니다."));
        ComponentStatus.Text = SignInAddon.Installed ? L.T("설치됨") : L.T("설치되지 않음");
    }
    private async void RemoveAddon_Click(object sender, RoutedEventArgs e)
    { await RunAsync(SignInAddon.RemoveAsync, L.T("구성 요소 제거 중…"), L.T("구성 요소를 제거했습니다. 계정 연결은 유지됩니다.")); ComponentStatus.Text = SignInAddon.Installed ? L.T("설치됨") : L.T("설치되지 않음"); }
    private void Done_Click(object sender, RoutedEventArgs e) { if (SavePreferences()) FeedbackText.Text = L.T("설정을 저장했습니다."); }
    private void RefreshGitHubAccess()
    {
        try { GitHubAccessStatus.Text = string.IsNullOrEmpty(GitHubCredential.Read()) ? L.T("저장된 GitHub 토큰 없음") : L.T("GitHub 토큰 저장됨"); }
        catch (Exception ex) { GitHubAccessStatus.Text = L.T("자격 증명 저장소 오류: ") + L.Error(ex); }
    }
    private async void SaveGitHub_Click(object sender, RoutedEventArgs e)
    {
        var token = GitHubTokenBox.Password; GitHubTokenBox.Clear();
        await RunAsync(async () => { GitHubCredential.Save(token); await controller.CheckForUpdatesAsync(true); }, L.T("GitHub 토큰 저장 중…"), L.T("Windows 자격 증명 관리자에 저장했습니다."));
        RefreshGitHubAccess(); Render(controller.Status);
    }
    private void RemoveGitHub_Click(object sender, RoutedEventArgs e)
    { try { GitHubCredential.Remove(); GitHubAccessStatus.Text = L.T("GitHub 토큰을 제거했습니다"); } catch (Exception ex) { FeedbackText.Text = L.Error(ex); } }
    private async void CheckUpdates_Click(object sender, RoutedEventArgs e)
    { await RunAsync(() => controller.CheckForUpdatesAsync(true), L.T("새 버전 확인 중…"), L.T("업데이트 확인을 마쳤습니다.")); Render(controller.Status); }
    private async void InstallUpdate_Click(object sender, RoutedEventArgs e)
    {
        if (!SavePreferences()) return;
        UpdateProgress.Visibility = Visibility.Visible;
        var progress = new Progress<double>(value => { UpdateProgress.Value = value * 100; FeedbackText.Text = L.Pair($"업데이트 다운로드 중… {value:P0}", $"Downloading update… {value:P0}"); });
        await RunAsync(() => controller.InstallUpdateAsync(progress), L.T("업데이트 다운로드 및 확인 중…"), L.T("설치 프로그램을 열었습니다. 설치가 시작되면 앱이 종료됩니다."));
        UpdateProgress.Visibility = Visibility.Collapsed;
    }
    private void Uninstall_Click(object sender, RoutedEventArgs e)
    {
        if (!SavePreferences()) return;
        try { InstalledProduct.Uninstall(); FeedbackText.Text = L.T("제거 프로그램을 열었습니다. 내려받은 파일과 로컬 폴더는 보존됩니다."); }
        catch (Exception ex) { FeedbackText.Text = L.Error(ex); }
    }
    private void Update_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Microsoft.Win32.OpenFileDialog { Title = L.T("새 버전 설치 파일 선택"), Filter = "SNUETL setup|SNUETLSetup*.exe", CheckFileExists = true };
        if (dialog.ShowDialog(Window.GetWindow(this)) != true) return;
        try
        {
            if (!SavePreferences()) return;
            Process.Start(new ProcessStartInfo(dialog.FileName) { UseShellExecute = true });
            FeedbackText.Text = L.T("설치 프로그램을 열었습니다. 업데이트 시 앱이 종료됩니다.");
        }
        catch (Exception exception) { FeedbackText.Text = L.Error(exception); }
    }
}
