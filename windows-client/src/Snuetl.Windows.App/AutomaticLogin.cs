using System.ComponentModel;
using System.Diagnostics;

namespace Snuetl.Windows;

public sealed record LoginProgress(string Message, double? Fraction = null);

internal sealed class InlineProgress<T>(Action<T> report) : IProgress<T>
{
    public void Report(T value) => report(value);
}

internal static class AutomaticLogin
{
    internal static ProcessStartInfo CreateStartInfo(string dataDirectory)
    {
        var executable = Path.Combine(dataDirectory, "addons", "signin", "snuetl-signin.exe");
        if (!File.Exists(executable)) throw new IOException("Automatic login is not installed. Download the component and try again.");
        var start = new ProcessStartInfo(executable) { WorkingDirectory = Path.GetDirectoryName(executable)! };
        start.Environment["SNUETL_WINDOWS_DATA_DIR"] = dataDirectory;
        start.Environment["PLAYWRIGHT_BROWSERS_PATH"] = "0";
        start.Environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1";
        return start;
    }

    internal static Task RunAsync(string dataDirectory, IProgress<LoginProgress> progress, CancellationToken ct) =>
        RunProcessAsync(CreateStartInfo(dataDirectory),progress,ct,TimeSpan.FromMinutes(11));

    internal static async Task RunProcessAsync(ProcessStartInfo start, IProgress<LoginProgress> progress, CancellationToken ct, TimeSpan timeout)
    {
        ct.ThrowIfCancellationRequested();
        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        progress.Report(new("Opening the SNU sign-in browser…"));
        using var process = new Process { StartInfo = start };
        try { process.Start(); }
        catch (Win32Exception ex)
        {
            throw new IOException("Windows could not start automatic login. Check Windows Security for a blocked SNUETL sign-in component, or use a manual Canvas token.", ex);
        }
        // Drain both pipes without logging account data or blocking the process.
        var stdout = process.StandardOutput.BaseStream.CopyToAsync(Stream.Null);
        var stderr = process.StandardError.BaseStream.CopyToAsync(Stream.Null);
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(timeout);
        var elapsed = Stopwatch.StartNew();
        var exited = process.WaitForExitAsync(deadline.Token);
        try
        {
            while (!exited.IsCompleted)
            {
                await Task.WhenAny(exited, Task.Delay(1000, deadline.Token)).ConfigureAwait(false);
                deadline.Token.ThrowIfCancellationRequested();
                if (!exited.IsCompleted)
                    progress.Report(new($"Waiting for browser sign-in · {elapsed.Elapsed:m\\:ss} elapsed. Complete SNU login in the browser; if no window appears, cancel and retry."));
            }
            await exited.ConfigureAwait(false);
            await Task.WhenAll(stdout, stderr).WaitAsync(deadline.Token).ConfigureAwait(false);
            if (process.ExitCode != 0)
                throw new IOException("Automatic login closed without completing. Try again, or use a manual Canvas token.");
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            throw new IOException("Automatic login timed out. Try again or use a manual Canvas token.");
        }
        finally
        {
            if (!process.HasExited)
            {
                try { process.Kill(entireProcessTree: true); }
                catch (InvalidOperationException) { /* It exited between the check and cancellation. */ }
                await process.WaitForExitAsync().ConfigureAwait(false);
            }
        }
    }
}
