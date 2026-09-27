using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Snuetl.Windows;
using global::Windows.Storage.Provider;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        var app = new App { SuppressStartupForTests = true }; app.InitializeComponent();
        SynchronizationContext.SetSynchronizationContext(new DispatcherSynchronizationContext());
        var task = RunAsync(args);
        var frame = new DispatcherFrame();
        _ = task.ContinueWith(_ => app.Dispatcher.BeginInvoke(new Action(() => frame.Continue = false)));
        Dispatcher.PushFrame(frame);
        try { task.GetAwaiter().GetResult(); return 0; }
        catch (Exception e) { Console.Error.WriteLine(e); return 1; }
    }
    private static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    private static async Task RunAsync(string[] args)
    {
        using (var manager = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager"))
            foreach (var oldId in manager!.GetSubKeyNames().Where(k => k.StartsWith("SNUETL.Test!", StringComparison.Ordinal)))
                ExplorerSyncRoot.RemoveNavigationEntry(oldId);
        var output = Path.GetFullPath(args[0]); Directory.CreateDirectory(output);
        var root = Path.Combine(Path.GetTempPath(), "snuetl-integration-" + Guid.NewGuid()); Directory.CreateDirectory(root);
        var sync = Path.Combine(root, "courses"); var data = Path.Combine(root, "data"); Directory.CreateDirectory(data);
        var id = "SNUETL.Test!" + System.Security.Principal.WindowsIdentity.GetCurrent().User!.Value + "!" + Guid.NewGuid();
        CloudFilesProvider? provider = null;
        try
        {
            using var original = new System.Drawing.Bitmap(400, 200);
            using var g = System.Drawing.Graphics.FromImage(original); g.Clear(System.Drawing.Color.Blue);
            if (args.Contains("--online-logo"))
            {
                using var http = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(30) };
                http.DefaultRequestHeaders.UserAgent.ParseAdd("SNUETL/0.9.0");
                using var response = await http.GetAsync(Branding.SnuLogoUrl);
                var logoBytes = await response.Content.ReadAsByteArrayAsync();
                Console.WriteLine($"Logo HTTP {response.StatusCode}; type={response.Content.Headers.ContentType}; encoding={string.Join(',',response.Content.Headers.ContentEncoding)}; bytes={logoBytes.Length}; prefix={Convert.ToHexString(logoBytes.Take(16).ToArray())}");
                using var downloaded = new MemoryStream(logoBytes);
                using var snu = System.Drawing.Image.FromStream(downloaded);
                using var icoData = new MemoryStream(Branding.EncodeIcon(snu, whiteBackground: true));
                using var snuIcon = new System.Drawing.Icon(icoData, 32, 32);
                Check(snuIcon.Width == 32, "Official SNU logo download/conversion failed");
                using var preview = snuIcon.ToBitmap();
                Check(preview.GetPixel(0, 0).ToArgb() == System.Drawing.Color.White.ToArgb(), "SNU icon must have an opaque white background");
                preview.Save(Path.Combine(output, "snu-tray-icon.png"));
                foreach (var size in new[] {16,20,24,32,48,64,256})
                {
                    var iconBytes = Branding.EncodeIcon(snu,true);
                    var sizes = new[] {16,20,24,32,48,64,256};
                    var frameOffset = 6 + Array.IndexOf(sizes,size) * 16;
                    var length = BitConverter.ToInt32(iconBytes,frameOffset + 8);
                    var offset = BitConverter.ToInt32(iconBytes,frameOffset + 12);
                    File.WriteAllBytes(Path.Combine(output,$"snu-{size}.png"),iconBytes.AsSpan(offset,length).ToArray());
                }
                Console.WriteLine("PASS: official SNU logo downloaded and converted.");
            }
            var bytes = Branding.EncodeIcon(original);
            using var uploadedIco = new MemoryStream(bytes);
            using var decodedIco = System.Drawing.Image.FromStream(uploadedIco);
            Check(decodedIco.Width > 0, "Uploaded ICO decoding failed");
            using var stream = new MemoryStream(bytes); using var icon = new System.Drawing.Icon(stream, 32, 32);
            Check(icon.Width == 32 && BitConverter.ToUInt16(bytes, 4) == 7, "Multi-size icon conversion failed");
            Check(UninstallCleanup.OwnsCredential(GitHubCredential.Target), "Missing private GitHub credential cleanup");
            Check(UninstallCleanup.OwnsCredential("snuetl-windows"), "Missing primary credential cleanup");
            Check(UninstallCleanup.OwnsCredential("old-account@snuetl-windows"), "Missing old credential cleanup");
            Check(!UninstallCleanup.OwnsCredential("snuetl-desktop"), "Other client credentials must survive");

            var addonSource = Path.Combine(root,"addon-source"); Directory.CreateDirectory(addonSource);
            File.WriteAllText(Path.Combine(addonSource,"snuetl-signin.exe"),"test component, never executed");
            var archive = Path.Combine(root,"addon.zip"); System.IO.Compression.ZipFile.CreateFromDirectory(addonSource,archive);
            var descriptor = new SignInAddon.Descriptor("0.9.0","https://github.com/invalid", Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(archive))),new FileInfo(archive).Length,29);
            await SignInAddon.InstallAsync(archive,data,descriptor);
            var installedAddon = Path.Combine(data,"addons","signin","snuetl-signin.exe");
            Check(File.Exists(installedAddon),"Optional component installation failed");
            var corruptRejected = false;
            try { await SignInAddon.InstallAsync(archive,data,descriptor with { Sha256 = new string('0',64) }); } catch (IOException) { corruptRejected = true; }
            Check(corruptRejected && File.Exists(installedAddon),"Failed optional install damaged the existing component");
            await SignInAddon.RemoveAtAsync(data);
            Check(!Directory.Exists(Path.Combine(data,"addons","signin")),"Optional component removal failed");
            var store = new SettingsStore(data, configureStartup: false);
            store.Save(new AppSettings { SyncRoot = sync, IconChoice = "Custom", StartWithWindows = false });
            var reloaded = store.Load(); Check(reloaded.IconChoice == "Custom" && !reloaded.StartWithWindows, "Settings must survive reload");
            await using (var controller = new ClientController(store))
            {
                for (var i = 0; i < 40; i++) controller.Activity.Add(i % 2 == 0 ? "Downloaded to this device" : "Updated from SNU eTL", $"2026 Fall/Computer Science/Lecture {i + 1:00}.pdf");
                var status = new ClientStatus("Up to date", "Connected", sync, DateTimeOffset.Now, DateTimeOffset.Now.AddMinutes(15), 128, null);
                using var timer = new PeriodicTimer(TimeSpan.FromMinutes(15));
                var timerField = typeof(ClientController).GetField("refreshTimer", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)!;
                timerField.SetValue(controller, timer);
                controller.SavePreferences(false, 7);
                Check(store.Load().RefreshMinutes == 7 && timer.Period == TimeSpan.FromMinutes(7), "Changing interval must persist and reschedule immediately");
                var rejected = false;
                try { controller.SavePreferences(false, 0); } catch (ArgumentOutOfRangeException) { rejected = true; }
                Check(rejected && store.Load().RefreshMinutes == 7, "Invalid interval must leave settings unchanged");
                timerField.SetValue(controller, null);
                Check(ClientController.UpdateDue(new AppSettings(),DateTimeOffset.UtcNow), "First update check must be due");
                Check(!ClientController.UpdateDue(new AppSettings { LastUpdateCheck = DateTimeOffset.UtcNow.AddDays(-6) },DateTimeOffset.UtcNow), "Updates must be weekly");
                Check(!ClientController.UpdateDue(new AppSettings { LastUpdateAttempt = DateTimeOffset.UtcNow.AddHours(-1) },DateTimeOffset.UtcNow), "Failed checks must back off");
                var activity = new MainWindow(controller); activity.Render(status);
                Render(activity, Path.Combine(output, "activity.png"));
                var activityList = (System.Windows.Controls.ListBox)activity.FindName("ActivityList");
                var scroll = MainWindow.FindScrollViewer(activityList)!;
                Check(scroll.ScrollableHeight > 0, "Activity panel has no scrollable history");
                scroll.ScrollToEnd(); activity.UpdateLayout();
                Check(scroll.VerticalOffset > 0, "Activity scrollbar did not move");
                Render(activity, Path.Combine(output, "activity-scrolled.png"));
                var wheel = new System.Windows.Input.MouseWheelEventArgs(System.Windows.Input.Mouse.PrimaryDevice, Environment.TickCount, 120)
                    { RoutedEvent = System.Windows.Input.Mouse.PreviewMouseWheelEvent };
                var previousOffset = scroll.VerticalOffset;
                activityList.RaiseEvent(wheel); activity.UpdateLayout();
                Check(scroll.VerticalOffset < previousOffset, "Mouse wheel did not scroll activity");
                var heldOffset = scroll.VerticalOffset;
                var heldCount = activityList.Items.Count;
                controller.Activity.Add("Updated from SNU eTL", "2026 Fall/Computer Science/New lecture.pdf");
                await Task.Delay(80); activity.UpdateLayout();
                Check(activityList.Items.Count == heldCount + 1 && scroll.VerticalOffset >= heldOffset, "New activity jumped the reader back to the beginning");
                var fileButton = FindByTag<System.Windows.Controls.Button>(activityList, b => b.Tag is ActivityEntry);
                Check(fileButton is not null && fileButton.Focusable, "File actions must be keyboard focusable");
                activity.Hide();
                var settingsDashboard = new DashboardWindow(controller);
                var settings = settingsDashboard.SettingsPage; settings.Render(status);
                Render(settingsDashboard, Path.Combine(output, "settings-general.png"));
                Check(Window.GetWindow(settings) == settingsDashboard, "Settings must be embedded in the dashboard");
                var interval = (System.Windows.Controls.TextBox)settings.FindName("IntervalBox");
                Check(interval.Text == "7", "Settings UI does not show the saved interval");
                interval.BringIntoView(); settings.UpdateLayout();
                Render(settingsDashboard, Path.Combine(output, "settings-interval.png"));
                var tabs = Find<System.Windows.Controls.TabControl>(settings)!;
                tabs.SelectedIndex = 1; Render(settingsDashboard, Path.Combine(output, "settings-notifications.png"));
                tabs.SelectedIndex = 2; Render(settingsDashboard, Path.Combine(output, "settings-components.png"));
                tabs.SelectedIndex = 3; Render(settingsDashboard, Path.Combine(output, "settings-appearance.png"));
                tabs.SelectedIndex = 4; Render(settingsDashboard, Path.Combine(output, "settings-about.png"));
                var academic = new AcademicSnapshot { GeneratedAt = DateTimeOffset.Now, Courses = [new AcademicCourse { Id = "7", Name = "Computer Science", Url = "https://myetl.snu.ac.kr/courses/7" }],
                    Datasets = new() { ["announcements"] = [System.Text.Json.JsonSerializer.SerializeToElement(new { title = "Welcome to the new semester", course_name = "Computer Science", summary = "Lecture materials are now available in your SNUETL folder.", url = "https://myetl.snu.ac.kr/courses/7" })],
                    ["upcoming"] = [System.Text.Json.JsonSerializer.SerializeToElement(new { title = "Assignment 1 · Algorithm analysis", course_name = "Computer Science", due_at = DateTimeOffset.Now.AddDays(1).ToString("O"), url = "https://myetl.snu.ac.kr/courses/7" })] } };
                typeof(ClientController).GetProperty("Academic")!.SetValue(controller,academic);
                var dashboard = new DashboardWindow(controller); Render(dashboard, Path.Combine(output, "dashboard.png")); dashboard.Close();
                settingsDashboard.Close();
                if (args.Contains("--benchmark"))
                {
                    activity.Hide();
                    await Task.Delay(3000);
                    using var process = System.Diagnostics.Process.GetCurrentProcess();
                    var cpu = process.TotalProcessorTime.TotalMilliseconds;
                    var start = System.Diagnostics.Stopwatch.StartNew();
                    await Task.Delay(45000);
                    process.Refresh();
                    var report = new { uiWorkingSetBytes = process.WorkingSet64, uiPrivateBytes = process.PrivateMemorySize64,
                        idleCpuPercentOneCore = (process.TotalProcessorTime.TotalMilliseconds - cpu) / start.Elapsed.TotalMilliseconds * 100,
                        seconds = start.Elapsed.TotalSeconds, os = Environment.OSVersion.ToString(), logicalProcessors = Environment.ProcessorCount,
                        scenario = "WPF integration host, hidden tray/dashboard, 40 activity rows, offline fixture backend" };
                    File.WriteAllText(Path.Combine(output,"ui-performance.json"),System.Text.Json.JsonSerializer.Serialize(report));
                }
            }
            await using (var backend = new BackendClient(data))
            {
                provider = new CloudFilesProvider(sync, data, backend, id);
                await provider.RegisterAndConnectAsync();
                await ExplorerSyncRoot.RegisterAsync(sync, id); // Applying an icon while connected must keep hydration working.
                using var registration = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager\" + id);
                Check(registration is not null, "Explorer registration missing");
                var clsid = registration!.GetValue("NamespaceCLSID") as string;
                var entry = new ManifestEntry { RelativePath = "downloaded.txt", Kind = "file", CourseId = "test", SourceId = "1", Revision = "1", Size = 26 };
                await provider.CreateAsync(entry, entry.RelativePath, CancellationToken.None);
                var text = await Task.Run(() => File.ReadAllText(Path.Combine(sync, "downloaded.txt")));
                Check(text == "Preserve my course notes.\n", "Hydration content mismatch");
                var online = entry with { RelativePath = "online-only.txt", SourceId = "2" };
                await provider.CreateAsync(online, online.RelativePath, CancellationToken.None);
                await File.WriteAllTextAsync(Path.Combine(sync, "my-local-notes.txt"), "My local edits");
                Directory.CreateDirectory(Path.Combine(sync, "empty-course-folder"));
                await provider.DisposeAsync(); provider = null;
                File.Move(Path.Combine(sync, "downloaded.txt"), Path.Combine(sync, "renamed-notes.txt"));
                File.Delete(Path.Combine(data, "placeholder-index.json"));
                CloudFilesProvider.PrepareForUninstall(sync, data, id);
                Check(File.ReadAllText(Path.Combine(sync, "renamed-notes.txt")) == text, "Uninstall lost downloaded content");
                Check((File.GetAttributes(Path.Combine(sync, "renamed-notes.txt")) & FileAttributes.ReparsePoint) == 0, "Downloaded file still depends on cloud provider");
                Check(!File.Exists(Path.Combine(sync, "online-only.txt")), "Orphaned online placeholder remains");
                Check(File.ReadAllText(Path.Combine(sync, "my-local-notes.txt")) == "My local edits", "Uninstall touched local notes");
                Check(Directory.Exists(Path.Combine(sync, "empty-course-folder")), "Uninstall removed local folders");
                using var leftover = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager\" + id);
                Check(leftover is null, "Explorer registration remains");
                using var shell = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(@"Software\Classes\CLSID\" + clsid);
                Check(shell is null, "Explorer navigation entry remains");
            }
            Console.WriteLine("PASS: icon sizes, settings persistence, UI rendering, isolated cloud hydration, uninstall file preservation and Explorer removal.");
        }
        finally
        {
            if (provider is not null) await provider.DisposeAsync();
            ExplorerSyncRoot.RemoveNavigationEntry(id);
            if (Directory.Exists(root)) Directory.Delete(root, true);
        }
    }
    private static T? FindByTag<T>(DependencyObject parent, Func<T,bool> predicate) where T : DependencyObject
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
        { var child = VisualTreeHelper.GetChild(parent,i); if (child is T found && predicate(found)) return found; var nested = FindByTag(child,predicate); if (nested is not null) return nested; }
        return null;
    }
    private static T? Find<T>(DependencyObject parent) where T : DependencyObject
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
        { var child = VisualTreeHelper.GetChild(parent, i); if (child is T found) return found; var nested = Find<T>(child); if (nested is not null) return nested; }
        return null;
    }
    private static void Render(Window window, string file)
    {
        window.ShowActivated = false; window.WindowStartupLocation = WindowStartupLocation.Manual; window.Left = -10000; window.Top = -10000;
        window.Show(); window.UpdateLayout();
        var content = (FrameworkElement)window.Content;
        var image = new RenderTargetBitmap((int)content.ActualWidth, (int)content.ActualHeight, 96, 96, PixelFormats.Pbgra32);
        image.Render(content); var encoder = new PngBitmapEncoder(); encoder.Frames.Add(BitmapFrame.Create(image));
        using var stream = File.Create(file); encoder.Save(stream);
    }
}
