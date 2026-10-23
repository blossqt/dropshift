# PyInstaller spec for Dropshift - built by build_app.ps1
import os

project = os.path.abspath(os.path.join(SPECPATH, '..'))
app_dir = os.path.join(project, 'app')

hiddenimports = [
    'dropshift', 'dropshift.app', 'dropshift.menu',
    'win32com', 'win32com.client', 'pythoncom', 'pywintypes',
]

# Qt's plugins, named here rather than left to PyInstaller's Qt hook. The hook
# drops any plugin whose Qt libraries it finds by a second path - and a build
# run from inside a packaged app, such as the Claude desktop app, sees AppData
# by two paths. Grabbit lost every plugin that way once, qwindows.dll included,
# and the exe it made could not open a window.
import PySide6
qt_plugins = os.path.join(os.path.dirname(PySide6.__file__), 'plugins')
qt_plugin_binaries = [
    (os.path.join(qt_plugins, kind, name), os.path.join('PySide6', 'plugins', kind))
    for kind in ('platforms', 'imageformats', 'iconengines', 'styles', 'generic', 'multimedia')
    if os.path.isdir(os.path.join(qt_plugins, kind))
    for name in sorted(os.listdir(os.path.join(qt_plugins, kind)))
    # qpdf.dll needs Qt's PDF library, which is left out below; PDFs are pypdfium2's job.
    if name.lower().endswith('.dll') and name.lower() != 'qpdf.dll'
]
if not any(dest.endswith('platforms') and src.endswith('qwindows.dll') for src, dest in qt_plugin_binaries):
    raise SystemExit('Qt\'s Windows platform plugin (qwindows.dll) was not found - the exe could not start')

# The video editor's player: Qt's FFmpeg plugin, and the FFmpeg libraries it
# loads, which sit beside PySide6's own DLLs. (Not the FFmpeg in tools\ - that
# one is a separate program, and a different build.)
pyside = os.path.dirname(PySide6.__file__)
qt_ffmpeg = [(os.path.join(pyside, name), '.') for name in sorted(os.listdir(pyside))
             if name.lower().startswith(('avcodec', 'avformat', 'avutil', 'swresample', 'swscale'))
             and name.lower().endswith('.dll')]
if not qt_ffmpeg or not any('ffmpegmediaplugin' in src for src, _ in qt_plugin_binaries):
    raise SystemExit('Qt Multimedia (PySide6-Addons) is missing - the video editor could not play anything')
qt_plugin_binaries += qt_ffmpeg

a = Analysis(
    [os.path.join(app_dir, 'main.py')],
    pathex=[app_dir],
    binaries=qt_plugin_binaries,
    datas=[(os.path.join(app_dir, 'dropshift', 'ui', 'backdrop.jpg'), os.path.join('dropshift', 'ui'))],     # the window's faint photo
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=[
        'tkinter', 'unittest', 'pydoc_data', 'lib2to3', 'test',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.Qt3DCore', 'PySide6.QtDesigner', 'PySide6.QtHelp',
        'PySide6.QtTest', 'PySide6.QtSql', 'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets', 'PySide6.QtCharts',
        'PySide6.QtOpenGL', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Dropshift',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windowed by default; set DROPSHIFT_CONSOLE=1 to build a console variant
    # that prints tracebacks, which is the only way to debug startup failures.
    console=os.environ.get('DROPSHIFT_CONSOLE') == '1',
    disable_windowed_traceback=False,
    icon=os.path.join(SPECPATH, 'dropshift.ico'),
    version=os.path.join(SPECPATH, 'version_info.txt'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Dropshift',
)
