# ============================================================
# landing 产品主页 — 单一多阶段构建（仿 apps/enduser/Dockerfile）
# VitePress 单依赖（vitepress），现场 install 即可，无需 deps 预构建镜像。
# allowBuilds.esbuild 在 pnpm-workspace.yaml 放行 esbuild postinstall。
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
COPY apps/landing/pnpm-lock.yaml apps/landing/pnpm-lock.yaml
COPY apps/landing/package.json apps/landing/package.json
COPY apps/landing/pnpm-workspace.yaml apps/landing/pnpm-workspace.yaml
RUN cd apps/landing && pnpm install --no-frozen-lockfile --registry https://registry.npmmirror.com

# docs 依赖层（资料中心 VitePress，打进 landing 镜像让"查看文档"按钮可用）
COPY apps/docs/pnpm-lock.yaml apps/docs/pnpm-lock.yaml
COPY apps/docs/package.json apps/docs/package.json
COPY apps/docs/pnpm-workspace.yaml apps/docs/pnpm-workspace.yaml
RUN cd apps/docs && pnpm install --no-frozen-lockfile --registry https://registry.npmmirror.com

# 源码层 + 构建（VitePress 产物输出到 .vitepress/dist/）
COPY apps/landing/ apps/landing/
COPY apps/docs/ apps/docs/
ENV PNPM_CONFIG_VERIFY_DEPS_BEFORE_RUN=false
RUN cd apps/landing && pnpm build

# docs 构建（VitePress 产物输出到 apps/docs/.vitepress/dist/）
RUN cd apps/docs && pnpm build

# ------------------------------------------------------------
# 生产镜像
# ------------------------------------------------------------
FROM docker.m.daocloud.io/library/nginx:stable-alpine

COPY --from=build-stage /app/apps/landing/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build-stage /app/apps/landing/.vitepress/dist/ /usr/share/nginx/html/
COPY --from=build-stage /app/apps/docs/.vitepress/dist/ /usr/share/nginx/html/docs
# VitePress 产物文件默认 0640，nginx worker（非 root）读不到 → 统一放开读权限
RUN chmod -R a+rX /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
