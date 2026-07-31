# Better Auth Server for Gaia (ADR-016/017 Phase 5)
#
# 自包含：容器内 npm ci 安装依赖，不依赖宿主 node_modules。
# Runs a Hono server on port 3000, shares Gaia's PostgreSQL (independent better_auth schema).
#
# 构建：docker build -t gaia-better-auth -f auth-server/Dockerfile .
#   （构建上下文为仓库根）

FROM node:22-slim

# 配国内镜像源加速（构建环境无外网时仍可装依赖）
RUN npm config set registry https://registry.npmmirror.com

WORKDIR /app

# 先拷 lockfile + package.json，利用 Docker 层缓存
COPY auth-server/package.json auth-server/package-lock.json ./

# 容器内安装依赖（npm ci 按 lockfile 精确安装，保证可复现）
# --omit=dev 不装 devDependencies；随后清理缓存 + 非运行时文件
RUN npm ci --omit=dev && \
    npm cache clean --force && \
    rm -rf /root/.npm && \
    # 清理生产不需要的文件（省 ~26M）
    find node_modules -name "*.map" -delete 2>/dev/null; \
    find node_modules -type d \( -name test -o -name tests -o -name __tests__ \) -exec rm -rf {} + 2>/dev/null; \
    # typescript 的 lib 是 tsx 运行时必需，保留；只删其测试文件
    find node_modules/typescript -type d -name test* -exec rm -rf {} + 2>/dev/null; \
    true

# 拷源码
COPY auth-server/ ./

ENV NODE_ENV=production
ENV PORT=3000

EXPOSE 3000

# tsx runs TypeScript directly (no separate build step needed for this size).
CMD ["npx", "tsx", "auth.ts"]
