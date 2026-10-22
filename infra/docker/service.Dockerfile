# syntax=docker/dockerfile:1.7
#
# One multi-stage build for every Python service. Each image contains exactly one
# workspace package and its locked dependencies, nothing else: no tests, no fixtures,
# no build tools, no .env.
#
#   docker build -f infra/docker/service.Dockerfile \
#     --build-arg SERVICE=signal-engine --build-arg MODULE=alphadots_signal_engine .
#
# Tags are pinned to exact versions here. Pinning by digest and signing release
# artifacts is part of the release policy that arrives with the CI/CD milestone (M5).

ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm
# Docker Hub mirror of ghcr.io/astral-sh/uv (same image, published by Astral).
ARG UV_IMAGE=astral/uv:0.9.21

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------- build
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/ services/
ARG SERVICE
# --frozen: fail rather than re-resolve if uv.lock is stale.
# --no-editable: install workspace packages as wheels, so the runtime stage needs no source.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package "alphadots-${SERVICE}"

# -------------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime
ARG MODULE
RUN groupadd --system --gid 10001 alphadots \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin alphadots
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ALPHADOTS_MODULE=${MODULE}
WORKDIR /app
USER 10001:10001
# `exec` makes Python PID 1, so SIGTERM reaches it and shutdown is graceful.
ENTRYPOINT ["/bin/sh", "-c", "exec python -m \"$ALPHADOTS_MODULE\" \"$@\"", "--"]
