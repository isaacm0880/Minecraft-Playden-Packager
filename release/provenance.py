"""Fail closed on stale CLI revisions, dirty release sources or missing features."""
import hashlib
import json
import re
from pathlib import Path
import subprocess

# Public repository bootstrap; no development-repository ancestry is required.
BASELINE = 'f9529f532f377514d4f05ba29bac55d2c23f0ae1'
REQUIRED = {
    'minecraft_playden/gui_app.py': ('QSplitter(', 'setSortingEnabled(True)', 'layout/instanceSplitter', 'table/sortColumn', 'start_usb_queue', 'offlineName'),
    'minecraft_playden/usb_queue.py': ('transfer_candidates', 'plan_transfers', 'usb_summary'),
    'minecraft_playden/compatibility.py': ('FORGE_BROKEN', 'Status.TESTED'),
    'minecraft_playden/privacy.py': ('def cleanup(',),
    'minecraft_playden/cli.py': ("add_parser('gui'",),
    'minecraft_playden/transfer.py': ('sha256', 'CancellationToken', 'playden/import'),
    'minecraft_playden/drive_discovery.py': ('windows_external',),
    'minecraft_playden/formatting.py': ('GB',),
    'pyproject.toml': ('PySide6', 'minecraft-playden-gui'),
    'docs/INSTALLATION.md': ('minecraft_playden gui',),
    'release/desktop.spec': ('console=False',),
    '.github/workflows/desktop-build.yml': ('workflow_dispatch',),
    'CHANGELOG.md': ('0.1.0',),
}



def candidate_source(event: str, ref: str, workflow_sha: str, requested: str) -> str:
    """Select an exact public commit; never fall back from an invalid manual input."""
    if event == 'push':
        if ref != 'refs/heads/release/v0.1.0-prep':
            raise ValueError('Automatic candidates are restricted to the preparation branch')
        if requested:
            raise ValueError('Push candidates use the pushed commit, not a supplied revision')
        selected = workflow_sha
    elif event == 'workflow_dispatch':
        selected = requested
    else:
        raise ValueError('Unsupported candidate workflow event')
    if not re.fullmatch('[0-9a-f]{40}', selected):
        raise ValueError('Supply an exact lowercase 40-character public commit SHA')
    if selected != workflow_sha:
        raise ValueError('Source SHA must equal this workflow run commit; select its branch/ref')
    return selected


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def application_hash(root: Path) -> str:
    """Hash the complete reviewed application, independent of private history."""
    result = hashlib.sha256()
    for path in sorted((root / 'minecraft_playden').rglob('*')):
        if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
            continue
        if path.is_symlink():
            raise ValueError('Linked application source is not permitted')
        if path.is_file():
            name = path.relative_to(root).as_posix()
            result.update(name.encode() + b'\0' + path.read_bytes() + b'\0')
    return result.hexdigest()


def check_source(root: Path, expected: str | None = None, preview: bool = False) -> dict:
    sha = git(root, 'rev-parse', 'HEAD')
    if expected is not None and (len(expected) != 40 or expected != sha):
        raise ValueError('Checkout does not match the required exact source commit')
    subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', BASELINE, sha], check=True)
    for required_path, markers in REQUIRED.items():
        text = (root / required_path).read_text(encoding='utf-8')
        if not all(marker in text for marker in markers):
            raise ValueError(f'Complete desktop implementation missing: {required_path}')
    policy = json.loads((root / 'release/source-policy.json').read_text(encoding='utf-8'))
    if policy.get('schema') != 1 or application_hash(root) != policy.get('application_tree_sha256'):
        raise ValueError('Application differs from the reviewed GUI snapshot; explicit source review is required')
    dirty = bool(git(root, 'status', '--porcelain', '--untracked-files=all'))
    if dirty and not preview:
        raise ValueError('Release builds require a clean committed checkout; use --allow-dirty-preview only for local previews')
    names = git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split('\0')
    digest = hashlib.sha256()
    manifest = {}
    for name in sorted(set(filter(None, names))):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Source snapshot contains missing/linked input: {name}')
        if not (name.startswith(('minecraft_playden/', 'release/', 'tests/', 'docs/', '.github/workflows/', 'tools/'))
                or name in ('pyproject.toml', 'LICENSE', '.gitignore', '.gitattributes', 'CHANGELOG.md', 'THIRD_PARTY.md', 'RELEASE_NOTES.md') or name.startswith('README')):
            continue
        data = path.read_bytes()
        manifest[name] = hashlib.sha256(data).hexdigest()
        digest.update(name.encode() + b'\0' + data + b'\0')
    return {'source_commit': sha, 'source_dirty': dirty, 'preview': preview,
            'source_tree_sha256': digest.hexdigest(), 'source_files': manifest,
            'reviewed_gui_snapshot': policy['reviewed_gui_snapshot'],
            'source_commit_time': int(git(root, 'show', '-s', '--format=%ct', sha)),
            'source_branch': git(root, 'branch', '--show-current') or 'detached'}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
