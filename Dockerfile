# This Dockerfile installs Sydent from source, which is assumed to be in the current
# working directory. The resulting image contains a single "sydent" user, and populates
# their home area with "src" and "venv" directories. The entrypoint runs Sydent,
# listening on port 8090.
#
# Users must provide a persistent volume available to the container as `/data`. This
# will contain Sydent's configuration and database. A blank configuration and database
# file is created the first time Sydent runs.

# Dependabot only updates image tags in FROM lines, so uv gets a stage of its own.
FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

# Step 1: install dependencies
FROM docker.io/python:3.15-slim-trixie AS builder

# Add user sydent
RUN addgroup --system --gid 993 sydent \
    && useradd -m --system --uid 993 -g sydent sydent
USER sydent:sydent

COPY --from=uv /uv /usr/local/bin/uv

# Build the virtualenv with the base image's Python, which the runtime image
# also provides.
ENV UV_PYTHON_DOWNLOADS=never
ENV UV_PROJECT_ENVIRONMENT=/home/sydent/venv

# Copy source code and resources
WORKDIR /home/sydent/src
COPY --chown=sydent:sydent ["res", "res"]
COPY --chown=sydent:sydent ["scripts", "scripts"]
COPY --chown=sydent:sydent ["sydent", "sydent"]
COPY --chown=sydent:sydent ["README.rst", "pyproject.toml", "uv.lock", "./"]

# Install dependencies
RUN uv sync --locked --no-dev --extra prometheus --extra sentry

# Record dependencies for posterity
RUN uv export --locked --no-dev --extra prometheus --extra sentry -o requirements.txt

# Step 2: Create runtime image
FROM docker.io/python:3.15-slim-trixie

# Add user sydent and create /data directory. Debian creates home directories
# with mode 0700, which would stop a container started with another user ID
# (`docker run --user`) from reading the virtualenv.
RUN addgroup --system --gid 993 sydent \
    && useradd -m -K HOME_MODE=0755 --system --uid 993 -g sydent sydent \
    && mkdir /data \
    && chown sydent:sydent /data

# Copy sydent and the virtualenv
COPY --from=builder ["/home/sydent/src", "/home/sydent/src"]
COPY --from=builder ["/home/sydent/venv", "/home/sydent/venv"]

ENV SYDENT_CONF=/data/sydent.conf
ENV SYDENT_PID_FILE=/data/sydent.pid
ENV SYDENT_DB_PATH=/data/sydent.db

WORKDIR /home/sydent
USER sydent:sydent
VOLUME ["/data"]
EXPOSE 8090/tcp
CMD [ "venv/bin/python", "-m", "sydent.sydent" ]
