#!/usr/bin/env python3
"""PyInstaller entry point for the Gaia lite backend (桌面单机版后端, C1).

云版（full）后端用 `.venv/bin/uvicorn ontology.main:app` 直接起进程，但桌面版
需要打包成单可执行（PyInstaller bundle，作 Tauri sidecar，见 C2）。FastAPI 的
`app` 是一个 ASGI 对象而非可调用入口，故这里显式包一层 uvicorn.run。

锁 EDITION=lite 的时机很关键：必须在 `import ontology.config.settings` **之前**
把 `EDITION` 写进 os.environ，否则 `Settings()` 实例化时读到默认 "full"，会去
装配 asyncpg/Trino/Iceberg 等 lite 不装的 Layer（import 即炸）。
`os.environ.setdefault` 只在 env 未设时填——便于开发时 `EDITION=full python
scripts/gaia_lite_backend.py` 跑云版回归（虽然云版一般直接 uvicorn）。

Tauri sidecar（C2）启动本进程时会注入 PORT env（动态端口，避免与宿主冲突），
默认 8000 仅供本地裸跑调试。host 锁 127.0.0.1——桌面版不对外暴露。
"""

from __future__ import annotations

import os

# 必须在任何 ontology.* import 之前锁定 edition（settings 在 import 期实例化）。
os.environ.setdefault("EDITION", "lite")

import uvicorn  # noqa: E402

# 顶层 import ontology.main 让 PyInstaller 跟踪 main.py 的 routes import 链
# 把整个应用打进 bundle。若用 uvicorn.run("ontology.main:app") 字符串形式，
# PyInstaller 静态分析看不到字符串里的 import，ontology.main + routes 不会进 PYZ。
from ontology.main import app  # noqa: E402, F401


def _watch_stdin_eof() -> None:
    """监听 stdin EOF：Tauri Rust 壳退出（任何路径，含 SIGTERM/SIGKILL）时
    sidecar stdin 的写端关闭，本线程读到 EOF 即 os._exit。

    孤儿 sidecar 防护：kill_on_drop / RunEvent::Exit 在外部信号场景不可靠
    （Drop 不跑 / RunEvent 不触发），stdin EOF 是最可靠的兜底（Tauri 官方
    sidecar 推荐做法）。裸跑调试时 stdin 是终端，不 EOF，不影响。
    """
    import sys
    import threading

    def _reader() -> None:
        try:
            while sys.stdin.read(1) != "":
                pass
        except Exception:
            pass
        # EOF 或异常——Rust 壳已退出，立即终止本进程（不走 atexit/finally，避免
        # uvicorn graceful shutdown 卡住）。
        import os as _os

        _os._exit(0)

    t = threading.Thread(target=_reader, name="stdin-eof-watcher", daemon=True)
    t.start()


def main() -> None:
    _watch_stdin_eof()
    port = int(os.environ.get("PORT", "8000"))
    # lite 桌面版只绑 127.0.0.1（Tauri webview 同机反代，不对外暴露）。
    # 直接传 app 对象（非字符串）：打包后字符串 import 会因 sys.modules 路径
    # 变化失败，对象引用稳定。reload=False：PyInstaller bundle 无源码可 reload。
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level=os.environ.get("APP_LOG_LEVEL", "info").lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
