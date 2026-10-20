"""Video, audio and subtitles, through FFmpeg.

Streams that already suit the target are copied rather than re-encoded, so
turning an MKV of H.264 into an MP4 takes seconds and loses nothing.
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import locate, win32
from ..jobs import Job, UserError

AUDIO_ENCODERS = {
    'mp3': ['-c:a', 'libmp3lame', '-q:a', '2'],
    'wav': ['-c:a', 'pcm_s16le'],
    'flac': ['-c:a', 'flac'],
    'm4a': ['-c:a', 'aac', '-b:a', '192k'],
    'ogg': ['-c:a', 'libvorbis', '-q:a', '5'],
    'opus': ['-c:a', 'libopus', '-b:a', '128k'],
    'aiff': ['-c:a', 'pcm_s16be'],
}
# Codecs each audio container takes as they are.
AUDIO_COPYABLE = {'mp3': {'mp3'}, 'm4a': {'aac', 'alac'}, 'flac': {'flac'},
                  'ogg': {'vorbis'}, 'opus': {'opus'}, 'wav': {'pcm_s16le'}, 'aiff': {'pcm_s16be'}}

H264 = ['-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-pix_fmt', 'yuv420p']
EVEN = 'scale=trunc(iw/2)*2:trunc(ih/2)*2'


def _ffmpeg() -> Path:
    path = locate.ffmpeg()
    if not path:
        raise UserError('FFmpeg is missing, so video and audio are unavailable')
    return path


def probe(src: Path) -> dict:
    ffprobe = locate.ffprobe()
    if not ffprobe:
        return {}
    result = win32.run_hidden([ffprobe, '-v', 'error', '-print_format', 'json', '-show_format',
                               '-show_streams', src], capture_output=True, text=True,
                              encoding='utf-8', errors='replace')
    try:
        return json.loads(result.stdout or '{}')
    except json.JSONDecodeError:
        return {}


def _streams(info: dict, kind: str) -> list[dict]:
    return [s for s in info.get('streams', []) if s.get('codec_type') == kind
            and not s.get('disposition', {}).get('attached_pic')]


def _codec(info: dict, kind: str) -> str:
    streams = _streams(info, kind)
    return streams[0].get('codec_name', '') if streams else ''


def duration(info: dict) -> float:
    try:
        return float(info.get('format', {}).get('duration') or 0)
    except ValueError:
        return 0.0


def run(job: Job | None, args: list, length: float = 0.0) -> None:
    """Run FFmpeg, turning its progress output into job.progress()."""
    command = [_ffmpeg(), '-hide_banner', '-nostdin', '-y', '-loglevel', 'error',
               '-progress', 'pipe:1', '-nostats', *args]
    errors: list[str] = []
    with win32.popen_hidden(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='replace') as process:
        reader = threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True)
        reader.start()
        for line in process.stdout:
            if job and job.cancelled:
                process.kill()
                break
            key, _, value = line.strip().partition('=')
            if job and length and key in ('out_time_us', 'out_time_ms') and value.isdigit():
                job.progress(int(value) / 1_000_000 / length)
        process.wait()
        reader.join(timeout=5)
    if process.returncode:
        message = next((e.strip() for e in reversed(errors) if e.strip()), 'FFmpeg failed')
        raise UserError(message)


# --------------------------------------------------------------------------- conversions


def convert_video(job: Job, src: Path, target: str) -> None:
    info = probe(src)
    if target in AUDIO_ENCODERS:
        return extract_audio(job, src, target, info)
    if not _streams(info, 'video') and info:
        raise UserError(f'{src.name} has no picture to convert')
    vcodec, acodec = _codec(info, 'video'), _codec(info, 'audio')
    out = job.output_for(src, target)
    args = ['-i', src]

    if target in ('mp4', 'mov'):
        args += ['-map', '0:v:0', '-map', '0:a:0?']
        if vcodec == 'h264':
            args += ['-c:v', 'copy']
        elif vcodec == 'hevc':
            args += ['-c:v', 'copy', '-tag:v', 'hvc1']
        else:
            args += H264 + ['-vf', EVEN]
        args += ['-c:a', 'copy'] if acodec in ('aac', 'mp3', 'alac') else ['-c:a', 'aac', '-b:a', '192k']
        args += ['-movflags', '+faststart']
    elif target == 'webm':
        args += ['-map', '0:v:0', '-map', '0:a:0?']
        args += (['-c:v', 'copy'] if vcodec in ('vp8', 'vp9', 'av1') else
                 ['-c:v', 'libvpx-vp9', '-crf', '32', '-b:v', '0', '-row-mt', '1',
                  '-deadline', 'good', '-cpu-used', '4'])
        args += ['-c:a', 'copy'] if acodec in ('opus', 'vorbis') else ['-c:a', 'libopus', '-b:a', '128k']
    elif target == 'mkv':
        args += ['-map', '0', '-c', 'copy']
    elif target == 'avi':
        args += ['-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'mpeg4', '-q:v', '3', '-vf', EVEN,
                 '-c:a', 'libmp3lame', '-q:a', '2']
    elif target == 'gif':
        args += ['-vf', "fps=15,scale='min(640,iw)':-2:flags=lanczos,split[a][b];"
                        '[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4',
                 '-loop', '0']
    else:
        raise UserError(f"Can't make {target.upper()} from video")
    args.append(out)
    try:
        run(job, args, duration(info))
    except UserError:
        if target != 'mkv':
            raise
        # Some streams (MP4's own subtitle format, say) can't go into MKV as
        # they are; keep just the picture and sound.
        run(job, ['-i', src, '-map', '0:v', '-map', '0:a?', '-c', 'copy', out], duration(info))
    job.add(out)


def extract_audio(job: Job, src: Path, target: str, info: dict | None = None) -> None:
    info = info if info is not None else probe(src)
    if info and not _streams(info, 'audio'):
        raise UserError(f'{src.name} has no sound')
    out = job.output_for(src, target)
    copy = _codec(info, 'audio') in AUDIO_COPYABLE.get(target, ())
    codec = ['-c:a', 'copy'] if copy else AUDIO_ENCODERS[target]
    run(job, ['-i', src, '-map', '0:a:0', '-vn', *codec, out], duration(info))
    job.add(out)


def convert_audio(job: Job, src: Path, target: str) -> None:
    extract_audio(job, src, target)


def convert_subtitles(job: Job, src: Path, target: str) -> None:
    out = job.output_for(src, target)
    run(job, ['-i', src, out])
    job.add(out)


# --------------------------------------------------------------------------- tools


def compress_video(job: Job, src: Path) -> None:
    info = probe(src)
    out = job.output_for(src, 'mp4', 'compressed')
    run(job, ['-i', src, '-map', '0:v:0', '-map', '0:a:0?',
              '-c:v', 'libx264', '-preset', 'medium', '-crf', '28', '-pix_fmt', 'yuv420p',
              '-vf', "scale=-2:'min(1080,trunc(ih/2)*2)'",
              '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', out], duration(info))
    _keep_if_smaller(job, src, out)


def compress_audio(job: Job, src: Path) -> None:
    info = probe(src)
    target = 'mp3' if src.suffix.lower() == '.mp3' else 'm4a'
    codec = ['-c:a', 'libmp3lame', '-b:a', '128k'] if target == 'mp3' else ['-c:a', 'aac', '-b:a', '128k']
    out = job.output_for(src, target, 'compressed')
    run(job, ['-i', src, '-map', '0:a:0', '-vn', *codec, out], duration(info))
    _keep_if_smaller(job, src, out)


def _keep_if_smaller(job: Job, src: Path, out: Path) -> None:
    if out.stat().st_size >= src.stat().st_size:
        out.unlink()
        job.notes.append(f'{src.name} is already as small as it gets')
        return
    job.add(out)


def _container(src: Path) -> str:
    return src.suffix.lower().lstrip('.') or 'mp4'


def half_size_video(job: Job, src: Path) -> None:
    info = probe(src)
    out = job.output_for(src, _container(src), 'half size')
    run(job, ['-i', src, '-map', '0:v:0', '-map', '0:a:0?', *H264,
              '-vf', 'scale=trunc(iw/4)*2:trunc(ih/4)*2', '-c:a', 'copy', out], duration(info))
    job.add(out)


def rotate_video(job: Job, src: Path, clockwise: bool) -> None:
    info = probe(src)
    out = job.output_for(src, _container(src), 'rotated')
    run(job, ['-i', src, '-map', '0:v:0', '-map', '0:a:0?', *H264,
              '-vf', 'transpose=1' if clockwise else 'transpose=2', '-c:a', 'copy', out], duration(info))
    job.add(out)


def mute(job: Job, src: Path) -> None:
    out = job.output_for(src, _container(src), 'no sound')
    run(job, ['-i', src, '-map', '0', '-map', '-0:a', '-c', 'copy', out])
    job.add(out)


def strip_metadata(job: Job, src: Path) -> None:
    out = job.output_for(src, _container(src), 'no metadata')
    run(job, ['-i', src, '-map', '0', '-map_metadata', '-1', '-map_chapters', '-1', '-c', 'copy', out])
    job.add(out)


def snapshot(job: Job, src: Path) -> None:
    """The frame a third of the way in - past most fade-ins and title cards."""
    info = probe(src)
    at = duration(info) / 3 if duration(info) else 0
    out = job.output_for(src, 'png', 'frame')
    run(job, ['-ss', f'{at:.3f}', '-i', src, '-frames:v', '1', out])
    job.add(out)


def join_audio(job: Job) -> None:
    files = job.files
    target = files[0].suffix.lower().lstrip('.')
    if target not in AUDIO_ENCODERS:
        target = 'm4a'
    total = sum(duration(probe(f)) for f in files)
    out = job.output_named(files[0], f'Joined.{target}')
    args: list = []
    for f in files:
        args += ['-i', f]
    # Brought to one rate and layout first: concat needs its pieces to match.
    even = ';'.join(f'[{i}:a:0]aresample=48000,aformat=channel_layouts=stereo[a{i}]'
                    for i in range(len(files)))
    pieces = ''.join(f'[a{i}]' for i in range(len(files)))
    args += ['-filter_complex', f'{even};{pieces}concat=n={len(files)}:v=0:a=1[a]', '-map', '[a]',
             *AUDIO_ENCODERS[target], out]
    run(job, args, total)
    job.add(out)


# --------------------------------------------------------------------------- the media editor

SOUND_FORMATS = ('mp3', 'm4a', 'wav', 'flac', 'ogg', 'opus', 'aiff')
SOUND_CODECS = {'mp3': 'libmp3lame', 'm4a': 'aac', 'wav': 'pcm_s16le', 'flac': 'flac',
                'ogg': 'libvorbis', 'opus': 'libopus', 'aiff': 'pcm_s16be'}
SOUND_QUALITY = {'mp3': {'high': ['-q:a', '0'], 'medium': ['-q:a', '2'], 'small': ['-q:a', '5']},
                 'm4a': {'high': ['-b:a', '256k'], 'medium': ['-b:a', '192k'], 'small': ['-b:a', '128k']},
                 'ogg': {'high': ['-q:a', '7'], 'medium': ['-q:a', '5'], 'small': ['-q:a', '3']},
                 'opus': {'high': ['-b:a', '192k'], 'medium': ['-b:a', '128k'], 'small': ['-b:a', '80k']}}
QUALITY = {'mp4': {'high': ('18', '192k'), 'medium': ('23', '160k'), 'small': ('28', '128k')},
           'webm': {'high': ('28', '160k'), 'medium': ('33', '128k'), 'small': ('38', '96k')}}
LOUDNESS = 'loudnorm=I=-16:TP=-1.5:LRA=11'     # EBU R128, the level streaming services aim for


@dataclass
class MediaEdit:
    """What the editor window asks for, for a video, an audio file or a GIF.

    Crop is (x, y, width, height) as fractions of the picture as it is shown -
    upright, before Rotate. Start and end are in the original's seconds; fades
    are seconds of the finished file.
    """
    start: float = 0.0
    end: float | None = None
    crop: tuple[float, float, float, float] | None = None
    rotate: int = 0             # degrees clockwise: 0, 90, 180, 270
    flip: bool = False          # mirror left to right, after rotating
    speed: float = 1.0
    mute: bool = False
    volume: float = 1.0         # 1 as it is, 0.5 half as loud, 2 twice
    normalize: bool = False     # even the loudness out instead of a fixed volume
    fade_in: float = 0.0
    fade_out: float = 0.0
    target: str = 'mp4'         # mp4, mov, webm, gif - or a sound format: mp3, m4a, wav...
    height: int = 0             # 0 keeps the size; else at most this tall
    quality: str = 'high'       # high, medium, small
    fast: bool = False          # trim by copying, cutting at the nearest keyframe

    @property
    def sound_only(self) -> bool:
        return self.target in SOUND_FORMATS

    def length(self, total: float) -> float:
        end = total if self.end is None else min(self.end, total or self.end)
        return max(0.0, end - self.start) / (self.speed or 1)


def _atempo(speed: float) -> list[str]:
    # atempo takes 0.5 to 100 at a time; slower than half speed is chained.
    steps = []
    while speed < 0.5:
        steps.append('atempo=0.5')
        speed /= 0.5
    steps.append(f'atempo={speed:g}')
    return steps


def _sound_filters(edit: MediaEdit, length: float, rate: int) -> list[str]:
    chain = _atempo(edit.speed) if edit.speed != 1 else []
    if edit.normalize:
        # loudnorm works at 192 kHz; put the sound back at its own rate after.
        chain += [LOUDNESS, f'aresample={rate or 48000}']
    elif edit.volume != 1:
        chain.append(f'volume={edit.volume:g}')
    if edit.fade_in:
        chain.append(f'afade=t=in:st=0:d={edit.fade_in:g}')
    if edit.fade_out and length:
        chain.append(f'afade=t=out:st={max(length - edit.fade_out, 0):.3f}:d={edit.fade_out:g}')
    return chain


def _picture_fades(edit: MediaEdit, length: float) -> list[str]:
    fades = []
    if edit.fade_in:
        fades.append(f'fade=t=in:st=0:d={edit.fade_in:g}')
    if edit.fade_out and length:
        fades.append(f'fade=t=out:st={max(length - edit.fade_out, 0):.3f}:d={edit.fade_out:g}')
    return fades


def edit_args(src: Path, edit: MediaEdit, out: Path, has_audio: bool = True, total: float = 0.0,
              rate: int = 0) -> list:
    """FFmpeg's arguments for an edit (everything after the global options).
    total is the original's length, which fading out at the end needs."""
    length = edit.length(total)
    # Both before -i, so they choose what is read: quick, exact when re-encoding,
    # and measured in the original's time however the speed is changed.
    args: list = []
    if edit.start > 0:
        args += ['-ss', f'{edit.start:.3f}']
    if edit.end is not None:
        args += ['-t', f'{max(edit.end - edit.start, 0.01):.3f}']
    args += ['-i', src]

    if edit.fast:
        args += ['-map', '0', *(['-map', '-0:a'] if edit.mute else []), '-c', 'copy',
                 '-avoid_negative_ts', 'make_zero', out]
        return args

    if edit.sound_only:
        if not has_audio:
            raise UserError(f'{src.name} has no sound to save')
        sound = _sound_filters(edit, length, rate)
        args += ['-map', '0:a:0', '-vn', *(['-af', ','.join(sound)] if sound else []),
                 '-c:a', SOUND_CODECS[edit.target], *SOUND_QUALITY.get(edit.target, {}).get(edit.quality, [])]
        return args + [out]

    video = []
    if edit.crop:
        x, y, w, h = edit.crop
        video.append(f'crop=trunc(iw*{w:.6f}/2)*2:trunc(ih*{h:.6f}/2)*2:trunc(iw*{x:.6f}):trunc(ih*{y:.6f})')
    video += {90: ['transpose=1'], 180: ['transpose=1', 'transpose=1'], 270: ['transpose=2']}.get(edit.rotate % 360, [])
    if edit.flip:
        video.append('hflip')
    if edit.speed != 1:
        video.append(f'setpts=PTS/{edit.speed:g}')
    video += _picture_fades(edit, length)

    if edit.target == 'gif':
        tall = f'min({edit.height},ih)' if edit.height else 'ih'
        video += ['fps=15', f"scale='min(640,iw)':'{tall}':force_original_aspect_ratio=decrease:flags=lanczos"]
        graph = ','.join(video) + ',split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4'
        return args + ['-vf', graph, '-an', '-loop', '0', out]

    if edit.height:
        video.append(f"scale=-2:'trunc(min({edit.height},ih)/2)*2'")
    video += ['scale=trunc(iw/2)*2:trunc(ih/2)*2', 'format=yuv420p']
    args += ['-map', '0:v:0', '-vf', ','.join(video)]
    crf, bitrate = QUALITY['webm' if edit.target == 'webm' else 'mp4'][edit.quality]
    if edit.target == 'webm':
        args += ['-c:v', 'libvpx-vp9', '-crf', crf, '-b:v', '0', '-row-mt', '1', '-deadline', 'good',
                 '-cpu-used', '4']
    else:
        args += ['-c:v', 'libx264', '-preset', 'medium', '-crf', crf]

    if edit.mute or not has_audio:
        args.append('-an')
    else:
        sound = _sound_filters(edit, length, rate)
        args += ['-map', '0:a:0', *(['-af', ','.join(sound)] if sound else [])]
        args += ['-c:a', 'libopus', '-b:a', bitrate] if edit.target == 'webm' else ['-c:a', 'aac', '-b:a', bitrate]
    if edit.target in ('mp4', 'mov'):
        args += ['-movflags', '+faststart']
    return args + [out]


def edit_media(job: Job, src: Path, edit: MediaEdit) -> None:
    info = probe(src)
    sound = _streams(info, 'audio')
    if not edit.sound_only and not edit.fast and info and not _streams(info, 'video'):
        raise UserError(f'{src.name} has no picture, so it can only be saved as sound')
    target = _container(src) if edit.fast else edit.target
    out = job.output_for(src, target, 'edited')
    total = duration(info)
    rate = int(sound[0].get('sample_rate') or 0) if sound else 0
    run(job, edit_args(src, edit, out, has_audio=bool(sound), total=total, rate=rate), edit.length(total))
    job.add(out)


def _picture(args: list, timeout: int = 60) -> bytes:
    if not locate.ffmpeg():
        return b''
    result = win32.run_hidden([locate.ffmpeg(), '-hide_banner', '-loglevel', 'error', '-nostdin', *args,
                               '-frames:v', '1', '-f', 'image2pipe', '-c:v', 'png', '-'],
                              capture_output=True, timeout=timeout)
    return result.stdout if result.returncode == 0 else b''


def filmstrip(src: Path, count: int = 12, height: int = 54) -> bytes:
    """A row of evenly spaced frames as one PNG, for the editor's timeline."""
    length = duration(probe(src))
    if not length:
        return b''
    return _picture(['-i', src, '-vf', f'fps={count}/{length:.3f},scale=-2:{height},tile={count}x1'])


def waveform(src: Path, width: int = 1600, height: int = 240, color: str = '#ee9fb5') -> bytes:
    """The sound's shape as a PNG, transparent around it. Square-root scaled,
    so quiet passages still show."""
    return _picture(['-i', src, '-filter_complex',
                     f'aformat=channel_layouts=mono,showwavespic=s={width}x{height}:colors={color}'
                     ':scale=sqrt:filter=peak'])


def cover_art(src: Path) -> bytes:
    """The picture embedded in an audio file (an album cover), if it has one."""
    info = probe(src)
    for index, stream in enumerate(info.get('streams', [])):
        if stream.get('disposition', {}).get('attached_pic'):
            return _picture(['-i', src, '-map', f'0:{index}'])
    return b''
