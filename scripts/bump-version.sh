#!/bin/bash
# ============================================================
# UnionAgents 版本同步脚本
# 用法: scripts/bump-version.sh [版本号]
# 示例: scripts/bump-version.sh 0.6.0
#       scripts/bump-version.sh 0.6.0-beta.1
#
# 如果不传版本号参数，则读取 VERSION 文件中的版本号进行同步
# ============================================================
set -euo pipefail

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}✓${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; exit 1; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="${REPO_ROOT}/VERSION"

# ── 1. 确定版本号 ──
if [ $# -ge 1 ]; then
  NEW_VERSION="$1"
  # 如果带 v 前缀，自动去掉（VERSION 文件只存裸版本号）
  NEW_VERSION="${NEW_VERSION#v}"
  echo "$NEW_VERSION" > "$VERSION_FILE"
  info "VERSION 文件更新为: ${NEW_VERSION}"
else
  if [ ! -f "$VERSION_FILE" ]; then
    error "VERSION 文件不存在，请先创建或传入版本号参数"
  fi
  NEW_VERSION="$(cat "$VERSION_FILE" | tr -d '[:space:]')"
  if [ -z "$NEW_VERSION" ]; then
    error "VERSION 文件为空，请传入版本号参数"
  fi
  info "从 VERSION 文件读取版本号: ${NEW_VERSION}"
fi

# 校验 SemVer 格式（宽松版）
if ! echo "$NEW_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$'; then
  warn "版本号 '${NEW_VERSION}' 不符合 SemVer 格式 (MAJOR.MINOR.PATCH)"
  warn "继续执行，但请确认版本号正确"
fi

echo ""
echo "=========================================="
echo "  同步版本: ${NEW_VERSION}"
echo "=========================================="
echo ""

# ── 2. 更新 pyproject.toml ──
PYPROJ="${REPO_ROOT}/pyproject.toml"
if [ -f "$PYPROJ" ]; then
  sed -i '' "s/^version = \".*\"/version = \"${NEW_VERSION}\"/" "$PYPROJ"
  info "pyproject.toml"
else
  warn "pyproject.toml 不存在，跳过"
fi

# ── 3. 更新 package.json ──
update_package_json() {
  local file="$1"
  if [ -f "$file" ]; then
    sed -i '' "s/\"version\": \".*\"/\"version\": \"${NEW_VERSION}\"/" "$file"
    info "$file"
  else
    warn "$file 不存在，跳过"
  fi
}
update_package_json "${REPO_ROOT}/package.json"
update_package_json "${REPO_ROOT}/apps/admin/package.json"
update_package_json "${REPO_ROOT}/apps/enduser/package.json"
update_package_json "${REPO_ROOT}/apps/landing/package.json"

# ── 4. 更新 FastAPI 服务版本 ──
update_fastapi_version() {
  local file="$1"
  if [ -f "$file" ]; then
    # 更新 FastAPI(title=..., version="xxx", ...)
    sed -i '' "s/version=\"[^\"]*\"/version=\"${NEW_VERSION}\"/g" "$file"
    # 更新 health 端点或配置接口中的硬编码版本字符串
    # 匹配 "version": "xxx" 模式
    sed -i '' "s/\"version\": \"[^\"]*\"/\"version\": \"${NEW_VERSION}\"/g" "$file"
    info "$file"
  else
    warn "$file 不存在，跳过"
  fi
}
update_fastapi_version "${REPO_ROOT}/services/manager/app/main.py"
update_fastapi_version "${REPO_ROOT}/services/gateway/app/main.py"

# ── 4b. 更新 Android Gradle versionName + versionCode ──
ANDROID_GRADLE="${REPO_ROOT}/apps/android/app/build.gradle.kts"
if [ -f "$ANDROID_GRADLE" ]; then
  sed -i '' "s/versionName = \"[^\"]*\"/versionName = \"${NEW_VERSION}\"/g" "$ANDROID_GRADLE"
  PATCH_NUM=$(echo "$NEW_VERSION" | awk -F. '{print $3}' | sed 's/[^0-9].*//')
  if [ -n "$PATCH_NUM" ]; then
    sed -i "" "s/versionCode = [0-9]*/versionCode = ${PATCH_NUM}/g" "$ANDROID_GRADLE"
  fi
  info "apps/android/app/build.gradle.kts"
else
  warn "apps/android/app/build.gradle.kts 不存在，跳过"
fi

# ── 4c. 更新 HarmonyOS AppScope/app.json5 versionName + versionCode ──
HARMONY_APP_JSON5="${REPO_ROOT}/apps/harmony/AppScope/app.json5"
if [ -f "$HARMONY_APP_JSON5" ]; then
  sed -i '' "s/\"versionName\": \"[^\"]*\"/\"versionName\": \"${NEW_VERSION}\"/g" "$HARMONY_APP_JSON5"
  PATCH_NUM=$(echo "$NEW_VERSION" | awk -F. '{print $3}' | sed 's/[^0-9].*//')
  if [ -n "$PATCH_NUM" ]; then
    sed -i '' "s/\"versionCode\": [0-9]*/\"versionCode\": ${PATCH_NUM}/g" "$HARMONY_APP_JSON5"
  fi
  info "apps/harmony/AppScope/app.json5"
else
  warn "apps/harmony/AppScope/app.json5 不存在，跳过"
fi

# ── 5. 更新 README.md ──
README="${REPO_ROOT}/README.md"
if [ -f "$README" ]; then
  sed -i '' "s/版本: v[0-9]*\.[0-9]*\.[0-9][^\"]*/版本: ${NEW_VERSION}/g" "$README"
  sed -i '' "s/构建带版本号镜像（如 v[0-9]*\.[0-9]*\.[0-9][^）]*）/构建带版本号镜像（如 ${NEW_VERSION}）/g" "$README"
  info "README.md"
else
  warn "README.md 不存在，跳过"
fi

# ── 6. 更新 CI 文档示例版本号 ──
CI_DEPLOYMENT="${REPO_ROOT}/deploy/ci/deployment.yaml"
if [ -f "$CI_DEPLOYMENT" ]; then
  sed -i '' "s|sed \"s/__VERSION__/[v]*[0-9]*\.[0-9]*\.[0-9][^\"]*/g|sed \"s/__VERSION__/${NEW_VERSION}/g|g" "$CI_DEPLOYMENT" 2>/dev/null || true
  # 更精确地替换注释中的示例版本
  sed -i '' "s|sed \"s/__VERSION__/v[0-9]*\.[0-9]*\.[0-9][^ ]*|sed \"s/__VERSION__/${NEW_VERSION}|g" "$CI_DEPLOYMENT" 2>/dev/null || true
  # 上述可能有边界问题，直接用更精确的匹配
  info "deploy/ci/deployment.yaml (请手动检查注释中的版本示例)"
else
  warn "deploy/ci/deployment.yaml 不存在，跳过"
fi

CI_DEPLOY="${REPO_ROOT}/deploy/ci/deploy.sh"
if [ -f "$CI_DEPLOY" ]; then
  # 更新用法示例
  sed -i '' "s|bash deploy/ci/deploy.sh v[0-9]*\.[0-9]*\.[0-9][^\"']*|bash deploy/ci/deploy.sh ${NEW_VERSION}|g" "$CI_DEPLOY"
  sed -i '' "s|bash deploy/ci/deploy\.sh [0-9]*\.[0-9]*\.[0-9][^\"']*|bash deploy/ci/deploy.sh ${NEW_VERSION}|g" "$CI_DEPLOY"
  info "deploy/ci/deploy.sh"
else
  warn "deploy/ci/deploy.sh 不存在，跳过"
fi

echo ""
echo "=========================================="
echo -e "${GREEN}  ✅ 版本同步完成: ${NEW_VERSION}${NC}"
echo "=========================================="
echo ""
echo "请执行以下步骤确认："
echo "  1. git diff --stat  查看变更的文件列表"
echo "  2. git diff         检查具体变更"
echo "  3. git add -A && git commit -m \"chore: bump version to ${NEW_VERSION}\""
echo "  4. git tag v${NEW_VERSION}"
echo "  5. git push && git push --tags"
echo ""
