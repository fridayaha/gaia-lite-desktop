#!/bin/bash
# Daily build verification for union_agent
# Pulls latest code, builds all Docker images, runs lint, reports status
# Keeps only the 2 most recent verify-* images, prunes older ones to save disk
set -e

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
mkdir -p "$REPO_DIR/logs"
LOG_FILE="$REPO_DIR/logs/daily-build-$(date +%Y%m%d).log"
DATE=$(date "+%Y-%m-%d %H:%M:%S")
TODAY_TAG=$(date +%Y%m%d-%H%M%S)

echo "[$DATE] === Daily Build Verification ===" | tee "$LOG_FILE"

# Step 1: Pull latest code
echo "[$DATE] [1/5] Pulling latest code..." | tee -a "$LOG_FILE"
cd "$REPO_DIR"
git fetch origin develop 2>&1 | tee -a "$LOG_FILE"
git reset --hard origin/develop 2>&1 | tee -a "$LOG_FILE"
echo "[$DATE] Current commit: $(git rev-parse --short HEAD)" | tee -a "$LOG_FILE"

# Step 2: Lint check
echo "[$DATE] [2/5] Running lint check..." | tee -a "$LOG_FILE"
if command -v ruff &>/dev/null; then
    ruff check services/ pkg/ 2>&1 | tee -a "$LOG_FILE" || echo "[WARN] Lint issues found" | tee -a "$LOG_FILE"
    ruff format --check services/ pkg/ 2>&1 | tee -a "$LOG_FILE" || echo "[WARN] Format issues found" | tee -a "$LOG_FILE"
else
    echo "[SKIP] ruff not installed, skipping lint" | tee -a "$LOG_FILE"
fi

# Step 3: Build Docker images
echo "[$DATE] [3/5] Building Docker images..." | tee -a "$LOG_FILE"
IMAGES=(
    "manager:services/manager/Dockerfile"
    "controller:services/controller/Dockerfile"
    "engine-hermes:engines/hermes/Dockerfile"
)

BUILD_SUCCESS=0
BUILD_FAIL=0

for item in "${IMAGES[@]}"; do
    NAME="${item%%:*}"
    DOCKERFILE="${item##*:}"
    echo "[$DATE]   Building unionagents/$NAME..." | tee -a "$LOG_FILE"
    if docker build -t "unionagents/$NAME:verify-$TODAY_TAG" -f "$DOCKERFILE" . 2>&1 | tee -a "$LOG_FILE"; then
        echo "[$DATE]   -> $NAME BUILD OK" | tee -a "$LOG_FILE"
        BUILD_SUCCESS=$((BUILD_SUCCESS + 1))
    else
        echo "[$DATE]   -> $NAME BUILD FAILED" | tee -a "$LOG_FILE"
        BUILD_FAIL=$((BUILD_FAIL + 1))
    fi
done

# Step 4: Cleanup old verify images (keep only 2 most recent per image name)
echo "[$DATE] [4/5] Cleaning up old verify images (keeping 2 most recent)..." | tee -a "$LOG_FILE"
for item in "${IMAGES[@]}"; do
    NAME="${item%%:*}"
    # List verify-* tags for this image, sorted by creation date (newest first)
    # Keep first 2, delete the rest
    OLD_TAGS=$(docker images --format "{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}" \
        | grep "unionagents/$NAME:verify-" \
        | sort -t$'\t' -k2 -r \
        | tail -n +3 \
        | awk -F'\t' '{print $1}')
    if [ -n "$OLD_TAGS" ]; then
        echo "$OLD_TAGS" | while read -r old_tag; do
            echo "[$DATE]   Removing $old_tag" | tee -a "$LOG_FILE"
            docker rmi "$old_tag" -f 2>&1 | tee -a "$LOG_FILE" || true
        done
    else
        echo "[$DATE]   No old tags to clean for $NAME" | tee -a "$LOG_FILE"
    fi
done

# Also prune dangling/untagged images left behind
docker image prune -f 2>&1 | tee -a "$LOG_FILE" || true

# Step 5: Summary
echo "[$DATE] [5/5] Summary" | tee -a "$LOG_FILE"
echo "  Images built successfully: $BUILD_SUCCESS" | tee -a "$LOG_FILE"
echo "  Images failed: $BUILD_FAIL" | tee -a "$LOG_FILE"

if [ "$BUILD_FAIL" -gt 0 ]; then
    echo "[$DATE] === BUILD VERIFICATION: FAILED ===" | tee -a "$LOG_FILE"
    exit 1
else
    echo "[$DATE] === BUILD VERIFICATION: PASSED ===" | tee -a "$LOG_FILE"
    exit 0
fi
