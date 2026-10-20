"""Pictures: converting, compressing, resizing, rotating - through Pillow.

What Pillow cannot read itself (HEIC from a phone, JPEG XL...) is decoded by
FFmpeg into a PNG first. SVG is drawn by Qt, which is why SVG work runs on the
GUI thread (see catalog.py).
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from .. import locate, win32
from ..jobs import Job, UserError

Image.MAX_IMAGE_PIXELS = 400_000_000

# Pillow's name for each format, by the extension Dropshift writes.
PIL_FORMAT = {'png': 'PNG', 'jpg': 'JPEG', 'webp': 'WEBP', 'avif': 'AVIF', 'gif': 'GIF',
              'bmp': 'BMP', 'tiff': 'TIFF', 'ico': 'ICO', 'pdf': 'PDF'}
ANIMATED_FORMATS = {'gif', 'webp', 'png', 'avif'}
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
A4_LONG_INCHES = 11.69


def normal_ext(path: Path) -> str:
    ext = path.suffix.lower().lstrip('.')
    return {'jpeg': 'jpg', 'jpe': 'jpg', 'jfif': 'jpg', 'tif': 'tiff', 'heif': 'heic'}.get(ext, ext)


def open_image(src: Path) -> Image.Image:
    if normal_ext(src) == 'svg':
        return render_svg(src)
    try:
        # Read into memory: Pillow keeps an animated picture's file open for
        # as long as the image lives, and an open file can't be moved or
        # deleted on Windows.
        image = Image.open(io.BytesIO(src.read_bytes()))
        image.load()
    except OSError as error:
        if not isinstance(error, Image.UnidentifiedImageError) and not src.is_file():
            raise
        image = _open_with_ffmpeg(src)
    except Exception:  # noqa: BLE001 - try FFmpeg before giving up
        image = _open_with_ffmpeg(src)
    if getattr(image, 'is_animated', False):
        return image
    return ImageOps.exif_transpose(image) or image


def _open_with_ffmpeg(src: Path) -> Image.Image:
    ffmpeg = locate.ffmpeg()
    if not ffmpeg:
        raise UserError(f"Can't read {src.name} as a picture")
    with tempfile.TemporaryDirectory(prefix='dropshift-') as tmp:
        png = Path(tmp) / 'frame.png'
        result = win32.run_hidden([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                                   '-i', src, '-frames:v', '1', png], capture_output=True)
        if result.returncode or not png.exists():
            raise UserError(f"Can't read {src.name} as a picture")
        image = Image.open(io.BytesIO(png.read_bytes()))
        image.load()
        return image


def render_svg(src: Path, longest: int = 2048) -> Image.Image:
    from PySide6.QtCore import QBuffer, QIODevice, QRectF, QSize, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(src))
    if not renderer.isValid():
        raise UserError(f"Can't read {src.name} as an SVG drawing")
    size = renderer.defaultSize()
    if size.isEmpty():
        size = QSize(1024, 1024)
    scale = max(1.0, longest / max(size.width(), size.height()))
    image = QImage(round(size.width() * scale), round(size.height() * scale), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    picture = Image.open(io.BytesIO(bytes(buffer.data())))
    picture.load()
    return picture


def svg_to_pdf(src: Path, out: Path) -> None:
    """An SVG as a PDF page of the same size, still as vectors."""
    from PySide6.QtCore import QMarginsF, QRectF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(src))
    if not renderer.isValid():
        raise UserError(f"Can't read {src.name} as an SVG drawing")
    size = renderer.defaultSize()
    points = QSizeF(max(size.width(), 1) * 0.75, max(size.height(), 1) * 0.75)
    writer = QPdfWriter(str(out))
    writer.setPageLayout(QPageLayout(QPageSize(points, QPageSize.Point), QPageLayout.Portrait,
                                     QMarginsF(0, 0, 0, 0)))
    writer.setResolution(300)
    painter = QPainter(writer)
    renderer.render(painter, QRectF(0, 0, writer.width(), writer.height()))
    painter.end()


# --------------------------------------------------------------------------- saving


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ('RGBA', 'LA', 'PA') or (image.mode == 'P' and 'transparency' in image.info)


def flatten(image: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """RGB, with any transparency laid on white."""
    if _has_alpha(image):
        rgba = image.convert('RGBA')
        base = Image.new('RGB', rgba.size, background)
        base.paste(rgba, mask=rgba.getchannel('A'))
        return base
    if image.mode in ('RGB', 'L'):
        return image
    return image.convert('RGB')


def _for_format(image: Image.Image, target: str) -> Image.Image:
    if target in ('jpg', 'bmp', 'pdf'):
        return flatten(image)
    if target in ('webp', 'avif', 'ico'):
        return image.convert('RGBA' if _has_alpha(image) else 'RGB')
    if target == 'png' and image.mode in ('CMYK', 'YCbCr', 'LAB', 'HSV'):
        return image.convert('RGB')
    if target == 'tiff' and image.mode in ('P', 'PA'):
        return image.convert('RGBA')
    return image


def save(image: Image.Image, out: Path, target: str, quality: int | None = None,
         icc: bytes | None = None) -> None:
    options: dict = {}
    if icc and target in ('png', 'jpg', 'webp', 'tiff', 'avif'):
        options['icc_profile'] = icc
    if target == 'jpg':
        options.update(quality=quality or 92, optimize=True, progressive=True)
    elif target == 'webp':
        options.update(quality=quality or 90, method=5)
    elif target == 'avif':
        options.update(quality=quality or 80)
    elif target == 'png':
        options.update(optimize=True)
    elif target == 'tiff':
        options.update(compression='tiff_lzw')
    elif target == 'ico':
        side = max(image.size)
        square = Image.new('RGBA', (side, side), (0, 0, 0, 0))
        square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        image = square
        options['sizes'] = [(s, s) for s in ICO_SIZES if s <= side] or [(side, side)]
    elif target == 'pdf':
        # Phone photos say 72 dpi, which would make a page four feet across;
        # fit the long side to A4 instead, unless the picture is smaller.
        options['resolution'] = max(72.0, max(image.size) / A4_LONG_INCHES)

    if getattr(image, 'is_animated', False) and target in ANIMATED_FORMATS:
        frames = [_for_format(frame.copy(), target) for frame in ImageSequence.Iterator(image)]
        frames[0].save(out, PIL_FORMAT[target], save_all=True, append_images=frames[1:],
                       duration=image.info.get('duration', 100), loop=image.info.get('loop', 0),
                       **options)
        return
    _for_format(image, target).save(out, PIL_FORMAT[target], **options)


def pdf_page(src: Path) -> bytes:
    """A picture as a one-page PDF, for combining with other pages."""
    image = open_image(src)
    buffer = io.BytesIO()
    save(image, buffer, 'pdf')
    return buffer.getvalue()


# --------------------------------------------------------------------------- operations


def convert(job: Job, src: Path, target: str) -> None:
    out = job.output_for(src, target)
    if normal_ext(src) == 'svg' and target == 'pdf':
        svg_to_pdf(src, out)
    else:
        image = open_image(src)
        save(image, out, target, icc=image.info.get('icc_profile'))
    job.add(out)


def _same_format(src: Path) -> str:
    ext = normal_ext(src)
    return ext if ext in PIL_FORMAT and ext not in ('ico', 'pdf') else 'png'


def _transform(job: Job, src: Path, tag: str, change) -> None:
    image = open_image(src)
    target = _same_format(src)
    out = job.output_for(src, target, tag)
    save(change(image), out, target, quality=95, icc=image.info.get('icc_profile'))
    job.add(out)


def half_size(job: Job, src: Path) -> None:
    def shrink(image):
        size = (max(1, image.width // 2), max(1, image.height // 2))
        return image.resize(size, Image.Resampling.LANCZOS)
    _transform(job, src, 'half size', shrink)


def rotate(job: Job, src: Path, clockwise: bool) -> None:
    turn = Image.Transpose.ROTATE_270 if clockwise else Image.Transpose.ROTATE_90
    _transform(job, src, 'rotated', lambda image: image.transpose(turn))


def flip(job: Job, src: Path) -> None:
    _transform(job, src, 'flipped', lambda image: image.transpose(Image.Transpose.FLIP_LEFT_RIGHT))


def grayscale(job: Job, src: Path) -> None:
    def gray(image):
        if _has_alpha(image):
            return image.convert('LA')
        return ImageOps.grayscale(image)
    _transform(job, src, 'grayscale', gray)


def strip_metadata(job: Job, src: Path) -> None:
    """Drop EXIF (camera, GPS location, dates) but keep the colour profile."""
    image = open_image(src)
    target = _same_format(src)
    out = job.output_for(src, target, 'no metadata')
    icc = image.info.get('icc_profile')
    if target == 'jpg' and getattr(image, 'format', '') == 'JPEG':
        # quality='keep' reuses the original's quantisation, so it doesn't
        # get any blurrier for being saved again.
        image.save(out, 'JPEG', quality='keep', icc_profile=icc)
    else:
        save(image, out, target, quality=95, icc=icc)
    job.add(out)


def compress(job: Job, src: Path) -> None:
    image = open_image(src)
    ext = normal_ext(src)
    icc = image.info.get('icc_profile')
    if ext in ('jpg', 'webp', 'avif'):
        target, quality = ext, {'jpg': 72, 'webp': 70, 'avif': 60}[ext]
        out = job.output_for(src, target, 'compressed')
        save(image, out, target, quality=quality, icc=icc)
    elif ext == 'gif' or getattr(image, 'is_animated', False):
        raise UserError(f"{src.name}: animated pictures can't be compressed here yet")
    else:
        # PNG and friends: down to 256 colours, which is what makes PNGs small;
        # a picture with no transparency that is still big becomes a JPG.
        out = job.output_for(src, 'png', 'compressed')
        rgba = image.convert('RGBA') if _has_alpha(image) else image.convert('RGB')
        method = Image.Quantize.FASTOCTREE if rgba.mode == 'RGBA' else Image.Quantize.MEDIANCUT
        rgba.quantize(colors=256, method=method, dither=Image.Dither.FLOYDSTEINBERG).save(
            out, 'PNG', optimize=True)
    _keep_if_smaller(job, src, out)


def _keep_if_smaller(job: Job, src: Path, out: Path) -> None:
    if out.stat().st_size >= src.stat().st_size:
        out.unlink()
        job.notes.append(f'{src.name} is already as small as it gets')
        return
    job.add(out)
