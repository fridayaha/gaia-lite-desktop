#!/bin/bash
# ============================================================
# 导入自有镜像 OCI tar 到 containerd
#
# 特性：
#   1. 可重入：已存在的镜像自动跳过（按镜像名+digest 判断）
#   2. 短名称兼容：import 后自动补短名称 tag（与 manifest 引用一致）
#   3. 多节点支持：通过 SSH 把镜像流式分发到集群其他节点（可选）
#   4. 进度反馈：每个镜像/每个节点都有明确状态输出
#   5. 节点标签：导入成功的节点自动打 k8s 标签 gaia-images=loaded
#
# 用法: bash scripts/load-images.sh <images_dir> [k8s_runtime]
#   images_dir:   含 gaia-<svc>-<VERSION>.tar 的目录
#   k8s_runtime:  k3s（默认，用 k3s ctr）| k8s（标准 k8s，用 ctr -n k8s.io）
#
# 环境变量（可选，由 .env.local 注入）：
#   LOAD_IMAGES_NODES  逗号分隔的 SSH 可达节点列表（user@host:port 或 host）
#                      留空=只导入本机（单节点集群）
#                      示例: "root@192.168.0.125,root@192.168.0.172"
#   SSH_PRIVATE_KEY    SSH 私钥路径（默认 ~/.ssh/id_rsa）
#   SSH_PORT           默认 SSH 端口（默认 22，被 LOAD_IMAGES_NODES 的 :port 覆盖）
#   NAMESPACE          k8s 命名空间（默认 gaia，用于打节点标签）
#   VERSION            镜像版本 tag（默认从 tar 文件名推导）
#
# 退出码：0=成功；1=本机导入失败；2=部分节点失败（本机已成功）
# ============================================================
set -uo pipefail

IMAGES_DIR="${1:?用法: load-images.sh <images_dir> [k3s|k8s]}"
RUNTIME="${2:-k3s}"

# 检测 root（ctr 操作 containerd 需要）
if [ "$(id -u)" -ne 0 ]; then
  echo "❌ 需 root 权限导入镜像（ctr 操作 containerd）"
  echo "   请用: sudo -E bash scripts/load-images.sh \"${IMAGES_DIR}\" ${RUNTIME}"
  exit 1
fi

# 选择 ctr 命令
case "${RUNTIME}" in
  k3s) CTR="k3s ctr" ;;
  k8s) CTR="ctr -n k8s.io" ;;
  *)   echo "❌ 未知 runtime: ${RUNTIME}（支持: k3s, k8s）"; exit 1 ;;
esac

NAMESPACE="${NAMESPACE:-gaia}"
SSH_PRIVATE_KEY="${SSH_PRIVATE_KEY:-$HOME/.ssh/id_rsa}"
SSH_PORT_DEFAULT="${SSH_PORT:-22}"

# 从 tar 文件名推导 VERSION（gaia-api-0.1.0-arm64.tar → 0.1.0）
VERSION="${VERSION:-}"
if [ -z "${VERSION}" ] && ls "${IMAGES_DIR}"/gaia-*.tar >/dev/null 2>&1; then
  VERSION=$(ls "${IMAGES_DIR}"/gaia-*.tar 2>/dev/null | head -1 | sed -E 's/.*gaia-[a-z-]+-([0-9]+\.[0-9]+\.[0-9]+).*/\1/')
fi

echo "=========================================="
echo "  导入自有镜像到 containerd"
echo "  Runtime: ${RUNTIME}"
echo "  Version: ${VERSION:-未知}"
echo "  本机:    $(hostname)"
echo "  节点:    ${LOAD_IMAGES_NODES:-仅本机}"
echo "=========================================="
echo ""

# 自有镜像清单（imagePullPolicy=Never，必须 import）
# 格式：tar 文件名前缀 : 完整镜像名（不含 tag）
declare -a IMAGE_LIST=(
  "gaia-api:unionagents/gaia-api"
  "gaia-better-auth:unionagents/gaia-better-auth"
  "gaia-trino-plugins:unionagents/gaia-trino-plugins"
  "gaia-gravitino-libs:unionagents/gaia-gravitino-libs"
  "gaia-web-ui:unionagents/gaia-web-ui"
)

# ── 工具函数 ──

# 判断镜像是否已在 containerd（按完整引用名）
image_exists() {
  local ref="$1"
  ${CTR} images ls "ref:${ref}" 2>/dev/null | grep -q "${ref}"
  return $?
}

# 导入单个 tar 到本机 containerd（可重入：已存在则跳过）
import_local() {
  local tar="$1" full_ref="$2" short_ref="$3"
  local name; name=$(basename "$tar" .tar)
  local size; size=$(du -h "$tar" | cut -f1)

  if image_exists "${full_ref}"; then
    echo "    ⏭️  ${name} 已存在，跳过（${size}）"
    return 0
  fi

  echo "    ⬇️  ${name} (${size}) 导入中 ..."
  if ${CTR} images import "$tar" >/dev/null 2>&1; then
    echo "    ✅ ${name} 导入成功"
  else
    echo "    ❌ ${name} 导入失败"
    return 1
  fi

  # 补短名称 tag（manifest 引用 unionagents/gaia-xxx:VERSION，无 docker.io/ 前缀）
  # containerd import 后镜像 ref 是 docker.io/unionagents/gaia-xxx:VERSION
  # Never 策略下 kubelet 用 manifest 里的 unionagents/gaia-xxx:VERSION 精确匹配，需补短名
  if [ -n "${short_ref}" ] && [ "${short_ref}" != "${full_ref}" ]; then
    if ! image_exists "${short_ref}"; then
      ${CTR} images tag "${full_ref}" "${short_ref}" >/dev/null 2>&1 && \
        echo "    🏷️  补短名称 tag: ${short_ref}"
    fi
  fi
  return 0
}

# 给本机节点打 k8s 标签（标记镜像已导入，供 nodeSelector 用）
label_local_node() {
  local node_name; node_name=$(hostname)
  # 尝试匹配 kubectl 里的节点名（hostname 可能与 k8s node 名不完全一致）
  if command -v kubectl >/dev/null 2>&1 && kubectl get node "${node_name}" >/dev/null 2>&1; then
    kubectl label node "${node_name}" gaia-images=loaded --overwrite >/dev/null 2>&1 && \
      echo "    🏷️  节点 ${node_name} 打标签 gaia-images=loaded"
  fi
}

# SSH 导入到远程节点（流式传输，不落临时文件）
import_remote() {
  local node_spec="$1"
  local ssh_host ssh_port ssh_user
  # 解析 user@host:port
  ssh_user="root"
  ssh_host="${node_spec}"
  ssh_port="${SSH_PORT_DEFAULT}"
  if echo "${node_spec}" | grep -q "@"; then
    ssh_user="${node_spec%%@*}"
    ssh_host="${node_spec#*@}"
  fi
  if echo "${ssh_host}" | grep -q ":"; then
    ssh_port="${ssh_host##*:}"
    ssh_host="${ssh_host%%:*}"
  fi

  echo ""
  echo "  ── 分发到节点 ${ssh_user}@${ssh_host}:${ssh_port} ──"

  # SSH 公共参数
  local ssh_opts="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes -p ${ssh_port}"
  [ -f "${SSH_PRIVATE_KEY}" ] && ssh_opts="${ssh_opts} -i ${SSH_PRIVATE_KEY}"

  # 测试 SSH 连通性
  if ! ssh ${ssh_opts} "${ssh_user}@${ssh_host}" "echo ok" >/dev/null 2>&1; then
    echo "    ⚠️  SSH 不可达 ${ssh_user}@${ssh_host}:${ssh_port}，跳过该节点"
    echo "    （该节点需手动导入镜像，或 Pod 将调度到其他有镜像的节点）"
    return 1
  fi
  echo "    ✅ SSH 连通"

  # 探测远程 runtime 的 ctr 命令
  local remote_ctr
  remote_ctr=$(ssh ${ssh_opts} "${ssh_user}@${ssh_host}" \
    "command -v k3s >/dev/null 2>&1 && echo 'k3s ctr' || (command -v ctr >/dev/null 2>&1 && echo 'ctr -n k8s.io')" 2>/dev/null)
  if [ -z "${remote_ctr}" ]; then
    echo "    ⚠️  远程节点未找到 ctr/k3s，跳过"
    return 1
  fi

  # 远程节点名（用于打标签）
  local remote_node; remote_node=$(ssh ${ssh_opts} "${ssh_user}@${ssh_host}" "hostname" 2>/dev/null)

  local failed=0
  for entry in "${IMAGE_LIST[@]}"; do
    local prefix="${entry%%:*}"
    local img_name="${entry#*:}"
    # 找匹配的 tar（支持 arch 后缀）
    local tar; tar=$(ls "${IMAGES_DIR}"/${prefix}-*.tar 2>/dev/null | head -1)
    [ -f "${tar}" ] || continue
    local name; name=$(basename "$tar" .tar)
    local size; size=$(du -h "$tar" | cut -f1)

    local full_ref="${img_name}:${VERSION}"
    local short_ref="${img_name#${img_name%%/*}/}:${VERSION}"  # 去掉 docker.io/ 前缀（如有）
    # unionagents/gaia-xxx 本身就是短名称，full 和 short 一致
    [ "${short_ref}" = "${img_name##*/}:${VERSION}" ] && short_ref="${img_name}:${VERSION}"

    # 检查远程是否已有此镜像（可重入）
    if ssh ${ssh_opts} "${ssh_user}@${ssh_host}" "${remote_ctr} images ls 'ref:${full_ref}' 2>/dev/null" | grep -q "${full_ref}"; then
      echo "    ⏭️  ${name} 远程已存在，跳过（${size}）"
      continue
    fi

    echo "    ⬇️  ${name} (${size}) 流式传输 ..."
    # 用 SSH 管道流式传输：本地 cat tar | ssh 远程 ctr import（不落临时文件）
    if cat "$tar" | ssh ${ssh_opts} "${ssh_user}@${ssh_host}" \
        "${remote_ctr} images import - >/dev/null 2>&1"; then
      echo "    ✅ ${name} 远程导入成功"
      # 补短名称 tag
      ssh ${ssh_opts} "${ssh_user}@${ssh_host}" \
        "${remote_ctr} images tag '${full_ref}' '${short_ref}' >/dev/null 2>&1" 2>/dev/null || true
    else
      echo "    ❌ ${name} 远程导入失败"
      failed=$((failed+1))
    fi
  done

  # 远程节点打标签（通过本地 kubectl，因为远程不一定有 kubectl）
  if [ -n "${remote_node}" ] && [ "${failed}" -eq 0 ] && command -v kubectl >/dev/null 2>&1; then
    if kubectl get node "${remote_node}" >/dev/null 2>&1; then
      kubectl label node "${remote_node}" gaia-images=loaded --overwrite >/dev/null 2>&1 && \
        echo "    🏷️  节点 ${remote_node} 打标签 gaia-images=loaded"
    fi
  fi

  return ${failed}
}

# ── 主流程 ──

echo "[1/2] 导入镜像到本机（$(hostname)）..."
LOCAL_FAIL=0
for entry in "${IMAGE_LIST[@]}"; do
  prefix="${entry%%:*}"
  img_name="${entry#*:}"
  tar=$(ls "${IMAGES_DIR}"/${prefix}-*.tar 2>/dev/null | head -1)
  if [ ! -f "${tar}" ]; then
    echo "    ⚠️  未找到 ${prefix} 的 tar，跳过"
    continue
  fi
  full_ref="${img_name}:${VERSION}"
  short_ref="${img_name}:${VERSION}"  # unionagents/* 本身就是短名称
  import_local "${tar}" "${full_ref}" "${short_ref}" || LOCAL_FAIL=$((LOCAL_FAIL+1))
done

if [ ${LOCAL_FAIL} -gt 0 ]; then
  echo ""
  echo "❌ 本机有 ${LOCAL_FAIL} 个镜像导入失败"
  exit 1
fi

label_local_node

# 多节点分发
REMOTE_FAIL=0
if [ -n "${LOAD_IMAGES_NODES:-}" ]; then
  echo ""
  echo "[2/2] 分发镜像到集群其他节点 ..."
  IFS=',' read -ra NODES <<< "${LOAD_IMAGES_NODES}"
  for node_spec in "${NODES[@]}"; do
    [ -z "${node_spec}" ] && continue
    import_remote "${node_spec}" || REMOTE_FAIL=$((REMOTE_FAIL+1))
  done
  if [ ${REMOTE_FAIL} -gt 0 ]; then
    echo ""
    echo "⚠️  ${REMOTE_FAIL} 个节点分发失败（本机已成功）"
    echo "    未分发的节点上 Pod 将因 ErrImageNeverPull 失败，"
    echo "    可手动在该节点导入镜像，或用 nodeSelector 调度到有镜像的节点"
    exit 2
  fi
else
  echo ""
  echo "[2/2] 跳过远程分发（未配置 LOAD_IMAGES_NODES）"
  echo "    单节点集群无需配置；多节点集群请在 .env.local 设置："
  echo "    LOAD_IMAGES_NODES=\"root@worker1,root@worker2\""
fi

echo ""
echo "✅ 镜像导入完成"
echo "  验证: ${CTR} images ls | grep unionagents/gaia"
echo "  节点标签: kubectl get nodes -l gaia-images=loaded"
