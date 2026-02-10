#!/bin/bash
# Eiqora Database Migrations
# Runs Alembic migrations against Azure PostgreSQL

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

echo -e "${GREEN}=== Eiqora Database Migrations ===${NC}"
echo ""

# Get database URL from Key Vault
DATABASE_URL=$(az keyvault secret show --vault-name $KEYVAULT_NAME --name "DATABASE-URL" --query "value" -o tsv)

# Navigate to project root
cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

# Add local IP to firewall temporarily
echo -e "${YELLOW}Adding your IP to PostgreSQL firewall...${NC}"
MY_IP=$(curl -s ifconfig.me)
az postgres flexible-server firewall-rule create \
    --resource-group $RESOURCE_GROUP \
    --name $POSTGRES_SERVER \
    --rule-name "LocalMachine" \
    --start-ip-address $MY_IP \
    --end-ip-address $MY_IP \
    --output none 2>/dev/null || true
echo -e "${GREEN}✓ Firewall rule added for $MY_IP${NC}"
echo ""

# Wait a moment for firewall to propagate
sleep 5

# Export DATABASE_URL for alembic
export DATABASE_URL

# Run migrations
echo -e "${YELLOW}Running Alembic migrations...${NC}"
cd data_collection/db/migrations

# Check if alembic is installed
if ! command -v alembic &> /dev/null; then
    echo -e "${YELLOW}Installing alembic...${NC}"
    pip install alembic psycopg[binary]
fi

# Run upgrade
alembic upgrade head

echo -e "${GREEN}✓ Migrations complete${NC}"
echo ""

# Enable extensions via psql if available
if command -v psql &> /dev/null; then
    echo -e "${YELLOW}Enabling PostgreSQL extensions...${NC}"
    psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS vector;" 2>/dev/null || true
    psql "$DATABASE_URL" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" 2>/dev/null || true
    echo -e "${GREEN}✓ Extensions enabled${NC}"
else
    echo -e "${YELLOW}Note: Install psql to enable pgvector extension, or run manually:${NC}"
    echo "  psql \"$DATABASE_URL\" -c \"CREATE EXTENSION IF NOT EXISTS vector;\""
fi

echo ""
echo -e "${GREEN}=== Migrations Complete ===${NC}"
echo ""
echo -e "${YELLOW}Next step:${NC} Run ./06-deploy-frontend.sh"
