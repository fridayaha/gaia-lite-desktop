#!/usr/bin/env python3
"""CDP 感知代理：把 chrome 强制绑 127.0.0.1:9223 的 DevTools 暴露到 Pod 网络 0.0.0.0:9222。

chrome DevTools 两个 loopback 限制（P0+云冒烟实测）：
1. Host 头 DNS-rebinding 保护：非 localhost Host → 500。跨 Pod 请求 Host=browser-svc:9222 被拒。
2. /json/version 返回 webSocketDebuggerUrl=ws://127.0.0.1:9223/...，跨 Pod 不可达。

本代理（替代裸 TCP relay）：
- HTTP /json/* 请求：转发到 chrome（Host 重写为 localhost:9223），响应里把 webSocketDebuggerUrl
  的 127.0.0.1:9223 重写成客户端请求的 Host（外部地址），再回客户端。
- WebSocket 升级：转发到 chrome（Host 重写 localhost:9223），之后二进制 raw 透传。
- 其它：raw 透传。

hermes browser_cdp_tool `_resolve_cdp_override` GET {cdp_url}/json/version → 取 webSocketDebuggerUrl
→ 连该 WS。经本代理后 webSocketDebuggerUrl 指向外部地址（browser-svc:9222），hermes WS 连代理→chrome。
"""
import select
import socket
import sys
import threading
import traceback

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 9222
TARGET_HOST = "127.0.0.1"
TARGET_PORT = 9223
CHROME_HOST_HEADER = f"localhost:{TARGET_PORT}"


def _read_http_head(conn: socket.socket) -> bytes:
    """读到 HTTP 请求头结束（\r\n\r\n），返回头部字节（含结束符）。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        try:
            d = conn.recv(4096)
        except OSError:
            return buf
        if not d:
            break
        buf += d
        if len(buf) > 65536:  # 头过大保护
            break
    return buf


def _rewrite_request_host(head: bytes, external_host: str) -> bytes:
    """把请求头里的 Host 改成 localhost:9223（chrome 才接受）。保留其它头。"""
    lines = head.split(b"\r\n")
    out = []
    for ln in lines:
        if ln.lower().startswith(b"host:"):
            out.append(f"Host: {CHROME_HOST_HEADER}".encode())
        else:
            out.append(ln)
    return b"\r\n".join(out)


def _is_websocket_upgrade(head: bytes) -> bool:
    return b"upgrade: websocket" in head.lower()


def _is_json_endpoint(head: bytes) -> bool:
    """GET /json 或 /json/version 等（需重写响应里的 webSocketDebuggerUrl）。"""
    try:
        first = head.split(b"\r\n", 1)[0].decode("latin1")
    except Exception:
        return False
    parts = first.split()
    return len(parts) >= 2 and parts[0].upper() == "GET" and parts[1].startswith("/json")


def _relay(a: socket.socket, b: socket.socket) -> None:
    conns = [a, b]
    try:
        while True:
            r, _, _ = select.select(conns, [], [])
            for s in r:
                d = s.recv(65536)
                if not d:
                    return
                (b if s is a else a).sendall(d)
    except OSError:
        pass


def _handle_json_request(client: socket.socket, req_head: bytes, external_host: str) -> None:
    """转发 /json* 到 chrome，重写响应 webSocketDebuggerUrl 后回客户端。"""
    try:
        upstream = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=5)
    except OSError:
        return
    # 建连后改回阻塞无超时：create_connection 的 5s timeout 会留在 socket 上，后续 sendall/recv
    # 同样受 5s 限制，chrome 接收缓冲瞬时填满（渲染/GC 忙）会触发 TimeoutError 撕断长连接。
    upstream.settimeout(None)
    try:
        upstream.sendall(_rewrite_request_host(req_head, external_host))
        # 读 chrome 响应头
        resp = b""
        while b"\r\n\r\n" not in resp:
            d = upstream.recv(4096)
            if not d:
                break
            resp += d
        head, _, rest = resp.partition(b"\r\n\r\n")
        # Content-Length 决定 body 长度
        clen = 0
        for ln in head.split(b"\r\n"):
            if ln.lower().startswith(b"content-length:"):
                try:
                    clen = int(ln.split(b":", 1)[1].strip())
                except ValueError:
                    clen = 0
        body = rest
        while len(body) < clen:
            d = upstream.recv(4096)
            if not d:
                break
            body += d
        # 重写 webSocketDebuggerUrl: chrome 报 localhost:9223 或 127.0.0.1:9223 -> external_host
        for src in (f"localhost:{TARGET_PORT}", f"127.0.0.1:{TARGET_PORT}"):
            body = body.replace(src.encode(), external_host.encode())
        # 更新 Content-Length
        new_head = []
        for ln in head.split(b"\r\n"):
            if ln.lower().startswith(b"content-length:"):
                new_head.append(f"Content-Length: {len(body)}".encode())
            else:
                new_head.append(ln)
        client.sendall(b"\r\n".join(new_head) + b"\r\n\r\n" + body)
    except OSError:
        pass
    finally:
        try:
            upstream.close()
        except OSError:
            pass


def _handle_ws(client: socket.socket, req_head: bytes) -> None:
    """WS 升级：转发到 chrome（Host 重写），之后 raw 透传。"""
    try:
        upstream = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=5)
    except OSError:
        return
    # 建连后改回阻塞无超时（CDP WS 是长连接，5s timeout 会在背压时误撕断，见 _handle_json_request 注释）
    upstream.settimeout(None)
    try:
        upstream.sendall(_rewrite_request_host(req_head, ""))
        # 先把 chrome 的升级响应 + 任何初始数据转给 client，再双向 raw
        _relay(client, upstream)
    except OSError:
        pass
    finally:
        try:
            upstream.close()
        except OSError:
            pass


def handle(client: socket.socket) -> None:
    try:
        head = _read_http_head(client)
        if not head:
            return
        # 客户端请求的 Host（外部地址），用于重写 webSocketDebuggerUrl
        external_host = CHROME_HOST_HEADER
        for ln in head.split(b"\r\n"):
            if ln.lower().startswith(b"host:"):
                external_host = ln.split(b":", 1)[1].strip().decode("latin1")
                break
        if _is_websocket_upgrade(head):
            _handle_ws(client, head)
        elif _is_json_endpoint(head):
            _handle_json_request(client, head, external_host)
        else:
            # 其它 HTTP（如 /json/list 也走 json 分支；fallback raw 透传）
            try:
                upstream = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=5)
                upstream.settimeout(None)  # 同上：建连后阻塞无超时，避免背压误撕断
                upstream.sendall(_rewrite_request_host(head, external_host))
                _relay(client, upstream)
                upstream.close()
            except OSError:
                pass
    except Exception:
        sys.stderr.write("cdp-proxy handler error:\n" + traceback.format_exc())
        sys.stderr.flush()
    finally:
        try:
            client.close()
        except OSError:
            pass


def main() -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(128)
    print(f"cdp-proxy (CDP-aware) listening {LISTEN_HOST}:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    while True:
        try:
            client, _ = srv.accept()
        except OSError:
            continue
        threading.Thread(target=handle, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
