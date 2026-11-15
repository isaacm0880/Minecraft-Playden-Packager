"""Display-only decimal storage sizes; callers retain exact integer byte counts."""


def format_size(value: int | None, *, prefer_gb: bool = False) -> str:
    """Use decimal GB/MB/KB (1000-based), with bytes for very small values.

    Negative values are OS sentinels for unknown sizes. Capacity labels prefer
    GB, falling back for tiny capacities so positive values never show as zero.
    """
    if value is None or value < 0:
        return 'Unknown'
    if prefer_gb and value >= 10_000_000:
        return f'{value / 1_000_000_000:.2f} GB'
    for unit, divisor in (('GB', 1_000_000_000), ('MB', 1_000_000), ('KB', 1_000)):
        if value >= divisor:
            return f'{value / divisor:.2f} {unit}'
    return f'{value} B'


def format_drive_space(available: int | None, total: int | None) -> str:
    return f'{format_size(available, prefer_gb=True)} free / {format_size(total, prefer_gb=True)} total'
