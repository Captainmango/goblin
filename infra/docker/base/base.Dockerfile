FROM ghcr.io/astral-sh/uv:python3.13-alpine3.23

# Prevent Python from writing pyc files and keep stdout/stderr unbuffered.
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Use the Python version already shipped in this image and compile bytecode for
# faster startup.
ENV UV_PYTHON_DOWNLOADS=0
ENV UV_COMPILE_BYTECODE=1

WORKDIR /app

# Install Alpine build tooling commonly required to compile Python dependencies
# that do not ship musllinux wheels. This is a *build* base image, so keeping
# these installed keeps downstream project builds stable.
RUN apk add --no-cache \
    build-base \
    libffi-dev \
    openssl-dev \
    rust \
    cargo

# Copy the workspace manifest, shared lockfile, and each workspace member's
# project metadata. No source code is copied here.
COPY pyproject.toml uv.lock ./
COPY goblin/pyproject.toml ./goblin/
COPY goblin-common/pyproject.toml ./goblin-common/
COPY notifications-relay/pyproject.toml ./notifications-relay/

# Install all third-party dependencies declared across the workspace, but do not
# build or install the workspace packages themselves. Those will be layered on
# top of this base image in per-project Dockerfiles.
RUN uv sync --frozen --all-packages --no-install-workspace --no-dev
