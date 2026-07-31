#!/bin/bash
# ============================================================
# Gaia 部署制品打包脚本
# 收集清单模板 + 部署脚本 + 镜像 OCI tar + 特殊 jar → 单个 tar.gz
#
# 用法: bash deploy/ci/package.sh <VERSION> [ARCH_TAG]
#   ARCH_TAG: multi（默认，双架构）| arm64 | amd64
# 前置: make docker-buildx VERSION=<VERSION> PLATFORMS=<...> 已产出镜像 tar
# 产物: dist/gaia-deploy-<VERSION>-<ARCH_TAG>.tar.gz
# ============================================================
set -e

VERSION="${1:?用法: bash deploy/ci/package.sh <VERSION> [ARCH_TAG]}"
ARCH_TAG="${2:-multi}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DIST_DIR="${REPO_ROOT}/dist"
IMAGES_DIR="${DIST_DIR}/images"
PKG_DIR="${DIST_DIR}/gaia-deploy-${VERSION}"
TARBALL="${DIST_DIR}/gaia-deploy-${VERSION}-${ARCH_TAG}.tar.gz"

echo "=========================================="
echo "  打包 Gaia 部署制品"
echo "  版本: ${VERSION}"
echo "  架构: ${ARCH_TAG}"
echo "=========================================="

# 1. 校验镜像 tar 是否已构建
echo "[1/5] 校验镜像 OCI tar [${ARCH_TAG}] ..."
MISSING=0
for svc in api trino-plugins gravitino-libs better-auth web-ui; do
  tar="${IMAGES_DIR}/gaia-${svc}-${VERSION}-${ARCH_TAG}.tar"
  if [ ! -f "${tar}" ]; then
    echo "  ❌ 缺失: ${tar}（请先 make docker-buildx VERSION=${VERSION} PLATFORMS=<对应架构>）"
    MISSING=1
  else
    size=$(du -h "${tar}" | cut -f1)
    echo "  ✅ gaia-${svc}  ${size}"
  fi
done
[ ${MISSING} -eq 1 ] && exit 1

# 2. 准备打包目录（清掉旧的）
echo "[2/5] 准备打包目录 ..."
rm -rf "${PKG_DIR}"
mkdir -p "${PKG_DIR}"/{manifests/infra/core,manifests/infra/optional,manifests/services,manifests/apps,scripts,images,jars}

# 3. 拷贝清单模板（保留 ${VAR} 占位符，部署时 envsubst 注入）
echo "[3/5] 拷贝清单模板 ..."
cp "${REPO_ROOT}/deploy/k8s/namespace.yaml" "${PKG_DIR}/manifests/"
cp "${REPO_ROOT}/deploy/k8s/infra/core/"*.yaml "${PKG_DIR}/manifests/infra/core/"
cp "${REPO_ROOT}/deploy/k8s/infra/optional/"*.yaml "${PKG_DIR}/manifests/infra/optional/"
cp "${REPO_ROOT}/deploy/k8s/services/"*.yaml "${PKG_DIR}/manifests/services/"
cp "${REPO_ROOT}/deploy/k8s/apps/"*.yaml "${PKG_DIR}/manifests/apps/"
# Secret 模板（envsubst）
cp "${REPO_ROOT}/deploy/ci/secret.yaml" "${PKG_DIR}/secret.yaml.template"
# 配置示例
cp "${REPO_ROOT}/deploy/ci/.env.local.example" "${PKG_DIR}/.env.local.example"

# 4. 拷贝部署脚本
echo "[4/5] 拷贝部署脚本 ..."
cp "${REPO_ROOT}/deploy/ci/deploy.sh"        "${PKG_DIR}/scripts/"
cp "${REPO_ROOT}/deploy/ci/preflight.sh"     "${PKG_DIR}/scripts/"
cp "${REPO_ROOT}/deploy/ci/load-images.sh"   "${PKG_DIR}/scripts/"
cp "${REPO_ROOT}/deploy/ci/envsubst-all.sh"  "${PKG_DIR}/scripts/"
cp "${REPO_ROOT}/deploy/ci/make-first-admin.sh" "${PKG_DIR}/scripts/"

# 拷贝多架构镜像 tar
cp "${IMAGES_DIR}"/gaia-*-${VERSION}-${ARCH_TAG}.tar "${PKG_DIR}/images/"

# 特殊 jar 依赖（ADR-014 国产库 JDBC）已内置进 gaia-gravitino-libs 镜像，
# 此处仅保留非镜像化的 jar（如有）。当前国产库驱动已进镜像，目录留空以备扩展。
if [ -d "${REPO_ROOT}/infra/jars" ] && ls "${REPO_ROOT}/infra/jars/"*.jar >/dev/null 2>&1; then
  echo "  注：infra/jars 下的国产库 JDBC 已内置进 gaia-gravitino-libs 镜像，此处不再单独打包"
fi
cat > "${PKG_DIR}/jars/README.md" <<'EOF'
# 特殊 jar 依赖
国产库 JDBC 驱动（opengaussjdbc / kingbase8 / oceanbase-client）已内置进
gaia-gravitino-libs 镜像（见 Dockerfile.gravitino-libs），由 initContainer
拷贝到 Gravitino 各 catalog 的 libs 目录，无需手动挂载。

此目录保留以备未来非镜像化的 jar 依赖扩展。
EOF

# 版本号 + 校验和
echo "${VERSION}" > "${PKG_DIR}/VERSION"

# 5. 打包
echo "[5/5] 打包 tar.gz ..."
tar -czf "${TARBALL}" -C "${DIST_DIR}" "gaia-deploy-${VERSION}"

# 生成校验和
cd "${DIST_DIR}" && sha256sum "gaia-deploy-${VERSION}-${ARCH_TAG}.tar.gz" > "${TARBALL}.sha256"

echo ""
echo "✅ 打包完成"
echo "  制品: ${TARBALL}"
echo "  大小: $(du -h "${TARBALL}" | cut -f1)"
echo "  校验: ${TARBALL}.sha256"
echo ""
echo "部署方式:"
echo "  tar xzf gaia-deploy-${VERSION}-${ARCH_TAG}.tar.gz"
echo "  cd gaia-deploy-${VERSION}"
echo "  cp .env.local.example .env.local && vi .env.local"
echo "  bash scripts/deploy.sh ${VERSION}"
