FROM python:3.12-slim

WORKDIR /app

# Install system deps for psycopg2 and playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install playwright browsers
RUN playwright install chromium --with-deps

COPY . .

CMD ["python", "-m", "src.scheduler"]
