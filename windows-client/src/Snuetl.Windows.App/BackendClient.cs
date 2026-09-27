using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Text.Json;

namespace Snuetl.Windows;

public sealed class BackendException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}

public sealed class BackendClient : IAsyncDisposable
{
    private readonly Process process;
    private readonly StreamWriter input;
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> pending = new();
    private readonly SemaphoreSlim writeLock = new(1, 1);
    private readonly CancellationTokenSource lifetime = new();
    private long nextId;

    public BackendClient(string dataDirectory)
    {
        var overrideCommand = Environment.GetEnvironmentVariable("SNUETL_BACKEND_COMMAND");
        ProcessStartInfo start;
        if (!string.IsNullOrWhiteSpace(overrideCommand))
        {
            start = new ProcessStartInfo("cmd.exe", $"/d /s /c \"{overrideCommand}\"");
        }
        else
        {
            var executable = Path.Combine(
                AppContext.BaseDirectory,
                "backend",
                "snuetl-windows-backend.exe");
            start = new ProcessStartInfo(executable);
        }

        start.UseShellExecute = false;
        start.CreateNoWindow = true;
        start.RedirectStandardInput = true;
        start.RedirectStandardOutput = true;
        start.RedirectStandardError = true;
        start.StandardInputEncoding = new System.Text.UTF8Encoding(encoderShouldEmitUTF8Identifier: false);
        start.StandardOutputEncoding = System.Text.Encoding.UTF8;
        start.Environment["SNUETL_WINDOWS_DATA_DIR"] = dataDirectory;
        start.Environment["PYTHONIOENCODING"] = "utf-8";

        process = Process.Start(start)
            ?? throw new InvalidOperationException("Could not start the SNUETL backend");
        input = process.StandardInput;
        _ = ReadResponsesAsync(lifetime.Token);
        _ = DrainErrorsAsync(lifetime.Token);
    }

    public async Task<T> InvokeAsync<T>(
        string method,
        object? parameters = null,
        CancellationToken cancellationToken = default)
    {
        var id = Interlocked.Increment(ref nextId);
        var completion = new TaskCompletionSource<JsonElement>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        if (!pending.TryAdd(id, completion))
        {
            throw new InvalidOperationException("Duplicate backend request ID");
        }

        var request = JsonSerializer.Serialize(new { id, method, @params = parameters ?? new { } });
        await writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            await input.WriteLineAsync(request.AsMemory(), cancellationToken).ConfigureAwait(false);
            await input.FlushAsync(cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            writeLock.Release();
        }

        using var registration = cancellationToken.Register(
            () => completion.TrySetCanceled(cancellationToken));
        var element = await completion.Task.ConfigureAwait(false);
        return element.Deserialize<T>()
            ?? throw new BackendException("INVALID_RESPONSE", "The backend returned no data");
    }

    private async Task ReadResponsesAsync(CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                var line = await process.StandardOutput.ReadLineAsync(cancellationToken)
                    .ConfigureAwait(false);
                if (line is null)
                {
                    break;
                }

                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;
                if (!root.TryGetProperty("id", out var idElement) || !idElement.TryGetInt64(out var id))
                {
                    continue;
                }

                if (!pending.TryRemove(id, out var completion))
                {
                    continue;
                }

                if (root.GetProperty("ok").GetBoolean())
                {
                    completion.TrySetResult(root.GetProperty("result").Clone());
                }
                else
                {
                    var error = root.GetProperty("error");
                    completion.TrySetException(new BackendException(
                        error.GetProperty("code").GetString() ?? "BACKEND",
                        error.GetProperty("message").GetString() ?? "Backend operation failed"));
                }
            }
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            FailPending(exception);
        }
        finally
        {
            FailPending(new BackendException("BACKEND_EXITED", "The SNUETL backend stopped"));
        }
    }

    private async Task DrainErrorsAsync(CancellationToken cancellationToken)
    {
        var logPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "SNUETL",
            "backend.log");
        Directory.CreateDirectory(Path.GetDirectoryName(logPath)!);
        while (!cancellationToken.IsCancellationRequested)
        {
            var line = await process.StandardError.ReadLineAsync(cancellationToken).ConfigureAwait(false);
            if (line is null)
            {
                break;
            }
            // The worker is required never to print secrets. Bound the local log.
            if (new FileInfo(logPath).Exists && new FileInfo(logPath).Length > 1_000_000)
            {
                File.Move(logPath, logPath + ".old", true);
            }
            await File.AppendAllTextAsync(logPath, $"{DateTimeOffset.Now:u} {line}{Environment.NewLine}", cancellationToken)
                .ConfigureAwait(false);
        }
    }

    private void FailPending(Exception exception)
    {
        foreach (var item in pending.ToArray())
        {
            if (pending.TryRemove(item.Key, out var completion))
            {
                completion.TrySetException(exception);
            }
        }
    }

    public async ValueTask DisposeAsync()
    {
        try
        {
            if (!process.HasExited)
            {
                await InvokeAsync<JsonElement>("shutdown").WaitAsync(TimeSpan.FromSeconds(3));
            }
        }
        catch (Exception)
        {
            // Shutdown remains best-effort.
        }
        lifetime.Cancel();
        if (!process.HasExited)
        {
            process.Kill(entireProcessTree: true);
        }
        process.Dispose();
        lifetime.Dispose();
        writeLock.Dispose();
    }
}
