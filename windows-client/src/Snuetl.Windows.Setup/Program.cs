using System.Diagnostics;
using System.Threading;
using Snuetl.Windows;

// Runs from the new MSI before files are changed, including upgrades from 0.7.x.
try
{
    var signaled = await InstanceChannel.SendAsync("shutdown");
    if (!signaled && args.Length == 1)
    {
        var installed = Path.GetFullPath(args[0]);
        foreach (var process in Process.GetProcessesByName("SNUETL"))
        {
            using (process)
            {
                try
                {
                    if (process.SessionId != Process.GetCurrentProcess().SessionId) continue;
                    if (string.Equals(process.MainModule?.FileName, installed, StringComparison.OrdinalIgnoreCase)
                        && Version.TryParse(process.MainModule?.FileVersionInfo.FileVersion, out var version)
                        && version < new Version(0, 8, 0))
                    { process.Kill(entireProcessTree: true); await process.WaitForExitAsync(); }
                }
                catch (InvalidOperationException) { }
            }
        }
    }
    using var mutex = new Mutex(false, "Local\\SNUETL-Windows-Client");
    for (var i = 0; i < 200; i++)
    {
        bool acquired;
        try { acquired = mutex.WaitOne(0); } catch (AbandonedMutexException) { acquired = true; }
        if (acquired) { mutex.ReleaseMutex(); return 0; }
        await Task.Delay(200);
    }
    return 1;
}
catch { return 1; }
