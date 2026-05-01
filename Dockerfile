# ──────────────────────────────────────────────────────────────────────────────
# NITS Arena – Multi-stage Dockerfile
# ──────────────────────────────────────────────────────────────────────────────

# ── Stage 1: build/dependency layer ──────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ───────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Install only runtime shared libraries (libpq for asyncpg)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY bot.py .
COPY cogs/ ./cogs/
COPY database/ ./database/
COPY utils/ ./utils/

# Create unprivileged user
RUN useradd --no-create-home --shell /bin/false appuser
USER appuser

# Healthcheck (optional; requires the bot to have a health endpoint)
# HEALTHCHECK --interval=30s --timeout=10s CMD python -c "import sys; sys.exit(0)"

CMD ["python", "bot.py"]
