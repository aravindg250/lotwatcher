"""Make ONE real API call and save the response to recordings/ for building test fixtures.

This makes a live call. Only run it after the exact call has been approved.
The call goes through SourceClient, so it is paced, counted in the monthly budget and cached,
and the API key is never written to the recording.

recordings/ is ignored by git: it holds real third-party data. Committed fixtures in
tests/fixtures/ are trimmed copies with identifying values replaced.

Usage (inside the api container):
    python scripts/record_fixture.py <source> <path> <name> [param=value ...]
Example:
    python scripts/record_fixture.py nhtsa /recalls/recallsByVehicle recalls_rav4_2020 \
        make=Toyota model=RAV4 modelYear=2020
"""

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from lotwatcher.db.session import get_engine
from lotwatcher.sources.settings import AUTODEV, MARKETCHECK, NHTSA, VPIC, build_client

SOURCES = {s.name: s for s in (AUTODEV, MARKETCHECK, NHTSA, VPIC)}
RECORDINGS = Path("recordings")


def summarize(body) -> str:
    if isinstance(body, list):
        return f"a list of {len(body)} items"
    if isinstance(body, dict):
        lists = {k: len(v) for k, v in body.items() if isinstance(v, list)}
        return f"an object with keys {sorted(body)}; list sizes {lists}"
    return type(body).__name__


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[0] not in SOURCES:
        print(__doc__)
        print(f"Sources: {', '.join(SOURCES)}")
        return 2
    source, path, name, *pairs = argv
    params = dict(pair.split("=", 1) for pair in pairs)

    settings = SOURCES[source]
    client = build_client(settings, get_engine())
    print(f"Calling {settings.base_url}{path} with {params}")
    body = client.get_json(path, params=params)

    out = RECORDINGS / source / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    recording = {
        "request": {"source": source, "path": path, "params": params},
        "recorded_at": datetime.now(UTC).isoformat(),
        "response": body,
    }
    out.write_text(json.dumps(recording, indent=2, sort_keys=True) + "\n")

    print(f"Saved {out} ({out.stat().st_size:,} bytes): {summarize(body)}")
    cap = settings.monthly_cap or "no cap"
    print(f"{source} calls this month: {client.budget.calls_this_month()} (cap {cap})")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main(sys.argv[1:]))
