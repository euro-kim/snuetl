using System.ComponentModel;
using System.Runtime.InteropServices;

namespace Snuetl.Windows;

internal static class UninstallCleanup
{
    internal static void Run()
    {
        var store = new SettingsStore();
        var settings = store.Load();
        var syncRoot = ExplorerSyncRoot.RegisteredPath() ?? settings.SyncRoot;
        CloudFilesProvider.PrepareForUninstall(syncRoot, store.DataDirectory);
        // StorageProviderSyncRootManager removes the provider's Explorer namespace too.
        ExplorerSyncRoot.RemoveNavigationEntry();
        SettingsStore.RemoveStartup();
        NativeNotifications.Remove();
        Microsoft.Win32.Registry.CurrentUser.DeleteSubKeyTree(@"Software\SNUETL", false);
        ForgetCredentials();
        CleanupPaths.PruneEmptyDirectories(Path.Combine(store.DataDirectory, "app"), syncRoot);
        CleanupPaths.RemoveAppData(store.DataDirectory, syncRoot, Path.Combine(store.DataDirectory, "app"));
    }
    internal static bool OwnsCredential(string target) => target == GitHubCredential.Target || target == "snuetl-windows" || target.EndsWith("@snuetl-windows", StringComparison.Ordinal);
    private static void ForgetCredentials()
    {
        if (!CredEnumerate(null, 0, out var count, out var array))
        {
            var error = Marshal.GetLastWin32Error();
            if (error == 1168) return;
            throw new Win32Exception(error);
        }
        try
        {
            for (var i = 0; i < count; i++)
            {
                var pointer = Marshal.ReadIntPtr(array, i * IntPtr.Size);
                var credential = Marshal.PtrToStructure<CredentialHeader>(pointer);
                var target = Marshal.PtrToStringUni(credential.TargetName);
                if (credential.Type == 1 && target is not null && OwnsCredential(target) && !CredDelete(target, 1, 0))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }
        finally { CredFree(array); }
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct CredentialHeader { public uint Flags; public uint Type; public IntPtr TargetName; }
    [DllImport("advapi32.dll", EntryPoint = "CredEnumerateW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredEnumerate(string? filter, uint flags, out int count, out IntPtr credentials);
    [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredDelete(string target, uint type, uint flags);
    [DllImport("advapi32.dll")] private static extern void CredFree(IntPtr buffer);
}
