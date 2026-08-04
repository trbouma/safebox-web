# syntax=docker/dockerfile:1

FROM python:3.11-slim-bookworm AS builder

ARG POETRY_VERSION=1.8.2

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=1

# Git is required for the safebox-acorn GitHub dependency. The remaining
# packages support native-wheel fallbacks on architectures where a wheel is
# not published, notably secp256k1 on Linux arm64.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        autoconf \
        automake \
        build-essential \
        git \
        libffi-dev \
        libtool \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

# safebox-acorn is declared as a GitHub dependency in pyproject.toml and pinned
# to a resolved Git commit by poetry.lock. Copying these files first preserves
# Docker's dependency cache until either dependency definition changes.
COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root --no-ansi


FROM python:3.11-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FORWARDED_ALLOW_IPS=127.0.0.1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 safebox \
    && useradd --uid 10001 --gid safebox --create-home --shell /usr/sbin/nologin safebox \
    && install -d -o safebox -g safebox /app/data

WORKDIR /app

COPY --from=builder --chown=safebox:safebox /app/.venv /app/.venv
COPY --chown=safebox:safebox app /app/app
COPY --chown=safebox:safebox alembic /app/alembic
COPY --chown=safebox:safebox alembic.ini /app/alembic.ini

USER safebox

EXPOSE 8000

# This internal loopback request is the only plain-HTTP case accepted by the
# application. It does not weaken the HTTPS requirement for external clients.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

# A production reverse proxy must terminate TLS, set X-Forwarded-Proto=https,
# and connect from an address listed in FORWARDED_ALLOW_IPS.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
