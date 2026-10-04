param(
  [Parameter(Mandatory = $true)][string]$Source,
  [Parameter(Mandatory = $true)][string]$Destination,
  [switch]$CropCells,
  [switch]$SingleRow,
  [switch]$NormalizeBaseline,
  [switch]$NormalizeHeight
)

$ErrorActionPreference = 'Stop'

Add-Type -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;

public static class HealthMateIdleSpriteOptimizer
{
    private const int Columns = 4;
    private const int Rows = 2;
    private const int FrameWidth = 256;
    private const int FrameHeight = 384;

    public static void Convert(string sourcePath, string destinationPath, bool cropCells, bool singleRow, bool normalizeBaseline, bool normalizeHeight)
    {
        using (var source = new Bitmap(sourcePath))
        using (var resized = new Bitmap(Columns * FrameWidth, Rows * FrameHeight, PixelFormat.Format32bppArgb))
        {
            using (var graphics = Graphics.FromImage(resized))
            {
                graphics.CompositingMode = CompositingMode.SourceCopy;
                graphics.CompositingQuality = CompositingQuality.HighQuality;
                graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
                graphics.SmoothingMode = SmoothingMode.None;
                graphics.Clear(Color.Transparent);

                var sourceCellWidth = source.Width / (float)Columns;
                var sourceCellHeight = source.Height / (float)Rows;
                var cropWidth = cropCells ? sourceCellHeight * FrameWidth / FrameHeight : sourceCellWidth;

                for (var row = 0; row < Rows; row++)
                {
                    for (var column = 0; column < Columns; column++)
                    {
                        var sourceX = column * sourceCellWidth + (sourceCellWidth - cropWidth) / 2f;
                        var sourceRect = new RectangleF(sourceX, row * sourceCellHeight, cropWidth, sourceCellHeight);
                        var destinationRect = new Rectangle(column * FrameWidth, row * FrameHeight, FrameWidth, FrameHeight);
                        graphics.DrawImage(source, destinationRect, sourceRect, GraphicsUnit.Pixel);
                    }
                }
            }

            if (!singleRow)
            {
                SaveIndexedPng(resized, destinationPath);
                return;
            }

            using (var rowSheet = new Bitmap(Columns * Rows * FrameWidth, FrameHeight, PixelFormat.Format32bppArgb))
            {
                var frameBottoms = new int[Columns * Rows];
                var frameTops = new int[Columns * Rows];
                var targetBottom = 0;
                var targetTop = 0;
                for (var frame = 0; frame < frameBottoms.Length; frame++)
                {
                    frameBottoms[frame] = FindFrameBottom(resized, frame);
                    frameTops[frame] = FindFrameTop(resized, frame);
                    targetBottom = Math.Max(targetBottom, frameBottoms[frame]);
                    targetTop = Math.Max(targetTop, frameTops[frame]);
                }

                using (var graphics = Graphics.FromImage(rowSheet))
                {
                    graphics.CompositingMode = CompositingMode.SourceCopy;
                    graphics.InterpolationMode = InterpolationMode.NearestNeighbor;
                    graphics.PixelOffsetMode = PixelOffsetMode.Half;
                    graphics.Clear(Color.Transparent);

                    for (var frame = 0; frame < frameBottoms.Length; frame++)
                    {
                        var sourceColumn = frame % Columns;
                        var sourceRow = frame / Columns;
                        if (normalizeHeight)
                        {
                            var sourceHeight = frameBottoms[frame] - frameTops[frame] + 1;
                            var targetHeight = targetBottom - targetTop + 1;
                            var normalizedSource = new Rectangle(
                                sourceColumn * FrameWidth,
                                sourceRow * FrameHeight + frameTops[frame],
                                FrameWidth,
                                sourceHeight
                            );
                            var normalizedDestination = new Rectangle(
                                frame * FrameWidth,
                                targetTop,
                                FrameWidth,
                                targetHeight
                            );
                            graphics.DrawImage(resized, normalizedDestination, normalizedSource, GraphicsUnit.Pixel);
                            continue;
                        }

                        var verticalOffset = normalizeBaseline ? targetBottom - frameBottoms[frame] : 0;
                        var sourceRect = new Rectangle(sourceColumn * FrameWidth, sourceRow * FrameHeight, FrameWidth, FrameHeight);
                        var destinationRect = new Rectangle(frame * FrameWidth, verticalOffset, FrameWidth, FrameHeight);
                        graphics.DrawImage(resized, destinationRect, sourceRect, GraphicsUnit.Pixel);
                    }
                }

                SaveIndexedPng(rowSheet, destinationPath);
            }
        }
    }

    private static int FindFrameBottom(Bitmap source, int frame)
    {
        var sourceColumn = frame % Columns;
        var sourceRow = frame / Columns;
        for (var y = FrameHeight - 1; y >= 0; y--)
        {
            for (var x = 0; x < FrameWidth; x++)
            {
                if (source.GetPixel(sourceColumn * FrameWidth + x, sourceRow * FrameHeight + y).A >= 112)
                {
                    return y;
                }
            }
        }
        return FrameHeight - 1;
    }

    private static int FindFrameTop(Bitmap source, int frame)
    {
        var sourceColumn = frame % Columns;
        var sourceRow = frame / Columns;
        for (var y = 0; y < FrameHeight; y++)
        {
            for (var x = 0; x < FrameWidth; x++)
            {
                if (source.GetPixel(sourceColumn * FrameWidth + x, sourceRow * FrameHeight + y).A >= 112)
                {
                    return y;
                }
            }
        }
        return 0;
    }

    private static void SaveIndexedPng(Bitmap source, string destinationPath)
    {
        using (var indexed = new Bitmap(source.Width, source.Height, PixelFormat.Format8bppIndexed))
        {
            var palette = indexed.Palette;
            palette.Entries[0] = Color.FromArgb(0, 0, 0, 0);
            for (var red = 0; red < 6; red++)
            {
                for (var green = 0; green < 6; green++)
                {
                    for (var blue = 0; blue < 6; blue++)
                    {
                        var index = 1 + red * 36 + green * 6 + blue;
                        palette.Entries[index] = Color.FromArgb(255, red * 51, green * 51, blue * 51);
                    }
                }
            }
            indexed.Palette = palette;

            var sourceRect = new Rectangle(0, 0, source.Width, source.Height);
            var sourceData = source.LockBits(sourceRect, ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
            var destinationData = indexed.LockBits(sourceRect, ImageLockMode.WriteOnly, PixelFormat.Format8bppIndexed);
            try
            {
                var sourceBytes = new byte[Math.Abs(sourceData.Stride) * source.Height];
                var destinationBytes = new byte[Math.Abs(destinationData.Stride) * source.Height];
                Marshal.Copy(sourceData.Scan0, sourceBytes, 0, sourceBytes.Length);

                for (var y = 0; y < source.Height; y++)
                {
                    var sourceRow = y * sourceData.Stride;
                    var destinationRow = y * destinationData.Stride;
                    for (var x = 0; x < source.Width; x++)
                    {
                        var sourceIndex = sourceRow + x * 4;
                        var alpha = sourceBytes[sourceIndex + 3];
                        if (alpha < 112)
                        {
                            destinationBytes[destinationRow + x] = 0;
                            continue;
                        }

                        var blue = Quantize(sourceBytes[sourceIndex]);
                        var green = Quantize(sourceBytes[sourceIndex + 1]);
                        var red = Quantize(sourceBytes[sourceIndex + 2]);
                        destinationBytes[destinationRow + x] = (byte)(1 + red * 36 + green * 6 + blue);
                    }
                }

                Marshal.Copy(destinationBytes, 0, destinationData.Scan0, destinationBytes.Length);
            }
            finally
            {
                source.UnlockBits(sourceData);
                indexed.UnlockBits(destinationData);
            }

            var directory = Path.GetDirectoryName(destinationPath);
            if (!String.IsNullOrEmpty(directory)) Directory.CreateDirectory(directory);
            indexed.Save(destinationPath, ImageFormat.Png);
        }
    }

    private static int Quantize(byte value)
    {
        return Math.Min(5, (value + 25) / 51);
    }
}
'@ -ReferencedAssemblies System.Drawing

[HealthMateIdleSpriteOptimizer]::Convert(
  (Resolve-Path -LiteralPath $Source).Path,
  [System.IO.Path]::GetFullPath($Destination),
  $CropCells.IsPresent,
  $SingleRow.IsPresent,
  $NormalizeBaseline.IsPresent,
  $NormalizeHeight.IsPresent
)
