#!/usr/bin/env bash
# =============================================================================
# RAGFlow Offline Deployment - Install Script
# =============================================================================
# Run this on the target air-gapped machine to deploy RAGFlow.
#
# Prerequisites:
#   - Linux x86_64
#   - Docker Engine 20.10+ (with docker compose v2 plugin)
#   - At least 25 GB free disk space
#   - 8 GB+ RAM (7 GB minimum)
#
# Usage:
#   sudo bash install.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()    { echo -e "${GREEN}[INSTALL]${NC} $*"; }
warn()   { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()    { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
info()   { echo -e "${BLUE}[INFO]${NC} $*"; }

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  RAGFlow Offline Deployment Installer      ${NC}"
echo -e "${GREEN}  Version: v0.25.6                          ${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

FAILED_CHECKS=0

# ===========================================================================
# STEP 1: System Requirements
# ===========================================================================
log "Step 1/8: Checking system requirements..."

# --- 1a. Architecture ---
ARCH=$(uname -m)
if [ "${ARCH}" != "x86_64" ]; then
    err "Unsupported architecture: ${ARCH}. This package is for x86_64 only."
fi
log "  Architecture: ${ARCH} (OK)"

# --- 1b. Memory ---
TOTAL_MEM_MB=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)
TOTAL_MEM_GB=$(( TOTAL_MEM_MB / 1024 ))
if [ "${TOTAL_MEM_GB}" -lt 7 ]; then
    err "Insufficient memory: ${TOTAL_MEM_GB}GB detected, 7GB minimum required.
    RAGFlow + OpenSearch + MySQL + TEI (bge-m3) need at least 7GB."
elif [ "${TOTAL_MEM_GB}" -lt 8 ]; then
    warn "  Memory: ${TOTAL_MEM_GB}GB (minimum, 8GB+ recommended)"
else
    log "  Memory: ${TOTAL_MEM_GB}GB (OK)"
fi

# --- 1c. Disk space ---
AVAILABLE_GB=$(df -BG "${SCRIPT_DIR}" | awk 'NR==2 {gsub(/G/,"",$4); print $4}')
REQUIRED_DISK_GB=25
if [ "${AVAILABLE_GB}" -lt "${REQUIRED_DISK_GB}" ]; then
    err "Insufficient disk space: ${AVAILABLE_GB}GB available, ${REQUIRED_DISK_GB}GB required.
    Package files: ~10GB, extracted images: ~10GB, runtime data: ~5GB."
fi
log "  Disk space: ${AVAILABLE_GB}GB available (OK)"

# --- 1d. vm.max_map_count (OpenSearch requirement) ---
CURRENT_MAP_COUNT=$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)
REQUIRED_MAP_COUNT=262144
if [ "${CURRENT_MAP_COUNT}" -lt "${REQUIRED_MAP_COUNT}" ]; then
    warn "  vm.max_map_count=${CURRENT_MAP_COUNT} is too low for OpenSearch."
    warn "  RAGFlow will attempt to set it to ${REQUIRED_MAP_COUNT}."
    if sysctl -w vm.max_map_count="${REQUIRED_MAP_COUNT}" &>/dev/null; then
        # Make persistent
        if [ ! -f /etc/sysctl.d/99-ragflow.conf ]; then
            echo "vm.max_map_count=${REQUIRED_MAP_COUNT}" > /etc/sysctl.d/99-ragflow.conf 2>/dev/null || true
        fi
        log "  vm.max_map_count set to ${REQUIRED_MAP_COUNT} (OK)"
    else
        warn "  Could not set vm.max_map_count automatically."
        warn "  Run manually: sudo sysctl -w vm.max_map_count=${REQUIRED_MAP_COUNT}"
        warn "  OpenSearch may fail to start until this is fixed."
    fi
else
    log "  vm.max_map_count=${CURRENT_MAP_COUNT} (OK)"
fi

# ===========================================================================
# STEP 2: Docker & Prerequisites
# ===========================================================================
log "Step 2/8: Checking prerequisites..."

if ! command -v openssl &>/dev/null; then
    err "openssl is required but not installed."
fi

if ! command -v docker &>/dev/null; then
    err "Docker is not installed. Please install Docker Engine 20.10+ first.
    
    Offline Docker installation (x86_64):
      1. Download static binary from:
         https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz
      2. tar xzvf docker-27.3.1.tgz
      3. sudo cp docker/* /usr/bin/
      4. sudo dockerd &
    
    For systemd-based distributions, you may also install the .rpm/.deb package
    bundled with your OS installation media.
    
    See: https://docs.docker.com/engine/install/binaries/"
fi

if ! docker compose version &>/dev/null 2>&1; then
    # Try docker-compose (v1)
    if command -v docker-compose &>/dev/null 2>&1; then
        warn "  Found docker-compose v1. Docker Compose v2 plugin is recommended."
        warn "  Consider installing it from:"
        warn "  https://github.com/docker/compose/releases"
    else
        err "Docker Compose is required but not found.
    
    Offline Docker Compose installation:
      1. Download binary from:
         https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64
      2. sudo cp docker-compose-linux-x86_64 /usr/local/bin/docker-compose
      3. sudo chmod +x /usr/local/bin/docker-compose
      4. Create plugin symlink:
         sudo mkdir -p /usr/local/lib/docker/cli-plugins
         sudo ln -s /usr/local/bin/docker-compose /usr/local/lib/docker/cli-plugins/docker-compose"
    fi
fi

DOCKER_VERSION=$(docker --version 2>/dev/null || echo "unknown")
log "  Docker: ${DOCKER_VERSION}"

# Check if dockerd is running
if ! docker info &>/dev/null 2>&1; then
    err "Docker daemon is not running. Start it with: sudo dockerd &"
fi
log "  Docker daemon: running (OK)"

# ===========================================================================
# STEP 3: Port Conflict Check
# ===========================================================================
log "Step 3/8: Checking port availability..."

# Source .env to pick up user-configured ports
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

check_port() {
    local port=$1
    local name=$2
    if ss -tlnp 2>/dev/null | grep -q ":${port}\b" || \
       netstat -tlnp 2>/dev/null | grep -q ":${port}\b"; then
        warn "  Port ${port} (${name}) is already in use."
        ((FAILED_CHECKS++)) || true
    else
        log "  Port ${port} (${name}): free (OK)"
    fi
}

check_port "${SVR_WEB_HTTP_PORT:-80}"     "Web UI"
check_port "${SVR_WEB_HTTPS_PORT:-443}"   "Web UI (HTTPS)"
check_port "${OS_PORT:-1201}"             "OpenSearch"
check_port "${TEI_PORT:-6380}"            "TEI Embedding"
check_port "${MINIO_PORT:-9000}"          "MinIO"
check_port "${MINIO_CONSOLE_PORT:-9001}"  "MinIO Console"
check_port "${SVR_HTTP_PORT:-9380}"       "RAGFlow API"
check_port "${ADMIN_SVR_HTTP_PORT:-9381}" "Admin API"

if [ "${FAILED_CHECKS}" -gt 0 ]; then
    echo ""
    warn "  ${FAILED_CHECKS} port(s) are in use."
    warn "  Edit .env to change conflicting ports, then re-run install.sh."
    warn "  For example: SVR_WEB_HTTP_PORT=8080"
    read -rp "  Continue anyway? [y/N] " CONFIRM
    if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 1
    fi
fi

# ===========================================================================
# STEP 4: Load Docker Images
# ===========================================================================
log "Step 4/8: Loading Docker images..."

IMAGE_COUNT=$(ls images/*.tar.gz 2>/dev/null | wc -l)
if [ "${IMAGE_COUNT}" -eq 0 ]; then
    err "No Docker images found in images/ directory."
fi

for img in images/*.tar.gz; do
    img_name=$(basename "$img")

    # Verify file integrity (if checksums exist)
    if [ -f "images/checksums.sha256" ]; then
        EXPECTED=$(grep "${img_name}" images/checksums.sha256 | awk '{print $1}' 2>/dev/null || true)
        if [ -n "${EXPECTED}" ]; then
            ACTUAL=$(sha256sum "$img" | awk '{print $1}')
            if [ "${EXPECTED}" != "${ACTUAL}" ]; then
                warn "  Checksum mismatch for ${img_name} — file may be corrupted."
                warn "  Expected: ${EXPECTED}"
                warn "  Actual:   ${ACTUAL}"
                read -rp "  Continue anyway? [y/N] " CONFIRM
                if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
                    echo "Aborted."
                    exit 1
                fi
            fi
        fi
    fi

    log "  Loading ${img_name}..."
    if ! gunzip -c "$img" | docker load; then
        err "Failed to load ${img_name}. The file may be corrupted — try re-extracting the package."
    fi
done

log "  All images loaded successfully."

# ===========================================================================
# STEP 5: Verify Loaded Images
# ===========================================================================
log "Step 5/8: Verifying loaded images..."

REQUIRED_IMAGES=(
    "mysql:8.0.39"
    "opensearchproject/opensearch:2.19.1"
    "pgsty/minio:RELEASE.2026-03-25T00-00-00Z"
    "valkey/valkey:8"
    "infiniflow/ragflow:v0.25.6"
    "infiniflow/text-embeddings-inference:cpu-1.8"
)

for img in "${REQUIRED_IMAGES[@]}"; do
    if docker image inspect "${img}" &>/dev/null; then
        log "  OK: ${img}"
    else
        err "Missing image: ${img}. The image tar.gz may be corrupted."
    fi
done

# ===========================================================================
# STEP 6: Generate Keys & Prepare Directories
# ===========================================================================
log "Step 6/8: Preparing directories and generating RSA keys..."

mkdir -p ragflow-logs conf

# Generate fresh RSA key pair for password encryption.
# These are NOT the keys from the git repo — each deployment gets unique keys.
# The passphrase must match what api/utils/crypt.py expects ("Welcome").
if [ ! -f conf/private.pem ] || [ ! -f conf/public.pem ]; then
    log "  Generating unique RSA key pair for this deployment..."
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
        -aes-256-cbc -pass pass:Welcome \
        -out conf/private.pem 2>/dev/null
    openssl pkey -in conf/private.pem -passin pass:Welcome \
        -pubout -out conf/public.pem 2>/dev/null
    chmod 600 conf/private.pem
    chmod 644 conf/public.pem
    log "  RSA keys generated: conf/private.pem, conf/public.pem"
else
    log "  RSA keys already exist (OK)"
fi

# ===========================================================================
# STEP 7: Start Services
# ===========================================================================
log "Step 7/8: Starting RAGFlow services..."

docker compose -f docker-compose.offline.yml up -d

log "  Compose started. Waiting for service initialization..."

# ===========================================================================
# STEP 8: Health Checks
# ===========================================================================
log "Step 8/8: Running health checks..."

# --- 8a. MySQL ---
log "  Waiting for MySQL..."
MYSQL_OK=false
for i in $(seq 1 60); do
    if docker compose -f docker-compose.offline.yml exec -T mysql \
        mysqladmin ping -uroot -p"${MYSQL_PASSWORD:-infini_rag_flow}" --silent 2>/dev/null; then
        log "  MySQL is ready."
        MYSQL_OK=true
        break
    fi
    sleep 2
done
[ "${MYSQL_OK}" = false ] && warn "  MySQL did not become ready in time. Check: docker compose logs mysql"

# --- 8b. OpenSearch ---
log "  Waiting for OpenSearch..."
OS_OK=false
for i in $(seq 1 60); do
    if curl -s -o /dev/null -w "%{http_code}" "http://localhost:${OS_PORT:-1201}" 2>/dev/null | grep -qE "200|401"; then
        log "  OpenSearch is ready."
        OS_OK=true
        break
    fi
    sleep 2
done
[ "${OS_OK}" = false ] && warn "  OpenSearch did not become ready in time. Check: docker compose logs opensearch01"

# --- 8c. Redis ---
log "  Waiting for Redis..."
REDIS_OK=false
for i in $(seq 1 30); do
    if docker compose -f docker-compose.offline.yml exec -T redis \
        redis-cli -a "${REDIS_PASSWORD:-infini_rag_flow}" --no-auth-warning ping 2>/dev/null | grep -q PONG; then
        log "  Redis is ready."
        REDIS_OK=true
        break
    fi
    sleep 2
done
[ "${REDIS_OK}" = false ] && warn "  Redis did not become ready in time."

# --- 8d. MinIO ---
log "  Waiting for MinIO..."
MINIO_OK=false
for i in $(seq 1 30); do
    if curl -s -f -o /dev/null "http://localhost:${MINIO_PORT:-9000}/minio/health/live" 2>/dev/null; then
        log "  MinIO is ready."
        MINIO_OK=true
        break
    fi
    sleep 2
done
[ "${MINIO_OK}" = false ] && warn "  MinIO did not become ready in time."

# --- 8e. TEI (bge-m3 loading takes 1-2 minutes) ---
log "  Waiting for TEI to load bge-m3 model..."
TEI_OK=false
TEI_RETRIES=180  # 6 minutes max for slow machines
log "  (This loads the 2GB bge-m3 model into memory — may take 1-3 minutes)"
for i in $(seq 1 ${TEI_RETRIES}); do
    RESPONSE=$(curl -s -X POST "http://localhost:${TEI_PORT:-6380}/embed" \
        -H "Content-Type: application/json" \
        -d '{"inputs":"warmup"}' 2>/dev/null || true)
    if echo "${RESPONSE}" | grep -q "embedding"; then
        # Show model name from response for verification
        MODEL_NAME=$(echo "${RESPONSE}" | grep -o '"model":"[^"]*"' | head -1 | sed 's/"model":"\(.*\)"/\1/')
        log "  TEI is ready (model: ${MODEL_NAME:-bge-m3})."
        TEI_OK=true
        break
    fi
    # Show progress every 30 seconds
    if [ $(( i % 15 )) -eq 0 ]; then
        info "  Still waiting... (${i}s elapsed)"
    fi
    sleep 2
done
if [ "${TEI_OK}" = false ]; then
    warn "  TEI did not become ready. Check logs: docker compose logs tei"
    warn "  Common causes:"
    warn "    - Out of memory (bge-m3 needs ~3.5GB; system has ${TOTAL_MEM_GB}GB total)."
    warn "      Check dmesg for OOM kills: dmesg | grep -i oom"
    warn "    - Model files missing or corrupted."
    warn "      Verify: ls models/BAAI/bge-m3/ should contain pytorch_model.bin"
fi

# --- 8f. RAGFlow API ---
log "  Waiting for RAGFlow API..."
RAGFLOW_OK=false
for i in $(seq 1 90); do
    if curl -s -o /dev/null "http://localhost:${SVR_HTTP_PORT:-9380}/api/v1/system/healthz" 2>/dev/null; then
        log "  RAGFlow API is ready."
        RAGFLOW_OK=true
        break
    fi
    sleep 2
done
[ "${RAGFLOW_OK}" = false ] && warn "  RAGFlow API did not become ready in time. Check: docker compose logs ragflow"

# ===========================================================================
# Summary
# ===========================================================================
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  RAGFlow deployment complete!              ${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""

SERVICE_STATUS=""
[ "${MYSQL_OK}" = false ]     && SERVICE_STATUS="${SERVICE_STATUS} MySQL:FAILED"
[ "${OS_OK}" = false ]       && SERVICE_STATUS="${SERVICE_STATUS} OpenSearch:FAILED"
[ "${REDIS_OK}" = false ]    && SERVICE_STATUS="${SERVICE_STATUS} Redis:FAILED"
[ "${MINIO_OK}" = false ]    && SERVICE_STATUS="${SERVICE_STATUS} MinIO:FAILED"
[ "${TEI_OK}" = false ]      && SERVICE_STATUS="${SERVICE_STATUS} TEI:FAILED"
[ "${RAGFLOW_OK}" = false ]  && SERVICE_STATUS="${SERVICE_STATUS} RAGFlow:FAILED"
if [ -z "${SERVICE_STATUS}" ]; then
    SERVICE_STATUS="all OK"
    STATUS_COLOR="${GREEN}"
else
    STATUS_COLOR="${RED}"
fi

echo -e "  Status:    ${STATUS_COLOR}${SERVICE_STATUS}${NC}"
echo -e "  Web UI:    ${BLUE}http://localhost${NC}"
echo -e "  API:       ${BLUE}http://localhost:${SVR_HTTP_PORT:-9380}${NC}"
echo -e "  Admin:     ${BLUE}http://localhost:${ADMIN_SVR_HTTP_PORT:-9381}${NC}"
echo ""

if [ "${TEI_OK}" = false ] || [ "${RAGFLOW_OK}" = false ]; then
    echo -e "${RED}  Some services failed to start.${NC}"
    echo -e "  Check logs: ${BLUE}docker compose -f docker-compose.offline.yml logs${NC}"
    echo -e "  Rollback:   ${BLUE}sudo bash uninstall.sh${NC}"
    echo ""
fi

echo -e "${YELLOW}  Post-deployment (UI configuration):${NC}"
echo -e "   1. Open ${BLUE}http://localhost${NC} → Register an account"
echo -e "   2. Add embedding model (bge-m3 is already serving via TEI):"
echo -e "      Model Providers > Add > OpenAI-API-Compatible"
echo -e "      Base URL: http://tei:80    Model: BAAI/bge-m3    API Key: xxx"
echo -e "   3. Add chat model (external LLM — see README.md)"
echo ""
echo -e "  Management:"
echo -e "    View logs:     ${BLUE}docker compose -f docker-compose.offline.yml logs -f${NC}"
echo -e "    Restart:       ${BLUE}docker compose -f docker-compose.offline.yml restart${NC}"
echo -e "    Stop:          ${BLUE}docker compose -f docker-compose.offline.yml down${NC}"
echo -e "    Uninstall:     ${RED}sudo bash uninstall.sh${NC}"
echo -e "    Full cleanup:  ${RED}sudo bash uninstall.sh --images${NC}"
echo ""
