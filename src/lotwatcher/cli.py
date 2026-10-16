"""Command-line tools. Run inside the api container, e.g.

    docker compose run --rm api python -m lotwatcher.cli add-search --email you@example.com \\
        --home-zip 75201 --name "RAV4" --make Toyota --model RAV4 --radius 50 \\
        --year-min 2019 --year-max 2022 --max-price 30000 --max-mileage 60000
    docker compose run --rm api python -m lotwatcher.cli list-searches
    docker compose run --rm api python -m lotwatcher.cli pause-search 1     (resume-search 1)
    docker compose run --rm api python -m lotwatcher.cli run-now daily      (or backfill)
    docker compose run --rm api python -m lotwatcher.cli status

Saved searches live only in your local database, never in git.
"""

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from lotwatcher.db.models import ApiCallCount, Job, SavedSearch, User
from lotwatcher.db.session import get_engine, transaction
from lotwatcher.worker import JOBS, build_source_clients


def add_search(
    session: Session,
    *,
    email: str,
    home_zip: str,
    name: str,
    make: str,
    model: str,
    radius_miles: int,
    year_min: int | None = None,
    year_max: int | None = None,
    max_price: int | None = None,
    max_mileage: int | None = None,
) -> int:
    """Add a saved search, creating the user on first use (one user in version 1)."""
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, home_zip=home_zip)
        session.add(user)
    user.home_zip = home_zip
    session.flush()
    search = SavedSearch(
        user_id=user.id,
        name=name,
        make=make,
        model=model,
        radius_miles=radius_miles,
        year_min=year_min,
        year_max=year_max,
        max_price=max_price,
        max_mileage=max_mileage,
    )
    session.add(search)
    session.flush()
    return search.id


def set_search_active(session: Session, search_id: int, active: bool) -> None:
    search = session.get(SavedSearch, search_id)
    if search is None:
        raise SystemExit(f"No saved search {search_id}")
    search.active = active


def _print_searches(session: Session) -> None:
    for s in session.scalars(select(SavedSearch).order_by(SavedSearch.id)):
        years = f"{s.year_min or ''}-{s.year_max or ''}"
        print(
            f"{s.id}: {s.name} | {s.make} {s.model} {years} | max ${s.max_price or '-'} "
            f"| max {s.max_mileage or '-'} mi | {s.radius_miles} mi | "
            f"{'active' if s.active else 'paused'}"
        )


def _print_status(session: Session) -> None:
    month = datetime.now(UTC).date().replace(day=1)
    print("API calls this month:")
    for row in session.scalars(select(ApiCallCount).where(ApiCallCount.month == month)):
        print(f"  {row.source}: {row.calls}")
    print("Recent jobs:")
    for job in session.scalars(select(Job).order_by(Job.id.desc()).limit(10)):
        print(f"  {job.key}: {job.status} (attempts {job.attempts}) {job.error or ''}")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="lotwatcher")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add-search")
    add.add_argument("--email", required=True)
    add.add_argument("--home-zip", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--make", required=True)
    add.add_argument("--model", required=True)
    add.add_argument("--radius", type=int, required=True)
    for flag in ("--year-min", "--year-max", "--max-price", "--max-mileage"):
        add.add_argument(flag, type=int)
    commands.add_parser("list-searches")
    for name in ("pause-search", "resume-search"):
        commands.add_parser(name).add_argument("search_id", type=int)
    commands.add_parser("run-now").add_argument("job", choices=sorted(JOBS))
    commands.add_parser("status")
    args = parser.parse_args(argv)

    engine = get_engine()
    if args.command == "run-now":
        # Runs in the foreground now (safe to repeat: everything is keyed by day)
        timezone = ZoneInfo(os.environ.get("LOTWATCHER_TIMEZONE") or "UTC")
        today = datetime.now(timezone).date()
        result = JOBS[args.job](engine, today, build_source_clients(engine))
        print(json.dumps(result, indent=2))
        return

    with transaction(engine) as session:
        if args.command == "add-search":
            search_id = add_search(
                session,
                email=args.email,
                home_zip=args.home_zip,
                name=args.name,
                make=args.make,
                model=args.model,
                radius_miles=args.radius,
                year_min=args.year_min,
                year_max=args.year_max,
                max_price=args.max_price,
                max_mileage=args.max_mileage,
            )
            print(f"Added saved search {search_id}")
        elif args.command == "list-searches":
            _print_searches(session)
        elif args.command in ("pause-search", "resume-search"):
            set_search_active(session, args.search_id, args.command == "resume-search")
            print(f"Search {args.search_id} {args.command.split('-')[0]}d")
        elif args.command == "status":
            _print_status(session)


if __name__ == "__main__":
    main()
