#!/bin/bash
# ============================================================
# UnionAgents (知行) 离线部署包打包脚本
# 用法: bash scripts/package-offline.sh [版本号] [输出目录]
# 示例: bash scripts/package-offline.sh v1.0.0 ./dist
# ============================================================
set -euo pipefail

VERSION="${1:-v1.0.0}"
OUTDIR="${2:-./dist/unionagents-offline-${VERSION}}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================="
echo " UnionAgents 知行  离线包打包 ${VERSION}"
echo "=========================================="

# ── 清理输出目录 ──
rm -rf "$OUTDIR"
mkdir -p "$OUTDIR/images"
mkdir -p "$OUTDIR/manifests/infra"
mkdir -p "$OUTDIR/manifests/services"
mkdir -p "$OUTDIR/manifests/apps"

# ── 辅助函数：构建 + 保存镜像 ──
# 参数: <镜像短名> <Dockerfile路径> <构建上下文> <完整镜像名(含tag)>
# 自动将 Dockerfile 中的 daocloud 镜像源替换为 Docker Hub 官方源（离线打包用）
build_and_save() {
    local short_name="$1"
    local dockerfile="$2"
    local context="$3"
    local full_image="$4"
    local outfile="${OUTDIR}/images/${short_name}.tar"

    # 创建临时 Dockerfile（去掉 daocloud 前缀，避免 TLS 超时）
    local tmp_dockerfile
    tmp_dockerfile=$(mktemp /tmp/Dockerfile-${short_name}-XXXXXX)
    sed 's|docker\.m\.daocloud\.io/library/||g; s|docker\.m\.daocloud\.io/||g' "${dockerfile}" > "${tmp_dockerfile}"

    echo "  构建 ${full_image} ..."
    DOCKER_BUILDKIT=0 docker build --pull=false -t "${full_image}" -f "${tmp_dockerfile}" "${context}" 2>&1 | tail -1
    rm -f "${tmp_dockerfile}"
    echo "  保存 ${short_name}.tar.gz ..."
    docker save "${full_image}" -o "${outfile}"
    gzip -f "${outfile}"
    echo "  ✅ ${short_name} ($(du -h "${outfile}.gz" | cut -f1))"
}

# ── 1. 构建应用 Docker 镜像 ──
echo ""
echo "[1/5] 构建 Docker 镜像 ..."

# 1a. 后端服务（构建上下文 = 项目根目录）
build_and_save "manager"         "${PROJECT_DIR}/services/manager/Dockerfile"         "${PROJECT_DIR}" "unionagents/manager:${VERSION}"
build_and_save "gateway"         "${PROJECT_DIR}/services/gateway/Dockerfile"         "${PROJECT_DIR}" "unionagents/gateway:${VERSION}"
build_and_save "engine-hermes"   "${PROJECT_DIR}/engines/hermes/Dockerfile"           "${PROJECT_DIR}" "unionagents/engine-hermes:${VERSION}"
build_and_save "hub"             "${PROJECT_DIR}/services/hub/Dockerfile"             "${PROJECT_DIR}" "unionagents/hub:${VERSION}"

# 1c. 前端镜像（宿主机预编译 dist/，Docker 仅做 Nginx 打包）
#     在原生文件系统 /tmp 上构建（WSL /mnt/d/ 上 npm/pnpm 极慢）

# 辅助函数：在 /tmp 原生 ext4 上预编译前端
# 参数: <源目录>
prebuild_frontend() {
    local src_dir="$1"
    local app_name
    app_name=$(basename "$src_dir")
    # 创建镜像仓库目录结构的临时构建目录，使 vite alias 相对路径（../../../packages/ 或
    # ../../packages/）能正确解析到 packages/ua-chat/src
    local build_root
    build_root=$(mktemp -d /tmp/ua-build-${app_name}-XXXXXX)
    local fe_tmp="${build_root}/apps/${app_name}"

    echo "  预编译 ${app_name} 前端 (/tmp 原生 ext4) ..."
    mkdir -p "${fe_tmp}"
    rsync -a --exclude=node_modules --exclude=dist "${src_dir}/" "${fe_tmp}/"
    # 同步 packages/ua-chat 共享包（vite alias @ua/chat 指向 ../../packages/ua-chat/src）
    local project_root
    project_root="$(cd "${PROJECT_DIR}" && pwd)"
    if [ -d "${project_root}/packages/ua-chat" ]; then
        mkdir -p "${build_root}/packages"
        rsync -a --exclude=node_modules --exclude=dist \
            "${project_root}/packages/ua-chat/" "${build_root}/packages/ua-chat/"
        # 安装 ua-chat 自身依赖（marked/katex/mermaid/codemirror 等，enduser vite build 需要）
        (
            cd "${build_root}/packages/ua-chat"
            pnpm install --no-frozen-lockfile \
                --registry https://registry.npmjs.org/ \
                --fetch-timeout 600000 \
                --fetch-retries 5 \
                --fetch-retry-mintimeout 20000 \
                --fetch-retry-maxtimeout 120000 \
                --config.allow-build=esbuild \
                2>&1 | tail -3 || true
        )
    fi
    # 修复 pnpm-workspace.yaml 中未设置的 allowBuilds
    if [ -f "${fe_tmp}/pnpm-workspace.yaml" ]; then
        sed -i 's/set this to true or false/true/g' "${fe_tmp}/pnpm-workspace.yaml"
    fi
    (
        cd "${fe_tmp}"
        # @iconify/json ~85MB，npmmirror 经常超时，用官方 npm registry + 长超时
        pnpm install --no-frozen-lockfile \
            --registry https://registry.npmjs.org/ \
            --fetch-timeout 600000 \
            --fetch-retries 5 \
            --fetch-retry-mintimeout 20000 \
            --fetch-retry-maxtimeout 120000 \
            --config.allow-build=esbuild --config.allow-build=vue-demi \
            2>&1 | tail -5 || true
        # 跳过 vue-tsc 类型检查（CI 单独跑 typecheck），直接 vite build
        npx vite build 2>&1 | tail -5
    )
    cp -a "${fe_tmp}/dist" "${src_dir}/dist"
    rm -rf "${build_root}"
    echo "  ✅ ${app_name} 前端编译完成"
}

# 辅助函数：预编译 VitePress 文档站
prebuild_docs() {
    local docs_dir="${PROJECT_DIR}/apps/docs"
    if [ ! -d "$docs_dir" ]; then
        echo "  ⏭️  apps/docs 不存在，跳过文档编译"
        return 0
    fi
    local docs_tmp
    docs_tmp=$(mktemp -d /tmp/fe-docs-XXXXXX)

    echo "  预编译 VitePress 文档 (/tmp 原生 ext4) ..."
    rsync -a --exclude=node_modules --exclude=dist "${docs_dir}/" "${docs_tmp}/"
    if [ -f "${docs_tmp}/pnpm-workspace.yaml" ]; then
        sed -i 's/set this to true or false/true/g' "${docs_tmp}/pnpm-workspace.yaml"
    fi
    (
        cd "${docs_tmp}"
        pnpm install --no-frozen-lockfile \
            --registry https://registry.npmjs.org/ \
            --fetch-timeout 600000 \
            --fetch-retries 5 \
            --fetch-retry-mintimeout 20000 \
            --fetch-retry-maxtimeout 120000 \
            --config.allow-build=esbuild \
            2>&1 | tail -5 || true
        pnpm build 2>&1 | tail -5 || true
    )
    mkdir -p "${docs_dir}/.vitepress"
    cp -a "${docs_tmp}/.vitepress/dist" "${docs_dir}/.vitepress/dist"
    rm -rf "${docs_tmp}"
    echo "  ✅ VitePress 文档编译完成"
}

# 预编译 admin 前端（console-admin 镜像使用）
prebuild_frontend "${PROJECT_DIR}/apps/admin"

# 预编译 VitePress 文档站（console-admin 镜像需要 /docs 路径）
prebuild_docs

# console-admin：使用预编译 dist/ 的简化 Dockerfile（避免 Docker 内 pnpm install 超时）
build_and_save "console-admin" "${PROJECT_DIR}/apps/admin/Dockerfile.prebuilt" "${PROJECT_DIR}" "unionagents/console-admin:${VERSION}"

# 预编译 enduser 前端
prebuild_frontend "${PROJECT_DIR}/apps/enduser"

# enduser-portal：使用预编译 dist/ 的简化 Dockerfile
build_and_save "enduser-portal" "${PROJECT_DIR}/apps/enduser/Dockerfile.prebuilt" "${PROJECT_DIR}" "unionagents/enduser-portal:${VERSION}"

# 清理临时构建产物（不提交到 git）
rm -rf "${PROJECT_DIR}/apps/admin/dist" "${PROJECT_DIR}/apps/enduser/dist" "${PROJECT_DIR}/apps/docs/.vitepress/dist" 2>/dev/null || true

echo ""

# ── 2. 拉取并保存基础设施镜像 ──
echo "[2/5] 拉取并保存基础设施镜像 ..."

# 辅助函数：拉取 + 重命名 + 保存
# 参数: <源镜像(含registry)> <目标镜像名(与manifest一致)>
pull_retag_save() {
    local source_image="$1"
    local target_image="$2"
    local short_name="${target_image##*/}"
    short_name="${short_name//:/-}"
    local outfile="${OUTDIR}/images/${short_name}.tar"

    # 先检查本地是否已有该镜像，有则跳过 pull
    if ! docker image inspect "${source_image}" &>/dev/null; then
        echo "  拉取 ${source_image} ..."
        docker pull "${source_image}" 2>&1 | tail -1 || {
            echo "  ⚠️ ${source_image} 拉取失败，跳过"
            return 0
        }
    else
        echo "  ${source_image} (本地已存在)"
    fi
    echo "  重命名为 ${target_image} ..."
    docker tag "${source_image}" "${target_image}"
    echo "  保存 ${short_name}.tar.gz ..."
    docker save "${target_image}" -o "${outfile}"
    gzip -f "${outfile}"
    echo "  ✅ ${short_name} ($(du -h "${outfile}.gz" | cut -f1))"
}

pull_retag_save "postgres:16-alpine" "postgres:16-alpine"
pull_retag_save "minio/minio:latest" "minio/minio:latest"
pull_retag_save "ghcr.io/berriai/litellm-database:main-stable" "unionagents/litellm:${VERSION}"

echo ""

# ── 3. 准备 k3s 二进制 ──
echo "[3/5] 准备 k3s 二进制文件 ..."
if command -v k3s &>/dev/null; then
    K3S_BIN=$(command -v k3s)
    echo "  使用本机 k3s: ${K3S_BIN}"
    cp "${K3S_BIN}" "${OUTDIR}/k3s"
elif [ -f /usr/local/bin/k3s ]; then
    cp /usr/local/bin/k3s "${OUTDIR}/k3s"
else
    echo "  ⚠️ 未找到 k3s 二进制，尝试下载 ..."
    curl -sL "https://ghproxy.net/https://github.com/k3s-io/k3s/releases/download/v1.28.8%2Bk3s1/k3s" \
        -o "${OUTDIR}/k3s" 2>/dev/null && echo "  ✅ 下载成功" || echo "  ⚠️ 下载失败，请手动放置 k3s 二进制到离线包根目录"
fi
chmod +x "${OUTDIR}/k3s" 2>/dev/null || true
echo ""

# ── 4. 复制 K8s manifests ──
echo "[4/5] 复制 K8s manifests ..."

# 主命名空间
cp "${PROJECT_DIR}/deploy/k8s/namespace.yaml" "${OUTDIR}/manifests/00-namespace.yaml"

# 基础设施
cp "${PROJECT_DIR}/deploy/k8s/infra/secret.yaml"       "${OUTDIR}/manifests/infra/10-secret.yaml"
cp "${PROJECT_DIR}/deploy/k8s/infra/postgres.yaml"     "${OUTDIR}/manifests/infra/20-postgres.yaml"
cp "${PROJECT_DIR}/deploy/k8s/infra/minio.yaml"        "${OUTDIR}/manifests/infra/30-minio.yaml"
cp "${PROJECT_DIR}/deploy/k8s/infra/manager-rbac.yaml" "${OUTDIR}/manifests/infra/40-manager-rbac.yaml"

# 后端服务
cp "${PROJECT_DIR}/deploy/k8s/services/manager.yaml"          "${OUTDIR}/manifests/services/10-manager.yaml"
cp "${PROJECT_DIR}/deploy/k8s/services/gateway.yaml"          "${OUTDIR}/manifests/services/20-gateway.yaml"
cp "${PROJECT_DIR}/deploy/k8s/services/gateway-callback.yaml" "${OUTDIR}/manifests/services/30-gateway-callback.yaml"
cp "${PROJECT_DIR}/deploy/k8s/services/litellm.yaml"          "${OUTDIR}/manifests/services/40-litellm.yaml"
cp "${PROJECT_DIR}/deploy/k8s/services/hub.yaml"              "${OUTDIR}/manifests/services/50-hub.yaml"

# 前端应用
cp "${PROJECT_DIR}/deploy/k8s/apps/admin.yaml"          "${OUTDIR}/manifests/apps/10-admin.yaml"
cp "${PROJECT_DIR}/deploy/k8s/apps/enduser-portal.yaml" "${OUTDIR}/manifests/apps/20-enduser-portal.yaml"

# Hermes 引擎模板
cp "${PROJECT_DIR}/deploy/k8s/engines/hermes-template.yaml" "${OUTDIR}/manifests/60-hermes-engine.yaml"

# ── 4b. 替换镜像标签为离线版本 ──
echo "  替换镜像标签为离线版本 (${VERSION}) ..."
# 替换所有 unionagents/ 开头的镜像标签（兼容 :latest / :1.0.0 等任意 tag）
for f in \
    "${OUTDIR}/manifests/services/"*.yaml \
    "${OUTDIR}/manifests/apps/"*.yaml \
    "${OUTDIR}/manifests/60-hermes-engine.yaml"; do
    [ -f "$f" ] || continue
    # 匹配 unionagents/<name>:<any-tag> → unionagents/<name>:<VERSION>
    sed -i "s|unionagents/\([^:]*\):[^ \"]*|unionagents/\1:${VERSION}|g" "$f"
    # litellm.yaml 引用 ghcr.io 预构建镜像，离线部署需替换为本地镜像
    sed -i "s|ghcr.io/berriai/litellm-database:main-stable|unionagents/litellm:${VERSION}|g" "$f"
    # 本地开发模式 localhost:32000/ 前缀 + imagePullPolicy: Never → 离线 IfNotPresent
    sed -i "s|localhost:32000/||g" "$f"
    sed -i "s|imagePullPolicy: Never|imagePullPolicy: IfNotPresent|g" "$f"
done

echo ""

# ── 5. 复制安装脚本 + VERSION + 文档 ──
echo "[5/5] 复制安装脚本 + 文档 ..."
cp "${SCRIPT_DIR}/install-offline.sh" "${OUTDIR}/install-offline.sh"
chmod +x "${OUTDIR}/install-offline.sh"
echo "${VERSION}" > "${OUTDIR}/VERSION"

# 复制安装指导文档
if [ -f "${PROJECT_DIR}/docs/deployment/guide.md" ]; then
    cp "${PROJECT_DIR}/docs/deployment/guide.md" "${OUTDIR}/INSTALL_GUIDE.md"
    echo "  ✅ INSTALL_GUIDE.md"
fi

# ── 打包 ──
echo ""
echo "打包离线包 ..."
PACKAGE_FILE="${PROJECT_DIR}/dist/unionagents-offline-${VERSION}.tar.gz"
mkdir -p "$(dirname "$PACKAGE_FILE")"
cd "$(dirname "$OUTDIR")"
tar -czf "${PACKAGE_FILE}" "$(basename "$OUTDIR")"
cd "${PROJECT_DIR}"

echo ""
echo "=========================================="
echo " ✅ 离线包已生成: ${PACKAGE_FILE}"
echo "    大小: $(du -h "${PACKAGE_FILE}" | cut -f1)"
echo "=========================================="
echo ""
echo "部署到离线 ECS:"
echo "  1. scp ${PACKAGE_FILE} root@ecs:/root/"
echo "  2. ssh root@ecs 'tar -xzf unionagents-offline-${VERSION}.tar.gz && cd unionagents-offline-${VERSION} && bash install-offline.sh'"
