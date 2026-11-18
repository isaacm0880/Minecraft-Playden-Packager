"""Format-1 recipe at the game folder root, discovered by Playden on import.

See docs/PLAYDEN-RECIPES.md for the verified upstream contract and precedence.
"""
import json
import re
import unicodedata
from pathlib import Path
from .downloads import safe_path

PROGRAM = 'java/bin/java.exe'
ARGUMENTS = '@../../playden.args'
MAX_RECIPE_BYTES = 16 * 1024


def name_key(name: str) -> str:
    """Match Playden NameKey: NFKD, drop marks, lowercase, ASCII slug."""
    normalized = unicodedata.normalize('NFKD', name)
    normalized = ''.join(c for c in normalized if not unicodedata.category(c).startswith('M'))
    key = re.sub(r'[^a-z0-9]+', '-', normalized.lower()).strip('-')
    return key[:48].rstrip('-') or 'game'


def generate(name: str) -> dict:
    # IsGameName in Playden's library/game_ref.cpp counts Unicode code points.
    if (not isinstance(name, str) or not 1 <= len(name) <= 120
            or any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in name)
            or not any(unicodedata.category(c)[0] not in 'ZMC' for c in name)):
        raise ValueError('Game name must be 1-120 printable characters with visible content')
    key = name_key(name)
    return {
        'recipe_id': f'community-imported-{key}-playden',
        'recipe_version': 1,
        'game': {'store': 'imported', 'key': key, 'name': name},
        'launch_entries': [{
            'id': 'program',
            'label': 'java.exe',
            'executable': PROGRAM,
            'arguments': ARGUMENTS,
        }],
        'executable': PROGRAM,
        # Playden defaults to pe32; the downloaded Java runtime is Windows x64.
        'architecture': 'pe64',
        'graphics': 'opengl',
        # Both fields are required by ParseCommunityRecipe, even for local imports.
        'community': {'format_version': 1, 'status': 'untested'},
    }


def write(root: Path, name: str) -> None:
    text = json.dumps(generate(name), indent=2, ensure_ascii=False) + '\n'
    with safe_path(root, 'recipe.json').open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)


def validate(root: Path, name: str | None = None) -> list[str]:
    """Check our minimal configuration and paths, without executing Java.

    This intentionally validates packager output, not arbitrary Playden recipes.
    Package validation supplies the expected name from the package version.
    """
    errors = []
    try:
        with safe_path(root, 'recipe.json').open('rb') as stream:
            data = stream.read(MAX_RECIPE_BYTES + 1)
        if len(data) > MAX_RECIPE_BYTES:
            raise ValueError('recipe exceeds Playden\'s 16 KB import limit')
        document = json.loads(data.decode('utf-8'))
        expected = generate(name if name is not None else document['game']['name'])
        # JSON comparison also distinguishes true from 1 and 1.0 from 1.
        if json.dumps(document, sort_keys=True) != json.dumps(expected, sort_keys=True):
            errors.append('Recipe differs from the generated Playden configuration')
            return errors
        # No working_directory: LaunchWorkingFolder uses the executable's parent.
        executable = safe_path(root, document['launch_entries'][0]['executable'])
        argfile = safe_path(root, (executable.parent / ARGUMENTS[1:]).relative_to(root).as_posix())
        if not executable.is_file():
            errors.append(f'Recipe executable is missing: {PROGRAM}')
        if not argfile.is_file():
            errors.append('Recipe argument file is missing: playden.args')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f'Invalid Playden recipe: {exc}')
    return errors
