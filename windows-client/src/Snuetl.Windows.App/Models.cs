using System.Text.Json.Serialization;

namespace Snuetl.Windows;

public sealed record AppSettings
{
    public int SchemaVersion { get; init; } = 3;
    public string IconChoice { get; init; } = "Default";
    public int IconStyleVersion { get; init; }
    public NotificationPreferences Notifications { get; init; } = new();
    public bool NotificationSetupCompleted { get; init; }
    public string ReleaseRepository { get; init; } = "euro-kim/snuetl";
    public DateTimeOffset? LastUpdateCheck { get; init; }
    public DateTimeOffset? LastUpdateAttempt { get; init; }
    public ReleaseDownload? AvailableUpdate { get; init; }
    public bool SetupCompleted { get; init; }
    public string? LastRunVersion { get; init; }
    public string SyncRoot { get; init; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "SNUETL");
    public int RefreshMinutes { get; init; } = 15;
    public bool StartWithWindows { get; init; } = true;
    public DateTimeOffset? LastRefresh { get; init; }
}

public sealed record AccountStatus
{
    [JsonPropertyName("configured")]
    public bool Configured { get; init; }

    [JsonPropertyName("ready")]
    public bool Ready { get; init; }

    [JsonPropertyName("valid")]
    public bool? Valid { get; init; }

    [JsonPropertyName("origin")]
    public string? Origin { get; init; }

    [JsonPropertyName("user_id")]
    public string? UserId { get; init; }

    [JsonPropertyName("expires_at")]
    public string? ExpiresAt { get; init; }

    [JsonPropertyName("manual")]
    public bool Manual { get; init; }
}

public sealed record Manifest
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; init; }

    [JsonPropertyName("generated_at")]
    public DateTimeOffset GeneratedAt { get; init; }

    [JsonPropertyName("entries")]
    public IReadOnlyList<ManifestEntry> Entries { get; init; } = [];
}

public sealed record ManifestEntry
{
    [JsonPropertyName("relative_path")]
    public required string RelativePath { get; init; }

    [JsonPropertyName("kind")]
    public required string Kind { get; init; }

    [JsonPropertyName("course_id")]
    public required string CourseId { get; init; }

    [JsonPropertyName("source_id")]
    public required string SourceId { get; init; }

    [JsonPropertyName("revision")]
    public required string Revision { get; init; }

    [JsonPropertyName("size")]
    public long Size { get; init; }

    [JsonPropertyName("updated_at")]
    public string? UpdatedAt { get; init; }

    [JsonPropertyName("content_type")]
    public string? ContentType { get; init; }

    [JsonIgnore]
    public string SourceKey => $"{Kind}\u001f{CourseId}\u001f{SourceId}";

    public PlaceholderIdentity Identity => new(1, Kind, CourseId, SourceId, Revision);
}

public sealed record PlaceholderIdentity(
    [property: JsonPropertyName("v")] int Version,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("course_id")] string CourseId,
    [property: JsonPropertyName("source_id")] string SourceId,
    [property: JsonPropertyName("revision")] string Revision)
{
    [JsonIgnore]
    public string SourceKey => $"{Kind}\u001f{CourseId}\u001f{SourceId}";
    [JsonIgnore]
    public string ExactKey => $"{SourceKey}\u001f{Revision}";
}

public sealed record LocalEntry(
    string RelativePath,
    bool IsManaged,
    PlaceholderIdentity? Identity,
    bool IsInSync,
    bool IsHydrated,
    bool IsPinned)
{
    public string? SourceKey => Identity?.SourceKey;
    public string? ExactKey => Identity?.ExactKey;
}

public sealed record ReconcileSummary(
    int Created,
    int Updated,
    int Removed,
    int Conflicts,
    int Unchanged);

public sealed record ClientStatus(
    string State,
    string Account,
    string Root,
    DateTimeOffset? LastRefresh,
    DateTimeOffset? NextRefresh,
    int ItemCount,
    string? Error);

public sealed record NotificationPreferences
{
    [JsonPropertyName("categories")] public string[] Categories { get; init; } = [];
    [JsonPropertyName("courses")] public Dictionary<string, string[]> Courses { get; init; } = [];
    [JsonPropertyName("reminder_hours")] public double[] ReminderHours { get; init; } = [24, 1];
}
public sealed record AcademicEvent
{
    [JsonPropertyName("id")] public string Id { get; init; } = "";
    [JsonPropertyName("category")] public string Category { get; init; } = "";
    [JsonPropertyName("course_id")] public string CourseId { get; init; } = "";
    [JsonPropertyName("title")] public string Title { get; init; } = "";
    [JsonPropertyName("detail")] public string Detail { get; init; } = "";
    [JsonPropertyName("url")] public string? Url { get; init; }
    [JsonPropertyName("time")] public DateTimeOffset Time { get; init; }
}
public sealed record AcademicCourse
{
    [JsonPropertyName("id")] public string Id { get; init; } = "";
    [JsonPropertyName("name")] public string Name { get; init; } = "";
    [JsonPropertyName("url")] public string? Url { get; init; }
}
public sealed record AcademicSnapshot
{
    [JsonPropertyName("generated_at")] public DateTimeOffset? GeneratedAt { get; init; }
    [JsonPropertyName("courses")] public AcademicCourse[] Courses { get; init; } = [];
    [JsonPropertyName("datasets")] public Dictionary<string, System.Text.Json.JsonElement[]> Datasets { get; init; } = [];
    [JsonPropertyName("events")] public AcademicEvent[] Events { get; init; } = [];
    [JsonPropertyName("new_events")] public AcademicEvent[] NewEvents { get; init; } = [];
    [JsonPropertyName("errors")] public string[] Errors { get; init; } = [];
}
