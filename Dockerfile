# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.14

# ---- build stage -----------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ARG POETRY_VERSION=2.3.2
ENV POETRY_HOME=/opt/poetry \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# compiler toolchain for pytsk3's bundled Sleuth Kit build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$POETRY_HOME" \
 && "$POETRY_HOME/bin/pip" install "poetry==$POETRY_VERSION"
ENV PATH="$POETRY_HOME/bin:$PATH"

WORKDIR /app

# dependencies first so they're cached independently of source changes
COPY pyproject.toml poetry.lock ./
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    poetry install --only main --no-root

# then the project itself
COPY . .
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    poetry install --only main

# ---- runtime stage ---------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# libudev1: loaded by pyudev via ctypes
# libstdc++6: needed by pytsk3's compiled extension
# tini: runs as PID 1 so SIGTERM reaches f5 and stops it promptly
RUN apt-get update \
 && apt-get install -y --no-install-recommends libudev1 libstdc++6 tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["/usr/bin/tini", "--", "f5"]
