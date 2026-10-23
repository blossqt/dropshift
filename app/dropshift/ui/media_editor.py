"""The editor for any media file: video, audio or an animated GIF.

Trim (timeline handles, or I and O), change the speed, volume or fades, and
for pictures crop, rotate, mirror or drop the sound; then export. The preview
is Qt's own player; the export is FFmpeg (media.edit_args), so what is shown
here previews the settings rather than the final encode. The crop box lives
on the picture itself, as a child of the video item, so it turns and mirrors
with it. A sound-only file shows its cover, title and waveform instead.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QPointF, QRectF, QSizeF, Qt, QUrl, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
                           QShortcut, QTransform)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGraphicsObject, QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView, QHBoxLayout, QLabel, QPushButton, QStyleOptionGraphicsItem,
                               QToolButton, QVBoxLayout, QWidget)

from ..engines import media
from ..engines.media import MediaEdit
from . import icons, theme

CROPS = [('Off', None), ('Free', 0.0), ('16:9', 16 / 9), ('9:16', 9 / 16), ('1:1', 1.0), ('4:3', 4 / 3),
         ('3:4', 3 / 4)]
SPEEDS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 4.0]
SIZES = [('Original size', 0), ('1080p', 1080), ('720p', 720), ('480p', 480)]
QUALITIES = [('High quality', 'high'), ('Medium', 'medium'), ('Small file', 'small')]


def clock(ms: float) -> str:
    seconds = max(0.0, ms / 1000)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{int(h)}:{int(m):02d}:{s:05.2f}' if h else f'{int(m)}:{s:05.2f}'


# --------------------------------------------------------------------------- crop box


class CropOverlay(QGraphicsObject):
    """A crop box over the picture: drag inside to move it, its corners and
    edges to resize it, or anywhere outside it to draw a new one."""
    changed = Signal()
    HANDLE = 10      # screen pixels
    SMALLEST = 16    # picture pixels

    def __init__(self, parent):
        super().__init__(parent)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self._bounds = QRectF()
        self.rect = QRectF()
        self.ratio = 0.0            # width / height as shown; 0 for free
        self.turned = False         # the picture is shown a quarter turn round
        self._mode = ''
        self._anchor = QPointF()
        self._press = QPointF()
        self._start = QRectF()
        self._scale = 1.0

    def boundingRect(self) -> QRectF:
        return self._bounds

    def set_bounds(self, bounds: QRectF) -> None:
        self.prepareGeometryChange()
        self._bounds = QRectF(bounds)
        self.fill()

    def _source_ratio(self) -> float:
        if not self.ratio:
            return 0.0
        return 1 / self.ratio if self.turned else self.ratio

    def fill(self) -> None:
        """The largest box of the chosen shape, in the middle."""
        b, r = self._bounds, self._source_ratio()
        if r and b.height():
            w, h = (b.height() * r, b.height()) if b.width() / b.height() > r else (b.width(), b.width() / r)
            self.rect = QRectF(b.center().x() - w / 2, b.center().y() - h / 2, w, h)
        else:
            self.rect = QRectF(b)
        self.update()
        self.changed.emit()

    def set_ratio(self, ratio: float, turned: bool) -> None:
        self.ratio, self.turned = ratio, turned
        self.fill()

    def fraction(self) -> tuple[float, float, float, float] | None:
        b, r = self._bounds, self.rect
        if not b.width() or not b.height():
            return None
        return ((r.x() - b.x()) / b.width(), (r.y() - b.y()) / b.height(),
                r.width() / b.width(), r.height() / b.height())

    def is_whole(self) -> bool:
        f = self.fraction()
        return not f or (f[2] > 0.999 and f[3] > 0.999)

    # ------------------------------------------------------------------ drawing

    def paint(self, painter: QPainter, option, widget=None):
        self._scale = QStyleOptionGraphicsItem.levelOfDetailFromTransform(painter.worldTransform()) or 1.0
        outside = QPainterPath()
        outside.addRect(self._bounds)
        inside = QPainterPath()
        inside.addRect(self.rect)
        painter.fillPath(outside.subtracted(inside), QColor(0, 0, 0, 150))
        faint = QPen(QColor(255, 255, 255, 80), 1)
        faint.setCosmetic(True)
        painter.setPen(faint)
        r = self.rect
        for n in (1, 2):
            painter.drawLine(QPointF(r.left() + r.width() * n / 3, r.top()), QPointF(r.left() + r.width() * n / 3, r.bottom()))
            painter.drawLine(QPointF(r.left(), r.top() + r.height() * n / 3), QPointF(r.right(), r.top() + r.height() * n / 3))
        edge = QPen(QColor('#ffffff'), 2)
        edge.setCosmetic(True)
        painter.setPen(edge)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(r)
        size = self.HANDLE / self._scale
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#ffffff'))
        for corner in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
            painter.drawRect(QRectF(corner.x() - size / 2, corner.y() - size / 2, size, size))

    # ------------------------------------------------------------------ dragging

    def _hit(self, pos: QPointF) -> str:
        t = self.HANDLE / self._scale
        r = self.rect
        near_l, near_r = abs(pos.x() - r.left()) <= t, abs(pos.x() - r.right()) <= t
        near_t, near_b = abs(pos.y() - r.top()) <= t, abs(pos.y() - r.bottom()) <= t
        within_x = r.left() - t <= pos.x() <= r.right() + t
        within_y = r.top() - t <= pos.y() <= r.bottom() + t
        for name, hit in (('tl', near_l and near_t), ('tr', near_r and near_t),
                          ('bl', near_l and near_b), ('br', near_r and near_b)):
            if hit:
                return name
        if within_y and near_l:
            return 'l'
        if within_y and near_r:
            return 'r'
        if within_x and near_t:
            return 't'
        if within_x and near_b:
            return 'b'
        return 'move' if r.contains(pos) else 'new'

    def hoverMoveEvent(self, event):
        shapes = {'tl': Qt.SizeFDiagCursor, 'br': Qt.SizeFDiagCursor, 'tr': Qt.SizeBDiagCursor,
                  'bl': Qt.SizeBDiagCursor, 'l': Qt.SizeHorCursor, 'r': Qt.SizeHorCursor,
                  't': Qt.SizeVerCursor, 'b': Qt.SizeVerCursor, 'move': Qt.SizeAllCursor, 'new': Qt.CrossCursor}
        self.setCursor(shapes[self._hit(event.pos())])

    def mousePressEvent(self, event):
        pos = event.pos()
        self._mode, self._press, self._start = self._hit(pos), pos, QRectF(self.rect)
        if self._mode in ('l', 'r', 't', 'b') and self._source_ratio():
            # A fixed shape grows from a corner, not one side.
            self._mode = {'l': 'tl', 't': 'tl', 'r': 'br', 'b': 'br'}[self._mode]
        r = self.rect
        opposite = {'tl': r.bottomRight(), 'tr': r.bottomLeft(), 'bl': r.topRight(), 'br': r.topLeft()}
        if self._mode == 'new':
            self._anchor = self._clamp(pos)
        elif self._mode in opposite:
            self._anchor = opposite[self._mode]
        event.accept()

    def mouseMoveEvent(self, event):
        pos, b = event.pos(), self._bounds
        if self._mode == 'move':
            moved = self._start.translated(pos - self._press)
            dx = max(b.left() - moved.left(), 0) - max(moved.right() - b.right(), 0)
            dy = max(b.top() - moved.top(), 0) - max(moved.bottom() - b.bottom(), 0)
            self.rect = moved.translated(dx, dy)
        elif self._mode in ('new', 'tl', 'tr', 'bl', 'br'):
            self.rect = self._from_corner(self._anchor, pos)
        elif self._mode in ('l', 'r', 't', 'b'):
            p, r = self._clamp(pos), QRectF(self._start)
            if self._mode == 'l':
                r.setLeft(min(p.x(), r.right() - self.SMALLEST))
            elif self._mode == 'r':
                r.setRight(max(p.x(), r.left() + self.SMALLEST))
            elif self._mode == 't':
                r.setTop(min(p.y(), r.bottom() - self.SMALLEST))
            else:
                r.setBottom(max(p.y(), r.top() + self.SMALLEST))
            self.rect = r
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, event):
        if self.rect.width() < self.SMALLEST or self.rect.height() < self.SMALLEST:
            self.rect = self._start
            self.update()
            self.changed.emit()
        self._mode = ''

    def _clamp(self, p: QPointF) -> QPointF:
        b = self._bounds
        return QPointF(min(max(p.x(), b.left()), b.right()), min(max(p.y(), b.top()), b.bottom()))

    def _from_corner(self, anchor: QPointF, point: QPointF) -> QRectF:
        b, point = self._bounds, self._clamp(point)
        left, up = point.x() < anchor.x(), point.y() < anchor.y()
        w, h = abs(point.x() - anchor.x()), abs(point.y() - anchor.y())
        r = self._source_ratio()
        if r:
            room_w = anchor.x() - b.left() if left else b.right() - anchor.x()
            room_h = anchor.y() - b.top() if up else b.bottom() - anchor.y()
            w = max(w, h * r)
            w = min(w, room_w, room_h * r)
            h = w / r
        return QRectF(anchor.x() - w if left else anchor.x(), anchor.y() - h if up else anchor.y(), w, h)


# --------------------------------------------------------------------------- timeline


class Timeline(QWidget):
    """Frames along the clip, with handles for where it starts and ends."""
    seek = Signal(int)
    range_changed = Signal(int, int)
    GRIP = 12
    GAP_MS = 100

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(76)
        self.setMouseTracking(True)
        self.duration = 0
        self.position = 0
        self.start = 0
        self.end = 0
        self.strip: QPixmap | None = None
        self._drag = ''

    def set_duration(self, ms: int) -> None:
        self.duration, self.start, self.end = max(ms, 1), 0, max(ms, 1)
        self.update()

    def set_range(self, start: int, end: int) -> None:
        self.start, self.end = max(0, start), min(self.duration, end)
        self.update()
        self.range_changed.emit(self.start, self.end)

    def set_position(self, ms: int) -> None:
        self.position = ms
        self.update()

    def _track(self) -> QRectF:
        return QRectF(self.GRIP, 14, self.width() - 2 * self.GRIP, self.height() - 24)

    def _x(self, ms: float) -> float:
        t = self._track()
        return t.left() + t.width() * ms / self.duration

    def _ms(self, x: float) -> int:
        t = self._track()
        return round(min(max((x - t.left()) / t.width(), 0.0), 1.0) * self.duration)

    def _hit(self, x: float) -> str:
        if abs(x - (self._x(self.start) - self.GRIP / 2)) <= self.GRIP:
            return 'start'
        if abs(x - (self._x(self.end) + self.GRIP / 2)) <= self.GRIP:
            return 'end'
        return 'seek'

    def mousePressEvent(self, event):
        x = event.position().x()
        self._drag = self._hit(x)
        if self._drag == 'seek':
            self.seek.emit(self._ms(x))

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if not self._drag:
            self.setCursor(Qt.SizeHorCursor if self._hit(x) != 'seek' else Qt.PointingHandCursor)
            return
        ms = self._ms(x)
        if self._drag == 'start':
            self.set_range(min(ms, self.end - self.GAP_MS), self.end)
            self.seek.emit(self.start)
        elif self._drag == 'end':
            self.set_range(self.start, max(ms, self.start + self.GAP_MS))
            self.seek.emit(self.end)
        else:
            self.seek.emit(ms)

    def mouseReleaseEvent(self, event):
        self._drag = ''

    def paintEvent(self, event):
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = self._track()
        clip = QPainterPath()
        clip.addRoundedRect(track, 6, 6)
        p.fillPath(clip, c.chip)
        if self.strip:
            p.save()
            p.setClipPath(clip)
            p.drawPixmap(track.toRect(), self.strip)
            p.restore()
        s, e = self._x(self.start), self._x(self.end)
        p.save()
        p.setClipPath(clip)
        p.fillRect(QRectF(track.left(), track.top(), s - track.left(), track.height()), QColor(0, 0, 0, 160))
        p.fillRect(QRectF(e, track.top(), track.right() - e, track.height()), QColor(0, 0, 0, 160))
        p.restore()
        brand = QColor(theme.ACCENT)
        p.setPen(QPen(brand, 3))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(s, track.top() + 1.5, e - s, track.height() - 3))
        p.setPen(Qt.NoPen)
        p.setBrush(brand)
        for x, left in ((s, True), (e, False)):
            grip = QRectF(x - self.GRIP if left else x, track.top() - 3, self.GRIP, track.height() + 6)
            p.drawRoundedRect(grip, 4, 4)
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
            p.drawLine(QPointF(grip.center().x(), grip.top() + 10), QPointF(grip.center().x(), grip.bottom() - 10))
            p.setPen(Qt.NoPen)
        x = self._x(self.position)
        p.setPen(QPen(c.text, 2))
        p.drawLine(QPointF(x, track.top() - 8), QPointF(x, track.bottom() + 4))
        p.setPen(Qt.NoPen)
        p.setBrush(c.text)
        p.drawEllipse(QPointF(x, track.top() - 8), 4, 4)
        p.end()


# --------------------------------------------------------------------------- sound


class AudioView(QWidget):
    """What a sound-only file shows in place of a picture: its cover (or a
    note), its title, and its waveform to click around in."""
    seek = Signal(int)

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setMinimumHeight(260)
        self.setCursor(Qt.PointingHandCursor)
        self.title, self.subtitle = title, subtitle
        self.cover: QPixmap | None = None
        self.wave: QPixmap | None = None
        self.duration, self.position, self.start, self.end = 1, 0, 0, 1
        self.note = ''

    def _wave_rect(self) -> QRectF:
        return QRectF(28, 196, self.width() - 56, max(self.height() - 196 - 24, 40))

    def _ms(self, x: float) -> int:
        r = self._wave_rect()
        return round(min(max((x - r.left()) / r.width(), 0.0), 1.0) * self.duration)

    def mousePressEvent(self, event):
        if self._wave_rect().adjusted(0, -20, 0, 20).contains(event.position()):
            self.seek.emit(self._ms(event.position().x()))

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.seek.emit(self._ms(event.position().x()))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor('#0d0d10'))
        art = QRectF(28, 24, 148, 148)
        clip = QPainterPath()
        clip.addRoundedRect(art, 12, 12)
        if self.cover:
            p.save()
            p.setClipPath(clip)
            scaled = self.cover.scaled(art.size().toSize(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap(art.topLeft(), scaled, QRectF((scaled.width() - art.width()) / 2,
                                                       (scaled.height() - art.height()) / 2, art.width(), art.height()))
            p.restore()
        else:
            p.fillPath(clip, QColor('#22232b'))
            p.drawPixmap(art.adjusted(44, 44, -44, -44).toRect(), icons.glyph('audio', QColor('#9a9aa6'), 60, 1.4))
        p.setPen(QColor('#ececf1'))
        p.setFont(theme.font(15, QFont.DemiBold))
        text = QRectF(art.right() + 22, art.top() + 30, self.width() - art.right() - 50, 34)
        p.drawText(text, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.title, Qt.ElideRight, int(text.width())))
        p.setPen(QColor('#9a9aa6'))
        p.setFont(theme.font(10))
        p.drawText(text.translated(0, 34), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.subtitle, Qt.ElideRight, int(text.width())))
        if self.note:
            p.drawText(text.translated(0, 68), Qt.AlignLeft | Qt.AlignVCenter, self.note)

        r = self._wave_rect()
        if self.wave:
            p.drawPixmap(r.toRect(), self.wave)
        else:
            p.setPen(QPen(QColor('#34353f'), 1))
            p.drawLine(QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y()))
        x = lambda ms: r.left() + r.width() * ms / self.duration  # noqa: E731
        shade = QColor(13, 13, 16, 190)
        p.fillRect(QRectF(r.left(), r.top(), x(self.start) - r.left(), r.height()), shade)
        p.fillRect(QRectF(x(self.end), r.top(), r.right() - x(self.end), r.height()), shade)
        p.setPen(QPen(QColor('#ffffff'), 2))
        p.drawLine(QPointF(x(self.position), r.top() - 6), QPointF(x(self.position), r.bottom() + 6))
        p.end()


# --------------------------------------------------------------------------- the window

VIDEO_FORMATS = [('MP4', 'mp4'), ('WEBM', 'webm'), ('MOV', 'mov'), ('GIF', 'gif')]
SOUND_FROM_VIDEO = [('MP3 - sound only', 'mp3'), ('M4A - sound only', 'm4a'), ('WAV - sound only', 'wav')]
AUDIO_FORMATS = [('MP3', 'mp3'), ('M4A', 'm4a'), ('WAV', 'wav'), ('FLAC', 'flac'), ('OGG', 'ogg'),
                 ('OPUS', 'opus'), ('AIFF', 'aiff')]
LOSSLESS = ('wav', 'flac', 'aiff', 'gif')
VOLUMES = [('50%', 0.5), ('75%', 0.75), ('100%', 1.0), ('125%', 1.25), ('150%', 1.5), ('200%', 2.0),
           ('Even out', 'normalize')]
FADES = [('None', 0.0), ('0.5 s', 0.5), ('1 s', 1.0), ('2 s', 2.0), ('3 s', 3.0), ('5 s', 5.0)]


class _Loaded(QObject):
    strip = Signal(QImage)
    cover = Signal(QImage)


class MediaEditor(QWidget):
    """The editor for a video, an audio file or an animated GIF."""
    closed = Signal(object)

    def __init__(self, path: Path, export: Callable[[Path, MediaEdit], None]):
        super().__init__(None, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(icons.app_icon())
        theme.frost(self)
        self.path = path
        self._export = export
        self._rotate = 0
        self._native = QSizeF()

        info = media.probe(path)
        self.has_video = bool(media._streams(info, 'video'))
        self.has_audio = bool(media._streams(info, 'audio'))
        what = 'video' if self.has_video and self.has_audio else 'audio' if self.has_audio else 'GIF' \
            if path.suffix.lower() == '.gif' else 'video'
        self.setWindowTitle(f'{path.name} - Edit {what}')
        self.resize(1000, 760 if self.has_video else 600)
        video = media._streams(info, 'video')
        rate = video[0].get('avg_frame_rate', '30/1') if video else '30/1'
        try:
            num, _, den = rate.partition('/')
            self._frame_ms = 1000 / (float(num) / float(den or 1))
        except (ValueError, ZeroDivisionError):
            self._frame_ms = 1000 / 30
        if not self.has_video:
            self._frame_ms = 100        # ←/→ steps a tenth of a second through sound

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._player.positionChanged.connect(self._position)
        self._player.durationChanged.connect(self._duration)
        self._player.playbackStateChanged.connect(self._play_state)
        self._player.mediaStatusChanged.connect(self._status)
        self._player.errorOccurred.connect(self._error)

        # ------------------------------------------------------------- preview
        self._crop = None
        if self.has_video:
            self._scene = QGraphicsScene(self)
            self._view = QGraphicsView(self._scene)
            self._view.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
            self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._view.setBackgroundBrush(QColor('#0d0d10'))
            self._view.setFrameShape(QGraphicsView.NoFrame)
            self._view.setFocusPolicy(Qt.NoFocus)
            self._video = QGraphicsVideoItem()
            self._video.setAspectRatioMode(Qt.IgnoreAspectRatio)
            self._scene.addItem(self._video)
            self._video.nativeSizeChanged.connect(self._native_size)
            self._crop = CropOverlay(self._video)
            self._crop.hide()
            self._crop.changed.connect(self._refresh)
            self._message = QGraphicsSimpleTextItem()
            self._message.setBrush(QColor('#cccccc'))
            self._scene.addItem(self._message)
            self._player.setVideoOutput(self._video)
            preview = self._view
        else:
            tags = {k.lower(): v for k, v in info.get('format', {}).get('tags', {}).items()}
            subtitle = ' · '.join(t for t in (tags.get('artist') or tags.get('album_artist'), tags.get('album')) if t)
            self._sound = AudioView(tags.get('title') or path.stem, subtitle or path.suffix.lstrip('.').upper())
            self._sound.seek.connect(self._seek)
            preview = self._sound

        self._timeline = Timeline()
        self._timeline.set_duration(int(media.duration(info) * 1000) or 1)
        self._timeline.seek.connect(self._seek)
        self._timeline.range_changed.connect(lambda *_: self._refresh())

        # ------------------------------------------------------------- transport
        self._play = self._tool('play', 'Play / pause (Space)', self.toggle_play)
        self._time = QLabel()
        self._time.setFont(theme.font(10))
        set_start = self._button('Set start  I', lambda: self._set_start(self._player.position()))
        set_end = self._button('Set end  O', lambda: self._set_end(self._player.position()))
        self._selected = QLabel()
        self._selected.setEnabled(False)
        transport = QHBoxLayout()
        for widget in (self._play, self._time):
            transport.addWidget(widget)
        transport.addStretch(1)
        transport.addWidget(self._selected)
        transport.addSpacing(12)
        transport.addWidget(set_start)
        transport.addWidget(set_end)

        # ------------------------------------------------------------- changes
        self._picture_controls: list[QWidget] = []
        changes = QHBoxLayout()
        if self.has_video:
            self._crop_choice = self._combo([name for name, _ in CROPS], self._crop_changed)
            rotate_left = self._tool('rotate_left', 'Rotate left', lambda: self._turn(-90))
            rotate_right = self._tool('rotate_right', 'Rotate right', lambda: self._turn(90))
            self._flip = self._tool('flip', 'Mirror left to right', self._apply_transform)
            self._flip.setCheckable(True)
            self._picture_controls = [self._crop_choice, rotate_left, rotate_right, self._flip]
            for widget in (QLabel('Crop'), self._crop_choice, self._gap(), QLabel('Rotate'), rotate_left,
                           rotate_right, self._flip, self._gap()):
                changes.addWidget(widget)
        self._speed = self._combo([f'{s:g}×' for s in SPEEDS], self._speed_changed)
        self._speed.setCurrentIndex(SPEEDS.index(1.0))
        self._volume = self._combo([name for name, _ in VOLUMES], self._volume_changed)
        self._volume.setCurrentIndex(2)
        self._volume.setToolTip('"Even out" sets the loudness to the level streaming services use')
        self._fade_in = self._combo([name for name, _ in FADES], self._refresh)
        self._fade_out = self._combo([name for name, _ in FADES], self._refresh)
        self._mute = QCheckBox('Remove sound')
        self._mute.setFocusPolicy(Qt.NoFocus)
        self._mute.toggled.connect(self._mute_changed)
        self._sound_controls = [self._volume]
        changes.addWidget(QLabel('Speed'))
        changes.addWidget(self._speed)
        if self.has_audio:
            changes.addWidget(self._gap())
            changes.addWidget(QLabel('Volume'))
            changes.addWidget(self._volume)
        changes.addWidget(self._gap())
        for widget in (QLabel('Fade in'), self._fade_in, QLabel('out'), self._fade_out):
            changes.addWidget(widget)
        if self.has_video and self.has_audio:
            changes.addWidget(self._gap())
            changes.addWidget(self._mute)
        changes.addStretch(1)

        # ------------------------------------------------------------- export
        if self.has_video:
            self.formats = VIDEO_FORMATS + (SOUND_FROM_VIDEO if self.has_audio else [])
        else:
            self.formats = AUDIO_FORMATS
        self._format = self._combo([name for name, _ in self.formats], self._refresh)
        source = path.suffix.lower().lstrip('.')
        self._format.setCurrentIndex(next((i for i, (_, f) in enumerate(self.formats) if f == source), 0))
        self._size = self._combo([name for name, _ in SIZES], self._refresh)
        self._size.setVisible(self.has_video)
        self._quality = self._combo([name for name, _ in QUALITIES], self._refresh)
        self._fast = QCheckBox('Fast trim')
        self._fast.setFocusPolicy(Qt.NoFocus)
        self._fast.setToolTip('Copies the file instead of re-encoding it: done in seconds and loses\n'
                              'nothing, but cuts at the nearest keyframe, so the start may come a\n'
                              'moment early. Only for cutting (and removing the sound).')
        self._fast.toggled.connect(self._refresh)
        self._summary = QLabel()
        self._summary.setEnabled(False)
        self._go = QPushButton('Export')
        self._go.setFocusPolicy(Qt.NoFocus)
        self._go.setDefault(True)
        self._go.setMinimumWidth(110)
        self._go.setStyleSheet(f'QPushButton {{ background: {theme.ACCENT}; color: {theme.ON_ACCENT}; '
                               'border: none; border-radius: 6px; padding: 7px 16px; font-weight: 600; }'
                               f'QPushButton:hover {{ background: {theme.ACCENT_HOVER}; }}'
                               'QPushButton:disabled { background: #555a70; color: #c9c9d2; }')
        self._go.clicked.connect(self._export_now)
        output = QHBoxLayout()
        for widget in (QLabel('Save as'), self._format, self._size, self._quality, self._gap(), self._fast):
            output.addWidget(widget)
        output.addStretch(1)
        output.addWidget(self._summary)
        output.addSpacing(10)
        output.addWidget(self._go)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.addWidget(preview, 1)
        controls = QVBoxLayout()
        controls.setContentsMargins(14, 8, 14, 0)
        controls.addWidget(self._timeline)
        controls.addLayout(transport)
        controls.addSpacing(6)
        controls.addLayout(changes)
        controls.addLayout(output)
        layout.addLayout(controls)

        keys = {'Space': self.toggle_play, 'I': lambda: self._set_start(self._player.position()),
                'O': lambda: self._set_end(self._player.position()),
                'Left': lambda: self._step(-self._frame_ms), 'Right': lambda: self._step(self._frame_ms),
                'Shift+Left': lambda: self._step(-1000), 'Shift+Right': lambda: self._step(1000),
                'Home': lambda: self._seek(self._timeline.start), 'End': lambda: self._seek(self._timeline.end),
                'Escape': self.close}
        for key, action in keys.items():
            QShortcut(QKeySequence(key), self, action)

        self._loaded = _Loaded(self)
        self._loaded.strip.connect(self._got_strip)
        self._loaded.cover.connect(self._got_cover)
        threading.Thread(target=self._load_pictures, daemon=True).start()

        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.pause()     # shows the first frame
        self._refresh()

    # ------------------------------------------------------------------ small widgets

    def _tool(self, glyph: str, tip: str, action) -> QToolButton:
        button = QToolButton()
        button.setIcon(icons.glyph_icon(glyph, theme.colors().text, 20))
        button.setToolTip(tip)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(lambda *_: action())
        return button

    def _button(self, text: str, action) -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(lambda *_: action())
        return button

    def _combo(self, items: list[str], action) -> QComboBox:
        combo = QComboBox()
        combo.addItems(items)
        combo.setFocusPolicy(Qt.NoFocus)
        combo.currentIndexChanged.connect(lambda *_: action())
        return combo

    def _gap(self) -> QWidget:
        gap = QWidget()
        gap.setFixedWidth(14)
        return gap

    # ------------------------------------------------------------------ the picture

    def _native_size(self, size: QSizeF) -> None:
        if size.isEmpty() or size == self._native:
            return
        self._native = QSizeF(size)
        self._video.setSize(size)
        self._crop.set_bounds(QRectF(QPointF(0, 0), size))
        self._apply_transform()

    def _apply_transform(self) -> None:
        size = self._video.size()
        cx, cy = size.width() / 2, size.height() / 2
        transform = QTransform()
        transform.translate(cx, cy)
        if self._flip.isChecked():
            transform.scale(-1, 1)
        transform.rotate(self._rotate)
        transform.translate(-cx, -cy)
        self._video.setTransform(transform)
        self._crop.turned = self._rotate % 180 != 0
        if self._crop.ratio:
            self._crop.fill()
        self._scene.setSceneRect(self._video.sceneBoundingRect())
        self._fit()
        self._refresh()

    def _fit(self) -> None:
        if self.has_video and not self._video.size().isEmpty():
            self._view.fitInView(self._video.sceneBoundingRect(), Qt.KeepAspectRatio)

    def _turn(self, degrees: int) -> None:
        self._rotate = (self._rotate + degrees) % 360
        self._apply_transform()

    def _crop_changed(self) -> None:
        ratio = CROPS[self._crop_choice.currentIndex()][1]
        self._crop.setVisible(ratio is not None)
        if ratio is not None:
            self._crop.set_ratio(ratio, self._rotate % 180 != 0)
        self._refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def showEvent(self, event):
        super().showEvent(event)
        self._fit()

    # ------------------------------------------------------------------ playing

    def toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
            return
        position = self._player.position()
        if position < self._timeline.start or position >= self._timeline.end - 40:
            self._player.setPosition(self._timeline.start)
        self._player.play()

    def _seek(self, ms: int) -> None:
        self._player.setPosition(int(ms))
        self._show_position(int(ms))

    def _step(self, ms: float) -> None:
        self._player.pause()
        self._seek(min(max(self._player.position() + ms, 0), self._timeline.duration))

    def _position(self, ms: int) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState and ms >= self._timeline.end:
            self._player.pause()
            self._player.setPosition(self._timeline.end)
            ms = self._timeline.end
        self._show_position(ms)

    def _show_position(self, ms: int) -> None:
        self._timeline.set_position(ms)
        if not self.has_video:
            self._sound.position = ms
            self._sound.update()
        self._show_time(ms)

    def _show_time(self, ms: int) -> None:
        self._time.setText(f'{clock(ms)} / {clock(self._timeline.duration)}')

    def _duration(self, ms: int) -> None:
        if ms > 0 and self._timeline.duration <= 1:
            self._timeline.set_duration(ms)
            self._refresh()

    def _play_state(self, state) -> None:
        glyph = 'pause' if state == QMediaPlayer.PlayingState else 'play'
        self._play.setIcon(icons.glyph_icon(glyph, theme.colors().text, 20))

    def _status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            self._player.pause()

    def _error(self, error, text: str) -> None:
        message = f"Can't play this here ({text or error}), but cutting and exporting still work."
        if self.has_video:
            self._message.setText(message.replace(', but', ',\nbut'))
            self._message.setPos(20, 20)
        else:
            self._sound.note = message
            self._sound.update()

    def _set_start(self, ms: int) -> None:
        self._timeline.set_range(min(ms, self._timeline.end - Timeline.GAP_MS), self._timeline.end)

    def _set_end(self, ms: int) -> None:
        self._timeline.set_range(self._timeline.start, max(ms, self._timeline.start + Timeline.GAP_MS))

    def _speed_changed(self) -> None:
        self._player.setPlaybackRate(SPEEDS[self._speed.currentIndex()])
        self._refresh()

    def _volume_changed(self) -> None:
        # The preview can only go quieter, not louder; the export does both.
        level = VOLUMES[self._volume.currentIndex()][1]
        self._audio.setVolume(min(level, 1.0) if isinstance(level, float) else 1.0)
        self._refresh()

    def _mute_changed(self, muted: bool) -> None:
        self._audio.setMuted(muted)
        self._refresh()

    # ------------------------------------------------------------------ timeline pictures

    def _load_pictures(self) -> None:
        if self.has_video:
            strip = media.filmstrip(self.path)
            cover = b''
        else:
            strip = media.waveform(self.path, 1600, 160)
            cover = media.cover_art(self.path)
        try:
            self._loaded.strip.emit(QImage.fromData(strip, 'PNG') if strip else QImage())
            if cover:
                self._loaded.cover.emit(QImage.fromData(cover))
        except RuntimeError:
            pass     # the window was closed first

    def _got_strip(self, image: QImage) -> None:
        if image.isNull():
            return
        self._timeline.strip = QPixmap.fromImage(image)
        self._timeline.update()
        if not self.has_video:
            self._sound.wave = self._timeline.strip
            self._sound.update()

    def _got_cover(self, image: QImage) -> None:
        if not image.isNull():
            self._sound.cover = QPixmap.fromImage(image)
            self._sound.update()

    # ------------------------------------------------------------------ export

    def edit(self) -> MediaEdit:
        start, end, total = self._timeline.start, self._timeline.end, self._timeline.duration
        target = self.formats[self._format.currentIndex()][1]
        sound_only = target in media.SOUND_FORMATS
        cropping = self._crop is not None and self._crop.isVisible() and not self._crop.is_whole()
        picture = self.has_video and not sound_only
        level = VOLUMES[self._volume.currentIndex()][1] if self.has_audio else 1.0
        return MediaEdit(start=start / 1000, end=None if end >= total else end / 1000,
                         crop=self._crop.fraction() if cropping and picture else None,
                         rotate=self._rotate if picture else 0,
                         flip=picture and self._flip.isChecked(),
                         speed=SPEEDS[self._speed.currentIndex()],
                         mute=self.has_audio and self._mute.isChecked() and not sound_only,
                         volume=level if isinstance(level, float) else 1.0,
                         normalize=level == 'normalize',
                         fade_in=FADES[self._fade_in.currentIndex()][1],
                         fade_out=FADES[self._fade_out.currentIndex()][1],
                         target=target,
                         height=SIZES[self._size.currentIndex()][1] if picture else 0,
                         quality=QUALITIES[self._quality.currentIndex()][1],
                         fast=self._fast.isChecked() and self._fast.isEnabled())

    def _refresh(self) -> None:
        if not hasattr(self, '_go'):
            return       # still being built
        edit = self.edit()
        # Fast trim copies the streams as they are, so it can only cut.
        only_cuts = (not edit.crop and not edit.rotate and not edit.flip and edit.speed == 1
                     and not edit.height and edit.volume == 1 and not edit.normalize
                     and not edit.fade_in and not edit.fade_out)
        self._fast.setEnabled(only_cuts)
        fast = self._fast.isChecked() and only_cuts
        self._format.setEnabled(not fast)
        self._size.setEnabled(not fast and not edit.sound_only)
        self._quality.setEnabled(not fast and edit.target not in LOSSLESS)
        for widget in self._picture_controls:
            widget.setEnabled(not edit.sound_only)
        self._mute.setEnabled(not edit.sound_only)
        length = edit.length(self._timeline.duration / 1000)
        self._selected.setText(f'Selected {clock(length * 1000 * edit.speed)}')
        self._summary.setText(self._describe_output(edit, length))
        if not self.has_video:
            self._sound.start, self._sound.end = self._timeline.start, self._timeline.end
            self._sound.duration = self._timeline.duration
            self._sound.update()
        self._show_time(self._player.position())

    def _describe_output(self, edit: MediaEdit, length: float) -> str:
        target = self.path.suffix.lstrip('.').upper() if edit.fast else edit.target.upper()
        if edit.sound_only or not self.has_video or self._native.isEmpty():
            return f'{target} · {clock(length * 1000)}'
        w, h = self._native.width(), self._native.height()
        if edit.crop:
            w, h = w * edit.crop[2], h * edit.crop[3]
        if edit.rotate % 180:
            w, h = h, w
        if edit.target == 'gif' and not edit.fast:
            scale = min(1.0, 640 / w) if w else 1.0
            if edit.height:
                scale = min(scale, edit.height / h)
            w, h = w * scale, h * scale
        elif edit.height and h > edit.height and not edit.fast:
            w, h = w * edit.height / h, edit.height
        return f'{target} · {int(w) // 2 * 2} × {int(h) // 2 * 2} · {clock(length * 1000)}'

    def _export_now(self) -> None:
        self._player.pause()
        self._export(self.path, self.edit())
        self._summary.setText('Exporting - the card in the corner shows how far along it is')

    def describe(self) -> str:
        """For the development pipe: what is open, and whether it is showing."""
        if self.has_video:
            frame = self._video.videoSink().videoFrame().isValid()
            return f'MediaEditor {self._native.width():.0f}x{self._native.height():.0f}{" frame" if frame else ""}'
        loaded = self._player.mediaStatus() in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia)
        return f'MediaEditor audio{" loaded" if loaded else ""}{" wave" if self._sound.wave else ""}'

    def closeEvent(self, event):
        self._player.stop()
        self._player.setSource(QUrl())     # let go of the file, so it can be moved or deleted
        self.closed.emit(self)
        super().closeEvent(event)
