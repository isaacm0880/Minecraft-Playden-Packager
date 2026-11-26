"""Run the actual frozen startup path against the current GUI in isolation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    import PySide6  # noqa: F401
except ImportError:
    PySide6 = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(PySide6 is None, 'Optional PySide6 is not installed')
class FrozenStartupTests(unittest.TestCase):
    def test_real_smoke_entrypoint_matches_current_gui_progress(self):
        script = """
from unittest.mock import patch
from types import SimpleNamespace
from minecraft_playden import packaged_app
from minecraft_playden import receiver_manager
# The release environment supplies certifi; this smoke makes no TLS requests.
with patch.dict('sys.modules', {'certifi': SimpleNamespace(where=lambda: 'unused-smoke-ca.pem')}), \
     patch.object(packaged_app, 'metadata', return_value={'version': '0.1.0'}), \
     patch.object(receiver_manager, 'ensure_receiver_elf', side_effect=AssertionError('source-mode smoke must not inspect a receiver ELF')):
    raise SystemExit(packaged_app.main())
"""
        for style in ('Fusion', 'Windows'):
            with self.subTest(style=style), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                env = {**os.environ, 'HOME': str(root), 'USERPROFILE': str(root),
                       'LOCALAPPDATA': str(root / 'local'), 'APPDATA': str(root / 'roaming'),
                       'XDG_CONFIG_HOME': str(root / 'config'), 'XDG_STATE_HOME': str(root / 'state'),
                       'XDG_CACHE_HOME': str(root / 'cache'), 'QT_QPA_PLATFORM': 'offscreen',
                       'QT_STYLE_OVERRIDE': style}
                result = subprocess.run([sys.executable, '-c', script, '--smoke-test'],
                                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
                logs = '\n'.join(path.read_text() for path in root.rglob('desktop.log'))
                self.assertEqual(result.returncode, 0, result.stderr + logs)
                self.assertIn('FROZEN_SMOKE_OK', logs)
                self.assertNotIn('FROZEN_SMOKE_FAILED', logs)
