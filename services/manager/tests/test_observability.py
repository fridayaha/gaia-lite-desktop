"""
Observability API helper 单元测试。

测试 _obs_token_breakdown / _trace_token_breakdown / _trace_observation_count
等纯数据转换函数，不依赖 DB。
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# 让 tests/ 能 import app.* / pkg.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _import_helpers():
    from app.api.observability import (
        _obs_token_breakdown,
        _trace_token_breakdown,
        _trace_observation_count,
        _trace_latency_ms,
        _trace_status,
        _obs_tokens,
        _obs_ttft_ms,
        _trace_latency_breakdown,
    )
    return (
        _obs_token_breakdown,
        _trace_token_breakdown,
        _trace_observation_count,
        _trace_latency_ms,
        _trace_status,
        _obs_tokens,
        _obs_ttft_ms,
        _trace_latency_breakdown,
    )


def test_obs_token_breakdown_openai_format():
    """OpenAI 兼容 usage 字段名 prompt_tokens/completion_tokens。"""
    (
        _obs_token_breakdown,
        *_,
    ) = _import_helpers()
    obs = {"usage": {"prompt_tokens": 17043, "completion_tokens": 9, "total_tokens": 17052}}
    inp, out = _obs_token_breakdown(obs)
    assert inp == 17043
    assert out == 9


def test_obs_token_breakdown_langfuse_v3_format():
    """Langfuse v3 usageDetails 风格 input/output。"""
    _obs_token_breakdown = _import_helpers()[0]
    obs = {"usage": {"input": 100, "output": 20, "total": 120}}
    inp, out = _obs_token_breakdown(obs)
    assert inp == 100
    assert out == 20


def test_obs_token_breakdown_missing_usage():
    """无 usage 字段返回 (0, 0)。"""
    _obs_token_breakdown = _import_helpers()[0]
    assert _obs_token_breakdown({}) == (0, 0)
    assert _obs_token_breakdown({"usage": None}) == (0, 0)
    assert _obs_token_breakdown({"usage": "not a dict"}) == (0, 0)


def test_trace_token_breakdown_sums_across_observations():
    """多 observation 求和：模拟 agent loop 3 次 LLM 调用。"""
    _, _trace_token_breakdown, *_ = _import_helpers()
    trace = {"id": "t1"}
    observations = [
        {"usage": {"prompt_tokens": 1000, "completion_tokens": 50, "total_tokens": 1050}},
        {"usage": {"prompt_tokens": 1200, "completion_tokens": 80, "total_tokens": 1280}},
        {"usage": {"prompt_tokens": 1500, "completion_tokens": 100, "total_tokens": 1600}},
    ]
    inp, out = _trace_token_breakdown(trace, observations)
    assert inp == 3700
    assert out == 230


def test_trace_token_breakdown_empty_observations():
    """无 observations 返回 (0, 0)。"""
    _, _trace_token_breakdown, *_ = _import_helpers()
    assert _trace_token_breakdown({}, None) == (0, 0)
    assert _trace_token_breakdown({}, []) == (0, 0)


def test_trace_token_breakdown_skips_non_dict():
    """observation list 含异常元素时跳过。"""
    _, _trace_token_breakdown, *_ = _import_helpers()
    observations = [
        "not a dict",
        {"usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}},
        None,
    ]
    inp, out = _trace_token_breakdown({}, observations)
    assert inp == 100
    assert out == 10


def test_obs_tokens_total():
    """_obs_tokens 取 total_tokens 字段。"""
    *_, _trace_latency_ms, _trace_status, _obs_tokens, _obs_ttft_ms, _ = _import_helpers()
    assert _obs_tokens({"usage": {"total_tokens": 100}}) == 100
    assert _obs_tokens({"usage": {"total": 50}}) == 50
    assert _obs_tokens({}) == 0


def test_trace_observation_count():
    """统计 observation 数量。"""
    _, _, _trace_observation_count, *_ = _import_helpers()
    assert _trace_observation_count(None) == 0
    assert _trace_observation_count([]) == 0
    assert _trace_observation_count([{"id": "a"}, {"id": "b"}]) == 2
    # 非字典元素不计入
    assert _trace_observation_count([{"id": "a"}, "x", None, {"id": "b"}]) == 2


def test_trace_latency_ms_top_level():
    """v3 顶层 latency 字段（秒）转毫秒。"""
    helpers = _import_helpers()
    _trace_latency_ms = helpers[3]
    assert _trace_latency_ms({"latency": 1.346}, None) == 1346
    assert _trace_latency_ms({"latency": 0}, None) == 0


def test_trace_latency_ms_fallback_to_observations():
    """无顶层 latency 时从 observation startTime/endTime 计算。"""
    helpers = _import_helpers()
    _trace_latency_ms = helpers[3]
    observations = [
        {"startTime": "2026-06-30T10:31:05.115Z", "endTime": "2026-06-30T10:31:06.461Z"},
    ]
    latency = _trace_latency_ms({}, observations)
    assert latency is not None
    assert 1300 <= latency <= 1400  # ~1.346s


def test_trace_status_error_observation():
    """observation level=ERROR 判为 error。"""
    helpers = _import_helpers()
    _trace_status = helpers[4]
    observations = [{"level": "ERROR"}]
    assert _trace_status({}, observations) == "error"


def test_trace_status_ok():
    """正常 trace 判为 ok。"""
    helpers = _import_helpers()
    _trace_status = helpers[4]
    observations = [{"level": "DEFAULT"}]
    assert _trace_status({"output": "hello"}, observations) == "ok"


# ── TTFT / 延迟拆分测试 ────────────────────────────────────


def test_obs_ttft_ms_streaming():
    """流式响应：completionStartTime 在 startTime 之后，TTFT = 两者差值。"""
    helpers = _import_helpers()
    _obs_ttft_ms = helpers[6]
    obs = {
        "startTime": "2026-06-30T10:31:05.115Z",
        "completionStartTime": "2026-06-30T10:31:05.500Z",  # +385ms
    }
    ttft = _obs_ttft_ms(obs)
    assert ttft is not None
    assert 380 <= ttft <= 400


def test_obs_ttft_ms_non_streaming_is_none():
    """非流式响应：completionStartTime 为 null，TTFT 返回 None。"""
    helpers = _import_helpers()
    _obs_ttft_ms = helpers[6]
    obs = {
        "startTime": "2026-06-30T10:31:05.115Z",
        "completionStartTime": None,
    }
    assert _obs_ttft_ms(obs) is None


def test_obs_ttft_ms_missing_start():
    """缺 startTime 返回 None。"""
    helpers = _import_helpers()
    _obs_ttft_ms = helpers[6]
    assert _obs_ttft_ms({"completionStartTime": "2026-06-30T10:31:05.500Z"}) is None
    assert _obs_ttft_ms({}) is None


def test_trace_latency_breakdown_streaming():
    """流式 trace：e2e=1346ms, ttft=385ms, output=9 tokens → avg_inc=(1346-385)/8=120ms。"""
    helpers = _import_helpers()
    _trace_latency_breakdown = helpers[7]
    trace = {"latency": 1.346}
    observations = [
        {
            "type": "GENERATION",
            "startTime": "2026-06-30T10:31:05.115Z",
            "endTime": "2026-06-30T10:31:06.461Z",
            "completionStartTime": "2026-06-30T10:31:05.500Z",
            "usage": {"prompt_tokens": 17043, "completion_tokens": 9, "total_tokens": 17052},
        },
    ]
    e2e, ttft, avg_inc = _trace_latency_breakdown(trace, observations)
    assert e2e == 1346
    assert ttft is not None and 380 <= ttft <= 400
    # (1346 - 385) / (9 - 1) = 961 / 8 = 120.125 → 120
    assert avg_inc is not None and 115 <= avg_inc <= 125


def test_trace_latency_breakdown_non_streaming():
    """非流式 trace：无 completionStartTime → ttft=None, avg_inc=None。"""
    helpers = _import_helpers()
    _trace_latency_breakdown = helpers[7]
    trace = {"latency": 1.0}
    observations = [
        {
            "type": "GENERATION",
            "startTime": "2026-06-30T10:31:05.000Z",
            "endTime": "2026-06-30T10:31:06.000Z",
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        },
    ]
    e2e, ttft, avg_inc = _trace_latency_breakdown(trace, observations)
    assert e2e == 1000
    assert ttft is None
    assert avg_inc is None


def test_trace_latency_breakdown_single_output_token():
    """输出 token <= 1 时无法算增量 → avg_inc=None。"""
    helpers = _import_helpers()
    _trace_latency_breakdown = helpers[7]
    trace = {"latency": 1.0}
    observations = [
        {
            "type": "GENERATION",
            "startTime": "2026-06-30T10:31:05.000Z",
            "completionStartTime": "2026-06-30T10:31:05.500Z",
            "usage": {"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101},
        },
    ]
    e2e, ttft, avg_inc = _trace_latency_breakdown(trace, observations)
    assert e2e == 1000
    assert ttft == 500
    assert avg_inc is None  # output_tokens=1, 无法算增量


def test_trace_latency_breakdown_no_observations():
    """无 observations：e2e 来自 trace.latency，ttft=None, avg_inc=None。"""
    helpers = _import_helpers()
    _trace_latency_breakdown = helpers[7]
    e2e, ttft, avg_inc = _trace_latency_breakdown({"latency": 2.5}, None)
    assert e2e == 2500
    assert ttft is None
    assert avg_inc is None


# ── _trace_cost：聚合 observation 成本（USD → CNY 转换） ─────────


def _import_cost_helper():
    from app.api.observability import _trace_cost
    return _trace_cost


@pytest.fixture()
def usd_to_cny_rate(monkeypatch):
    """强制汇率=1.0，让测试断言用 USD 原值（避开汇率浮动）。"""
    from pkg.common.config import settings
    monkeypatch.setattr(settings, "spend_usd_to_cny", 1.0)
    return 1.0


def test_trace_cost_sums_calculated_total_cost(usd_to_cny_rate):
    """正常路径：聚合多个 observation 的 calculatedTotalCost。"""
    _trace_cost = _import_cost_helper()
    obs = [
        {"type": "GENERATION", "calculatedTotalCost": 0.001},
        {"type": "SPAN", "calculatedTotalCost": 0.002},
        {"type": "GENERATION", "calculatedTotalCost": 0.003},
    ]
    assert _trace_cost(obs) == 0.006


def test_trace_cost_single_observation(usd_to_cny_rate):
    _trace_cost = _import_cost_helper()
    obs = [{"calculatedTotalCost": 0.00463652}]
    assert _trace_cost(obs) == pytest.approx(0.00463652, abs=1e-6)


def test_trace_cost_all_zero_returns_none(usd_to_cny_rate):
    """所有 observation calculatedTotalCost=0 → 返回 None（自托管未配定价表）。"""
    _trace_cost = _import_cost_helper()
    obs = [
        {"calculatedTotalCost": 0},
        {"calculatedTotalCost": 0},
    ]
    assert _trace_cost(obs) is None


def test_trace_cost_mixed_zero_and_non_zero_sums_non_zero(usd_to_cny_rate):
    """有 0 有非 0 → 求和，返回总和（0 不影响求和但有非 0 就不返回 None）。"""
    _trace_cost = _import_cost_helper()
    obs = [
        {"calculatedTotalCost": 0},
        {"calculatedTotalCost": 0.005},
    ]
    assert _trace_cost(obs) == 0.005


def test_trace_cost_missing_field_skipped(usd_to_cny_rate):
    """observation 无 calculatedTotalCost 字段 → 跳过。"""
    _trace_cost = _import_cost_helper()
    obs = [
        {"type": "GENERATION"},  # 无 cost 字段
        {"calculatedTotalCost": 0.01},
    ]
    assert _trace_cost(obs) == 0.01


def test_trace_cost_none_value_skipped(usd_to_cny_rate):
    """calculatedTotalCost=None → 跳过（Langfuse 未关联定价 tier 时返回 None）。"""
    _trace_cost = _import_cost_helper()
    obs = [
        {"calculatedTotalCost": None},
        {"calculatedTotalCost": 0.02},
    ]
    assert _trace_cost(obs) == 0.02


def test_trace_cost_all_none_returns_none(usd_to_cny_rate):
    _trace_cost = _import_cost_helper()
    obs = [{"calculatedTotalCost": None}, {"calculatedTotalCost": None}]
    assert _trace_cost(obs) is None


def test_trace_cost_non_numeric_skipped(usd_to_cny_rate):
    """非数字值（字符串等）跳过，不崩。"""
    _trace_cost = _import_cost_helper()
    obs = [
        {"calculatedTotalCost": "not-a-number"},
        {"calculatedTotalCost": 0.03},
    ]
    assert _trace_cost(obs) == 0.03


def test_trace_cost_empty_observations_returns_none(usd_to_cny_rate):
    _trace_cost = _import_cost_helper()
    assert _trace_cost([]) is None
    assert _trace_cost(None) is None


def test_trace_cost_skips_non_dict_observations(usd_to_cny_rate):
    """非字典元素（字符串/None）跳过。"""
    _trace_cost = _import_cost_helper()
    obs = ["x", None, {"calculatedTotalCost": 0.04}, 123]
    assert _trace_cost(obs) == 0.04


def test_trace_cost_only_non_dict_returns_none(usd_to_cny_rate):
    _trace_cost = _import_cost_helper()
    obs = ["x", None, 123]
    assert _trace_cost(obs) is None


def test_trace_cost_usd_to_cny_conversion(monkeypatch):
    """USD → CNY 转换：0.01 USD × 7.2 = 0.072 CNY。"""
    from pkg.common.config import settings
    monkeypatch.setattr(settings, "spend_usd_to_cny", 7.2)
    _trace_cost = _import_cost_helper()
    obs = [{"calculatedTotalCost": 0.01}]
    assert _trace_cost(obs) == round(0.01 * 7.2, 6)


def test_trace_cost_cny_conversion_aggregates_before_conversion(monkeypatch):
    """多个 observation 先聚合 USD 再转 CNY： (0.01 + 0.02) × 7.2 = 0.216。"""
    from pkg.common.config import settings
    monkeypatch.setattr(settings, "spend_usd_to_cny", 7.2)
    _trace_cost = _import_cost_helper()
    obs = [{"calculatedTotalCost": 0.01}, {"calculatedTotalCost": 0.02}]
    assert _trace_cost(obs) == round(0.03 * 7.2, 6)


# ── _model_matches / _score_obs_log_match / _match_observations_to_logs ──


def _import_match_helpers():
    from app.api.observability import (
        _match_observations_to_logs,
        _model_matches,
        _score_obs_log_match,
    )
    return _model_matches, _score_obs_log_match, _match_observations_to_logs


def test_model_matches_exact():
    _model_matches, *_ = _import_match_helpers()
    assert _model_matches("deepseek-chat", "deepseek-chat") is True


def test_model_matches_substring():
    """Langfuse obs.model='deepseek-chat'，LiteLLM log.model='deepseek/deepseek-chat' 视为匹配。"""
    _model_matches, *_ = _import_match_helpers()
    assert _model_matches("deepseek-chat", "deepseek/deepseek-chat") is True
    assert _model_matches("deepseek/deepseek-chat", "deepseek-chat") is True


def test_model_matches_case_insensitive():
    _model_matches, *_ = _import_match_helpers()
    assert _model_matches("DeepSeek-Chat", "DEEPSEEK-CHAT") is True


def test_model_matches_empty_returns_false():
    _model_matches, *_ = _import_match_helpers()
    assert _model_matches(None, "deepseek-chat") is False
    assert _model_matches("deepseek-chat", None) is False
    assert _model_matches("", "") is False


def test_model_matches_different_models():
    _model_matches, *_ = _import_match_helpers()
    assert _model_matches("deepseek-chat", "gpt-4") is False


def _obs_for_match(start_iso: str, prompt: int, completion: int, model: str = "deepseek-chat"):
    return {
        "type": "GENERATION",
        "startTime": start_iso,
        "model": model,
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


def _log_for_match(start_iso: str, prompt: int, completion: int, *, model: str = "deepseek/deepseek-chat", spend: float = 0.001, api_key: str = "key-123"):
    return {
        "startTime": start_iso,
        "model": model,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "spend": spend,
        "api_key": api_key,
    }


def test_score_obs_log_match_perfect():
    """模型+时间+token 全匹配 → (0, 0)。"""
    _, _score_obs_log_match, _ = _import_match_helpers()
    from datetime import datetime, UTC
    obs_start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    log = _log_for_match("2026-07-01T12:00:00Z", 100, 50)
    assert _score_obs_log_match(obs_start, 100, 50, log) == (0, 0)


def test_score_obs_log_match_time_too_far():
    """时间差 > 5min → None。"""
    _, _score_obs_log_match, _ = _import_match_helpers()
    from datetime import datetime, UTC
    obs_start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    log = _log_for_match("2026-07-01T12:06:01Z", 100, 50)  # 6min1s 后
    assert _score_obs_log_match(obs_start, 100, 50, log) is None


def test_score_obs_log_match_token_diff_does_not_filter():
    """token 差再大也不返回 None（Hermes obs.usage 跟 LiteLLM log.prompt_tokens 可能差几万，
    因 Hermes 把历史累加进 prompt，LiteLLM 只算实际发给上游的 token）。
    token_diff 仅作为多候选 tiebreaker，不作为过滤条件。"""
    _, _score_obs_log_match, _ = _import_match_helpers()
    from datetime import datetime, UTC
    obs_start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    log = _log_for_match("2026-07-01T12:00:00Z", 200, 50)  # prompt 差 100
    score = _score_obs_log_match(obs_start, 100, 50, log)
    assert score is not None
    assert score[0] == 0  # time_diff=0
    assert score[1] == 100  # token_diff=100（不过滤，但参与排序）


def test_score_obs_log_match_missing_start_time():
    """log 无 startTime → None。"""
    _, _score_obs_log_match, _ = _import_match_helpers()
    from datetime import datetime, UTC
    obs_start = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
    log = {"model": "deepseek-chat", "prompt_tokens": 100, "completion_tokens": 50, "spend": 0.001}
    assert _score_obs_log_match(obs_start, 100, 50, log) is None


def test_match_observations_to_logs_perfect_match():
    """1 obs + 1 log 完美匹配 → 返回 [log]。"""
    *_, _match_observations_to_logs = _import_match_helpers()
    obs = [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)]
    logs = [_log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.002)]
    matched = _match_observations_to_logs(obs, logs)
    assert len(matched) == 1
    assert matched[0]["spend"] == 0.002


def test_match_observations_to_logs_two_obs_two_logs():
    """2 obs + 2 log 各自匹配 → 返回 2 个 log（按 startTime 升序排）。"""
    *_, _match_observations_to_logs = _import_match_helpers()
    obs = [
        _obs_for_match("2026-07-01T12:01:00Z", 100, 50),
        _obs_for_match("2026-07-01T12:00:00Z", 200, 60),  # 早的先匹配
    ]
    logs = [
        _log_for_match("2026-07-01T12:00:00Z", 200, 60, spend=0.003),
        _log_for_match("2026-07-01T12:01:00Z", 100, 50, spend=0.002),
    ]
    matched = _match_observations_to_logs(obs, logs)
    assert len(matched) == 2
    spends = sorted([m["spend"] for m in matched])
    assert spends == [0.002, 0.003]


def test_match_observations_to_logs_log_reused_prevented():
    """2 obs + 1 log → 只匹配 1 个（log 已用不被重复匹配）。"""
    *_, _match_observations_to_logs = _import_match_helpers()
    obs = [
        _obs_for_match("2026-07-01T12:00:00Z", 100, 50),
        _obs_for_match("2026-07-01T12:00:30Z", 100, 50),  # 30s 后，跟第一个 obs 接近
    ]
    logs = [_log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.002)]
    matched = _match_observations_to_logs(obs, logs)
    assert len(matched) == 1


def test_match_observations_to_logs_no_logs_returns_empty():
    *_, _match_observations_to_logs = _import_match_helpers()
    obs = [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)]
    assert _match_observations_to_logs(obs, []) == []


def test_match_observations_to_logs_no_matching_obs_returns_empty():
    """obs 跟 log 模型/时间/token 都对不上 → []。"""
    *_, _match_observations_to_logs = _import_match_helpers()
    obs = [_obs_for_match("2026-07-01T12:00:00Z", 100, 50, model="gpt-4")]
    logs = [_log_for_match("2026-07-01T12:00:00Z", 100, 50, model="deepseek/deepseek-chat")]
    assert _match_observations_to_logs(obs, logs) == []


def test_match_observations_to_logs_skips_non_generation_obs():
    """非 GENERATION observation 跳过。"""
    *_, _match_observations_to_logs = _import_match_helpers()
    obs = [
        {"type": "SPAN", "startTime": "2026-07-01T12:00:00Z", "model": "deepseek-chat"},
        _obs_for_match("2026-07-01T12:00:00Z", 100, 50),
    ]
    logs = [_log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.002)]
    matched = _match_observations_to_logs(obs, logs)
    assert len(matched) == 1


def test_match_observations_to_logs_skips_obs_without_start_time():
    """observation 无 startTime 跳过。"""
    *_, _match_observations_to_logs = _import_match_helpers()
    obs = [
        {"type": "GENERATION", "model": "deepseek-chat", "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
        _obs_for_match("2026-07-01T12:00:00Z", 100, 50),
    ]
    logs = [_log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.002)]
    matched = _match_observations_to_logs(obs, logs)
    assert len(matched) == 1


def test_match_observations_to_logs_picks_closest_when_multiple_candidates():
    """多个候选 log 时选 token+时间差最小的。"""
    *_, _match_observations_to_logs = _import_match_helpers()
    obs = [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)]
    logs = [
        _log_for_match("2026-07-01T12:00:30Z", 105, 50, spend=0.001),  # 时间差 30s, token 差 5
        _log_for_match("2026-07-01T12:00:05Z", 100, 50, spend=0.002),  # 时间差 5s, token 差 0 ← 更优
    ]
    matched = _match_observations_to_logs(obs, logs)
    assert len(matched) == 1
    assert matched[0]["spend"] == 0.002


# ── _litellm_cost_for_trace：orchestration（含 DB + LiteLLM mock） ──


def _import_litellm_cost():
    from app.api.observability import _litellm_cost_for_trace
    return _litellm_cost_for_trace


@pytest.fixture()
def force_usd_to_cny_1(monkeypatch):
    """汇率=1.0，断言用 USD 原值。"""
    from pkg.common.config import settings
    monkeypatch.setattr(settings, "spend_usd_to_cny", 1.0)
    return 1.0


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_no_user_id_returns_none(force_usd_to_cny_1):
    """trace 无 userId → None。"""
    _litellm_cost_for_trace = _import_litellm_cost()
    result = await _litellm_cost_for_trace(None, {"userId": None}, [])
    assert result is None


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_no_key_id_returns_none(monkeypatch, force_usd_to_cny_1):
    """agent 在 DB 但无 key_id → None。"""
    from unittest.mock import AsyncMock
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value=None),
    )
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123"},
        [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)],
    )
    assert result is None


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_no_observations_returns_none(monkeypatch, force_usd_to_cny_1):
    """无 observations → None。"""
    _litellm_cost_for_trace = _import_litellm_cost()
    result = await _litellm_cost_for_trace(None, {"userId": "agent-123"}, [])
    assert result is None


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_litellm_api_error_returns_none(monkeypatch, force_usd_to_cny_1):
    """LiteLLM API 抛异常 → None（不阻塞，调用方回退）。"""
    from unittest.mock import AsyncMock
    from app.services.litellm_client import LitellmError
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value="key-123"),
    )
    monkeypatch.setattr(
        "app.api.observability.litellm_client.spend_logs",
        AsyncMock(side_effect=LitellmError("LiteLLM 不可达", 502)),
    )
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123"},
        [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)],
    )
    assert result is None


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_no_candidate_logs_returns_none(monkeypatch, force_usd_to_cny_1):
    """时间窗内无候选 log（api_key 不匹配）→ None。"""
    from unittest.mock import AsyncMock
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value="key-123"),
    )
    # LiteLLM 返回的 log api_key 跟 trace 的 key_id 不匹配
    monkeypatch.setattr(
        "app.api.observability.litellm_client.spend_logs",
        AsyncMock(return_value={"data": [_log_for_match("2026-07-01T12:00:00Z", 100, 50, api_key="other-key")]}),
    )
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123"},
        [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)],
    )
    assert result is None


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_perfect_match_aggregates_spend(monkeypatch, force_usd_to_cny_1):
    """1 obs + 1 log 完美匹配 → spend × 1.0（汇率被强制为 1）。"""
    from unittest.mock import AsyncMock
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value="key-123"),
    )
    monkeypatch.setattr(
        "app.api.observability.litellm_client.spend_logs",
        AsyncMock(return_value={"data": [_log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.0023, api_key="key-123")]}),
    )
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123"},
        [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)],
    )
    assert result == pytest.approx(0.0023, abs=1e-6)


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_multi_obs_aggregates(monkeypatch, force_usd_to_cny_1):
    """2 obs + 2 log 各自匹配 → 聚合 spend (0.001 + 0.002 = 0.003)。"""
    from unittest.mock import AsyncMock
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value="key-123"),
    )
    monkeypatch.setattr(
        "app.api.observability.litellm_client.spend_logs",
        AsyncMock(return_value={"data": [
            _log_for_match("2026-07-01T12:00:00Z", 200, 60, spend=0.001, api_key="key-123"),
            _log_for_match("2026-07-01T12:01:00Z", 100, 50, spend=0.002, api_key="key-123"),
        ]}),
    )
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123"},
        [
            _obs_for_match("2026-07-01T12:00:00Z", 200, 60),
            _obs_for_match("2026-07-01T12:01:00Z", 100, 50),
        ],
    )
    assert result == pytest.approx(0.003, abs=1e-6)


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_usd_to_cny_conversion(monkeypatch):
    """USD → CNY：0.002 USD × 7.2 = 0.0144 CNY。"""
    from unittest.mock import AsyncMock
    from pkg.common.config import settings
    monkeypatch.setattr(settings, "spend_usd_to_cny", 7.2)
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value="key-123"),
    )
    monkeypatch.setattr(
        "app.api.observability.litellm_client.spend_logs",
        AsyncMock(return_value={"data": [_log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.002, api_key="key-123")]}),
    )
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123"},
        [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)],
    )
    assert result == pytest.approx(round(0.002 * 7.2, 6), abs=1e-6)


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_partial_match_only_returns_matched(monkeypatch, force_usd_to_cny_1):
    """2 obs 但只有 1 个匹配 log → 只聚合匹配的那条。"""
    from unittest.mock import AsyncMock
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value="key-123"),
    )
    monkeypatch.setattr(
        "app.api.observability.litellm_client.spend_logs",
        AsyncMock(return_value={"data": [
            _log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.0015, api_key="key-123"),
            # 第二个 log 时间差 > 5min，匹配不上第二个 obs
            _log_for_match("2026-07-01T12:10:00Z", 200, 60, spend=0.002, api_key="key-123"),
        ]}),
    )
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123"},
        [
            _obs_for_match("2026-07-01T12:00:00Z", 100, 50),
            _obs_for_match("2026-07-01T12:00:30Z", 200, 60),  # 30s 后的 obs，log 在 12:10:00 不匹配
        ],
    )
    assert result == pytest.approx(0.0015, abs=1e-6)


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_zero_spend_returns_none(monkeypatch, force_usd_to_cny_1):
    """匹配到 log 但 spend=0 → 返回 None（让调用方回退 Langfuse calculatedTotalCost）。"""
    from unittest.mock import AsyncMock
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value="key-123"),
    )
    monkeypatch.setattr(
        "app.api.observability.litellm_client.spend_logs",
        AsyncMock(return_value={"data": [_log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.0, api_key="key-123")]}),
    )
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123"},
        [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)],
    )
    assert result is None


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_litellm_returns_list_not_dict(monkeypatch, force_usd_to_cny_1):
    """LiteLLM 返回直接是 list（兼容格式）也能处理。"""
    from unittest.mock import AsyncMock
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value="key-123"),
    )
    monkeypatch.setattr(
        "app.api.observability.litellm_client.spend_logs",
        AsyncMock(return_value=[_log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.004, api_key="key-123")]),
    )
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123"},
        [_obs_for_match("2026-07-01T12:00:00Z", 100, 50)],
    )
    assert result == pytest.approx(0.004, abs=1e-6)


@pytest.mark.asyncio
async def test_litellm_cost_for_trace_trace_without_observations_starttime_uses_createdat(monkeypatch, force_usd_to_cny_1):
    """observation 无 startTime 时回退到 trace.createdAt 作为时间窗锚点。"""
    from unittest.mock import AsyncMock
    _litellm_cost_for_trace = _import_litellm_cost()
    monkeypatch.setattr(
        "app.api.observability._resolve_agent_key_id",
        AsyncMock(return_value="key-123"),
    )
    monkeypatch.setattr(
        "app.api.observability.litellm_client.spend_logs",
        AsyncMock(return_value={"data": [_log_for_match("2026-07-01T12:00:00Z", 100, 50, spend=0.001, api_key="key-123")]}),
    )
    # observation 无 startTime，但有 usage；trace.createdAt 在 12:00:00
    obs = [{"type": "GENERATION", "model": "deepseek-chat", "usage": {"prompt_tokens": 100, "completion_tokens": 50}}]
    result = await _litellm_cost_for_trace(
        None,
        {"userId": "agent-123", "createdAt": "2026-07-01T12:00:00Z"},
        obs,
    )
    # observation 无 startTime，_match_observations_to_logs 会跳过它 → 无匹配 → None
    assert result is None


# ── _aggregate_by_agent / _aggregate_by_model：纯聚合 ──


def _import_aggregate_by_agent():
    from app.api.observability import _aggregate_by_agent
    return _aggregate_by_agent


def _import_aggregate_by_model():
    from app.api.observability import _aggregate_by_model
    return _aggregate_by_model


def _import_aggregate_by_group():
    from app.api.observability import _aggregate_by_group
    return _aggregate_by_group


def test_aggregate_by_agent_empty_logs_returns_empty():
    _aggregate_by_agent = _import_aggregate_by_agent()
    result = _aggregate_by_agent([], {"key-1": {"agent_id": "a1", "name": "Agent1"}})
    assert result == []


def test_aggregate_by_agent_single_log_matches():
    _aggregate_by_agent = _import_aggregate_by_agent()
    logs = [{"api_key": "key-123", "prompt_tokens": 100, "completion_tokens": 50}]
    key_meta = {"key-123": {"agent_id": "a1", "name": "Agent1"}}
    result = _aggregate_by_agent(logs, key_meta)
    assert len(result) == 1
    assert result[0]["agent_id"] == "a1"
    assert result[0]["name"] == "Agent1"
    assert result[0]["conversation_count"] == 1
    assert result[0]["total_tokens"] == 150


def test_aggregate_by_agent_groups_by_agent_and_sorts_desc():
    _aggregate_by_agent = _import_aggregate_by_agent()
    # key-1 出现 2 次，key-2 出现 1 次 → 按 conversation_count 降序
    logs = [
        {"api_key": "key-1", "prompt_tokens": 10, "completion_tokens": 5},
        {"api_key": "key-2", "prompt_tokens": 20, "completion_tokens": 10},
        {"api_key": "key-1", "prompt_tokens": 30, "completion_tokens": 15},
    ]
    key_meta = {
        "key-1": {"agent_id": "a1", "name": "Agent1"},
        "key-2": {"agent_id": "a2", "name": "Agent2"},
    }
    result = _aggregate_by_agent(logs, key_meta)
    assert len(result) == 2
    assert result[0]["agent_id"] == "a1"
    assert result[0]["conversation_count"] == 2
    assert result[0]["total_tokens"] == 60  # (10+5) + (30+15)
    assert result[1]["agent_id"] == "a2"
    assert result[1]["conversation_count"] == 1


def test_aggregate_by_agent_prefix_match_log_api_key_truncated():
    """log.api_key 是 key_id 前 20 字符（LiteLLM 截断显示），用 startswith 匹配。"""
    _aggregate_by_agent = _import_aggregate_by_agent()
    full_key_id = "a" * 64
    truncated = full_key_id[:20]
    logs = [{"api_key": truncated, "prompt_tokens": 100, "completion_tokens": 50}]
    key_meta = {full_key_id: {"agent_id": "a1", "name": "Agent1"}}
    result = _aggregate_by_agent(logs, key_meta)
    assert len(result) == 1
    assert result[0]["agent_id"] == "a1"


def test_aggregate_by_agent_skips_unmatched_and_missing_api_key():
    _aggregate_by_agent = _import_aggregate_by_agent()
    logs = [
        {"api_key": "unknown-key", "prompt_tokens": 100, "completion_tokens": 50},  # 不匹配
        {"prompt_tokens": 100, "completion_tokens": 50},  # 无 api_key
    ]
    key_meta = {"key-1": {"agent_id": "a1", "name": "Agent1"}}
    result = _aggregate_by_agent(logs, key_meta)
    assert result == []


def test_aggregate_by_agent_uses_log_agent_id_field_when_present():
    """fake log（Dify trace 注入）带 agent_id 字段时直取，不查 key_to_agent_meta。"""
    _aggregate_by_agent = _import_aggregate_by_agent()
    logs = [
        # fake log：带 agent_id / agent_name，无 api_key
        {"agent_id": "dify-agent-1", "agent_name": "Dify Agent 1",
         "prompt_tokens": 1000, "completion_tokens": 200},
        # 真实 spend_log：走 api_key 反查
        {"api_key": "key-aaa", "prompt_tokens": 500, "completion_tokens": 100},
    ]
    key_meta = {"key-aaa": {"agent_id": "hermes-agent-1", "name": "Hermes Agent 1"}}
    result = _aggregate_by_agent(logs, key_meta)
    assert len(result) == 2
    by_agent = {r["agent_id"]: r for r in result}
    assert by_agent["dify-agent-1"]["name"] == "Dify Agent 1"
    assert by_agent["dify-agent-1"]["total_tokens"] == 1200
    assert by_agent["dify-agent-1"]["conversation_count"] == 1
    assert by_agent["hermes-agent-1"]["total_tokens"] == 600


def test_aggregate_by_model_empty_logs_returns_empty():
    _aggregate_by_model = _import_aggregate_by_model()
    assert _aggregate_by_model([]) == []


def test_aggregate_by_model_groups_and_sorts_desc():
    _aggregate_by_model = _import_aggregate_by_model()
    logs = [
        {"model": "gpt-4", "prompt_tokens": 100, "completion_tokens": 50, "spend": 0.01},
        {"model": "claude-3", "prompt_tokens": 200, "completion_tokens": 100, "spend": 0.02},
        {"model": "gpt-4", "prompt_tokens": 50, "completion_tokens": 25, "spend": 0.005},
    ]
    result = _aggregate_by_model(logs)
    assert len(result) == 2
    # gpt-4 total=225, claude-3 total=300 → claude 在前
    assert result[0]["model"] == "claude-3"
    assert result[0]["total_tokens"] == 300
    assert result[0]["total_cost"] == 0.02
    assert result[1]["model"] == "gpt-4"
    assert result[1]["total_tokens"] == 225
    assert result[1]["total_cost"] == 0.015


def test_aggregate_by_model_missing_model_field_skipped():
    """model 为空/None 的 log（失败请求）跳过，不入 unknown 桶。"""
    _aggregate_by_model = _import_aggregate_by_model()
    logs = [
        {"prompt_tokens": 100, "completion_tokens": 50, "spend": 0.01},  # 无 model
        {"model": None, "prompt_tokens": 50, "completion_tokens": 25, "spend": 0.005},  # model=None
        {"model": "", "prompt_tokens": 10, "completion_tokens": 5, "spend": 0.001},  # model=空串
    ]
    result = _aggregate_by_model(logs)
    assert result == []


# ── _aggregate_by_group ──


def test_aggregate_by_group_empty_logs_returns_empty():
    _aggregate_by_group = _import_aggregate_by_group()
    result = _aggregate_by_group([], {"key-1": {"agent_id": "a1", "name": "Agent1", "group_id": "g1"}})
    assert result == []


def test_aggregate_by_group_groups_by_group_id_and_sorts_by_tokens_desc():
    """两组各 1 条 log，按 total_tokens 降序；name 字段初始为空（由调用方 enrich）。"""
    _aggregate_by_group = _import_aggregate_by_group()
    logs = [
        {"api_key": "key-1", "prompt_tokens": 100, "completion_tokens": 50, "spend": 0.01},  # g1: 150 tokens
        {"api_key": "key-2", "prompt_tokens": 200, "completion_tokens": 100, "spend": 0.02},  # g2: 300 tokens
    ]
    key_meta = {
        "key-1": {"agent_id": "a1", "name": "Agent1", "group_id": "g1"},
        "key-2": {"agent_id": "a2", "name": "Agent2", "group_id": "g2"},
    }
    result = _aggregate_by_group(logs, key_meta)
    assert len(result) == 2
    # g2 (300 tokens) 在前，g1 (150 tokens) 在后
    assert result[0]["group_id"] == "g2"
    assert result[0]["total_tokens"] == 300
    assert result[0]["total_cost"] == 0.02
    assert result[0]["conversation_count"] == 1
    assert result[0]["name"] == ""  # 待 enrich
    assert result[1]["group_id"] == "g1"
    assert result[1]["total_tokens"] == 150


def test_aggregate_by_group_accumulates_within_same_group():
    """同一 group 的多个 agent 多次调用累加到同一桶。"""
    _aggregate_by_group = _import_aggregate_by_group()
    logs = [
        {"api_key": "key-1", "prompt_tokens": 10, "completion_tokens": 5, "spend": 0.01},
        {"api_key": "key-2", "prompt_tokens": 20, "completion_tokens": 10, "spend": 0.02},
        {"api_key": "key-1", "prompt_tokens": 30, "completion_tokens": 15, "spend": 0.03},
    ]
    # key-1 和 key-2 都属于 g1
    key_meta = {
        "key-1": {"agent_id": "a1", "name": "Agent1", "group_id": "g1"},
        "key-2": {"agent_id": "a2", "name": "Agent2", "group_id": "g1"},
    }
    result = _aggregate_by_group(logs, key_meta)
    assert len(result) == 1
    assert result[0]["group_id"] == "g1"
    assert result[0]["conversation_count"] == 3
    assert result[0]["total_tokens"] == 90  # (10+5) + (20+10) + (30+15)
    assert result[0]["total_cost"] == 0.06


def test_aggregate_by_group_skips_unmatched_and_missing_group_id():
    """api_key 不匹配 / group_id 为空的 log 跳过。"""
    _aggregate_by_group = _import_aggregate_by_group()
    logs = [
        {"api_key": "unknown-key", "prompt_tokens": 100, "completion_tokens": 50, "spend": 0.01},  # 不匹配
        {"api_key": "key-no-group", "prompt_tokens": 50, "completion_tokens": 25, "spend": 0.005},  # group_id 空
        {"prompt_tokens": 30, "completion_tokens": 15, "spend": 0.001},  # 无 api_key
    ]
    key_meta = {
        "key-no-group": {"agent_id": "a3", "name": "Agent3", "group_id": ""},  # group_id 空
    }
    result = _aggregate_by_group(logs, key_meta)
    assert result == []


def test_aggregate_by_group_uses_log_group_id_field_when_present():
    """fake log（Dify trace 注入）带 group_id 字段时直取，不查 key_to_agent_meta。"""
    _aggregate_by_group = _import_aggregate_by_group()
    logs = [
        # fake log：带 group_id，无 api_key
        {"group_id": "dify-group-1", "prompt_tokens": 1000, "completion_tokens": 200, "spend": 0.002},
        # 真实 spend_log：走 api_key 反查
        {"api_key": "key-aaa", "prompt_tokens": 500, "completion_tokens": 100, "spend": 0.001},
    ]
    key_meta = {"key-aaa": {"agent_id": "a1", "name": "A1", "group_id": "hermes-group-1"}}
    result = _aggregate_by_group(logs, key_meta)
    assert len(result) == 2
    by_group = {r["group_id"]: r for r in result}
    assert by_group["dify-group-1"]["total_tokens"] == 1200
    assert by_group["dify-group-1"]["total_cost"] == pytest.approx(0.002, abs=1e-9)
    assert by_group["hermes-group-1"]["total_tokens"] == 600


# ── _resolve_agent_ids：mock DB ──


def _make_mock_db_execute(scalars_all_result=None, all_result=None):
    """构造 mock AsyncSession，db.execute(...) 返回预设结果。

    scalars_all_result: 用于 .scalars().all() 路径（_resolve_agent_ids 用）
    all_result: 用于 .all() 路径（_resolve_key_ids_for_agents 用）
    """
    result = MagicMock()
    if scalars_all_result is not None:
        result.scalars.return_value.all.return_value = scalars_all_result
    if all_result is not None:
        result.all.return_value = all_result
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_resolve_agent_ids_no_filter_returns_none():
    """全 None → 返回 None（无筛选，全部 agents）。"""
    from app.api.observability import _resolve_agent_ids
    result = await _resolve_agent_ids(
        MagicMock(), agent_id=None, user_group_id=None
    )
    assert result is None


@pytest.mark.asyncio
async def test_resolve_agent_ids_user_group_id_only():
    from app.api.observability import _resolve_agent_ids
    db = _make_mock_db_execute(scalars_all_result=["a1", "a2"])
    result = await _resolve_agent_ids(
        db, agent_id=None, user_group_id="group-1"
    )
    assert set(result) == {"a1", "a2"}


@pytest.mark.asyncio
async def test_resolve_agent_ids_agent_id_only():
    from app.api.observability import _resolve_agent_ids
    # agent_id 单传不查 DB（直接返回 [agent_id]）
    result = await _resolve_agent_ids(
        MagicMock(), agent_id="a-specific-id", user_group_id=None
    )
    assert result == ["a-specific-id"]


@pytest.mark.asyncio
async def test_resolve_agent_ids_user_group_id_and_agent_id_intersect():
    from app.api.observability import _resolve_agent_ids
    # user_group_id 解析得到 [a1, a2, a3]，与 agent_id="a2" 取交集 → ["a2"]
    db = _make_mock_db_execute(scalars_all_result=["a1", "a2", "a3"])
    result = await _resolve_agent_ids(
        db, agent_id="a2", user_group_id="group-1"
    )
    assert result == ["a2"]


@pytest.mark.asyncio
async def test_resolve_agent_ids_intersection_empty_returns_empty():
    from app.api.observability import _resolve_agent_ids
    # user_group_id 解析得到 [a1, a2]，与 agent_id="a-other" 取交集 → 空
    db = _make_mock_db_execute(scalars_all_result=["a1", "a2"])
    result = await _resolve_agent_ids(
        db, agent_id="a-other", user_group_id="group-1"
    )
    assert result == []


# ── _filter_traces_by_enduser：客户端 metadata.enduser_id 过滤 ──


def test_filter_traces_by_enduser_none_returns_original_list():
    """enduser_id=None → 不过滤，返回原列表。"""
    from app.api.observability import _filter_traces_by_enduser
    traces = [
        {"id": "t1", "metadata": {"enduser_id": "u1"}},
        {"id": "t2", "metadata": {"enduser_id": "u2"}},
        {"id": "t3", "metadata": {}},
    ]
    result = _filter_traces_by_enduser(traces, None)
    assert len(result) == 3


def test_filter_traces_by_enduser_match():
    """enduser_id 有值 → 只保留 metadata.enduser_id 匹配的。"""
    from app.api.observability import _filter_traces_by_enduser
    traces = [
        {"id": "t1", "metadata": {"enduser_id": "u1"}},
        {"id": "t2", "metadata": {"enduser_id": "u2"}},
        {"id": "t3", "metadata": {}},
        {"id": "t4"},  # 完全无 metadata
    ]
    result = _filter_traces_by_enduser(traces, "u1")
    assert len(result) == 1
    assert result[0]["id"] == "t1"


def test_filter_traces_by_enduser_no_match_returns_empty():
    """enduser_id 匹配不到任何 trace → 返回空列表。"""
    from app.api.observability import _filter_traces_by_enduser
    traces = [
        {"id": "t1", "metadata": {"enduser_id": "u1"}},
        {"id": "t2", "metadata": {"enduser_id": "u2"}},
    ]
    result = _filter_traces_by_enduser(traces, "u-nonexistent")
    assert result == []


def test_filter_traces_by_enduser_empty_traces():
    """traces 为空列表 → 返回空列表（无论 enduser_id 是否有值）。"""
    from app.api.observability import _filter_traces_by_enduser
    assert _filter_traces_by_enduser([], "u1") == []
    assert _filter_traces_by_enduser([], None) == []


# ── _filter_traces_by_channel_type：客户端 metadata.channel_type 过滤 ──


def test_filter_traces_by_channel_type_none_returns_original_list():
    """channel_type=None → 不过滤，返回原列表。"""
    from app.api.observability import _filter_traces_by_channel_type
    traces = [
        {"id": "t1", "metadata": {"channel_type": "web"}},
        {"id": "t2", "metadata": {"channel_type": "feishu"}},
        {"id": "t3", "metadata": {}},
    ]
    result = _filter_traces_by_channel_type(traces, None)
    assert len(result) == 3


def test_filter_traces_by_channel_type_match():
    """channel_type 有值 → 只保留 metadata.channel_type 匹配的。"""
    from app.api.observability import _filter_traces_by_channel_type
    traces = [
        {"id": "t1", "metadata": {"channel_type": "web"}},
        {"id": "t2", "metadata": {"channel_type": "feishu"}},
        {"id": "t3", "metadata": {}},
        {"id": "t4"},  # 完全无 metadata
    ]
    result = _filter_traces_by_channel_type(traces, "feishu")
    assert len(result) == 1
    assert result[0]["id"] == "t2"


def test_filter_traces_by_channel_type_no_match_returns_empty():
    """channel_type 匹配不到任何 trace → 返回空列表。"""
    from app.api.observability import _filter_traces_by_channel_type
    traces = [
        {"id": "t1", "metadata": {"channel_type": "web"}},
        {"id": "t2", "metadata": {"channel_type": "wecom"}},
    ]
    result = _filter_traces_by_channel_type(traces, "dingtalk")
    assert result == []


def test_filter_traces_by_channel_type_empty_traces():
    """traces 为空列表 → 返回空列表。"""
    from app.api.observability import _filter_traces_by_channel_type
    assert _filter_traces_by_channel_type([], "web") == []
    assert _filter_traces_by_channel_type([], None) == []


# ── /traces 端点：TraceItem 顶层提取 enduser_id/channel_type ──


def test_list_traces_extracts_enduser_and_channel_to_top_level():
    """list_traces 应从 trace.metadata 提取 enduser_id/channel_type 放到 item 顶层。"""
    from app.api.observability import _filter_traces_by_enduser, _filter_traces_by_channel_type
    # 这里不调端点（需 DB + langfuse mock），只验证提取逻辑等价于过滤逻辑：
    # 即 metadata.enduser_id / metadata.channel_type 能从顶层 metadata 读出
    traces = [
        {"id": "t1", "userId": "a1", "metadata": {"enduser_id": "u1", "channel_type": "web"}},
        {"id": "t2", "userId": "a2", "metadata": {"enduser_id": "u2", "channel_type": "feishu"}},
        {"id": "t3", "userId": "a3", "metadata": {}},
    ]
    # 模拟 list_traces 内的提取逻辑
    items = []
    for t in traces:
        metadata = t.get("metadata") or {}
        items.append({
            "id": t.get("id"),
            "enduser_id": metadata.get("enduser_id"),
            "channel_type": metadata.get("channel_type"),
        })
    assert items[0]["enduser_id"] == "u1"
    assert items[0]["channel_type"] == "web"
    assert items[1]["enduser_id"] == "u2"
    assert items[1]["channel_type"] == "feishu"
    assert items[2]["enduser_id"] is None
    assert items[2]["channel_type"] is None
    # 顺便验证过滤函数与提取一致
    assert len(_filter_traces_by_enduser(traces, "u1")) == 1
    assert len(_filter_traces_by_channel_type(traces, "feishu")) == 1


# ── _resolve_key_ids_for_agents：mock DB ──


@pytest.mark.asyncio
async def test_resolve_key_ids_empty_agent_ids_returns_empty_dict_no_db_query():
    from app.api.observability import _resolve_key_ids_for_agents
    db = MagicMock()
    db.execute = AsyncMock()  # 不应被调用
    result = await _resolve_key_ids_for_agents(db, [])
    assert result == {}
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_key_ids_specific_agent_ids_returns_mapping():
    from app.api.observability import _resolve_key_ids_for_agents
    # 模拟 DB 返回 [(agent_id, name, litellm_config, group_id), ...]
    db = _make_mock_db_execute(
        all_result=[
            ("a1", "Agent1", {"key_id": "key-1"}, "g1"),
            ("a2", "Agent2", {"key_id": "key-2"}, "g2"),
        ]
    )
    result = await _resolve_key_ids_for_agents(db, ["a1", "a2"])
    assert result == {
        "key-1": {"agent_id": "a1", "name": "Agent1", "group_id": "g1"},
        "key-2": {"agent_id": "a2", "name": "Agent2", "group_id": "g2"},
    }


@pytest.mark.asyncio
async def test_resolve_key_ids_skips_agents_without_key_id():
    from app.api.observability import _resolve_key_ids_for_agents
    db = _make_mock_db_execute(
        all_result=[
            ("a1", "Agent1", {"key_id": "key-1"}, "g1"),
            ("a2", "Agent2", {}, "g2"),  # 无 key_id
            ("a3", "Agent3", None, "g3"),  # cfg=None
        ]
    )
    result = await _resolve_key_ids_for_agents(db, ["a1", "a2", "a3"])
    assert "key-1" in result
    assert len(result) == 1


@pytest.mark.asyncio
async def test_resolve_key_ids_none_agent_ids_returns_all_published():
    from app.api.observability import _resolve_key_ids_for_agents
    db = _make_mock_db_execute(
        all_result=[
            ("a1", "Agent1", {"key_id": "key-1"}, "g1"),
            ("a2", "Agent2", {"key_id": "key-2"}, "g2"),
        ]
    )
    result = await _resolve_key_ids_for_agents(db, None)
    assert len(result) == 2
    assert result["key-1"]["agent_id"] == "a1"
    assert result["key-2"]["name"] == "Agent2"
    assert result["key-1"]["group_id"] == "g1"


# ── _resolve_agent_names：mock DB ──


@pytest.mark.asyncio
async def test_resolve_agent_names_empty_list_returns_empty_no_db_query():
    """空列表返回 {}，不查 DB。"""
    from app.api.observability import _resolve_agent_names
    db = MagicMock()
    db.execute = AsyncMock()  # 不应被调用
    result = await _resolve_agent_names(db, [])
    assert result == {}
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_agent_names_returns_mapping_for_known_ids():
    """正常路径：DB 返回 (id, name) 元组列表，转 dict。"""
    from app.api.observability import _resolve_agent_names
    db = _make_mock_db_execute(
        all_result=[
            ("a1", "Agent1"),
            ("a2", "Agent2"),
        ]
    )
    result = await _resolve_agent_names(db, ["a1", "a2"])
    assert result == {"a1": "Agent1", "a2": "Agent2"}


@pytest.mark.asyncio
async def test_resolve_agent_names_skips_unknown_ids():
    """agent_id 在 DB 中不存在时，结果里不包含该 id（不抛错）。"""
    from app.api.observability import _resolve_agent_names
    db = _make_mock_db_execute(
        all_result=[
            ("a1", "Agent1"),  # a2 不在 DB
        ]
    )
    result = await _resolve_agent_names(db, ["a1", "a2"])
    assert result == {"a1": "Agent1"}
    assert "a2" not in result


@pytest.mark.asyncio
async def test_resolve_agent_names_coerces_uuid_to_str():
    """UUID 对象在结果 key 里被 str() 化（避免 UUID key 查 dict 拿不到）。"""
    from app.api.observability import _resolve_agent_names
    import uuid
    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    db = _make_mock_db_execute(
        all_result=[(uid, "Agent1")]
    )
    result = await _resolve_agent_names(db, [str(uid)])
    assert result == {"12345678-1234-5678-1234-567812345678": "Agent1"}


# ── _resolve_group_names：mock DB ──


@pytest.mark.asyncio
async def test_resolve_group_names_empty_list_returns_empty_no_db_query():
    """空列表返回 {}，不查 DB。"""
    from app.api.observability import _resolve_group_names
    db = MagicMock()
    db.execute = AsyncMock()  # 不应被调用
    result = await _resolve_group_names(db, [])
    assert result == {}
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_group_names_returns_mapping_for_known_ids():
    """DB 返回 (id, name) 元组列表，转 {id: name}。"""
    from app.api.observability import _resolve_group_names
    db = _make_mock_db_execute(
        all_result=[
            ("g1", "Group1"),
            ("g2", "Group2"),
        ]
    )
    result = await _resolve_group_names(db, ["g1", "g2"])
    assert result == {"g1": "Group1", "g2": "Group2"}


# ── _fetch_traces_for_agents：mock langfuse_client ──


def _make_trace(tid: str, user_id: str, created_at: str) -> dict:
    return {"id": tid, "userId": user_id, "createdAt": created_at}


@pytest.mark.asyncio
async def test_fetch_traces_no_filter_calls_once_without_user_id(monkeypatch):
    """agent_ids=None：单次调用，不传 user_id。"""
    from app.api import observability
    calls: list[dict] = []

    async def _mock_list_traces(**kwargs):
        calls.append(kwargs)
        return {"data": [_make_trace("t1", "aid1", "2026-07-01T10:00:00Z")]}

    monkeypatch.setattr(observability.langfuse_client, "list_traces", _mock_list_traces)
    traces = await observability._fetch_traces_for_agents(None, None, None)
    assert len(traces) == 1
    assert traces[0]["id"] == "t1"
    assert len(calls) == 1
    assert "user_id" not in calls[0] or calls[0]["user_id"] is None


@pytest.mark.asyncio
async def test_fetch_traces_single_agent_passes_user_id(monkeypatch):
    """agent_ids=[id1]：单次调用，传 user_id=id1。"""
    from app.api import observability
    calls: list[dict] = []

    async def _mock_list_traces(**kwargs):
        calls.append(kwargs)
        return {"data": [_make_trace("t1", "aid1", "2026-07-01T10:00:00Z")]}

    monkeypatch.setattr(observability.langfuse_client, "list_traces", _mock_list_traces)
    traces = await observability._fetch_traces_for_agents(["aid1"], None, None)
    assert len(traces) == 1
    assert len(calls) == 1
    assert calls[0]["user_id"] == "aid1"


@pytest.mark.asyncio
async def test_fetch_traces_multi_agent_merges_and_sorts_by_createdAt_desc(monkeypatch):
    """agent_ids=[id1, id2]：并发拉每个 agent，合并后按 createdAt 倒序。"""
    from app.api import observability
    calls: list[str] = []

    async def _mock_list_traces(**kwargs):
        aid = kwargs.get("user_id")
        calls.append(aid)
        if aid == "aid1":
            return {"data": [
                _make_trace("t1", "aid1", "2026-07-01T10:00:00Z"),
                _make_trace("t2", "aid1", "2026-07-01T08:00:00Z"),
            ]}
        else:
            return {"data": [
                _make_trace("t3", "aid2", "2026-07-01T09:00:00Z"),
            ]}

    monkeypatch.setattr(observability.langfuse_client, "list_traces", _mock_list_traces)
    traces = await observability._fetch_traces_for_agents(["aid1", "aid2"], None, None)
    assert len(traces) == 3
    # 按 createdAt 倒序：10:00 > 09:00 > 08:00
    assert traces[0]["id"] == "t1"
    assert traces[1]["id"] == "t3"
    assert traces[2]["id"] == "t2"
    assert set(calls) == {"aid1", "aid2"}


@pytest.mark.asyncio
async def test_fetch_traces_skips_failed_calls_and_merges_successful(monkeypatch):
    """某个 agent 的调用抛异常时跳过，不影响其他 agent 的结果。"""
    from app.api import observability

    async def _mock_list_traces(**kwargs):
        aid = kwargs.get("user_id")
        if aid == "aid_bad":
            raise RuntimeError("langfuse timeout")
        return {"data": [_make_trace("t1", aid, "2026-07-01T10:00:00Z")]}

    monkeypatch.setattr(observability.langfuse_client, "list_traces", _mock_list_traces)
    traces = await observability._fetch_traces_for_agents(["aid_bad", "aid_ok"], None, None)
    # aid_bad 抛错被跳过，只保留 aid_ok 的 trace
    assert len(traces) == 1
    assert traces[0]["id"] == "t1"


@pytest.mark.asyncio
async def test_fetch_traces_skips_none_response(monkeypatch):
    """langfuse_client.list_traces 返回 None 时跳过（未配置/超时等）。"""
    from app.api import observability

    async def _mock_list_traces(**kwargs):
        return None

    monkeypatch.setattr(observability.langfuse_client, "list_traces", _mock_list_traces)
    traces = await observability._fetch_traces_for_agents(["aid1", "aid2"], None, None)
    assert traces == []


@pytest.mark.asyncio
async def test_fetch_traces_single_agent_does_not_sort(monkeypatch):
    """agent_ids 只有一个时不排序（保持 Langfuse 返回的原始顺序）。"""
    from app.api import observability

    async def _mock_list_traces(**kwargs):
        return {"data": [
            _make_trace("t1", "aid1", "2026-07-01T08:00:00Z"),
            _make_trace("t2", "aid1", "2026-07-01T10:00:00Z"),
        ]}

    monkeypatch.setattr(observability.langfuse_client, "list_traces", _mock_list_traces)
    traces = await observability._fetch_traces_for_agents(["aid1"], None, None)
    # 单 agent 不排序，保持原始顺序
    assert traces[0]["id"] == "t1"
    assert traces[1]["id"] == "t2"


# ═══════════════════════════════════════════════════
# /resources 端点测试
# ═══════════════════════════════════════════════════

def _prom_scalar(value: float) -> list[dict]:
    """Prometheus 即时查询返回的单值结构。"""
    return [{"metric": {}, "value": [1719700000, str(value)]}]


def _prom_topk(items: list[tuple[str, float]]) -> list[dict]:
    """topk() 查询返回结构，items = [(label_value, value), ...]。"""
    return [{"metric": {"instance": lbl}, "value": [1719700000, str(v)]} for lbl, v in items]


def _prom_topk_pod(items: list[tuple[str, float]]) -> list[dict]:
    """topk() 查询返回结构（pod 标签）。"""
    return [{"metric": {"pod": lbl}, "value": [1719700000, str(v)]} for lbl, v in items]


def _prom_range(values: list[tuple[float, float]]) -> list[dict]:
    """query_range 返回单条 series。values = [(ts, value), ...]。"""
    return [{"metric": {}, "values": [[ts, str(v)] for ts, v in values]}]


def _prom_pod_restarts(items: list[tuple[str, int]]) -> list[dict]:
    """kube_pod_container_status_restarts_total 返回多条 series（每 pod 一条）。"""
    return [{"metric": {"pod": lbl}, "value": [1719700000, str(v)]} for lbl, v in items]


@pytest.mark.asyncio
async def test_resources_not_configured_returns_empty_state(monkeypatch):
    """prometheus_client.is_configured() 返回 False 时，返回 metrics_available=False 但 grafana_url 仍返回。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: False)
    # 即使 query 被调用也应返回 None（保险起见 mock 掉）
    monkeypatch.setattr(observability.prometheus_client, "query", AsyncMock(return_value=None))
    monkeypatch.setattr(observability.prometheus_client, "query_range", AsyncMock(return_value=None))
    # 设个 grafana 外链验证仍返回
    monkeypatch.setattr(observability.settings, "grafana_external_url", "http://example.com:30090", raising=False)

    resp = await _client_get_resources(monkeypatch)
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics_available"] is False
    assert data["cluster"] == {"cpu_pct": 0.0, "memory_pct": 0.0, "pod_count": 0}
    assert data["trend"] == []
    assert data["top_nodes"] == []
    assert data["top_pods"] == []
    assert data["firing_alerts"] == 0
    assert data["grafana_url"] == "http://example.com:30090"


@pytest.mark.asyncio
async def test_resources_returns_aggregated_data(monkeypatch):
    """全查询成功时，返回集群概览 + 趋势 + Top 节点 + Top Pod。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)

    # 用计数器记录 query 被调用的 PromQL（验证查询模板正确）
    query_calls: list[str] = []
    range_calls: list[str] = []

    async def _mock_query(promql: str):
        query_calls.append(promql)
        if "avg(rate(node_cpu_seconds_total" in promql and "topk" not in promql:
            return _prom_scalar(0.235)  # 23.5%
        if "sum(node_memory_MemAvailable_bytes" in promql and "topk" not in promql:
            return _prom_scalar(0.642)  # 64.2%
        if 'count(kube_pod_status_phase' in promql:
            return _prom_scalar(17.0)
        if "count(ALERTS" in promql:
            return _prom_scalar(2.0)
        if "topk(5, (1 - rate(node_cpu_seconds_total" in promql:
            return _prom_topk([("node-1", 45.2), ("node-2", 30.1), ("node-3", 15.5)])
        if "topk(5, (1 - node_memory_MemAvailable_bytes" in promql:
            return _prom_topk([("node-1", 70.1), ("node-2", 60.0)])
        if "topk(5, (1 - node_filesystem_avail_bytes" in promql:
            return _prom_topk([("node-1", 38.5), ("node-3", 25.0)])
        if "topk(5, sum by (pod) (kube_pod_container_resource_requests{resource=\"cpu\"" in promql:
            return _prom_topk_pod([("manager-x", 0.325), ("gateway-y", 0.15)])
        if "topk(5, sum by (pod) (kube_pod_container_resource_requests{resource=\"memory\"" in promql:
            return _prom_topk_pod([("manager-x", 608174080), ("gateway-y", 209715200)])  # 580 MB, 200 MB
        if "max by (pod) (kube_pod_container_status_restarts_total" in promql:
            return _prom_pod_restarts([("manager-x", 3), ("gateway-y", 0)])
        return None

    async def _mock_query_range(promql: str, start, end, step):
        range_calls.append(promql)
        if "avg(rate(node_cpu_seconds_total" in promql:
            return _prom_range([(1719700000, 0.221), (1719700060, 0.235), (1719700120, 0.240)])
        if "sum(node_memory_MemAvailable_bytes" in promql:
            return _prom_range([(1719700000, 0.635), (1719700060, 0.642), (1719700120, 0.650)])
        return None

    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)
    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)
    monkeypatch.setattr(observability.settings, "grafana_external_url", "http://example.com:30090", raising=False)

    resp = await _client_get_resources(monkeypatch)
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics_available"] is True
    assert data["range"] == "1h"
    assert data["cluster"] == {"cpu_pct": 23.5, "memory_pct": 64.2, "pod_count": 17}
    assert len(data["trend"]) == 3
    assert data["trend"][0] == {"ts": 1719700000, "cpu_pct": 22.1, "memory_pct": 63.5}
    # top_nodes 按 CPU 排序
    assert data["top_nodes"][0]["instance"] == "node-1"
    assert data["top_nodes"][0]["cpu_pct"] == 45.2
    assert data["top_nodes"][0]["memory_pct"] == 70.1
    assert data["top_nodes"][0]["disk_pct"] == 38.5
    # top_pods 按 CPU 排序
    assert data["top_pods"][0]["pod"] == "manager-x"
    assert data["top_pods"][0]["cpu_used_cores"] == 0.325
    assert data["top_pods"][0]["memory_used_mb"] == 580.0
    assert data["top_pods"][0]["restarts"] == 3
    assert data["firing_alerts"] == 2
    assert data["grafana_url"] == "http://example.com:30090"

    # 验证关键 PromQL 被调用
    assert any("avg(rate(node_cpu_seconds_total{mode=\"idle\"}[5m]))" in q for q in query_calls)
    assert any("count(kube_pod_status_phase{namespace=\"unionagents\"" in q for q in query_calls)
    assert any("topk(5, sum by (pod) (kube_pod_container_resource_requests{resource=\"cpu\",namespace=\"unionagents\"})" in q for q in query_calls)


@pytest.mark.asyncio
async def test_resources_partial_failure_keeps_metrics_available(monkeypatch):
    """部分查询失败（返回 None）时，只要任一核心查询成功仍 metrics_available=True。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)

    async def _mock_query(promql: str):
        if "avg(rate(node_cpu_seconds_total" in promql and "topk" not in promql:
            return _prom_scalar(0.30)
        return None  # 其他全失败

    async def _mock_query_range(promql, start, end, step):
        return None

    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)
    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)

    resp = await _client_get_resources(monkeypatch)
    data = resp.json()
    assert data["metrics_available"] is True
    assert data["cluster"]["cpu_pct"] == 30.0
    assert data["cluster"]["memory_pct"] == 0.0  # 失败回退
    assert data["cluster"]["pod_count"] == 0
    assert data["trend"] == []
    assert data["top_nodes"] == []


@pytest.mark.asyncio
async def test_resources_invalid_range_returns_422(monkeypatch):
    """非法 range 值返回 422 校验错误。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)
    monkeypatch.setattr(observability.prometheus_client, "query", AsyncMock(return_value=None))
    monkeypatch.setattr(observability.prometheus_client, "query_range", AsyncMock(return_value=None))

    resp = await _client_get_resources(monkeypatch, range_param="invalid")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_resources_range_7d_uses_3600s_step(monkeypatch):
    """range=7d 时 step=3600s（1 小时一个点）。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)
    range_calls: list[tuple[str, float, float, str]] = []

    async def _mock_query_range(promql, start, end, step):
        range_calls.append((promql, start, end, step))
        return _prom_range([(1719700000, 0.2)])

    async def _mock_query(promql):
        return _prom_scalar(0.0)

    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)
    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)

    resp = await _client_get_resources(monkeypatch, range_param="7d")
    assert resp.status_code == 200
    # 验证 step 是 3600s
    assert all(call[3] == "3600s" for call in range_calls)
    # start 距 end 至少 604800 秒（7d）
    end = max(call[2] for call in range_calls)
    start = min(call[1] for call in range_calls)
    assert end - start >= 604800


@pytest.mark.asyncio
async def test_resources_custom_range_uses_provided_ts_and_auto_step(monkeypatch):
    """传 start_ts+end_ts 时：用传入时间窗，step 按跨度自动计算（6h → 300s），range 字段返回 'custom'。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)
    range_calls: list[tuple[float, float, str]] = []  # (start, end, step)

    async def _mock_query_range(promql, start, end, step):
        range_calls.append((start, end, step))
        return _prom_range([(1719700000, 0.2)])

    async def _mock_query(promql):
        return _prom_scalar(0.0)

    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)
    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)

    # 6h 跨度 → step 应该是 300s
    start = 1719700000
    end = start + 6 * 3600
    resp = await _client_get_resources(monkeypatch, range_param="1h",
                                       extra_query=f"&start_ts={start}&end_ts={end}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["range"] == "custom"
    # 所有 query_range 调用都用传入的 start/end
    assert all(call[0] == float(start) and call[1] == float(end) for call in range_calls)
    # 跨度 6h（21600s）→ step=300s
    assert all(call[2] == "300s" for call in range_calls)


@pytest.mark.asyncio
async def test_resources_custom_range_short_span_uses_60s_step(monkeypatch):
    """1h 跨度（≤2h）→ step=60s。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)
    range_calls: list[str] = []

    async def _mock_query_range(promql, start, end, step):
        range_calls.append(step)
        return _prom_range([(1719700000, 0.2)])

    async def _mock_query(promql):
        return _prom_scalar(0.0)

    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)
    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)

    start = 1719700000
    end = start + 3600  # 1h
    resp = await _client_get_resources(monkeypatch, range_param="1h",
                                       extra_query=f"&start_ts={start}&end_ts={end}")
    assert resp.status_code == 200
    assert all(s == "60s" for s in range_calls)


@pytest.mark.asyncio
async def test_resources_custom_range_long_span_uses_1800s_step(monkeypatch):
    """10d 跨度（≤14d）→ step=1800s。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)
    range_calls: list[str] = []

    async def _mock_query_range(promql, start, end, step):
        range_calls.append(step)
        return _prom_range([(1719700000, 0.2)])

    async def _mock_query(promql):
        return _prom_scalar(0.0)

    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)
    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)

    start = 1719700000
    end = start + 10 * 86400  # 10d
    resp = await _client_get_resources(monkeypatch, range_param="1h",
                                       extra_query=f"&start_ts={start}&end_ts={end}")
    assert resp.status_code == 200
    assert all(s == "1800s" for s in range_calls)


@pytest.mark.asyncio
async def test_resources_custom_range_end_before_start_returns_400(monkeypatch):
    """end_ts ≤ start_ts 返回 400。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)
    monkeypatch.setattr(observability.prometheus_client, "query", AsyncMock(return_value=None))
    monkeypatch.setattr(observability.prometheus_client, "query_range", AsyncMock(return_value=None))

    start = 1719700000
    end = start - 60  # end < start
    resp = await _client_get_resources(monkeypatch, range_param="1h",
                                       extra_query=f"&start_ts={start}&end_ts={end}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resources_custom_range_over_30d_returns_400(monkeypatch):
    """跨度 > 30d 返回 400。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)
    monkeypatch.setattr(observability.prometheus_client, "query", AsyncMock(return_value=None))
    monkeypatch.setattr(observability.prometheus_client, "query_range", AsyncMock(return_value=None))

    start = 1719700000
    end = start + 31 * 86400  # 31d
    resp = await _client_get_resources(monkeypatch, range_param="1h",
                                       extra_query=f"&start_ts={start}&end_ts={end}")
    assert resp.status_code == 400


async def _client_get_resources(monkeypatch, range_param: str = "1h", extra_query: str = ""):
    """辅助：通过 ASGI client 调 /resources。extra_query 形如 '&start_ts=...&end_ts=...'。"""
    from app.api import observability
    # 复用现有 conftest 的 client fixture 比较麻烦（需要 mock_db_session 等），
    # 这里直接构造最小化的 ASGI transport 调用
    from app.main import app
    from httpx import AsyncClient, ASGITransport

    # 旁路 get_current_user
    from app.core.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"/api/manager/observability/resources?range={range_param}{extra_query}")
    app.dependency_overrides.clear()
    return resp


# ═══════════════════════════════════════════════════
# /service-health 端点测试
# ═══════════════════════════════════════════════════


async def _client_get_service_health(monkeypatch, range_param: str = "1h", extra_query: str = ""):
    """辅助：通过 ASGI client 调 /service-health。"""
    from app.main import app
    from httpx import AsyncClient, ASGITransport
    from app.core.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: MagicMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"/api/manager/observability/service-health?range={range_param}{extra_query}")
    app.dependency_overrides.clear()
    return resp


@pytest.mark.asyncio
async def test_service_health_not_configured_returns_empty_state(monkeypatch):
    """prometheus_client.is_configured() 返回 False 时，返回 metrics_available=False 但 grafana_url 仍返回。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: False)
    monkeypatch.setattr(observability.prometheus_client, "query", AsyncMock(return_value=None))
    monkeypatch.setattr(observability.prometheus_client, "query_range", AsyncMock(return_value=None))
    monkeypatch.setattr(observability.settings, "grafana_external_url", "http://example.com:30090", raising=False)

    resp = await _client_get_service_health(monkeypatch)
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics_available"] is False
    assert data["overall"]["total_count"] == 6
    assert data["overall"]["up_count"] == 0
    assert data["items"] == []
    assert data["trend"] == []
    assert data["grafana_url"] == "http://example.com:30090"
    assert data["grafana_dashboard_uid"] == "unionagents-overview"


@pytest.mark.asyncio
async def test_service_health_returns_aggregated_data(monkeypatch):
    """全查询成功时，返回 6 个服务的状态/延迟/可用率/SLO。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)

    async def _mock_query(promql: str):
        # 即时查询：根据 PromQL 关键词返回不同值（PromQL 已含 * 1000 / * 100，返回最终值）
        if "probe_success" in promql and "avg_over_time" not in promql:
            return _prom_scalar(1.0)  # 全部 ok
        if "avg_over_time(probe_success" in promql:
            return _prom_scalar(100.0)  # 100% 可用率（PromQL 已 * 100）
        if "probe_duration_seconds" in promql and "quantile_over_time" not in promql:
            return _prom_scalar(12.0)  # 12ms（PromQL 已 * 1000）
        if "quantile_over_time(0.50" in promql:
            return _prom_scalar(10.0)  # 10ms
        if "quantile_over_time(0.95" in promql:
            return _prom_scalar(25.0)  # 25ms
        if promql.startswith("up{"):
            return _prom_scalar(1.0)
        return None

    async def _mock_query_range(promql, start, end, step):
        # 趋势：返回 3 个点
        return _prom_range([(1719700000, 12.0), (1719700060, 15.0), (1719700120, 11.0)])

    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)
    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)

    resp = await _client_get_service_health(monkeypatch)
    assert resp.status_code == 200
    data = resp.json()
    assert data["metrics_available"] is True
    assert data["range"] == "1h"
    assert data["overall"]["total_count"] == 6
    assert data["overall"]["up_count"] == 6  # 全部 ok
    assert data["overall"]["avg_p95_ms"] == 25.0  # 0.025s * 1000
    assert data["overall"]["avg_uptime_pct"] == 100.0
    assert len(data["items"]) == 6
    # 验证 item 字段
    manager = next(it for it in data["items"] if it["name"] == "Manager")
    assert manager["status"] == "ok"
    assert manager["latency_ms"] == 12.0
    assert manager["p50_ms"] == 10.0
    assert manager["p95_ms"] == 25.0
    assert manager["uptime_pct"] == 100.0
    assert manager["slo_met"] is True  # 100% >= 99.5 且 25ms < 500ms
    # 趋势：每个服务 3 个点（PG/MinIO 是 tcp probe，但本测试 mock 也不影响）
    assert len(data["trend"]) == 3
    assert data["trend"][0]["ts"] == 1719700000


@pytest.mark.asyncio
async def test_service_health_partial_failure_marks_down(monkeypatch):
    """某服务 probe_success=None 时，status 降级为 down，overall up_count 减少。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)

    async def _mock_query(promql: str):
        # Manager 的 probe 全部失败（返回 None）
        if "manager.unionagents" in promql:
            return None
        if "probe_success" in promql and "avg_over_time" not in promql:
            return _prom_scalar(1.0)
        if "avg_over_time(probe_success" in promql:
            return _prom_scalar(100.0)
        if "probe_duration_seconds" in promql and "quantile_over_time" not in promql:
            return _prom_scalar(12.0)
        if "quantile_over_time(0.50" in promql:
            return _prom_scalar(10.0)
        if "quantile_over_time(0.95" in promql:
            return _prom_scalar(25.0)
        return None

    async def _mock_query_range(promql, start, end, step):
        if "manager.unionagents" in promql:
            return None
        return _prom_range([(1719700000, 12.0)])

    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)
    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)

    resp = await _client_get_service_health(monkeypatch)
    data = resp.json()
    assert data["overall"]["up_count"] == 5  # Manager down，其他 5 个 ok
    manager = next(it for it in data["items"] if it["name"] == "Manager")
    assert manager["status"] == "down"
    assert manager["latency_ms"] is None
    assert manager["slo_met"] is False


@pytest.mark.asyncio
async def test_service_health_custom_range_uses_provided_ts(monkeypatch):
    """传 start_ts+end_ts 时用传入时间窗，step 按跨度自动计算，range 字段返回 'custom'。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)
    range_calls: list[tuple[float, float, str]] = []

    async def _mock_query_range(promql, start, end, step):
        range_calls.append((start, end, step))
        return _prom_range([(1719700000, 12.0)])

    async def _mock_query(promql):
        return _prom_scalar(0.0)

    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)
    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)

    # 6h 跨度 → step=300s
    start = 1719700000
    end = start + 6 * 3600
    resp = await _client_get_service_health(monkeypatch, range_param="1h",
                                            extra_query=f"&start_ts={start}&end_ts={end}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["range"] == "custom"
    assert all(call[0] == float(start) and call[1] == float(end) for call in range_calls)
    assert all(call[2] == "300s" for call in range_calls)


@pytest.mark.asyncio
async def test_service_health_preset_range_7d_uses_3600s_step(monkeypatch):
    """preset range=7d 时 step=3600s。"""
    from app.api import observability

    monkeypatch.setattr(observability.prometheus_client, "is_configured", lambda: True)
    range_calls: list[str] = []

    async def _mock_query_range(promql, start, end, step):
        range_calls.append(step)
        return _prom_range([(1719700000, 12.0)])

    async def _mock_query(promql):
        return _prom_scalar(0.0)

    monkeypatch.setattr(observability.prometheus_client, "query_range", _mock_query_range)
    monkeypatch.setattr(observability.prometheus_client, "query", _mock_query)

    resp = await _client_get_service_health(monkeypatch, range_param="7d")
    assert resp.status_code == 200
    assert all(s == "3600s" for s in range_calls)


# ── /top-agents 端点测试 ──


def _mock_langfuse_list_traces(counts: dict[str, int]):
    """构造 mock list_traces：按 user_id 返回 {meta: {totalItems: N}}。"""
    async def _mock(*, user_id=None, from_ts=None, to_ts=None, limit=1, **_):
        if user_id is None:
            return {"data": [], "meta": {"totalItems": 0}}
        return {
            "data": [],
            "meta": {"totalItems": counts.get(user_id, 0)},
        }
    return _mock


async def _client_get_top_agents(monkeypatch, db_mock, limit=5, days=30):
    """辅助：通过 ASGI client 调 /top-agents。"""
    from app.api import observability
    from app.main import app
    from httpx import AsyncClient, ASGITransport
    from app.core.auth import get_current_user
    from pkg.common.database import get_db

    app.dependency_overrides[get_current_user] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: db_mock

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(
            "/api/manager/observability/top-agents",
            params={"limit": limit, "days": days},
        )
    app.dependency_overrides.clear()
    return resp


def _make_top_agents_db(agents: list[tuple[str, str]]):
    """构造 mock db，db.execute(...) 返回 agents 列表（id, name 元组）。"""
    result = MagicMock()
    result.all.return_value = agents
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_top_agents_not_configured_returns_empty(monkeypatch):
    """Langfuse 未配置时返回 langfuse_configured=False + 空 items。"""
    from app.api import observability

    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: False)
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        AsyncMock(return_value=None),
    )
    db = _make_top_agents_db([("a1", "Agent1")])

    resp = await _client_get_top_agents(monkeypatch, db)
    assert resp.status_code == 200
    data = resp.json()
    assert data["langfuse_configured"] is False
    assert data["items"] == []


@pytest.mark.asyncio
async def test_top_agents_no_published_agents_returns_empty(monkeypatch):
    """无 PUBLISHED agent 时返回空 items。"""
    from app.api import observability

    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        _mock_langfuse_list_traces({"a1": 10}),
    )
    db = _make_top_agents_db([])  # 无 agent

    resp = await _client_get_top_agents(monkeypatch, db)
    assert resp.status_code == 200
    data = resp.json()
    assert data["langfuse_configured"] is True
    assert data["items"] == []


@pytest.mark.asyncio
async def test_top_agents_returns_sorted_by_count_desc(monkeypatch):
    """3 个 agent 不同 trace 数，按 count 降序返回 top limit。"""
    from app.api import observability

    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        _mock_langfuse_list_traces({"a1": 5, "a2": 30, "a3": 12}),
    )
    db = _make_top_agents_db([("a1", "Agent1"), ("a2", "Agent2"), ("a3", "Agent3")])

    resp = await _client_get_top_agents(monkeypatch, db, limit=3)
    assert resp.status_code == 200
    data = resp.json()
    assert data["langfuse_configured"] is True
    items = data["items"]
    assert len(items) == 3
    # 按对话次数降序
    assert items[0]["agent_id"] == "a2"
    assert items[0]["conversation_count"] == 30
    assert items[1]["agent_id"] == "a3"
    assert items[1]["conversation_count"] == 12
    assert items[2]["agent_id"] == "a1"
    assert items[2]["conversation_count"] == 5
    # total_tokens 固定 0（本端点不拉 trace 详情）
    assert all(it["total_tokens"] == 0 for it in items)


@pytest.mark.asyncio
async def test_top_agents_limit_truncates(monkeypatch):
    """limit=2 时只返回 top 2，其余截断。"""
    from app.api import observability

    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        observability.langfuse_client,
        "list_traces",
        _mock_langfuse_list_traces({"a1": 5, "a2": 30, "a3": 12}),
    )
    db = _make_top_agents_db([("a1", "Agent1"), ("a2", "Agent2"), ("a3", "Agent3")])

    resp = await _client_get_top_agents(monkeypatch, db, limit=2)
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["items"][0]["agent_id"] == "a2"
    assert data["items"][1]["agent_id"] == "a3"


@pytest.mark.asyncio
async def test_top_agents_langfuse_failure_falls_back_to_zero(monkeypatch):
    """某 agent 的 list_traces 返回 None（失败）时 count 回退 0，不影响其他 agent。"""
    from app.api import observability

    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    async def _mock(*, user_id=None, **_):
        if user_id == "a2":
            return None  # 模拟失败
        return {"data": [], "meta": {"totalItems": 15 if user_id == "a1" else 8}}

    monkeypatch.setattr(observability.langfuse_client, "list_traces", _mock)
    db = _make_top_agents_db([("a1", "Agent1"), ("a2", "Agent2"), ("a3", "Agent3")])

    resp = await _client_get_top_agents(monkeypatch, db, limit=5)
    data = resp.json()
    items = {it["agent_id"]: it["conversation_count"] for it in data["items"]}
    assert items == {"a1": 15, "a2": 0, "a3": 8}


@pytest.mark.asyncio
async def test_top_agents_passes_time_window(monkeypatch):
    """days 参数转化为 from_ts/to_ts 传给 list_traces。"""
    from app.api import observability

    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: True)

    captured: list[dict] = []

    async def _mock(*, user_id=None, from_ts=None, to_ts=None, **_):
        captured.append({"user_id": user_id, "from_ts": from_ts, "to_ts": to_ts})
        return {"data": [], "meta": {"totalItems": 0}}

    monkeypatch.setattr(observability.langfuse_client, "list_traces", _mock)
    db = _make_top_agents_db([("a1", "Agent1")])

    resp = await _client_get_top_agents(monkeypatch, db, days=7)
    assert resp.status_code == 200
    assert len(captured) == 1
    # 验证传了 from_ts 和 to_ts（ISO 8601 字符串）
    assert captured[0]["from_ts"] is not None
    assert captured[0]["to_ts"] is not None
    assert "T" in captured[0]["from_ts"]  # ISO 格式含 T


# ═══════════════════════════════════════════════════
# /usage 端点测试
# ═══════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_usage_returns_by_group_with_enriched_names(monkeypatch):
    """HTTP /usage 返回 by_group 数组，按 total_tokens 降序，name 已 enrich。"""
    from unittest.mock import MagicMock
    from app.api import observability
    from app.main import app
    from httpx import AsyncClient, ASGITransport
    from app.core.auth import get_current_user
    from pkg.common.database import get_db

    async def _mock_spend_logs(*, start_date=None, user=None, limit=1000, **_):
        return {"data": [
            {"api_key": "key-aaa", "model": "gpt-4", "prompt_tokens": 100, "completion_tokens": 50, "spend": 0.01, "startTime": "2026-07-01T10:00:00Z"},
            {"api_key": "key-bbb", "model": "claude-3", "prompt_tokens": 200, "completion_tokens": 100, "spend": 0.02, "startTime": "2026-07-01T11:00:00Z"},
        ]}
    monkeypatch.setattr(observability.litellm_client, "spend_logs", _mock_spend_logs)

    async def _mock_resolve_keys(db, agent_ids):
        return {
            "key-aaa": {"agent_id": "a1", "name": "Agent1", "group_id": "g1"},
            "key-bbb": {"agent_id": "a2", "name": "Agent2", "group_id": "g2"},
        }
    monkeypatch.setattr(observability, "_resolve_key_ids_for_agents", _mock_resolve_keys)

    async def _mock_resolve_group_names(db, gids):
        return {"g1": "GroupA", "g2": "GroupB"}
    monkeypatch.setattr(observability, "_resolve_group_names", _mock_resolve_group_names)

    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: False)

    app.dependency_overrides[get_current_user] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/manager/observability/usage?days=30")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "by_group" in data
    by_group = data["by_group"]
    assert len(by_group) == 2
    # g2 (300 tokens) 在前，g1 (150 tokens) 在后
    assert by_group[0]["group_id"] == "g2"
    assert by_group[0]["name"] == "GroupB"
    assert by_group[0]["total_tokens"] == 300
    assert by_group[0]["total_cost"] == 0.02
    assert by_group[1]["group_id"] == "g1"
    assert by_group[1]["name"] == "GroupA"
    assert by_group[1]["total_tokens"] == 150
    assert by_group[1]["total_cost"] == 0.01


@pytest.mark.asyncio
async def test_get_usage_merges_dify_trace_into_by_agent(monkeypatch):
    """HTTP /usage 注入 Dify fake log 后，by_agent / by_model / by_group / today_tokens / monthly_cost 全部含 Dify 部分。"""
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from app.api import observability
    from app.main import app
    from httpx import AsyncClient, ASGITransport
    from app.core.auth import get_current_user
    from pkg.common.database import get_db

    # 1 条 Hermes spend_log（agent a1, group g1, model gpt-4）
    now_iso = datetime.now(timezone.utc).isoformat()
    async def _mock_spend_logs(*, start_date=None, user=None, limit=1000, **_):
        return {"data": [
            {"api_key": "key-aaa", "model": "gpt-4", "prompt_tokens": 100, "completion_tokens": 50,
             "spend": 0.01, "startTime": now_iso},
        ]}
    monkeypatch.setattr(observability.litellm_client, "spend_logs", _mock_spend_logs)

    async def _mock_resolve_keys(db, agent_ids):
        return {"key-aaa": {"agent_id": "a1", "name": "Hermes Agent", "group_id": "g1"}}
    monkeypatch.setattr(observability, "_resolve_key_ids_for_agents", _mock_resolve_keys)

    async def _mock_resolve_group_names(db, gids):
        return {"g1": "Hermes Group", "g2": "Dify Group 2", "g3": "Dify Group 3"}
    monkeypatch.setattr(observability, "_resolve_group_names", _mock_resolve_group_names)

    # mock _fetch_dify_trace_details 返回 2 条 fake log（不同 agent + 不同 model + 不同 session_id）
    async def _mock_fetch_dify(db, start_dt, end_dt, agent_ids, user_group_id):
        return [
            {"agent_id": "a2", "agent_name": "Dify Agent 2", "group_id": "g2",
             "model": "deepseek-chat", "prompt_tokens": 200, "completion_tokens": 100,
             "api_key": "", "session_id": "sess-a2", "spend": 0.005, "startTime": now_iso},
            {"agent_id": "a3", "agent_name": "Dify Agent 3", "group_id": "g3",
             "model": "gpt-4o", "prompt_tokens": 300, "completion_tokens": 150,
             "api_key": "", "session_id": "sess-a3", "spend": 0.008, "startTime": now_iso},
        ]
    monkeypatch.setattr(observability, "_fetch_dify_trace_details", _mock_fetch_dify)

    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: False)

    app.dependency_overrides[get_current_user] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/manager/observability/usage?days=30")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()

    # by_agent 含 3 条（a1 Hermes + a2 + a3 Dify）
    by_agent = data["by_agent"]
    assert len(by_agent) == 3
    by_agent_map = {a["agent_id"]: a for a in by_agent}
    assert by_agent_map["a1"]["total_tokens"] == 150  # 100+50
    assert by_agent_map["a2"]["total_tokens"] == 300  # 200+100
    assert by_agent_map["a3"]["total_tokens"] == 450  # 300+150
    assert by_agent_map["a2"]["name"] == "Dify Agent 2"

    # by_model 含 3 个 model
    by_model = data["by_model"]
    assert len(by_model) == 3
    by_model_map = {m["model"]: m for m in by_model}
    assert by_model_map["gpt-4"]["total_tokens"] == 150
    assert by_model_map["deepseek-chat"]["total_tokens"] == 300
    assert by_model_map["gpt-4o"]["total_tokens"] == 450

    # by_group 含 3 个 group
    by_group = data["by_group"]
    assert len(by_group) == 3
    by_group_map = {g["group_id"]: g for g in by_group}
    assert by_group_map["g2"]["name"] == "Dify Group 2"
    assert by_group_map["g3"]["name"] == "Dify Group 3"

    # today_tokens 含 Dify 部分（全部 startTime 是今天，所以都算 today）
    # 150 + 300 + 450 = 900
    assert data["today_tokens"] == 900
    # monthly_tokens 同 today（因为 startTime 都是今天，落在本月）
    assert data["monthly_tokens"] == 900
    # monthly_cost = (0.01 + 0.005 + 0.008) USD * spend_usd_to_cny
    from pkg.common.config import settings
    expected_cost_cny = round(0.023 * float(settings.spend_usd_to_cny or 7.0), 2)
    assert data["monthly_cost"] == pytest.approx(expected_cost_cny, abs=1e-6)


@pytest.mark.asyncio
async def test_get_usage_filters_failure_status_logs(monkeypatch):
    """status='failure' 的 LiteLLM spend_log（如 ProxyModelNotFoundError）不计入统计：
    by_model 不含失败 log 的 model 名（避免污染模型维度），by_agent conversation_count 不含失败请求，
    today/monthly tokens 不含失败 log 的 0 token（无影响但语义清晰）。
    Dify fake log 无 status 字段，不受过滤影响。
    """
    from unittest.mock import MagicMock
    from datetime import datetime, timezone
    from app.api import observability
    from app.main import app
    from httpx import AsyncClient, ASGITransport
    from app.core.auth import get_current_user
    from pkg.common.database import get_db

    now_iso = datetime.now(timezone.utc).isoformat()

    # 3 条 spend_log：1 条成功 + 2 条失败（其中 1 条 model 名输错）
    async def _mock_spend_logs(*, start_date=None, user=None, limit=1000, **_):
        return {"data": [
            # 成功 log
            {"api_key": "key-aaa", "model": "deepseek-chat", "prompt_tokens": 100,
             "completion_tokens": 50, "spend": 0.01, "startTime": now_iso,
             "status": "success", "agent_id": "a1"},
            # 失败 log 1：model 名输错（ProxyModelNotFoundError）
            {"api_key": "litellm_proxy_master_key", "model": "agent-test-deepseek-chat",
             "prompt_tokens": 0, "completion_tokens": 0, "spend": 0.0,
             "startTime": now_iso, "status": "failure", "agent_id": "test-agent-xyz"},
            # 失败 log 2：正常 model 名但调用失败
            {"api_key": "litellm_proxy_master_key", "model": "deepseek-chat",
             "prompt_tokens": 0, "completion_tokens": 0, "spend": 0.0,
             "startTime": now_iso, "status": "failure", "agent_id": "test-agent-xyz"},
        ]}
    monkeypatch.setattr(observability.litellm_client, "spend_logs", _mock_spend_logs)

    async def _mock_resolve_keys(db, agent_ids):
        return {}
    monkeypatch.setattr(observability, "_resolve_key_ids_for_agents", _mock_resolve_keys)

    async def _mock_resolve_group_names(db, gids):
        return {}
    monkeypatch.setattr(observability, "_resolve_group_names", _mock_resolve_group_names)

    async def _mock_fetch_dify(db, start_dt, end_dt, agent_ids, user_group_id):
        return []  # 无 Dify trace
    monkeypatch.setattr(observability, "_fetch_dify_trace_details", _mock_fetch_dify)

    monkeypatch.setattr(observability.langfuse_client, "is_configured", lambda: False)

    app.dependency_overrides[get_current_user] = lambda: MagicMock()
    app.dependency_overrides[get_db] = lambda: MagicMock()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/manager/observability/usage?days=30")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()

    # by_model 只含 1 个 model（deepseek-chat），失败 log 的 "agent-test-deepseek-chat" 不出现
    by_model = data["by_model"]
    model_names = [m["model"] for m in by_model]
    assert "agent-test-deepseek-chat" not in model_names
    assert "deepseek-chat" in model_names
    # deepseek-chat 的 token 只算成功 log 的 150，失败 log 的 0 token 不影响
    deepseek_bucket = next(m for m in by_model if m["model"] == "deepseek-chat")
    assert deepseek_bucket["total_tokens"] == 150

    # by_agent 只含 a1（1 次 150 tokens），test-agent-xyz 不出现（2 条失败 log 被过滤）
    by_agent = data["by_agent"]
    agent_ids = [a["agent_id"] for a in by_agent]
    assert "test-agent-xyz" not in agent_ids
    assert "a1" in agent_ids
    a1_bucket = next(a for a in by_agent if a["agent_id"] == "a1")
    assert a1_bucket["conversation_count"] == 1
    assert a1_bucket["total_tokens"] == 150

    # today_tokens / monthly_tokens 只算成功 log 的 150
    assert data["today_tokens"] == 150
    assert data["monthly_tokens"] == 150