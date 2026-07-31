#!/bin/bash
# ============================================================
# UnionAgents (知行) 每日构建归档脚本
#
# ARM 本机构建+归档，X86 远程构建+归档，互不拉取。
# ARM 归档: ARCHIVE_BASE/YYYYMMDD/
# X86 归档: X86 上 ARCHIVE_BASE/YYYYMMDD/
#
# 用法:
#   bash scripts/daily_build_archive.sh              # 自动检测最新版本
#   bash scripts/daily_build_archive.sh 0.8.98       # 指定版本号
#
# 部署位置: 本脚本运行在 ARM 机器上
# Crontab:  0 2 * * * /root/union_agent/scripts/daily_build_archive.sh
#
# 环境变量 (通过 export 或 .env.local 设置，无默认值):
#   X86_HOST      — X86 构建机器 SSH 地址 (必填)
#   X86_REPO      — X86 机器上的仓库路径 (必填)
#   ARCHIVE_BASE  — 本机归档根目录 (必填)
#   ARCHIVE_HOST  — 归档机器的大网地址，用于 BUILD_INFO 展示 (必填)
#   BASELINE_TAG  — 升级包基线 tag (必填)
#   REGISTRY_HOST — 镜像仓库主机地址 (可选，不设则跳过镜像推送)
# ============================================================
set -eu
# 注意: 不用 pipefail，tee 管道在子进程退出时会触发 SIGPIPE 导致误失败

# ── 配置 ──
REPO_DIR="${REPO_DIR:-/root/union_agent}"
: "${X86_HOST:?X86_HOST 未设置，请 export X86_HOST=root@<x86-ip>}"
: "${X86_REPO:?X86_REPO 未设置，请 export X86_REPO=<x86 仓库路径>}"
: "${ARCHIVE_BASE:?ARCHIVE_BASE 未设置，请 export ARCHIVE_BASE=<归档根目录>}"
: "${BASELINE_TAG:?BASELINE_TAG 未设置，请 export BASELINE_TAG=<基线tag>}"
: "${ARCHIVE_HOST:?ARCHIVE_HOST 未设置，请 export ARCHIVE_HOST=<归档机器大网IP>}"
LOG_DIR="${REPO_DIR}/logs"
DATE=$(date +%Y%m%d)
DATETIME=$(date '+%Y-%m-%d %H:%M:%S')

mkdir -p "$LOG_DIR" "$ARCHIVE_BASE"
LOG_FILE="${LOG_DIR}/daily-archive-${DATE}.log"
ARCHIVE_DIR="${ARCHIVE_BASE}/${DATE}"
mkdir -p "$ARCHIVE_DIR"

log() { echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

echo "=========================================="
echo " UnionAgents 每日构建归档"
echo " 日期: ${DATE}"
echo " 归档位置: ${ARCHIVE_DIR}"
echo "=========================================="
echo "" | tee -a "$LOG_FILE"

# ── 0. 拉取最新代码 ──
log "[0/7] 拉取最新代码 ..."
cd "$REPO_DIR"
git fetch origin develop 2>&1 | tee -a "$LOG_FILE"
git checkout develop 2>&1 | tee -a "$LOG_FILE"
git reset --hard origin/develop 2>&1 | tee -a "$LOG_FILE"
COMMIT=$(git rev-parse --short HEAD)
log "  当前提交: ${COMMIT}"

# 版本号格式: 1.1.0-YYYYMMDD（固定前缀 + 日期，不从 VERSION 文件读取）
VERSION="1.1.0-${DATE}"
log "  版本号: ${VERSION}"

# 同步代码到 X86 机器
log "  同步代码到 X86 机器 ..."
ssh "$X86_HOST" "cd ${X86_REPO} && git fetch upstream develop && git checkout develop && git reset --hard upstream/develop" 2>&1 | tee -a "$LOG_FILE"
log "  ✅ 代码同步完成"

# ── 1-2. ARM 和 X86 并行构建 ──
# ARM 在本机构建，X86 通过 SSH 远程构建，两者无依赖关系，同时启动
ARM_LOG="${LOG_DIR}/arm-build-${DATE}.log"
X86_LOG="${LOG_DIR}/x86-build-${DATE}.log"

log "[1/7] ARM + X86 并行构建 ..."

# --- ARM 后台构建 (安装包 + 升级包) ---
# 子shell退出码: 0=全部成功, 1=安装包失败, 2=升级包失败, 3=全部失败
(
    ARM_RC=0
    echo "[ARM] 构建安装包 ..."
    if bash "${REPO_DIR}/scripts/package-offline.sh" "${VERSION}" "${REPO_DIR}/dist/arm-install" 2>&1; then
        ARM_INSTALL="${REPO_DIR}/dist/unionagents-offline-${VERSION}.tar.gz"
        cp "$ARM_INSTALL" "${ARCHIVE_DIR}/unionagents-install-${VERSION}-arm64.tar.gz"
        echo "[ARM] ✅ 安装包: $(du -h "${ARCHIVE_DIR}/unionagents-install-${VERSION}-arm64.tar.gz" | cut -f1)"
    else
        echo "[ARM] ❌ 安装包构建失败"
        ARM_RC=$((ARM_RC | 1))
    fi

    echo "[ARM] 构建升级包 ..."
    if bash "${REPO_DIR}/scripts/package-upgrade.sh" "$BASELINE_TAG" "$VERSION" "${REPO_DIR}/dist/arm-upgrade" 2>&1; then
        ARM_UPGRADE="${REPO_DIR}/dist/unionagents-upgrade-${BASELINE_TAG}-to-${VERSION}-arm64.tar.gz"
        cp "$ARM_UPGRADE" "${ARCHIVE_DIR}/"
        echo "[ARM] ✅ 升级包: $(du -h "${ARCHIVE_DIR}/unionagents-upgrade-${BASELINE_TAG}-to-${VERSION}-arm64.tar.gz" | cut -f1)"
    else
        echo "[ARM] ⚠️ 升级包构建失败（可能无变更）"
        ARM_RC=$((ARM_RC | 2))
    fi

    # 给当天构建的镜像打 :latest tag，供系统测试导入 k3s 使用
    echo "[ARM] 打 :latest tag ..."
    for svc in manager gateway engine-hermes hub console-admin enduser-portal; do
        if docker image inspect "unionagents/${svc}:${VERSION}" &>/dev/null; then
            docker tag "unionagents/${svc}:${VERSION}" "unionagents/${svc}:latest"
            echo "  ✅ ${svc}:latest"
        fi
    done

    exit $ARM_RC
) > "$ARM_LOG" 2>&1 &
ARM_PID=$!

# --- X86 后台构建 (安装包 + 升级包) ---
# 远程退出码: 0=全部成功, 1=安装包失败, 2=升级包失败, 3=全部失败
ssh "$X86_HOST" bash -s <<REMOTE > "$X86_LOG" 2>&1 &
set -eu
cd "${X86_REPO}"
VERSION="${VERSION}"
BASELINE_TAG="${BASELINE_TAG}"
ARCHIVE_DIR="${ARCHIVE_DIR}"
mkdir -p "\$ARCHIVE_DIR"
X86_RC=0

echo "[X86] 构建安装包 ..."
if bash scripts/package-offline.sh "\${VERSION}" ./dist/x86-install; then
    cp ./dist/unionagents-offline-\${VERSION}.tar.gz \
       "\$ARCHIVE_DIR/unionagents-install-\${VERSION}-x86.tar.gz"
    echo "[X86] ✅ 安装包: \$(du -h "\$ARCHIVE_DIR/unionagents-install-\${VERSION}-x86.tar.gz" | cut -f1)"
else
    echo "[X86] ❌ 安装包构建失败"
    X86_RC=\$((X86_RC | 1))
fi

echo "[X86] 构建升级包 ..."
if bash scripts/package-upgrade.sh \${BASELINE_TAG} \${VERSION} ./dist/x86-upgrade; then
    cp ./dist/unionagents-upgrade-\${BASELINE_TAG}-to-\${VERSION}-x86.tar.gz \
       "\$ARCHIVE_DIR/" 2>/dev/null || true
    echo "[X86] ✅ 升级包: \$(du -h "\$ARCHIVE_DIR/unionagents-upgrade-\${BASELINE_TAG}-to-\${VERSION}-x86.tar.gz" 2>/dev/null | cut -f1)"
else
    echo "[X86] ⚠️ 升级包构建失败（可能无变更）"
    X86_RC=\$((X86_RC | 2))
fi

# 给当天构建的镜像打 :latest tag
echo "[X86] 打 :latest tag ..."
for svc in manager gateway engine-hermes hub console-admin enduser-portal; do
    if docker image inspect "unionagents/\${svc}:\${VERSION}" &>/dev/null; then
        docker tag "unionagents/\${svc}:\${VERSION}" "unionagents/\${svc}:latest"
        echo "  ✅ \${svc}:latest"
    fi
done

rm -rf ./dist/x86-install ./dist/x86-upgrade ./dist/unionagents-*.tar.gz 2>/dev/null || true

echo "[X86] 归档内容:"
ls -lh "\$ARCHIVE_DIR" 2>&1

DISK_AVAIL=\$(df -h /root | awk 'NR==2{print \$4}')
DISK_USE=\$(df -h /root | awk 'NR==2{print \$5}')
echo "[X86] 磁盘可用: \${DISK_AVAIL} (已用 \${DISK_USE})"

exit \$X86_RC
REMOTE
X86_PID=$!

# --- 等待两边都完成 (用 set +e 包裹, wait 非0不触发 set -e 退出) ---
log "  ARM 构建 PID=$ARM_PID, X86 构建 PID=$X86_PID"
log "  等待 ARM 和 X86 并行构建完成 ..."

set +e
wait $ARM_PID
ARM_EXIT=$?
wait $X86_PID
X86_EXIT=$?
set -e

# 将子进程日志追加到主日志
cat "$ARM_LOG" >> "$LOG_FILE"
cat "$X86_LOG" >> "$LOG_FILE"

log "  ARM 构建退出码: $ARM_EXIT"
log "  X86 构建退出码: $X86_EXIT"

# 检查实际产物（tar.gz 文件是否存在）
ARM_HAS_TARGZ=false
if ls "${ARCHIVE_DIR}"/unionagents-*-arm64.tar.gz >/dev/null 2>&1; then
    ARM_HAS_TARGZ=true
fi
X86_HAS_TARGZ=false
if ssh "$X86_HOST" "ls ${ARCHIVE_DIR}/unionagents-*-x86.tar.gz" >/dev/null 2>&1; then
    X86_HAS_TARGZ=true
fi

if [ "$ARM_EXIT" -eq 0 ] && [ "$ARM_HAS_TARGZ" = true ]; then
    log "  ✅ ARM 构建归档完成"
else
    log "  ❌ ARM 构建失败 (exit=$ARM_EXIT, 产物存在=$ARM_HAS_TARGZ)"
fi
if [ "$X86_EXIT" -eq 0 ] && [ "$X86_HAS_TARGZ" = true ]; then
    log "  ✅ X86 构建归档完成"
else
    log "  ❌ X86 构建失败 (exit=$X86_EXIT, 产物存在=$X86_HAS_TARGZ)"
fi

# ── 3. 推送镜像到 Registry ──
log "[3/7] 推送镜像到 Registry ..."

REGISTRY_HOST="${REGISTRY_HOST:-}"
REGISTRY_PORT="${REGISTRY_PORT:-5000}"

# ARM 本机推送
if [ -n "$REGISTRY_HOST" ]; then
    log "  [ARM] 推送应用镜像到 ${REGISTRY_HOST}:${REGISTRY_PORT} ..."
    if APP_VERSION="${VERSION}" REGISTRY_HOST="${REGISTRY_HOST}" REGISTRY_PORT="${REGISTRY_PORT}" \
        bash "${REPO_DIR}/scripts/sync_to_registry.sh" --app-only >> "$LOG_FILE" 2>&1; then
        log "  ✅ ARM 镜像推送完成"
    else
        log "  ⚠️ ARM 镜像推送失败（不影响构建归档）"
    fi
else
    log "  ⚠️ REGISTRY_HOST 未设置，跳过 ARM 镜像推送"
fi

# X86 远程推送
if [ -n "$REGISTRY_HOST" ]; then
    log "  [X86] 推送应用镜像到 ${REGISTRY_HOST}:${REGISTRY_PORT} ..."
    if ssh "$X86_HOST" "cd ${X86_REPO} && APP_VERSION='${VERSION}' REGISTRY_HOST='${REGISTRY_HOST}' REGISTRY_PORT='${REGISTRY_PORT}' \
        bash scripts/sync_to_registry.sh --app-only" >> "$LOG_FILE" 2>&1; then
        log "  ✅ X86 镜像推送完成"
    else
        log "  ⚠️ X86 镜像推送失败（不影响构建归档）"
    fi
else
    log "  ⚠️ REGISTRY_HOST 未设置，跳过 X86 镜像推送"
fi

# ── 4. 生成文档 ──
log "[4/7] 生成文档 ..."

# Release Notes
RELEASE_NOTES_LOCAL="${ARCHIVE_DIR}/RELEASE_NOTES.md"
bash "${REPO_DIR}/scripts/gen_release_notes.sh" "$BASELINE_TAG" "origin/develop" "$RELEASE_NOTES_LOCAL" 2>&1 | tee -a "$LOG_FILE" || true
log "  ✅ RELEASE_NOTES.md"

# 安装指导
cp "${REPO_DIR}/docs/deployment/guide.md" "${ARCHIVE_DIR}/INSTALL_GUIDE.md" 2>/dev/null || true
log "  ✅ INSTALL_GUIDE.md"

# 升级指导
cp "${REPO_DIR}/docs/UPGRADE_GUIDE.md" "${ARCHIVE_DIR}/UPGRADE_GUIDE.md" 2>/dev/null || true
log "  ✅ UPGRADE_GUIDE.md"

# 升级设计文档
cp "${REPO_DIR}/docs/UPGRADE_DESIGN.md" "${ARCHIVE_DIR}/UPGRADE_DESIGN.md" 2>/dev/null || true
log "  ✅ UPGRADE_DESIGN.md"

# ── 5. 清理 + 汇总 ──
log "[5/7] 清理 + 汇总 ..."

# 清理本地 dist 临时目录
rm -rf "${REPO_DIR}/dist/arm-install" "${REPO_DIR}/dist/arm-upgrade" 2>/dev/null || true

# 保留最近 3 天的归档
log "  清理 3 天前的归档 ..."
find "$ARCHIVE_BASE" -maxdepth 1 -type d -name '20*' -mtime +3 -exec rm -rf {} \; 2>/dev/null || true
ssh "$X86_HOST" "find '${ARCHIVE_BASE}' -maxdepth 1 -type d -name '20*' -mtime +3 -exec rm -rf {} \\; 2>/dev/null" || true

# 写入构建信息
cat > "${ARCHIVE_DIR}/BUILD_INFO.txt" << EOF
日期: ${DATE}
版本: v${VERSION}
提交: ${COMMIT}
基线: ${BASELINE_TAG}
ARM 归档: ${ARCHIVE_HOST}:${ARCHIVE_DIR}/
X86 归档: ${X86_HOST}:${ARCHIVE_DIR}/
构建时间: ${DATETIME}

产物清单:
$(ls -lh "$ARCHIVE_DIR" | grep -v '^total\|^d\|BUILD_INFO' | awk '{printf "  %s %s\n", $5, $9}')
EOF

# 列出归档内容
log "  归档内容:"
ls -lh "$ARCHIVE_DIR" 2>&1 | tee -a "$LOG_FILE"

# 磁盘使用
ARCHIVE_SIZE=$(du -sh "$ARCHIVE_DIR" | cut -f1)
TOTAL_ARCHIVE_SIZE=$(du -sh "$ARCHIVE_BASE" | cut -f1)
DISK_AVAIL=$(df -h /root | awk 'NR==2{print $4}')
DISK_USE=$(df -h /root | awk 'NR==2{print $5}')
log "  本次归档大小: ${ARCHIVE_SIZE}"
log "  归档总占用: ${TOTAL_ARCHIVE_SIZE}"
log "  磁盘可用: ${DISK_AVAIL} (已用 ${DISK_USE})"

echo "" | tee -a "$LOG_FILE"

# 最终状态: 检查是否有实际产物
OVERALL_OK=true
if [ "$ARM_HAS_TARGZ" != true ]; then OVERALL_OK=false; fi
if [ "$X86_HAS_TARGZ" != true ]; then OVERALL_OK=false; fi

if [ "$OVERALL_OK" = true ]; then
    log "==========================================="
    log " ✅ 每日构建归档完成"
    log "    ARM 归档: ${ARCHIVE_HOST}:${ARCHIVE_DIR}/"
    log "    X86 归档: ${X86_HOST}:${ARCHIVE_DIR}/"
    log "    版本: v${VERSION} (${COMMIT})"
    log "    磁盘可用: ${DISK_AVAIL} (已用 ${DISK_USE})"
    log "==========================================="
    exit 0
else
    log "==========================================="
    log " ❌ 每日构建归档失败（部分产物缺失）"
    log "    ARM 归档: ${ARCHIVE_HOST}:${ARCHIVE_DIR}/ (产物存在: ${ARM_HAS_TARGZ})"
    log "    X86 归档: ${X86_HOST}:${ARCHIVE_DIR}/ (产物存在: ${X86_HAS_TARGZ})"
    log "    版本: v${VERSION} (${COMMIT})"
    log "    磁盘可用: ${DISK_AVAIL} (已用 ${DISK_USE})"
    log "==========================================="
    exit 1
fi
