"""Refuse to commit or push anything secret or identifying. The repo is public.

    python scripts/leak_check.py            # check what is staged (used by the pre-commit hook)
    python scripts/leak_check.py --history  # check every commit on main (run before pushing)

Exits 1 and lists problems if anything is found. Secret values are never printed.
"""

import re
import subprocess
import sys
from pathlib import Path

ENV_SECRETS = ("POSTGRES_PASSWORD", "AUTODEV_API_KEY", "MARKETCHECK_API_KEY")
FORBIDDEN_PATHS = re.compile(r"^(\.env|recordings/.*)$")
KEY_PATTERN = re.compile(
    # [ \t]* not \s*: a value must be on the same line ("NAME=\nNEXT_NAME=" is not a key)
    r"(api[_-]?key|secret|token|password|bearer)[ \t]*[:=][ \t]*[\"']?[A-Za-z0-9_\-]{12,}"
    r"|postgres(ql)?(\+psycopg)?://[^:<\s]+:[^@<\s]+@",
    re.IGNORECASE,
)
# The fake values the tests use on purpose to prove real keys never leak
TEST_FAKES = re.compile(r"SECRET-|fake-")

FIXTURES = "tests/fixtures/"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
IP = re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b")
VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
# Fakes start with 0; the other two are the published example VIN and its invalid twin
ALLOWED_VINS = re.compile(r"^0FAKE|^1HGCM82633A00435[23]$")
URL_HOST = re.compile(r"https?://([^/\"'\s]+)")
ALLOWED_HOSTS = {
    "example.com",
    "api.auto.dev",
    "auto.dev",
    "docs.auto.dev",
    "api.marketcheck.com",
    "www.safercar.gov",
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def env_secrets() -> dict[str, str]:
    values = {}
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            name, _, value = line.partition("=")
            if name in ENV_SECRETS and value.strip():
                values[name] = value.strip()
    return values


def check_file(path: str, content: str, secrets: dict[str, str]) -> list[str]:
    problems = []
    if FORBIDDEN_PATHS.match(path):
        return [f"{path}: this file must never be committed"]
    for name, value in secrets.items():
        if value in content:
            problems.append(f"{path}: contains the real {name}")
    if path != "uv.lock":
        for match in KEY_PATTERN.finditer(content):
            if not TEST_FAKES.search(match.group(0)):
                problems.append(f"{path}: key-like text ({match.group(0)[:20]}…)")
    if path.startswith(FIXTURES):
        if EMAIL.search(content):
            problems.append(f"{path}: an email address")
        if IP.search(content):
            problems.append(f"{path}: an IP address")
        for vin in set(VIN.findall(content)):
            if not ALLOWED_VINS.match(vin):
                problems.append(f"{path}: a VIN that isn't a known fake or public example")
        for host in set(URL_HOST.findall(content)):
            if host not in ALLOWED_HOSTS:
                problems.append(f"{path}: a link to {host}")
    return problems


def staged_files() -> list[tuple[str, str]]:
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR").split()
    return [(n, git("show", f":{n}")) for n in names]


def history_files() -> list[tuple[str, str]]:
    files = []
    for commit in git("rev-list", "main").split():
        for name in git("ls-tree", "-r", "--name-only", commit).split("\n"):
            if name:
                files.append((f"{commit[:7]}:{name}", git("show", f"{commit}:{name}")))
        message = git("log", "-1", "--format=%B", commit)
        files.append((f"{commit[:7]}:<commit message>", message))
    return files


def main(argv: list[str]) -> int:
    secrets = env_secrets()
    history = "--history" in argv
    files = history_files() if history else staged_files()
    problems = []
    for name, content in files:
        path = name.split(":", 1)[1] if history else name
        problems += [p.replace(path, name, 1) for p in check_file(path, content, secrets)]
    scope = "every commit on main" if history else "staged files"
    if problems:
        print(f"LEAK CHECK FAILED ({scope}):", file=sys.stderr)
        for p in sorted(set(problems)):
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"Leak check passed ({scope}: {len(files)} files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
