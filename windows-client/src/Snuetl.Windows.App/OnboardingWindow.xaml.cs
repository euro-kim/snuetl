using System.Windows;

namespace Snuetl.Windows;

public partial class OnboardingWindow : Window
{
    private readonly ClientController controller;
    private readonly Action completed;
    private SettingsView? page;
    private bool closed;
    internal OnboardingWindow(ClientController controller, Action completed)
    {
        InitializeComponent(); this.controller = controller; this.completed = completed;
        SetReady(false);
        Closed += (_, _) => { closed = true; controller.CancelAutomaticLogin(); };
    }
    internal void SetReady(bool ready)
    {
        AutomaticChoice.IsEnabled = ManualChoice.IsEnabled = ready;
        ReadyText.Text = ready ? L.T("방법을 선택하기 전에는 브라우저를 다운로드하거나 로그인을 시작하지 않습니다.") : L.T("앱을 준비하는 중입니다…");
    }
    private void Automatic_Click(object sender, RoutedEventArgs e) => SelectMethod(true);
    private void Manual_Click(object sender, RoutedEventArgs e) => SelectMethod(false);
    internal void SelectMethod(bool automatic)
    {
        page = new SettingsView(controller);
        page.ConfigureOnboarding(automatic);
        page.ConnectionVerified += (_, _) =>
        {
            if (closed) return;
            if (!controller.CompleteOnboarding()) return;
            Close(); completed();
        };
        ConnectionHost.Content = page;
        ChoicePanel.Visibility = Visibility.Collapsed; ConnectionPanel.Visibility = Visibility.Visible;
        // Selecting a method reveals its instructions. Only the next explicit
        // connect button may start a download or submit a key.
    }
    private void Back_Click(object sender, RoutedEventArgs e)
    {
        if (controller.IsSigningIn || page?.IsBusy == true) return;
        ConnectionHost.Content = null; page = null;
        ConnectionPanel.Visibility = Visibility.Collapsed; ChoicePanel.Visibility = Visibility.Visible;
    }
}
