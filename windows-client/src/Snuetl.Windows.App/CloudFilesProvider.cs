using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;

namespace Snuetl.Windows;

public sealed class CloudFilesProvider : IPlaceholderStore, IAsyncDisposable
{
    private static readonly Guid ProviderId = new("8532A890-8B27-4A75-9849-BFC7D892539D");
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
    private readonly string root;
    private readonly string? registrationId;
    private readonly string indexPath;
    private readonly BackendClient backend;
    private readonly SemaphoreSlim indexLock = new(1, 1);
    private readonly ConcurrentDictionary<long, CancellationTokenSource> hydrations = new();
    private readonly ConcurrentDictionary<long, Task> hydrationTasks = new();
    private readonly CloudFilesNative.Callback fetchCallback;
    private readonly CloudFilesNative.Callback cancelCallback;
    private CloudFilesNative.ConnectionKey connectionKey;
    private bool connected;
    private FileSystemWatcher? pinWatcher;
    private readonly CancellationTokenSource pinLifetime = new();
    private readonly Channel<string> pinChanges = Channel.CreateBounded<string>(
        new BoundedChannelOptions(1) { SingleReader = true, FullMode = BoundedChannelFullMode.DropWrite });
    private readonly SemaphoreSlim hydrationSlots = new(2, 2);
    private Task? pinWorker;
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

    public CloudFilesProvider(string root, string dataDirectory, BackendClient backend, string? registrationId = null)
    {
        this.root = Path.GetFullPath(root);
        this.registrationId = registrationId;
        this.backend = backend;
        indexPath = Path.Combine(dataDirectory, "placeholder-index.json");
        fetchCallback = OnFetchData;
        cancelCallback = OnCancelFetchData;
        LoadIndex();
    }

    public event Action<string>? FileDownloaded;
    public event Action<string, string>? FileActivity;

    public async Task RegisterAndConnectAsync()
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
                InSync = 0x00000100,
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

        await ExplorerSyncRoot.RegisterAsync(root, registrationId);

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
        pinWatcher = new FileSystemWatcher(root)
        {
            IncludeSubdirectories = true,
            NotifyFilter = NotifyFilters.Attributes | NotifyFilters.LastWrite | NotifyFilters.Size,
        };
        pinWatcher.Changed += (_, args) => pinChanges.Writer.TryWrite(root);
        pinWatcher.Error += (_, _) => pinChanges.Writer.TryWrite(root);
        pinWorker = Task.Run(() => ProcessPinChangesAsync(pinLifetime.Token));
        pinWatcher.EnableRaisingEvents = true;
        pinChanges.Writer.TryWrite(root);
    }

    private async Task ProcessPinChangesAsync(CancellationToken cancellationToken)
    {
        try
        {
            var observedPinStates = new Dictionary<string, uint>(StringComparer.OrdinalIgnoreCase);
            var observedWrites = new Dictionary<string, long>(StringComparer.OrdinalIgnoreCase);
            await foreach (var changed in pinChanges.Reader.ReadAllAsync(cancellationToken))
            {
                await Task.Delay(350, cancellationToken).ConfigureAwait(false);
                while (pinChanges.Reader.TryRead(out _)) { }
                IndexEntry[] entries;
                await indexLock.WaitAsync(cancellationToken).ConfigureAwait(false);
                try
                {
                    entries = index.Values.Where(item =>
                    {
                        var path = Absolute(item.RelativePath);
                        return path.Equals(changed, StringComparison.OrdinalIgnoreCase)
                            || path.StartsWith(changed.TrimEnd(Path.DirectorySeparatorChar)
                                + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
                    }).ToArray();
                }
                finally { indexLock.Release(); }
                foreach (var entry in entries)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    try
                    {
                        var path = Absolute(entry.RelativePath);
                        var file = new FileInfo(path);
                        if (!file.Exists) continue;
                        var attributes = (uint)file.Attributes;
                        var write = file.LastWriteTimeUtc.Ticks;
                        if (!CloudFilesNative.IsCloudOnly(attributes) && write != entry.LastWriteUtcTicks
                            && (!observedWrites.TryGetValue(path, out var previousWrite) || previousWrite != write))
                            FileActivity?.Invoke("Local changes detected · kept on this device", entry.RelativePath);
                        observedWrites[path] = write;
                        var pinState = attributes & (CloudFilesNative.FileAttributePinned | CloudFilesNative.FileAttributeUnpinned);
                        if (observedPinStates.TryGetValue(path, out var previous) && previous == pinState) continue;
                        observedPinStates[path] = pinState;
                        var offline = CloudFilesNative.IsCloudOnly(attributes);
                        var hydrate = offline && (attributes & CloudFilesNative.FileAttributePinned) != 0;
                        var dehydrate = !offline && (attributes & CloudFilesNative.FileAttributeUnpinned) != 0;
                        if (!hydrate && !dehydrate) continue;
                        // Never discard locally edited content when the user frees space.
                        if (dehydrate && (file.LastWriteTimeUtc.Ticks != entry.LastWriteUtcTicks
                            || file.Length != entry.Size)) continue;
                        using var handle = CloudFilesNative.CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0, IntPtr.Zero);
                        if (handle.IsInvalid) throw new IOException("Could not open the placeholder metadata");
                        CloudFilesNative.ThrowIfFailed(hydrate
                            ? CloudFilesNative.CfHydratePlaceholder(handle, 0, long.MaxValue, 0, IntPtr.Zero)
                            : CloudFilesNative.CfDehydratePlaceholder(handle, 0, long.MaxValue, 0, IntPtr.Zero),
                            hydrate ? "Pinning content" : "Freeing local space");
                        FileActivity?.Invoke(hydrate ? "Available offline" : "Freed space · available online", entry.RelativePath);
                    }
                    catch (Exception exception) when (exception is not OperationCanceledException)
                    {
                        LogCloudError("Pin-state update", exception);
                    }
                }
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { }
    }

    private void LogCloudError(string operation, Exception exception)
    {
        try
        {
            var detail = exception is BackendException backendError ? backendError.Code
                : exception is InvalidOperationException ? exception.Message
                : $"{exception.GetType().Name} (0x{exception.HResult:X8})";
            var log = Path.Combine(Path.GetDirectoryName(indexPath)!, "cloud-files.log");
            if (File.Exists(log) && new FileInfo(log).Length > 1_000_000)
                File.Move(log, log + ".old", true);
            File.AppendAllText(log, $"{DateTimeOffset.Now:u} {operation}: {detail}{Environment.NewLine}");
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
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
                var hydrated = !CloudFilesNative.IsCloudOnly(attributes);
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
        var requestedPath = Marshal.PtrToStringUni(info.NormalizedPath) ?? root;
        var cancellation = new CancellationTokenSource();
        hydrations[info.RequestKey.Internal] = cancellation;
        var task = Task.Run(
            () => HydrateAsync(info, parameters, identity, requestedPath, cancellation.Token),
            CancellationToken.None);
        hydrationTasks[info.RequestKey.Internal] = task;
        _ = task.ContinueWith(completed => { hydrationTasks.TryRemove(info.RequestKey.Internal, out _); }, TaskScheduler.Default);
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
        string requestedPath,
        CancellationToken cancellationToken)
    {
        string? temporary = null;
        var acquired = false;
        try
        {
            await hydrationSlots.WaitAsync(cancellationToken).ConfigureAwait(false); acquired = true;
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
            FileDownloaded?.Invoke(Path.GetRelativePath(root, requestedPath));
        }
        catch (OperationCanceledException)
        {
            // The platform no longer needs this byte range.
        }
        catch (Exception exception)
        {
            LogCloudError("Hydration failed", exception);
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
            if (acquired) hydrationSlots.Release();
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

    public static void Unregister(string root, string? registrationId = null)
    {
        if (ExplorerSyncRoot.TryUnregister(root, registrationId)) return;
        if (Directory.Exists(root))
        {
            CloudFilesNative.ThrowIfFailed(
                CloudFilesNative.CfUnregisterSyncRoot(root),
                "Sync-root unregistration");
        }
    }

    public static void PrepareForUninstall(string root, string dataDirectory, string? registrationId = null)
    {
        var indexPath = Path.Combine(dataDirectory, "placeholder-index.json");
        // Walk the actual folder, not only the cached index. Renamed placeholders
        // and a damaged/missing index must not leave downloaded files provider-dependent.
        var registered = ExplorerSyncRoot.RegisteredPath(registrationId);
        if (registered is not null && !string.Equals(Path.GetFullPath(root), Path.GetFullPath(registered), StringComparison.OrdinalIgnoreCase))
            throw new IOException("The registered sync folder differs from the cleanup target.");
        if (registered is not null) _ = CleanupPaths.ManagedPath(root, "__boundary_check__");
        foreach (var path in registered is null ? Enumerable.Empty<string>() : EnumerateCloudFiles(root))
        {
            if (CloudFilesNative.IsCloudOnly((uint)File.GetAttributes(path)))
            {
                File.Delete(path);
                continue;
            }
            if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0) continue;
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
        if (!ExplorerSyncRoot.TryUnregister(root, registrationId))
            ExplorerSyncRoot.RemoveNavigationEntry(registrationId);
    }

    private static IEnumerable<string> EnumerateCloudFiles(string root)
    {
        if (!Directory.Exists(root)) yield break;
        var pending = new Stack<string>(); pending.Push(root);
        while (pending.Count > 0)
        {
            foreach (var path in Directory.EnumerateFileSystemEntries(pending.Pop()))
            {
                var attributes = File.GetAttributes(path);
                if ((attributes & FileAttributes.Directory) != 0)
                {
                    if ((attributes & FileAttributes.ReparsePoint) == 0) pending.Push(path);
                    continue;
                }
                if ((attributes & FileAttributes.ReparsePoint) == 0) continue;
                using var handle = CloudFilesNative.CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x00200000, IntPtr.Zero);
                if (handle.IsInvalid || !CloudFilesNative.GetFileInformationByHandleEx(handle, 9, out var info, 8))
                    throw new IOException("Could not inspect cloud file before uninstall: " + path);
                if ((CloudFilesNative.CfGetPlaceholderStateFromAttributeTag(info.Attributes, info.ReparseTag) & 1) != 0)
                    yield return path;
            }
        }
    }

    public async ValueTask DisposeAsync()
    {
        pinWatcher?.Dispose();
        pinChanges.Writer.TryComplete();
        pinLifetime.Cancel();
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
        if (pinWorker is not null) await pinWorker.ConfigureAwait(false);
        await Task.WhenAll(hydrationTasks.Values).ConfigureAwait(false);
        pinLifetime.Dispose();
        indexLock.Dispose();
    }
}
