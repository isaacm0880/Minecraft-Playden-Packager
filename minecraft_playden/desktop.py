"""Qt-independent desktop build request validation and engine handoff."""
from dataclasses import dataclass
import json
from pathlib import Path
import os
import re
import unicodedata

from .discovery import Installation
from .fabric_remap import PreparationStatus, package_status
from .downloads import Downloader
from .exporter import build
from .progress import Progress, ProgressCallback
from . import profiles
from .instance_content import import_instance, inspect as inspect_content
from .validator import validate as validate_package
from .operations import CancellationToken
from .sources import ClientSource
from .cleanup import GeneratedOutput
from .importers import version_id, MultiMCAdapter, OfficialAdapter


@dataclass(frozen=True)
class ValidatedPackage:
    path: Path
    kind: str = 'launch-package'
    generated: GeneratedOutput | None = None
    preparation: PreparationStatus | None = None

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class LocalBuildRequest:
    installation: Installation
    output: Path
    cache: Path
    exclude_saves: bool = False
    launch_profile: str = profiles.DEFAULT_PROFILE
    username: str = 'Player'
    file_only: bool = False
    privacy_cleanup: bool = True
    allow_runtime_download: bool = True
    prepare_fabric: bool = True
    remap_java: Path | None = None
    offline: bool = False

    def validate(self) -> None:
        if self.installation.build_problem and not self.file_only:
            raise ValueError(self.installation.build_problem)
        if self.file_only:
            inspect_content(self.installation.instance, self.installation.version if self.installation.version != 'Unknown' else None)
        elif self.launch_profile not in profiles.PROFILES:
            raise ValueError('Choose a supported launch profile.')
        elif self.launch_profile == profiles.REFERENCE_PROFILE and (self.installation.version != '26.3' or self.installation.loaders):
            raise ValueError('reference-26.3 is a diagnostic profile for Vanilla 26.3 only. Choose official for Fabric.')
        if not self.file_only:
            version_id(self.installation.version)
        if self.installation.version == 'Unknown' and not self.file_only:
            raise ValueError('Base Minecraft version could not be resolved. Rescan or choose another installation.')
        # A source may have acquired a loader since discovery. Recheck local
        # metadata so the explicit-JAR handoff cannot silently strip that loader.
        instance = self.installation.instance
        latest = None
        if not self.file_only and (instance / 'mmc-pack.json').is_file():
            latest = MultiMCAdapter().inspect(instance, None, self.installation.libraries_root, self.installation.launcher_root)
        elif not self.file_only and (instance / f'{instance.name}.json').is_file():
            latest = OfficialAdapter().inspect(instance)
        if latest is not None:
            if latest.packaging_problem or latest.base_problem:
                raise ValueError('\n'.join(p for p in (latest.base_problem, latest.packaging_problem) if p))
            if latest.loaders != self.installation.loaders:
                raise ValueError('Source loader changed since discovery; rescan before building.')
            if latest.version != self.installation.version or (self.installation.client is None or latest.client.resolve() != self.installation.client.resolve()):
                raise ValueError('Source installation changed since discovery. Rescan before building.')
        output, cache = self.output.resolve(), self.cache.resolve()
        if self.output.exists() or self.output.is_symlink():
            raise ValueError('Choose a new output folder. Existing folders and packages are never overwritten.')
        for path, label in ((output.parent, 'Output parent'), (cache, 'Download cache')):
            ancestor = path
            while not ancestor.exists() and not ancestor.is_symlink() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            if not ancestor.is_dir():
                raise ValueError(f'{label} has a parent that is not a directory: {ancestor}')
            if not os.access(ancestor, os.W_OK | os.X_OK):
                raise ValueError(f'{label} is not writable: {ancestor}')
        instance = self.installation.instance.resolve()
        protected = [instance]
        if self.installation.launcher_root is not None:
            protected.append(self.installation.launcher_root.resolve())
        if self.installation.libraries_root is not None:
            protected.append(self.installation.libraries_root.resolve())
        if instance.parent.name.casefold() in ('instances', 'versions'):
            protected.append(instance.parent.parent)
        if self.installation.client is not None:
            protected.append(self.installation.client.resolve().parent)
        for root in protected:
            if output.is_relative_to(root) or root.is_relative_to(output):
                raise ValueError('Output must be separate from the source installation.')
            if cache.is_relative_to(root) or root.is_relative_to(cache):
                raise ValueError('Cache must be separate from the source installation.')
        if output.is_relative_to(cache) or cache.is_relative_to(output):
            raise ValueError('Output and download cache must be separate folders.')

    def run(self, progress: ProgressCallback | None = None,
            cancellation: CancellationToken | None = None) -> ValidatedPackage:
        # Validate again in the worker; files may have changed since selection.
        if cancellation is not None:
            cancellation.check()
        self.validate()
        if self.file_only:
            import_instance(self.installation.instance, self.output,
                            self.installation.version if self.installation.version != 'Unknown' else None,
                            self.exclude_saves, cancellation=cancellation, progress=progress)
            return ValidatedPackage(self.output, 'instance-import')
        source = ClientSource('local', version=self.installation.version, instance=self.installation.instance, client_jar=self.installation.client, libraries_root=self.installation.libraries_root, launcher_root=self.installation.launcher_root)
        build(self.output, Downloader(self.cache, cancellation, allow_runtime_download=self.allow_runtime_download, offline=self.offline), source, self.username,
              progress=progress, cancellation=cancellation, exclude_saves=self.exclude_saves,
              launch_profile=self.launch_profile, privacy_cleanup=self.privacy_cleanup,
              instance_name=self.installation.name, prepare_fabric='auto' if self.prepare_fabric else False,
              remap_java=self.remap_java)
        protected = tuple(path for path in (self.installation.instance, self.installation.launcher_root,
                                             self.installation.libraries_root,
                                             self.installation.client.parent if self.installation.client else None)
                          if path is not None)
        try:
            generated = GeneratedOutput.capture(self.output, protected)
        except (OSError, ValueError) as exc:
            # A published build still succeeded if optional cleanup cannot be enabled.
            generated = None
            if progress is not None:
                progress(Progress('cleanup-registration', f'Local cleanup unavailable: {exc}'))
        return ValidatedPackage(self.output, generated=generated, preparation=package_status(self.output, validated=True))


def build_readiness(installation: Installation | None, output: str, cache: str,
                    busy: bool = False, *, exclude_saves: bool = False,
                    launch_profile: str = profiles.DEFAULT_PROFILE, username: str = 'Player',
                    file_only: bool = False) -> tuple[LocalBuildRequest | None, str]:
    """Single validation policy for the button, explanation and worker launch."""
    if busy:
        return None, 'An operation is in progress. Wait for it to finish.'
    if installation is None:
        return None, 'Select a detected installation or add a source folder.'
    issues = []
    if installation.build_problem and not file_only:
        issues.append(installation.build_problem)
    if not output.strip():
        issues.append('Choose a new output folder.')
    if not cache.strip():
        issues.append('Choose a download cache folder.')
    if issues:
        return None, '\n'.join(issues)
    try:
        request = LocalBuildRequest(installation, Path(output.strip()).expanduser().absolute(),
                                    Path(cache.strip()).expanduser().absolute(), exclude_saves,
                                    launch_profile, username, file_only)
        request.validate()
        return request, ''
    except (OSError, ValueError, RuntimeError) as exc:
        return None, str(exc)


def check_package(path: Path, progress: ProgressCallback | None = None,
                  cancellation: CancellationToken | None = None) -> ValidatedPackage:
    """Run the CLI's package validator in the desktop worker without writing files."""
    if cancellation is not None:
        cancellation.check()
    if not (path / 'package.json').is_file():
        raise ValueError('Select a package folder containing package.json, not a launcher instance.')
    errors = validate_package(path, cancellation=cancellation, progress=progress)
    if errors:
        raise ValueError('Package validation failed:\n' + '\n'.join(errors))
    inventory = json.loads((path / 'package.json').read_text(encoding='utf-8'))
    if progress is not None:
        progress(Progress('complete', 'Package file validation passed.', 1, 1))
    return ValidatedPackage(path, inventory.get('kind', 'launch-package'), preparation=package_status(path, validated=True, cancellation=cancellation))


def suggest_output(installation: Installation, home: Path | None = None) -> Path:
    """Suggest a fresh path without creating or altering any directory."""
    parent = (home if home is not None else Path.home()) / 'Minecraft-Playden-Packages'
    base = parent / sanitise_name(installation.name)
    return unique_output(base)


def sanitise_name(name: str) -> str:
    """A single portable Windows/macOS/Linux folder component."""
    name = unicodedata.normalize('NFC', name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', name).strip().rstrip('. ')
    name = name[:120]
    while len(name.encode('utf-8')) > 180:
        name = name[:-1]
    name = name.rstrip('. ') or 'Minecraft-Instance'
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{p}{n}' for p in ('COM', 'LPT') for n in (*range(1, 10), '¹', '²', '³')}
    if name.split('.')[0].upper() in reserved:
        name = '_' + name
    return name


def unique_output(base: Path, reserved: set[Path] | None = None) -> Path:
    """Avoid on-disk and queued names, including case-only collisions."""
    occupied = {unicodedata.normalize('NFC', p.name).casefold() for p in base.parent.iterdir()} if base.parent.is_dir() else set()
    occupied.update(unicodedata.normalize('NFC', p.name).casefold() for p in (reserved or set()) if p.parent == base.parent)
    candidate, suffix = base, 2
    while candidate.name.casefold() in occupied or candidate.exists() or candidate.is_symlink():
        candidate = base.with_name(f'{base.name}-{suffix}')
        suffix += 1
    return candidate


def selection_readiness(installations: list[Installation], output: str, cache: str,
                        busy: bool = False, *, username: str = 'Player',
                        privacy_cleanup: bool = True, allow_runtime_download: bool = True, offline: bool = False,
                        prepare_fabric: bool = True, remap_java: Path | None = None) -> tuple[list[LocalBuildRequest], str]:
    """Capture every selected row; no partial queue if any input is blocked."""
    from dataclasses import replace
    if busy:
        return [], 'An operation is in progress. Wait for it to finish.'
    if not installations:
        return [], 'Select a detected installation or add a source folder.'
    requests = []
    problems = []
    reserved: set[Path] = set()
    for installation in installations:
        try:
            path = output
            if len(installations) > 1 and output.strip():
                target = unique_output(Path(output.strip()).expanduser().absolute().parent / sanitise_name(installation.name), reserved)
                reserved.add(target)
                path = str(target)
            request, reason = build_readiness(installation, path, cache, username=username)
            if not installation.compatibility.permits_build:
                reason = installation.compatibility.label
            if reason or request is None:
                problems.append(f'{installation.name} ({installation.loader_label}, {installation.version}): {reason}')
            else:
                requests.append(replace(request, privacy_cleanup=privacy_cleanup, allow_runtime_download=allow_runtime_download,
                                        prepare_fabric=prepare_fabric, remap_java=remap_java, offline=offline))
        except (OSError, ValueError, RuntimeError) as exc:
            problems.append(f'{installation.name}: {exc}')
    if problems:
        return [], (f'Cannot build selection: {len(problems)} of {len(installations)} selected instances prevent building.\n'
                    + '\n'.join(problems) + '\nDeselect blocked instances or resolve their problems to continue.')
    return requests, ''


def offline_player_name(value: object) -> str:
    """GUI display-name preference, preserving printable names and argfile quoting.

    Spaces and Unicode are already supported by the offline launch generator.
    Empty/non-text preferences fall back to Player; control characters cannot be
    useful in a display name. CLI choices do not read or write this preference.
    """
    if not isinstance(value, str):
        return 'Player'
    return ''.join(character for character in value if character.isprintable()).strip() or 'Player'


@dataclass
class BuildRecord:
    request: LocalBuildRequest
    state: str = 'queued'
    detail: str = ''
    package: ValidatedPackage | None = None
    removed: bool = False
    usb: Path | None = None


def queue_summary(records: list[BuildRecord]) -> str:
    counts = {state: sum(record.state == state for record in records)
              for state in ('completed', 'failed', 'cancelled', 'not started')}
    summary = (f'{len(records)} instances: {counts["completed"]} succeeded, {counts["failed"]} failed, '
               f'{counts["cancelled"]} cancelled, {counts["not started"]} not started.')
    lines = [summary]
    for record in records:
        location = ('Local output removed after verified USB copy: ' if record.removed else
                    'Output: ' if record.package else 'Planned output (not published): ')
        installation = record.request.installation
        lines.append(f'{installation.name} ({installation.version}, {installation.loader_label}) — {record.state}\n'
                     f'Source: {installation.instance}\n{location}{record.request.output}' +
                     (f'\nUSB: {record.usb}' if record.usb else '') +
                     (f'\n{record.detail}' if record.detail else ''))
    return '\n\n'.join(lines)


def prepare_existing_package(source: Path, output: Path, java: Path | None = None,
                             progress: ProgressCallback | None = None,
                             cancellation: CancellationToken | None = None) -> ValidatedPackage:
    from .fabric_remap import prepare_package
    prepare_package(source, output, java, cancellation, progress)
    # Publication is the commit point; a late cancellation cannot revoke success.
    generated = None
    try:
        generated = GeneratedOutput.capture(output, (source,))
    except (OSError, ValueError):
        pass
    result = ValidatedPackage(output, generated=generated, preparation=package_status(output, validated=True))
    if progress is not None:
        progress(Progress('complete', 'Prepared Fabric package validated and ready for transfer.', 1, 1))
    return result
