#!/bin/bash
# Eiqora Data Collection Server - Start Script

set -e

cd "$(dirname "$0")"

echo "==================================="
echo "Eiqora Data Collection Server"
echo "==================================="

# Check for .env file
if [ ! -f config/.env ]; then
    echo "❌ Error: config/.env not found"
    echo "   Create it with POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB"
    exit 1
fi

# Load environment
set -a
source config/.env
set +a

# Build and start services
echo "🔧 Building Docker images..."
docker-compose build

echo "🚀 Starting services..."
docker-compose up -d

echo ""
echo "✅ Services started!"
echo ""
echo "📊 Check status:   docker-compose ps"
echo "📜 View logs:      docker-compose logs -f scheduler"
echo "🛑 Stop:           docker-compose down"
echo ""

# Show status
docker-compose ps
