"""CodexSource 回归测试：rollout JSONL 增量解析、新文件发现、半行安全、模型按文件隔离。"""
import json
import time
import pytest

from token_running.codex_source import CodexSource
from token_running.source import UsageEvent


def _local_midnight(ts: float) -> int:
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


DAY_START = _local_midnight(1_700_000_000)


def _iso(epoch_sec):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch_sec))


def _meta(model: str, ts: float) -> str:
    return json.dumps({"timestamp": _iso(ts), "type": "session_meta",
                       "payload": {"base_instructions": {"provenance": {"model": model}}},
                       "config": {}, "cwd": "/tmp", "session_id": "x"})


def _token_count(ts: float, input_all: int, cached: int, output: int, cw: int = 0) -> str:
    """构造 token_count 事件行（last_token_usage 即本次请求增量）。"""
    payload = {
        "type": "token_count",
        "info": {
            "total_token_usage": {"input_tokens": input_all, "cached_input_tokens": cached,
                                  "cache_write_input_tokens": cw, "output_tokens": output,
                                  "reasoning_output_tokens": 0, "total_tokens": input_all + output},
            "last_token_usage": {"input_tokens": input_all, "cached_input_tokens": cached,
                                 "cache_write_input_tokens": cw, "output_tokens": output,
                                 "reasoning_output_tokens": 0, "total_tokens": input_all + output},
        },
    }
    return json.dumps({"timestamp": _iso(ts), "type": "event_msg", "payload": payload})


def _irrelevant(ts: float) -> str:
    return json.dumps({"timestamp": _iso(ts), "type": "event_msg",
                       "payload": {"type": "task_complete", "turn_id": "t"}})


def _write(tmp_path, name: str, lines: list[str]) -> None:
    p = tmp_path / "2026" / "08" / "18"
    p.mkdir(parents=True, exist_ok=True)
    (p / name).write_text("".join(l + "\n" for l in lines), encoding="utf-8")


def test_incremental_parse_and_breakdown(tmp_path):
    _write(tmp_path, "rollout-a.jsonl", [
        _meta("gpt-5.6-terra", DAY_START + 1),
        _token_count(DAY_START + 10, 17790, 3840, 140),
    ])
    src = CodexSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    evs = src.poll()
    assert len(evs) == 1
    e = evs[0]
    assert e.tokens == 17790 - 3840 + 140 + 3840 + 0  # 付费输入+输出+缓存命中+缓存写入
    assert e.model == "gpt-5.6-terra"
    assert e.input_t == 17790 - 3840
    assert e.cache_read_t == 3840
    assert e.output_t == 140
    assert e.cache_create_t == 0
    # 增量：第二次 poll 无新行
    assert src.poll() == []
    # daily_total
    assert src.daily_total() == e.tokens
    assert src.total() == e.tokens


def test_new_rollout_file_discovered(tmp_path):
    _write(tmp_path, "rollout-a.jsonl", [
        _meta("gpt-5.6-terra", DAY_START + 1),
        _token_count(DAY_START + 10, 100, 0, 5),
    ])
    src = CodexSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    src.poll()
    _write(tmp_path, "rollout-b.jsonl", [
        _meta("gpt-5.1", DAY_START + 11),
        _token_count(DAY_START + 20, 200, 0, 7),
    ])
    evs = src.poll()
    assert [e.tokens for e in evs] == [200 + 7], "new rollout must be discovered"
    assert evs[0].model == "gpt-5.1", "model must come from its own file (no cross-pollution)"


def test_partial_line_safety(tmp_path):
    _write(tmp_path, "rollout-a.jsonl", [
        _meta("gpt-5.6-terra", DAY_START + 1),
        _token_count(DAY_START + 10, 100, 0, 5),
    ])
    src = CodexSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    src.poll()
    # 追加一个不完整行（模拟 Codex 正在写入）
    p = tmp_path / "2026" / "08" / "18" / "rollout-a.jsonl"
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"timestamp":"' + _iso(DAY_START + 30) + '","type":"event_msg"')
    assert src.poll() == [], "partial line must not be parsed yet"
    # 补全后下一轮读到
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(',"payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":50,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":3,"reasoning_output_tokens":0,"total_tokens":53}}}}\n')
    evs = src.poll()
    assert [e.tokens for e in evs] == [53]