using System.Diagnostics;
using Microsoft.Win32;
namespace Snuetl.Windows;
internal static class InstalledProduct
{
    internal static void Uninstall()
    {
        foreach (var hive in new[] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
        foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
        {
        using var registry = RegistryKey.OpenBaseKey(hive, view);
        using var root = registry.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall");
        if (root is not null)
        {
            foreach (var name in root.GetSubKeyNames().OrderBy(n => n,StringComparer.Ordinal))
            {
                using var product = root.OpenSubKey(name);
                if (product?.GetValue("DisplayName") as string != "SNUETL Setup") continue;
                var command = product.GetValue("UninstallString") as string;
                if (command?.StartsWith('"') == true)
                {
                    var end = command.IndexOf('"',1);
                    if (end > 1 && File.Exists(command[1..end]))
                    { Process.Start(new ProcessStartInfo(command[1..end],command[(end+1)..]) { UseShellExecute = true }); return; }
                }
            }
            foreach (var name in root.GetSubKeyNames())
            {
                using var product = root.OpenSubKey(name);
                if (product?.GetValue("DisplayName") as string == "SNUETL" && Guid.TryParse(name,out var id))
                { Process.Start(new ProcessStartInfo("msiexec.exe",$"/x {{{id}}}") { UseShellExecute = true }); return; }
            }
        }
        }
        throw new IOException("This copy is not registered as an installed application. Install SNUETLSetup.exe first, or remove it from Windows Settings → Installed apps.");
    }
}
