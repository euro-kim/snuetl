using System.Diagnostics;
using Snuetl.Windows;
using Xunit;

public class AutomaticLoginTests
{
    private static ProcessStartInfo Command(string command) => new("cmd.exe", "/d /c " + command);

    [Fact]
    public async Task ProcessExitIsReportedWithoutWaitingForLoginTimeout()
    {
        var messages = new List<LoginProgress>();
        await AutomaticLogin.RunProcessAsync(Command("exit 0"),new InlineProgress<LoginProgress>(messages.Add),default,TimeSpan.FromSeconds(10));
        Assert.Contains(messages,p => p.Message.Contains("Opening"));
        await Assert.ThrowsAsync<IOException>(() => AutomaticLogin.RunProcessAsync(Command("exit 1"),new InlineProgress<LoginProgress>(_ => {}),default,TimeSpan.FromSeconds(10)));
    }

    [Fact]
    public async Task CancellationTerminatesWaitingProcess()
    {
        var progress = new InlineProgress<LoginProgress>(_ => {});
        using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(500));
        var watch = Stopwatch.StartNew();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => AutomaticLogin.RunProcessAsync(
            Command("ping -n 30 127.0.0.1 >nul"),progress,cancellation.Token,TimeSpan.FromMinutes(1)));
        Assert.True(watch.Elapsed < TimeSpan.FromSeconds(10),"Cancel left the sign-in process waiting");
    }

    [Fact]
    public async Task StalledBrowserHasBoundedWaitAndActionableError()
    {
        var messages = new List<LoginProgress>();
        var error = await Assert.ThrowsAsync<IOException>(() => AutomaticLogin.RunProcessAsync(
            Command("ping -n 30 127.0.0.1 >nul"),new InlineProgress<LoginProgress>(messages.Add),default,TimeSpan.FromSeconds(2)));
        Assert.Contains("timed out",error.Message);
        Assert.Contains(messages,p => p.Message.Contains("elapsed"));
    }
}
