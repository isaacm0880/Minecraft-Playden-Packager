"""Public first-release messaging and local-only CLI boundaries."""
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from minecraft_playden.cli import main
from minecraft_playden.exporter import INSTRUCTIONS

EXPECTED = '''Import this package folder in Playden. On builds with game-folder recipe detection,
choose Use recipe when offered the included recipe.json. Accepting it replaces
the game's current settings; choose Skip to keep your custom settings.

If no recipe is offered, configure these settings manually:
Playden configuration:
  Program: java/bin/java.exe
  Launch arguments: @../../playden.args

Note: Online play limited to offline mode servers, authentication is not yet implemented'''


def test_completion_configuration_is_exact():
    assert INSTRUCTIONS == EXPECTED


@pytest.mark.parametrize('command', [[], ['build'], ['gui'], ['validate']])
def test_cli_help_is_local_only_without_unavailable_workflows(command):
    out = io.StringIO()
    with patch('sys.argv', ['packager', *command, '--help']), contextlib.redirect_stdout(out), pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    help_text = out.getvalue()
    for removed in ('--client-id', '--auth-method', 'microsoft', 'Import recipe', 'Web access'):
        assert removed not in help_text


@pytest.mark.parametrize('command', ['login', 'auth', 'download-client'])
def test_no_auth_or_client_download_commands(command):
    with patch('sys.argv', ['packager', command]), patch('urllib.request.urlopen') as network, contextlib.redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    network.assert_not_called()


def test_cli_missing_local_client_fails_before_network(tmp_path):
    output = tmp_path / 'output'
    err = io.StringIO()
    argv = ['packager', '--cache', str(tmp_path / 'cache'), 'build', '--source', 'local',
            '--client-jar', str(tmp_path / 'missing.jar'), '--version', '1.20.1', '--output', str(output)]
    with patch('sys.argv', argv), patch('urllib.request.urlopen') as network, contextlib.redirect_stderr(err):
        assert main() == 1
    assert 'missing' in err.getvalue().lower()
    network.assert_not_called()
    assert not output.exists()


def test_cli_success_and_validation_use_exact_configuration(tmp_path):
    output = tmp_path / 'package'
    out = io.StringIO()
    argv = ['packager', 'build', '--source', 'local', '--client-jar', str(tmp_path / 'client.jar'),
            '--version', '26.3', '--output', str(output)]
    with patch('sys.argv', argv), patch('minecraft_playden.cli.build'), contextlib.redirect_stdout(out):
        assert main() == 0
    assert out.getvalue() == f'Package validated: {output}\n{EXPECTED}\n'
    output.mkdir()
    (output / 'package.json').write_text('{"schema":2}')
    out = io.StringIO()
    with patch('sys.argv', ['packager', 'validate', str(output)]), patch('minecraft_playden.cli.validate', return_value=[]), contextlib.redirect_stdout(out):
        assert main() == 0
    assert out.getvalue() == 'Package files and portable launch arguments validated.\n' + EXPECTED + '\n'


def test_current_guides_have_exact_configuration_and_no_obsolete_workflows():
    root = Path(__file__).resolve().parents[1]
    guides = ['README.md', 'docs/INSTALLATION.md', 'docs/USAGE.md']
    for guide in guides:
        text = (root / guide).read_text(encoding="utf-8")
        manual = 'Playden configuration:' + EXPECTED.split('Playden configuration:', 1)[1]
        assert manual in text, guide
        assert 'recipe.json' in text and 'Skip' in text, guide
    for guide in [root / 'README.md', *sorted((root / 'docs').glob('*.md'))]:
        text = guide.read_text(encoding="utf-8").casefold()
        for removed in ('recipe → import', 'recipe > import', '--source microsoft',
                        '--client-id', '--auth-method', 'authentication.md'):
            assert removed not in text, (guide, removed)


def test_cli_missing_required_loader_artifact_fails_before_network(tmp_path):
    from test_fabric_launch import installed
    instance, _, _ = installed(tmp_path / 'source')
    next((tmp_path / 'source/libraries/org/ow2/asm').rglob('*.jar')).unlink()
    output = tmp_path / 'output'
    argv = ['packager', '--cache', str(tmp_path / 'cache'), 'build', '--source', 'local',
            '--instance', str(instance), '--output', str(output)]
    err = io.StringIO()
    with patch('sys.argv', argv), patch('urllib.request.urlopen') as network, contextlib.redirect_stderr(err):
        assert main() == 1
    network.assert_not_called()
    assert 'library missing' in err.getvalue()
    assert 'launcher' in err.getvalue()
    assert not output.exists()
