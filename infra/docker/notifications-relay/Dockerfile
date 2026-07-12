# syntax=docker/dockerfile:1

# Build stage: use the local dependency+build base image.
# This image already contains the workspace lockfile, project metadata, and all
# third-party dependencies, so only workspace source needs to be added.
FROM jp-goblin:latest AS builder

# Copy workspace source code and package metadata on top of the dependency-only
# base image. README.md is required by hatchling during wheel builds.
COPY goblin-common/README.md ./goblin-common/
COPY goblin-common/goblin_common ./goblin-common/goblin_common
COPY notifications-relay/README.md ./notifications-relay/
COPY notifications-relay/notifications_relay ./notifications-relay/notifications_relay

# Build wheels for the workspace packages this project depends on.
RUN uv build --package goblin-common --wheel --out-dir /dist \
    && uv build --package notifications-relay --wheel --out-dir /dist

# Install the wheels into the base image's already-populated virtual environment.
# --no-deps is safe because the base image already installed all third-party deps.
RUN uv pip install --no-deps /dist/*.whl

# Runtime stage: use the same upstream slim base as jp-goblin:latest, without the
# build tooling installed in the local base image.
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install runtime libraries required by compiled Python extensions in the venv.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        libffi8 \
        libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Copy the fully prepared virtual environment from the builder.
COPY --from=builder /app/.venv ./.venv
ENV PATH="/app/.venv/bin:$PATH"

# Default to running the relay HTTP server.
CMD ["notifications-relay", "serve"]
