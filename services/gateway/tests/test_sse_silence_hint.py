"""SSE 静默看门狗（_aiter_sse_with_silence_hints）单元测试。

场景：引擎生成长工具调用参数（几十 KB write_file JSON）期间全程无 SSE 事件，
端上既无流式增量又无工具卡。gateway 转发时超过 sse_silence_hint_seconds 无字节
则注入 gateway.silence 提示帧（仅完整帧边界后注入，不截断上游帧）。
"""

import asyncio
import json

import pytest
from app.proxy import (
    _aiter_sse_with_silence_hints,
    _is_silence_hint,
    _is_sse_comment_only,
    _silence_hint_frame,
)


class _FakeResp:
    """按脚本 (delay, chunk) 逐条产出的假 httpx.Response（None 表示流结束）。"""

    def __init__(self, script: list[tuple[float, bytes | None]]):
        self._script = script

    def aiter_bytes(self):
        async def _gen():
            for delay, chunk in self._script:
                await asyncio.sleep(delay)
                if chunk is not None:
                    yield chunk

        return _gen()


async def _collect(resp, hint_seconds: float) -> list[bytes]:
    return [c async for c in _aiter_sse_with_silence_hints(resp, hint_seconds)]


def _hints(chunks: list[bytes]) -> list[bytes]:
    return [c for c in chunks if _is_silence_hint(c)]


class TestSilenceHintFrame:

    def test_frame_format(self):
        frame = _silence_hint_frame(16).decode()
        assert frame.startswith("event: gateway.silence\n")
        assert frame.endswith("\n\n")
        data_line = [ln for ln in frame.split("\n") if ln.startswith("data:")][0]
        payload = json.loads(data_line.removeprefix("data:").strip())
        assert payload == {"event": "gateway.silence", "elapsed": 16}

    def test_is_silence_hint(self):
        assert _is_silence_hint(_silence_hint_frame(8))
        assert _is_silence_hint(_silence_hint_frame(8).decode())
        assert not _is_silence_hint(b'data: {"event":"message.delta"}\n\n')
        assert not _is_silence_hint('data: {"choices":[]}\n\n')
        assert not _is_silence_hint(b"")


@pytest.mark.asyncio
class TestWatchdog:

    async def test_hint_injected_during_silence(self):
        """静默超阈 → 每个阈值周期注入一帧提示，随后真实 chunk 原样到达。"""
        resp = _FakeResp([(0.18, b"event: run.completed\ndata: {}\n\n")])
        chunks = await _collect(resp, 0.05)
        hints = _hints(chunks)
        # 0.05/0.10/0.15 三个超时各一帧；0.18s 真实 chunk 到达后不再注入
        assert len(hints) == 3
        assert chunks[-1] == b"event: run.completed\ndata: {}\n\n"

    async def test_no_hint_when_bytes_flow(self):
        """字节持续到达（间隔 < 阈值）→ 零提示帧，输出与输入逐字节一致。"""
        script = [(0.02, f"data: chunk-{i}\n\n".encode()) for i in range(5)]
        chunks = await _collect(_FakeResp(script), 0.05)
        assert _hints(chunks) == []
        assert chunks == [c for _, c in script]

    async def test_no_injection_mid_frame(self):
        """上游帧被字节切片拆断时暂停 → 不注入（避免截断帧）；帧收尾后再静默 → 注入。"""
        resp = _FakeResp([
            (0.01, b'data: {"partial":'),   # 半个帧
            (0.12, b'"x"}\n\n'),             # 期间静默 0.12s：at_boundary=False，不得注入
            (0.12, b"data: next\n\n"),       # 完整帧；之后的静默应触发注入
            (0.12, None),
        ])
        chunks = await _collect(resp, 0.05)
        hints = _hints(chunks)
        real = [c for c in chunks if not _is_silence_hint(c)]
        assert real == [b'data: {"partial":', b'"x"}\n\n', b"data: next\n\n"]
        # 第一段静默（mid-frame）无提示；第二段静默（boundary）有提示
        assert len(hints) >= 1
        # 提示帧不得出现在半帧与后半帧之间
        idx_partial = chunks.index(b'data: {"partial":')
        idx_rest = chunks.index(b'"x"}\n\n')
        assert not any(_is_silence_hint(c) for c in chunks[idx_partial:idx_rest + 1])

    async def test_no_hint_after_stream_end(self):
        """流正常结束后不再注入提示帧。"""
        resp = _FakeResp([(0.01, b"data: done\n\n"), (0.01, None)])
        chunks = await _collect(resp, 0.05)
        assert chunks == [b"data: done\n\n"]

    async def test_crlf_frame_boundary(self):
        """\\r\\n\\r\\n 结尾同样视为帧边界。"""
        resp = _FakeResp([(0.01, b"data: x\r\n\r\n"), (0.12, None)])
        chunks = await _collect(resp, 0.05)
        assert len(_hints(chunks)) >= 1

    async def test_pending_read_survives_timeouts(self):
        """多次超时后到达的 chunk 内容完整（读协程未被取消/损坏）。"""
        payload = b"data: " + b"x" * 5000 + b"\n\n"
        resp = _FakeResp([(0.13, payload)])
        chunks = await _collect(resp, 0.05)
        assert chunks[-1] == payload


class TestSseCommentOnly:

    def test_comment_frame(self):
        assert _is_sse_comment_only(b": keepalive\n\n")
        assert _is_sse_comment_only(b": keepalive\r\n\r\n")
        assert _is_sse_comment_only(": keepalive\n\n")
        assert _is_sse_comment_only(b": a\n: b\n\n")

    def test_data_frame_is_not_comment(self):
        assert not _is_sse_comment_only(b'data: {"x":1}\n\n')
        assert not _is_sse_comment_only(b"event: message.delta\ndata: {}\n\n")

    def test_mixed_comment_and_data_is_not_comment(self):
        # 注释帧与内容帧同 chunk（TCP 合包）→ 视为内容，应重置计时
        assert not _is_sse_comment_only(b': keepalive\n\ndata: {"x":1}\n\n')
        assert not _is_sse_comment_only(b'data: {"x":1}\n\n: keepalive\n\n')

    def test_empty_is_not_comment(self):
        assert not _is_sse_comment_only(b"")
        assert not _is_sse_comment_only(b"\n\n")


@pytest.fixture
def captured_elapsed(monkeypatch):
    """捕获看门狗注入提示帧时的 float elapsed。

    帧内 elapsed 经 int() 截断（_silence_hint_frame），亚秒级测试阈值下恒为 0，
    无法区分清零与否，故在模块全局函数上挂 spy 取原始 float 值。
    """
    import app.proxy as proxy_mod

    captured: list[float] = []
    orig = proxy_mod._silence_hint_frame

    def _spy(elapsed: float) -> bytes:
        captured.append(elapsed)
        return orig(elapsed)

    monkeypatch.setattr(proxy_mod, "_silence_hint_frame", _spy)
    return captured


@pytest.mark.asyncio
class TestWatchdogKeepalive:

    async def test_keepalive_forwarded_but_does_not_reset_elapsed(self, captured_elapsed):
        """`: keepalive` 注释帧照常转发给端上，但不重置静默计时——
        引擎 30s 保活是传输层信号，提示帧 elapsed 应持续累加（端上实测
        修复前为 8→16→24→8 被隐形清零）。"""
        resp = _FakeResp([
            (0.01, b"data: first\n\n"),
            (0.17, b": keepalive\n\n"),   # 期间约 3 个超时
            (0.17, b"data: end\n\n"),     # keepalive 后再约 3 个超时
            (0.01, None),
        ])
        chunks = await _collect(resp, 0.05)
        # 未清零：elapsed 严格递增（每次超时固定 +hint_seconds），不出现回落
        assert len(captured_elapsed) >= 4
        assert all(
            b > a for a, b in zip(captured_elapsed, captured_elapsed[1:])
        )
        # keepalive 帧本身原样转发
        assert b": keepalive\n\n" in chunks

    async def test_real_frame_resets_elapsed(self, captured_elapsed):
        """真实内容帧到达 → 计时清零，后续提示重新从最小值累加。"""
        resp = _FakeResp([
            (0.01, b"data: first\n\n"),
            (0.13, b"data: second\n\n"),   # 2 个超时后内容帧 → 清零
            (0.13, None),                   # 再 2 个超时
        ])
        await _collect(resp, 0.05)
        # 前段提示累加后，内容帧引发一次回落（清零重新开始）
        assert len(captured_elapsed) >= 3
        assert captured_elapsed[0] < captured_elapsed[1]
        assert any(
            b < a for a, b in zip(captured_elapsed, captured_elapsed[1:])
        )
