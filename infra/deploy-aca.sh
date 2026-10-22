#!/usr/bin/env bash
# Deploy MatchItUp to Azure Container Apps with a Microsoft Foundry (Azure OpenAI) model.
#
# Prerequisites: Azure CLI (preinstalled in the Codespace), `az login` done, and a subscription.
# Usage:   bash infra/deploy-aca.sh
# Re-run safely: every step is idempotent or skipped if it already exists.
#
# Uses keyless auth: the container app gets a managed identity that is granted the
# "Cognitive Services OpenAI User" role on the Foundry resource. No API keys are stored.
set -euo pipefail

# ---------- settings (override with environment variables) ----------
SUFFIX="${SUFFIX:-$(whoami | tr -dc 'a-z0-9' | cut -c1-6)$RANDOM}"
RG="${RG:-rg-matchitup}"
LOCATION="${LOCATION:-swedencentral}"          # a region with good model availability
AI_NAME="${AI_NAME:-matchitup-ai-${SUFFIX}}"    # Foundry (Azure AI Services) resource, globally unique
DEPLOYMENT="${DEPLOYMENT:-gpt-4.1-mini}"
MODEL_NAME="${MODEL_NAME:-gpt-4.1-mini}"
MODEL_VERSION="${MODEL_VERSION:-2025-04-14}"    # check the Foundry model catalog if this fails
MODEL_SKU="${MODEL_SKU:-GlobalStandard}"
MODEL_CAPACITY="${MODEL_CAPACITY:-30}"          # thousands of tokens per minute
APP="${APP:-matchitup}"
ENV_NAME="${ENV_NAME:-matchitup-env}"
INSIGHTS="${INSIGHTS:-matchitup-insights}"

echo "==> Using resource group '$RG' in '$LOCATION'"
az account show --query "{subscription:name, user:user.name}" -o table
az extension add --name containerapp --upgrade --only-show-errors
az extension add --name application-insights --upgrade --only-show-errors || true
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.OperationalInsights --wait
az provider register --namespace Microsoft.CognitiveServices --wait

az group create -n "$RG" -l "$LOCATION" -o none

# ---------- 1. Microsoft Foundry resource + model deployment ----------
EXISTING_AI=$(az cognitiveservices account list -g "$RG" --query "[?kind=='AIServices'].name | [0]" -o tsv)
if [[ -n "$EXISTING_AI" ]]; then
  AI_NAME="$EXISTING_AI"
  echo "==> Reusing Foundry resource $AI_NAME"
else
  echo "==> Creating Foundry resource $AI_NAME"
  az cognitiveservices account create -n "$AI_NAME" -g "$RG" -l "$LOCATION" \
    --kind AIServices --sku S0 --custom-domain "$AI_NAME" --yes -o none
fi

if ! az cognitiveservices account deployment show -n "$AI_NAME" -g "$RG" --deployment-name "$DEPLOYMENT" -o none 2>/dev/null; then
  echo "==> Deploying model $MODEL_NAME ($MODEL_VERSION) as '$DEPLOYMENT'"
  az cognitiveservices account deployment create -n "$AI_NAME" -g "$RG" \
    --deployment-name "$DEPLOYMENT" --model-name "$MODEL_NAME" --model-version "$MODEL_VERSION" \
    --model-format OpenAI --sku-name "$MODEL_SKU" --sku-capacity "$MODEL_CAPACITY" -o none
fi
AI_ID=$(az cognitiveservices account show -n "$AI_NAME" -g "$RG" --query id -o tsv)
AZURE_OPENAI_ENDPOINT="https://${AI_NAME}.openai.azure.com/"

# ---------- 2. Application Insights (OpenTelemetry traces) ----------
APPINSIGHTS_CONN=""
if az monitor app-insights component show -a "$INSIGHTS" -g "$RG" -o none 2>/dev/null || \
   az monitor app-insights component create -a "$INSIGHTS" -g "$RG" -l "$LOCATION" -o none 2>/dev/null; then
  APPINSIGHTS_CONN=$(az monitor app-insights component show -a "$INSIGHTS" -g "$RG" --query connectionString -o tsv)
fi

# ---------- 3. Build from source and deploy to Azure Container Apps ----------
echo "==> Building and deploying the container app (first run takes a few minutes)"
az containerapp up -n "$APP" -g "$RG" -l "$LOCATION" --environment "$ENV_NAME" \
  --source . --ingress external --target-port 8000 \
  --env-vars LLM_PROVIDER=azure AZURE_OPENAI_ENDPOINT="$AZURE_OPENAI_ENDPOINT" \
             AZURE_OPENAI_DEPLOYMENT="$DEPLOYMENT" PLATFORM_NAME=aca \
             APPLICATIONINSIGHTS_CONNECTION_STRING="$APPINSIGHTS_CONN"

# ---------- 4. Keyless access: managed identity -> Foundry ----------
PRINCIPAL_ID=$(az containerapp identity assign -n "$APP" -g "$RG" --system-assigned --query principalId -o tsv)
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" --scope "$AI_ID" -o none 2>/dev/null || true

# Keep one replica warm during judging; scale out under load.
az containerapp update -n "$APP" -g "$RG" --min-replicas 1 --max-replicas 3 \
  --scale-rule-name http-rule --scale-rule-http-concurrency 50 -o none

URL=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
echo ""
echo "✅ MatchItUp is live at: https://${URL}"
echo "   Health check:          https://${URL}/api/health"
echo "   (Role assignments can take 1-5 minutes to apply. Until then the app runs in template mode.)"
echo ""
echo "For local development with the same model, add to .env:"
echo "   LLM_PROVIDER=azure"
echo "   AZURE_OPENAI_ENDPOINT=${AZURE_OPENAI_ENDPOINT}"
echo "   AZURE_OPENAI_DEPLOYMENT=${DEPLOYMENT}"
echo "and grant yourself the same role:"
echo "   az role assignment create --assignee \$(az ad signed-in-user show --query id -o tsv) --role 'Cognitive Services OpenAI User' --scope ${AI_ID}"
