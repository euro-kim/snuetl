using System.IO.Pipes;
using System.Security.Principal;
using System.Text;

namespace Snuetl.Windows;

internal static class InstanceChannel
{
    private static string Name => "SNUETL-" + WindowsIdentity.GetCurrent().User!.Value;
    internal static async Task<bool> SendAsync(string command)
    {
        using var pipe = new NamedPipeClientStream(".", Name, PipeDirection.InOut, PipeOptions.Asynchronous);
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(5));
        try
        {
            await pipe.ConnectAsync(timeout.Token);
            using var writer = new StreamWriter(pipe, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
            using var reader = new StreamReader(pipe, leaveOpen: true);
            await writer.WriteLineAsync(command.AsMemory(), timeout.Token);
            return await reader.ReadLineAsync(timeout.Token) == "ok";
        }
        catch (Exception e) when (e is IOException or OperationCanceledException or TimeoutException) { return false; }
    }
    internal static async Task ServeAsync(Action<string> receive, CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var pipe = new NamedPipeServerStream(Name, PipeDirection.InOut, 1,
                    PipeTransmissionMode.Byte, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
                await pipe.WaitForConnectionAsync(ct);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
                timeout.CancelAfter(TimeSpan.FromSeconds(5));
                using var reader = new StreamReader(pipe, leaveOpen: true);
                using var writer = new StreamWriter(pipe, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
                var command = await reader.ReadLineAsync(timeout.Token);
                await writer.WriteLineAsync("ok".AsMemory(), timeout.Token);
                if (command is "show" or "shutdown" or "dashboard" or "notifications" or "files") receive(command);
            }
            catch (OperationCanceledException) { }
            catch (IOException) { }
        }
    }
}
