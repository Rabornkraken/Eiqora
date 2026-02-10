# Eiqora Data Collection Scheduler
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY data_collection/requirements.txt /app/data_collection/
RUN pip install --no-cache-dir -r data_collection/requirements.txt

# Install Playwright browsers (optional - for corporate actions)
RUN pip install playwright && playwright install chromium --with-deps || true

# Copy application code
COPY data_collection/ /app/data_collection/
COPY eiqora_v2/ /app/eiqora_v2/

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV TZ=America/New_York

# Run the scheduler
CMD ["python", "-m", "data_collection.scheduler"]
