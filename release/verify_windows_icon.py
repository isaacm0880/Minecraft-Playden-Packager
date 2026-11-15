"""Check actual PE icon resources against every frame of the official ICO."""
from pathlib import Path
import struct


def verify(executable: Path, ico_file: Path) -> None:
    import pefile  # type: ignore[import-not-found, import-untyped]
    ico = ico_file.read_bytes()
    reserved, kind, count = struct.unpack_from('<HHH', ico)
    if (reserved, kind, count) != (0, 1, 7):
        raise ValueError('Expected the seven-frame official ICO')
    frames = []
    for index in range(count):
        entry = struct.unpack_from('<BBBBHHII', ico, 6 + 16 * index)
        frames.append((entry[:6], ico[entry[7]:entry[7] + entry[6]]))
    pe = pefile.PE(str(executable))
    try:
        resources = {entry.id: entry for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries}
        images = {image.id: [pe.get_data(language.data.struct.OffsetToData, language.data.struct.Size)
                            for language in image.directory.entries]
                  for image in resources[3].directory.entries}
        group = next(entry for entry in resources[14].directory.entries if entry.id == 1)
        if not group.directory.entries:
            raise ValueError('Windows executable has no icon language resources')
        for language in group.directory.entries:
            data = pe.get_data(language.data.struct.OffsetToData, language.data.struct.Size)
            if struct.unpack_from('<HHH', data) != (0, 1, count):
                raise ValueError('Windows executable is missing official icon resolutions')
            for index, (dimensions, expected) in enumerate(frames):
                entry = struct.unpack_from('<BBBBHHIH', data, 6 + 14 * index)
                payloads = images.get(entry[7], [])
                if entry[:6] != dimensions or entry[6] != len(expected) or not payloads or any(blob != expected for blob in payloads):
                    raise ValueError('Windows executable icon pixels do not match the official ICO')
    except (AttributeError, KeyError, StopIteration, struct.error) as exc:
        raise ValueError('Windows executable has incomplete icon resources') from exc
    finally:
        pe.close()
