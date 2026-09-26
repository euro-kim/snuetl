namespace Snuetl.Windows;

public interface IPlaceholderStore
{
    Task<IReadOnlyList<LocalEntry>> SnapshotAsync(CancellationToken cancellationToken);
    Task CreateAsync(ManifestEntry entry, string relativePath, CancellationToken cancellationToken);
    Task UpdateAsync(LocalEntry local, ManifestEntry remote, CancellationToken cancellationToken);
    Task RemoveAsync(LocalEntry local, CancellationToken cancellationToken);
    Task PreserveRemovedEditAsync(LocalEntry local, CancellationToken cancellationToken);
    Task PreserveRenameAsync(LocalEntry local, CancellationToken cancellationToken);
}

public sealed class ManifestReconciler(IPlaceholderStore store)
{
    private static readonly StringComparer PathComparer = StringComparer.OrdinalIgnoreCase;

    public async Task<ReconcileSummary> ReconcileAsync(
        Manifest manifest,
        CancellationToken cancellationToken = default)
    {
        var local = await store.SnapshotAsync(cancellationToken).ConfigureAwait(false);
        var byPath = local.ToDictionary(item => Normalize(item.RelativePath), PathComparer);
        var exact = local.Where(item => item.ExactKey is not null)
            .GroupBy(item => item.ExactKey!, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.ToList(), StringComparer.Ordinal);
        var bySource = local.Where(item => item.SourceKey is not null)
            .GroupBy(item => item.SourceKey!, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.ToList(), StringComparer.Ordinal);
        var remoteSources = manifest.Entries.Select(item => item.SourceKey)
            .ToHashSet(StringComparer.Ordinal);

        var created = 0;
        var updated = 0;
        var removed = 0;
        var conflicts = 0;
        var unchanged = 0;

        foreach (var remote in manifest.Entries)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var desiredPath = Normalize(remote.RelativePath);
            if (exact.TryGetValue(remote.Identity.ExactKey, out var currentRevision))
            {
                var canonical = currentRevision.FirstOrDefault(
                    item => PathComparer.Equals(Normalize(item.RelativePath), desiredPath));
                if (canonical is not null)
                {
                    unchanged++;
                    continue;
                }

                // A hydrated managed file renamed by the user is preserved as a
                // normal local file, then the canonical placeholder is restored.
                var renamed = currentRevision[0];
                await store.PreserveRenameAsync(renamed, cancellationToken).ConfigureAwait(false);
                var target = AvailablePath(desiredPath, byPath, remote.SourceId);
                await store.CreateAsync(remote, target, cancellationToken).ConfigureAwait(false);
                created++;
                conflicts++;
                continue;
            }

            if (bySource.TryGetValue(remote.SourceKey, out var oldRevisions))
            {
                var old = oldRevisions.FirstOrDefault(
                    item => PathComparer.Equals(Normalize(item.RelativePath), desiredPath))
                    ?? oldRevisions[0];
                if (old.IsInSync)
                {
                    await store.UpdateAsync(old, remote, cancellationToken).ConfigureAwait(false);
                    updated++;
                    continue;
                }

                // Preserve the edited original; expose the incoming server
                // revision next to it exactly once.
                var target = ConflictPath(desiredPath, remote.SourceId, byPath);
                await store.CreateAsync(remote, target, cancellationToken).ConfigureAwait(false);
                created++;
                conflicts++;
                continue;
            }

            var available = AvailablePath(desiredPath, byPath, remote.SourceId);
            await store.CreateAsync(remote, available, cancellationToken).ConfigureAwait(false);
            byPath[available] = new LocalEntry(available, true, remote.Identity, true, false, false);
            created++;
        }

        foreach (var item in local.Where(item => item.IsManaged && item.SourceKey is not null))
        {
            if (remoteSources.Contains(item.SourceKey!))
            {
                continue;
            }
            if (item.IsInSync)
            {
                await store.RemoveAsync(item, cancellationToken).ConfigureAwait(false);
                removed++;
            }
            else
            {
                await store.PreserveRemovedEditAsync(item, cancellationToken).ConfigureAwait(false);
                conflicts++;
            }
        }

        return new ReconcileSummary(created, updated, removed, conflicts, unchanged);
    }

    internal static string Normalize(string path) => path.Replace('\\', '/').TrimStart('/');

    internal static string AvailablePath(
        string desired,
        IReadOnlyDictionary<string, LocalEntry> occupied,
        string sourceId)
    {
        if (!occupied.ContainsKey(desired))
        {
            return desired;
        }
        var directory = Path.GetDirectoryName(desired)?.Replace('\\', '/') ?? string.Empty;
        var stem = Path.GetFileNameWithoutExtension(desired);
        var extension = Path.GetExtension(desired);
        var safeId = new string(sourceId.Where(char.IsLetterOrDigit).Take(24).ToArray());
        safeId = string.IsNullOrEmpty(safeId) ? "remote" : safeId;
        var candidate = Combine(directory, $"{stem}__snuetl-{safeId}{extension}");
        var number = 2;
        while (occupied.ContainsKey(candidate))
        {
            candidate = Combine(directory, $"{stem}__snuetl-{safeId}-{number++}{extension}");
        }
        return candidate;
    }

    private static string ConflictPath(
        string desired,
        string sourceId,
        IReadOnlyDictionary<string, LocalEntry> occupied)
    {
        var directory = Path.GetDirectoryName(desired)?.Replace('\\', '/') ?? string.Empty;
        var stem = Path.GetFileNameWithoutExtension(desired);
        var extension = Path.GetExtension(desired);
        var stamp = DateTime.UtcNow.ToString("yyyyMMddTHHmmssZ");
        var candidate = Combine(directory, $"{stem}.remote-{stamp}{extension}");
        return occupied.ContainsKey(candidate)
            ? AvailablePath(candidate, occupied, sourceId)
            : candidate;
    }

    private static string Combine(string directory, string fileName) =>
        string.IsNullOrEmpty(directory) ? fileName : $"{directory}/{fileName}";
}
