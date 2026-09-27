using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace Snuetl.Windows;

internal static class CloudFilesNative
{
    internal const uint FileAttributeNormal = 0x00000080;
    internal const uint FileAttributePinned = 0x00080000;
    internal const uint FileAttributeUnpinned = 0x00100000;
    internal const uint FileAttributeRecallOnDataAccess = 0x00400000;

    internal static bool IsCloudOnly(uint attributes) =>
        (attributes & ((uint)FileAttributes.Offline | FileAttributeRecallOnDataAccess)) != 0;
    internal const uint PlaceholderMarkInSync = 0x00000002;
    internal const uint RegisterUpdate = 0x00000001;
    internal const uint RegisterMarkRootInSync = 0x00000004;
    internal const uint ConnectRequireFullPath = 0x00000004;
    internal const uint UpdateVerifyInSync = 0x00000001;
    internal const uint UpdateMarkInSync = 0x00000002;
    internal const uint UpdateDehydrate = 0x00000004;
    internal const uint OpenFileExclusive = 0x00000001;
    internal const uint OpenFileWriteAccess = 0x00000002;
    internal const uint PinStatePinned = 1;
    internal const uint PinStateUnpinned = 2;
    internal const uint InSyncStateInSync = 1;
    internal const int CallbackFetchData = 0;
    internal const int CallbackCancelFetchData = 2;
    internal const int CallbackNone = -1;
    internal const int OperationTransferData = 0;
    internal const int StatusSuccess = 0;
    internal const int StatusUnsuccessful = unchecked((int)0xC0000001);

    [StructLayout(LayoutKind.Sequential)]
    internal struct ConnectionKey { public long Internal; }

    [StructLayout(LayoutKind.Sequential)]
    internal struct TransferKey { public long Internal; }

    [StructLayout(LayoutKind.Sequential)]
    internal struct RequestKey { public long Internal; }

    [StructLayout(LayoutKind.Sequential)]
    internal struct FileBasicInfo
    {
        public long CreationTime;
        public long LastAccessTime;
        public long LastWriteTime;
        public long ChangeTime;
        public uint FileAttributes;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct FsMetadata
    {
        public FileBasicInfo BasicInfo;
        public long FileSize;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct SyncRegistration
    {
        public uint StructSize;
        [MarshalAs(UnmanagedType.LPWStr)] public string ProviderName;
        [MarshalAs(UnmanagedType.LPWStr)] public string ProviderVersion;
        public IntPtr SyncRootIdentity;
        public uint SyncRootIdentityLength;
        public IntPtr FileIdentity;
        public uint FileIdentityLength;
        public Guid ProviderId;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct Policy
    {
        public ushort Primary;
        public ushort Modifier;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct SyncPolicies
    {
        public uint StructSize;
        public Policy Hydration;
        public Policy Population;
        public uint InSync;
        public uint HardLink;
        public uint PlaceholderManagement;
    }

    [UnmanagedFunctionPointer(CallingConvention.Winapi)]
    internal delegate void Callback(IntPtr callbackInfo, IntPtr callbackParameters);

    [StructLayout(LayoutKind.Sequential)]
    internal struct CallbackRegistration
    {
        public int Type;
        public Callback? Callback;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct CallbackInfo
    {
        public uint StructSize;
        public ConnectionKey ConnectionKey;
        public IntPtr CallbackContext;
        public IntPtr VolumeGuidName;
        public IntPtr VolumeDosName;
        public uint VolumeSerialNumber;
        public long SyncRootFileId;
        public IntPtr SyncRootIdentity;
        public uint SyncRootIdentityLength;
        public long FileId;
        public long FileSize;
        public IntPtr FileIdentity;
        public uint FileIdentityLength;
        public IntPtr NormalizedPath;
        public TransferKey TransferKey;
        public byte PriorityHint;
        public IntPtr CorrelationVector;
        public IntPtr ProcessInfo;
        public RequestKey RequestKey;
    }

    // CF_CALLBACK_PARAMETERS starts with ULONG followed by an 8-byte-aligned
    // union. Explicit offsets are required on x64; a sequential managed struct
    // would incorrectly place Flags at offset 4.
    [StructLayout(LayoutKind.Explicit, Size = 64)]
    internal struct FetchDataParameters
    {
        [FieldOffset(0)] public uint ParamSize;
        [FieldOffset(8)] public uint Flags;
        [FieldOffset(16)] public long RequiredFileOffset;
        [FieldOffset(24)] public long RequiredLength;
        [FieldOffset(32)] public long OptionalFileOffset;
        [FieldOffset(40)] public long OptionalLength;
        [FieldOffset(48)] public long LastDehydrationTime;
        [FieldOffset(56)] public uint LastDehydrationReason;
    }

    [StructLayout(LayoutKind.Explicit, Size = 32)]
    internal struct CancelFetchDataParameters
    {
        [FieldOffset(0)] public uint ParamSize;
        [FieldOffset(8)] public uint Flags;
        [FieldOffset(16)] public long FileOffset;
        [FieldOffset(24)] public long Length;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct OperationInfo
    {
        public uint StructSize;
        public int Type;
        public ConnectionKey ConnectionKey;
        public TransferKey TransferKey;
        public IntPtr CorrelationVector;
        public IntPtr SyncStatus;
        public RequestKey RequestKey;
    }

    // CF_OPERATION_PARAMETERS has the same ULONG + aligned-union layout.
    [StructLayout(LayoutKind.Explicit, Size = 40)]
    internal struct TransferDataParameters
    {
        [FieldOffset(0)] public uint ParamSize;
        [FieldOffset(8)] public uint Flags;
        [FieldOffset(12)] public int CompletionStatus;
        [FieldOffset(16)] public IntPtr Buffer;
        [FieldOffset(24)] public long Offset;
        [FieldOffset(32)] public long Length;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct PlaceholderCreateInfo
    {
        [MarshalAs(UnmanagedType.LPWStr)] public string RelativeFileName;
        public FsMetadata FsMetadata;
        public IntPtr FileIdentity;
        public uint FileIdentityLength;
        public uint Flags;
        public int Result;
        public long CreateUsn;
    }

    [DllImport("CldApi.dll", CharSet = CharSet.Unicode)]
    internal static extern int CfRegisterSyncRoot(
        string syncRootPath,
        ref SyncRegistration registration,
        ref SyncPolicies policies,
        uint registerFlags);

    [DllImport("CldApi.dll", CharSet = CharSet.Unicode)]
    internal static extern int CfUnregisterSyncRoot(string syncRootPath);

    [DllImport("CldApi.dll", CharSet = CharSet.Unicode)]
    internal static extern int CfConnectSyncRoot(
        string syncRootPath,
        [In] CallbackRegistration[] callbackTable,
        IntPtr callbackContext,
        uint connectFlags,
        out ConnectionKey connectionKey);

    [DllImport("CldApi.dll")]
    internal static extern int CfDisconnectSyncRoot(ConnectionKey connectionKey);

    [DllImport("CldApi.dll", CharSet = CharSet.Unicode)]
    internal static extern int CfCreatePlaceholders(
        string baseDirectoryPath,
        [In, Out] PlaceholderCreateInfo[] placeholderArray,
        uint placeholderCount,
        uint createFlags,
        out uint entriesProcessed);

    [DllImport("CldApi.dll")]
    internal static extern int CfExecute(
        ref OperationInfo operationInfo,
        ref TransferDataParameters operationParameters);

    [DllImport("CldApi.dll")]
    internal static extern int CfOpenFileWithOplock(
        [MarshalAs(UnmanagedType.LPWStr)] string filePath,
        uint flags,
        out IntPtr protectedHandle);

    [DllImport("CldApi.dll")]
    internal static extern void CfCloseHandle(IntPtr protectedHandle);

    [DllImport("CldApi.dll")]
    internal static extern int CfUpdatePlaceholder(
        IntPtr fileHandle,
        ref FsMetadata metadata,
        IntPtr fileIdentity,
        uint fileIdentityLength,
        IntPtr dehydrateRangeArray,
        uint dehydrateRangeCount,
        uint updateFlags,
        IntPtr updateUsn,
        IntPtr overlapped);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    internal static extern SafeFileHandle CreateFileW(
        string path, uint desiredAccess, uint shareMode, IntPtr securityAttributes,
        uint creationDisposition, uint flags, IntPtr template);

    [DllImport("CldApi.dll")]
    internal static extern int CfHydratePlaceholder(
        SafeFileHandle fileHandle, long startingOffset, long length, uint flags, IntPtr overlapped);

    [DllImport("CldApi.dll")]
    internal static extern int CfDehydratePlaceholder(
        SafeFileHandle fileHandle, long startingOffset, long length, uint flags, IntPtr overlapped);

    [DllImport("CldApi.dll")]
    internal static extern int CfSetPinState(
        IntPtr fileHandle,
        uint pinState,
        uint pinFlags,
        IntPtr overlapped);

    [DllImport("CldApi.dll")]
    internal static extern int CfRevertPlaceholder(
        IntPtr fileHandle,
        uint revertFlags,
        IntPtr overlapped);

    [DllImport("CldApi.dll")]
    internal static extern int CfGetPlatformInfo(out PlatformInfo platformVersion);

    [StructLayout(LayoutKind.Sequential)]
    internal struct PlatformInfo
    {
        public uint BuildNumber;
        public uint RevisionNumber;
        public uint IntegrationNumber;
    }

    internal static void ThrowIfFailed(int result, string operation)
    {
        if (result < 0)
        {
            throw new InvalidOperationException($"{operation} failed with HRESULT 0x{result:X8}");
        }
    }
    [StructLayout(LayoutKind.Sequential)]
    internal struct AttributeTagInfo { public uint Attributes; public uint ReparseTag; }
    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool GetFileInformationByHandleEx(Microsoft.Win32.SafeHandles.SafeFileHandle handle,
        int informationClass, out AttributeTagInfo info, uint size);
    [DllImport("cldapi.dll")]
    internal static extern uint CfGetPlaceholderStateFromAttributeTag(uint attributes, uint reparseTag);
}
