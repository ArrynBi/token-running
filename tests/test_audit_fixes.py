"""审查修复回归测试：费用明细保留、新文件发现、渠道费用过滤、窗口口径增量。"""
import json
import time
import zstandard

from token_running.source import UsageEvent
from token_running.jsonl_source import JsonlSource
from token_running.deepseek_trace import DeepseekTraceSource


def _local_midnight(ts: float) -> int:
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


DAY_START = _local_midnight(1_700_000_000)


def _iso(epoch_sec):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch_sec))


def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _assistant(ts_iso, usage):
    return {"type": "assistant", "timestamp": ts_iso, "message": {"usage": usage, "model": "deepseek-v4-flash"}}


def _write_zstd(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    cctx = zstandard.ZstdCompressor()
    with open(path, "wb") as fh:
        data = cctx.compress(("\n".join(json.dumps(l) for l in lines) + "\n").encode("utf-8"))
        fh.write(data)


def _dsh_msg(epoch_sec, usage):
    return {"type": "assistant/message", "time": epoch_sec * 1000,
            "data": {"turn": 1, "step": 1, "message": {"role": "assistant", "content": []}, "usage": usage}}


def _dsh_header(model):
    return {"type": "request/header", "data": {"header": {"config": {"model": model}}}}


def test_poll_preserves_breakdown_for_cost(tmp_path):
    # 聚合后事件必须保留 token 明细（费用计算依赖）
    _write_jsonl(tmp_path / "a.jsonl", [
        _assistant(_iso(DAY_START + 10), {"input_tokens": 10, "output_tokens": 20,
                                          "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40}),
        _assistant(_iso(DAY_START + 10), {"input_tokens": 5, "output_tokens": 5,
                                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
    ])
    src = JsonlSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    evs = src.poll()
    assert len(evs) == 1, "same second should aggregate"
    e = evs[0]
    assert e.tokens == 110, e.tokens
    assert e.input_t == 15 and e.output_t == 25 and e.cache_read_t == 30, (e.input_t, e.output_t, e.cache_read_t)
    assert e.model == "deepseek-v4-flash", e.model


def test_new_jsonl_file_discovered(tmp_path):
    # 运行期间新建的会话文件应被 poll 发现
    _write_jsonl(tmp_path / "a.jsonl", [_assistant(_iso(DAY_START + 10), {"input_tokens": 1, "output_tokens": 0,
                                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})])
    src = JsonlSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    src.poll()
    # 新建文件
    _write_jsonl(tmp_path / "b.jsonl", [_assistant(_iso(DAY_START + 11), {"input_tokens": 5, "output_tokens": 0,
                                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})])
    evs = src.poll()
    assert [e.tokens for e in evs] == [5], "new file events must be discovered"


def test_partial_line_not_lost(tmp_path):
    # 半行（未写完的 JSON）不应推进偏移，后续补全后可解析
    f = tmp_path / "a.jsonl"
    _write_jsonl(f, [_assistant(_iso(DAY_START + 10), {"input_tokens": 1, "output_tokens": 0,
                                                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})])
    src = JsonlSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    src.poll()
    # 追加一个不完整行
    with open(f, "a", encoding="utf-8") as fh:
        fh.write('{"type": "assistant", "timestamp": "' + _iso(DAY_START + 12) + '"')
    assert src.poll() == [], "partial line must not be consumed"
    # 补全该行
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(', "message": {"usage": {"input_tokens": 9}, "model": "m"}}' + "\n")
    evs = src.poll()
    assert [e.tokens for e in evs] == [9], "completed line must be parsed"


def test_new_dsh_trace_discovered(tmp_path):
    _write_zstd(tmp_path / "session-a.jsonl.zstd", [_dsh_header("deepseek-v4-flash"),
                 _dsh_msg(DAY_START + 10, {"inputTokens": 1, "outputTokens": 0, "cacheReadTokens": 0, "reasoningTokens": 0})])
    src = DeepseekTraceSource(tmp_path, now_fn=lambda: DAY_START + 3600)
    src.poll()
    # 新建轨迹文件
    _write_zstd(tmp_path / "session-b.jsonl.zstd", [_dsh_header("deepseek-v4-flash"),
                 _dsh_msg(DAY_START + 11, {"inputTokens": 5, "outputTokens": 0, "cacheReadTokens": 0, "reasoningTokens": 0})])
    evs = src.poll()
    assert [e.tokens for e in evs] == [5], "new trace must be discovered"