using System.Windows.Markup;
namespace Snuetl.Windows;

public sealed class TranslateExtension(string text) : MarkupExtension
{
    public override object ProvideValue(IServiceProvider serviceProvider) => L.T(text);
}
