# Eiqora Azure Deployment

## Deployment Date
February 4, 2026

## Resources Created

| Resource | Name | URL/Connection |
|----------|------|----------------|
| Resource Group | eiqora-rg | southeastasia |
| Container Registry | eiqoraacr6e53f1e0 | eiqoraacr6e53f1e0.azurecr.io |
| Key Vault | eiqora-kv-25ac9ead | - |
| PostgreSQL Server | eiqora-pg-727264b6 | eiqora-pg-727264b6.postgres.database.azure.com |
| Container Apps Env | eiqora-env | - |
| Storage Account | eiqorafrontend | eiqorafrontend.z23.web.core.windows.net |

## Application URLs

| Service | URL |
|---------|-----|
| **Frontend** | https://eiqorafrontend.z23.web.core.windows.net/ |
| **API** | https://eiqora-api.salmonriver-3b9ccf9a.southeastasia.azurecontainerapps.io |
| **API Health** | https://eiqora-api.salmonriver-3b9ccf9a.southeastasia.azurecontainerapps.io/health |

## Container Apps

| App | Status | CPU | Memory |
|-----|--------|-----|--------|
| eiqora-api | Running | 0.5 | 1Gi |
| eiqora-data-scheduler | Running | 0.5 | 1Gi |
| eiqora-live-scheduler | Running | 0.25 | 0.5Gi |

## Database

- **Host**: eiqora-pg-727264b6.postgres.database.azure.com
- **Database**: finance
- **Admin User**: eiqoraadmin
- **Password**: Eiqora2024Db
- **SSL**: Required
- **Extensions**: vector, pg_trgm

### Connection String
```
postgresql://eiqoraadmin:Eiqora2024Db@eiqora-pg-727264b6.postgres.database.azure.com:5432/finance?sslmode=require
```

## Estimated Monthly Cost

| Service | Tier | Est. Cost |
|---------|------|-----------|
| Container Apps | Consumption | ~$15-30 |
| PostgreSQL | Burstable B1ms | ~$13 |
| Container Registry | Basic | ~$5 |
| Static Web Apps | Free | $0 |
| Key Vault | Standard | ~$1 |
| **Total** | | **~$34-49/mo** |

## Management Commands

```bash
# View API logs
az containerapp logs show -g eiqora-rg -n eiqora-api

# View data scheduler logs
az containerapp logs show -g eiqora-rg -n eiqora-data-scheduler

# View live scheduler logs
az containerapp logs show -g eiqora-rg -n eiqora-live-scheduler

# Restart an app
az containerapp revision restart -g eiqora-rg -n eiqora-api

# Scale API
az containerapp update -g eiqora-rg -n eiqora-api --min-replicas 1 --max-replicas 3

# Connect to database
psql "postgresql://eiqoraadmin:Eiqora2024Db@eiqora-pg-727264b6.postgres.database.azure.com:5432/finance?sslmode=require"

# Delete all resources (cleanup)
az group delete -g eiqora-rg --yes
```

## API Keys Stored in Key Vault

The following secrets are stored in Key Vault `eiqora-kv-25ac9ead`:
- DATABASE-URL
- OPENROUTER-API-KEY
- ALPACA-API-KEY
- ALPACA-API-SECRET

## Frontend Deployment

The frontend is deployed to Azure Storage Static Website:
- **Storage Account**: eiqorafrontend
- **URL**: https://eiqorafrontend.z23.web.core.windows.net/

### To Update Frontend
```bash
cd eiqora_v2_frontend
npm run build
STORAGE_KEY=$(az storage account keys list --account-name eiqorafrontend --resource-group eiqora-rg --query "[0].value" -o tsv)
az storage blob upload-batch --account-name eiqorafrontend --account-key "$STORAGE_KEY" --source ./dist --destination '$web' --overwrite
```

## Deployment Scripts

All deployment scripts are located in the `azure/` directory:
- `deploy-all.sh` - Full deployment script (infrastructure + apps + frontend)
- `01-setup-infrastructure.sh` - Create Azure resources
- `02-store-secrets.sh` - Store secrets in Key Vault
- `03-build-images.sh` - Build and push Docker images
- `04-deploy-api.sh` - Deploy API container
- `05-deploy-schedulers.sh` - Deploy scheduler containers
- `06-deploy-frontend.sh` - Deploy frontend (Static Web Apps)

## Dockerfiles

| Component | Dockerfile Location |
|-----------|---------------------|
| API Server | `eiqora_v2/Dockerfile` |
| Data Scheduler | `data_collection/Dockerfile` |
| Live Scheduler | `eiqora_v2/live/Dockerfile` |

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Azure Resource Group (eiqora-rg)             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────┐    ┌──────────────────────────────────┐  │
│  │ Storage Static   │    │ Container Apps Environment       │  │
│  │ Website (React)  │───▶│                                  │  │
│  └──────────────────┘    │  ┌────────────────────────────┐  │  │
│                          │  │ eiqora-api (FastAPI)       │  │  │
│  ┌──────────────────┐    │  │ - /api/* endpoints         │  │  │
│  │ Container        │    │  │ - WebSocket support        │  │  │
│  │ Registry (ACR)   │───▶│  │ - 0.5 vCPU, 1GB RAM       │  │  │
│  │ eiqoraacr6e53f1e0│    │  └────────────────────────────┘  │  │
│  └──────────────────┘    │                                  │  │
│                          │  ┌────────────────────────────┐  │  │
│  ┌──────────────────┐    │  │ eiqora-data-scheduler      │  │  │
│  │ Key Vault        │───▶│  │ - Daily data collection    │  │  │
│  │ eiqora-kv-...    │    │  │ - Technical indicators     │  │  │
│  └──────────────────┘    │  │ - 0.5 vCPU, 1GB RAM       │  │  │
│                          │  └────────────────────────────┘  │  │
│                          │                                  │  │
│                          │  ┌────────────────────────────┐  │  │
│                          │  │ eiqora-live-scheduler      │  │  │
│                          │  │ - Live trigger monitoring  │  │  │
│                          │  │ - Multi-agent analysis     │  │  │
│                          │  │ - 0.25 vCPU, 0.5GB RAM    │  │  │
│                          │  └────────────────────────────┘  │  │
│                          └────────────────┬─────────────────┘  │
│                                           │                     │
│                          ┌────────────────▼─────────────────┐  │
│                          │ PostgreSQL Flexible Server       │  │
│                          │ eiqora-pg-727264b6               │  │
│                          │ - Burstable B1ms                 │  │
│                          │ - pgvector + pg_trgm extensions  │  │
│                          │ - 32GB storage                   │  │
│                          └──────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```
