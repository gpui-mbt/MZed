param(
    [Parameter(Mandatory = $true)][Int64]$Handle,
    [Parameter(Mandatory = $true)][string]$Output
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$drawingReferences = @(
    [System.Drawing.Bitmap].Assembly.Location
    [System.Drawing.Rectangle].Assembly.Location
) | Select-Object -Unique
Add-Type -ReferencedAssemblies $drawingReferences -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
public static class MzedWindowCapture {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint GetDpiForWindow(IntPtr hWnd);

    public sealed class ColorBounds {
        public int count { get; set; }
        public int left { get; set; }
        public int top { get; set; }
        public int right { get; set; }
        public int bottom { get; set; }
    }

    public static ColorBounds FindColor(Bitmap bitmap, int red, int green, int blue) {
        ColorBounds bounds = new ColorBounds();
        bounds.left = bitmap.Width;
        bounds.top = bitmap.Height;
        bounds.right = -1;
        bounds.bottom = -1;
        Rectangle area = new Rectangle(0, 0, bitmap.Width, bitmap.Height);
        BitmapData data = bitmap.LockBits(area, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        try {
            int stride = data.Stride;
            int rowStride = Math.Abs(stride);
            byte[] pixels = new byte[rowStride * bitmap.Height];
            Marshal.Copy(data.Scan0, pixels, 0, pixels.Length);
            for (int y = 0; y < bitmap.Height; y++) {
                int row = stride >= 0 ? y * stride : (bitmap.Height - 1 - y) * rowStride;
                for (int x = 0; x < bitmap.Width; x++) {
                    int offset = row + x * 4;
                    if (pixels[offset] == blue && pixels[offset + 1] == green && pixels[offset + 2] == red) {
                        bounds.count++;
                        if (x < bounds.left) bounds.left = x;
                        if (y < bounds.top) bounds.top = y;
                        if (x > bounds.right) bounds.right = x;
                        if (y > bounds.bottom) bounds.bottom = y;
                    }
                }
            }
        } finally {
            bitmap.UnlockBits(data);
        }
        if (bounds.count == 0) {
            bounds.left = 0;
            bounds.top = 0;
            bounds.right = 0;
            bounds.bottom = 0;
        }
        return bounds;
    }
}
'@

$null = [MzedWindowCapture]::SetProcessDpiAwarenessContext([IntPtr](-4))
$rect = New-Object MzedWindowCapture+RECT
if (-not [MzedWindowCapture]::GetWindowRect([IntPtr]$Handle, [ref]$rect)) {
    throw "GetWindowRect failed for $Handle"
}
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -le 0 -or $height -le 0) { throw "Window has invalid bounds ${width}x${height}" }
$bitmap = [System.Drawing.Bitmap]::new($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size)
    $bitmap.Save($Output, [System.Drawing.Imaging.ImageFormat]::Png)
    $summary = [ordered]@{
        left = $rect.Left
        top = $rect.Top
        width = $width
        height = $height
        dpi = [MzedWindowCapture]::GetDpiForWindow([IntPtr]$Handle)
        colors = [ordered]@{
            blue = [MzedWindowCapture]::FindColor($bitmap, 40, 96, 160)
            pink = [MzedWindowCapture]::FindColor($bitmap, 200, 96, 160)
        }
    }
    [Console]::Out.WriteLine(($summary | ConvertTo-Json -Compress -Depth 5))
} finally {
    $graphics.Dispose()
    $bitmap.Dispose()
}
