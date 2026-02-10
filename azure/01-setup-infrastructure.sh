#!/bin/bash
# Eiqora Azure Infrastructure Setup
# Run this script first to create all Azure resources

set -e

# Configuration - modify these as needed
RESOURCE_GROUP="eiqora-rg"
LOCATION="eastus"
ACR_NAME="eiqoraacr"
KEYVAULT_NAME="eiqora-kv"
POSTGRES_SERVER="eiqora-postgres"
POSTGRES_ADMIN="eiqoraadmin"
CONTAINER_ENV="eiqora-env"
STORAGE_ACCOUNT="eiqorastorage"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Eiqora Azure Infrastructure Setup ===${NC}"
echo ""

# Check if logged in
echo -e "${YELLOW}Checking Azure CLI login...${NC}"
az account show > /dev/null 2>&1 || { echo -e "${RED}Please login with 'az login' first${NC}"; exit 1; }
echo -e "${GREEN}✓ Logged in to Azure${NC}"

# Get subscription info
SUBSCRIPTION=$(az account show --query name -o tsv)
echo -e "Using subscription: ${GREEN}$SUBSCRIPTION${NC}"
echo ""

# Prompt for PostgreSQL password
echo -e "${YELLOW}Enter PostgreSQL admin password (min 8 chars, must include uppercase, lowercase, number):${NC}"
read -s POSTGRES_PASSWORD
echo ""

# 1. Create Resource Group
echo -e "${YELLOW}Creating Resource Group...${NC}"
az group create --name $RESOURCE_GROUP --location $LOCATION --output none
echo -e "${GREEN}✓ Resource Group created: $RESOURCE_GROUP${NC}"

# 2. Create Container Registry
echo -e "${YELLOW}Creating Container Registry...${NC}"
az acr create \
    --resource-group $RESOURCE_GROUP \
    --name $ACR_NAME \
    --sku Basic \
    --admin-enabled true \
    --output none
echo -e "${GREEN}✓ Container Registry created: $ACR_NAME${NC}"

# 3. Create Key Vault
echo -e "${YELLOW}Creating Key Vault...${NC}"
az keyvault create \
    --resource-group $RESOURCE_GROUP \
    --name $KEYVAULT_NAME \
    --location $LOCATION \
    --enable-rbac-authorization false \
    --output none
echo -e "${GREEN}✓ Key Vault created: $KEYVAULT_NAME${NC}"

# 4. Create PostgreSQL Flexible Server
echo -e "${YELLOW}Creating PostgreSQL Flexible Server (this may take a few minutes)...${NC}"
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

# Enable pgvector extension
echo -e "${YELLOW}Enabling pgvector extension...${NC}"
az postgres flexible-server parameter set \
    --resource-group $RESOURCE_GROUP \
    --server-name $POSTGRES_SERVER \
    --name azure.extensions \
    --value vector \
    --output none

# Allow Azure services to connect
echo -e "${YELLOW}Configuring firewall rules...${NC}"
az postgres flexible-server firewall-rule create \
    --resource-group $RESOURCE_GROUP \
    --name $POSTGRES_SERVER \
    --rule-name AllowAzureServices \
    --start-ip-address 0.0.0.0 \
    --end-ip-address 0.0.0.0 \
    --output none

echo -e "${GREEN}✓ PostgreSQL created: $POSTGRES_SERVER${NC}"

# 5. Create Storage Account (for logs)
echo -e "${YELLOW}Creating Storage Account...${NC}"
# Make storage account name unique by adding random suffix
STORAGE_SUFFIX=$(openssl rand -hex 4)
STORAGE_ACCOUNT="${STORAGE_ACCOUNT}${STORAGE_SUFFIX}"
az storage account create \
    --resource-group $RESOURCE_GROUP \
    --name $STORAGE_ACCOUNT \
    --location $LOCATION \
    --sku Standard_LRS \
    --output none
echo -e "${GREEN}✓ Storage Account created: $STORAGE_ACCOUNT${NC}"

# 6. Create Container Apps Environment
echo -e "${YELLOW}Creating Container Apps Environment...${NC}"
az containerapp env create \
    --resource-group $RESOURCE_GROUP \
    --name $CONTAINER_ENV \
    --location $LOCATION \
    --output none
echo -e "${GREEN}✓ Container Apps Environment created: $CONTAINER_ENV${NC}"

# 7. Create the finance database
echo -e "${YELLOW}Creating finance database...${NC}"
az postgres flexible-server db create \
    --resource-group $RESOURCE_GROUP \
    --server-name $POSTGRES_SERVER \
    --database-name finance \
    --output none
echo -e "${GREEN}✓ Database 'finance' created${NC}"

# Store database URL in Key Vault
DATABASE_URL="postgresql://${POSTGRES_ADMIN}:${POSTGRES_PASSWORD}@${POSTGRES_SERVER}.postgres.database.azure.com:5432/finance?sslmode=require"
az keyvault secret set \
    --vault-name $KEYVAULT_NAME \
    --name "DATABASE-URL" \
    --value "$DATABASE_URL" \
    --output none
echo -e "${GREEN}✓ Database URL stored in Key Vault${NC}"

# Get ACR credentials and store in Key Vault
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)
az keyvault secret set \
    --vault-name $KEYVAULT_NAME \
    --name "ACR-PASSWORD" \
    --value "$ACR_PASSWORD" \
    --output none

echo ""
echo -e "${GREEN}=== Infrastructure Setup Complete ===${NC}"
echo ""
echo "Resources created:"
echo "  - Resource Group: $RESOURCE_GROUP"
echo "  - Container Registry: $ACR_NAME.azurecr.io"
echo "  - Key Vault: $KEYVAULT_NAME"
echo "  - PostgreSQL Server: $POSTGRES_SERVER.postgres.database.azure.com"
echo "  - Storage Account: $STORAGE_ACCOUNT"
echo "  - Container Apps Environment: $CONTAINER_ENV"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Run ./02-setup-secrets.sh to add API keys to Key Vault"
echo "2. Run ./03-build-and-push.sh to build and push Docker images"
echo "3. Run ./04-deploy-apps.sh to deploy Container Apps"
echo ""

# Save config for other scripts
cat > .azure-config <<EOF
RESOURCE_GROUP=$RESOURCE_GROUP
LOCATION=$LOCATION
ACR_NAME=$ACR_NAME
KEYVAULT_NAME=$KEYVAULT_NAME
POSTGRES_SERVER=$POSTGRES_SERVER
POSTGRES_ADMIN=$POSTGRES_ADMIN
CONTAINER_ENV=$CONTAINER_ENV
STORAGE_ACCOUNT=$STORAGE_ACCOUNT
EOF

echo -e "${GREEN}Configuration saved to .azure-config${NC}"
