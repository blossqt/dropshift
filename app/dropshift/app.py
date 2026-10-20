"""Dropshift's start-up, its tray icon, and the wiring between the drag watcher,
the drop menu and the jobs that do the work."""

from __future__ import annotations

import ctypes
import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QCursor
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog

from . import APP_NAME, __version__, catalog
from .catalog import Op
from .engines import media
from .jobs import Job, JobRunner
from .menu import DropMenu
from .settings import Settings
from .suitetray import SuiteTray
from .ui import icons, theme
from .ui.dialogs import SettingsDialog, WelcomeDialog
from .ui.floater import FloatShelf
from .ui.info_window import InfoWindow
from .ui.toast import ToastStack
from .ui.media_editor import MediaEditor
from .watcher import DragWatcher

# Tests run a second copy beside a real one under a name of their own.
SERVER_NAME = os.environ.get('DROPSHIFT_INSTANCE') or f'dropshift-{os.environ.get("USERNAME", "user")}'
APP_ID = 'Suite.Dropshift'


def _name(paths: list[Path]) -> str:
    return paths[0].name if len(paths) == 1 else f'{len(paths)} files'


class Controller(QObject):
    def __init__(self):
        super().__init__()
        self.settings = Settings()
        keys = self.settings.keys
        self.watcher = DragWatcher(keys, self)
        self.menu = DropMenu(keys)
        self.runner = JobRunner(self)
        self.toasts = ToastStack()
        self.shelves: list[FloatShelf] = []
        self.windows: list = []
        self.paused = False
        self._dialog = None

        self.watcher.triggered.connect(self._triggered)
        self.watcher.moved.connect(self.menu.track)
        self.watcher.released.connect(self.menu.released)
        self.watcher.escaped.connect(self.menu.dismiss)
        self.menu.chosen.connect(self.perform)

        self._make_tray()
        self._listen()
        self.watcher.start()
        if not self.settings.welcomed and os.environ.get('DROPSHIFT_DEV') != '1':
            self.settings.welcomed = True
            self.show_welcome()

    # ------------------------------------------------------------------ drag to menu

    def _triggered(self, mode: str) -> None:
        if not self.paused:
            self.menu.open(mode, QCursor.pos())

    def perform(self, op: Op, paths: list[Path]) -> None:
        taken, skipped = catalog.split(op, paths)
        if not taken:
            self.toasts.show(op.label, 'Nothing here it can work on').finish(op.label, 'Nothing here it can work on')
            return
        windows = {'actions.float': self._float, 'tools.info': self._show_info,
                   'tools.edit': self._edit_media}
        if op.id in windows:
            windows[op.id](taken)
            return
        if op.gui:
            self._run_now(op, taken)
            return

        destination = None
        if op.ask_folder:
            folder = QFileDialog.getExistingDirectory(None, op.ask_folder,
                                                      self.settings.last_folder or str(taken[0].parent))
            if not folder:
                return
            self.settings.last_folder = folder
            destination = Path(folder)

        if op.mode == 'convert':
            title = f'Converting {_name(taken)} to {op.label}'
        else:
            title = f'{op.verb} {_name(taken)}'
        self.start_job(op, taken, skipped, title, destination)

    def start_job(self, op: Op, files: list[Path], skipped: list[Path], title: str,
                  destination: Path | None = None) -> None:
        job = Job(op, files, skipped, self.settings.output_folder, destination)
        toast = self.toasts.show(title, 'Starting…')
        self.runner.start(job, toast.set_progress, lambda job, error: self._finished(job, error, toast))

    def _run_now(self, op: Op, paths: list[Path]) -> None:
        try:
            op.run(paths)
        except Exception as error:  # noqa: BLE001
            traceback.print_exc()
            self.toasts.show(op.label).finish(f'{op.label} failed', str(error), error=True)
            return
        done = {'actions.copy': f'Copied {_name(paths)} - paste it into any folder',
                'actions.copy_path': 'Copied the path' if len(paths) == 1 else f'Copied {len(paths)} paths'}
        if op.id in done:
            self.toasts.show(done[op.id]).finish(done[op.id])

    def _finished(self, job: Job, error: str, toast) -> None:
        op = job.op
        if error:
            toast.finish(f'{op.label} failed', error, error=True)
            return
        outputs = job.outputs
        notes = list(job.notes)
        if job.skipped:
            notes.append(f'{len(job.skipped)} skipped - {op.label} does not suit '
                         f'{"it" if len(job.skipped) == 1 else "them"}')
        if op.id in ('actions.move_to', 'actions.copy_to'):
            verb = 'Moved' if op.id == 'actions.move_to' else 'Copied'
            title = f'{verb} {_name(outputs)}' if outputs else 'Nothing was moved'
            notes.insert(0, f'to {job.destination}')
        elif not outputs:
            title = 'Nothing new was made'
        elif len(outputs) == 1:
            title = f'Saved {outputs[0].name}'
        else:
            title = f'Saved {len(outputs)} files'
        toast.finish(title, ' · '.join(notes), outputs)

    def _keep(self, window) -> None:
        # Held here until closed, or Qt's window would vanish with its last reference.
        self.windows.append(window)
        window.closed.connect(lambda w: self.windows.remove(w) if w in self.windows else None)
        window.show()
        window.raise_()
        window.activateWindow()

    def _show_info(self, paths: list[Path]) -> None:
        strip = catalog.BY_ID['tools.strip']
        self._keep(InfoWindow(paths[:50], lambda files: self.perform(strip, files)))

    def _edit_media(self, paths: list[Path]) -> None:
        for path in paths[:4]:
            self._keep(MediaEditor(path, self._export_media))

    def _export_media(self, path: Path, edit) -> None:
        op = Op(id='tools.edit.export', label='Export', mode='tools', kinds=frozenset({'video', 'audio'}),
                run=lambda job: media.edit_media(job, job.files[0], edit), verb='Exporting')
        self.start_job(op, [path], [], f'Exporting {path.name}')

    def _float(self, paths: list[Path]) -> None:
        shelf = FloatShelf(paths)
        area = self.menu.screen().availableGeometry() if self.menu.screen() else \
            QApplication.primaryScreen().availableGeometry()
        offset = 30 * len(self.shelves)
        shelf.move(area.right() - shelf.width() - 20 - offset, area.top() + 60 + offset)
        shelf.closed.connect(lambda s: self.shelves.remove(s) if s in self.shelves else None)
        self.shelves.append(shelf)
        shelf.show()

    # ------------------------------------------------------------------ tray and windows

    def _make_tray(self) -> None:
        # In Hanami's cherry-blossom icon while Hanami runs, else an icon of its own.
        self.tray = SuiteTray(APP_NAME, icons.app_icon(), self)
        self.tray.clicked.connect(self._tray_clicked)
        self.tray.set_shown(self.settings.tray_shown)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        if self.paused:
            tip = 'Dropshift - paused'
        else:
            keys = [k for k in self.settings.keys.values() if k != 'off']
            names = ', '.join(k.capitalize() if k != 'ctrl' else 'Ctrl' for k in keys)
            tip = f'Dropshift - drag a file, then press {names}'
        self.tray.set_menu([
            {'id': 'settings', 'text': 'Settings…', 'default': True},
            {'id': 'welcome', 'text': 'How it works'},
            {'id': 'pause', 'text': 'Pause', 'checkable': True, 'checked': self.paused},
            {'separator': True},
            {'id': 'quit', 'text': 'Quit Dropshift'},
        ], tip)

    def _tray_clicked(self, item: str) -> None:
        actions = {'settings': self.show_settings, 'welcome': self.show_welcome,
                   'pause': lambda: self.set_paused(not self.paused), 'quit': self.quit}
        if item in actions:
            actions[item]()

    def set_tray_shown(self, shown: bool) -> None:
        self.settings.tray_shown = shown
        self.tray.set_shown(shown)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused:
            self.menu.dismiss()
            self.watcher.stop()
        else:
            self.watcher.start()
        self._update_tooltip()

    def show_settings(self) -> None:
        if self._dialog and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        self._dialog = SettingsDialog(self.settings, self)
        self._dialog.accepted.connect(self._settings_changed)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def _settings_changed(self) -> None:
        keys = self.settings.keys
        self.watcher.set_keys(keys)
        self.menu.keys = keys
        self._update_tooltip()

    def show_welcome(self) -> None:
        dialog = WelcomeDialog(self.settings.keys)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._welcome = dialog

    def quit(self) -> None:
        self.watcher.stop()
        self.tray.stop()
        QApplication.quit()

    # ------------------------------------------------------------------ one copy at a time

    def _listen(self) -> None:
        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._connection)
        self._server.listen(SERVER_NAME)

    def _connection(self) -> None:
        socket = self._server.nextPendingConnection()
        socket.waitForReadyRead(500)
        message = bytes(socket.readAll()).decode('utf-8', 'replace').strip()
        reply = self.command(message)
        if reply:
            socket.write(reply.encode('utf-8'))
            socket.waitForBytesWritten(500)
        socket.disconnectFromServer()

    def command(self, message: str) -> str:
        words = message.split()
        if not words or words[0] == 'show':
            # Opening Dropshift again - the way back in when it is out of the tray.
            self.show_settings()
        elif words[0] == 'quit':
            self.quit()
        elif words[0] == 'pid' and os.environ.get('DROPSHIFT_DEV') == '1':
            return str(os.getpid())
        elif words[0] == 'paused' and os.environ.get('DROPSHIFT_DEV') == '1':
            return str(self.paused)
        elif words[0] == 'perform' and os.environ.get('DROPSHIFT_DEV') == '1':
            # Development only: as if a file had been dropped on a choice.
            op_id, _, path = message.split(' ', 1)[1].partition(' ')
            self.perform(catalog.BY_ID[op_id], [Path(path)])
        elif words[0] == 'windows' and os.environ.get('DROPSHIFT_DEV') == '1':
            return '; '.join(self._describe_window(w) for w in self.windows) or 'none'
        elif words[0] == 'rect' and os.environ.get('DROPSHIFT_DEV') == '1':
            import win32gui
            return ' '.join(map(str, win32gui.GetWindowRect(int(self.menu.winId()))))
        elif words[0] == 'where' and os.environ.get('DROPSHIFT_DEV') == '1':
            # Development only (tests/drag_test.py): where a choice in the
            # open menu is, in screen pixels.
            point = self.menu.screen_point_of(words[1])
            return f'{point[0]} {point[1]}' if point else 'none'
        return ''


    @staticmethod
    def _describe_window(window) -> str:
        return window.describe() if isinstance(window, MediaEditor) else type(window).__name__


def send(message: str) -> bool:
    """Hand a message to the copy of Dropshift already running, if there is one."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(400):
        return False
    socket.write(message.encode('utf-8'))
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()
    return True


def _log_to_file() -> None:
    # A windowed exe (or pythonw) has nowhere to print tracebacks; keep them.
    if sys.stderr is not None:
        return
    folder = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Dropshift'
    folder.mkdir(parents=True, exist_ok=True)
    log = open(folder / 'dropshift.log', 'a', encoding='utf-8', buffering=1)  # noqa: SIM115
    sys.stdout = sys.stderr = log


def self_test(argv: list[str]) -> int:
    """Load what a real start loads and answer with the version - the build's check."""
    target = argv[argv.index('--self-test') + 1] if len(argv) > argv.index('--self-test') + 1 else ''
    app = QApplication(argv[:1])
    theme.install(app)
    icons.render_app_icon(32)
    DropMenu({'convert': 'shift', 'tools': 'ctrl', 'actions': 'alt'})
    assert catalog.OPS
    # The video editor's player: no decodable formats means its plugin didn't load.
    from PySide6.QtMultimedia import QMediaFormat
    if not QMediaFormat().supportedFileFormats(QMediaFormat.Decode):
        return 1
    answer = __version__
    if target:
        Path(target).write_text(answer, encoding='utf-8')
    else:
        print(answer)
    app.quit()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _log_to_file()
    # Qt's video player narrates every file it opens; keep the log for our own problems.
    os.environ.setdefault('QT_LOGGING_RULES', 'qt.multimedia*=false')
    if '--self-test' in argv:
        return self_test(argv)
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass

    app = QApplication(argv[:1])
    theme.install(app)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(icons.app_icon())

    message = argv[argv.index('--send') + 1] if '--send' in argv and len(argv) > argv.index('--send') + 1 else 'show'
    if send(message):
        return 0
    if '--send' in argv:
        print('Dropshift is not running', file=sys.stderr)
        return 1

    controller = Controller()  # noqa: F841 - lives as long as the app
    app.styleHints().colorSchemeChanged.connect(lambda *_: icons.clear_cache())
    return app.exec()
