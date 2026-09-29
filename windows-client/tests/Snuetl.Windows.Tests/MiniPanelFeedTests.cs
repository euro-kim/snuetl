using System.Text.Json;
using Snuetl.Windows;
using Xunit;

public class MiniPanelFeedTests
{
    static readonly DateTimeOffset Now = new(2026,9,28,12,0,0,TimeSpan.Zero);
    static JsonElement Row(object value) => JsonSerializer.SerializeToElement(value);
    static AcademicSnapshot Snapshot(Dictionary<string,JsonElement[]> datasets) => new()
    { Courses = [new() { Id="7",Name="Algorithms",Url="https://myetl.snu.ac.kr/courses/7" }], Datasets = datasets };

    [Fact]
    public void AnnouncementsShowCourseAndMessageWithoutNotificationOptIn()
    {
        var snapshot = Snapshot(new() { ["announcements"] = [Row(new { course_id=7, announcement_id=9, title="Room change", summary="Meet in room 302 tomorrow.", posted_at=Now, url="https://myetl.snu.ac.kr/courses/7/discussion_topics/9" })] });
        var announcement = Assert.Single(MiniPanelFeed.Announcements(snapshot));
        Assert.Equal("Algorithms",announcement.Course);
        Assert.Equal("Meet in room 302 tomorrow.",announcement.Detail);
        Assert.EndsWith("/discussion_topics/9",announcement.Url);
        Assert.True(announcement.CanOpen);
        Assert.Single(MiniPanelFeed.Alerts(snapshot,Now));
    }
    [Fact]
    public void DeadlinesDeduplicatePlannerAndSuppressSubmittedWork()
    {
        var due = Now.AddHours(4);
        var snapshot = Snapshot(new()
        {
            ["submissions"] = [Row(new { course_id=7,assignment_id=1,title="Problem set",due_at=due,workflow_state="unsubmitted" }),Row(new { course_id=7,assignment_id=2,title="Already submitted",due_at=due,workflow_state="submitted" })],
            ["upcoming"] = [Row(new { course_id=7,item_id=1,type="assignment",title="Problem set",due_at=due }),Row(new { course_id=7,item_id=2,type="assignment",title="Already submitted",due_at=due })]
        });
        var deadline = Assert.Single(MiniPanelFeed.Deadlines(snapshot,Now));
        Assert.Equal("Algorithms",deadline.Course);
        Assert.EndsWith("/assignments/1",deadline.Url);
        Assert.True(deadline.Urgent);
    }
    [Fact]
    public void MissingWorkRemainsVisibleAndOldAnnouncementsStayOutOfAlerts()
    {
        var snapshot = Snapshot(new()
        {
            ["missing"] = [Row(new { course_id=7,assignment_id=1,title="Report",due_at=Now.AddDays(-2) })],
            ["announcements"] = [Row(new { course_id=7,announcement_id=3,title="Old news",posted_at=Now.AddDays(-60) })]
        });
        var alert = Assert.Single(MiniPanelFeed.Alerts(snapshot,Now));
        Assert.StartsWith("Overdue",alert.TimeLabel);
        Assert.Single(MiniPanelFeed.Announcements(snapshot));
    }
    [Theory]
    [InlineData("file:///C:/announcement.md")]
    [InlineData("https://example.com/courses/7")]
    [InlineData("javascript:alert(1)")]
    public void AlertsNeverOpenFilesOrUntrustedLinks(string url)
    {
        var item = Assert.Single(MiniPanelFeed.Announcements(Snapshot(new() { ["announcements"] = [Row(new { course_id=7,announcement_id=9,url })] })));
        Assert.False(item.CanOpen);
    }
    [Fact]
    public void GeneratedMarkdownNoiseIsSuppressedButCourseMarkdownFilesSurvive()
    {
        var root = Path.Combine(Path.GetTempPath(),"snuetl-feed-"+Guid.NewGuid());
        try
        {
            using var store = new ActivityStore(root);
            store.Add("Added to cloud folder","2026/Algorithms--7/articles/announcement/Notice--9.md","7","announcement");
            store.Add("Downloaded to this device","2026/Algorithms--7/articles/page/Guide--2.md");
            store.Add("Removed from SNU eTL","2026/Algorithms--7/syllabus/Official syllabus.md");
            store.Add("Downloaded to this device","2026/Algorithms--7/files/README.md","7","file");
            Assert.Single(store.Snapshot());
            Assert.Equal("Algorithms",store.Snapshot()[0].Course);
            store.Add("Local changes preserved","2026/Algorithms--7/articles/page/Guide--2.md");
            Assert.Equal(2,store.Snapshot().Count);
        }
        finally { if(Directory.Exists(root)) Directory.Delete(root,true); }
    }
    [Fact]
    public void OlderSavedMarkdownEventsAreFilteredOnUpgrade()
    {
        var root = Path.Combine(Path.GetTempPath(),"snuetl-feed-"+Guid.NewGuid());
        Directory.CreateDirectory(root);
        try
        {
            File.WriteAllText(Path.Combine(root,"activity.json"),JsonSerializer.Serialize(new[] { new ActivityEntry(Now,"Removed from SNU eTL","2026/Algorithms--7/articles/announcement/Notice.md") }));
            using var store = new ActivityStore(root);
            Assert.Empty(store.Snapshot());
        }
        finally { Directory.Delete(root,true); }
    }
}
