"""VIN helpers: clean up a VIN and check it locally, so obviously bad VINs cost no API call.

The check digit (9th character) is the standard North American formula: each character maps
to a number, numbers are weighted by position, and the sum mod 11 is the check digit
(10 is written "X"). I, O and Q are never used in VINs.
"""

import re

_VALUES = {
    **{str(d): d for d in range(10)},
    **dict(zip("ABCDEFGH", range(1, 9), strict=True)),
    **dict(zip("JKLMN", range(1, 6), strict=True)),
    "P": 7,
    "R": 9,
    **dict(zip("STUVWXYZ", range(2, 10), strict=True)),
}
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
_SHAPE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")


def clean_vin(vin: str | None) -> str | None:
    """Trim and uppercase a VIN; None when blank."""
    if vin is None:
        return None
    vin = vin.strip().upper()
    return vin or None


def check_digit(vin: str) -> str:
    """The check digit a 17-character VIN should have in position 9."""
    total = sum(_VALUES[c] * w for c, w in zip(vin, _WEIGHTS, strict=True))
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def is_valid_vin(vin: str) -> bool:
    """17 allowed characters with a correct check digit."""
    return bool(_SHAPE.fullmatch(vin)) and vin[8] == check_digit(vin)
