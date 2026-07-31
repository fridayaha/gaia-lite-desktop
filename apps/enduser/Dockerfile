# syntax=docker/dockerfile:1
# ============================================================
# enduser 门户 — 单一多阶段构建（CI amd64 + 本地 Mac arm64 通用）
# ============================================================
# esbuild postinstall 按目标平台拉 @esbuild/linux-{x64|arm64}，容器内构建双架构均可，
# 无需 admin 那种 deps 镜像预构建（enduser 仅 ~66 个包，install ~1.4s，缓存零收益）。
# allowBuilds（pnpm 10+/11 语法）在 apps/enduser/pnpm-workspace.yaml 放行 esbuild/vue-demi
# 构建脚本，解决 pnpm 11 的 ERR_PNPM_IGNORED_BUILDS 硬错（无需交互式 approve-builds）。
# ============================================================
FROM docker.m.daocloud.io/library/node:22-alpine AS build-stage

WORKDIR /app
ENV CI=true
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
# esbuild postinstall 走 node fetch（不读 pnpm --registry 参数），必须显式设 npm_config_registry
# 指向可达镜像，否则容器内 DNS 找不到 registry.npmjs.org 导致 @esbuild/linux-x64 下载失败。
ENV npm_config_registry=https://registry.npmmirror.com

RUN corepack enable && corepack prepare pnpm@11.5.1 --activate

# 依赖层（拷真实 lockfile，确保可复现）
COPY apps/enduser/pnpm-lock.yaml apps/enduser/pnpm-lock.yaml
COPY apps/enduser/package.json apps/enduser/package.json
COPY apps/enduser/pnpm-workspace.yaml apps/enduser/pnpm-workspace.yaml
RUN cd apps/enduser && pnpm install --no-frozen-lockfile --registry https://registry.npmmirror.com

# 源码层 + 构建（.dockerignore 已排除 node_modules/dist，不污染容器内产物）
# @ua/chat 共享对话区组件包（alias 直指源码，需在构建上下文内）
COPY packages/ua-chat/ packages/ua-chat/
COPY apps/enduser/ apps/enduser/
ENV PNPM_CONFIG_VERIFY_DEPS_BEFORE_RUN=false
RUN cd apps/enduser && pnpm build

# ------------------------------------------------------------
# 生产镜像
# ------------------------------------------------------------
FROM docker.m.daocloud.io/library/nginx:stable-alpine

COPY --from=build-stage /app/apps/enduser/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build-stage /app/apps/enduser/dist/ /usr/share/nginx/html/
# macOS rsync 默认把扩展属性拆成 `._*` 文件污染源码；COPY 进镜像后 nginx 不会去读它们，
# 但留着脏。同时 node:alpine 默认 umask 027 → 文件 640、目录 750，nginx worker 读不了 → 403。
# a+rX 把所有文件设 644 / 目录 755；find -delete 清掉 `._*` 残留。
RUN find /usr/share/nginx/html -name '._*' -delete && \
    chmod -R a+rX /usr/share/nginx/html/
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
