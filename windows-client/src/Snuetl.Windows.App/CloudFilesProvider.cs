using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace Snuetl.Windows;

public sealed class CloudFilesProvider : IPlaceholderStore, IAsyncDisposable
{
    private static readonly Guid ProviderId = new("8532A890-8B27-4A75-9849-BFC7D892539D");
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
    private readonly string root;
    private readonly string indexPath;
    private readonly BackendClient backend;
    private readonly SemaphoreSlim indexLock = new(1, 1);
    private readonly ConcurrentDictionary<long, CancellationTokenSource> hydrations = new();
    private readonly CloudFilesNative.Callback fetchCallback;
    private readonly CloudFilesNative.Callback cancelCallback;
    private CloudFilesNative.ConnectionKey connectionKey;
    private bool connected;
    private Dictionary<string, IndexEntry> index = new(StringComparer.OrdinalIgnoreCase);

    private sealed record IndexEntry(
        string RelativePath,
        PlaceholderIdentity Identity,
        long Size,
        long LastWriteUtcTicks);

    private sealed record HydrationResult(
        [property: System.Text.Json.Serialization.JsonPropertyName("path")] string Path,
        [property: System.Text.Json.Serialization.JsonPropertyName("size")] long Size,
        [property: System.Text.Json.Serialization.JsonPropertyName("sha256")] string Sha256);

    public CloudFilesProvider(string root, string dataDirectory, BackendClient backend)
    {
        this.root = Path.GetFullPath(root);
        this.backend = backend;
        indexPath = Path.Combine(dataDirectory, "placeholder-index.json");
        fetchCallback = OnFetchData;
        cancelCallback = OnCancelFetchData;
        LoadIndex();
    }

    public void RegisterAndConnect()
    {
        if (!OperatingSystem.IsWindowsVersionAtLeast(10, 0, 16299))
        {
            throw new PlatformNotSupportedException("SNUETL requires Windows 10 version 1709 or newer");
        }
        if (RuntimeInformation.ProcessArchitecture != Architecture.X64)
        {
            throw new PlatformNotSupportedException("This release supports Windows x64 only");
        }
        CloudFilesNative.ThrowIfFailed(
            CloudFilesNative.CfGetPlatformInfo(out _),
            "Cloud Files platform detection");

        Directory.CreateDirectory(root);
        var drive = new DriveInfo(Path.GetPathRoot(root)!);
        if (!string.Equals(drive.DriveFormat, "NTFS", StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("The SNUETL folder must be on a local NTFS volume");
        }

        var identity = Encoding.UTF8.GetBytes("{\"schema\":1,\"account\":\"single\"}");
        var identityPointer = Marshal.AllocHGlobal(identity.Length);
        try
        {
            Marshal.Copy(identity, 0, identityPointer, identity.Length);
            var registration = new CloudFilesNative.SyncRegistration
            {
                StructSize = (uint)Marshal.SizeOf<CloudFilesNative.SyncRegistration>(),
                ProviderName = "SNUETL",
                ProviderVersion = typeof(CloudFilesProvider).Assembly.GetName().Version?.ToString() ?? "0.1.0",
                SyncRootIdentity = identityPointer,
                SyncRootIdentityLength = (uint)identity.Length,
                FileIdentity = IntPtr.Zero,
                FileIdentityLength = 0,
                ProviderId = ProviderId,
            };
            var policies = new CloudFilesNative.SyncPolicies
            {
                StructSize = (uint)Marshal.SizeOf<CloudFilesNative.SyncPolicies>(),
                Hydration = new CloudFilesNative.Policy { Primary = 2, Modifier = 0 },
                Population = new CloudFilesNative.Policy { Primary = 3, Modifier = 0 },
                // Data writes always clear in-sync; tracking last-write metadata
                // additionally makes ordinary editor saves visible.
                InSync = 0x00000010,
                HardLink = 0,
                PlaceholderManagement = 0,
            };
            var result = CloudFilesNative.CfRegisterSyncRoot(
                root,
                ref registration,
                ref policies,
                CloudFilesNative.RegisterMarkRootInSync);
            if (result < 0)
            {
                result = CloudFilesNative.CfRegisterSyncRoot(
                    root,
                    ref registration,
                    ref policies,
                    CloudFilesNative.RegisterUpdate | CloudFilesNative.RegisterMarkRootInSync);
            }
            CloudFilesNative.ThrowIfFailed(result, "Sync-root registration");
        }
        finally
        {
            Marshal.FreeHGlobal(identityPointer);
        }

        var callbacks = new[]
        {
            new CloudFilesNative.CallbackRegistration
            {
                Type = CloudFilesNative.CallbackFetchData,
                Callback = fetchCallback,
            },
            new CloudFilesNative.CallbackRegistration
            {
                Type = CloudFilesNative.CallbackCancelFetchData,
                Callback = cancelCallback,
            },
            new CloudFilesNative.CallbackRegistration
            {
                Type = CloudFilesNative.CallbackNone,
                Callback = null,
            },
        };
        CloudFilesNative.ThrowIfFailed(
            CloudFilesNative.CfConnectSyncRoot(
                root,
                callbacks,
                IntPtr.Zero,
                CloudFilesNative.ConnectRequireFullPath,
                out connectionKey),
            "Sync-root connection");
        connected = true;
    }

    public async Task<IReadOnlyList<LocalEntry>> SnapshotAsync(CancellationToken cancellationToken)
    {
        await indexLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            var result = new List<LocalEntry>();
            var indexedPaths = new HashSet<string>(index.Keys, StringComparer.OrdinalIgnoreCase);
            foreach (var item in index.Values.ToArray())
            {
                var path = Absolute(item.RelativePath);
                if (!File.Exists(path))
                {
                    index.Remove(item.RelativePath);
                    continue;
                }
                var info = new FileInfo(path);
                var attributes = (uint)info.Attributes;
                var hydrated = (attributes & (uint)FileAttributes.Offline) == 0;
                var pinned = (attributes & CloudFilesNative.FileAttributePinned) != 0;
                var inSync = info.LastWriteTimeUtc.Ticks == item.LastWriteUtcTicks;
                result.Add(new LocalEntry(
                    item.RelativePath,
                    true,
                    item.Identity,
                    inSync,
                    hydrated,
                    pinned));
            }

            if (Directory.Exists(root))
            {
                foreach (var path in Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories))
                {
                    var relative = Relative(path);
                    if (!indexedPaths.Contains(relative))
                    {
                        result.Add(new LocalEntry(relative, false, null, false, true, false));
                    }
                }
            }
            SaveIndex();
            return result;
        }
        finally
        {
            indexLock.Release();
        }
    }

    public async Task CreateAsync(
        ManifestEntry entry,
        string relativePath,
        CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var absolute = Absolute(relativePath);
        var parent = Path.GetDirectoryName(absolute)!;
        Directory.CreateDirectory(parent);
        var identity = JsonSerializer.SerializeToUtf8Bytes(entry.Identity);
        var identityPointer = Marshal.AllocHGlobal(identity.Length);
        try
        {
            Marshal.Copy(identity, 0, identityPointer, identity.Length);
            var timestamp = ParseTimestamp(entry.UpdatedAt);
            var create = new CloudFilesNative.PlaceholderCreateInfo
            {
                RelativeFileName = Path.GetFileName(absolute),
                FsMetadata = new CloudFilesNative.FsMetadata
                {
                    BasicInfo = new CloudFilesNative.FileBasicInfo
                    {
                        CreationTime = timestamp,
                        LastAccessTime = timestamp,
                        LastWriteTime = timestamp,
                        ChangeTime = timestamp,
                        FileAttributes = CloudFilesNative.FileAttributeNormal,
                    },
                    FileSize = entry.Size,
                },
                FileIdentity = identityPointer,
                FileIdentityLength = (uint)identity.Length,
                Flags = CloudFilesNative.PlaceholderMarkInSync,
            };
            var values = new[] { create };
            CloudFilesNative.ThrowIfFailed(
                CloudFilesNative.CfCreatePlaceholders(parent, values, 1, 1, out var processed),
                "Placeholder creation");
            if (processed != 1 || values[0].Result < 0)
            {
                CloudFilesNative.ThrowIfFailed(values[0].Result, "Placeholder creation result");
                throw new InvalidOperationException("Cloud Files did not create the placeholder");
            }
        }
        finally
        {
            Marshal.FreeHGlobal(identityPointer);
        }

        var writeTicks = File.GetLastWriteTimeUtc(absolute).Ticks;
        await indexLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            index[Normalize(relativePath)] = new IndexEntry(
                Normalize(relativePath), entry.Identity, entry.Size, writeTicks);
            SaveIndex();
        }
        finally
        {
            indexLock.Release();
        }
    }

    public async Task UpdateAsync(
        LocalEntry local,
        ManifestEntry remote,
        CancellationToken cancellationToken)
    {
        var absolute = Absolute(local.RelativePath);
        var identity = JsonSerializer.SerializeToUtf8Bytes(remote.Identity);
        var pointer = Marshal.AllocHGlobal(identity.Length);
        IntPtr handle = IntPtr.Zero;
        try
        {
            Marshal.Copy(identity, 0, pointer, identity.Length);
            CloudFilesNative.ThrowIfFailed(
                CloudFilesNative.CfOpenFileWithOplock(
                    absolute,
                    CloudFilesNative.OpenFileExclusive | CloudFilesNative.OpenFileWriteAccess,
                    out handle),
                "Opening placeholder for update");
            var timestamp = ParseTimestamp(remote.UpdatedAt);
            var metadata = new CloudFilesNative.FsMetadata
            {
                BasicInfo = new CloudFilesNative.FileBasicInfo
                {
                    LastWriteTime = timestamp,
                    ChangeTime = timestamp,
                    FileAttributes = CloudFilesNative.FileAttributeNormal,
                },
                FileSize = remote.Size,
            };
            var flags = CloudFilesNative.UpdateVerifyInSync | CloudFilesNative.UpdateMarkInSync;
            if (local.IsPinned)
            {
                CloudFilesNative.ThrowIfFailed(
                    CloudFilesNative.CfSetPinState(
                        handle,
                        CloudFilesNative.PinStateUnpinned,
                        0,
                        IntPtr.Zero),
                    "Temporarily unpinning updated content");
            }
            flags |= CloudFilesNative.UpdateDehydrate;
            CloudFilesNative.ThrowIfFailed(
                CloudFilesNative.CfUpdatePlaceholder(
                    handle,
                    ref metadata,
                    pointer,
                    (uint)identity.Length,
                    IntPtr.Zero,
                    0,
                    flags,
                    IntPtr.Zero,
                    IntPtr.Zero),
                "Placeholder update");
            if (local.IsPinned)
            {
                CloudFilesNative.ThrowIfFailed(
                    CloudFilesNative.CfSetPinState(
                        handle,
                        CloudFilesNative.PinStatePinned,
                        0,
                        IntPtr.Zero),
                    "Restoring the pinned state");
            }
        }
        finally
        {
            if (handle != IntPtr.Zero)
            {
                CloudFilesNative.CfCloseHandle(handle);
            }
            Marshal.FreeHGlobal(pointer);
        }

        await indexLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            index[Normalize(local.RelativePath)] = new IndexEntry(
                Normalize(local.RelativePath),
                remote.Identity,
                remote.Size,
                File.GetLastWriteTimeUtc(absolute).Ticks);
            SaveIndex();
        }
        finally
        {
            indexLock.Release();
        }
    }

    public async Task RemoveAsync(LocalEntry local, CancellationToken cancellationToken)
    {
        cancellationToken.ThrowIfCancellationRequested();
        File.Delete(Absolute(local.RelativePath));
        await RemoveIndexAsync(local.RelativePath, cancellationToken).ConfigureAwait(false);
    }

    public async Task PreserveRemovedEditAsync(
        LocalEntry local,
        CancellationToken cancellationToken)
    {
        var source = Absolute(local.RelativePath);
        var destination = Path.Combine(root, "Local Conflicts", local.RelativePath.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
        destination = AvailableOrdinaryPath(destination);
        File.Move(source, destination);
        await RevertIfPlaceholderAsync(destination).ConfigureAwait(false);
        await RemoveIndexAsync(local.RelativePath, cancellationToken).ConfigureAwait(false);
    }

    public async Task PreserveRenameAsync(LocalEntry local, CancellationToken cancellationToken)
    {
        var path = Absolute(local.RelativePath);
        if (local.IsHydrated)
        {
            await RevertIfPlaceholderAsync(path).ConfigureAwait(false);
        }
        else
        {
            File.Delete(path);
        }
        await RemoveIndexAsync(local.RelativePath, cancellationToken).ConfigureAwait(false);
    }

    private async Task RevertIfPlaceholderAsync(string path)
    {
        IntPtr handle = IntPtr.Zero;
        try
        {
            CloudFilesNative.ThrowIfFailed(
                CloudFilesNative.CfOpenFileWithOplock(
                    path,
                    CloudFilesNative.OpenFileExclusive | CloudFilesNative.OpenFileWriteAccess,
                    out handle),
                "Opening local file");
            CloudFilesNative.ThrowIfFailed(
                CloudFilesNative.CfRevertPlaceholder(handle, 0, IntPtr.Zero),
                "Converting the file to local-only content");
        }
        finally
        {
            if (handle != IntPtr.Zero)
            {
                CloudFilesNative.CfCloseHandle(handle);
            }
        }
        await Task.CompletedTask;
    }

    private void OnFetchData(IntPtr infoPointer, IntPtr parametersPointer)
    {
        var info = Marshal.PtrToStructure<CloudFilesNative.CallbackInfo>(infoPointer);
        var parameters = Marshal.PtrToStructure<CloudFilesNative.FetchDataParameters>(parametersPointer);
        var identity = new byte[checked((int)info.FileIdentityLength)];
        Marshal.Copy(info.FileIdentity, identity, 0, identity.Length);
        var cancellation = new CancellationTokenSource();
        hydrations[info.RequestKey.Internal] = cancellation;
        _ = Task.Run(
            () => HydrateAsync(info, parameters, identity, cancellation.Token),
            CancellationToken.None);
    }

    private void OnCancelFetchData(IntPtr infoPointer, IntPtr parametersPointer)
    {
        _ = parametersPointer;
        var info = Marshal.PtrToStructure<CloudFilesNative.CallbackInfo>(infoPointer);
        if (hydrations.TryRemove(info.RequestKey.Internal, out var cancellation))
        {
            cancellation.Cancel();
            cancellation.Dispose();
        }
    }

    private async Task HydrateAsync(
        CloudFilesNative.CallbackInfo info,
        CloudFilesNative.FetchDataParameters parameters,
        byte[] identityBytes,
        CancellationToken cancellationToken)
    {
        string? temporary = null;
        try
        {
            var identity = JsonSerializer.Deserialize<PlaceholderIdentity>(identityBytes)
                ?? throw new InvalidOperationException("Placeholder identity is empty");
            var result = await backend.InvokeAsync<HydrationResult>(
                "content.hydrate",
                new { identity },
                cancellationToken).ConfigureAwait(false);
            temporary = result.Path;
            await using var stream = new FileStream(
                temporary,
                FileMode.Open,
                FileAccess.Read,
                FileShare.Read,
                4 * 1024 * 1024,
                FileOptions.SequentialScan);
            var buffer = new byte[4 * 1024 * 1024];
            long offset = 0;
            while (offset < stream.Length)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var read = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
                if (read == 0)
                {
                    break;
                }
                Transfer(info, buffer, read, offset, CloudFilesNative.StatusSuccess);
                offset += read;
            }
            if (stream.Length == 0)
            {
                Transfer(info, [], 0, 0, CloudFilesNative.StatusSuccess);
            }
        }
        catch (OperationCanceledException)
        {
            // The platform no longer needs this byte range.
        }
        catch (Exception)
        {
            Transfer(
                info,
                [],
                0,
                Math.Max(0, parameters.RequiredFileOffset),
                CloudFilesNative.StatusUnsuccessful,
                Math.Max(1, parameters.RequiredLength));
        }
        finally
        {
            if (temporary is not null)
            {
                try { File.Delete(temporary); } catch (IOException) { }
            }
            if (hydrations.TryRemove(info.RequestKey.Internal, out var cancellation))
            {
                cancellation.Dispose();
            }
        }
    }

    private static void Transfer(
        CloudFilesNative.CallbackInfo callback,
        byte[] bytes,
        int count,
        long offset,
        int status,
        long? failureLength = null)
    {
        var handle = count == 0 ? default : GCHandle.Alloc(bytes, GCHandleType.Pinned);
        try
        {
            var info = new CloudFilesNative.OperationInfo
            {
                StructSize = (uint)Marshal.SizeOf<CloudFilesNative.OperationInfo>(),
                Type = CloudFilesNative.OperationTransferData,
                ConnectionKey = callback.ConnectionKey,
                TransferKey = callback.TransferKey,
                RequestKey = callback.RequestKey,
            };
            var values = new CloudFilesNative.TransferDataParameters
            {
                ParamSize = (uint)Marshal.SizeOf<CloudFilesNative.TransferDataParameters>(),
                CompletionStatus = status,
                Buffer = count == 0 ? IntPtr.Zero : handle.AddrOfPinnedObject(),
                Offset = offset,
                Length = failureLength ?? count,
            };
            CloudFilesNative.ThrowIfFailed(
                CloudFilesNative.CfExecute(ref info, ref values),
                "Placeholder data transfer");
        }
        finally
        {
            if (handle.IsAllocated)
            {
                handle.Free();
            }
        }
    }

    private async Task RemoveIndexAsync(string relativePath, CancellationToken cancellationToken)
    {
        await indexLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            index.Remove(Normalize(relativePath));
            SaveIndex();
        }
        finally
        {
            indexLock.Release();
        }
    }

    private void LoadIndex()
    {
        try
        {
            var values = JsonSerializer.Deserialize<List<IndexEntry>>(File.ReadAllText(indexPath)) ?? [];
            index = values.ToDictionary(item => Normalize(item.RelativePath), StringComparer.OrdinalIgnoreCase);
        }
        catch (Exception exception) when (exception is IOException or JsonException)
        {
            index = new(StringComparer.OrdinalIgnoreCase);
        }
    }

    private void SaveIndex()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(indexPath)!);
        var temporary = indexPath + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(index.Values, JsonOptions));
        File.Move(temporary, indexPath, true);
    }

    private string Absolute(string relativePath)
    {
        var normalized = Normalize(relativePath);
        var value = Path.GetFullPath(Path.Combine(root, normalized.Replace('/', Path.DirectorySeparatorChar)));
        if (!value.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("A manifest path escaped the sync root");
        }
        return value;
    }

    private string Relative(string absolute) => Normalize(Path.GetRelativePath(root, absolute));
    private static string Normalize(string value) => value.Replace('\\', '/').TrimStart('/');

    private static long ParseTimestamp(string? value)
    {
        return DateTimeOffset.TryParse(value, out var timestamp)
            ? timestamp.UtcDateTime.ToFileTimeUtc()
            : DateTime.UtcNow.ToFileTimeUtc();
    }

    private static string AvailableOrdinaryPath(string target)
    {
        if (!File.Exists(target))
        {
            return target;
        }
        var directory = Path.GetDirectoryName(target)!;
        var stem = Path.GetFileNameWithoutExtension(target);
        var suffix = Path.GetExtension(target);
        for (var index = 2; ; index++)
        {
            var candidate = Path.Combine(directory, $"{stem}-{index}{suffix}");
            if (!File.Exists(candidate))
            {
                return candidate;
            }
        }
    }

    public static void Unregister(string root)
    {
        if (Directory.Exists(root))
        {
            CloudFilesNative.ThrowIfFailed(
                CloudFilesNative.CfUnregisterSyncRoot(root),
                "Sync-root unregistration");
        }
    }

    public static void PrepareForUninstall(string root, string dataDirectory)
    {
        var indexPath = Path.Combine(dataDirectory, "placeholder-index.json");
        List<IndexEntry> entries;
        try
        {
            entries = JsonSerializer.Deserialize<List<IndexEntry>>(File.ReadAllText(indexPath)) ?? [];
        }
        catch (Exception exception) when (exception is IOException or JsonException)
        {
            entries = [];
        }

        foreach (var item in entries)
        {
            var path = Path.GetFullPath(Path.Combine(root, item.RelativePath.Replace('/', Path.DirectorySeparatorChar)));
            if (!File.Exists(path))
            {
                continue;
            }
            if ((File.GetAttributes(path) & FileAttributes.Offline) != 0)
            {
                File.Delete(path);
                continue;
            }
            IntPtr handle = IntPtr.Zero;
            try
            {
                CloudFilesNative.ThrowIfFailed(
                    CloudFilesNative.CfOpenFileWithOplock(
                        path,
                        CloudFilesNative.OpenFileExclusive | CloudFilesNative.OpenFileWriteAccess,
                        out handle),
                    "Opening hydrated placeholder during uninstall");
                CloudFilesNative.ThrowIfFailed(
                    CloudFilesNative.CfRevertPlaceholder(handle, 0, IntPtr.Zero),
                    "Preserving hydrated content during uninstall");
            }
            finally
            {
                if (handle != IntPtr.Zero)
                {
                    CloudFilesNative.CfCloseHandle(handle);
                }
            }
        }
        File.Delete(indexPath);
        Unregister(root);
    }

    public ValueTask DisposeAsync()
    {
        if (connected)
        {
            CloudFilesNative.CfDisconnectSyncRoot(connectionKey);
            connected = false;
        }
        foreach (var cancellation in hydrations.Values)
        {
            cancellation.Cancel();
            cancellation.Dispose();
        }
        indexLock.Dispose();
        return ValueTask.CompletedTask;
    }
}
