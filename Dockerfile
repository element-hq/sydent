ARG PYTHON_VERSION=3.13
ARG DEBIAN_VERSION=trixie

# Stage 1: Build
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-${DEBIAN_VERSION}-slim AS builder

ENV UV_PYTHON_DOWNLOADS=0
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

# Step 1: Install dependencies only (cached unless pyproject.toml/uv.lock change)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev --extra sentry --extra prometheus

# Step 2: Copy source and install the project
COPY . /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra sentry --extra prometheus

# Stage 2: Runtime
FROM docker.io/library/python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION}

# Create sydent user and data directory
RUN addgroup --system --gid 993 sydent \
    && useradd -m --system --uid 993 -g sydent sydent \
    && mkdir /data \
    && chown sydent:sydent /data

# Copy the virtualenv from builder
COPY --from=builder /app/.venv /app/.venv
# Copy resources needed at runtime
COPY --from=builder /app/res /app/res

ENV PATH="/app/.venv/bin:$PATH"
ENV SYDENT_CONF=/data/sydent.conf
ENV SYDENT_PID_FILE=/data/sydent.pid
ENV SYDENT_DB_PATH=/data/sydent.db

WORKDIR /app
USER sydent:sydent
VOLUME ["/data"]
EXPOSE 8090/tcp
CMD ["sydent"]
