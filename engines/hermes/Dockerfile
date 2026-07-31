# V2 engine 镜像：基于 python:3.11-slim + pip install hermes-agent（对齐 main 分支）。
# 不再用第三方预构建镜像 nousresearch/hermes-agent（3.79GB，打包 70+ 预置 skills + s6/Node/Playwright）。
# pip 安装只含 hermes 框架 + tools 工具集，不含 70+ 预置 skills（平台技能由 manager fan-out 到 profile 目录）。

# ── build stage：装 agent-browser CLI（hermes 0.17 的 browser_tool 硬依赖它驱动浏览器） ──
# agent-browser npm 包捆绑 7 个平台二进制(~85MB)，仅保留 linux-x64(~13MB)。
# 用 node:20-slim 多阶段，避免 apt 装 nodejs/npm 拉入 ~300MB 的 node-* deb 链(webpack/babel/jest...)。
# agent-browser 无运行时 npm 依赖，final 镜像只需 node 二进制 + agent-browser 模块目录，不带 npm。
FROM docker.m.daocloud.io/library/node:20-bookworm-slim AS ab-build
RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g agent-browser \
    && rm -rf /usr/local/lib/node_modules/agent-browser/bin/agent-browser-linux-musl-x64 \
              /usr/local/lib/node_modules/agent-browser/bin/agent-browser-win32-x64.exe \
              /usr/local/lib/node_modules/agent-browser/bin/agent-browser-darwin-x64 \
              /usr/local/lib/node_modules/agent-browser/bin/agent-browser-darwin-arm64 \
              /usr/local/lib/node_modules/agent-browser/bin/agent-browser-linux-arm64 \
              /usr/local/lib/node_modules/agent-browser/bin/agent-browser-linux-musl-arm64 \
    && npm cache clean --force

FROM docker.m.daocloud.io/library/python:3.11-slim

WORKDIR /app

# aliyun apt 源（deb822，国内构建需换源否则 apt-get 卡死，同 services/manager/Dockerfile）
# + nginx（V2 多 Profile 路由需要）+ passwd（per-profile UID 隔离的 useradd 需要，debian-slim 默认不含）
RUN sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g' /etc/apt/sources.list 2>/dev/null; true
RUN apt-get update && apt-get install -y --no-install-recommends nginx passwd fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /var/log/nginx/* /var/cache/nginx/*

# pip 装 hermes-agent（pin 0.19.0 = 2026.7.20 Quicksilver Release，避免浮动 tag 漂移）
# + 预装 messaging lazy deps（discord.py[voice] + brotlicffi，同 main，避免新 Pod 启动 lazy-install 阻塞）
# HERMES_DISABLE_LAZY_INSTALLS=1：禁止运行时 lazy-install（Pod 网络受限 + root sealed 会卡）
ENV HERMES_DISABLE_LAZY_INSTALLS=1
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
    hermes-agent==0.19.0 'discord.py[voice]==2.7.1' brotlicffi==1.2.0.1 langfuse==4.14.0 matplotlib
# hermes_cli 权限：0.19.0 已修 0.17.0 的 750/640 root:root 异常（现为默认 755/644 o+rX）。
# 保留 chmod o+rX 作 belt-suspenders：per-profile UID 降权后（uid 20000+，other）需读全部 pip 包，
# 且 0.19 仍会 secure_parent_dir chmod HERMES_HOME 0700（entrypoint-v2.sh 已 chmod 755 兜底恢复）。
RUN chmod -R o+rX /usr/local/lib/python3.11/site-packages

# ── browser 工具集：从 ab-build 拷 node 二进制 + agent-browser(pruned)，不带 npm ──
# per-profile UID 降权后需 o+rX 才能执行；agent-browser.js 检测 linux/x64 spawn 原生二进制。
COPY --from=ab-build /usr/local/bin/node /usr/local/bin/node
COPY --from=ab-build /usr/local/lib/node_modules/agent-browser /usr/local/lib/node_modules/agent-browser
RUN ln -sf /usr/local/lib/node_modules/agent-browser/bin/agent-browser.js /usr/local/bin/agent-browser \
    && chmod -R o+rX /usr/local/lib/node_modules/agent-browser /usr/local/bin/node

# ── 复制 V2 入口（多 Profile + nginx 路由）+ per-profile UID 隔离脚本 ──
# sed 兜底：即使源文件带 CRLF（Windows 提交未走 .gitattributes）也能修
COPY engines/hermes/entrypoint-v2.sh /entrypoint-v2.sh
COPY engines/hermes/profile_isolation.py /opt/scripts/profile_isolation.py
COPY engines/hermes/port_map.py /opt/scripts/port_map.py
RUN sed -i 's/\r$//' /entrypoint-v2.sh /opt/scripts/profile_isolation.py /opt/scripts/port_map.py \
    && chmod +x /entrypoint-v2.sh /opt/scripts/profile_isolation.py /opt/scripts/port_map.py

ENTRYPOINT ["/entrypoint-v2.sh"]

EXPOSE 8642
