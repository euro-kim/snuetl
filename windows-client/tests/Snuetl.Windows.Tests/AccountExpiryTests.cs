using Snuetl.Windows;
using Xunit;

public class AccountExpiryTests
{
    [Fact]
    public void AutomaticKeyShowsSavedExpiryInsteadOfAssumingAnotherYear()
    {
        var account = new AccountStatus { ExpiresAt = "2026-11-15T12:00:00Z" };
        Assert.Contains("2026-11-15", account.ExpiryDescription);
        Assert.Contains("폐기된", account.ExpiryDescription);
    }

    [Fact]
    public void ManualKeyDoesNotShowInternalPlaceholderExpiry()
    {
        var account = new AccountStatus { Manual = true, ExpiresAt = "2036-11-15T12:00:00Z" };
        Assert.DoesNotContain("2036", account.ExpiryDescription);
        Assert.Contains("조회할 수 없습니다", account.ExpiryDescription);
    }
}
