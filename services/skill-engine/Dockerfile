# ============================================================
# UnionAgents Skill Engine — 技能开发/调试引擎服务
# Node.js 22 + pi coding-agent SDK。多阶段：builder 编译 TS → runtime 仅装 prod 依赖。
# ============================================================

# 国内构建走 daocloud 镜像加速（与 manager Dockerfile 一致），可由 build arg 覆盖
ARG NODE_IMAGE=docker.m.daocloud.io/library/node:22-slim

# ── builder：编译 TypeScript → dist ────────────────────────────
FROM ${NODE_IMAGE} AS builder
WORKDIR /build

# 先 COPY 依赖清单单独一层：源码变动不使依赖层失效
COPY services/skill-engine/package.json services/skill-engine/package-lock.json ./
RUN npm ci

COPY services/skill-engine/tsconfig.json ./
COPY services/skill-engine/src ./src
RUN npm run build

# ── runtime：仅 prod 依赖 + 编译产物 ───────────────────────────
FROM ${NODE_IMAGE} AS runtime

# ripgrep / fd：引擎 grep/find 工具依赖。debian 上 fd 包名是 fd-find，二进制 fdfind，软链成 fd。
# python3 + PyYAML + pip：预制 skill-creator 的 scripts/*.py 依赖（仅用 stdlib + yaml），
# 同时为用户开发的 Python 技能提供出厂运行环境。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ripgrep fd-find \
        python3 python3-yaml python3-pip \
    && ln -sf "$(which fdfind)" /usr/local/bin/fd \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY services/skill-engine/package.json services/skill-engine/package-lock.json ./
RUN npm ci --omit=dev

# 编译产物
COPY --from=builder /build/dist ./dist
# 迁移 SQL：migrate.ts 用相对路径 src/db/migrations 定位
COPY services/skill-engine/src/db/migrations ./src/db/migrations
# 预制技能：工作区创建时 seed 到 .pi/skills/（dev 默认开发技能 skill-creator）
COPY services/skill-engine/preinstalled-skills ./preinstalled-skills

ENV NODE_ENV=production \
    WORKSPACE_BASE_DIR=/data/workspaces

EXPOSE 8004
CMD ["node", "dist/index.js"]
