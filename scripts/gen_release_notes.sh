#!/bin/bash
# ============================================================
# UnionAgents (知行) Release Notes 生成脚本
# 从 git log 自动生成版本间的 Release Notes
# 用法: bash scripts/gen_release_notes.sh <from_tag> <to_ref> [输出文件]
# ============================================================
set -euo pipefail

FROM_TAG="${1:?用法: gen_release_notes.sh <from_tag> <to_ref> [输出文件]}"
TO_REF="${2:-HEAD}"
OUTPUT="${3:-/dev/stdout}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

FROM_DATE=$(git log -1 --format="%ai" "$FROM_TAG" 2>/dev/null | cut -d' ' -f1 || echo "unknown")
TO_DATE=$(git log -1 --format="%ai" "$TO_REF" 2>/dev/null | cut -d' ' -f1 || echo "unknown")
TO_COMMIT=$(git rev-parse --short "$TO_REF" 2>/dev/null || echo "unknown")
COMMIT_COUNT=$(git rev-list --count "$FROM_TAG..$TO_REF" 2>/dev/null || echo "0")

{
echo "# UnionAgents (知行) Release Notes"
echo ""
echo "- 版本: ${TO_REF} (${TO_COMMIT})"
echo "- 基线: ${FROM_TAG} (${FROM_DATE})"
echo "- 发布日期: $(date '+%Y-%m-%d')"
echo "- 提交数: ${COMMIT_COUNT}"
echo ""

# ── 分类汇总 ──
echo "## 变更分类汇总"
echo ""

# feat
FEATS=$(git log "$FROM_TAG..$TO_REF" --format="%s" | grep -iE "^feat" | grep -v "^merge" || true)
if [ -n "$FEATS" ]; then
    echo "### 新功能"
    echo "$FEATS" | sed 's/^/- /' | grep -v "^- merge"
    echo ""
fi

# fix
FIXES=$(git log "$FROM_TAG..$TO_REF" --format="%s" | grep -iE "^fix" | grep -v "^merge" || true)
if [ -n "$FIXES" ]; then
    echo "### Bug 修复"
    echo "$FIXES" | sed 's/^/- /' | grep -v "^- merge"
    echo ""
fi

# refactor
REFACTORS=$(git log "$FROM_TAG..$TO_REF" --format="%s" | grep -iE "^refactor" | grep -v "^merge" || true)
if [ -n "$REFACTORS" ]; then
    echo "### 重构优化"
    echo "$REFACTORS" | sed 's/^/- /' | grep -v "^- merge"
    echo ""
fi

# docs
DOCS=$(git log "$FROM_TAG..$TO_REF" --format="%s" | grep -iE "^docs" | grep -v "^merge" || true)
if [ -n "$DOCS" ]; then
    echo "### 文档"
    echo "$DOCS" | sed 's/^/- /' | grep -v "^- merge"
    echo ""
fi

# chore / other
OTHERS=$(git log "$FROM_TAG..$TO_REF" --format="%s" | grep -ivE "^feat|^fix|^refactor|^docs|^merge|^chore|^!|^test|^ci" | grep -v "^$" || true)
CHORES=$(git log "$FROM_TAG..$TO_REF" --format="%s" | grep -iE "^chore|^test|^ci" | grep -v "^merge" | grep -v "^!" || true)
if [ -n "$OTHERS" ] || [ -n "$CHORES" ]; then
    echo "### 其他"
    [ -n "$OTHERS" ] && echo "$OTHERS" | sed 's/^/- /'
    [ -n "$CHORES" ] && echo "$CHORES" | sed 's/^/- /'
    echo ""
fi

# ── 数据库迁移 ──
MIGRATIONS=$(git diff --name-only --diff-filter=A "$FROM_TAG..$TO_REF" -- services/manager/migrations/ 2>/dev/null || true)
if [ -n "$MIGRATIONS" ]; then
    echo "## 数据库迁移"
    echo ""
    echo "本次升级包含以下数据库迁移脚本，升级时会自动执行："
    echo ""
    echo "$MIGRATIONS" | sed 's|.*/|- |'
    echo ""
fi

# ── 镜像变更 ──
echo "## 镜像变更"
echo ""
echo "| 镜像 | 是否变更 |"
echo "|------|----------|"

for img in manager controller engine-hermes hub channel-gateway; do
    dir="services/${img}"
    [ "$img" = "engine-hermes" ] && dir="engines/hermes"
    if git diff --quiet "$FROM_TAG..$TO_REF" -- "$dir/" 2>/dev/null; then
        echo "| $img | - |"
    else
        echo "| $img | ✅ 变更 |"
    fi
done

# llm-gateway
if git diff --quiet "$FROM_TAG..$TO_REF" -- services/llm-gateway/ 2>/dev/null; then
    echo "| llm-gateway (litellm) | - |"
else
    echo "| llm-gateway (litellm) | ✅ 变更 |"
fi

# 前端
if git diff --quiet "$FROM_TAG..$TO_REF" -- apps/admin/ apps/docs/ 2>/dev/null; then
    echo "| console-admin | - |"
else
    echo "| console-admin | ✅ 变更 |"
fi
if git diff --quiet "$FROM_TAG..$TO_REF" -- apps/enduser/ 2>/dev/null; then
    echo "| console-enduser / enduser-portal | - |"
else
    echo "| console-enduser / enduser-portal | ✅ 变更 |"
fi

echo ""

# ── 完整 Changelog ──
echo "## 完整 Changelog"
echo ""
echo '```'
git log "$FROM_TAG..$TO_REF" --oneline --format="%h %s" | grep -v "merge develop into develop" | grep -v "^.* !" | head -100
echo '```'
echo ""

} > "$OUTPUT"

echo "Release Notes 已生成: ${OUTPUT}" >&2
