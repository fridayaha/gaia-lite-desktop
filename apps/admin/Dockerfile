# syntax=docker/dockerfile:1
# ============================================================
# CI 构建（阿里云 ECS/ACK amd64 → ACR 内网拉取 deps 镜像）
# ============================================================
# deps 镜像包含完整的 node_modules + lockfile + package.json，
# 作为 build-stage 基础，避免 COPY node_modules 破坏 pnpm 硬链接。
# deps 中包含 amd64 native 二进制（esbuild 等），
# 不能在 arm64 Mac 上使用。本地开发请用 Dockerfile.local。
# ============================================================

ARG DEPS_IMAGE=crpi-x1lxt7dogr41s0b4.cn-hangzhou.personal.cr.aliyuncs.com/unionagents/console-admin-deps:latest

FROM ${DEPS_IMAGE} AS build-stage

WORKDIR /app
ENV CI=true
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH

# deps 镜像已包含：
#   /app/pnpm-lock.yaml
#   /app/apps/admin/package.json
#   /app/apps/admin/node_modules （pnpm 硬链接结构完整）
#   /pnpm/bin/pnpm

# ------------------------------------------------------------
# 源码 + 构建
# ------------------------------------------------------------
# @ua/chat 共享对话区组件包（alias 直指源码，需在构建上下文内）
COPY packages/ua-chat/ packages/ua-chat/
COPY apps/admin/ apps/admin/
COPY apps/docs/ apps/docs/
ENV NODE_OPTIONS=--max-old-space-size=4096
# 用最新拷贝的 pnpm-lock.yaml 同步 deps（deps 镜像滞后时补装新增依赖，如 0.8.104 的 @zxcvbn-ts/language-en + language-common）
RUN cd apps/admin && pnpm install --frozen-lockfile
# @ua/chat 是 alias 到源码的共享包（非 pnpm workspace 成员，pnpm-workspace.yaml 未列 packages），
# 其源码里的裸导入（marked/dompurify/katex/prismjs/mermaid/codemirror 等）需从 node_modules 解析。
# 本地开发时 packages/ua-chat/node_modules 已装好；CI deps 镜像只装了 admin 的依赖，
# 故建一个指向 admin node_modules 的软链接——否则 rolldown 会把这些裸导入当作 external 留在产物里，
# 浏览器运行时报 "Failed to resolve module specifier 'marked'"。这些依赖 admin package.json 都已声明。
RUN rm -rf /app/packages/ua-chat/node_modules && ln -s /app/apps/admin/node_modules /app/packages/ua-chat/node_modules
RUN cd apps/admin && pnpm build
# 文档站点（VitePress）：现装 deps + build，~30s。输出到 apps/docs/.vitepress/dist
RUN cd apps/docs && pnpm install --frozen-lockfile && pnpm build
# 生成 version.json（version-rocket 每 5min 轮询 /version.json 检测新版本）。
# 与内置 localPackageVersion(__APP_INFO__.pkg.version) 同源（均取自 package.json），
# 避免缺文件时 nginx SPA fallback 返回 index.html → res.json() 解析 <!doctype 报 SyntaxError。
RUN cd apps/admin && node -e "require('fs').writeFileSync('dist/version.json', JSON.stringify({version: require('./package.json').version}))"

# ------------------------------------------------------------
# 生产镜像
# ------------------------------------------------------------
FROM docker.m.daocloud.io/library/nginx:stable-alpine AS production-stage

COPY --from=build-stage /app/apps/admin/dist /usr/share/nginx/html
COPY --from=build-stage /app/apps/docs/.vitepress/dist /usr/share/nginx/html/docs
COPY apps/admin/nginx.conf /etc/nginx/conf.d/default.conf
# vite-plugin-compression 输出的 .gz 文件权限是 640、目录 750，nginx 用户读不了 → 显式放宽
RUN chmod -R a+rX /usr/share/nginx/html
EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
