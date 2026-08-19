"""OpencodeSource 回归测试：opencode.db message 表增量解析、breakdown、跨天。"""
import json
import sqlite3
import time

from token_running.opencode_source import OpencodeSource
from token_running.source import UsageEvent


def _local_midnight(ts: float) -> int:
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


DAY_START = _local_midnight(1_700_000_000)


def _msg(ts_ms: int, it: int, ot: int, rt: int, cr: int, cw: int = 0, model: str = "deepseek-v4-flash",
         role: str = "assistant") -> str:
    total = it + ot + rt + cr + cw
    return json.dumps({
        "parentID": "p", "role": role, "mode": "build",
        "cost": 0.001,
        "tokens": {"total": total, "input": it, "output": ot, "reasoning": rt,
                   "cache": {"write": cw, "read": cr}},
        "modelID": model, "providerID": "deepseek",
        "time": {"created": ts_ms, "completed": ts_ms},
    })


def _make_db(path, rows: list[str]):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, "
                 "time_created INTEGER, time_updated INTEGER, data TEXT)")
    for i, d in enumerate(rows):
        conn.execute("INSERT INTO message (id, session_id, time_created, time_updated, data) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (f"msg_{i}", f"ses_{i}", i * 1000, i * 1000, d))
    conn.commit()
    conn.close()


def test_incremental_and_breakdown(tmp_path):
    db = tmp_path / "opencode.db"
    now = DAY_START + 3600
    _make_db(db, [
        _msg((DAY_START + 10) * 1000, 1435, 97, 0, 12544),           # total=14076
        _msg((DAY_START + 11) * 1000, 35, 102, 224, 12288),          # total=12649
        _msg((DAY_START + 12) * 1000, 10, 10, 0, 10, 5, role="user"),  # user 消息跳过
    ])
    src = OpencodeSource(db, now_fn=lambda: now)
    evs = src.poll()
    assert len(evs) == 2, "only assistant messages with tokens"
    e0, e1 = evs
    assert e0.tokens == 14076
    assert e0.input_t == 1435 and e0.output_t == 97 and e0.cache_read_t == 12544
    assert e0.model == "deepseek-v4-flash"
    assert e1.tokens == 12649
    assert e1.input_t == 35 and e1.output_t == 102
    assert e1.cache_create_t == 0
    assert src.daily_total() == 14076 + 12649
    # 增量：无新消息
    assert src.poll() == []
    # 新消息出现
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
                 ("msg_new", "ses_n", now * 1000, now * 1000,
                  _msg(now * 1000, 500, 50, 0, 100)))
    conn.commit()
    conn.close()
    evs = src.poll()
    assert [e.tokens for e in evs] == [650]
    assert src.daily_total() == 14076 + 12649 + 650
    assert src.total() == 14076 + 12649 + 650


def test_day_rollover_resets_daily(tmp_path):
    db = tmp_path / "opencode.db"
    _make_db(db, [_msg((DAY_START + 10) * 1000, 100, 0, 0, 0)])
    src = OpencodeSource(db, now_fn=lambda: DAY_START + 3600)
    src.poll()
    assert src.daily_total() == 100
    # 跨天：新日期累计清零，但历史消息不重算
    next_day = DAY_START + 86400
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?,?,?,?,?)",
                 ("msg_nd", "ses_nd", (next_day + 5) * 1000, (next_day + 5) * 1000,
                  _msg((next_day + 5) * 1000, 200, 0, 0, 0)))
    conn.commit()
    conn.close()
    src2 = OpencodeSource(db, now_fn=lambda: next_day + 100)
    # 新实例：今日累计只算新日消息
    evs = src2.poll()
    assert src2.daily_total() == 200
    assert src2.total() == 300  # 历史 100 + 新日 200


def test_missing_db_offline(tmp_path):
    src = OpencodeSource(tmp_path / "nope.db", now_fn=lambda: DAY_START + 1)
    assert src.online() is False
    assert src.poll() == []
    assert src.daily_total() == 0
