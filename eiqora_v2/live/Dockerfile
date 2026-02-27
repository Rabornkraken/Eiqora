# Eiqora Live Trading Scheduler
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements-live.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements-live.txt

# Copy application code
COPY eiqora_v2/ ./eiqora_v2/
COPY data_collection/ ./data_collection/

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV TZ=America/New_York

# Run live pipeline (trigger dispatcher + polling + account refresh)
CMD ["python", "-m", "eiqora_v2.live.pipeline"]
