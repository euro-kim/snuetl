using Xunit;

namespace Snuetl.Windows.Tests;

public sealed class CloudFileStateTests
{
    [Theory]
    [InlineData(0x00001020u, true)] // Legacy Offline + Archive
    [InlineData(0x00500020u, true)] // RecallOnDataAccess + Unpinned + Archive, no Offline bit
    [InlineData(0x00080020u, false)] // Pinned local content
    [InlineData(0x00000020u, false)] // Local content
    public void RecognizesCloudOnlyContentEvenWithoutTheOfflineAttribute(uint attributes, bool cloudOnly)
    {
        Assert.Equal(cloudOnly, CloudFilesNative.IsCloudOnly(attributes));
    }
}
