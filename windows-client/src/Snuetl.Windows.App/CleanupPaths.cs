namespace Snuetl.Windows;

internal static class CleanupPaths
{
    internal static bool Within(string path, string parent) =>
        Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar).StartsWith(
            Path.GetFullPath(parent).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar,
            StringComparison.OrdinalIgnoreCase);
    internal static bool Overlaps(string a, string b) => Within(a, b) || Within(b, a) ||
        string.Equals(Path.GetFullPath(a).TrimEnd('\\'), Path.GetFullPath(b).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
    internal static string? ManagedPath(string root, string relative)
    {
        if (Path.IsPathRooted(relative)) return null;
        var path = Path.GetFullPath(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar)));
        if (!Within(path, root)) return null;
        for (var parent = Path.GetDirectoryName(path); parent is not null && Within(parent, root); parent = Path.GetDirectoryName(parent))
            if (Directory.Exists(parent) && (File.GetAttributes(parent) & FileAttributes.ReparsePoint) != 0) return null;
        if (Directory.Exists(root) && (File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
            throw new IOException("The sync folder is a link. Restore its original location before uninstalling.");
        return path;
    }
    internal static void PruneEmptyDirectories(string directory, string syncRoot)
    {
        if (!Directory.Exists(directory) || Overlaps(directory, syncRoot)
            || (File.GetAttributes(directory) & FileAttributes.ReparsePoint) != 0) return;
        foreach (var child in Directory.EnumerateDirectories(directory))
        {
            if ((File.GetAttributes(child) & FileAttributes.ReparsePoint) != 0) continue;
            PruneEmptyDirectories(child, syncRoot);
            if (!Directory.EnumerateFileSystemEntries(child).Any()) Directory.Delete(child);
        }
    }
    internal static void RemoveAppData(string directory, string syncRoot, string binaries)
    {
        if (!Directory.Exists(directory)) return;
        foreach (var path in Directory.EnumerateFileSystemEntries(directory))
        {
            if (Overlaps(path, syncRoot) || Overlaps(path, binaries)) continue;
            var attrs = File.GetAttributes(path);
            if ((attrs & FileAttributes.Directory) == 0) File.Delete(path);
            else if ((attrs & FileAttributes.ReparsePoint) != 0) Directory.Delete(path);
            else { RemoveAppData(path, syncRoot, binaries); if (!Directory.EnumerateFileSystemEntries(path).Any()) Directory.Delete(path); }
        }
    }
}
