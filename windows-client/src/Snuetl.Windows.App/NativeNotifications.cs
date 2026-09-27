using System.Security;
using Microsoft.Win32;
using Windows.Data.Xml.Dom;
using Windows.UI.Notifications;

namespace Snuetl.Windows;

internal static class NativeNotifications
{
    internal const string AppId = "SNUETL.Windows";
    internal const string Activator = "EB87E4D3-3C8D-493C-A763-B5330FBD4329";
    internal static string Status { get; private set; } = "Available after installation";
    internal static void Register()
    {
        var exe = SettingsStore.InstalledExecutable;
        if (exe is null) return;
        using (var key = Registry.CurrentUser.CreateSubKey(@"Software\Classes\snuetl"))
        {
            key.SetValue("", "URL:SNUETL"); key.SetValue("URL Protocol", "");
            using var command = key.CreateSubKey(@"shell\open\command");
            command.SetValue("", $"\"{exe}\" \"%1\"");
        }
        using (var key = Registry.CurrentUser.CreateSubKey(@"Software\Classes\AppUserModelId\" + AppId))
        { key.SetValue("DisplayName", "SNUETL"); key.SetValue("IconUri", Branding.IconPath); }
        try { Status = ToastNotificationManager.CreateToastNotifier(AppId).Setting.ToString(); }
        catch (Exception e) when (e is System.Runtime.InteropServices.COMException or InvalidOperationException) { Status = "Windows notifications unavailable"; }
    }
    internal static void Show(IReadOnlyList<AcademicEvent> events)
    {
        if (events.Count == 0 || SettingsStore.InstalledExecutable is null) return;
        try
        {
            var notifier = ToastNotificationManager.CreateToastNotifier(AppId);
            Status = notifier.Setting.ToString();
            if (notifier.Setting != NotificationSetting.Enabled) return;
            var files = events.All(e => e.Category == "files");
            var route = files ? "files" : "notifications";
            var title = events.Count == 1 ? events[0].Title : $"{events.Count} updates from SNU eTL";
            var body = events.Count == 1 ? events[0].Detail : string.Join(" · ", events.Take(3).Select(e => e.Title));
            var xml = new XmlDocument();
            xml.LoadXml($"<toast activationType='protocol' launch='snuetl://{route}'><visual><binding template='ToastGeneric'><text>{SecurityElement.Escape(title)}</text><text>{SecurityElement.Escape(body)}</text></binding></visual><audio silent='true'/></toast>");
            notifier.Show(new ToastNotification(xml) { Tag = files ? "files" : "academic", Group = "updates", ExpirationTime = DateTimeOffset.Now.AddDays(1) });
        }
        catch (Exception e) when (e is System.Runtime.InteropServices.COMException or InvalidOperationException or UnauthorizedAccessException)
        { Status = "Windows notifications unavailable — updates remain in the dashboard"; }
    }
    internal static void Remove()
    {
        try { ToastNotificationManager.History.Clear(AppId); } catch (Exception e) when (e is System.Runtime.InteropServices.COMException or InvalidOperationException) { }
        Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\" + AppId, false);
        Registry.CurrentUser.DeleteSubKeyTree(@"Software\Classes\snuetl", false);
        Registry.CurrentUser.DeleteSubKeyTree(@"Software\Classes\AppUserModelId\" + AppId, false);
    }
}
