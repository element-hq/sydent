# This Dockerfile installs Sydent from source, which is assumed to be in the current
# working directory. The resulting image contains a single "sydent" user, and populates
# their home area with a "venv" directory that holds Sydent and its dependencies, and
# a "src" directory that holds the "res" and "scripts" directories. The entrypoint
# runs Sydent, listening on port 8090.
#
# Users must provide a persistent volume available to the container as `/data`. This
# will contain Sydent's configuration and database. A blank configuration and database
# file is created the first time Sydent runs.

# Dependabot only updates image tags in FROM lines, so uv gets a stage of its own.
FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

# Step 1: build the virtualenv
FROM docker.io/python:3.15-slim-trixie AS builder

COPY --from=uv /uv /usr/local/bin/uv

# Build the virtualenv with the base image's Python, which the runtime image
# also provides.
ENV UV_PYTHON_DOWNLOADS=never
ENV UV_PROJECT_ENVIRONMENT=/home/sydent/venv
ENV UV_NO_DEV=1
# The cache mount and the virtualenv are on different filesystems, so uv cannot
# hardlink between them.
ENV UV_LINK_MODE=copy

WORKDIR /home/sydent/src

# Copy the files that the build of Sydent's wheel reads
COPY ["sydent", "sydent"]
COPY ["README.rst", "pyproject.toml", "uv.lock", "LICENSE-AGPL-3.0", "LICENSE-COMMERCIAL", "./"]
# uv needs every workspace member's metadata to check uv.lock, even though the
# image does not install the integration tests.
COPY ["integration-tests/pyproject.toml", "integration-tests/"]

# Install Sydent and its dependencies into the virtualenv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --extra prometheus --extra sentry

# Step 2: Create runtime image
FROM docker.io/python:3.15-slim-trixie

# Add user sydent and create /data directory. Debian creates home directories
# with mode 0700, which would stop a container started with another user ID
# (`docker run --user`) from reading the virtualenv.
RUN addgroup --system --gid 993 sydent \
    && useradd -m -K HOME_MODE=0755 --system --uid 993 -g sydent sydent \
    && mkdir /data \
    && chown sydent:sydent /data

# Copy the resources, the scripts and the virtualenv
COPY ["res", "/home/sydent/src/res"]
COPY ["scripts", "/home/sydent/src/scripts"]
COPY --from=builder ["/home/sydent/venv", "/home/sydent/venv"]

ENV SYDENT_CONF=/data/sydent.conf
ENV SYDENT_PID_FILE=/data/sydent.pid
ENV SYDENT_DB_PATH=/data/sydent.db

WORKDIR /home/sydent
USER sydent:sydent
VOLUME ["/data"]
EXPOSE 8090/tcp
CMD [ "venv/bin/python", "-m", "sydent.sydent" ]
