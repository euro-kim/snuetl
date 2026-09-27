using System.Runtime.InteropServices;
using System.Security.Principal;
using global::Windows.Security.Cryptography;
using global::Windows.Storage;
using global::Windows.Storage.Provider;

namespace Snuetl.Windows;

internal static class ExplorerSyncRoot
{
    internal static string Id => $"SNUETL!{WindowsIdentity.GetCurrent().User?.Value
        ?? throw new InvalidOperationException("The Windows user identity is unavailable") }!single";

    internal static async Task RegisterAsync(string root, string? registrationId = null)
    {
        var folder = await StorageFolder.GetFolderFromPathAsync(root);
        var info = new StorageProviderSyncRootInfo
        {
            Id = registrationId ?? Id,
            Path = folder,
            DisplayNameResource = "SNUETL",
            IconResource = File.Exists(Branding.IconPath) ? Branding.IconPath + ",0" : Path.Combine(AppContext.BaseDirectory, "SNUETL.exe") + ",0",
            Version = typeof(ExplorerSyncRoot).Assembly.GetName().Version?.ToString() ?? "0.7.1",
            HydrationPolicy = StorageProviderHydrationPolicy.Full,
            HydrationPolicyModifier = StorageProviderHydrationPolicyModifier.None,
            PopulationPolicy = StorageProviderPopulationPolicy.AlwaysFull,
            InSyncPolicy = StorageProviderInSyncPolicy.FileLastWriteTime,
            HardlinkPolicy = StorageProviderHardlinkPolicy.None,
            AllowPinning = true,
            ShowSiblingsAsGroup = false,
            Context = CryptographicBuffer.ConvertStringToBinary(
                "{\"schema\":1,\"account\":\"single\"}", BinaryStringEncoding.Utf8),
        };
        StorageProviderSyncRootManager.Register(info);
        using (var registered = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager\" + (registrationId ?? Id)))
        {
            if (registered?.GetValue("NamespaceCLSID") is string clsid && Guid.TryParse(clsid, out _))
            {
                using var shell = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Classes\CLSID\" + clsid, writable: true);
                shell?.SetValue("SortOrderIndex", 66, Microsoft.Win32.RegistryValueKind.DWord);
            }
        }
        // Refresh Explorer's provider and folder caches without restarting Explorer.
        SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero);
    }

    internal static string? RegisteredPath(string? registrationId = null)
    {
        using var roots = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager\" + (registrationId ?? Id) + @"\UserSyncRoots");
        return roots?.GetValue(WindowsIdentity.GetCurrent().User!.Value) as string;
    }

    internal static bool TryUnregister(string root, string? registrationId = null)
    {
        var id = registrationId ?? Id;
        using var roots = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager\" + id + @"\UserSyncRoots");
        var registeredPath = roots?.GetValue(WindowsIdentity.GetCurrent().User!.Value) as string;
        if (registeredPath is null || !string.Equals(Path.GetFullPath(registeredPath), Path.GetFullPath(root), StringComparison.OrdinalIgnoreCase))
            return false;
        RemoveNavigationEntry(registrationId);
        SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero);
        return true;
    }

    internal static void RemoveNavigationEntry(string? registrationId = null)
    {
        var id = registrationId ?? Id;
        // Capture only the CLSID owned by our registered provider, never other cloud providers.
        using var registration = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(
            @"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager\" + id);
        var clsid = registration?.GetValue("NamespaceCLSID") as string;
        // Enumeration can return a cached snapshot after registration in this process.
        // Unregister the owned ID directly, even if it is missing from that snapshot.
        try { StorageProviderSyncRootManager.Unregister(id); }
        catch (COMException e) when (e.HResult == unchecked((int)0x80070490)) { }
        if (Guid.TryParse(clsid, out var guid))
        {
            var key = guid.ToString("B").ToUpperInvariant();
            Microsoft.Win32.Registry.CurrentUser.DeleteSubKeyTree(@"Software\Classes\CLSID\" + key, false);
            Microsoft.Win32.Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Explorer\Desktop\NameSpace\" + key, false);
        }
        SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero);
    }

    [DllImport("shell32.dll")]
    private static extern void SHChangeNotify(uint eventId, uint flags, IntPtr item1, IntPtr item2);
}
