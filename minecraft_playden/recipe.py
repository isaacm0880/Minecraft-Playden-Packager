"""Format-1 recipe in the game's playden metadata directory, discovered on import.

See docs/PLAYDEN-RECIPES.md for the verified upstream contract and precedence.
"""
import json
import re
import stat
import unicodedata
from pathlib import Path
from .downloads import safe_path

PATH = 'playden/recipe.json'
LEGACY_PATH = 'recipe.json'
PROGRAM = 'java/bin/java.exe'
ARGUMENTS = '@../../playden.args'
MAX_RECIPE_BYTES = 16 * 1024

# Playden controller_layout / prospero-win input-map grammar, not launch args.
# Stick key order is up, down, left, right.
CONTROLLER_LAYOUT = ';'.join([
    'cross = key:space', 'circle = key:q', 'square = key:e', 'triangle = key:f',
    'l1 = mouse:wheel_up', 'r1 = mouse:wheel_down',
    'l2 = mouse:right', 'r2 = mouse:left', 'l3 = key:lctrl', 'r3 = key:lshift',
    'up = key:l', 'down = key:f3', 'left = key:t', 'right = mouse:middle',
    'options = key:escape', 'create = key:f2',
    'touchpad_left = key:tab', 'touchpad_right = key:f5',
    'left_stick = keys', 'left_stick_keys = w,s,a,d', 'right_stick = mouse',
])


def location(root: Path) -> str:
    """Prefer the new format, with an unambiguous, no-symlink legacy fallback."""
    folder = root / 'playden'
    current, legacy = root / PATH, root / LEGACY_PATH
    try:
        info = folder.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                             or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
        raise ValueError('Playden metadata directory must be a real directory without links')
    if current.is_symlink() or legacy.is_symlink():
        raise ValueError('Playden recipe symlinks are unsupported')
    if current.exists() and legacy.exists():
        raise ValueError('Ambiguous package: both current and legacy recipes are present')
    return PATH if current.exists() else LEGACY_PATH


def name_key(name: str) -> str:
    """Match Playden NameKey: NFKD, drop marks, lowercase, ASCII slug."""
    normalized = unicodedata.normalize('NFKD', name)
    normalized = ''.join(c for c in normalized if not unicodedata.category(c).startswith('M'))
    key = re.sub(r'[^a-z0-9]+', '-', normalized.lower()).strip('-')
    return key[:48].rstrip('-') or 'game'


def generate(name: str, controller_layout: bool = True, *, launch_arguments: str = ARGUMENTS) -> dict:
    # IsGameName in Playden's library/game_ref.cpp counts Unicode code points.
    if (not isinstance(name, str) or not 1 <= len(name) <= 120
            or any(unicodedata.category(c).startswith('C') or c in '\u2028\u2029' for c in name)
            or not any(unicodedata.category(c)[0] not in 'ZMC' for c in name)):
        raise ValueError('Game name must be 1-120 printable characters with visible content')
    if type(controller_layout) is not bool:
        raise ValueError('Controller layout option must be a boolean')
    key = name_key(name)
    return {
        'recipe_id': f'community-imported-{key}-playden',
        'recipe_version': 1,
        'game': {'store': 'imported', 'key': key, 'name': name},
        'launch_entries': [{
            'id': 'program',
            'label': 'java.exe',
            'executable': PROGRAM,
            'arguments': launch_arguments,
        }],
        'executable': PROGRAM,
        # Playden defaults to pe32; the downloaded Java runtime is Windows x64.
        'architecture': 'pe64',
        'graphics': 'opengl',
        # Both fields are required by ParseCommunityRecipe, even for local imports.
        'community': {'format_version': 1, 'status': 'untested'},
        **({'defaults': {'controller': 'keyboard', 'controller_layout': CONTROLLER_LAYOUT}}
           if controller_layout else {}),
    }


def write(root: Path, name: str, controller_layout: bool = True, *, launch_arguments: str = ARGUMENTS) -> None:
    text = json.dumps(generate(name, controller_layout, launch_arguments=launch_arguments), indent=2, ensure_ascii=False) + '\n'
    if (root / LEGACY_PATH).exists() or (root / LEGACY_PATH).is_symlink():
        raise FileExistsError('Refusing to replace a legacy recipe')
    location(root)  # Refuse linked metadata directories before creating files.
    target = safe_path(root, PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)


def validate(root: Path, name: str | None = None, *, launch_arguments: str = ARGUMENTS) -> list[str]:
    """Check either supported controller configuration and paths, without executing Java.

    This intentionally validates packager output, not arbitrary Playden recipes.
    Package validation supplies the expected name from the package version.
    """
    errors = []
    try:
        with safe_path(root, location(root)).open('rb') as stream:
            data = stream.read(MAX_RECIPE_BYTES + 1)
        if len(data) > MAX_RECIPE_BYTES:
            raise ValueError('recipe exceeds Playden\'s 16 KB import limit')
        document = json.loads(data.decode('utf-8'))
        expected = generate(name if name is not None else document['game']['name'],
                            controller_layout='defaults' in document, launch_arguments=launch_arguments)
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
