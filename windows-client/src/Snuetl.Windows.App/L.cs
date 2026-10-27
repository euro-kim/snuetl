using System.Globalization;
using System.Text.Json;


namespace Snuetl.Windows;

internal static class L
{
    internal static string Language { get; set; } = "system";
    internal static bool Korean => Language == "ko" || (Language != "en" && CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "ko");
    private static readonly Dictionary<string, string> English = Load();
    private static Dictionary<string,string> Load()
    {
        using var stream = typeof(L).Assembly.GetManifestResourceStream("Snuetl.Windows.Assets.strings.json");
        return stream is null ? new() : JsonSerializer.Deserialize<Dictionary<string,string>>(stream)!;
    }
    internal static string T(string text)
    {
        if (Korean) return text;
        return English.GetValueOrDefault(text, text);
    }
    internal static string Pair(string ko, string en) => Korean ? ko : en;
    internal static string Error(Exception error) => error is BackendException backend ? backend.Code switch
    {
        "INVALID_TOKEN" => Pair("API 키 전체를 붙여 넣으세요. 공백이나 설명 문구는 제외하세요.", "Paste the complete API key without spaces or surrounding instructions."),
        "TOKEN_REJECTED" or "NOT_CONNECTED" or "AUTH_EXPIRED" => Pair("연결을 확인하지 못했습니다. 만료되거나 폐기된 키인지 확인한 뒤 새 키로 다시 시도하세요.", "Connection could not be verified. Check whether the key expired or was revoked, then try a new key."),
        "NETWORK" => Pair("eTL에 연결할 수 없습니다. 인터넷 연결을 확인한 뒤 다시 시도하세요.", "Cannot reach eTL. Check your internet connection and try again."),
        "CREDENTIAL_STORE" => Pair("Windows 자격 증명 관리자에 키를 저장하지 못했습니다. 다시 시도하세요.", "Could not save the key in Windows Credential Manager. Please retry."),
        _ => Pair("eTL 요청을 완료하지 못했습니다. 잠시 후 다시 시도하세요. 오류 코드: ", "The eTL request failed. Please retry. Error code: ") + backend.Code
    } : error is OperationCanceledException ? Pair("요청 시간이 초과되었거나 취소되었습니다. 다시 시도하세요.", "The request timed out or was cancelled. Please retry.")
      : KnownError(error.Message);
    private static string KnownError(string message)
    {
        if (English.ContainsKey(message)) return T(message);
        foreach (var item in English)
            if (item.Value == message) return T(item.Key);
        return Pair("작업을 완료하지 못했습니다. 인터넷 연결과 Windows 권한을 확인한 뒤 다시 시도하세요.",
            "Could not complete the operation. Check your internet connection and Windows permissions, then retry.");
    }
    internal static string Status(string text)
    {
        if (!Korean) return T(text);
        foreach (var item in English)
            if (item.Value == text) return item.Key;
        if (text.StartsWith("Downloading automatic login", StringComparison.Ordinal)) return "자동 로그인 구성 요소 다운로드 중…";
        if (text.StartsWith("Waiting for browser sign-in", StringComparison.Ordinal)) return "브라우저에서 서울대학교 로그인을 완료하세요. 창이 나타나지 않으면 취소 후 다시 시도하세요.";
        if (text.StartsWith("Connected.", StringComparison.Ordinal)) return "연결을 확인했습니다. 수업 자료를 동기화합니다.";
        if (text.StartsWith("Installing the sign-in browser", StringComparison.Ordinal)) return "로그인 브라우저 설치 중…";
        if (text.StartsWith("GitHub releases for", StringComparison.Ordinal)) return "GitHub 릴리스에 접근하지 못했습니다. 인터넷 연결과 릴리스 접근 설정을 확인하세요.";
        if (text.StartsWith("The release is missing", StringComparison.Ordinal)) return "릴리스에 무결성 확인 정보가 없습니다. 다른 릴리스를 사용하세요.";
        return text;
    }
}
