#!/usr/bin/env bash
# =============================================================================
# RAGFlow Offline Deployment - Uninstall / Rollback Script
# =============================================================================
# Safely removes all RAGFlow deployment artifacts WITHOUT touching other
# Docker containers, images, volumes, or networks on the system.
#
# Usage:
#   sudo bash uninstall.sh            # Full cleanup (keep images for re-deploy)
#   sudo bash uninstall.sh --images   # Full cleanup + remove loaded images
#
# States handled:
#   - Normal deployment (all running)   → stop + remove
#   - Failed/partial deployment         → force remove + cleanup
#   - Pre-deployment (images loaded)    → optional image removal
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[UNINSTALL]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
skip() { echo -e "${YELLOW}[SKIP]${NC} $*"; }

REMOVE_IMAGES=false
if [ "${1:-}" = "--images" ]; then
    REMOVE_IMAGES=true
fi

COMPOSE_FILE="docker-compose.offline.yml"

echo ""
echo -e "${RED}============================================${NC}"
echo -e "${RED}  RAGFlow Offline - Uninstall               ${NC}"
echo -e "${RED}============================================${NC}"
echo ""

if [ "${REMOVE_IMAGES}" = true ]; then
    echo -e "${RED}  WARNING: --images flag set. All RAGFlow Docker images will be removed.${NC}"
    echo -e "${RED}  This means you will need to re-load images from the package to re-deploy.${NC}"
fi
echo ""

# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------
read -rp "Are you sure you want to uninstall RAGFlow? [y/N] " CONFIRM
if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# ---------------------------------------------------------------------------
# Step 1: Stop and remove containers / network / volumes (compose-managed)
# ---------------------------------------------------------------------------
log "Step 1/4: Stopping and removing RAGFlow containers..."

if [ -f "${COMPOSE_FILE}" ]; then
    # docker compose down: stops containers, removes network, removes volumes (-v)
    # This ONLY affects resources defined in our compose file, NOT other Docker resources.
    if docker compose -f "${COMPOSE_FILE}" down -v --remove-orphans --timeout 30 2>/dev/null; then
        log "  Containers, network, and volumes removed via compose."
    else
        # Compose down failed — likely partial deployment or compose not found.
        # Fall back to manual cleanup.
        warn "  Compose down failed, attempting manual cleanup..."
    fi
else
    skip "  Compose file not found. Skipping compose down."
fi

# Derive project name from the package directory.
# Docker compose derives the project name from the directory basename by
# lowercasing and removing characters not in [a-z0-9_-] (dots are removed).
PROJECT_NAME=$(basename "${SCRIPT_DIR}" | tr '[:upper:]' '[:lower:]' | tr -cd '[:alnum:]-_')

# ---------------------------------------------------------------------------
# Step 2: Force-remove any leftover containers from our deployment
# ---------------------------------------------------------------------------
log "Step 2/4: Cleaning up any leftover containers..."

CLEANED=0
# Use docker compose project label to find ONLY our containers
# This is safer than name-based matching which could hit other projects.
PROJECT_LABEL="com.docker.compose.project=${PROJECT_NAME}"

# Containers with our compose project label
leftover_ids=$(docker ps -a --filter "label=${PROJECT_LABEL}" --format '{{.ID}}' 2>/dev/null || true)

if [ -n "${leftover_ids}" ]; then
    log "  Found leftover container(s) from project '${PROJECT_NAME}':"
    for cid in ${leftover_ids}; do
        cname=$(docker ps -a --filter "id=${cid}" --format '{{.Names}}' 2>/dev/null)
        log "    - ${cname} (${cid:0:12})"
        docker rm -f "${cid}" 2>/dev/null && ((CLEANED++)) || true
    done
fi

if [ "${CLEANED}" -gt 0 ]; then
    log "  Removed ${CLEANED} leftover container(s)."
else
    skip "  No leftover containers found."
fi

# ---------------------------------------------------------------------------
# Step 3: Clean up volumes not caught by compose down
# ---------------------------------------------------------------------------
log "Step 3/4: Checking for leftover Docker volumes..."

# Only match volumes whose names begin with our exact compose project prefix
# (e.g. ragflow-offline-v0256_mysql_data).
VOLUME_CLEANED=0
for vol in $(docker volume ls --format '{{.Name}}' 2>/dev/null); do
    if [[ "${vol}" == "${PROJECT_NAME}_"* ]]; then
        log "  Removing volume: ${vol}"
        docker volume rm "${vol}" 2>/dev/null && ((VOLUME_CLEANED++)) || true
    fi
done

if [ "${VOLUME_CLEANED}" -gt 0 ]; then
    log "  Removed ${VOLUME_CLEANED} volume(s)."
else
    skip "  No volumes to clean."
fi

# ---------------------------------------------------------------------------
# Step 4: Clean up Docker network (if still lingering)
# ---------------------------------------------------------------------------
log "Step 4/4: Checking for RAGFlow Docker network..."

# The ragflow network is created by our compose file with the project prefix.
# Only remove if no other containers use it.
NETWORK_NAME="${PROJECT_NAME}_ragflow"
if docker network inspect "${NETWORK_NAME}" &>/dev/null 2>&1; then
    # Check if any running containers are connected
    connected=$(docker network inspect "${NETWORK_NAME}" --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null)
    if [ -z "${connected}" ]; then
        docker network rm "${NETWORK_NAME}" 2>/dev/null && log "  Removed '${NETWORK_NAME}' network." || skip "  Could not remove '${NETWORK_NAME}' network (may be in use)."
    else
        skip "  '${NETWORK_NAME}' network still has connected containers: ${connected}"
    fi
else
    skip "  '${NETWORK_NAME}' network not found."
fi

# ---------------------------------------------------------------------------
# Optional: Remove Docker images
# ---------------------------------------------------------------------------
if [ "${REMOVE_IMAGES}" = true ]; then
    log "Removing RAGFlow Docker images..."

    OUR_IMAGES=(
        "mysql:8.0.39"
        "opensearchproject/opensearch:2.19.1"
        "pgsty/minio:RELEASE.2026-03-25T00-00-00Z"
        "valkey/valkey:8"
        "infiniflow/ragflow:v0.25.6"
        "infiniflow/text-embeddings-inference:cpu-1.8"
    )

    for img in "${OUR_IMAGES[@]}"; do
        if docker image inspect "${img}" &>/dev/null 2>&1; then
            docker rmi "${img}" 2>/dev/null && log "  Removed image: ${img}" || warn "  Could not remove: ${img}"
        fi
    done
else
    log "Keeping Docker images (use --images flag to remove)."
fi

# ---------------------------------------------------------------------------
# Clean up host directory artifacts (safe — only within our package dir)
# ---------------------------------------------------------------------------
log "Cleaning up runtime data..."

# Remove logs directory (contains container log output)
if [ -d "ragflow-logs" ]; then
    rm -rf ragflow-logs 2>/dev/null || true
    log "  Removed ragflow-logs/ directory."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  RAGFlow uninstall complete.               ${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "  What was removed:"
echo -e "    - RAGFlow Docker containers"
echo -e "    - RAGFlow Docker volumes (data)"
echo -e "    - RAGFlow Docker network"
echo -e "    - Runtime logs"
if [ "${REMOVE_IMAGES}" = true ]; then
    echo -e "    - RAGFlow Docker images"
else
    echo -e "  What was kept:"
    echo -e "    - Docker images (ready for re-deploy)"
fi
echo ""
echo -e "  To re-deploy: ${GREEN}sudo bash install.sh${NC}"
echo ""
