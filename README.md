# Dropshift

Drag a file, press a key, drop it on what it should become. A Windows take on
the Mac's Tangerine, and part of the same suite as Homework Hub and Grabbit.

Everything happens on your PC; nothing is uploaded.

## Installing

Through [Hanami](https://github.com/blossqt/hanami), the suite's launcher:
click **Install** on Dropshift's card, and Hanami keeps it up to date. Or
download `Dropshift-<version>-win64.zip` from the
[latest release](https://github.com/blossqt/dropshift/releases/latest), unzip it
somewhere of your own and run `Dropshift.exe`.

## Using it

Dropshift sits in the tray. Start dragging any file - from Explorer, the
desktop, a download bar - and while the button is still held, press:

| Key   | Menu    | What's in it |
|-------|---------|--------------|
| Shift | Convert | a copy in another format: PNG, JPG, WEBP, AVIF, GIF, ICO, PDF; MP4, MOV, WEBM, MKV, GIF, MP3; Word, PDF, XLSX, CSV, PPTX; ZIP, 7Z... |
| Ctrl  | Tools   | edit video, audio or GIFs, compress, half size, rotate, flip, grayscale, show metadata, remove metadata, combine into a PDF, split pages, join audio, remove sound, grab a frame, zip, extract |
| Alt   | Actions | copy (to paste anywhere), copy path, float, move to, copy to, terminal here, open with, properties |

The menu appears under the cursor; drop the file on a choice. Results are
saved next to the original as `name.jpg`, `name (compressed).png` and so on,
never over an existing file. A card in the corner shows progress, then
**Show in folder**. Hover over a menu's tabs to switch between them mid-drag;
Esc, or dragging well away from the menu, closes it.

The keys, and where results go, can be changed from the tray icon's
**Settings**. A drop is always taken as a copy: Explorer reads Shift as
"move", and Dropshift never lets that delete your original.

**Edit** opens an editor for any video, audio file or animated GIF (the tile
says Edit video, Edit audio or Edit GIF). Play it, drag the handles on the
timeline to trim it (or press I and O at the playhead), change the speed, the
volume - or **Even out** the loudness - and fade in or out. Pictures can also
be cropped (free, or 16:9, 9:16, 1:1...), rotated and mirrored, and lose their
sound. Export a video as MP4, WEBM, MOV or GIF at a chosen size and quality,
or just its sound as MP3, M4A or WAV; audio as MP3, M4A, WAV, FLAC, OGG, OPUS
or AIFF. A song shows its cover, title and waveform instead of a picture.
**Fast trim** skips re-encoding when only cutting: done in seconds with no loss,
but it cuts at the nearest keyframe. Keys: Space play/pause, ←/→ a frame (a
tenth of a second for audio), Shift+←/→ a second, Home/End the start and end
of the selection.

**Show metadata** lists what a file says about itself: for photos the camera,
settings, date and GPS location (with a map link); for video and audio the
codecs, resolution, frame rate and tags; for PDFs and Office files the author,
dates and page or word counts. **Remove metadata** is a button away.

**Float** keeps files in a small always-on-top shelf to drag somewhere later -
handy for collecting files from several folders.

**From a web page.** Drag a picture or a link out of Chrome, Edge or Firefox
and press a key, as with a file. Each menu starts with **Download**, which
saves it as it is. **Grabbit** hands the address to Grabbit, when it's
installed, for a video page, a post of several pictures or a playlist. A
picture, or a link to a file such as a PDF, also gets the usual choices for
that kind of file: drop it on PNG and it is downloaded, then converted. With
no folder of its own, what it makes goes to your Downloads folder, or to the
folder chosen in Settings. A file is named for what the server actually sent,
so a picture whose address ends in .jpg but which arrives as a WEBP is saved
as a WEBP.

## What it needs

- **FFmpeg** for video, audio and subtitles - bundled with the app.
- **Microsoft Word, Excel and PowerPoint** for documents, spreadsheets and
  slides (and PDF to Word). Without them those choices simply don't appear.
- **7-Zip**, if installed, for 7Z, RAR and ISO archives. ZIP and TAR work without it.

The tray's Settings window lists which of these it found.

## Limits

- Words selected on a web page are left alone: only files, pictures and links
  open the menu.
- A picture behind a sign-in, or one the page made itself (a blob: address),
  may not download: Dropshift asks the site for it as a visitor would.
- A program running as administrator can't drop onto Dropshift; Windows blocks
  drags from a higher-privilege window to a normal one.
- Ctrl mid-drag is also Explorer's "copy". With Tools on Ctrl, pressing it
  opens the menu; drag away from the menu and it closes, and Explorer's copy
  still happens. Set Tools to Off in Settings if that gets in the way.
- Keep Dropshift in an ordinary folder. From a very deep one (a path over
  about 200 characters) Qt's DLLs can't load and it won't start.
- No Windows share sheet yet.

## Building

Like the other suite apps, everything heavy lives in
`%LOCALAPPDATA%\DropshiftBuild`, not in the project folder.

```powershell
powershell -ExecutionPolicy Bypass -File build\bootstrap.ps1     # venv + FFmpeg (once)
powershell -ExecutionPolicy Bypass -File build\build_app.ps1     # -> dist\Dropshift\Dropshift.exe
```

Run from source with `%LOCALAPPDATA%\DropshiftBuild\venv\Scripts\python.exe app\main.py`.

## Releasing

```
powershell -ExecutionPolicy Bypass -File build\release.ps1 --notes "What's new"
```

Builds, checks the exe is the version in `app\dropshift\__init__.py` (keep
`build\version_info.txt` in step), zips it, signs `latest.json` with the key in
`~\.dropshift\release\`, and publishes the release Hanami installs from. See the
top of `build\release.py`. The script is Hanami's, copied here.

## Tests

```powershell
$py = "$env:LOCALAPPDATA\DropshiftBuild\venv\Scripts\python.exe"
& $py -m unittest discover -s tests -v       # every conversion, tool and action, on real files
& $py tests\drag_test.py                      # a real drag and drop (takes the mouse ~3 s)
& $py tests\drag_test.py --editor             # ...a video, with Ctrl, onto Edit
& $py tests\window_test.py --exe dist\Dropshift\Dropshift.exe   # editor (video, song, GIF) + metadata, no input
```

`test_engines.py` drives Word, Excel and PowerPoint unseen; set
`DROPSHIFT_SKIP_OFFICE=1` to skip those. `drag_test.py` clicks only on its own
test window and drops only onto Dropshift; anything else cancels the drag.

## How it works

Windows has no "a drag started" event, so `watcher.py` polls the mouse and
keyboard: the button held, the cursor moved past the drag threshold, then a
trigger key going down. The menu (`menu.py`) opens nearly invisible under the
cursor and nudges the cursor a pixel so Windows' drag loop notices it; only
when Windows hands it the drag (DragEnter) does it know there are files - and
which - and fade in. Drags of anything else never show it.
