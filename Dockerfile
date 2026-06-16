FROM python:3.12-slim

WORKDIR /app

# Build deps (psycopg2) + Chromium runtime libs (Debian names — Playwright's
# --with-deps assumes Ubuntu and fails on python:slim, so we install them here).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 \
    libatspi2.0-0 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
    libgbm1 libxcb1 libxkbcommon0 libpango-1.0-0 libcairo2 libasound2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the Chromium browser binary (no --with-deps; libs installed above)
RUN playwright install chromium

COPY . .

CMD ["python", "-m", "src.cron"]
