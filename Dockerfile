FROM python:3.12-slim

# uv, pinned to the same version used locally
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

WORKDIR /app

# Put the virtual environment outside /app so it is never mixed up with a local .venv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

# Install dependencies first (cached until pyproject.toml or uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Then the project itself
COPY src ./src
COPY tests ./tests
RUN uv sync --frozen

CMD ["uvicorn", "lotwatcher.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
