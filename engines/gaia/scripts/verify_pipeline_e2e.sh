#!/usr/bin/env bash
# Pipeline Builder 端到端冒烟测试（真实 DB + 真实 Kestra，非 mock）
# 用法: bash scripts/verify_pipeline_e2e.sh
set -uo pipefail

BASE="http://127.0.0.1:8000"
PIPE_NAME="e2etest_$(date +%s)"
PASS=0
FAIL=0

ok()   { echo "  ✓ $1"; PASS=$((PASS+1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL+1)); }
req()  { # method url data
  local method="$1" url="$2" data="${3:-}"
  if [ -n "$data" ]; then
    curl -s -X "$method" "$BASE$url" -H "Content-Type: application/json" -d "$data"
  else
    curl -s -X "$method" "$BASE$url"
  fi
}

echo "=========================================="
echo "Pipeline Builder E2E (name=$PIPE_NAME)"
echo "=========================================="

# ── 0. 前置: 取一个真实 dataset api_name ──
echo "[0] 取真实 dataset"
DATASET=$(curl -s "$BASE/api/datasets?limit=1" | python3 -c "import sys,json; d=json.load(sys.stdin); items=d if isinstance(d,list) else d.get('items',[]); print(items[0]['api_name'] if items else '')" 2>/dev/null)
if [ -z "$DATASET" ]; then fail "无可用 dataset"; exit 1; fi
ok "dataset=$DATASET"

# ── 1. create pipeline ──
echo "[1] create pipeline"
GRAPH='{"nodes":[{"id":"src1","type":"Source","operator_type":"Source","config":{"extra":{"dataset":"'"$DATASET"'"}}},{"id":"tfm1","type":"Transform","operator_type":"Filter","config":{"expression":"1=1"}},{"id":"snk1","type":"Sink","operator_type":"Sink","config":{"extra":{"dataset":"'"$DATASET"'"}}}],"edges":[{"id":"e1","source_id":"src1","target_id":"tfm1"},{"id":"e2","source_id":"tfm1","target_id":"snk1"}]}'
RESP=$(req POST /api/v1/pipelines "{\"api_name\":\"$PIPE_NAME\",\"display_name\":\"E2E Test\",\"description\":\"end-to-end smoke\",\"sink_dataset_api_name\":\"$DATASET\",\"graph\":$GRAPH}")
GOT_NAME=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('api_name',''))" 2>/dev/null)
VER1=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('current_version_number',0))" 2>/dev/null)
if [ "$GOT_NAME" = "$PIPE_NAME" ] && [ "$VER1" = "1" ]; then ok "created name=$GOT_NAME ver=$VER1"; else fail "create failed: $RESP"; fi

# ── 2. get pipeline ──
echo "[2] get pipeline"
RESP=$(req GET /api/v1/pipelines/$PIPE_NAME)
GOT_NAME=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('api_name',''))" 2>/dev/null)
if [ "$GOT_NAME" = "$PIPE_NAME" ]; then ok "get ok"; else fail "get failed: $RESP"; fi

# ── 3. patch (new version - add a Select node) ──
echo "[3] patch (new version)"
GRAPH2='{"nodes":[{"id":"src1","type":"Source","operator_type":"Source","config":{"extra":{"dataset":"'"$DATASET"'"}}},{"id":"sel1","type":"Transform","operator_type":"Select","config":{"columns":["id"]}},{"id":"snk1","type":"Sink","operator_type":"Sink","config":{"extra":{"dataset":"'"$DATASET"'"}}}],"edges":[{"id":"e1","source_id":"src1","target_id":"sel1"},{"id":"e2","source_id":"sel1","target_id":"snk1"}]}'
RESP=$(req PATCH /api/v1/pipelines/$PIPE_NAME "{\"graph\":$GRAPH2,\"change_summary\":\"add select\"}")
VER2=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('current_version_number',0))" 2>/dev/null)
if [ "$VER2" = "2" ]; then ok "patched ver=$VER2"; else fail "patch failed: $RESP"; fi

# ── 4. list versions ──
echo "[4] list versions"
RESP=$(req GET /api/v1/pipelines/$PIPE_NAME/versions)
VCOUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))" 2>/dev/null)
if [ "$VCOUNT" = "2" ]; then ok "versions=2"; else fail "versions failed: $RESP"; fi

# ── 5. validate raw graph ──
echo "[5] validate raw graph"
RESP=$(req POST /api/v1/pipelines/validate "$GRAPH")
VALID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('valid',''))" 2>/dev/null)
if [ "$VALID" = "True" ]; then ok "validate ok"; else fail "validate: $RESP"; fi

# ── 6. deploy ──
echo "[6] deploy"
RESP=$(req POST /api/v1/pipelines/$PIPE_NAME/deploy '{"flow_id":"test_flow"}')
DEPLOY_STATUS=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null)
if [ -n "$DEPLOY_STATUS" ]; then ok "deploy status=$DEPLOY_STATUS"; else fail "deploy: $RESP"; fi

# ── 7. trigger build ──
echo "[7] trigger build"
RESP=$(req POST /api/v1/pipelines/$PIPE_NAME/builds '{}')
BUILD_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('build_id',''))" 2>/dev/null)
BUILD_STATUS=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null)
if [ -n "$BUILD_ID" ]; then ok "build id=$BUILD_ID status=$BUILD_STATUS"; else fail "build: $RESP"; fi

# ── 8. list builds ──
echo "[8] list builds"
RESP=$(req GET /api/v1/pipelines/$PIPE_NAME/builds)
BCOUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d))" 2>/dev/null)
if [ "$BCOUNT" -ge "1" ] 2>/dev/null; then ok "builds count=$BCOUNT"; else fail "list builds: $RESP"; fi

# ── 9. get build detail ──
echo "[9] get build detail"
RESP=$(req GET /api/v1/pipelines/$PIPE_NAME/builds/$BUILD_ID)
GOT_BUILD=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('build_id',''))" 2>/dev/null)
if [ "$GOT_BUILD" = "$BUILD_ID" ]; then ok "build detail ok"; else fail "build detail: $RESP"; fi

# ── 10. cancel build ──
echo "[10] cancel build"
RESP=$(req POST /api/v1/pipelines/$PIPE_NAME/builds/$BUILD_ID/cancel)
CANCEL_STATUS=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null)
if [ -n "$CANCEL_STATUS" ]; then ok "cancel status=$CANCEL_STATUS"; else fail "cancel: $RESP"; fi

# ── 11. list pipelines ──
echo "[11] list pipelines"
RESP=$(req GET "/api/v1/pipelines?limit=100")
FOUND=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); items=d.get('items',[]); print(any(i.get('api_name')=='$PIPE_NAME' for i in items))" 2>/dev/null)
if [ "$FOUND" = "True" ]; then ok "list contains test"; else fail "list: $RESP"; fi

# ── 12. rollback version ──
echo "[12] rollback to v1"
RESP=$(req POST /api/v1/pipelines/$PIPE_NAME/versions/1/rollback)
RB_VER=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('current_version_number',0))" 2>/dev/null)
if [ -n "$RB_VER" ]; then ok "rollback ver=$RB_VER"; else fail "rollback: $RESP"; fi

# ── 13. deprecate ──
echo "[13] deprecate"
RESP=$(req POST /api/v1/pipelines/$PIPE_NAME/deprecate)
DEP_STATUS=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null)
if [ -n "$DEP_STATUS" ]; then ok "deprecate status=$DEP_STATUS"; else fail "deprecate: $RESP"; fi

# ── 14. delete ──
echo "[14] delete"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$BASE/api/v1/pipelines/$PIPE_NAME")
if [ "$CODE" = "204" ]; then ok "deleted"; else fail "delete code=$CODE"; fi

echo "=========================================="
echo "PASS=$PASS FAIL=$FAIL"
echo "=========================================="
[ "$FAIL" -eq 0 ]
