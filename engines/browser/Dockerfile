# browser-v2 镜像：kasmweb/chrome + CDP 感知代理（python3）
#
# kasmweb/chrome:1.18.0 提供 KasmVNC(6901) + chrome（APP_ARGS 注 CDP 9223 loopback）。
# 加 cdp_proxy.py：cdp-proxy sidecar 跑它，把 chrome 强制绑 127.0.0.1 的 DevTools 暴露到
# Pod 网络 0.0.0.0:9222，并重写 Host 头 + webSocketDebuggerUrl（chrome loopback 两限制，
# 见 cdp_proxy.py 注释）。裸 TCP relay（socat/python）不重写头，chrome Host 检查 + loopback
# WS URL 致跨 Pod 不可用，故需 CDP 感知代理。kasm 镜像自带 python3，无需额外装包。
FROM kasmweb/chrome:1.18.0

USER root
COPY engines/browser/cdp_proxy.py /opt/cdp_proxy.py
RUN chmod 644 /opt/cdp_proxy.py
USER 1000
