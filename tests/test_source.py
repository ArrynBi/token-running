import sqlite3
import time

import pytest

from token_running.source import TokenSource, UsageEvent


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE proxy_request_logs (
               rowid INTEGER PRIMARY KEY AUTOINCREMENT,
               created_at INTEGER, app_type TEXT,
               input_tokens INTEGER, output_tokens INTEGER,
               cache_read_tokens INTEGER, cache_creation_tokens INTEGER)"""
    )
    for created_at, app, i, o, cr, cc in rows:
        con.execute(
            "INSERT INTO proxy_request_logs (created_at, app_type, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens) VALUES (?,?,?,?,?,?)",
            (created_at, app, i, o, cr, cc),
        )
    con.commit()
    con.close()


def _local_midnight(ts: float) -> int:
    """给定 epoch 秒所在本地日期的 0 点（epoch 秒），与 TokenSource._day_start 语义一致。"""
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


DAY_START = _local_midnight(1_700_000_000)  # 本机时区"今日 0 点"epoch 秒（固定基准日）


def test_poll_returns_all_rows_once(tmp_path):
    make_db(tmp_path / "t.db", [(1000, "claude", 10, 20, 30, 40), (1001, "codex", 1, 2, 3, 4)])
    src = TokenSource(tmp_path / "t.db", now_fn=lambda: DAY_START + 3600)
    events = src.poll()
    assert events == [UsageEvent(1000, 100), UsageEvent(1001, 10)]  # 10+20+30+40=100, 1+2+3+4=10
    assert src.poll() == []  # 第二次无增量


def test_tokens_include_cache_full_sum(tmp_path):
    make_db(tmp_path / "t.db", [(1000, "claude", 5, 5, 50, 500)])
    src = TokenSource(tmp_path / "t.db", now_fn=lambda: DAY_START + 3600)
    assert src.poll()[0].tokens == 560  # 含缓存全量


def test_daily_total_starts_from_day_zero(tmp_path):
    # 今日 0 点前 1 秒的请求不应计入；0 点及之后计入
    make_db(tmp_path / "t.db", [(DAY_START - 1, "claude", 100, 0, 0, 0), (DAY_START, "claude", 50, 50, 0, 0)])
    src = TokenSource(tmp_path / "t.db", now_fn=lambda: DAY_START + 3600)
    src.poll()
    assert src.daily_total() == 100


def test_daily_total_resets_across_midnight(tmp_path):
    db = tmp_path / "t.db"
    make_db(db, [(DAY_START, "claude", 100, 0, 0, 0)])
    now = [DAY_START + 3600]  # 当日
    src = TokenSource(db, now_fn=lambda: now[0])
    src.poll()
    assert src.daily_total() == 100
    # 跨天：插入第二天记录，now 推进到第二天 0 点后
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO proxy_request_logs (created_at, app_type, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens) VALUES (?,?,?,?,?,?)",
        (DAY_START + 86400, "claude", 7, 0, 0, 0),
    )
    con.commit()
    con.close()
    now[0] = DAY_START + 86400 + 100
    src.poll()
    assert src.daily_total() == 7  # 昨日 100 不再计入


def test_offline_when_db_missing(tmp_path):
    src = TokenSource(tmp_path / "nope.db", now_fn=lambda: DAY_START + 3600)
    assert src.online() is False
    assert src.poll() == []
    assert src.daily_total() == 0



def test_app_type_filter(tmp_path):
    # 只统计指定 app_type 的记录（供 CombinedSource 排除 claude 避免与 JSONL 重复）
    make_db(tmp_path / "t.db", [
        (DAY_START, "claude", 100, 0, 0, 0),
        (DAY_START, "codex", 50, 50, 0, 0),
        (DAY_START, "opencode", 10, 0, 0, 0),
    ])
    src = TokenSource(tmp_path / "t.db", now_fn=lambda: DAY_START + 3600, app_types=["codex", "opencode"])
    src.poll()
    assert src.daily_total() == 110  # codex=100 + opencode=10，claude=100 被排除


def test_app_type_filter_poll(tmp_path):
    # 过滤只影响增量拉取：插入 codex（被统计）与 claude（被排除）各一条后 poll
    make_db(tmp_path / "t.db", [])
    src = TokenSource(tmp_path / "t.db", now_fn=lambda: DAY_START + 3600, app_types=["codex"])
    assert src.poll() == []
    con = sqlite3.connect(tmp_path / "t.db")
    for app, i, o in (("codex", 50, 50), ("claude", 100, 0)):
        con.execute(
            "INSERT INTO proxy_request_logs (created_at, app_type, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens) VALUES (?,?,?,?,?,?)",
            (DAY_START, app, i, o, 0, 0),
        )
    con.commit()
    con.close()
    events = src.poll()
    assert len(events) == 1 and events[0].tokens == 100  # 只返回 codex 事件（100），claude 被排除
