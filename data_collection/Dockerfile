# Eiqora Data Collection Scheduler
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (optional - for corporate actions)
RUN pip install playwright && playwright install chromium --with-deps || true

# Copy application code
COPY . /app/

# Set timezone
ENV TZ=America/New_York

# Run the scheduler
CMD ["python", "-m", "data_collection.scheduler"]
