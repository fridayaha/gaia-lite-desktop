# ============================================================
# Gaia k3s 部署（独立 namespace `gaia`，与 UnionAgents 平台隔离）
# 端口避让：NodePort 30082（web-ui），避开 UA 的 30080/30081/30090/30030
# ============================================================

NAMESPACE ?= gaia
# 本地开发默认值（k8s-* 目标用；生产用 deploy.sh 制品流程）
VERSION ?= latest
POD_CIDR ?= 10.42.0.0/16
STORAGE_CLASS ?= local-path
NODE_PORT_WEB_UI ?= 30082
IMAGE_PULL_POLICY_INFRA ?= IfNotPresent
IMAGE_PULL_POLICY_SERVICES ?= IfNotPresent
# envsubst 只替换这些变量（避免吞掉清单内 shell 脚本的 $VAR）
ENVSUBST_VARS = '$${VERSION} $${NAMESPACE} $${POD_CIDR} $${STORAGE_CLASS} $${NODE_PORT_WEB_UI} $${IMAGE_PULL_POLICY_INFRA} $${IMAGE_PULL_POLICY_SERVICES}'
DEPLOY_PROFILE ?= minimal
# export 给 envsubst 子进程
$(foreach v,VERSION NAMESPACE POD_CIDR STORAGE_CLASS NODE_PORT_WEB_UI IMAGE_PULL_POLICY_INFRA IMAGE_PULL_POLICY_SERVICES,$(eval export $(v)))

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

.PHONY: k8s-ns
k8s-ns: ## Create gaia namespace
	kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -

.PHONY: k8s-infra
k8s-infra: k8s-ns ## Deploy infrastructure (profile=minimal: PG/Gravitino/Trino; profile=full: +Doris/RustFS/SeaTunnel/Kafka/Kestra/Neo4j)
	-kubectl delete job gaia-gravitino-init -n $(NAMESPACE) --ignore-not-found
	@for f in deploy/k8s/infra/core/*.yaml; do \
	  envsubst $(ENVSUBST_VARS) < $$f | kubectl apply -f - -n $(NAMESPACE); \
	done
	@if [ "$(DEPLOY_PROFILE)" = "full" ]; then \
	  for f in deploy/k8s/infra/optional/*.yaml; do \
	    envsubst $(ENVSUBST_VARS) < $$f | kubectl apply -f - -n $(NAMESPACE); \
	  done; \
	  echo "  ✅ infra applied (core + optional)"; \
	else \
	  echo "  ✅ infra applied (core only, profile=$(DEPLOY_PROFILE))"; \
	fi

.PHONY: k8s-services
k8s-services: ## Deploy backend services (migrate Job + api + better-auth)
	-kubectl delete job gaia-migrate -n $(NAMESPACE) --ignore-not-found
	@for f in deploy/k8s/services/*.yaml; do \
	  envsubst $(ENVSUBST_VARS) < $$f | kubectl apply -f - -n $(NAMESPACE); \
	done

.PHONY: k8s-apps
k8s-apps: ## Deploy frontend (web-ui)
	@for f in deploy/k8s/apps/*.yaml; do \
	  envsubst $(ENVSUBST_VARS) < $$f | kubectl apply -f - -n $(NAMESPACE); \
	done

.PHONY: k8s-all
k8s-all: k8s-infra k8s-services k8s-apps ## Deploy everything

.PHONY: k8s-delete
k8s-delete: ## Delete all gaia resources (namespace + everything in it)
	kubectl delete namespace $(NAMESPACE) --ignore-not-found

.PHONY: k8s-logs
k8s-logs: ## Tail logs for a service (usage: make k8s-logs SVC=gaia-api)
	kubectl logs -n $(NAMESPACE) -l app=$(SVC) -f

.PHONY: k8s-pods
k8s-pods: ## List all pods
	kubectl get pods -n $(NAMESPACE)

.PHONY: k8s-svc
k8s-svc: ## List all services
	kubectl get svc -n $(NAMESPACE)

.PHONY: k8s-pvc
k8s-pvc: ## List all PVCs
	kubectl get pvc -n $(NAMESPACE)

# ============================================================
# Port Forwarding (Local Development)
# ============================================================

.PHONY: pf-api
pf-api: ## Port-forward gaia-api (8000)
	kubectl port-forward -n $(NAMESPACE) svc/gaia-api 8000:8000

.PHONY: pf-web-ui
pf-web-ui: ## Port-forward gaia-web-ui (5173 → 80)
	kubectl port-forward -n $(NAMESPACE) svc/gaia-web-ui 5173:80

.PHONY: pf-postgres
pf-postgres: ## Port-forward gaia-postgres (5432)
	kubectl port-forward -n $(NAMESPACE) svc/gaia-postgres 5432:5432

.PHONY: pf-trino
pf-trino: ## Port-forward gaia-trino (8080)
	kubectl port-forward -n $(NAMESPACE) svc/gaia-trino 8080:8080

.PHONY: pf-gravitino
pf-gravitino: ## Port-forward gaia-gravitino (8090 + 9001)
	kubectl port-forward -n $(NAMESPACE) svc/gaia-gravitino 8090:8090 9001:9001

.PHONY: pf-all
pf-all: ## Port-forward all dev services (background)
	kubectl port-forward -n $(NAMESPACE) svc/gaia-api 8000:8000 &
	kubectl port-forward -n $(NAMESPACE) svc/gaia-web-ui 5173:80 &

# ============================================================
# 热重载（开发调试用，秒级生效，不重建镜像）
# ============================================================
# 与 local-update.sh 互补：改代码用热重载，改依赖/Dockerfile/部署清单才用 local-update.sh。

.PHONY: dev-api
dev-api: ## 后端热重载：hostPath 挂源码 + uvicorn --reload（改 Python 代码 ~1s 生效）
	bash scripts/dev-hotload-api.sh

.PHONY: dev-api-off
dev-api-off: ## 关闭后端热重载，回到镜像版
	bash scripts/dev-hotload-api.sh off

.PHONY: dev-web
dev-web: ## 前端热重载：本地 Vite dev server + HMR（改前端代码 <1s 生效），API 经 proxy 转发 k8s
	bash scripts/dev-hotload-webui.sh

.PHONY: dev
dev: ## 前后端同时热重载（= dev-web --api）
	bash scripts/dev-hotload-webui.sh --api

# ============================================================
# Docker Builds
# ============================================================
# Docker Builds
# ============================================================
# 本地单架构构建（开发调试用，产物进 docker 本地缓存）
# 4 个 Dockerfile 的构建上下文统一为仓库根（.），COPY 路径基于仓库根

.PHONY: docker-api
docker-api: ## Build gaia-api image (local, amd64 only)
	docker build -t unionagents/gaia-api:latest -f Dockerfile .

.PHONY: docker-trino
docker-trino: ## Build gaia-trino-plugins image (gravitino connector plugin only, ~31MB)
	docker build -t unionagents/gaia-trino-plugins:latest -f Dockerfile.trino-plugins .

.PHONY: docker-better-auth
docker-better-auth: ## Build gaia-better-auth image
	docker build -t unionagents/gaia-better-auth:latest -f auth-server/Dockerfile .

.PHONY: docker-web-ui
docker-web-ui: ## Build gaia-web-ui image
	docker build -t unionagents/gaia-web-ui:latest -f src/web-ui/Dockerfile .

.PHONY: docker-all
docker-all: docker-api docker-trino docker-better-auth docker-web-ui ## Build all Gaia images (local, amd64)

# ============================================================
# 多架构构建 + 制品打包（CI / 发布用）
# ============================================================
# 多架构（amd64+arm64）构建，导出 OCI tar（含 fat manifest），不推 registry
# 产物：dist/images/gaia-<svc>-<VERSION>.tar（每服务一个多架构 tar）
VERSION ?= 0.1.0
PLATFORMS ?= linux/amd64,linux/arm64
BUILDER ?= gaia-multiarch
# 架构标识（用于镜像 tar 文件名，区分单/多架构制品）
# 双架构 → multi，仅 arm64 → arm64，仅 amd64 → amd64
ifeq ($(PLATFORMS),linux/amd64,linux/arm64)
  ARCH_TAG := multi
else ifeq ($(PLATFORMS),linux/arm64)
  ARCH_TAG := arm64
else ifeq ($(PLATFORMS),linux/amd64)
  ARCH_TAG := amd64
else
  ARCH_TAG := custom
endif

# 4 个自有镜像的 Dockerfile 路径（上下文统一仓库根 .）
DF_API := Dockerfile
DF_TRINO := Dockerfile.trino-plugins
DF_GRAVITINO_LIBS := Dockerfile.gravitino-libs
DF_BETTER_AUTH := auth-server/Dockerfile
DF_WEB_UI := src/web-ui/Dockerfile

.PHONY: buildx-builder
buildx-builder: ## Create multiarch buildx builder (run once after install)
	@docker buildx inspect $(BUILDER) >/dev/null 2>&1 || \
	  docker buildx create --name $(BUILDER) --driver docker-container \
	    --driver-opt network=host --config $(CURDIR)/deploy/ci/buildkitd.toml --bootstrap
	docker buildx use $(BUILDER)

.PHONY: docker-buildx
docker-buildx: buildx-builder ## Build images → OCI tar (dist/images/), arch tag from PLATFORMS
	@mkdir -p dist/images
	@for svc in api trino-plugins gravitino-libs better-auth web-ui; do \
	  df=$$(case $$svc in api) echo $(DF_API);; trino-plugins) echo $(DF_TRINO);; \
	    gravitino-libs) echo $(DF_GRAVITINO_LIBS);; \
	    better-auth) echo $(DF_BETTER_AUTH);; web-ui) echo $(DF_WEB_UI);; esac); \
	  echo "→ Building gaia-$$svc [$(ARCH_TAG)] $(PLATFORMS) → dist/images/gaia-$$svc-$(VERSION)-$(ARCH_TAG).tar"; \
	  docker buildx build --builder $(BUILDER) \
	    --platform $(PLATFORMS) \
	    --output type=oci,dest=dist/images/gaia-$$svc-$(VERSION)-$(ARCH_TAG).tar \
	    -t unionagents/gaia-$$svc:$(VERSION) \
	    -f $$df . || exit 1; \
	done
	@echo "✅ 镜像构建完成 [$(ARCH_TAG)] → dist/images/"

.PHONY: package
package: docker-buildx ## Build images + pack deploy artifact (dist/gaia-deploy-VERSION-ARCH.tar.gz)
	@bash deploy/ci/package.sh $(VERSION) $(ARCH_TAG)
	@echo "✅ 部署制品打包完成 [$(ARCH_TAG)] → dist/gaia-deploy-$(VERSION)-$(ARCH_TAG).tar.gz"
