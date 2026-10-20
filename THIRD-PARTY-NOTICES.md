# Third-party software in Dropshift

| Component | Licence | Use |
|-----------|---------|-----|
| Qt 6 / PySide6 | LGPL-3.0 | windows, drawing, drag and drop |
| Qt Multimedia, with its own FFmpeg libraries | LGPL-3.0 | the video editor's player |
| Pillow | MIT-CMU (HPND) | pictures |
| pypdf | BSD-3-Clause | combining, splitting, rotating and compressing PDFs |
| pypdfium2 / PDFium | Apache-2.0 or BSD-3-Clause / BSD-3-Clause | rendering PDF pages, reading their text |
| pywin32 | PSF | Office automation, shell |
| FFmpeg (yt-dlp FFmpeg-Builds, win64 gpl-shared) | GPL-3.0 | video, audio and subtitles |

FFmpeg is not linked into Dropshift: it is a separate program in
`tools\ffmpeg\`, run as a command, with its licence in `tools\ffmpeg\LICENSE.txt`.
Its source is at https://ffmpeg.org and the build recipe at
https://github.com/yt-dlp/FFmpeg-Builds.

Microsoft Office and 7-Zip are used only if already installed, and are not
distributed with Dropshift.
