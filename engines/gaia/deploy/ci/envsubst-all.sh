#!/bin/bash
# ============================================================
# 清单模板渲染：把 manifests/ 下的 ${VAR} 占位符替换为实际值
# 输出到临时目录，供 kubectl apply
#
# 用法: bash scripts/envsubst-all.sh <manifests_dir> <output_dir>
# 依赖: envsubst（gettext-base 包）
# ============================================================
set -e

MANIFESTS_DIR="${1:?用法: envsubst-all.sh <manifests_dir> <output_dir>}"
OUTPUT_DIR="${2:?用法: envsubst-all.sh <manifests_dir> <output_dir>}"

mkdir -p "${OUTPUT_DIR}"/infra/core "${OUTPUT_DIR}"/infra/optional "${OUTPUT_DIR}"/services "${OUTPUT_DIR}"/apps

# envsubst 只替换指定的占位符（避免吞掉清单内 shell 脚本的 $VAR 变量，如 gravitino-init 的 $METALAKE）
ENVSUBST_VARS='${VERSION} ${NAMESPACE} ${POD_CIDR} ${STORAGE_CLASS} ${NODE_PORT_WEB_UI} ${IMAGE_PULL_POLICY_INFRA} ${IMAGE_PULL_POLICY_SERVICES} ${GAIA_AI_MODEL}'

render_dir() {
  local src="$1" dst="$2"
  [ -d "${src}" ] || return 0
  for f in "${src}"/*.yaml; do
    [ -f "$f" ] || continue
    name=$(basename "$f")
    envsubst "${ENVSUBST_VARS}" < "$f" > "${dst}/${name}"
  done
}

# namespace.yaml 在 manifests/ 根
[ -f "${MANIFESTS_DIR}/namespace.yaml" ] && \
  envsubst "${ENVSUBST_VARS}" < "${MANIFESTS_DIR}/namespace.yaml" > "${OUTPUT_DIR}/namespace.yaml"

# infra 分 core/optional 两个子目录（按 DEPLOY_PROFILE 选择性 apply）
render_dir "${MANIFESTS_DIR}/infra/core"        "${OUTPUT_DIR}/infra/core"
render_dir "${MANIFESTS_DIR}/infra/optional"   "${OUTPUT_DIR}/infra/optional"
render_dir "${MANIFESTS_DIR}/services"         "${OUTPUT_DIR}/services"
render_dir "${MANIFESTS_DIR}/apps"             "${OUTPUT_DIR}/apps"

echo "  ✅ 清单渲染完成 → ${OUTPUT_DIR}"
