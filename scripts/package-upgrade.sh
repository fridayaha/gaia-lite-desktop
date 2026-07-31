#!/bin/bash
# ============================================================
# UnionAgents (知行) 升级包打包脚本
# 基于 tag 基线，只打包发生变化的镜像 + 数据库迁移 + 升级脚本
# 用法: bash scripts/package-upgrade.sh <from_tag> <to_version> [输出目录]
# 示例: bash scripts/package-upgrade.sh v20260709 0.8.98 ./dist
# ============================================================
set -euo pipefail

FROM_TAG="${1:?用法: package-upgrade.sh <from_tag> <to_version> [输出目录]}"
TO_VERSION="${2:?用法: package-upgrade.sh <from_tag> <to_version> [输出目录]}"
OUTDIR="${3:-./dist/unionagents-upgrade-${FROM_TAG}-to-${TO_VERSION}}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ARCH="$(uname -m)"
case "$ARCH" in
    aarch64|arm64) ARCH_TAG="arm64" ;;
    x86_64|amd64)  ARCH_TAG="x86" ;;
    *) echo "不支持的架构: $ARCH"; exit 1 ;;
esac

echo "=========================================="
echo " UnionAgents 知行  升级包打包"
echo "  从: ${FROM_TAG}"
echo "  到: ${TO_VERSION}"
echo "  架构: ${ARCH_TAG} (${ARCH})"
echo "=========================================="

# ── 清理输出目录 ──
rm -rf "$OUTDIR"
mkdir -p "$OUTDIR/images"
mkdir -p "$OUTDIR/migrations"
mkdir -p "$OUTDIR/manifests"

# ── 辅助函数：构建 + 保存镜像 ──
build_and_save() {
    local short_name="$1"
    local dockerfile="$2"
    local context="$3"
    local full_image="$4"
    local outfile="${OUTDIR}/images/${short_name}.tar"

    local tmp_dockerfile
    tmp_dockerfile=$(mktemp /tmp/Dockerfile-${short_name}-XXXXXX)
    sed 's|docker\.m\.daocloud\.io/library/||g; s|docker\.m\.daocloud\.io/||g' "${dockerfile}" > "${tmp_dockerfile}"

    echo "  构建 ${full_image} ..."
    DOCKER_BUILDKIT=0 docker build --pull=false -t "${full_image}" -f "${tmp_dockerfile}" "${context}" 2>&1 | tail -3
    rm -f "${tmp_dockerfile}"
    echo "  保存 ${short_name}.tar.gz ..."
    docker save "${full_image}" -o "${outfile}"
    gzip -f "${outfile}"
    echo "  ✅ ${short_name} ($(du -h "${outfile}.gz" | cut -f1))"
}

# 辅助函数：在 /tmp 原生 ext4 上预编译前端
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
    if [ -f "${fe_tmp}/pnpm-workspace.yaml" ]; then
        sed -i 's/set this to true or false/true/g' "${fe_tmp}/pnpm-workspace.yaml"
    fi
    (
        cd "${fe_tmp}"
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

# ── 1. 确定哪些镜像发生了变化 ──
echo ""
echo "[1/4] 分析 ${FROM_TAG} → ${TO_VERSION} 之间的变更 ..."

# 列出所有应用镜像，逐个检查 Dockerfile 相关文件是否有变化
CHANGED_IMAGES=()

# manager
if git -C "$PROJECT_DIR" diff --quiet "${FROM_TAG}..HEAD" -- services/manager/ 2>/dev/null; then
    echo "  manager: 无变化，跳过"
else
    CHANGED_IMAGES+=("manager")
    echo "  manager: 有变化 ✏️"
fi

# controller
if git -C "$PROJECT_DIR" diff --quiet "${FROM_TAG}..HEAD" -- services/controller/ 2>/dev/null; then
    echo "  controller: 无变化，跳过"
else
    CHANGED_IMAGES+=("controller")
    echo "  controller: 有变化 ✏️"
fi

# engine-hermes
if git -C "$PROJECT_DIR" diff --quiet "${FROM_TAG}..HEAD" -- engines/hermes/ 2>/dev/null; then
    echo "  engine-hermes: 无变化，跳过"
else
    CHANGED_IMAGES+=("engine-hermes")
    echo "  engine-hermes: 有变化 ✏️"
fi

# hub
if git -C "$PROJECT_DIR" diff --quiet "${FROM_TAG}..HEAD" -- services/hub/ 2>/dev/null; then
    echo "  hub: 无变化，跳过"
else
    CHANGED_IMAGES+=("hub")
    echo "  hub: 有变化 ✏️"
fi

# gateway (旧名 channel-gateway，已重命名为 services/gateway/)
if git -C "$PROJECT_DIR" diff --quiet "${FROM_TAG}..HEAD" -- services/gateway/ 2>/dev/null; then
    echo "  gateway: 无变化，跳过"
else
    CHANGED_IMAGES+=("gateway")
    echo "  gateway: 有变化 ✏️"
fi

# litellm (镜像名 litellm-custom，源码不在此仓库，检查 deploy/k8s/services/40-litellm.yaml)
if git -C "$PROJECT_DIR" diff --quiet "${FROM_TAG}..HEAD" -- deploy/k8s/services/40-litellm.yaml 2>/dev/null; then
    echo "  litellm: 无变化，跳过"
else
    CHANGED_IMAGES+=("llm-gateway")
    echo "  litellm: 有变化 ✏️"
fi

# admin 前端（Dockerfile + 源码 + docs）
if git -C "$PROJECT_DIR" diff --quiet "${FROM_TAG}..HEAD" -- apps/admin/ apps/docs/ 2>/dev/null; then
    echo "  console-admin: 无变化，跳过"
else
    CHANGED_IMAGES+=("console-admin")
    echo "  console-admin: 有变化 ✏️"
fi

# enduser 前端
if git -C "$PROJECT_DIR" diff --quiet "${FROM_TAG}..HEAD" -- apps/enduser/ 2>/dev/null; then
    echo "  console-enduser: 无变化，跳过"
else
    CHANGED_IMAGES+=("console-enduser")
    echo "  console-enduser: 有变化 ✏️"
fi

# enduser-portal (nginx 反代，通常随 enduser 变)
if [[ " ${CHANGED_IMAGES[@]} " =~ " console-enduser " ]]; then
    CHANGED_IMAGES+=("enduser-portal")
    echo "  enduser-portal: 随 enduser 变更 ✏️"
fi

if [ ${#CHANGED_IMAGES[@]} -eq 0 ]; then
    echo ""
    echo "⚠️  没有镜像发生变化，无需升级包"
    exit 0
fi

echo ""
echo "  需要构建的镜像: ${CHANGED_IMAGES[*]}"

# ── 2. 构建变化的镜像 ──
echo ""
echo "[2/4] 构建变化的 Docker 镜像 ..."

# 前端预编译标志
ADMIN_PREBUILT=0
DOCS_PREBUILT=0
ENDUSER_PREBUILT=0

for img in "${CHANGED_IMAGES[@]}"; do
    case "$img" in
        manager)
            build_and_save "manager" \
                "${PROJECT_DIR}/services/manager/Dockerfile" \
                "${PROJECT_DIR}" \
                "unionagents/manager:${TO_VERSION}"
            ;;
        controller)
            build_and_save "controller" \
                "${PROJECT_DIR}/services/controller/Dockerfile" \
                "${PROJECT_DIR}" \
                "unionagents/controller:${TO_VERSION}"
            ;;
        engine-hermes)
            build_and_save "engine-hermes" \
                "${PROJECT_DIR}/engines/hermes/Dockerfile" \
                "${PROJECT_DIR}" \
                "unionagents/engine-hermes:${TO_VERSION}"
            ;;
        hub)
            build_and_save "hub" \
                "${PROJECT_DIR}/services/hub/Dockerfile" \
                "${PROJECT_DIR}" \
                "unionagents/hub:${TO_VERSION}"
            ;;
        gateway)
            build_and_save "gateway" \
                "${PROJECT_DIR}/services/gateway/Dockerfile" \
                "${PROJECT_DIR}" \
                "unionagents/gateway:${TO_VERSION}"
            ;;
        llm-gateway)
            # litellm-custom 镜像源码不在此仓库，跳过构建
            # 如果需要更新 litellm，请通过 package-offline.sh 全量打包
            echo "  ⏭️ litellm-custom 源码不在本仓库，跳过构建"
            continue
            ;;
        console-admin)
            if [ "$ADMIN_PREBUILT" -eq 0 ]; then
                prebuild_frontend "${PROJECT_DIR}/apps/admin"
                ADMIN_PREBUILT=1
            fi
            if [ "$DOCS_PREBUILT" -eq 0 ]; then
                prebuild_docs
                DOCS_PREBUILT=1
            fi
            build_and_save "console-admin" \
                "${PROJECT_DIR}/apps/admin/Dockerfile.prebuilt" \
                "${PROJECT_DIR}" \
                "unionagents/console-admin:${TO_VERSION}"
            ;;
        console-enduser)
            if [ "$ENDUSER_PREBUILT" -eq 0 ]; then
                prebuild_frontend "${PROJECT_DIR}/apps/enduser"
                ENDUSER_PREBUILT=1
            fi
            build_and_save "console-enduser" \
                "${PROJECT_DIR}/apps/enduser/Dockerfile.prebuilt" \
                "${PROJECT_DIR}" \
                "unionagents/console-enduser:${TO_VERSION}"
            ;;
        enduser-portal)
            if [ "$ENDUSER_PREBUILT" -eq 0 ]; then
                prebuild_frontend "${PROJECT_DIR}/apps/enduser"
                ENDUSER_PREBUILT=1
            fi
            build_and_save "enduser-portal" \
                "${PROJECT_DIR}/apps/enduser/Dockerfile.prebuilt" \
                "${PROJECT_DIR}" \
                "unionagents/enduser-portal:${TO_VERSION}"
            ;;
    esac
done

# 清理临时构建产物
rm -rf "${PROJECT_DIR}/apps/admin/dist" "${PROJECT_DIR}/apps/enduser/dist" "${PROJECT_DIR}/apps/docs/.vitepress/dist" 2>/dev/null || true

echo ""

# ── 3. 收集数据库迁移脚本 ──
echo "[3/4] 收集数据库迁移脚本 ..."

MIGRATIONS_DIR="${PROJECT_DIR}/services/manager/migrations"
if [ -d "$MIGRATIONS_DIR" ]; then
    # 从 from_tag 到当前，找出新增/修改的迁移文件
    CHANGED_MIGRATIONS=$(git -C "$PROJECT_DIR" diff --name-only --diff-filter=A "$FROM_TAG"..HEAD -- services/manager/migrations/ 2>/dev/null | sort)
    if [ -n "$CHANGED_MIGRATIONS" ]; then
        for f in $CHANGED_MIGRATIONS; do
            src="${PROJECT_DIR}/${f}"
            if [ -f "$src" ]; then
                cp "$src" "${OUTDIR}/migrations/"
                echo "  ✅ $(basename "$src")"
            fi
        done
    else
        echo "  无新增迁移文件"
    fi
fi

echo ""

# ── 4. 生成升级脚本 + 元信息 ──
echo "[4/4] 生成升级脚本 ..."

# 生成 CHANGELOG.md（git log 摘要）
git -C "$PROJECT_DIR" log --oneline "$FROM_TAG"..HEAD --format="- %s (%h)" > "${OUTDIR}/CHANGELOG.txt"
echo "  ✅ CHANGELOG.txt ($(wc -l < "${OUTDIR}/CHANGELOG.txt") 条记录)"

# 生成升级镜像列表
echo "# 升级包镜像列表 (${ARCH_TAG})" > "${OUTDIR}/IMAGES.txt"
echo "# 格式: <镜像名> <tag>" >> "${OUTDIR}/IMAGES.txt"
for img in "${CHANGED_IMAGES[@]}"; do
    case "$img" in
        llm-gateway) ;; # litellm-custom 源码不在本仓库，不写入镜像列表
        *) echo "${img} ${TO_VERSION}" >> "${OUTDIR}/IMAGES.txt" ;;
    esac
done
echo "  ✅ IMAGES.txt"

# 写入版本信息
cat > "${OUTDIR}/VERSION" << EOF
FROM_TAG=${FROM_TAG}
TO_VERSION=${TO_VERSION}
ARCH=${ARCH_TAG}
BUILD_DATE=$(date '+%Y-%m-%d %H:%M:%S')
GIT_COMMIT=$(git -C "$PROJECT_DIR" rev-parse --short HEAD)
EOF
echo "  ✅ VERSION"

# 生成升级包内的 Release Notes
bash "${SCRIPT_DIR}/gen_release_notes.sh" "$FROM_TAG" HEAD "${OUTDIR}/RELEASE_NOTES.md" 2>/dev/null || true
echo "  ✅ RELEASE_NOTES.md"

# 复制升级指导文档
if [ -f "${PROJECT_DIR}/docs/UPGRADE_GUIDE.md" ]; then
    cp "${PROJECT_DIR}/docs/UPGRADE_GUIDE.md" "${OUTDIR}/UPGRADE_GUIDE.md"
    echo "  ✅ UPGRADE_GUIDE.md"
fi

# 复制升级执行脚本
cp "${SCRIPT_DIR}/upgrade-offline.sh" "${OUTDIR}/upgrade.sh"
chmod +x "${OUTDIR}/upgrade.sh"
echo "  ✅ upgrade.sh"

# ── 打包 ──
echo ""
echo "打包升级包 ..."
PACKAGE_FILE="${PROJECT_DIR}/dist/unionagents-upgrade-${FROM_TAG}-to-${TO_VERSION}-${ARCH_TAG}.tar.gz"
mkdir -p "$(dirname "$PACKAGE_FILE")"
cd "$(dirname "$OUTDIR")"
tar -czf "$PACKAGE_FILE" "$(basename "$OUTDIR")"
cd "$PROJECT_DIR"

echo ""
echo "=========================================="
echo " ✅ 升级包已生成: ${PACKAGE_FILE}"
echo "    架构: ${ARCH_TAG}"
echo "    大小: $(du -h "$PACKAGE_FILE" | cut -f1)"
echo "    包含镜像: ${CHANGED_IMAGES[*]}"
echo "=========================================="
echo ""
echo "升级部署:"
echo "  1. scp ${PACKAGE_FILE} root@ecs:/root/"
echo "  2. ssh root@ecs 'tar -xzf $(basename $PACKAGE_FILE) && cd $(basename $OUTDIR) && bash upgrade.sh'"
echo ""
