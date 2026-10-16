"""NHTSA vPIC VIN decoding: one VIN per request, cached forever (a VIN's specs never change).

Learned from real recordings (2026-10-15):
- Every value is text, and missing values are "" or "Not Applicable".
- vPIC answers "successfully" even for a bad VIN, and still fills in make, model and year.
  Only ErrorCode shows the problem ("0" = clean; several codes are comma-separated).
  So only a clean decode should be trusted for exact VIN matching.
"""

from dataclasses import dataclass
from typing import Any

from lotwatcher.sources.client import SourceClient

DECODE_PATH = "/vehicles/DecodeVinValues/{vin}"
EMPTY_VALUES = ("", None, "Not Applicable")


@dataclass(frozen=True)
class VinDecode:
    vin: str
    year: int | None
    make: str | None
    model: str | None
    trim: str | None
    engine: str | None
    drivetrain: str | None
    body_style: str | None
    error_codes: tuple[str, ...]
    error_text: str
    # Every filled-in field, as vPIC sent it, for vehicles.decoded_specs
    specs: dict[str, str]

    @property
    def clean(self) -> bool:
        return self.error_codes == ("0",)


def _has_one_result(body: Any) -> bool:
    results = body.get("Results") if isinstance(body, dict) else None
    return isinstance(results, list) and len(results) == 1 and isinstance(results[0], dict)


def _engine(specs: dict[str, str]) -> str | None:
    parts = []
    try:
        parts.append(f"{float(specs['DisplacementL']):.1f}L")
    except (KeyError, ValueError):
        pass
    if "EngineCylinders" in specs:
        parts.append(f"{specs['EngineCylinders']}-cyl")
    if "FuelTypePrimary" in specs:
        parts.append(specs["FuelTypePrimary"])
    return " ".join(parts) or None


def decode_vin(client: SourceClient, vin: str) -> VinDecode:
    vin = vin.strip().upper()
    body = client.get_json(
        DECODE_PATH.format(vin=vin), params={"format": "json"}, ttl=None, check=_has_one_result
    )
    result = body["Results"][0]
    specs = {k: str(v) for k, v in result.items() if v not in EMPTY_VALUES}

    year = specs.get("ModelYear")
    return VinDecode(
        vin=vin,
        year=int(year) if year and year.isdigit() else None,
        make=specs.get("Make"),
        model=specs.get("Model"),
        trim=specs.get("Trim"),
        engine=_engine(specs),
        drivetrain=specs.get("DriveType"),
        body_style=specs.get("BodyClass"),
        error_codes=tuple(c.strip() for c in specs.get("ErrorCode", "").split(",") if c.strip()),
        error_text=specs.get("ErrorText", ""),
        specs=specs,
    )
