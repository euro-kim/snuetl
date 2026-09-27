using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
namespace Snuetl.Windows;
internal static class GitHubCredential
{
    internal const string Target = "snuetl-windows-github";
    internal static string? Read()
    {
        if (!CredRead(Target,1,0,out var pointer)) { if (Marshal.GetLastWin32Error() == 1168) return null; throw new Win32Exception(Marshal.GetLastWin32Error()); }
        try { var value = Marshal.PtrToStructure<Credential>(pointer); return Marshal.PtrToStringUni(value.Blob, checked((int)value.BlobSize / 2)); }
        finally { CredFree(pointer); }
    }
    internal static void Save(string token)
    {
        var bytes = Encoding.Unicode.GetBytes(token.Trim());
        if (bytes.Length == 0 || bytes.Length > 2500) throw new IOException("Enter a GitHub access token.");
        var pointer = Marshal.AllocHGlobal(bytes.Length);
        try
        {
            Marshal.Copy(bytes,0,pointer,bytes.Length);
            var value = new Credential { Type = 1, TargetName = Target, BlobSize = (uint)bytes.Length, Blob = pointer, Persist = 2, UserName = "GitHub release access" };
            if (!CredWrite(ref value,0)) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        finally { Marshal.Copy(new byte[bytes.Length],0,pointer,bytes.Length); Marshal.FreeHGlobal(pointer); Array.Clear(bytes); }
    }
    internal static void Remove() { if (!CredDelete(Target,1,0) && Marshal.GetLastWin32Error() != 1168) throw new Win32Exception(Marshal.GetLastWin32Error()); }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct Credential
    {
        public uint Flags, Type;
        public string TargetName;
        public string? Comment;
        public long LastWritten;
        public uint BlobSize;
        public IntPtr Blob;
        public uint Persist, AttributeCount;
        public IntPtr Attributes;
        public string? TargetAlias, UserName;
    }
    [DllImport("advapi32.dll", EntryPoint="CredReadW", CharSet=CharSet.Unicode, SetLastError=true)] private static extern bool CredRead(string target,uint type,uint flags,out IntPtr credential);
    [DllImport("advapi32.dll", EntryPoint="CredWriteW", CharSet=CharSet.Unicode, SetLastError=true)] private static extern bool CredWrite(ref Credential credential,uint flags);
    [DllImport("advapi32.dll", EntryPoint="CredDeleteW", CharSet=CharSet.Unicode, SetLastError=true)] private static extern bool CredDelete(string target,uint type,uint flags);
    [DllImport("advapi32.dll")] private static extern void CredFree(IntPtr credential);
}
