"""DeepseekTraceSource 测试：解析 DSH 会话轨迹（session.jsonl.zstd）获取 token 用量。

口径与其它源一致：全量 = input + output + cacheRead + reasoning（DSH 轨迹的 usage 字段），
按消息完成时刻（time，epoch 毫秒）转秒分桶；当日从本地 0 点起。
"""
import json
import time

import pytest
import zstandard

from token_running.source import UsageEvent
from token_running.deepseek_trace import DeepseekTraceSource


def _local_midnight(ts: float) -> int:
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


DAY_START = _local_midnight(1_700_000_000)


def _write_zstd(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor()
    with open(path, "wb") as fh:
        data = cctx.compress(("\n".join(json.dumps(l) for l in lines) + "\n").encode("utf-8"))
        fh.write(data)


def _assistant_msg(epoch_sec, usage):
    return {
        "type": "assistant/message",
        "time": epoch_sec * 1000,
        "data": {"turn": 1, "step": 1, "message": {"role": "assistant", "content": []}, "usage": usage},
    }


def test_poll_parses_usage_and_ignores_other_events(tmp_path):
    lines = [
        {"type": "session", "id": "s1"},
        _assistant_msg(DAY_START + 10, {"inputTokens": 10, "outputTokens": 20, "cacheReadTokens": 30, "reasoningTokens": 40}),
        _assistant_msg(DAY_START + 11, {"inputTokens": 1, "outputTokens": 2, "cacheReadTokens": 3, "reasoningTokens": 4}),
        {"type": "tool/call", "time": (DAY_START + 12) * 1000, "data": {}},
    ]
    _write_zstd(tmp_path / "session.jsonl.zstd", lines)
    src = DeepseekTraceSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    events = src.poll()
    assert events == [UsageEvent(DAY_START + 10, 100), UsageEvent(DAY_START + 11, 10)]  # 10+20+30+40=100
    assert src.poll() == []  # 增量


def test_poll_incremental_after_append(tmp_path):
    f = tmp_path / "session.jsonl.zstd"
    _write_zstd(f, [_assistant_msg(DAY_START + 10, {"inputTokens": 10, "outputTokens": 0, "cacheReadTokens": 0, "reasoningTokens": 0})])
    src = DeepseekTraceSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    assert src.poll() == [UsageEvent(DAY_START + 10, 10)]
    # 追加新帧（zstd 支持 concat 多帧）
    cctx = zstandard.ZstdCompressor()
    extra = cctx.compress((json.dumps(_assistant_msg(DAY_START + 12, {"inputTokens": 5, "outputTokens": 5, "cacheReadTokens": 0, "reasoningTokens": 0})) + "\n").encode())
    with open(f, "ab") as fh:
        fh.write(extra)
    assert src.poll() == [UsageEvent(DAY_START + 12, 10)]
    assert src.poll() == []


def test_daily_total_and_online(tmp_path):
    _write_zstd(tmp_path / "session.jsonl.zstd", [
        _assistant_msg(DAY_START - 1, {"inputTokens": 100, "outputTokens": 0, "cacheReadTokens": 0, "reasoningTokens": 0}),
        _assistant_msg(DAY_START, {"inputTokens": 50, "outputTokens": 50, "cacheReadTokens": 0, "reasoningTokens": 0}),
    ])
    src = DeepseekTraceSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    src.poll()
    assert src.daily_total() == 100
    assert src.total() == 200  # 全时段含昨日
    assert src.online() is True


def test_offline_when_dir_missing(tmp_path):
    src = DeepseekTraceSource(tmp_path / "nope", now_fn=lambda: DAY_START + 3600)
    assert src.online() is False
    assert src.poll() == []
    assert src.daily_total() == 0
    assert src.total() == 0
