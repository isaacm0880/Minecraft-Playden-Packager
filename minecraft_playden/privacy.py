"""Conservative, known-pattern privacy cleanup of a private staged package only."""
import json
import re
from pathlib import Path

from .operations import CancellationToken

# Normalised exact field names, rather than substring matches (e.g. tokenCount).
SENSITIVE_KEYS = frozenset({
    'accesstoken', 'refreshtoken', 'clienttoken', 'idtoken', 'authtoken',
    'authenticationtoken', 'token', 'authorization', 'password', 'passwd', 'secret',
    'clientsecret', 'apikey', 'session', 'sessionid', 'sessiontoken',
    'username', 'playername', 'accountname', 'displayname', 'uuid', 'playeruuid', 'userid', 'xuid',
    'email', 'emailaddress', 'accountid', 'selectedaccount', 'selecteduser',
    'authenticationdatabase', 'credentials', 'lastserver', 'authsession',
})
PRIVATE_FILES = frozenset({
    'launcher_accounts.json',
    'launcher_accounts_microsoft_store.json', 'launcher_profiles.json',
    'launcher_profiles_microsoft_store.json', 'usercache.json', 'usernamecache.json',
    'servers.dat', 'servers.dat_old', 'realms_persistence.json',
    'cookies', 'cookies.sqlite', 'cookies.sqlite-wal', 'cookies.sqlite-shm',
    'login data', 'login data-journal', 'session.lock', '.env',
})
PRIVATE_DIRS = frozenset({'webcache', 'webcache2', 'sessionstorage', 'session storage',
                          'local storage', 'logs', 'crash-reports'})


def sensitive(key: str) -> bool:
    return re.sub(r'[^a-z0-9]', '', key.casefold()) in SENSITIVE_KEYS


def clean_json(value: object) -> object:
    if isinstance(value, dict):
        return {key: clean_json(item) for key, item in value.items() if not sensitive(key)}
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    return value


def cleanup(root: Path, cancellation: CancellationToken | None = None) -> None:
    """Run before inventory hashing. Never pass the source installation here.

    Unknown binary formats and unlabelled secrets cannot be identified reliably.
    Malformed structured config is omitted rather than copied with known secrets.
    """
    import shutil
    for path in sorted(root.rglob('*'), key=lambda item: len(item.parts)):
        if cancellation is not None:
            cancellation.check()
        if not path.exists():  # A private parent may already have been removed.
            continue
        if path.is_symlink():
            raise ValueError('Privacy cleanup requires a staged tree without symlinks')
        name = path.name.casefold()
        if path.is_dir():
            if name in PRIVATE_DIRS:
                shutil.rmtree(path)
            continue
        if name in PRIVATE_FILES or (name in {'accounts.json', 'accounts.json.bak'} and
                                      path.parent.name in {'minecraft', '.minecraft', 'instance-files'}):
            path.unlink()
            continue
        if path.suffix.casefold() not in {'.json', '.cfg', '.ini', '.properties', '.toml', '.yaml', '.yml', '.txt'}:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeError:
            continue
        if path.suffix.casefold() == '.json':
            try:
                original = json.loads(text)
            except ValueError:
                if any(sensitive(key) for key in re.findall(r'"([^"\n]+)"\s*:', text)):
                    path.unlink()
                continue
            cleaned = clean_json(original)
            if cleaned != original:
                path.write_text(json.dumps(cleaned, indent=2) + '\n', encoding='utf-8')
        else:
            # Omit a config containing known sensitive fields in formats where
            # deleting one line could leave multiline secret values behind.
            fields = re.findall(r"""(?m)^\s*["']?([\w .-]+?)["']?\s*[:=]""", text)
            if any(sensitive(key) for key in fields):
                path.unlink()
