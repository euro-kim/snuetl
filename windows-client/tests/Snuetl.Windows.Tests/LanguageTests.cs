using System.Globalization;
using Snuetl.Windows;
using Xunit;

[CollectionDefinition("Language", DisableParallelization = true)]
public class LanguageCollection { }

[Collection("Language")]
public class LanguageTests
{
    [Theory]
    [InlineData("system", "ko-KR", true)]
    [InlineData("system", "en-US", false)]
    [InlineData("system", "fr-FR", false)]
    [InlineData("en", "ko-KR", false)]
    [InlineData("ko", "en-US", true)]
    public void LanguagePreferenceOverridesWindows(string preference, string culture, bool korean)
    {
        var original = CultureInfo.CurrentUICulture; var language = L.Language;
        try
        {
            CultureInfo.CurrentUICulture = CultureInfo.GetCultureInfo(culture); L.Language = preference;
            Assert.Equal(korean, L.Korean);
            Assert.Equal(korean ? "연결 확인 후 저장" : "Check connection and save", L.T("연결 확인 후 저장"));
            Assert.Equal(korean ? "연결됨: 42" : "Connected: 42", KoreanText.Status("Connected: 42"));
            Assert.Equal(korean ? "저장된 API 키의 연결 확인 중…" : "Checking the saved Canvas token…", L.Status("Checking the saved Canvas token…"));
            Assert.DoesNotContain("secret-example", L.Error(new BackendException("TOKEN_REJECTED", "secret-example")));
        }
        finally { CultureInfo.CurrentUICulture = original; L.Language = language; }
    }
}
