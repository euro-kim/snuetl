using System.Text.Json;
using Xunit;

namespace Snuetl.Windows.Tests;

public sealed class PlaceholderIdentityTests
{
    [Fact]
    public void HydrationRequestContainsOnlyTheBackendIdentityFields()
    {
        // The Python worker deliberately rejects identities with extra fields.
        var identity = new PlaceholderIdentity(1, "page", "7", "9", "revision");
        using var request = JsonDocument.Parse(JsonSerializer.Serialize(new { identity }));
        var fields = request.RootElement.GetProperty("identity").EnumerateObject()
            .Select(property => property.Name).Order().ToArray();
        Assert.Equal(new[] { "course_id", "kind", "revision", "source_id", "v" }, fields);
    }

    [Fact]
    public void PreviouslyCreatedPlaceholderIdentitiesRemainReadable()
    {
        const string legacy = """
            {"v":1,"kind":"page","course_id":"7","source_id":"9","revision":"revision",
             "SourceKey":"old","ExactKey":"old"}
            """;
        var identity = JsonSerializer.Deserialize<PlaceholderIdentity>(legacy);
        Assert.Equal(new PlaceholderIdentity(1, "page", "7", "9", "revision"), identity);
        Assert.DoesNotContain("SourceKey", JsonSerializer.Serialize(identity));
    }
}
