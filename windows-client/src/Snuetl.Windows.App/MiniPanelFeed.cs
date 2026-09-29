using System.Text.Json;

namespace Snuetl.Windows;

public sealed record MiniPanelItem(string Key, string Category, string Course, string Title, string Detail,
    string? Url, DateTimeOffset? When, string TimeLabel, bool Urgent = false)
{
    public bool CanOpen => MiniPanelFeed.IsEtlUrl(Url);
    public string CategoryLabel => Category switch { "announcements" => "Announcement", "deadlines" => "Deadline", "grades" => "Grade update", "discussions" => "Discussion", _ => "Course update" };
    public string Accent => Urgent ? "#B42335" : "#0F0F70";
    public string LinkLabel => CanOpen ? "Open on eTL ↗" : "eTL link unavailable";
}

public static class MiniPanelFeed
{
    public static bool IsEtlUrl(string? url) => Uri.TryCreate(url,UriKind.Absolute,out var uri)
        && uri.Scheme == "https" && uri.IsDefaultPort && uri.UserInfo.Length == 0
        && uri.Host is "myetl.snu.ac.kr" or "etl.snu.ac.kr";
    private static string Value(JsonElement row,string key) => row.ValueKind == JsonValueKind.Object && row.TryGetProperty(key,out var value) && value.ValueKind != JsonValueKind.Null ? value.ToString() : "";
    private static DateTimeOffset? Date(JsonElement row,string key) => DateTimeOffset.TryParse(Value(row,key),out var time) ? time : null;
    private static bool Flag(JsonElement row,string key) => Value(row,key).Equals("true",StringComparison.OrdinalIgnoreCase);
    private static JsonElement[] Rows(AcademicSnapshot snapshot,string key) => snapshot.Datasets.GetValueOrDefault(key,[]);
    private static string Course(AcademicSnapshot snapshot,string id,string name = "") =>
        snapshot.Courses.FirstOrDefault(c => c.Id == id)?.Name is { Length: > 0 } found ? found
        : name.Length > 0 ? name : id.Length > 0 ? $"Course {id}" : "SNU eTL";
    private static string? Url(AcademicSnapshot snapshot,JsonElement row,string type,string id)
    {
        var url = Value(row,"url");
        if (url.Length > 0) return IsEtlUrl(url) ? url : null;
        var courseId = Value(row,"course_id");
        if (courseId.Length == 0 || id.Length == 0 || type.Length == 0) return null;
        var courseUrl = snapshot.Courses.FirstOrDefault(c => c.Id == courseId)?.Url;
        var origin = IsEtlUrl(courseUrl) ? new Uri(courseUrl!).GetLeftPart(UriPartial.Authority) : "https://myetl.snu.ac.kr";
        return $"{origin}/courses/{Uri.EscapeDataString(courseId)}/{type}/{Uri.EscapeDataString(id)}";
    }
    public static IReadOnlyList<MiniPanelItem> Announcements(AcademicSnapshot snapshot)
    {
        return Rows(snapshot,"announcements").Select(row =>
        {
            var course = Value(row,"course_id");
            var id = Value(row,"announcement_id");
            var posted = Date(row,"posted_at");
            var updated = Date(row,"updated_at");
            var when = updated > posted ? updated : posted ?? updated;
            var title = Value(row,"title");
            return new MiniPanelItem($"announcement:{course}:{id}:{Value(row,"url")}","announcements",
                Course(snapshot,course,Value(row,"course_name")),title.Length > 0 ? title : "Announcement",
                Value(row,"summary"),Url(snapshot,row,"discussion_topics",id),when,
                when is { } time ? $"{(updated > posted ? "Updated" : "Posted")} {time.LocalDateTime:MMM d · HH:mm}" : "Announcement");
        }).DistinctBy(item => item.Key).OrderByDescending(item => item.When).Take(100).ToArray();
    }
    private static string AssignmentKey(JsonElement row)
    {
        var id = Value(row,"assignment_id");
        if (id.Length == 0) id = Value(row,"item_id");
        var type = Value(row,"type");
        if (type.Length == 0) type = "assignment";
        return $"{Value(row,"course_id")}:{type}:{(id.Length > 0 ? id : Value(row,"url"))}";
    }
    private static bool Completed(JsonElement row)
    {
        if (Flag(row,"excused") || Value(row,"submitted_at").Length > 0 || Value(row,"workflow_state") is "submitted" or "graded" or "pending_review") return true;
        return row.ValueKind == JsonValueKind.Object && row.TryGetProperty("submission",out var submission) && submission.ValueKind == JsonValueKind.Object
            && (Flag(submission,"submitted") || Flag(submission,"graded") || Flag(submission,"excused"));
    }
    public static IReadOnlyList<MiniPanelItem> Deadlines(AcademicSnapshot snapshot,DateTimeOffset now)
    {
        var completed = Rows(snapshot,"submissions").Where(Completed).Select(AssignmentKey).ToHashSet();
        var seen = new HashSet<string>();
        var result = new List<MiniPanelItem>();
        foreach (var dataset in new[] { "submissions", "missing", "upcoming" })
        foreach (var row in Rows(snapshot,dataset))
        {
            var key = AssignmentKey(row);
            if (completed.Contains(key) || Completed(row) || Date(row,"due_at") is not { } due) continue;
            var missing = dataset == "missing" || Flag(row,"missing");
            // Avoid stale old assignments; keep explicitly missing work visible.
            if (due < now && !missing || !seen.Add(key)) continue;
            var course = Value(row,"course_id");
            var id = Value(row,"assignment_id");
            if (id.Length == 0) id = Value(row,"item_id");
            var label = due < now ? "Overdue" : due.LocalDateTime.Date == now.LocalDateTime.Date ? "Due today" : "Due";
            var title = Value(row,"title");
            var route = Value(row,"type") switch { "" or "assignment" => "assignments", "quiz" => "quizzes", _ => "" };
            result.Add(new(key,"deadlines",Course(snapshot,course,Value(row,"course_name")),
                title.Length > 0 ? title : "Assignment",missing ? "Missing submission" : "Not yet submitted",
                Url(snapshot,row,route,id),due,$"{label} {due.LocalDateTime:MMM d · HH:mm}",due <= now.AddDays(1)));
        }
        return result.OrderBy(item => item.When).Take(100).ToArray();
    }
    public static IReadOnlyList<MiniPanelItem> Alerts(AcademicSnapshot snapshot,DateTimeOffset now)
    {
        var items = Announcements(snapshot).Where(item => item.When is null || item.When >= now.AddDays(-14))
            .Concat(Deadlines(snapshot,now).Where(item => item.When <= now.AddDays(7)))
            .Concat(snapshot.Events.Where(e => e.Category is "grades" or "discussions" && e.Time >= now.AddDays(-14)).Select(e =>
                new MiniPanelItem(e.Id,e.Category,Course(snapshot,e.CourseId),e.Title,e.Detail,e.Url,e.Time,e.Time.LocalDateTime.ToString("MMM d · HH:mm"))));
        return items.OrderByDescending(item => item.Urgent).ThenBy(item => item.Category == "deadlines" ? 1 : 0)
            .ThenBy(item => item.Category == "deadlines" ? item.When : DateTimeOffset.MaxValue).ThenByDescending(item => item.When)
            .Take(100).ToArray();
    }
}
