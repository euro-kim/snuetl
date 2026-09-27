using Xunit;

namespace Snuetl.Windows.Tests;

public sealed class LifecycleTests : IDisposable
{
    private readonly string root = Path.Combine(Path.GetTempPath(), "snuetl-tests-" + Guid.NewGuid());
    public LifecycleTests() => Directory.CreateDirectory(root);
    public void Dispose() => Directory.Delete(root, true);
    [Fact]
    public void CleanupKeepsSyncedContentAndInstallerOwnedBinaries()
    {
        var sync = Path.Combine(root, "courses"); var binaries = Path.Combine(root, "app");
        Directory.CreateDirectory(sync); Directory.CreateDirectory(binaries); Directory.CreateDirectory(Path.Combine(root, "cache"));
        File.WriteAllText(Path.Combine(sync, "notes.txt"), "my edits");
        File.WriteAllText(Path.Combine(binaries, "SNUETL.exe"), "installer owned");
        File.WriteAllText(Path.Combine(root, "cache", "cached.txt"), "temporary");
        File.WriteAllText(Path.Combine(root, "settings.json"), "{}");
        CleanupPaths.RemoveAppData(root, sync, binaries);
        Assert.Equal("my edits", File.ReadAllText(Path.Combine(sync, "notes.txt")));
        Assert.True(File.Exists(Path.Combine(binaries, "SNUETL.exe")));
        Assert.False(Directory.Exists(Path.Combine(root, "cache")));
        Assert.False(File.Exists(Path.Combine(root, "settings.json")));
    }
    [Theory]
    [InlineData("../outside.txt")]
    [InlineData("sub/../../outside.txt")]
    [InlineData("C:\\outside.txt")]
    public void UninstallRejectsIndexPathsOutsideTheSyncRoot(string path) => Assert.Null(CleanupPaths.ManagedPath(root, path));
    [Fact]
    public void SiblingWithSimilarNameIsNotInsideRoot() => Assert.False(CleanupPaths.Within(root + "-other/file", root));
    [Fact]
    public void ActivityPersistsNewest500EventsWithoutLosingUnicode()
    {
        using var store = new ActivityStore(root);
        for (var i = 0; i < 510; i++) store.Add("Downloaded", $"강의/{i}.pdf");
        store.Flush();
        var reloaded = new ActivityStore(root).Snapshot();
        Assert.Equal(500, reloaded.Count);
        Assert.Equal("강의/509.pdf", reloaded[0].Path);
        Assert.Equal("강의/10.pdf", reloaded[^1].Path);
    }
    [Fact]
    public void ActivityAcceptsConcurrentDownloads()
    {
        using var store = new ActivityStore(root);
        Parallel.For(0, 40, i => store.Add("Downloaded", $"{i}.pdf"));
        store.Flush();
        Assert.Equal(40, new ActivityStore(root).Snapshot().Select(e => e.Path).Distinct().Count());
    }
}
