namespace Snuetl.Windows.Tests;

using Xunit;

public sealed class ManifestReconcilerTests
{
    [Fact]
    public async Task LocalCollisionGetsStableRemoteSuffix()
    {
        var store = new FakeStore([
            new LocalEntry("2026/Course/files/notes.pdf", false, null, false, true, false),
        ]);
        var remote = Entry("2026/Course/files/notes.pdf", "file-7", "r1");

        var result = await new ManifestReconciler(store).ReconcileAsync(Manifest(remote));

        Assert.Equal(1, result.Created);
        Assert.Equal("2026/Course/files/notes__snuetl-file7.pdf", store.Created.Single().Path);
        Assert.Empty(store.Removed);
    }

    [Fact]
    public async Task InSyncOldRevisionIsUpdated()
    {
        var old = Entry("course/file.pdf", "4", "old");
        var local = new LocalEntry(old.RelativePath, true, old.Identity, true, false, false);
        var store = new FakeStore([local]);
        var current = Entry(old.RelativePath, "4", "new");

        var result = await new ManifestReconciler(store).ReconcileAsync(Manifest(current));

        Assert.Equal(1, result.Updated);
        Assert.Single(store.Updated);
        Assert.Empty(store.Removed);
    }

    [Fact]
    public async Task EditedOldRevisionGetsRemoteConflictCopy()
    {
        var old = Entry("course/file.pdf", "4", "old");
        var local = new LocalEntry(old.RelativePath, true, old.Identity, false, true, false);
        var store = new FakeStore([local]);
        var current = Entry(old.RelativePath, "4", "new");

        var result = await new ManifestReconciler(store).ReconcileAsync(Manifest(current));

        Assert.Equal(1, result.Conflicts);
        Assert.Contains("file.remote-", store.Created.Single().Path);
        Assert.Empty(store.Removed);
    }

    [Fact]
    public async Task RemoteDeletionPreservesOnlyEditedContent()
    {
        var untouched = Entry("course/a.pdf", "a", "r1");
        var edited = Entry("course/b.pdf", "b", "r1");
        var store = new FakeStore([
            new LocalEntry(untouched.RelativePath, true, untouched.Identity, true, false, false),
            new LocalEntry(edited.RelativePath, true, edited.Identity, false, true, false),
        ]);

        var result = await new ManifestReconciler(store).ReconcileAsync(new Manifest());

        Assert.Equal(1, result.Removed);
        Assert.Equal(1, result.Conflicts);
        Assert.Single(store.Removed);
        Assert.Single(store.PreservedRemoved);
    }

    private static ManifestEntry Entry(string path, string source, string revision) => new()
    {
        RelativePath = path,
        Kind = "file",
        CourseId = "course",
        SourceId = source,
        Revision = revision,
        Size = 12,
    };

    private static Manifest Manifest(params ManifestEntry[] entries) => new()
    {
        SchemaVersion = 1,
        GeneratedAt = DateTimeOffset.UtcNow,
        Entries = entries,
    };

    private sealed class FakeStore(IReadOnlyList<LocalEntry> initial) : IPlaceholderStore
    {
        public List<(ManifestEntry Entry, string Path)> Created { get; } = [];
        public List<(LocalEntry Local, ManifestEntry Remote)> Updated { get; } = [];
        public List<LocalEntry> Removed { get; } = [];
        public List<LocalEntry> PreservedRemoved { get; } = [];

        public Task<IReadOnlyList<LocalEntry>> SnapshotAsync(CancellationToken cancellationToken) =>
            Task.FromResult(initial);

        public Task CreateAsync(ManifestEntry entry, string relativePath, CancellationToken cancellationToken)
        {
            Created.Add((entry, relativePath));
            return Task.CompletedTask;
        }

        public Task UpdateAsync(LocalEntry local, ManifestEntry remote, CancellationToken cancellationToken)
        {
            Updated.Add((local, remote));
            return Task.CompletedTask;
        }

        public Task RemoveAsync(LocalEntry local, CancellationToken cancellationToken)
        {
            Removed.Add(local);
            return Task.CompletedTask;
        }

        public Task PreserveRemovedEditAsync(LocalEntry local, CancellationToken cancellationToken)
        {
            PreservedRemoved.Add(local);
            return Task.CompletedTask;
        }

        public Task PreserveRenameAsync(LocalEntry local, CancellationToken cancellationToken) =>
            Task.CompletedTask;
    }
}
