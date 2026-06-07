# syntax=docker/dockerfile:1
# SPDX-License-Identifier: AGPL-3.0-or-later

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build tools (stay in builder stage only)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy only what pip needs to resolve and install the package
COPY pyproject.toml ./
COPY revok/ ./revok/

# Install into an isolated prefix so we can copy it cleanly to the runtime stage
RUN pip install --no-cache-dir --prefix=/install .


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# No build tools — only the Python stdlib + our installed package

# Copy installed package tree from builder
COPY --from=builder /install /usr/local

# Non-root user for security
RUN useradd --no-create-home --shell /bin/false revok
USER revok

# Config is supplied via a bind-mount or volume at runtime
VOLUME ["/config"]

EXPOSE 7771

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c \
        "import urllib.request, sys; \
         r = urllib.request.urlopen('http://localhost:7771/health', timeout=4); \
         sys.exit(0 if r.status == 200 else 1)"

CMD ["python", "-m", "revok", "--config", "/config/revok.yaml"]
