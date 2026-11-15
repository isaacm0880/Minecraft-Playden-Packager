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
    BROKEN = 'Broken'
    CANNOT_PACKAGE = 'Cannot package'


@dataclass(frozen=True)
class Compatibility:
    status: Status
    evidence: str

    @property
    def permits_build(self) -> bool:
        return self.status in (Status.TESTED, Status.UNTESTED)

    @property
    def label(self) -> str:
        return f'{self.status} — {self.evidence}'


# Exact Minecraft + loader identities; never infer evidence from a completed build.
REGISTRY: dict[tuple[str, tuple[ModLoader, ...]], Compatibility] = {
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
