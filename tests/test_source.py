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


DAY_START = 1_700_000_000  # 固定的"今日 0 点"epoch 秒


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
