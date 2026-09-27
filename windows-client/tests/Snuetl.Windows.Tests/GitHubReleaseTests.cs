using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Snuetl.Windows;
using Xunit;

public class GitHubReleaseTests
{
    const string Repo = "euro-kim/snuetl";
    static readonly byte[] Payload = Encoding.UTF8.GetBytes("verified installer fixture");
    static string Hash => Convert.ToHexString(SHA256.HashData(Payload));
    static object Asset(string name, int id = 1, string? hash = null) => new {
        name, size = Payload.Length, digest = "sha256:" + (hash ?? Hash),
        browser_download_url = $"https://github.com/{Repo}/releases/download/v0.9.0/{name}",
        url = $"https://api.github.com/repos/{Repo}/releases/assets/{id}"
    };
    static object Release(string tag, object[] assets, bool draft = false, bool prerelease = false) => new { tag_name = tag, draft, prerelease, assets };
    sealed class Handler(Func<HttpRequestMessage, HttpResponseMessage> reply) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) => Task.FromResult(reply(request));
    }
    static HttpResponseMessage Json(object value) => new(HttpStatusCode.OK) { Content = new StringContent(JsonSerializer.Serialize(value)) };
    [Fact]
    public async Task NewCoreReleaseReusesOlderSignInZip()
    {
        using var client = new HttpClient(new Handler(_ => Json(new[] {
            Release("v0.9.1", [Asset("SNUETLSetup-0.9.1.exe")]),
            Release("v0.9.0", [Asset("SNUETL-SignIn-0.9.0-win-x64.zip")])
        })));
        using var api = new GitHubReleases(Repo,client);
        Assert.Equal("0.9.1",(await api.FindAsync(false))!.Version);
        Assert.Equal("0.9.0",(await api.FindAsync(true))!.Version);
    }
    [Fact]
    public async Task CoreIgnoresDraftAndPreviewAndUsesHighestVersion()
    {
        using var client = new HttpClient(new Handler(_ => Json(new[] {
            Release("v1.0.0", [Asset("SNUETLSetup.exe")],draft:true),
            Release("v0.10.0", [Asset("SNUETLSetup.exe")],prerelease:true),
            Release("v0.8.0", [Asset("SNUETLSetup.exe")]),
            Release("v0.9.1", [Asset("SNUETLSetup.exe")])
        })));
        using var api = new GitHubReleases(Repo,client);
        Assert.Equal("0.9.1",(await api.FindAsync(false))!.Version);
    }
    [Fact]
    public async Task PrivateDownloadsUseAuthenticatedAssetApi()
    {
        var dir = Path.Combine(Path.GetTempPath(),"snuetl-release-test-"+Guid.NewGuid());
        try {
            using var client = new HttpClient(new Handler(request => {
                Assert.Equal("api.github.com",request.RequestUri!.Host);
                Assert.Equal("fixture-token",request.Headers.Authorization!.Parameter);
                Assert.Contains(request.Headers.Accept,a => a.MediaType == "application/octet-stream");
                return new(HttpStatusCode.OK) { Content = new ByteArrayContent(Payload) };
            }));
            using var api = new GitHubReleases(Repo,client,"fixture-token");
            var file = Path.Combine(dir,"setup.exe");
            await api.DownloadAsync(new("0.9.1","setup.exe",$"https://github.com/{Repo}/releases/download/v0.9.1/setup.exe",Payload.Length,Hash,$"https://api.github.com/repos/{Repo}/releases/assets/1"),file);
            Assert.Equal(Payload,await File.ReadAllBytesAsync(file));
            Assert.False(File.Exists(file+".partial"));
        } finally { if(Directory.Exists(dir)) Directory.Delete(dir,true); }
    }
    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public async Task CorruptOrTruncatedDownloadsLeaveNoInstaller(bool corrupt)
    {
        var dir = Path.Combine(Path.GetTempPath(),"snuetl-release-test-"+Guid.NewGuid());
        try {
            using var client = new HttpClient(new Handler(request => {
                Assert.Null(request.Headers.Authorization);
                return new(HttpStatusCode.OK) { Content = new ByteArrayContent(corrupt ? new byte[Payload.Length] : [1]) };
            }));
            using var api = new GitHubReleases(Repo,client,"fixture-token");
            var file = Path.Combine(dir,"setup.exe");
            await Assert.ThrowsAsync<IOException>(() => api.DownloadAsync(new("0.9.1","setup.exe",$"https://github.com/{Repo}/releases/download/v0.9.1/setup.exe",Payload.Length,Hash),file));
            Assert.False(File.Exists(file)); Assert.False(File.Exists(file+".partial"));
        } finally { if(Directory.Exists(dir)) Directory.Delete(dir,true); }
    }
    [Fact]
    public async Task Private404ExplainsRequiredAccess()
    {
        using var client = new HttpClient(new Handler(_ => new(HttpStatusCode.NotFound)));
        using var api = new GitHubReleases(Repo,client);
        Assert.Contains("Contents: read",(await Assert.ThrowsAsync<IOException>(()=>api.FindAsync(false))).Message);
    }
    [Fact]
    public async Task MissingDigestFailsBeforeDownload()
    {
        using var client = new HttpClient(new Handler(_ => Json(new[] { Release("v0.9.1", [Asset("SNUETLSetup.exe",hash:"")]) })));
        using var api = new GitHubReleases(Repo,client);
        Assert.Contains("SHA-256",(await Assert.ThrowsAsync<IOException>(()=>api.FindAsync(false))).Message);
    }
    [Fact]
    public async Task AddOnDiscoveryFollowsOlderReleasePages()
    {
        var calls = 0;
        using var client = new HttpClient(new Handler(request => {
            calls++;
            return request.RequestUri!.Query.EndsWith("&page=1")
                ? Json(Enumerable.Range(0,100).Select(_ => Release("v0.9.1",[Asset("SNUETLSetup.exe")])).ToArray())
                : Json(new[] { Release("v0.9.0",[Asset("SNUETL-SignIn-0.9.0-win-x64.zip")]) });
        }));
        using var api = new GitHubReleases(Repo,client);
        Assert.Equal("0.9.0",(await api.FindAsync(true))!.Version);
        Assert.Equal(2,calls);
    }
    [Fact]
    public async Task PrivateChecksumSidecarSupportsAssetsWithoutGitHubDigest()
    {
        using var client = new HttpClient(new Handler(request => {
            Assert.Equal("fixture-token",request.Headers.Authorization!.Parameter);
            if (request.RequestUri!.AbsolutePath.EndsWith("/2")) {
                Assert.Contains(request.Headers.Accept,a=>a.MediaType=="application/octet-stream");
                return new(HttpStatusCode.OK) { Content = new StringContent(Hash+"  SNUETLSetup.exe\n") };
            }
            return Json(new[] { Release("v0.9.1",[
                new { name="SNUETLSetup.exe", size=Payload.Length, digest=(string?)null,
                    browser_download_url=$"https://github.com/{Repo}/releases/download/v0.9.1/SNUETLSetup.exe",
                    url=$"https://api.github.com/repos/{Repo}/releases/assets/1" },
                Asset("SHA256SUMS.txt",id:2)
            ]) });
        }));
        using var api = new GitHubReleases(Repo,client,"fixture-token");
        Assert.Equal(Hash,(await api.FindAsync(false))!.Sha256);
    }

}
