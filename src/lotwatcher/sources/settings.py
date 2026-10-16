"""Connection settings for each data source, and a helper that builds its client.

Limits come from each provider's docs (checked 2026-10-15; see docs/decisions.md, step 4).
API keys are read from environment variables, never written here.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import Connection, Engine

from lotwatcher.sources.client import SourceClient


class MissingApiKey(Exception):
    """A source needs an API key, but its environment variable is empty or missing."""


@dataclass(frozen=True)
class SourceSettings:
    name: str
    base_url: str
    calls_per_second: float
    monthly_cap: int | None
    # Environment variable holding the API key, if the source needs one
    key_env: str | None = None
    # Where the key goes: a header (with an optional prefix) or a query parameter
    key_header: str | None = None
    key_header_prefix: str = ""
    key_param: str | None = None


AUTODEV = SourceSettings(
    name="autodev",
    base_url="https://api.auto.dev",
    calls_per_second=5,
    monthly_cap=1000,
    key_env="AUTODEV_API_KEY",
    key_header="Authorization",
    key_header_prefix="Bearer ",
)

MARKETCHECK = SourceSettings(
    name="marketcheck",
    base_url="https://api.marketcheck.com",
    calls_per_second=5,
    monthly_cap=500,
    key_env="MARKETCHECK_API_KEY",
    key_param="api_key",
)

# NHTSA publishes no rate number, so pace cautiously
NHTSA = SourceSettings(
    name="nhtsa", base_url="https://api.nhtsa.gov", calls_per_second=1, monthly_cap=None
)

VPIC = SourceSettings(
    name="vpic", base_url="https://vpic.nhtsa.dot.gov/api", calls_per_second=1, monthly_cap=None
)


def build_client(
    settings: SourceSettings,
    bind: Engine | Connection,
    *,
    environ: Mapping[str, str] = os.environ,
    **overrides,
) -> SourceClient:
    """A SourceClient for this source, with its API key put where the provider expects it."""
    secret_headers: dict[str, str] = {}
    secret_params: dict[str, str] = {}
    if settings.key_env is not None:
        key = environ.get(settings.key_env, "").strip()
        if not key:
            raise MissingApiKey(f"{settings.name} needs {settings.key_env} set in .env")
        if settings.key_header is not None:
            secret_headers[settings.key_header] = settings.key_header_prefix + key
        if settings.key_param is not None:
            secret_params[settings.key_param] = key

    return SourceClient(
        source=settings.name,
        base_url=settings.base_url,
        bind=bind,
        calls_per_second=settings.calls_per_second,
        monthly_cap=settings.monthly_cap,
        secret_headers=secret_headers,
        secret_params=secret_params,
        **overrides,
    )
