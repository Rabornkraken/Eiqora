#!/bin/bash
# Eiqora Container Apps Deployment
# Deploys all container apps to Azure

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
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== Eiqora Container Apps Deployment ===${NC}"
echo ""

# Get ACR credentials
ACR_USERNAME=$ACR_NAME
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

# Get secrets from Key Vault
echo -e "${YELLOW}Retrieving secrets from Key Vault...${NC}"
DATABASE_URL=$(az keyvault secret show --vault-name $KEYVAULT_NAME --name "DATABASE-URL" --query "value" -o tsv)
OPENROUTER_KEY=$(az keyvault secret show --vault-name $KEYVAULT_NAME --name "OPENROUTER-API-KEY" --query "value" -o tsv 2>/dev/null || echo "")
ALPACA_KEY=$(az keyvault secret show --vault-name $KEYVAULT_NAME --name "ALPACA-API-KEY" --query "value" -o tsv 2>/dev/null || echo "")
ALPACA_SECRET=$(az keyvault secret show --vault-name $KEYVAULT_NAME --name "ALPACA-API-SECRET" --query "value" -o tsv 2>/dev/null || echo "")
FRED_KEY=$(az keyvault secret show --vault-name $KEYVAULT_NAME --name "FRED-API-KEY" --query "value" -o tsv 2>/dev/null || echo "")
ALPHAVANTAGE_KEY=$(az keyvault secret show --vault-name $KEYVAULT_NAME --name "ALPHAVANTAGE-API-KEY" --query "value" -o tsv 2>/dev/null || echo "")
FINNHUB_KEY=$(az keyvault secret show --vault-name $KEYVAULT_NAME --name "FINNHUB-API-KEY" --query "value" -o tsv 2>/dev/null || echo "")
TELEGRAM_TOKEN=$(az keyvault secret show --vault-name $KEYVAULT_NAME --name "TELEGRAM-BOT-TOKEN" --query "value" -o tsv 2>/dev/null || echo "")

echo -e "${GREEN}✓ Secrets retrieved${NC}"
echo ""

# 1. Deploy API Server
echo -e "${YELLOW}Deploying API Server...${NC}"
az containerapp create \
    --resource-group $RESOURCE_GROUP \
    --name eiqora-api \
    --environment $CONTAINER_ENV \
    --image $ACR_NAME.azurecr.io/eiqora-api:latest \
    --registry-server $ACR_NAME.azurecr.io \
    --registry-username $ACR_USERNAME \
    --registry-password "$ACR_PASSWORD" \
    --target-port 8000 \
    --ingress external \
    --cpu 0.5 \
    --memory 1Gi \
    --min-replicas 1 \
    --max-replicas 2 \
    --env-vars \
        "DATABASE_URL=$DATABASE_URL" \
        "OPENROUTER_API_KEY=$OPENROUTER_KEY" \
        "ALPACA_API_KEY=$ALPACA_KEY" \
        "ALPACA_API_SECRET=$ALPACA_SECRET" \
        "TZ=America/New_York" \
        "CORS_ORIGINS=[\"*\"]" \
    --output none

API_URL=$(az containerapp show --resource-group $RESOURCE_GROUP --name eiqora-api --query "properties.configuration.ingress.fqdn" -o tsv)
echo -e "${GREEN}✓ API deployed: https://$API_URL${NC}"
echo ""

# 2. Deploy Data Scheduler
echo -e "${YELLOW}Deploying Data Scheduler...${NC}"
az containerapp create \
    --resource-group $RESOURCE_GROUP \
    --name eiqora-data-scheduler \
    --environment $CONTAINER_ENV \
    --image $ACR_NAME.azurecr.io/eiqora-data-scheduler:latest \
    --registry-server $ACR_NAME.azurecr.io \
    --registry-username $ACR_USERNAME \
    --registry-password "$ACR_PASSWORD" \
    --cpu 0.5 \
    --memory 1Gi \
    --min-replicas 1 \
    --max-replicas 1 \
    --env-vars \
        "DATABASE_URL=$DATABASE_URL" \
        "ALPACA_API_KEY=$ALPACA_KEY" \
        "ALPACA_API_SECRET=$ALPACA_SECRET" \
        "FRED_API_KEY=$FRED_KEY" \
        "ALPHAVANTAGE_API_KEY=$ALPHAVANTAGE_KEY" \
        "TZ=America/New_York" \
        "SCHEDULER_RUN_STARTUP=0" \
    --output none

echo -e "${GREEN}✓ Data Scheduler deployed${NC}"
echo ""

# 3. Deploy Live Scheduler
echo -e "${YELLOW}Deploying Live Scheduler...${NC}"
az containerapp create \
    --resource-group $RESOURCE_GROUP \
    --name eiqora-live-scheduler \
    --environment $CONTAINER_ENV \
    --image $ACR_NAME.azurecr.io/eiqora-live-scheduler:latest \
    --registry-server $ACR_NAME.azurecr.io \
    --registry-username $ACR_USERNAME \
    --registry-password "$ACR_PASSWORD" \
    --cpu 0.5 \
    --memory 1Gi \
    --min-replicas 1 \
    --max-replicas 1 \
    --env-vars \
        "DATABASE_URL=$DATABASE_URL" \
        "OPENROUTER_API_KEY=$OPENROUTER_KEY" \
        "ALPACA_API_KEY=$ALPACA_KEY" \
        "ALPACA_API_SECRET=$ALPACA_SECRET" \
        "FINNHUB_API_KEY=$FINNHUB_KEY" \
        "TZ=America/New_York" \
    --output none

echo -e "${GREEN}✓ Live Scheduler deployed${NC}"
echo ""

# 4. Deploy Telegram Bot
if [ -n "$TELEGRAM_TOKEN" ]; then
    echo -e "${YELLOW}Deploying Telegram Bot...${NC}"
    az containerapp create \
        --resource-group $RESOURCE_GROUP \
        --name eiqora-telegram-bot \
        --environment $CONTAINER_ENV \
        --image $ACR_NAME.azurecr.io/eiqora-telegram-bot:latest \
        --registry-server $ACR_NAME.azurecr.io \
        --registry-username $ACR_USERNAME \
        --registry-password "$ACR_PASSWORD" \
        --cpu 0.25 \
        --memory 0.5Gi \
        --min-replicas 1 \
        --max-replicas 1 \
        --env-vars \
            "DATABASE_URL=$DATABASE_URL" \
            "TELEGRAM_BOT_TOKEN=$TELEGRAM_TOKEN" \
            "TZ=America/New_York" \
        --output none

    echo -e "${GREEN}✓ Telegram Bot deployed${NC}"
else
    echo -e "${YELLOW}⚠ Skipping Telegram Bot (no TELEGRAM-BOT-TOKEN in Key Vault)${NC}"
fi
echo ""

echo -e "${GREEN}=== Deployment Complete ===${NC}"
echo ""
echo "Deployed apps:"
echo "  - API Server: https://$API_URL"
echo "  - Data Scheduler: Running (no external ingress)"
echo "  - Live Scheduler: Running (no external ingress)"
echo "  - Telegram Bot: Running (no external ingress)"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Run database migrations: ./05-run-migrations.sh"
echo "2. Deploy frontend: ./06-deploy-frontend.sh"
echo ""

# Save API URL for frontend deployment
echo "API_URL=https://$API_URL" >> .azure-config
