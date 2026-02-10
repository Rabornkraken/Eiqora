#!/bin/bash
# Eiqora Frontend Deployment
# Deploys frontend to Azure Static Web Apps

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

echo -e "${GREEN}=== Eiqora Frontend Deployment ===${NC}"
echo ""

# Navigate to frontend directory
cd "$(dirname "$0")/../eiqora_v2_frontend"

# Get API URL from config or environment
if [ -n "$API_URL" ]; then
    API_URL="$API_URL"
else
    API_URL=$(grep "API_URL=" ../.azure-config | cut -d'=' -f2)
fi

if [ -z "$API_URL" ]; then
    echo -e "${YELLOW}API URL not found in config. Please enter the API URL:${NC}"
    read API_URL
fi

echo "API URL: $API_URL"
echo ""

# Update the staticwebapp.config.json with actual API URL
echo -e "${YELLOW}Updating frontend config with API URL...${NC}"
cat > staticwebapp.config.json <<EOF
{
  "routes": [
    {
      "route": "/api/*",
      "rewrite": "$API_URL/api/*"
    }
  ],
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/api/*", "*.{css,js,svg,png,jpg,ico}"]
  },
  "globalHeaders": {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY"
  }
}
EOF
echo -e "${GREEN}✓ Config updated${NC}"
echo ""

# Install dependencies and build
echo -e "${YELLOW}Installing dependencies...${NC}"
npm install

echo -e "${YELLOW}Building frontend...${NC}"
VITE_API_URL="$API_URL" VITE_API_VERSION=local npm run build
echo -e "${GREEN}✓ Build complete${NC}"
echo ""

# Deploy to Azure Storage Static Website (SWA regions are policy-restricted)
echo -e "${YELLOW}Deploying to Azure Storage Static Website...${NC}"
ACCOUNT_KEY=$(az storage account keys list \
    --resource-group $RESOURCE_GROUP \
    --account-name $STORAGE_ACCOUNT \
    --query "[0].value" -o tsv)

az storage blob service-properties update \
    --account-name $STORAGE_ACCOUNT \
    --account-key "$ACCOUNT_KEY" \
    --static-website \
    --index-document index.html \
    --404-document index.html \
    --output none

az storage blob upload-batch \
    --account-name $STORAGE_ACCOUNT \
    --account-key "$ACCOUNT_KEY" \
    --destination '$web' \
    --source ./dist \
    --overwrite

FRONTEND_URL=$(az storage account show \
    --resource-group $RESOURCE_GROUP \
    --name $STORAGE_ACCOUNT \
    --query "primaryEndpoints.web" -o tsv)

echo ""
echo -e "${GREEN}=== Frontend Deployment Complete ===${NC}"
echo ""
echo -e "Frontend URL: ${GREEN}$FRONTEND_URL${NC}"
echo ""
echo "Your Eiqora deployment is complete!"
echo ""
echo "URLs:"
echo "  - Frontend: $FRONTEND_URL"
echo "  - API: $API_URL"
