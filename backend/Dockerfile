FROM python:3.12-slim AS builder

ENV UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN python -m venv /opt/venv && \
    pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY app ./app
RUN uv sync --frozen --no-dev --active && \
    find /opt/venv -type d -name "__pycache__" -prune -exec rm -rf {} + && \
    find /opt/venv -type f -name "*.pyc" -delete && \
    rm -rf /opt/venv/lib/python*/site-packages/pip*

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/* /usr/share/locale/*

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
