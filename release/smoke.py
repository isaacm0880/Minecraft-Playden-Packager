"""Run the frozen GUI against an isolated home; no Python/project imports in the child."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> int:
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='playden-frozen-smoke-') as temporary:
        root = Path(temporary)
        env = {key: value for key, value in os.environ.items()
               if key not in ('PYTHONPATH', 'PYTHONHOME', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH')}
        env.update(HOME=str(root), USERPROFILE=str(root), LOCALAPPDATA=str(root / 'local'),
                   APPDATA=str(root / 'roaming'), XDG_CONFIG_HOME=str(root / 'config'),
                   XDG_STATE_HOME=str(root / 'state'), XDG_CACHE_HOME=str(root / 'cache'))
        native_cocoa = '--native-cocoa' in sys.argv
        if native_cocoa and sys.platform != 'darwin':
            raise ValueError('Cocoa smoke requires a native macOS host')
        for style in (('Fusion',) if native_cocoa else ('Fusion', 'Windows')):
            subprocess.run([str(executable), '--smoke-test'], cwd=root, env={**env, 'QT_QPA_PLATFORM': 'cocoa' if native_cocoa else 'offscreen',
                           'QT_STYLE_OVERRIDE': style}, check=True, timeout=45, capture_output=True)
        result = root / 'build-info.json'
        subprocess.run([str(executable), '--build-info', '--output', str(result)], cwd=root,
                       env=env, check=True, timeout=20, capture_output=True)
        info = json.loads(result.read_text())
        logs = list(root.rglob('desktop.log'))
        if not logs or not any('FROZEN_SMOKE_OK' in p.read_text() for p in logs):
            raise ValueError('Frozen application did not record successful GUI smoke checks')
        print('Frozen GUI smoke passed:', info['source_commit'], 'preview=', info['preview'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
