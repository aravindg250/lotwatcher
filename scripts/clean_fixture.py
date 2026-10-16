"""Turn a raw recording (recordings/, never committed) into a fixture safe for a public repo.

Keeps a few listings, replaces every value that could identify a real car, dealer or person
with an obvious fake, and then VERIFIES that none of the original identifying values survive.
Car details (trim, price, mileage, history) stay real: that's what the tests need.

Fake VINs start with "0", which no manufacturer code uses, and carry a correct check digit.

Usage:
    python scripts/clean_fixture.py autodev <recording.json> <fixture.json> [--keep N]
"""

import argparse
import copy
import hashlib
import hmac
import json
import re
import secrets
import sys
from pathlib import Path

from lotwatcher.vin import check_digit

FAKE_ZIP = "75201"
FAKE_CITY = "Dallas"
# Random secret that keys the fakes. It lives in recordings/ (ignored by git), so nobody can
# work back from a fake to the real value, even by guessing VINs.
SECRET_FILE = Path("recordings/.fixture_secret")


def fake_vin(n: int) -> str:
    draft = f"0FAKE000X{n:08d}"
    return draft[:8] + check_digit(draft.replace("X", "0")) + draft[9:]


def _secret() -> bytes:
    if not SECRET_FILE.exists():
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_text(secrets.token_hex(32))
    return SECRET_FILE.read_text().strip().encode()


class Cleaner:
    """Hands out fakes consistently: the same real value always gets the same fake,
    in every fixture file, because each fake is a keyed fingerprint of the real value."""

    def __init__(self):
        self.originals: set[str] = set()
        self._key = _secret()

    def _number(self, kind: str, real: str, digits: int) -> int:
        mac = hmac.new(self._key, f"{kind}:{real}".encode(), hashlib.sha256).hexdigest()
        return int(mac, 16) % 10**digits

    def remember(self, *values):
        self.originals.update(str(v) for v in values if v not in (None, ""))

    def vin(self, real: str) -> str:
        self.remember(real)
        return fake_vin(self._number("vin", real, 8))

    def dealer_number(self, real_id: str) -> int:
        self.remember(real_id)
        return self._number("dealer", real_id, 4)


def pick_listings(listings: list, dealer_of, keep: int) -> list:
    """Prefer a pair from the same dealer (useful for dealer-matching tests), then fill up."""
    by_dealer: dict[str, list] = {}
    for listing in listings:
        by_dealer.setdefault(dealer_of(listing), []).append(listing)
    picked = next((group[:2] for group in by_dealer.values() if len(group) >= 2), [])
    for listing in listings:
        if len(picked) >= keep:
            break
        if listing not in picked:
            picked.append(listing)
    return picked[:keep]


def clean_autodev(response: dict, keep: int, cleaner: Cleaner) -> dict:
    out = copy.deepcopy(response)
    # "user" holds the account owner's email, name, IP and location; "discover" holds real VINs
    out.pop("user", None)
    out.pop("discover", None)

    data = pick_listings(out["data"], lambda x: x["retailListing"]["dealerId"], keep)
    for listing in data:
        real_vin = listing["vin"]
        vin = cleaner.vin(real_vin)
        listing["vin"] = vin
        listing["vehicle"]["vin"] = vin
        cleaner.remember(listing["vehicle"].get("squishVin"), listing.get("@id"))
        listing["vehicle"]["squishVin"] = vin[:8] + vin[9:11]
        listing["@id"] = f"https://api.auto.dev/listings/{vin}"

        retail = listing["retailListing"]
        n = cleaner.dealer_number(retail["dealerId"])
        cleaner.remember(
            retail.get("dealer"),
            retail.get("carfaxUrl"),
            retail.get("vdp"),
            retail.get("primaryImage"),
            retail.get("zip"),
        )
        retail["dealer"] = f"Example Motors {n}"
        retail["dealerId"] = f"d_example{n}"
        retail["city"] = FAKE_CITY
        retail["zip"] = FAKE_ZIP
        retail["carfaxUrl"] = f"https://example.com/carfax/{vin}"
        retail["vdp"] = f"https://example.com/dealer{n}/listing/{vin}"
        retail["primaryImage"] = f"https://example.com/photos/{vin}.jpg"
        # Same fake spot for the same dealer; keeps Auto.dev's [longitude, latitude] order
        offset = (n % 50) * 0.004  # within ~15 miles of downtown Dallas
        listing["location"] = [round(-96.80 + offset, 6), round(32.78 + offset, 6)]
    out["data"] = data
    return out


def verify(fixture_text: str, cleaner: Cleaner) -> list[str]:
    problems = [
        f"original value survived: {v[:12]}…" for v in cleaner.originals if v in fixture_text
    ]
    if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", fixture_text):
        problems.append("something that looks like an email address")
    if re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", fixture_text):
        problems.append("something that looks like an IP address")
    if '"user"' in fixture_text:
        problems.append('a "user" section')
    return problems


CLEANERS = {"autodev": clean_autodev}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=CLEANERS)
    parser.add_argument("recording", type=Path)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--keep", type=int, default=3)
    args = parser.parse_args()

    recording = json.loads(args.recording.read_text())
    cleaner = Cleaner()
    response = CLEANERS[args.source](recording["response"], args.keep, cleaner)
    fixture = {
        "note": (
            f"Real {args.source} response shape, trimmed to {len(response['data'])} listings. "
            "Every value that could identify a car, dealer or person is replaced with an obvious "
            "fake (VINs starting with 0, Example Motors, example.com). Car details are real."
        ),
        "request": recording["request"],
        "recorded_at": recording["recorded_at"],
        "response": response,
    }
    text = json.dumps(fixture, indent=2, sort_keys=True) + "\n"

    problems = verify(text, cleaner)
    if problems:
        print("NOT SAVED. " + "; ".join(problems), file=sys.stderr)
        return 1
    args.fixture.parent.mkdir(parents=True, exist_ok=True)
    args.fixture.write_text(text)
    print(f"Saved {args.fixture}: {len(response['data'])} listings, verified clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
