#!/bin/bash
# Eiqora Docker Build and Push
# Builds images and pushes to Azure Container Registry

set -e

# Load config
if [ -f .azure-config ]; then
    source .azure-config
else
    echo "Error: .azure-config not found. Run 01-setup-infrastructure.sh first."
    exit 1
fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Navigate to project root
cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

echo -e "${GREEN}=== Eiqora Docker Build & Push ===${NC}"
echo "Project root: $PROJECT_ROOT"
echo ""

# Login to ACR
echo -e "${YELLOW}Logging in to Azure Container Registry...${NC}"
az acr login --name $ACR_NAME
echo -e "${GREEN}✓ Logged in to $ACR_NAME${NC}"
echo ""

# Build and push API image (v2 API) for linux/amd64
echo -e "${YELLOW}Building & pushing API image (v2 API, linux/amd64)...${NC}"
docker buildx build --platform linux/amd64 \
  -t $ACR_NAME.azurecr.io/eiqora-api:latest \
  -f eiqora_v2/Dockerfile \
  --push .
echo -e "${GREEN}✓ API image built & pushed${NC}"
echo ""

# Build and push Data Scheduler image for linux/amd64
echo -e "${YELLOW}Building & pushing Data Scheduler image (linux/amd64)...${NC}"
docker buildx build --platform linux/amd64 \
  -t $ACR_NAME.azurecr.io/eiqora-data-scheduler:latest \
  -f data_collection/Dockerfile \
  --push .
echo -e "${GREEN}✓ Data Scheduler image built & pushed${NC}"
echo ""

# Build and push Live Scheduler image for linux/amd64
echo -e "${YELLOW}Building & pushing Live Scheduler image (linux/amd64)...${NC}"
docker buildx build --platform linux/amd64 \
  -t $ACR_NAME.azurecr.io/eiqora-live-scheduler:latest \
  -f eiqora_v2/live/Dockerfile \
  --push .
echo -e "${GREEN}✓ Live Scheduler image built & pushed${NC}"
echo ""

# Build and push Telegram Bot image for linux/amd64
echo -e "${YELLOW}Building & pushing Telegram Bot image (linux/amd64)...${NC}"
docker buildx build --platform linux/amd64 \
  -t $ACR_NAME.azurecr.io/eiqora-telegram-bot:latest \
  -f eiqora_v2/telegram/Dockerfile \
  --push .
echo -e "${GREEN}✓ Telegram Bot image built & pushed${NC}"
echo ""

echo -e "${GREEN}=== Build & Push Complete ===${NC}"
echo ""
echo "Images pushed to $ACR_NAME.azurecr.io:"
echo "  - eiqora-api:latest"
echo "  - eiqora-data-scheduler:latest"
echo "  - eiqora-live-scheduler:latest"
echo "  - eiqora-telegram-bot:latest"
echo ""
echo -e "${YELLOW}Next step:${NC} Run ./04-deploy-apps.sh"
