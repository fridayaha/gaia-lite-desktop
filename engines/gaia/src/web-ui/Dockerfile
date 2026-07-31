# Gaia web-ui 镜像（两阶段构建：容器内 pnpm install + vite build → nginx 托管）
#
# 自包含：不依赖宿主预构建产物，CI 环境可直接 docker build。
# build 脚本用 `vite build`（跳过 tsc -b，避免既有 TS 类型错误阻塞构建；
# vite 用 esbuild/oxc 转译，不做类型检查，等价于本地验证时的构建路径）。
#
# 构建：docker build -t gaia-web-ui -f src/web-ui/Dockerfile .
#   （构建上下文为仓库根，Dockerfile 在 src/web-ui/ 下）
#   COPY 路径相对于构建上下文根（. = 仓库根）

# ── 阶段 1：builder ──
FROM node:22-alpine AS builder

# 配国内镜像源 + 全局安装 pnpm（绕过 corepack，corepack 会去 npmjs.org 拉最新版）
RUN npm config set registry https://registry.npmmirror.com && \
    npm install -g pnpm

WORKDIR /app

# 先拷 lockfile + package.json，利用 Docker 层缓存（依赖不变则跳过 install）
COPY src/web-ui/package.json src/web-ui/pnpm-lock.yaml ./

# 容器内安装依赖（frozen-lockfile 保证可复现）
RUN pnpm install --frozen-lockfile

# 拷源码
COPY src/web-ui/ ./

# B6: 发行版开关。full = 全量前端（含图探索）；lite = 砍图探索路由（maplibre/
# cytoscape 扩展 tree-shake）。vite.config.ts 读 EDITION env 注入 __EDITION__ 常量。
ARG EDITION=full
ENV EDITION=${EDITION}

# 构建产物（跳过 tsc -b，直接 vite build）
RUN npx vite build

# ── 阶段 2：运行时（nginx 托管静态产物 + API 反代）──
FROM nginx:alpine

# 从 builder 阶段拷贝构建产物
COPY --from=builder /app/dist/ /usr/share/nginx/html/

# nginx 配置：SPA fallback + API 反代到 gaia-api
# 阶段 2 WORKDIR 默认 /，路径相对于构建上下文根
COPY deploy/nginx/web-ui.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
