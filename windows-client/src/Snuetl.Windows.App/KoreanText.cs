namespace Snuetl.Windows;

// Translate presentation values without changing persisted event names or API keys.
internal static class KoreanText
{
    internal static string Status(string value) => L.T(value switch
    {
        "Up to date" => "동기화 완료",
        "Setup required" => "계정 연결 필요",
        "Attention needed" => "확인 필요",
        "Not connected" => "연결되지 않음",
        "Connected" => "연결됨",
        "Disconnected" => "연결 해제됨",
        "Checking SNU eTL…" => "eTL 확인 중…",
        "Validating API token…" => "API 키 확인 중…",
        "Token needs attention" => "API 키 확인 필요",
        "Refresh failed" => "동기화 실패",
        "Available after installation" => "설치 후 사용 가능",
        "Enabled" => "알림 사용 가능",
        "DisabledForApplication" => "Windows에서 앱 알림이 꺼져 있습니다",
        "DisabledForUser" => "Windows에서 알림이 꺼져 있습니다",
        "DisabledByGroupPolicy" => "조직 정책에 따라 알림이 꺼져 있습니다",
        "DisabledByManifest" => "알림 등록 확인 필요",
        "Windows notifications unavailable" => "Windows 알림 사용 불가",
        "Windows notifications unavailable — updates remain in the dashboard" => "Windows 알림 사용 불가 · 학습 현황에서 확인하세요",
        "Disabled by Windows — enable SNUETL in Windows Startup apps." => "Windows 시작 앱 설정에서 SNUETL을 켜세요.",
        "Enabled — starts quietly after sign-in" => "Windows 로그인 시 백그라운드에서 실행됩니다",
        "Not registered — use Repair startup" => "등록되지 않음 · 자동 실행 복구를 누르세요",
        "Added to cloud folder" => "동기화 폴더에 추가됨",
        "Downloaded to this device" => "이 기기에 다운로드됨",
        "Updated from SNU eTL" => "eTL의 새 버전으로 갱신됨",
        "Removed from SNU eTL" => "eTL에서 삭제됨",
        "Local changes preserved" => "로컬 수정 내용 보존됨",
        "Renamed local file preserved" => "이름을 변경한 로컬 파일 보존됨",
        _ when value.StartsWith("Connected:", StringComparison.Ordinal) => L.Pair("연결됨", "Connected") + value[9..],
        _ => L.Status(value)
    });

    internal static string Category(string value) => L.T(value.ToLowerInvariant() switch
    {
        "announcements" => "공지사항", "upcoming" => "예정된 과제", "missing" => "미제출",
        "submissions" => "제출 현황", "feedback" => "피드백", "grades" => "성적",
        "calendar" => "일정", "discussions" => "토론", "assignments" => "과제",
        "reminders" => "마감 알림", "files" => "파일", "course" => "강좌", _ => L.Status(value)
    });

    internal static string Field(string value) => L.T(value switch
    {
        "due_at" => "마감", "start_at" => "시작", "workflow_state" => "상태",
        "grade" or "current_grade" => "성적", "current_score" => "점수",
        "submitted_at" => "제출", "graded_at" => "채점", "last_reply_at" => "최근 답글",
        "missing" => "미제출", "late" => "지각 제출", "unread_count" => "읽지 않음", _ => L.Status(value)
    });

    internal static string AcademicValue(string value) => L.T(value switch
    {
        "submitted" => "제출 완료", "graded" => "채점 완료", "unsubmitted" => "미제출",
        "pending_review" => "검토 중", "True" or "true" => "예", _ => L.Status(value)
    });
}
