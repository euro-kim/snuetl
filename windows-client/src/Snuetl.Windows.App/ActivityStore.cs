using System.Text.Json;

namespace Snuetl.Windows;

public sealed record ActivityEntry(DateTimeOffset Time, string Action, string Path)
{
    public string Badge => string.IsNullOrEmpty(Path)
        ? (Action.Contains("fail",StringComparison.OrdinalIgnoreCase) ? "!" : Action.Contains("complete",StringComparison.OrdinalIgnoreCase) ? "✓" : "↻")
        : Path.Contains("announcement",StringComparison.OrdinalIgnoreCase) ? "📣" : System.IO.Path.GetExtension(Path).ToLowerInvariant() switch { ".pdf" => "PDF", ".md" => "MD", ".ppt" or ".pptx" => "PPT", ".doc" or ".docx" => "DOC", ".xlsx" or ".csv" => "XLS", ".png" or ".jpg" => "IMG", _ => "FILE" };
    public string BadgeColor => Badge switch { "PDF" => "#C33D54", "MD" => "#4262B8", "📣" => "#966515", "✓" => "#198367", "!" => "#C33D54", "PPT" => "#B75A2D", _ => "#6951B1" };
    public string BadgeBackground => Badge switch { "PDF" or "!" => "#FDECEF", "MD" => "#EAF0FF", "📣" => "#FFF4D9", "✓" => "#E5F6EF", "PPT" => "#FFF0E5", _ => "#F0ECFA" };
    public string EventType { get; init; } = "file";
    public string? CourseId { get; init; }
    public string? ContentKind { get; init; }
    public string? CourseName { get; init; }
    public bool IsRoutineGeneratedCopy
    {
        get
        {
            var parts = Path.Replace('\\','/').Split('/');
            var generated = ContentKind is "announcement" or "page" or "syllabus"
                || (parts.Length >= 4 && parts[2] is "articles" or "syllabus" && Path.EndsWith(".md",StringComparison.OrdinalIgnoreCase));
            return generated && !Action.Contains("preserved",StringComparison.OrdinalIgnoreCase);
        }
    }
    public bool Unavailable { get; init; }
    public string StatusLabel => Unavailable ? Action + " · Unavailable" : Action;
    public string DirectoryLabel => System.IO.Path.GetDirectoryName(Path)?.Replace('\\', '/') ?? "";
    public string Course => CourseName ?? System.Text.RegularExpressions.Regex.Replace(Path.Replace('\\', '/').Split('/').ElementAtOrDefault(1) ?? "", @"--\d+$", "");
    public string TimeLabel => Time.LocalDateTime.ToString("MMM d · HH:mm:ss");
    public string Name => string.IsNullOrEmpty(Path) ? "SNUETL" : System.IO.Path.GetFileName(Path);
}

public sealed class ActivityStore : IDisposable
{
    private readonly string file;
    private readonly object gate = new();
    private List<ActivityEntry> entries = [];
    private readonly System.Threading.Timer flushTimer;
    private bool dirty;
    public event EventHandler? Changed;
    public ActivityStore(string directory)
    {
        file = System.IO.Path.Combine(directory, "activity.json");
        flushTimer = new(_ => Flush(), null, Timeout.Infinite, Timeout.Infinite);
        try { entries = JsonSerializer.Deserialize<List<ActivityEntry>>(File.ReadAllText(file))?.Where(e => !e.IsRoutineGeneratedCopy).Take(500).ToList() ?? []; }
        catch (Exception e) when (e is IOException or JsonException) { }
    }
    public IReadOnlyList<ActivityEntry> Snapshot() { lock (gate) return entries.ToArray(); }
    public void Add(string action, string path = "", string? courseId = null, string? contentKind = null)
    {
        var entry = new ActivityEntry(DateTimeOffset.Now,action,path) { CourseId = courseId, ContentKind = contentKind, EventType = string.IsNullOrEmpty(path) ? "status" : "file" };
        if (entry.IsRoutineGeneratedCopy) return;
        lock (gate)
        {
            entries.Insert(0, entry);
            if (entries.Count > 500) entries.RemoveRange(500, entries.Count - 500);
            if (!dirty) flushTimer.Change(1000, Timeout.Infinite);
            dirty = true;
        }
        Changed?.Invoke(this, EventArgs.Empty);
    }
    public void Flush()
    {
        lock (gate)
        {
            if (!dirty) return;
            try
            {
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(file)!);
                File.WriteAllText(file + ".tmp", JsonSerializer.Serialize(entries));
                File.Move(file + ".tmp", file, true); dirty = false;
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
        }
    }
    public void Dispose() { flushTimer.Dispose(); Flush(); }

}

internal sealed class ActivityPlaceholderStore(IPlaceholderStore inner, ActivityStore activity) : IPlaceholderStore
{
    public Task<IReadOnlyList<LocalEntry>> SnapshotAsync(CancellationToken ct) => inner.SnapshotAsync(ct);
    public async Task CreateAsync(ManifestEntry e, string path, CancellationToken ct)
    { await inner.CreateAsync(e, path, ct); activity.Add("Added to cloud folder", path, e.CourseId,e.Kind); }
    public async Task UpdateAsync(LocalEntry local, ManifestEntry remote, CancellationToken ct)
    { await inner.UpdateAsync(local, remote, ct); activity.Add("Updated from SNU eTL", remote.RelativePath, remote.CourseId,remote.Kind); }
    public async Task RemoveAsync(LocalEntry local, CancellationToken ct)
    { await inner.RemoveAsync(local, ct); activity.Add("Removed from SNU eTL", local.RelativePath, local.Identity?.CourseId,local.Identity?.Kind); }
    public async Task PreserveRemovedEditAsync(LocalEntry local, CancellationToken ct)
    { await inner.PreserveRemovedEditAsync(local, ct); activity.Add("Local changes preserved", local.RelativePath); }
    public async Task PreserveRenameAsync(LocalEntry local, CancellationToken ct)
    { await inner.PreserveRenameAsync(local, ct); activity.Add("Renamed local file preserved", local.RelativePath); }
}
