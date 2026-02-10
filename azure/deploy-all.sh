#!/bin/bash
# Eiqora Full Azure Deployment - Non-interactive
set -e

# Configuration
RESOURCE_GROUP="eiqora-rg"
LOCATION="southeastasia"
ACR_NAME="eiqoraacr$(openssl rand -hex 4)"  # Unique name
KEYVAULT_NAME="eiqora-kv-$(openssl rand -hex 4)"  # Unique name
POSTGRES_SERVER="eiqora-pg-$(openssl rand -hex 4)"  # Unique name
POSTGRES_ADMIN="eiqoraadmin"
POSTGRES_PASSWORD="Eiqora2024Secure!"
CONTAINER_ENV="eiqora-env"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

cd "$(dirname "$0")"
PROJECT_ROOT="$(cd .. && pwd)"

echo -e "${GREEN}=== Eiqora Full Azure Deployment ===${NC}"
echo "Project root: $PROJECT_ROOT"
echo ""

# Check Azure login
echo -e "${YELLOW}Checking Azure CLI login...${NC}"
az account show > /dev/null 2>&1 || { echo -e "${RED}Please login with 'az login' first${NC}"; exit 1; }
SUBSCRIPTION=$(az account show --query name -o tsv)
echo -e "${GREEN}✓ Using subscription: $SUBSCRIPTION${NC}"
echo ""

# ============================================
# PHASE 1: Create Infrastructure
# ============================================
echo -e "${GREEN}=== PHASE 1: Creating Infrastructure ===${NC}"

# Resource Group
echo -e "${YELLOW}Creating Resource Group...${NC}"
az group create --name $RESOURCE_GROUP --location $LOCATION --output none
echo -e "${GREEN}✓ Resource Group: $RESOURCE_GROUP${NC}"

# Container Registry
echo -e "${YELLOW}Creating Container Registry...${NC}"
az acr create \
    --resource-group $RESOURCE_GROUP \
    --name $ACR_NAME \
    --sku Basic \
    --admin-enabled true \
    --output none
echo -e "${GREEN}✓ Container Registry: $ACR_NAME${NC}"

# Key Vault
echo -e "${YELLOW}Creating Key Vault...${NC}"
az keyvault create \
    --resource-group $RESOURCE_GROUP \
    --name $KEYVAULT_NAME \
    --location $LOCATION \
    --enable-rbac-authorization false \
    --output none
echo -e "${GREEN}✓ Key Vault: $KEYVAULT_NAME${NC}"

# PostgreSQL
echo -e "${YELLOW}Creating PostgreSQL (this takes 3-5 minutes)...${NC}"
az postgres flexible-server create \
    --resource-group $RESOURCE_GROUP \
    --name $POSTGRES_SERVER \
    --location $LOCATION \
    --sku-name Standard_B1ms \
    --tier Burstable \
    --storage-size 32 \
    --version 15 \
    --admin-user $POSTGRES_ADMIN \
    --admin-password "$POSTGRES_PASSWORD" \
    --yes \
    --output none

# Enable pgvector
az postgres flexible-server parameter set \
    --resource-group $RESOURCE_GROUP \
    --server-name $POSTGRES_SERVER \
    --name azure.extensions \
    --value vector \
    --output none

# Firewall - allow Azure services
az postgres flexible-server firewall-rule create \
    --resource-group $RESOURCE_GROUP \
    --name $POSTGRES_SERVER \
    --rule-name AllowAzureServices \
    --start-ip-address 0.0.0.0 \
    --end-ip-address 0.0.0.0 \
    --output none

# Create database
az postgres flexible-server db create \
    --resource-group $RESOURCE_GROUP \
    --server-name $POSTGRES_SERVER \
    --database-name finance \
    --output none

echo -e "${GREEN}✓ PostgreSQL: $POSTGRES_SERVER${NC}"

# Container Apps Environment
echo -e "${YELLOW}Creating Container Apps Environment...${NC}"
az containerapp env create \
    --resource-group $RESOURCE_GROUP \
    --name $CONTAINER_ENV \
    --location $LOCATION \
    --output none
echo -e "${GREEN}✓ Container Apps Environment: $CONTAINER_ENV${NC}"

# ============================================
# PHASE 2: Store Secrets
# ============================================
echo -e "${GREEN}=== PHASE 2: Storing Secrets ===${NC}"

DATABASE_URL="postgresql://${POSTGRES_ADMIN}:${POSTGRES_PASSWORD}@${POSTGRES_SERVER}.postgres.database.azure.com:5432/finance?sslmode=require"

# Load secrets from .env
source "$PROJECT_ROOT/.env" 2>/dev/null || true

az keyvault secret set --vault-name $KEYVAULT_NAME --name "DATABASE-URL" --value "$DATABASE_URL" --output none
echo -e "${GREEN}✓ DATABASE-URL${NC}"

if [ -n "$OPENROUTER_API_KEY" ]; then
    az keyvault secret set --vault-name $KEYVAULT_NAME --name "OPENROUTER-API-KEY" --value "$OPENROUTER_API_KEY" --output none
    echo -e "${GREEN}✓ OPENROUTER-API-KEY${NC}"
fi

if [ -n "$ALPACA_API_KEY" ]; then
    az keyvault secret set --vault-name $KEYVAULT_NAME --name "ALPACA-API-KEY" --value "$ALPACA_API_KEY" --output none
    echo -e "${GREEN}✓ ALPACA-API-KEY${NC}"
fi

if [ -n "$ALPACA_API_SECRET" ]; then
    az keyvault secret set --vault-name $KEYVAULT_NAME --name "ALPACA-API-SECRET" --value "$ALPACA_API_SECRET" --output none
    echo -e "${GREEN}✓ ALPACA-API-SECRET${NC}"
fi

if [ -n "$FRED_API_KEY" ]; then
    az keyvault secret set --vault-name $KEYVAULT_NAME --name "FRED-API-KEY" --value "$FRED_API_KEY" --output none
    echo -e "${GREEN}✓ FRED-API-KEY${NC}"
fi

if [ -n "$ALPHAVANTAGE_API_KEY" ]; then
    az keyvault secret set --vault-name $KEYVAULT_NAME --name "ALPHAVANTAGE-API-KEY" --value "$ALPHAVANTAGE_API_KEY" --output none
    echo -e "${GREEN}✓ ALPHAVANTAGE-API-KEY${NC}"
fi

# ============================================
# PHASE 3: Build & Push Docker Images
# ============================================
echo -e "${GREEN}=== PHASE 3: Building Docker Images ===${NC}"

az acr login --name $ACR_NAME

cd "$PROJECT_ROOT"

echo -e "${YELLOW}Building API image...${NC}"
docker build -t $ACR_NAME.azurecr.io/eiqora-api:latest -f eiqora_v2/Dockerfile . 2>&1 | tail -5
docker push $ACR_NAME.azurecr.io/eiqora-api:latest 2>&1 | tail -3
echo -e "${GREEN}✓ API image pushed${NC}"

echo -e "${YELLOW}Building Data Scheduler image...${NC}"
docker build -t $ACR_NAME.azurecr.io/eiqora-data-scheduler:latest -f data_collection/Dockerfile . 2>&1 | tail -5
docker push $ACR_NAME.azurecr.io/eiqora-data-scheduler:latest 2>&1 | tail -3
echo -e "${GREEN}✓ Data Scheduler image pushed${NC}"

echo -e "${YELLOW}Building Live Scheduler image...${NC}"
docker build -t $ACR_NAME.azurecr.io/eiqora-live-scheduler:latest -f eiqora_v2/live/Dockerfile . 2>&1 | tail -5
docker push $ACR_NAME.azurecr.io/eiqora-live-scheduler:latest 2>&1 | tail -3
echo -e "${GREEN}✓ Live Scheduler image pushed${NC}"

# ============================================
# PHASE 4: Deploy Container Apps
# ============================================
echo -e "${GREEN}=== PHASE 4: Deploying Container Apps ===${NC}"

ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

# Deploy API
echo -e "${YELLOW}Deploying API Server...${NC}"
az containerapp create \
    --resource-group $RESOURCE_GROUP \
    --name eiqora-api \
    --environment $CONTAINER_ENV \
    --image $ACR_NAME.azurecr.io/eiqora-api:latest \
    --registry-server $ACR_NAME.azurecr.io \
    --registry-username $ACR_NAME \
    --registry-password "$ACR_PASSWORD" \
    --target-port 8000 \
    --ingress external \
    --cpu 0.5 \
    --memory 1Gi \
    --min-replicas 0 \
    --max-replicas 2 \
    --env-vars \
        "DATABASE_URL=$DATABASE_URL" \
        "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" \
        "ALPACA_API_KEY=$ALPACA_API_KEY" \
        "ALPACA_API_SECRET=$ALPACA_API_SECRET" \
        "TZ=America/New_York" \
        "CORS_ORIGINS=[\"*\"]" \
    --output none

API_FQDN=$(az containerapp show --resource-group $RESOURCE_GROUP --name eiqora-api --query "properties.configuration.ingress.fqdn" -o tsv)
echo -e "${GREEN}✓ API deployed: https://$API_FQDN${NC}"

# Deploy Data Scheduler
echo -e "${YELLOW}Deploying Data Scheduler...${NC}"
az containerapp create \
    --resource-group $RESOURCE_GROUP \
    --name eiqora-data-scheduler \
    --environment $CONTAINER_ENV \
    --image $ACR_NAME.azurecr.io/eiqora-data-scheduler:latest \
    --registry-server $ACR_NAME.azurecr.io \
    --registry-username $ACR_NAME \
    --registry-password "$ACR_PASSWORD" \
    --cpu 0.5 \
    --memory 1Gi \
    --min-replicas 1 \
    --max-replicas 1 \
    --env-vars \
        "DATABASE_URL=$DATABASE_URL" \
        "ALPACA_API_KEY=$ALPACA_API_KEY" \
        "ALPACA_API_SECRET=$ALPACA_API_SECRET" \
        "FRED_API_KEY=${FRED_API_KEY:-}" \
        "ALPHAVANTAGE_API_KEY=${ALPHAVANTAGE_API_KEY:-}" \
        "TZ=America/New_York" \
        "SCHEDULER_RUN_STARTUP=0" \
    --output none
echo -e "${GREEN}✓ Data Scheduler deployed${NC}"

# Deploy Live Scheduler
echo -e "${YELLOW}Deploying Live Scheduler...${NC}"
az containerapp create \
    --resource-group $RESOURCE_GROUP \
    --name eiqora-live-scheduler \
    --environment $CONTAINER_ENV \
    --image $ACR_NAME.azurecr.io/eiqora-live-scheduler:latest \
    --registry-server $ACR_NAME.azurecr.io \
    --registry-username $ACR_NAME \
    --registry-password "$ACR_PASSWORD" \
    --cpu 0.25 \
    --memory 0.5Gi \
    --min-replicas 1 \
    --max-replicas 1 \
    --env-vars \
        "DATABASE_URL=$DATABASE_URL" \
        "OPENROUTER_API_KEY=$OPENROUTER_API_KEY" \
        "ALPACA_API_KEY=$ALPACA_API_KEY" \
        "ALPACA_API_SECRET=$ALPACA_API_SECRET" \
        "TZ=America/New_York" \
    --output none
echo -e "${GREEN}✓ Live Scheduler deployed${NC}"

# ============================================
# PHASE 5: Database Migrations
# ============================================
echo -e "${GREEN}=== PHASE 5: Running Database Migrations ===${NC}"

# Add local IP to firewall
MY_IP=$(curl -s ifconfig.me)
az postgres flexible-server firewall-rule create \
    --resource-group $RESOURCE_GROUP \
    --name $POSTGRES_SERVER \
    --rule-name "LocalMachine" \
    --start-ip-address $MY_IP \
    --end-ip-address $MY_IP \
    --output none 2>/dev/null || true

sleep 5

export DATABASE_URL
cd "$PROJECT_ROOT/data_collection/db/migrations"
alembic upgrade head 2>&1 | tail -10
echo -e "${GREEN}✓ Migrations complete${NC}"

# ============================================
# PHASE 6: Deploy Frontend
# ============================================
echo -e "${GREEN}=== PHASE 6: Deploying Frontend ===${NC}"

cd "$PROJECT_ROOT/eiqora_v2_frontend"

# Update config with API URL
cat > staticwebapp.config.json <<EOF
{
  "routes": [
    {
      "route": "/api/*",
      "rewrite": "https://$API_FQDN/api/*"
    }
  ],
  "navigationFallback": {
    "rewrite": "/index.html",
    "exclude": ["/api/*", "*.{css,js,svg,png,jpg,ico}"]
  }
}
EOF

npm install --silent
npm run build --silent

# Create Static Web App
az staticwebapp create \
    --resource-group $RESOURCE_GROUP \
    --name eiqora-frontend \
    --location eastasia \
    --sku Free \
    --output none 2>/dev/null || true

DEPLOYMENT_TOKEN=$(az staticwebapp secrets list \
    --resource-group $RESOURCE_GROUP \
    --name eiqora-frontend \
    --query "properties.apiKey" -o tsv)

# Deploy
npx --yes @azure/static-web-apps-cli deploy ./dist \
    --deployment-token $DEPLOYMENT_TOKEN \
    --env production 2>&1 | tail -5

FRONTEND_URL=$(az staticwebapp show \
    --resource-group $RESOURCE_GROUP \
    --name eiqora-frontend \
    --query "defaultHostname" -o tsv)

echo -e "${GREEN}✓ Frontend deployed: https://$FRONTEND_URL${NC}"

# ============================================
# SAVE DEPLOYMENT INFO
# ============================================
cd "$PROJECT_ROOT/azure"
cat > deployment-info.md <<EOF
# Eiqora Azure Deployment

## Deployment Date
$(date)

## Resources Created

| Resource | Name | URL/Connection |
|----------|------|----------------|
| Resource Group | $RESOURCE_GROUP | - |
| Container Registry | $ACR_NAME | $ACR_NAME.azurecr.io |
| Key Vault | $KEYVAULT_NAME | - |
| PostgreSQL Server | $POSTGRES_SERVER | $POSTGRES_SERVER.postgres.database.azure.com |
| Container Apps Env | $CONTAINER_ENV | - |

## Application URLs

| Service | URL |
|---------|-----|
| **Frontend** | https://$FRONTEND_URL |
| **API** | https://$API_FQDN |
| **API Health** | https://$API_FQDN/health |

## Container Apps

| App | Status | CPU | Memory |
|-----|--------|-----|--------|
| eiqora-api | Running | 0.5 | 1Gi |
| eiqora-data-scheduler | Running | 0.5 | 1Gi |
| eiqora-live-scheduler | Running | 0.25 | 0.5Gi |

## Database

- **Host**: $POSTGRES_SERVER.postgres.database.azure.com
- **Database**: finance
- **Admin User**: $POSTGRES_ADMIN
- **Password**: Eiqora2024Secure!
- **SSL**: Required

## Estimated Monthly Cost

| Service | Tier | Est. Cost |
|---------|------|-----------|
| Container Apps | Consumption | ~\$15-30 |
| PostgreSQL | Burstable B1ms | ~\$13 |
| Container Registry | Basic | ~\$5 |
| Static Web Apps | Free | \$0 |
| Key Vault | Standard | ~\$1 |
| **Total** | | **~\$34-49/mo** |

## Management Commands

\`\`\`bash
# View API logs
az containerapp logs show -g $RESOURCE_GROUP -n eiqora-api

# View scheduler logs
az containerapp logs show -g $RESOURCE_GROUP -n eiqora-data-scheduler

# Restart an app
az containerapp revision restart -g $RESOURCE_GROUP -n eiqora-api

# Scale API
az containerapp update -g $RESOURCE_GROUP -n eiqora-api --min-replicas 1 --max-replicas 3

# Connect to database
psql "postgresql://${POSTGRES_ADMIN}:Eiqora2024Secure!@${POSTGRES_SERVER}.postgres.database.azure.com:5432/finance?sslmode=require"

# Delete all resources (cleanup)
az group delete -g $RESOURCE_GROUP --yes
\`\`\`
EOF

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   DEPLOYMENT COMPLETE!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Frontend: ${GREEN}https://$FRONTEND_URL${NC}"
echo -e "API:      ${GREEN}https://$API_FQDN${NC}"
echo ""
echo "Deployment info saved to: azure/deployment-info.md"
