#!/bin/bash
# Eiqora Azure Secrets Setup
# Run this to add API keys to Key Vault

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

echo -e "${GREEN}=== Eiqora Secrets Setup ===${NC}"
echo ""

# Try to load from local .env file
ENV_FILE="../.env"
if [ -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}Found .env file. Loading secrets from it...${NC}"
    source "$ENV_FILE"
fi

# Function to set secret (prompts if not in env)
set_secret() {
    local name=$1
    local env_var=$2
    local required=$3
    local value=${!env_var}

    if [ -z "$value" ]; then
        if [ "$required" = "true" ]; then
            echo -e "${YELLOW}Enter $name:${NC}"
            read -s value
            echo ""
        else
            echo -e "Skipping optional secret: $name"
            return
        fi
    fi

    if [ -n "$value" ]; then
        az keyvault secret set \
            --vault-name $KEYVAULT_NAME \
            --name "$name" \
            --value "$value" \
            --output none
        echo -e "${GREEN}✓ Set secret: $name${NC}"
    fi
}

# Required secrets
echo -e "${YELLOW}Setting required secrets...${NC}"
set_secret "OPENROUTER-API-KEY" "OPENROUTER_API_KEY" "true"
set_secret "ALPACA-API-KEY" "ALPACA_API_KEY" "true"
set_secret "ALPACA-API-SECRET" "ALPACA_API_SECRET" "true"

# Optional secrets
echo ""
echo -e "${YELLOW}Setting optional secrets (press Enter to skip)...${NC}"
set_secret "FRED-API-KEY" "FRED_API_KEY" "false"
set_secret "ALPHAVANTAGE-API-KEY" "ALPHAVANTAGE_API_KEY" "false"
set_secret "TAVILY-API-KEY" "TAVILY_API_KEY" "false"
set_secret "BENZINGA-API-KEY" "BENZINGA_API_KEY" "false"
set_secret "FMP-API-KEY" "FinancialModelingPrep_API_KEY" "false"

echo ""
echo -e "${GREEN}=== Secrets Setup Complete ===${NC}"
echo ""
echo "Secrets stored in Key Vault: $KEYVAULT_NAME"
echo ""
echo -e "${YELLOW}Next step:${NC} Run ./03-build-and-push.sh"
