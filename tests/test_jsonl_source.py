"""JsonlSource 测试：直接解析 ~/.claude/projects/**/*.jsonl 实现秒级 token 消耗。

口径与 TokenSource 一致：含缓存全量 = input+output+cache_read+cache_creation，
按 created_at（epoch 秒，由 ISO 时间戳转换）分桶；当日从本地 0 点起。
"""
import json
import time

from token_running.source import UsageEvent
from token_running.jsonl_source import JsonlSource


def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _assistant(ts_iso, usage):
    return {"type": "assistant", "timestamp": ts_iso, "message": {"usage": usage}, "uuid": "u" + ts_iso}


# 固定本地"今日 0 点"epoch 秒（与 TokenSource 测试同法）
def _local_midnight(ts: float) -> int:
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


DAY_START = _local_midnight(1_700_000_000)


def _iso(epoch_sec):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch_sec))


def test_poll_parses_assistant_usage_only(tmp_path):
    # assistant 行（含 usage）应解析；user/非 assistant 行应忽略
    _write_jsonl(tmp_path / "a.jsonl", [
        {"type": "user", "timestamp": _iso(DAY_START + 10), "message": {"content": "hi"}},
        _assistant(_iso(DAY_START + 10), {"input_tokens": 10, "output_tokens": 20,
                                           "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}),
        _assistant(_iso(DAY_START + 11), {"input_tokens": 1, "output_tokens": 2,
                                           "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4}),
    ])
    src = JsonlSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    events = src.poll()
    assert [e.tokens for e in events] == [100, 10]  # 10+20+30+40=100, 1+2+3+4=10
    assert events[0].input_t == 10 and events[0].output_t == 20
    assert events[0].cache_read_t == 30 and events[0].cache_create_t == 40
    assert src.poll() == []  # 第二次无增量


def test_poll_incremental_after_append(tmp_path):
    f = tmp_path / "a.jsonl"
    _write_jsonl(f, [_assistant(_iso(DAY_START + 10), {"input_tokens": 10, "output_tokens": 0,
                                                       "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})])
    src = JsonlSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    evs = src.poll()
    assert [e.tokens for e in evs] == [10] and evs[0].input_t == 10
    # 追加新行 → 只返回新事件
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_assistant(_iso(DAY_START + 12), {"input_tokens": 5, "output_tokens": 5,
                                                               "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})) + "\n")
    evs2 = src.poll()
    assert [e.tokens for e in evs2] == [10] and evs2[0].input_t == 5
    assert src.poll() == []


def test_poll_subdirectory_files_and_missing_usage(tmp_path):
    # 子目录文件也要扫；无 usage 的 assistant 行忽略
    _write_jsonl(tmp_path / "sub" / "agent.jsonl", [
        _assistant(_iso(DAY_START + 20), {"input_tokens": 7, "output_tokens": 3,
                                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
        {"type": "assistant", "timestamp": _iso(DAY_START + 21), "message": {"content": "no usage"}},
    ])
    src = JsonlSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    evs = src.poll()
    assert [e.tokens for e in evs] == [10] and evs[0].input_t == 7


def test_daily_total_and_online(tmp_path):
    _write_jsonl(tmp_path / "a.jsonl", [
        _assistant(_iso(DAY_START - 1), {"input_tokens": 100, "output_tokens": 0,
                                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),  # 昨日
        _assistant(_iso(DAY_START), {"input_tokens": 50, "output_tokens": 50,
                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),  # 今日
    ])
    src = JsonlSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    src.poll()
    assert src.daily_total() == 100  # 昨日不计
    assert src.online() is True


def test_offline_when_dir_missing(tmp_path):
    src = JsonlSource(tmp_path / "nope", now_fn=lambda: DAY_START + 3600)
    assert src.online() is False
    assert src.poll() == []
    assert src.daily_total() == 0


def test_total_includes_all_days(tmp_path):
    # total() 是全时段累计：昨日 + 今日都计入
    _write_jsonl(tmp_path / "a.jsonl", [
        _assistant(_iso(DAY_START - 86400), {"input_tokens": 100, "output_tokens": 0,
                                              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
        _assistant(_iso(DAY_START), {"input_tokens": 50, "output_tokens": 50,
                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
    ])
    src = JsonlSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    src.poll()
    assert src.daily_total() == 100      # 今日
    assert src.total() == 200            # 全时段（含昨日 100）


def test_total_empty_when_offline(tmp_path):
    src = JsonlSource(tmp_path / "nope", now_fn=lambda: DAY_START + 3600)
    assert src.total() == 0
