r"""Publish a release of this suite app on GitHub, where Hanami installs and
updates it from.

    build\release.ps1 --notes "What's new"     build, sign and publish the app's __version__
    build\release.ps1 --notes-file notes.md
    build\release.ps1 --no-build               use the build already in dist\
    build\release.ps1 --dry-run                everything except changing anything on GitHub
    build\release.ps1 --make-key               once per app, before its first release

Needs GitHub's command-line tool, signed in (winget install GitHub.cli, then
gh auth login), and the build venv from build\bootstrap.ps1.

build\release.json says which app this is and where its releases go:

    {"app": "Keytap", "repo": "blossqt/keytap", "public_key": "<hex>"}

What it does:

  1. Checks the work is committed and pushed, and that the version in
     app\<app>\__init__.py is newer than the latest release.
  2. Builds the app (build_app.ps1, which ends by starting the exe with
     --self-test), and checks the exe carries that version and says so.
  3. Zips dist\<App> as <App>-<version>-win64.zip, holding one folder, <App>\.
  4. Writes latest.json - the version, and the zip's name, size and SHA-256 -
     signs it with the app's key, and checks the signature verifies.
  5. Shows what it will publish, asks (unless --yes), and creates the release:
     tag v<version> on this commit, with the zip, its .sha256, latest.json and
     latest.json.sig.

The signing key is ~\.<app>\release\update-signing.key. BACK IT UP. Hanami
installs and updates only releases signed with it: lose it and no copy can be
updated again until every Hanami carries a new key.

This file is Hanami's build\release.py, copied into each suite app's build\
with ed25519.py beside it; Hanami's tests\check_copies.py keeps them the same.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = ROOT / 'dist'
CONFIG = HERE / 'release.json'
sys.path.insert(0, str(HERE))

import ed25519  # noqa: E402

SCHEMA = 1
CREATE_NO_WINDOW = 0x08000000


def say(message: str) -> None:
    print(f'>> {message}', flush=True)


def fail(message: str):
    print(f'\nERROR: {message}', file=sys.stderr, flush=True)
    raise SystemExit(1)


def run(*args, capture: bool = True, check: bool = True, cwd: Path = ROOT):
    result = subprocess.run([str(a) for a in args], cwd=cwd, text=True, capture_output=capture,
                            encoding='utf-8', errors='replace')
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip() if capture else ''
        fail(f'{Path(str(args[0])).name} {" ".join(str(a) for a in args[1:3])} failed'
             + (f':\n{detail}' if detail else ''))
    return result


# ------------------------------------------------------------------- the app

def load_config() -> dict:
    try:
        config = json.loads(CONFIG.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        fail(f'{CONFIG} is missing or unreadable: {error}')
    for key in ('app', 'repo'):
        if not config.get(key):
            fail(f'{CONFIG.name} has no "{key}"')
    return config


def key_file(config: dict) -> Path:
    return Path.home() / f'.{config["app"].lower().replace(" ", "-")}' / 'release' / 'update-signing.key'


def package_dir(config: dict) -> Path:
    return ROOT / 'app' / (config.get('package') or config['app'].lower())


def project_version(config: dict) -> str:
    text = (package_dir(config) / '__init__.py').read_text(encoding='utf-8')
    match = re.search(r'''^__version__\s*=\s*['"](\d+\.\d+\.\d+)['"]''', text, re.M)
    if not match:
        fail(f'no __version__ = "X.Y.Z" in {package_dir(config) / "__init__.py"}')
    return match.group(1)


def parse_version(text) -> tuple | None:
    match = re.match(r'^v?(\d+)\.(\d+)\.(\d+)$', str(text or '').strip())
    return tuple(int(part) for part in match.groups()) if match else None


# -------------------------------------------------------------------- the key

def make_key(config: dict) -> int:
    path = key_file(config)
    if path.exists() or config.get('public_key'):
        fail('this app already has a release key. Replacing it would leave every Hanami refusing '
             'its releases until Hanami carries the new key.')
    secret = ed25519.generate_secret()
    public = ed25519.public_key(secret).hex()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret.hex() + '\n', encoding='ascii')
    config['public_key'] = public
    CONFIG.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    print(f'made {path}')
    print(f'wrote its public half into build\\{CONFIG.name}: {public}')
    print(f'\nNext: put that public key in Hanami\'s app\\hanami\\catalog.py, in {config["app"]}\'s entry.')
    print('BACK UP the key file. Without it no copy can be updated again.')
    return 0


def load_key(config: dict) -> str:
    path = key_file(config)
    if not config.get('public_key'):
        fail('there is no release key yet: run release.ps1 --make-key once')
    if not path.exists():
        fail(f'the release key is missing: {path}\n\nHanami installs only releases signed with it. '
             'Restore it from your backup.')
    secret = path.read_text(encoding='ascii').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}', secret):
        fail(f'{path} is not a release key')
    if ed25519.public_key(bytes.fromhex(secret)).hex() != config['public_key']:
        fail(f'{path} does not match the public key in build\\{CONFIG.name}. Restore the right one '
             'from your backup.')
    return secret


# ----------------------------------------------------------------- GitHub

GH = ''


def find_gh() -> str:
    found = shutil.which('gh') or next(
        (str(p) for p in (Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'GitHub CLI' / 'gh.exe',
                          Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'GitHub CLI' / 'gh.exe')
         if p.is_file()), None)
    if not found:
        fail("GitHub's command-line tool is not installed: winget install GitHub.cli, then gh auth login")
    if subprocess.run([found, 'auth', 'status'], capture_output=True).returncode != 0:
        fail('gh is not signed in: run gh auth login')
    return found


def latest_published(repo: str) -> str | None:
    result = run(GH, 'release', 'view', '--repo', repo, '--json', 'tagName', '-q', '.tagName', check=False)
    return result.stdout.strip().lstrip('v') if result.returncode == 0 and result.stdout.strip() else None


def require_shared_files_current() -> None:
    """The suite's shared files in this app (ui\\suitetheme.py, suitelink.py and the rest) must be
    Hanami's originals: a release with a stale copy ships an app that looks or links differently
    from the rest of the suite - it happened once, the glass's rim fixes left out of three apps.
    Hanami's tests\\check_copies.py says which differ; for Hanami itself, a warning will do."""
    checker = Path.home() / 'Documents' / 'Hanami' / 'tests' / 'check_copies.py'
    if not checker.is_file():
        return
    result = subprocess.run([sys.executable, str(checker)], text=True, capture_output=True,
                            encoding='utf-8', errors='replace')
    stale = [line.strip() for line in result.stdout.splitlines() if line.startswith(('DIFFERS', 'MISSING'))]
    if ROOT.name.lower() == 'hanami':
        for line in stale:
            print(f'   warning: {line} - copy it over before releasing that app', flush=True)
        return
    mine = [line for line in stale if str(ROOT).lower() in line.lower()]
    if mine:
        fail("shared files here differ from Hanami's originals - run Hanami's tests\\check_copies.py "
             '--sync, commit, then release:\n   ' + '\n   '.join(mine))


def require_clean_and_pushed() -> str:
    if run('git', 'status', '--porcelain').stdout.strip():
        fail('there are uncommitted changes - commit them first, so the release is exactly a '
             'commit anyone can check out')
    branch = run('git', 'rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    run('git', 'fetch', '--quiet', 'origin', branch)
    head = run('git', 'rev-parse', 'HEAD').stdout.strip()
    if head != run('git', 'rev-parse', f'origin/{branch}').stdout.strip():
        fail(f'this commit is not what origin/{branch} points at - push it (or pull) first')
    return head


# ------------------------------------------------------------------ the build

class _FixedFileInfo(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        'signature', 'struct_version', 'file_ms', 'file_ls', 'product_ms', 'product_ls',
        'flags_mask', 'flags', 'os', 'type', 'subtype', 'date_ms', 'date_ls')]


def exe_version(path: Path) -> str:
    api = ctypes.windll.version
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, wintypes.LPDWORD]
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = api.GetFileVersionInfoSizeW(str(path), None)
    buffer = ctypes.create_string_buffer(size or 1)
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not size or not api.GetFileVersionInfoW(str(path), 0, size, buffer) or \
            not api.VerQueryValueW(buffer, '\\', ctypes.byref(pointer), ctypes.byref(length)):
        return ''
    info = ctypes.cast(pointer, ctypes.POINTER(_FixedFileInfo)).contents
    return f'{info.file_ms >> 16}.{info.file_ms & 0xFFFF}.{info.file_ls >> 16}'


def self_test(exe: Path) -> str:
    answer = Path(tempfile.gettempdir()) / f'release-self-test-{os.getpid()}.txt'
    answer.unlink(missing_ok=True)
    try:
        subprocess.run([str(exe), '--self-test', str(answer)], creationflags=CREATE_NO_WINDOW, timeout=180)
        return answer.read_text(encoding='utf-8').strip() if answer.exists() else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''
    finally:
        answer.unlink(missing_ok=True)


def build(config: dict, version: str, head: str, rebuild: bool) -> Path:
    app = config['app']
    folder = DIST / app
    exe = folder / f'{app}.exe'
    if rebuild:
        say(f'building {app}')
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(HERE / 'build_app.ps1')], cwd=ROOT)
        if result.returncode != 0:
            fail('the build failed - see above')
    if not exe.is_file():
        fail(f'{exe} is missing - build it, or leave out --no-build')
    stamped = exe_version(exe)
    if stamped != version:
        fail(f'{exe.name} carries version {stamped or "none"}, not {version}: '
             'keep build\\version_info.txt in step with __init__.py')
    said = self_test(exe)
    if said.split(' ', 1)[0] != version:
        fail(f'{exe.name} --self-test answered {said!r}, not {version}')
    say(f'{exe.name} is {version} and starts')
    (folder / 'build-commit.txt').write_text(head + '\n', encoding='ascii')

    archive = DIST / f'{app}-{version}-win64.zip'
    part = archive.with_name(archive.name + '.part')
    say(f'zipping {archive.name}')
    with zipfile.ZipFile(part, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                zf.write(path, f'{app}/{path.relative_to(folder).as_posix()}')
    with zipfile.ZipFile(part) as zf:
        if zf.testzip() is not None:
            fail('the zip came out damaged')
    os.replace(part, archive)
    return archive


def digest(path: Path) -> tuple[int, str]:
    sha, size = hashlib.sha256(), 0
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            sha.update(chunk)
            size += len(chunk)
    return size, sha.hexdigest()


def sign(config: dict, version: str, notes: str, archive: Path, secret: str) -> list[Path]:
    """latest.json and its signature - checked the way Hanami checks them."""
    size, sha = digest(archive)
    body = {'schema': SCHEMA, 'app': config['app'], 'version': version,
            'published_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'notes': notes,
            'desktop': {'file': archive.name, 'size': size, 'sha256': sha}, 'android': None}
    manifest = (json.dumps(body, indent=2) + '\n').encode('utf-8')
    signature = base64.b64encode(ed25519.sign(bytes.fromhex(secret), manifest))
    if not ed25519.verify(bytes.fromhex(config['public_key']), manifest, base64.b64decode(signature)):
        fail('the signed manifest does not verify against the public key')
    manifest_path, signature_path = DIST / 'latest.json', DIST / 'latest.json.sig'
    manifest_path.write_bytes(manifest)
    signature_path.write_bytes(signature + b'\n')
    checksum = archive.with_name(archive.name + '.sha256')
    checksum.write_text(f'{sha}  {archive.name}\n', encoding='ascii')
    return [archive, checksum, manifest_path, signature_path]


# --------------------------------------------------------------------- main

def main() -> int:
    global GH
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--make-key', action='store_true', help='make this app\'s release key (once)')
    parser.add_argument('--notes', help='release notes')
    parser.add_argument('--notes-file', type=Path, help='release notes, from a file')
    parser.add_argument('--no-build', action='store_true', help='use the build already in dist\\')
    parser.add_argument('--dry-run', action='store_true', help='change nothing on GitHub')
    parser.add_argument('--yes', action='store_true', help='publish without asking')
    args = parser.parse_args()
    config = load_config()
    if args.make_key:
        return make_key(config)

    secret = load_key(config)
    version = project_version(config)
    notes = args.notes_file.read_text(encoding='utf-8') if args.notes_file else (args.notes or '')
    if not notes.strip() and not args.dry_run:
        fail('say what is new: --notes "..." or --notes-file notes.md')
    GH = find_gh()
    require_shared_files_current()
    head = require_clean_and_pushed()
    previous = latest_published(config['repo'])
    if previous and not (parse_version(version) or ()) > (parse_version(previous) or ()):
        fail(f'{version} is not newer than the latest release, {previous}: raise __version__ '
             '(and build\\version_info.txt) first')
    say(f'{config["app"]} {version}, commit {head[:10]}, to github.com/{config["repo"]}'
        + (f' (latest: {previous})' if previous else ' (its first release)'))

    archive = build(config, version, head, rebuild=not args.no_build)
    files = sign(config, version, notes.strip(), archive, secret)
    for path in files:
        print(f'   {path.name}  ({path.stat().st_size / 1e6:.1f} MB)' if path.stat().st_size > 1e6
              else f'   {path.name}')
    if args.dry_run:
        say('dry run: nothing published')
        return 0
    if not args.yes:
        if input(f'\nPublish {config["app"]} {version}? [y/N] ').strip().lower() not in ('y', 'yes'):
            say('not published')
            return 1
    tag = f'v{version}'
    run(GH, 'release', 'create', tag, '--repo', config['repo'], '--target', head,
        '--title', f'{config["app"]} {version}', '--notes', notes.strip(), *files, capture=False)
    say(f'published: https://github.com/{config["repo"]}/releases/tag/{tag}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
