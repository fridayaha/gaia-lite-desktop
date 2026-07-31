# Hermes Agent Multi-Profile 自定义镜像
# 基于官方镜像，添加 Profile Gateway Supervisor (s6 服务)
FROM nousresearch/hermes-agent:latest

# === CRITICAL FIX: Pre-set hermes UID/GID to 1000 ===
# 基础镜像 hermes UID=10000，而 K8s 部署目标 UID=1000。
# 如果不在这里预设，stage2-hook.sh 会在容器启动时执行 chown -R，
# 在 k3s overlayfs 上卡在 D 状态（不可中断 I/O），gateway 永远无法启动。
# 预设后 stage2 检测到 owner 匹配，直接跳过 chown。
RUN usermod -u 1000 hermes && groupmod -g 1000 hermes && \
    chown -R 1000:1000 \
        /opt/hermes/.venv \
        /opt/hermes/ui-tui \
        /opt/hermes/gateway \
        /opt/hermes/node_modules \
        /opt/hermes/docker \
        /opt/hermes/scripts \
        /opt/hermes/skills \
        /opt/hermes/tools

# 共享资源对所有 Profile 用户可读可执行（Profile 用户 UID 1100-1107）
RUN chmod -R o+rX /opt/hermes/.venv && \
    chmod -R o+rX /opt/hermes/ui-tui && \
    chmod -R o+rX /opt/hermes/gateway && \
    chmod -R o+rX /opt/hermes/node_modules && \
    chmod -R o+rX /opt/hermes/docker && \
    chmod -R o+rX /opt/hermes/scripts && \
    chmod -R o+rX /opt/hermes/skills && \
    chmod -R o+rX /opt/hermes/tools && \
    chmod -R o+rX /opt/hermes/bin 2>/dev/null || true

# 添加 s6 服务定义: gateway-profiles
COPY s6/gateway-profiles/ /etc/s6-overlay/s6-rc.d/gateway-profiles/

# 注册到 s6 用户 bundle
RUN mkdir -p /etc/s6-overlay/s6-rc.d/user/contents.d && \
    touch /etc/s6-overlay/s6-rc.d/user/contents.d/gateway-profiles

# === 禁用 Hermes 内置 Profile 管理 ===
# Hermes 自带的 02-reconcile-profiles 会为每个 Profile 创建 s6 服务并以 hermes 用户启动，
# 与我们的 profile-supervisor.sh (多 UID 隔离) 冲突。禁用它，由 supervisor 全权管理。
RUN rm -f /opt/hermes/docker/cont-init.d/02-reconcile-profiles
# stage2-hook.sh 中的 chown -R hermes:hermes profiles/ 会覆盖我们的 per-profile UID 权限，
# 将其改为 no-op (只 chown profiles 目录本身，不递归)。
RUN sed -i 's|chown -R hermes:hermes "$HERMES_HOME/profiles"|chown hermes:hermes "$HERMES_HOME/profiles" 2>/dev/null; # disabled -R for multi-UID isolation|' \
    /opt/hermes/docker/stage2-hook.sh

# 添加 Profile Supervisor 脚本
COPY scripts/profile-supervisor.sh /opt/scripts/profile-supervisor.sh
RUN chmod +x /opt/scripts/profile-supervisor.sh

# 创建 Profile 数据目录并预先设置权限
# 父目录 root:hermes 755 — 所有用户可遍历但不可写，子目录由 supervisor 按 Profile UID 设置
RUN mkdir -p /opt/data/profiles && \
    chown root:hermes /opt/data && \
    chmod 755 /opt/data && \
    chown root:hermes /opt/data/profiles && \
    chmod 755 /opt/data/profiles

# 不覆盖 ENTRYPOINT —— 使用基础镜像默认的:
#   ENTRYPOINT ["/init", "/opt/hermes/docker/main-wrapper.sh"]
# 设置 CMD 为 gateway run，通过 main-wrapper.sh 路由:
#   main-wrapper 看到 $1="gateway" (非可执行文件) → hermes gateway run
CMD ["gateway", "run"]
