"""Console evidence is independent of successful package validation."""
from dataclasses import dataclass
from enum import StrEnum

from .modloaders import ModLoader

UNTESTED_WARNING = ('Unsupported version — not tested on PS5. Packaging will be attempted, '
                    'but the game or some mods may not work.')
FORGE_BROKEN = 'Broken — known unresolved Playden launch issues for Forge. Building Forge packages is disabled.'


class Status(StrEnum):
    TESTED = 'Tested'
    UNTESTED = 'Unsupported / Untested'
    RETEST = 'Failed on PS5 / Retest required'
    BROKEN = 'Broken'
    CANNOT_PACKAGE = 'Cannot package'


@dataclass(frozen=True)
class Compatibility:
    status: Status
    evidence: str

    @property
    def permits_build(self) -> bool:
        return self.status in (Status.TESTED, Status.UNTESTED, Status.RETEST)

    @property
    def label(self) -> str:
        if self.status == Status.TESTED:
            return 'Works'
        if self.status in (Status.BROKEN, Status.RETEST):
            return 'Broken'
        return 'Untested'

    @property
    def details(self) -> str:
        return f'{self.status} — {self.evidence}'


# Exact Minecraft + loader identities; never infer evidence from a completed build.
REGISTRY: dict[tuple[str, tuple[ModLoader, ...]], Compatibility] = {
    ('1.20.1', ()): Compatibility(
        Status.TESTED, 'Vanilla Minecraft 1.20.1 confirmed reaching the main menu on PS5 through Playden 0.12.0-nightly.20261116.1. '
        'This confirms startup, not world loading, Fabric, mods or other runtime configurations.'),
    ('1.20.1', (ModLoader('Fabric', '0.20.1'),)): Compatibility(
        Status.TESTED, 'Confirmed reaching the main menu on PS5 with native pre-remapping through Playden '
        '0.12.0-nightly.20261116.1. This confirms startup only, not world loading or every Fabric mod. '
        'The original unprepared package failed during first-launch remapping.'),
    ('26.3', ()): Compatibility(
        Status.TESTED,
        'Vanilla Minecraft 26.3 confirmed working on PS5 in a tested configuration. '
        'This evidence does not cover modded configurations or other loaders.'),
    ('26.3', (ModLoader('Fabric', '0.20.1'),)): Compatibility(
        Status.TESTED,
        'Confirmed working on PS5 in a tested configuration without Ixeris. '
        'Other mods and modpacks using this combination are not verified.'),
}


def assess(version: str, loaders: tuple[ModLoader, ...], problem: str = '') -> Compatibility:
    if any(loader.name == 'Forge' for loader in loaders):
        return Compatibility(Status.BROKEN, FORGE_BROKEN.removeprefix('Broken — '))
    if problem:
        return Compatibility(Status.CANNOT_PACKAGE, problem)
    if loaders and not (len(loaders) == 1 and loaders[0].name == 'Fabric'):
        return Compatibility(Status.CANNOT_PACKAGE, 'Launch-package generation for this loader is not implemented.')
    return REGISTRY.get((version, loaders), Compatibility(Status.UNTESTED, UNTESTED_WARNING))


@dataclass(frozen=True)
class MetadataCapability:
    """Preflight capability, independent of structural validation and PS5 reports."""
    can_package: bool
    required_java: int | None
    release_type: str
    problem: str = ''


def assess_metadata(metadata: dict) -> MetadataCapability:
    from .metadata import normalize, check_runtime_requirement, java_major, logging_spec
    from .dependencies import resolve
    major = None
    try:
        major = java_major(metadata)
        effective = normalize(metadata)
        check_runtime_requirement(effective)
        resolve(effective)
        logging_spec(effective)
        from .arguments import generate
        from .dependencies import classpath
        launch = generate(effective, classpath(effective), '../../')
        if major == 8:
            from .java8 import artifacts
            artifacts(effective, classpath(effective), launch)
    except (ValueError, TypeError, KeyError) as exc:
        return MetadataCapability(False, major, str(metadata.get('type', 'unknown')), str(exc))
    return MetadataCapability(True, major, str(metadata.get('type', 'unknown')))
