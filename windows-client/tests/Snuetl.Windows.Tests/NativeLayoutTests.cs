using System.Runtime.InteropServices;
using Xunit;

namespace Snuetl.Windows.Tests;

public sealed class NativeLayoutTests
{
    [Fact]
    public void FetchCallbackUnionMatchesX64CfApiLayout()
    {
        Assert.Equal(64, Marshal.SizeOf<CloudFilesNative.FetchDataParameters>());
        Assert.Equal(8, Marshal.OffsetOf<CloudFilesNative.FetchDataParameters>("Flags").ToInt32());
        Assert.Equal(16, Marshal.OffsetOf<CloudFilesNative.FetchDataParameters>("RequiredFileOffset").ToInt32());
        Assert.Equal(56, Marshal.OffsetOf<CloudFilesNative.FetchDataParameters>("LastDehydrationReason").ToInt32());
    }

    [Fact]
    public void TransferOperationUnionMatchesX64CfApiLayout()
    {
        Assert.Equal(40, Marshal.SizeOf<CloudFilesNative.TransferDataParameters>());
        Assert.Equal(8, Marshal.OffsetOf<CloudFilesNative.TransferDataParameters>("Flags").ToInt32());
        Assert.Equal(12, Marshal.OffsetOf<CloudFilesNative.TransferDataParameters>("CompletionStatus").ToInt32());
        Assert.Equal(16, Marshal.OffsetOf<CloudFilesNative.TransferDataParameters>("Buffer").ToInt32());
        Assert.Equal(32, Marshal.OffsetOf<CloudFilesNative.TransferDataParameters>("Length").ToInt32());
    }
}
