"""NHTSA recalls and owner complaints by make, model and year. Free, no key, cached 7 days.

Response details learned from real recordings (2026-10-15):
- Recalls use "Count" and complaints use "count"; both put records under "results".
- Recall dates are DD/MM/YYYY (e.g. "25/11/2020"); complaint dates are MM/DD/YYYY.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from lotwatcher.sources.client import SourceClient, UnexpectedResponse

CACHE_TTL = timedelta(days=7)
RECALLS_PATH = "/recalls/recallsByVehicle"
COMPLAINTS_PATH = "/complaints/complaintsByVehicle"


@dataclass(frozen=True)
class Recall:
    campaign_number: str
    component: str
    summary: str
    consequence: str
    remedy: str
    report_received: date | None
    # NHTSA's urgent warnings: don't drive it, or park it outside (fire risk)
    park_it: bool
    park_outside: bool
    over_the_air: bool


@dataclass(frozen=True)
class ComplaintSummary:
    total: int
    crashes: int  # complaints that report a crash
    fires: int  # complaints that report a fire
    injuries: int  # people injured, across all complaints
    deaths: int  # people killed, across all complaints


def _results(client: SourceClient, path: str, make: str, model: str, year: int) -> list[Any]:
    body = client.get_json(
        path, params={"make": make, "model": model, "modelYear": str(year)}, ttl=CACHE_TTL
    )
    results = body.get("results") if isinstance(body, dict) else None
    if not isinstance(results, list):
        raise UnexpectedResponse(f"{client.source} {path}: expected a 'results' list")
    return results


def _day_first_date(client: SourceClient, value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        raise UnexpectedResponse(
            f"{client.source} {RECALLS_PATH}: unreadable date {value!r}"
        ) from None


def fetch_recalls(client: SourceClient, make: str, model: str, year: int) -> list[Recall]:
    return [
        Recall(
            campaign_number=r["NHTSACampaignNumber"],
            component=r.get("Component", ""),
            summary=r.get("Summary", ""),
            consequence=r.get("Consequence", ""),
            remedy=r.get("Remedy", ""),
            report_received=_day_first_date(client, r.get("ReportReceivedDate")),
            park_it=bool(r.get("parkIt")),
            park_outside=bool(r.get("parkOutSide")),
            over_the_air=bool(r.get("overTheAirUpdate")),
        )
        for r in _results(client, RECALLS_PATH, make, model, year)
    ]


def fetch_complaint_summary(
    client: SourceClient, make: str, model: str, year: int
) -> ComplaintSummary:
    complaints = _results(client, COMPLAINTS_PATH, make, model, year)
    return ComplaintSummary(
        total=len(complaints),
        crashes=sum(1 for c in complaints if c.get("crash")),
        fires=sum(1 for c in complaints if c.get("fire")),
        injuries=sum(int(c.get("numberOfInjuries") or 0) for c in complaints),
        deaths=sum(int(c.get("numberOfDeaths") or 0) for c in complaints),
    )
