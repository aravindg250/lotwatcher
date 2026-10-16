"""Turn a raw recording (recordings/, never committed) into a fixture safe for a public repo.

Keeps a few listings, replaces every value that could identify a real car, dealer or person
with an obvious fake, and then VERIFIES that none of the original identifying values survive.
Car details (trim, price, mileage, history, days on market) stay real: that's what tests need.

Fakes are keyed fingerprints of the real values (see SECRET_FILE), so the same real car, dealer
name, street or phone gets the same fake in every file and from every source. That keeps the
signals duplicate merging and dealer matching rely on, without the real values.
Fake VINs start with "0", which no manufacturer code uses, and carry a correct check digit.

Usage:
    python scripts/clean_fixture.py <autodev|marketcheck> <recording> <fixture> [--keep N]
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
# Fake emails use example.com, a domain reserved for examples
EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.[\w.]+)")


def fake_vin(n: int) -> str:
    draft = f"0FAKE000X{n:08d}"
    return draft[:8] + check_digit(draft.replace("X", "0")) + draft[9:]


def _secret() -> bytes:
    if not SECRET_FILE.exists():
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_text(secrets.token_hex(32))
    return SECRET_FILE.read_text().strip().encode()


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


class Cleaner:
    """Hands out fakes and remembers every original value it replaced."""

    def __init__(self):
        self.originals: set[str] = set()
        self._key = _secret()

    def number(self, kind: str, real, digits: int) -> int:
        mac = hmac.new(self._key, f"{kind}:{real}".encode(), hashlib.sha256).hexdigest()
        return int(mac, 16) % 10**digits

    def remember(self, *values):
        for v in values:
            # Very short values (like "TX" or a 3-digit id) would match by accident elsewhere
            if v not in (None, "") and len(str(v)) >= 4:
                self.originals.add(str(v))

    def swap(self, record: dict, key: str, fake):
        """Replace record[key] with fake (if the key is there and set), remembering the original."""
        if record.get(key) not in (None, ""):
            self.remember(record[key])
            record[key] = fake(record[key]) if callable(fake) else fake

    def vin(self, real: str) -> str:
        self.remember(real)
        return fake_vin(self.number("vin", real, 8))

    def dealer_number(self, real_name: str) -> int:
        return self.number("dealer-name", _normal(real_name), 4)

    def dealer_name(self, real: str) -> str:
        return f"Example Motors {self.dealer_number(real)}"

    def street(self, real: str) -> str:
        return f"{self.number('street', _normal(real), 4) + 1} Example Street"

    def phone(self, real: str) -> str:
        digits = re.sub(r"\D", "", str(real))
        return f"555-01{self.number('phone', digits, 2):02d}"

    def place(self, number: int) -> tuple[float, float]:
        """Fake (latitude, longitude) within ~15 miles of downtown Dallas."""
        offset = (number % 50) * 0.004
        return round(32.78 + offset, 6), round(-96.80 + offset, 6)


def pick_listings(listings: list, dealer_of, keep: int, must=None) -> list:
    """A pair from the same dealer (for dealer-matching tests), any listing `must` asks for,
    then fill up in order."""
    by_dealer: dict[str, list] = {}
    for listing in listings:
        by_dealer.setdefault(dealer_of(listing), []).append(listing)
    picked = next((group[:2] for group in by_dealer.values() if len(group) >= 2), [])
    if must is not None:
        picked += [next(x for x in listings if must(x) and x not in picked)]
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
        vin = cleaner.vin(listing["vin"])
        listing["vin"] = vin
        listing["vehicle"]["vin"] = vin
        cleaner.swap(listing["vehicle"], "squishVin", vin[:8] + vin[9:11])
        cleaner.swap(listing, "@id", f"https://api.auto.dev/listings/{vin}")

        retail = listing["retailListing"]
        n = cleaner.dealer_number(retail["dealer"])
        cleaner.swap(retail, "dealerId", lambda real: f"d_example{cleaner.number('id', real, 6)}")
        cleaner.swap(retail, "dealer", cleaner.dealer_name)
        cleaner.swap(retail, "carfaxUrl", f"https://example.com/carfax/{vin}")
        cleaner.swap(retail, "vdp", f"https://example.com/dealer{n}/listing/{vin}")
        cleaner.swap(retail, "primaryImage", f"https://example.com/photos/{vin}.jpg")
        retail["city"] = FAKE_CITY
        retail["zip"] = FAKE_ZIP
        # Auto.dev's order is [longitude, latitude]
        lat, lon = cleaner.place(n)
        listing["location"] = [lon, lat]
    out["data"] = data
    return out


def _clean_mc_place(place: dict, cleaner: Cleaner) -> None:
    """A MarketCheck dealer, dealership or car_location block."""
    name = place.get("name") or place.get("seller_name") or ""
    n = cleaner.dealer_number(name)
    cleaner.swap(place, "name", cleaner.dealer_name)
    cleaner.swap(place, "seller_name", cleaner.dealer_name)
    cleaner.swap(place, "street", cleaner.street)
    cleaner.swap(place, "phone", cleaner.phone)
    cleaner.swap(place, "seller_email", f"sales{n}@example.com")
    cleaner.swap(place, "website", f"example.com/dealer{n}")
    lat, lon = cleaner.place(n)
    cleaner.swap(place, "latitude", str(lat))
    cleaner.swap(place, "longitude", str(lon))
    for key in list(place):
        if key == "id" or key.endswith("_id"):
            cleaner.swap(place, key, lambda real, k=key: type(real)(cleaner.number(k, real, 6)))
        elif key.endswith("group_name"):
            cleaner.swap(place, key, lambda real: f"Example Group {cleaner.number('g', real, 3)}")
    if "city" in place:
        place["city"] = FAKE_CITY
    if "zip" in place:
        place["zip"] = FAKE_ZIP


def clean_marketcheck(response: dict, keep: int, cleaner: Cleaner) -> dict:
    out = copy.deepcopy(response)
    listings = pick_listings(
        out["listings"],
        # dealer.id is a dealer group or shared website; the rooftop id is one physical lot
        lambda x: (x.get("mc_dealership") or {}).get("mc_rooftop_id"),
        keep,
        must=lambda x: not x.get("price"),  # keep one "call for price" listing if any
    )
    for listing in listings:
        vin = cleaner.vin(listing["vin"])
        listing["vin"] = vin
        n = cleaner.dealer_number(listing.get("dealer", {}).get("name", ""))
        cleaner.swap(listing, "id", lambda real: f"example-{cleaner.number('mc-id', real, 8)}")
        cleaner.swap(listing, "vdp_url", f"https://example.com/dealer{n}/listing/{vin}")
        cleaner.swap(listing, "source", "example.com")
        cleaner.swap(listing, "stock_no", lambda real: f"STOCK{cleaner.number('stock', real, 5)}")
        media = listing.get("media") or {}
        for key in ("photo_links", "photo_links_cached"):
            if media.get(key):
                cleaner.remember(*media[key])
                media[key] = [
                    f"https://example.com/photos/{vin}-{i}.jpg" for i in range(len(media[key]))
                ]
        for key in ("dealer", "mc_dealership", "car_location"):
            if isinstance(listing.get(key), dict):
                _clean_mc_place(listing[key], cleaner)
    out["listings"] = listings
    return out


def verify(fixture_text: str, cleaner: Cleaner) -> list[str]:
    problems = [
        f"original value survived: {v[:12]}…" for v in cleaner.originals if v in fixture_text
    ]
    if any(domain != "example.com" for domain in EMAIL.findall(fixture_text)):
        problems.append("an email address outside example.com")
    if re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", fixture_text):
        problems.append("something that looks like an IP address")
    if '"user"' in fixture_text:
        problems.append('a "user" section')
    return problems


CLEANERS = {"autodev": (clean_autodev, "data"), "marketcheck": (clean_marketcheck, "listings")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=CLEANERS)
    parser.add_argument("recording", type=Path)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--keep", type=int, default=3)
    args = parser.parse_args()

    clean, list_key = CLEANERS[args.source]
    recording = json.loads(args.recording.read_text())
    cleaner = Cleaner()
    response = clean(recording["response"], args.keep, cleaner)
    kept = len(response[list_key])
    fixture = {
        "note": (
            f"Real {args.source} response shape, trimmed to {kept} listings. Every value that "
            "could identify a car, dealer or person is replaced with an obvious fake (VINs "
            "starting with 0, Example Motors, 555 phones, example.com). Car details are real."
        ),
        "request": recording["request"],
        "recorded_at": recording["recorded_at"],
        "response": response,
    }
    text = json.dumps(fixture, indent=2, sort_keys=True) + "\n"

    problems = verify(text, cleaner)
    if problems:
        print("NOT SAVED. " + "; ".join(sorted(set(problems))), file=sys.stderr)
        return 1
    args.fixture.parent.mkdir(parents=True, exist_ok=True)
    args.fixture.write_text(text)
    print(f"Saved {args.fixture}: {kept} listings, verified clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
