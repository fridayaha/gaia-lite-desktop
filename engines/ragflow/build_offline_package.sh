#!/usr/bin/env bash
# =============================================================================
# RAGFlow Offline Package - Build Script
# =============================================================================
# Run this on an internet-connected machine (x86_64 Linux) to prepare the
# offline deployment package.
#
# Prerequisites:
#   - Docker Engine 20.10+ (with docker compose v2)
#   - ~30 GB free disk space
#   - curl, gzip, tar, sha256sum
#   - Internet access (model files are downloaded from HuggingFace CDN)
#
# Usage:
#   bash build_offline_package.sh                 # Global (Docker Hub)
#   bash build_offline_package.sh --china-mirrors  # China mirrors
#
# Output:
#   ragflow-offline-v0.25.6/     (package directory)
#   ragflow-offline-v0.25.6.tar.gz  (~8-10 GB archive)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
CHINA_MIRRORS=false
for arg in "$@"; do
    case "$arg" in
        --china-mirrors) CHINA_MIRRORS=true ;;
        *) echo "Unknown option: $arg"; echo "Usage: bash build_offline_package.sh [--china-mirrors]"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Registry configuration
# ---------------------------------------------------------------------------
if [ "${CHINA_MIRRORS}" = true ]; then
    DOCKER_PROXY="docker.1ms.run"
    HF_ENDPOINT="https://hf-mirror.com"
else
    DOCKER_PROXY=""
    HF_ENDPOINT=""
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PACKAGE_DIR="${SCRIPT_DIR}/ragflow-offline-v0.25.6"
IMAGES_DIR="${PACKAGE_DIR}/images"
MODELS_DIR="${PACKAGE_DIR}/models"
CHECKSUMS_FILE="${IMAGES_DIR}/checksums.sha256"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[BUILD]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ---------------------------------------------------------------------------
# Diagnostic & fallback for docker save failures (containerd-snapshotter)
# ---------------------------------------------------------------------------
# When Docker uses containerd-snapshotter, images with many layers may have
# their layer data stored as containerd snapshots rather than exportable blobs.
# docker save then produces only the manifest (~16KB) instead of the full image.
# The fallback below works around this by exporting the container filesystem
# and reconstructing the image with all metadata preserved.

diagnose_docker_save() {
    local img="$1"
    local filename="$2"
    local size="$3"

    warn "  ═══════════════════════════════════════════════"
    warn "  DIAGNOSTIC: docker save produced only ${size} bytes for ${img}"
    warn "  This usually indicates containerd-snapshotter stores layers as"
    warn "  snapshots that docker save cannot export (common with images"
    warn "  having many layers, e.g., buildkit-built images)."

    local driver_type
    driver_type=$(docker info 2>/dev/null | grep 'driver-type' | sed 's/^[[:space:]]*//' || echo "unavailable")
    warn "  driver-type: ${driver_type}"

    local layer_count image_size blob_count
    layer_count=$(docker image inspect "${img}" --format '{{len .RootFS.Layers}}' 2>/dev/null || echo "?")
    image_size=$(docker image inspect "${img}" --format '{{.Size}}' 2>/dev/null | awk '{printf "%.1fGB", $1/1073741824}' || echo "?")
    warn "  layers: ${layer_count}, image_compressed_size: ${image_size}"

    blob_count=$(gunzip -c "${filename}" 2>/dev/null | tar t 2>/dev/null | grep -c 'blobs/sha256/' || echo "0")
    warn "  blobs_in_tar: ${blob_count} (expected ~$((layer_count + 1)): ${layer_count} layers + 1 config)"
    warn "  ═══════════════════════════════════════════════"
}

fallback_save_image() {
    local canonical="$1"
    local output="$2"
    local fname
    fname=$(basename "${output}")

    log "  ── Fallback: docker export → import → save for ${canonical} ──"

    # Step 1: Create a container (don't start it)
    local cid
    cid=$(docker create "${canonical}" 2>&1) || {
        warn "  Fallback: could not create container from ${canonical}"
        return 1
    }
    log "  Container created: ${cid:0:12}"

    # Step 2: Export the full filesystem
    local tmp_tar="${IMAGES_DIR}/.fallback_${fname}.tar"
    log "  Exporting filesystem (may take several minutes for large images)..."
    if ! docker export "${cid}" > "${tmp_tar}" 2>/dev/null; then
        warn "  Fallback: docker export failed"
        docker rm "${cid}" 2>/dev/null || true
        rm -f "${tmp_tar}"
        return 1
    fi
    log "  Filesystem exported: $(du -h "${tmp_tar}" 2>/dev/null | cut -f1)"

    # Step 3: Extract metadata from original image
    local changes_args=()

    # ENV vars (each line is "KEY=VALUE")
    while IFS= read -r env_line; do
        [ -z "${env_line}" ] && continue
        changes_args+=(-c "ENV ${env_line}")
    done < <(docker image inspect "${canonical}" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null)

    # WORKDIR
    local wd
    wd=$(docker image inspect "${canonical}" --format '{{.Config.WorkingDir}}' 2>/dev/null)
    [ -n "${wd}" ] && [ "${wd}" != "<nil>" ] && changes_args+=(-c "WORKDIR ${wd}")

    # USER
    local usr
    usr=$(docker image inspect "${canonical}" --format '{{.Config.User}}' 2>/dev/null)
    [ -n "${usr}" ] && [ "${usr}" != "<nil>" ] && changes_args+=(-c "USER ${usr}")

    # ENTRYPOINT (JSON array, e.g. ["./entrypoint.sh"])
    local ep
    ep=$(docker image inspect "${canonical}" --format '{{json .Config.Entrypoint}}' 2>/dev/null)
    [ -n "${ep}" ] && [ "${ep}" != "null" ] && changes_args+=(-c "ENTRYPOINT ${ep}")

    # CMD (JSON array or null)
    local cmd
    cmd=$(docker image inspect "${canonical}" --format '{{json .Config.Cmd}}' 2>/dev/null)
    [ -n "${cmd}" ] && [ "${cmd}" != "null" ] && changes_args+=(-c "CMD ${cmd}")

    # EXPOSE
    while IFS= read -r port; do
        [ -z "${port}" ] && continue
        changes_args+=(-c "EXPOSE ${port}")
    done < <(docker image inspect "${canonical}" --format '{{range $p, $_ := .Config.ExposedPorts}}{{println $p}}{{end}}' 2>/dev/null)

    # VOLUME
    while IFS= read -r vol; do
        [ -z "${vol}" ] && continue
        changes_args+=(-c "VOLUME ${vol}")
    done < <(docker image inspect "${canonical}" --format '{{range $v, $_ := .Config.Volumes}}{{println $v}}{{end}}' 2>/dev/null)

    # LABEL
    while IFS='=' read -r lkey lval; do
        [ -z "${lkey}" ] && continue
        changes_args+=(-c "LABEL ${lkey}=${lval}")
    done < <(docker image inspect "${canonical}" --format '{{range $k, $v := .Config.Labels}}{{println $k "=" $v}}{{end}}' 2>/dev/null)

    log "  Metadata extracted: ${#changes_args[@]} change flags"

    # Step 4: Import filesystem with all metadata (overwrites the original tag)
    log "  Importing with metadata..."
    if ! docker import "${changes_args[@]}" "${tmp_tar}" "${canonical}" 2>/dev/null; then
        warn "  Fallback: docker import failed"
        docker rm "${cid}" 2>/dev/null || true
        rm -f "${tmp_tar}"
        return 1
    fi

    # Step 5: Save the reconstructed image
    log "  Saving reconstructed image..."
    if ! docker save "${canonical}" | gzip > "${output}" 2>/dev/null; then
        warn "  Fallback: docker save of reconstructed image failed"
        docker rm "${cid}" 2>/dev/null || true
        rm -f "${tmp_tar}"
        return 1
    fi

    # Step 6: Cleanup
    docker rm "${cid}" 2>/dev/null || true
    rm -f "${tmp_tar}"

    local final_size
    final_size=$(stat -c%s "${output}" 2>/dev/null || echo 0)
    if [ "${final_size}" -lt 10485760 ]; then
        warn "  Fallback also produced a small file (${final_size} bytes)"
        docker rm "${cid}" 2>/dev/null || true
        rm -f "${tmp_tar}"
        return 1
    fi

    log "  ── Fallback succeeded: ${fname} ($(du -h "${output}" | cut -f1)) ──"
    return 0
}

# ---------------------------------------------------------------------------
# Step 0: Check prerequisites
# ---------------------------------------------------------------------------
log "Step 0/5: Checking prerequisites..."

REQUIRED_CMDS=(docker curl gzip tar sha256sum)
for cmd in "${REQUIRED_CMDS[@]}"; do
    command -v "$cmd" &>/dev/null || err "'${cmd}' is required but not installed."
done

# Verify architecture
ARCH=$(uname -m)
if [ "${ARCH}" != "x86_64" ]; then
    err "This build script must run on x86_64. Detected: ${ARCH}"
fi

# Check disk space (for pulling images, saving, and final archive)
AVAILABLE_GB=$(df -BG "${SCRIPT_DIR}" | awk 'NR==2 {gsub(/G/,"",$4); print $4}')
if [ "${AVAILABLE_GB}" -lt 30 ]; then
    err "Insufficient disk space: ${AVAILABLE_GB}GB. Need at least 30GB."
fi
log "  Architecture: ${ARCH}, Disk: ${AVAILABLE_GB}GB (OK)"

# Create package directory structure
mkdir -p "${IMAGES_DIR}" "${MODELS_DIR}"
# Clean stale files from previous builds to avoid duplicates
rm -f "${IMAGES_DIR}"/*.tar.gz "${IMAGES_DIR}"/checksums.sha256 "${IMAGES_DIR}"/.fallback_*
rm -f "${CHECKSUMS_FILE}"

# ---------------------------------------------------------------------------
# Step 1: Pull and save Docker images (idempotent)
# ---------------------------------------------------------------------------
log "Step 1/5: Pulling Docker images..."
if [ "${CHINA_MIRRORS}" = true ]; then
    log "  Using China mirror: ${DOCKER_PROXY}"
fi

# Each entry: "canonical_image_name"
# When --china-mirrors is set, images are pulled from ${DOCKER_PROXY}/...
# and then re-tagged to the canonical name.
IMAGES=(
    "mysql:8.0.39"
    "opensearchproject/opensearch:2.19.1"
    "pgsty/minio:RELEASE.2026-03-25T00-00-00Z"
    "valkey/valkey:8"
    "infiniflow/ragflow:v0.25.6"
    "infiniflow/text-embeddings-inference:cpu-1.8"
)

pull_image() {
    local canonical="$1"

    # Already cached locally?
    if docker image inspect "${canonical}" &>/dev/null 2>&1; then
        log "  Already present: ${canonical}"
        return 0
    fi

    if [ "${CHINA_MIRRORS}" = true ]; then
        local mirror_ref="${DOCKER_PROXY}/${canonical}"
        log "  Pulling from mirror: ${mirror_ref}"
        if docker pull "${mirror_ref}"; then
            docker tag "${mirror_ref}" "${canonical}"
            docker rmi "${mirror_ref}" 2>/dev/null || true
            log "  Re-tagged to: ${canonical}"
        else
            # Fallback: try direct pull
            warn "  Mirror pull failed, trying direct..."
            docker pull "${canonical}" || err "Failed to pull ${canonical}"
        fi
    else
        log "  Pulling: ${canonical}..."
        docker pull "${canonical}" || err "Failed to pull ${canonical}. Check network and Docker Hub access."
    fi
}

# Pull images sequentially (mirror proxy may throttle parallel pulls)
for img in "${IMAGES[@]}"; do
    pull_image "${img}"
done

# Save images to tar.gz (skip if tar.gz already exists and the image hasn't changed)
log "  Saving images to tar.gz..."
for img in "${IMAGES[@]}"; do
    fname=$(echo "${img}" | tr '/:' '-')
    output="${IMAGES_DIR}/${fname}.tar.gz"

    if [ -f "${output}" ]; then
        # Check integrity: a real image tar.gz should be > 10MB minimum
        existing_size=$(stat -c%s "${output}" 2>/dev/null || echo 0)
        if [ "${existing_size}" -gt 10485760 ] && gunzip -t "${output}" 2>/dev/null; then
            log "  Skipping (already saved): ${fname}.tar.gz"
            continue
        else
            warn "  Existing ${fname}.tar.gz is incomplete (${existing_size} bytes) — re-saving..."
            rm -f "${output}"
        fi
    fi
    log "  Saving: ${img} -> images/${fname}.tar.gz"
    docker save "${img}" | gzip > "${output}" &
done
wait

# Verify each saved file is non-empty, valid gzip, and not suspiciously small
log "  Verifying saved images..."
for img in "${IMAGES[@]}"; do
    fname=$(echo "${img}" | tr '/:' '-')
    output="${IMAGES_DIR}/${fname}.tar.gz"

    size=$(stat -c%s "${output}" 2>/dev/null || echo 0)

    if [ "${size}" -lt 1024 ]; then
        warn "  EMPTY/TRIVIAL FILE (${size} bytes): ${fname}.tar.gz — will retry..."
        docker save "${img}" | gzip > "${output}"
        size=$(stat -c%s "${output}" 2>/dev/null || echo 0)
    fi

    if ! gunzip -t "${output}" 2>/dev/null; then
        warn "  CORRUPT GZIP: ${fname}.tar.gz — will retry..."
        docker save "${img}" | gzip > "${output}"
        size=$(stat -c%s "${output}" 2>/dev/null || echo 0)
        if ! gunzip -t "${output}" 2>/dev/null; then
            err "Failed to create valid tar.gz for ${img}"
        fi
    fi

    # Manifest-only tar.gz can be small but valid gzip. Check for real layers.
    # A valid docker image tar.gz always exceeds 10MB (even the smallest mysql is ~160MB).
    if [ "${size}" -lt 10485760 ]; then
        warn "  SUSPICIOUSLY SMALL (${size} bytes): ${fname}.tar.gz — may be manifest-only, retrying..."
        docker save "${img}" | gzip > "${output}"
        size=$(stat -c%s "${output}" 2>/dev/null || echo 0)
        if [ "${size}" -lt 10485760 ]; then
            diagnose_docker_save "${img}" "${output}" "${size}"
            warn "  docker save still produced only ${size} bytes — trying fallback..."
            if fallback_save_image "${img}" "${output}"; then
                size=$(stat -c%s "${output}" 2>/dev/null || echo 0)
            else
                err "Both docker save and fallback export failed for ${img}. See diagnostics above."
            fi
        fi
    fi

    log "  OK: ${fname}.tar.gz ($(du -h "${output}" | cut -f1))"

    # Generate checksum (use basename so verification works regardless of cwd)
    (cd "${IMAGES_DIR}" && sha256sum "${fname}.tar.gz") >> "${CHECKSUMS_FILE}"
done
log "  All images saved and verified."

# ---------------------------------------------------------------------------
# Step 2: Download TEI embedding model (BAAI/bge-m3)
# ---------------------------------------------------------------------------
log "Step 2/5: Downloading bge-m3 embedding model..."

TEI_MODEL="BAAI/bge-m3"
MODEL_TARGET="${MODELS_DIR}/${TEI_MODEL}"

# Required model files with minimum sizes (bytes) and download URLs
# Uses curl to download directly from HuggingFace CDN — no Python/pip dependency.
# BAAI/bge-m3 uses pytorch_model.bin (not safetensors) and sentencepiece tokenizer.
declare -A MODEL_FILES
MODEL_FILES["pytorch_model.bin"]=2000000000      # ~2.1GB
MODEL_FILES["sentencepiece.bpe.model"]=4000000   # ~4.8MB
MODEL_FILES["tokenizer.json"]=10000000           # ~10MB
MODEL_FILES["config.json"]=10
MODEL_FILES["tokenizer_config.json"]=10
MODEL_FILES["special_tokens_map.json"]=10
MODEL_FILES["sentence_bert_config.json"]=10
MODEL_FILES["modules.json"]=10
MODEL_FILES["1_Pooling/config.json"]=10

# Build base URL for downloads
if [ -n "${HF_ENDPOINT}" ]; then
    HF_BASE="${HF_ENDPOINT}/${TEI_MODEL}/resolve/main"
    log "  Using HuggingFace mirror: ${HF_ENDPOINT}"
else
    HF_BASE="https://huggingface.co/${TEI_MODEL}/resolve/main"
fi

NEED_DOWNLOAD=false
for fname in "${!MODEL_FILES[@]}"; do
    fpath="${MODEL_TARGET}/${fname}"
    min_size=${MODEL_FILES[${fname}]}

    if [ ! -f "${fpath}" ]; then
        NEED_DOWNLOAD=true
        break
    fi

    actual_size=$(stat -c%s "${fpath}" 2>/dev/null || echo 0)
    if [ "${actual_size}" -lt "${min_size}" ]; then
        warn "  ${fname} is too small (${actual_size} < ${min_size} bytes) — re-downloading."
        NEED_DOWNLOAD=true
        break
    fi
done

if [ "${NEED_DOWNLOAD}" = false ]; then
    log "  Model already downloaded with valid files (OK)."
else
    mkdir -p "${MODEL_TARGET}/1_Pooling"

    for fname in "${!MODEL_FILES[@]}"; do
        url="${HF_BASE}/${fname}"
        fpath="${MODEL_TARGET}/${fname}"
        min_size=${MODEL_FILES[${fname}]}

        log "  Downloading ${fname}..."
        curl --retry 3 --retry-delay 5 --retry-all-errors \
            -fSL --progress-bar -o "${fpath}" "${url}" || \
            err "Failed to download ${fname} from ${url}"

        actual_size=$(stat -c%s "${fpath}" 2>/dev/null || echo 0)
        if [ "${actual_size}" -lt "${min_size}" ]; then
            err "  Model file ${fname} too small after download (${actual_size} bytes). Check: ${url}"
        fi
        log "  OK: ${fname} ($(du -h "${fpath}" | cut -f1))"
    done
    log "  Model downloaded and verified: ${MODEL_TARGET}"
fi

# Calculate total model size for info
MODEL_SIZE=$(du -sh "${MODEL_TARGET}" 2>/dev/null | cut -f1)
log "  Model size: ${MODEL_SIZE}"

# ---------------------------------------------------------------------------
# Step 3: Copy configuration files
# ---------------------------------------------------------------------------
log "Step 3/5: Copying configuration files..."

# offline-package-specific files (copied from current directory)
cp "${SCRIPT_DIR}/.env"                        "${PACKAGE_DIR}/"
cp "${SCRIPT_DIR}/docker-compose.offline.yml"  "${PACKAGE_DIR}/"

# Note: conf/ keys are NOT copied — they are generated fresh at install time.
# This prevents hardcoded RSA keys from being distributed in the package.

# Shared files (copied from repo docker/ directory)
REPO_DOCKER="${SCRIPT_DIR}/../docker"
cp "${REPO_DOCKER}/service_conf.yaml.template"  "${PACKAGE_DIR}/"
cp "${REPO_DOCKER}/init.sql"                    "${PACKAGE_DIR}/"
cp "${REPO_DOCKER}/entrypoint.sh"               "${PACKAGE_DIR}/"
cp "${REPO_DOCKER}/infinity_conf.toml"          "${PACKAGE_DIR}/"
cp -r "${REPO_DOCKER}/nginx"                    "${PACKAGE_DIR}/"
mkdir -p "${PACKAGE_DIR}/ragflow-logs"

# Copy scripts
cp "${SCRIPT_DIR}/install.sh"                  "${PACKAGE_DIR}/"
cp "${SCRIPT_DIR}/uninstall.sh"                "${PACKAGE_DIR}/"
cp "${SCRIPT_DIR}/README.md"                   "${PACKAGE_DIR}/"
chmod +x "${PACKAGE_DIR}/install.sh"
chmod +x "${PACKAGE_DIR}/uninstall.sh"
chmod +x "${PACKAGE_DIR}/entrypoint.sh"

log "  Configuration files copied."

# ---------------------------------------------------------------------------
# Step 4: Final verification
# ---------------------------------------------------------------------------
log "Step 4/5: Verifying package integrity..."

PACKAGE_OK=true

# Check all required files exist
REQUIRED_FILES=(
    "images/mysql-8.0.39.tar.gz"
    "images/opensearchproject-opensearch-2.19.1.tar.gz"
    "images/pgsty-minio-RELEASE.2026-03-25T00-00-00Z.tar.gz"
    "images/valkey-valkey-8.tar.gz"
    "images/infiniflow-ragflow-v0.25.6.tar.gz"
    "images/infiniflow-text-embeddings-inference-cpu-1.8.tar.gz"
    "images/checksums.sha256"
    "models/BAAI/bge-m3/pytorch_model.bin"
    "models/BAAI/bge-m3/sentencepiece.bpe.model"
    ".env"
    "docker-compose.offline.yml"
    "service_conf.yaml.template"
    "init.sql"
    "entrypoint.sh"
    "install.sh"
    "uninstall.sh"
    "README.md"
)

for f in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "${PACKAGE_DIR}/${f}" ] && [ ! -d "${PACKAGE_DIR}/${f}" ]; then
        warn "  MISSING: ${f}"
        PACKAGE_OK=false
    else
        size=$(du -sh "${PACKAGE_DIR}/${f}" 2>/dev/null | cut -f1)
        log "  OK: ${f} (${size})"
    fi
done

# Verify checksums
log "  Verifying image checksums..."
while read -r expected_hash filename; do
    fpath="${IMAGES_DIR}/${filename}"
    if [ -f "${fpath}" ]; then
        actual_hash=$(sha256sum "${fpath}" | awk '{print $1}')
        if [ "${expected_hash}" = "${actual_hash}" ]; then
            log "  Checksum OK: ${filename}"
        else
            warn "  CHECKSUM MISMATCH: ${filename}"
            warn "    Expected: ${expected_hash}"
            warn "    Actual:   ${actual_hash}"
            PACKAGE_OK=false
        fi
    else
        warn "  Checksum file not found: ${fpath}"
        PACKAGE_OK=false
    fi
done < "${CHECKSUMS_FILE}"

if [ "${PACKAGE_OK}" = false ]; then
    err "Package verification failed. See warnings above."
fi

# ---------------------------------------------------------------------------
# Step 5: Create final archive
# ---------------------------------------------------------------------------
log "Step 5/5: Creating archive..."

ARCHIVE_NAME="ragflow-offline-v0.25.6.tar.gz"

# Remove stale archive if present
rm -f "${SCRIPT_DIR}/${ARCHIVE_NAME}"

cd "${SCRIPT_DIR}"
tar czf "${ARCHIVE_NAME}" "ragflow-offline-v0.25.6"

if [ ! -s "${SCRIPT_DIR}/${ARCHIVE_NAME}" ]; then
    err "Archive creation failed — output file is empty."
fi

# Verify archive integrity
if ! tar tzf "${ARCHIVE_NAME}" >/dev/null 2>&1; then
    err "Archive verification failed — the tar.gz may be corrupted."
fi

ARCHIVE_SIZE=$(du -h "${ARCHIVE_NAME}" | cut -f1)
ARCHIVE_SHA256=$(sha256sum "${ARCHIVE_NAME}" | awk '{print $1}')

log "=============================================="
log "Build complete!"
log "  Archive: ${SCRIPT_DIR}/${ARCHIVE_NAME}"
log "  Size:    ${ARCHIVE_SIZE}"
log "  SHA256:  ${ARCHIVE_SHA256}"
log ""
log "To deploy on the target machine:"
log "  1. Copy ${ARCHIVE_NAME} to the target machine"
log "  2. Verify: sha256sum ${ARCHIVE_NAME}"
log "  3. tar xzf ${ARCHIVE_NAME}"
log "  4. cd ragflow-offline-v0.25.6"
log "  5. sudo bash install.sh"
log "=============================================="
