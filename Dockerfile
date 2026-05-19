# ============================================================
# Saihawk — Production Dockerfile
# Target: Oracle Always Free Ampere A1 (linux/arm64 / aarch64)
# ============================================================
#
# Build (from Windows/Mac dev machine):
#   docker buildx build --platform linux/arm64 -t saihawk:latest --push .
#
# Run (on Oracle instance):
#   docker run --env-file .env saihawk:latest
#
# NOTE: mcr.microsoft.com/playwright/python images ship with all
# Chromium system dependencies pre-installed and support multi-arch
# (including arm64), so NO manual apt-get installs are needed.
# ============================================================

FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

# Metadata
LABEL maintainer="Sehaan-1" \
      description="Saihawk — Autonomous multi-platform internship application bot" \
      architecture="linux/arm64"

# Set working directory
WORKDIR /app

# ---- Python dependencies ----
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---- Playwright browser binaries ----
# The base image ships with Chromium, but we re-run install to ensure
# the correct version matches the Playwright SDK installed above.
RUN playwright install chromium

# ---- Application code ----
COPY . .

# ---- Runtime configuration ----
# All secrets are injected at runtime via --env-file, never baked in.
ENV PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO \
    PLAYWRIGHT_HEADLESS=true

# ---- Entrypoint ----
# Runs the main orchestrator. Modify src/main.py to set target URLs.
CMD ["python", "src/main.py"]
