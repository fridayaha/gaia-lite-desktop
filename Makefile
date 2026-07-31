.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ============================================================
# k3s Infrastructure
# ============================================================

NAMESPACE ?= unionagents

.PHONY: k8s-ns
k8s-ns: ## Create unionagents namespace
	kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -

.PHONY: k8s-infra
k8s-infra: k8s-ns ## Deploy infrastructure (PostgreSQL, MinIO)
	kubectl apply -f deploy/k8s/infra/ -n $(NAMESPACE)

.PHONY: k8s-services
k8s-services: ## Deploy backend services (manager, gateway)
	kubectl apply -f deploy/k8s/services/ -n $(NAMESPACE)

.PHONY: k8s-apps
k8s-apps: ## Deploy frontend apps (admin, enduser)
	kubectl apply -f deploy/k8s/apps/ -n $(NAMESPACE)

.PHONY: k8s-all
k8s-all: k8s-infra k8s-services k8s-apps ## Deploy everything

.PHONY: k8s-delete
k8s-delete: ## Delete all unionagents resources
	kubectl delete namespace $(NAMESPACE) --ignore-not-found

.PHONY: k8s-logs
k8s-logs: ## Tail logs for a service (usage: make k8s-logs SVC=manager)
	kubectl logs -n $(NAMESPACE) -l app=$(SVC) -f

.PHONY: k8s-pods
k8s-pods: ## List all pods
	kubectl get pods -n $(NAMESPACE)

.PHONY: k8s-svc
k8s-svc: ## List all services
	kubectl get svc -n $(NAMESPACE)

# ============================================================
# Port Forwarding (Local Development)
# ============================================================

.PHONY: pf-manager
pf-manager: ## Port-forward manager service
	kubectl port-forward -n $(NAMESPACE) svc/manager 8002:8002

.PHONY: pf-gateway
pf-gateway: ## Port-forward gateway service
	kubectl port-forward -n $(NAMESPACE) svc/gateway 8010:8010

.PHONY: pf-hub
pf-hub: ## Port-forward hub service
	kubectl port-forward -n $(NAMESPACE) svc/hub 8003:8003

.PHONY: pf-admin
pf-admin: ## Port-forward admin frontend
	kubectl port-forward -n $(NAMESPACE) svc/console-admin 3020:80

.PHONY: pf-enduser
pf-enduser: ## Port-forward enduser frontend
	kubectl port-forward -n $(NAMESPACE) svc/enduser-portal 3001:80

.PHONY: pf-all
pf-all: ## Port-forward all services (background)
	kubectl port-forward -n $(NAMESPACE) svc/manager 8002:8002 &
	kubectl port-forward -n $(NAMESPACE) svc/gateway 8010:8010 &
	kubectl port-forward -n $(NAMESPACE) svc/hub 8003:8003 &

# ============================================================
# Development
# ============================================================

.PHONY: dev-manager
dev-manager: ## Run manager service locally
	cd services/manager && uvicorn app.main:app --reload --port 8002

.PHONY: dev-gateway
dev-gateway: ## Run gateway service locally
	cd services/gateway && uvicorn app.main:app --reload --port 8010

.PHONY: dev-hub
dev-hub: ## Run hub service locally
	cd services/hub/backend && uvicorn app.main:app --reload --port 8003

.PHONY: test
test: ## Run all Python unit tests
	cd services/manager && pytest
	cd services/gateway && pytest
	cd services/hub/backend && DATABASE_URL=sqlite:///./hub_test.db PYTHONPATH=. pytest
	cd engines/hermes && PYTHONPATH=. pytest tests
	cd services/skill-secret-sidecar && PYTHONPATH=. pytest tests

.PHONY: test-hub
test-hub: ## Run hub unit tests (sqlite, no external DB needed)
	cd services/hub/backend && DATABASE_URL=sqlite:///./hub_test.db PYTHONPATH=. pytest

.PHONY: test-integration
test-integration: ## Run integration tests (need Docker for testcontainers)
	RUN_INTEGRATION_TESTS=1 cd services/manager && pytest tests/worker/test_worker_minio_archiver_integration.py tests/worker/test_worker_data_integrity.py -v

.PHONY: test-oss
test-oss: ## Run OSS compatibility tests (need Alibaba Cloud credentials: UA_OSS_ACCESS_KEY, UA_OSS_SECRET_KEY)
	cd services/manager && pytest tests/worker/test_worker_oss_compatibility.py -v

.PHONY: test-e2e
test-e2e: ## Run full lifecycle E2E tests (need k3s cluster + MinIO + postgres port-forward)
	@echo "前置：k3s 集群运行中（make k8s-infra），且已 port-forward postgres(5432) 与 minio(9000)"
	@echo "  kubectl port-forward -n unionagents svc/postgres 5432:5432 &"
	@echo "  kubectl port-forward -n unionagents svc/minio 9000:9000 &"
	cd services/manager && UA_PVC_STORAGE_CLASS=local-path RUN_E2E_TESTS=1 pytest tests/worker/e2e/ -v

.PHONY: test-all
test-all: test test-integration test-oss ## Run unit + integration + OSS tests (all except E2E)

.PHONY: lint
lint: ## Run linters
	ruff check services/ pkg/
	ruff format --check services/ pkg/

.PHONY: fmt
fmt: ## Format code
	ruff format services/ pkg/
	ruff check --fix services/ pkg/

# ============================================================
# Docker Builds
# ============================================================

.PHONY: docker-manager
docker-manager: ## Build manager image
	docker build -t unionagents/manager:latest -f services/manager/Dockerfile .

.PHONY: docker-skill-engine
docker-skill-engine: ## Build skill-engine image (技能开发/调试引擎，Node.js)
	docker build -t unionagents/skill-engine:latest -f services/skill-engine/Dockerfile .

.PHONY: android-base-apk
android-base-apk: ## Build Android base APK template (with placeholder URLs) and copy into services/manager/base-apks/
	cd apps/android && ./gradlew :app:assembleRelease
	@mkdir -p services/manager/base-apks
	@APK_VERSION=$$(grep -oE 'versionName = "[^"]+"' apps/android/app/build.gradle.kts | head -1 | sed 's/versionName = "//;s/"$$//'); \
		cp apps/android/app/build/outputs/apk/release/app-release.apk services/manager/base-apks/知行-$${APK_VERSION}.apk; \
		rm -f services/manager/base-apks/知行-template.apk; \
		echo "base APK copied to services/manager/base-apks/知行-$${APK_VERSION}.apk"

.PHONY: docker-manager-with-apk
docker-manager-with-apk: ## Build manager image with fresh base APK template bundled
	$(MAKE) android-base-apk
	$(MAKE) docker-manager

.PHONY: docker-gateway
docker-gateway: ## Build gateway image
	docker build -t unionagents/gateway:latest -f services/gateway/Dockerfile .

.PHONY: docker-asr
docker-asr: ## Build asr-sidecar image (faster-whisper；国内构建可设 PYTHON_BASE 走镜像加速源)
	docker build \
		--build-arg PYTHON_BASE=$(PYTHON_BASE) \
		--build-arg HF_ENDPOINT=$(HF_ENDPOINT) \
		-t unionagents/asr-sidecar:latest -f services/asr-sidecar/Dockerfile services/asr-sidecar

.PHONY: docker-skill-secret-sidecar
docker-skill-secret-sidecar: ## Build skill-secret-sidecar image (引擎 Pod sidecar，解密技能凭证；每个引擎 Pod 必需)
	docker build -t unionagents/skill-secret-sidecar:latest -f services/skill-secret-sidecar/Dockerfile services/skill-secret-sidecar

.PHONY: docker-hermes
docker-hermes: ## Build Hermes engine image (V2: 多 Profile + nginx)
	docker build -t unionagents/engine-hermes-v2:latest -f engines/hermes/Dockerfile .

.PHONY: docker-hub
docker-hub: ## Build hub service image
	docker build -t unionagents/hub:latest -f services/hub/Dockerfile .

ACR_REGISTRY ?= registry.example.com/unionagents
PYTHON_BASE ?= python:3.11-slim
HF_ENDPOINT ?= https://huggingface.co

.PHONY: docker-admin-deps
docker-admin-deps: ## Build & push admin deps image to registry (deps 变更时执行)
	docker build -f apps/admin/Dockerfile.deps \
		-t $(ACR_REGISTRY)/console-admin-deps:latest .
	docker push $(ACR_REGISTRY)/console-admin-deps:latest

.PHONY: docker-admin
docker-admin: ## Build admin frontend image (CI, 从镜像仓库拉 deps)
	DOCKER_BUILDKIT=1 docker build -f apps/admin/Dockerfile -t unionagents/console-admin:latest .

.PHONY: docker-enduser
docker-enduser: ## Build enduser frontend image (CI amd64，容器内构建，无需 deps 镜像)
	DOCKER_BUILDKIT=1 docker build -f apps/enduser/Dockerfile -t unionagents/enduser-portal:latest .

.PHONY: docker-landing
docker-landing: ## Build landing frontend image (CI amd64，容器内构建，无需 deps 镜像)
	DOCKER_BUILDKIT=1 docker build -f apps/landing/Dockerfile -t unionagents/console-landing:latest .

.PHONY: docker-admin-local
docker-admin-local: ## Build admin image locally (Mac arm64, 网络安装)
	DOCKER_BUILDKIT=1 docker build -f apps/admin/Dockerfile.local \
		-t unionagents/console-admin:latest .

.PHONY: docker-enduser-local
docker-enduser-local: ## Build enduser image locally (Mac arm64, 容器内构建)
	DOCKER_BUILDKIT=1 docker build -f apps/enduser/Dockerfile \
		-t unionagents/enduser-portal:latest .

.PHONY: docker-landing-local
docker-landing-local: ## Build landing image locally (Mac arm64, 容器内构建)
	DOCKER_BUILDKIT=1 docker build -f apps/landing/Dockerfile \
		-t unionagents/console-landing:latest .

.PHONY: docker-all
docker-all: docker-manager docker-skill-engine docker-gateway docker-hermes docker-hub docker-admin docker-enduser docker-landing docker-skill-secret-sidecar ## Build all images（不含 asr-sidecar；回退本地 whisper 用 make docker-asr）

# ============================================================
# Android client (APK — not deployed to k8s; distributed via release artifacts)
# ============================================================
.PHONY: android-bootstrap
android-bootstrap: ## One-time: bootstrap Gradle wrapper + first build (requires JDK 17 + Android SDK)
	cd apps/android && ./gradlew --no-daemon :app:assembleDebug

.PHONY: android-debug
android-debug: ## Build debug APK (debuggable, dev base URLs at 10.0.2.2)
	cd apps/android && ./gradlew :app:assembleDebug
	@echo "APK: apps/android/app/build/outputs/apk/debug/app-debug.apk"

.PHONY: android-release
android-release: ## Build release APK (needs apps/android/keystore.properties — see .example)
	cd apps/android && ./gradlew :app:assembleRelease
	@echo "APK: apps/android/app/build/outputs/apk/release/app-release.apk"

.PHONY: android-test
android-test: ## Run JVM unit tests (SSE parser, repositories with MockWebServer)
	cd apps/android && ./gradlew :app:testDebugUnitTest

.PHONY: android-lint
android-lint: ## Run Android Lint
	cd apps/android && ./gradlew :app:lint
