using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Net.Http;

namespace Snuetl.Windows;

internal static class Branding
{
    internal const string SnuLogoUrl = "https://www.snu.ac.kr/webdata/uploads/kor/image/2022/09/snu_ui_download.png";
    private static string IconDirectory => Path.Combine(new SettingsStore().DataDirectory, "icons");
    internal static string IconPath
    {
        get
        {
            var pointer = Path.Combine(IconDirectory, "current.txt");
            if (File.Exists(pointer))
            {
                var name = File.ReadAllText(pointer).Trim();
                if (name.StartsWith("client-", StringComparison.Ordinal) && name.EndsWith(".ico", StringComparison.Ordinal)
                    && name == Path.GetFileName(name)) return Path.Combine(IconDirectory, name);
            }
            return Path.Combine(IconDirectory, "client.ico");
        }
    }
    internal static string Version => typeof(Branding).Assembly.GetName().Version?.ToString(3) ?? "unknown";
    internal static async Task ApplyAsync(string choice, string? source = null)
    {
        byte[]? bytes = null;
        if (choice == "SNU" && File.Exists(Path.Combine(IconDirectory, "snu-original.png")))
            bytes = await File.ReadAllBytesAsync(Path.Combine(IconDirectory, "snu-original.png"));
        else if (choice == "SNU")
        {
            using var logo = typeof(Branding).Assembly.GetManifestResourceStream("Snuetl.Windows.Assets.snu-logo.png")
                ?? throw new IOException("The bundled SNU logo is unavailable. Repair the installation.");
            using var buffer = new MemoryStream();
            await logo.CopyToAsync(buffer);
            bytes = buffer.ToArray();
        }
        else if (choice == "Custom")
        {
            if (source is null || new FileInfo(source).Length > 10 * 1024 * 1024)
                throw new InvalidOperationException("Choose an image smaller than 10 MB.");
            bytes = await File.ReadAllBytesAsync(source);
        }
        using var input = bytes is null ? null : new MemoryStream(bytes);
        using var original = input is null ? DefaultImage() : Image.FromStream(input);
        if ((long)original.Width * original.Height > 40_000_000)
            throw new InvalidOperationException("Choose an image with fewer than 40 million pixels.");
        var icon = EncodeIcon(original, whiteBackground: choice == "SNU");
        Directory.CreateDirectory(IconDirectory);
        if (bytes is not null) await File.WriteAllBytesAsync(Path.Combine(IconDirectory, choice == "SNU" ? "snu-original.png" : "custom-original.image"), bytes);
        var name = "client-" + Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(icon))[..16] + ".ico";
        var path = Path.Combine(IconDirectory, name);
        await File.WriteAllBytesAsync(path + ".tmp", icon);
        File.Move(path + ".tmp", path, true);
        var pointer = Path.Combine(IconDirectory, "current.txt");
        await File.WriteAllTextAsync(pointer + ".tmp", name);
        File.Move(pointer + ".tmp", pointer, true);
    }
    internal static Image DefaultImage()
    {
        var bitmap = new Bitmap(256, 256);
        using var g = Graphics.FromImage(bitmap);
        g.Clear(Color.FromArgb(15, 15, 112));
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAliasGridFit;
        using var font = new Font("Segoe UI", 156, FontStyle.Bold, GraphicsUnit.Pixel);
        using var format = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };
        g.DrawString("S", font, Brushes.White, new RectangleF(0, -6, 256, 256), format);
        return bitmap;
    }
    internal static byte[] EncodeIcon(Image original, bool whiteBackground = false)
    {
        int[] sizes = [16, 20, 24, 32, 48, 64, 256];
        var artwork = new Rectangle(0, 0, original.Width, original.Height);
        if (whiteBackground)
        {
            using var pixels = new Bitmap(original);
            int left = pixels.Width, top = pixels.Height, right = -1, bottom = -1;
            for (var y = 0; y < pixels.Height; y++)
                for (var x = 0; x < pixels.Width; x++)
                {
                    var c = pixels.GetPixel(x, y);
                    if (c.A < 24 || (c.R > 245 && c.G > 245 && c.B > 245)) continue;
                    left = Math.Min(left, x); top = Math.Min(top, y); right = Math.Max(right, x); bottom = Math.Max(bottom, y);
                }
            if (right >= left) artwork = Rectangle.FromLTRB(left, top, right + 1, bottom + 1);
        }
        var images = sizes.Select(size =>
        {
            using var bitmap = new Bitmap(size, size, PixelFormat.Format32bppArgb);
            using var g = Graphics.FromImage(bitmap);
            g.Clear(whiteBackground ? Color.White : Color.Transparent);
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;
            var bounds = whiteBackground ? size - (size <= 32 ? 2f : size * 0.08f) : size;
            var scale = Math.Min(bounds / artwork.Width, bounds / artwork.Height);
            var w = artwork.Width * scale; var h = artwork.Height * scale;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
            g.DrawImage(original, new RectangleF((size - w) / 2, (size - h) / 2, w, h), artwork, GraphicsUnit.Pixel);
            using var png = new MemoryStream(); bitmap.Save(png, ImageFormat.Png); return png.ToArray();
        }).ToArray();
        using var output = new MemoryStream(); using var writer = new BinaryWriter(output);
        writer.Write((ushort)0); writer.Write((ushort)1); writer.Write((ushort)sizes.Length);
        var offset = 6 + sizes.Length * 16;
        for (var i = 0; i < sizes.Length; i++)
        {
            writer.Write((byte)(sizes[i] == 256 ? 0 : sizes[i])); writer.Write((byte)(sizes[i] == 256 ? 0 : sizes[i]));
            writer.Write((byte)0); writer.Write((byte)0); writer.Write((ushort)1); writer.Write((ushort)32);
            writer.Write(images[i].Length); writer.Write(offset); offset += images[i].Length;
        }
        foreach (var png in images) writer.Write(png);
        return output.ToArray();
    }
    internal static Icon LoadIcon(int size = 32)
    {
        using var stream = File.OpenRead(IconPath);
        using var icon = new Icon(stream, size, size);
        return (Icon)icon.Clone();
    }
}
