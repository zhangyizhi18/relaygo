# -*- coding: utf-8 -*-
"""
SQLite 存储层 —— 中转服务唯一的落库模块。
表结构：
  signals   聚宽发来的下单信号（jq_order_id 唯一索引 => 天然幂等防重）
  feedback  执行端回报（受理/成交/失败）
  events    简易审计日志（谁在什么时候干了什么）
线程安全：全局一把锁 + check_same_thread=False（Flask 多线程下够用，量很小）。
"""
import sqlite3
import threading
from datetime import datetime, timedelta

import config

_lock = threading.Lock()
_conn = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init():
    """建表（幂等，可重复调用）。"""
    global _conn
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock, _conn:
        _conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals(
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            jq_order_id   TEXT UNIQUE NOT NULL,   -- 聚宽订单ID，防重复的幂等键
            security      TEXT NOT NULL,          -- 如 600519.XSHG
            side          TEXT NOT NULL,          -- buy / sell
            amount        INTEGER NOT NULL,       -- 股数
            price         REAL,                   -- 期望价格（可空=市价意图）
            jq_status     TEXT,                   -- 聚宽侧订单状态 open/held 等
            status        TEXT NOT NULL DEFAULT 'pending',  -- pending/dispatched/accepted/filled/failed/expired
            created_at    TEXT NOT NULL,
            dispatched_at TEXT
        );
        CREATE TABLE IF NOT EXISTS feedback(
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id  INTEGER NOT NULL,
            result     TEXT NOT NULL,   -- accepted / filled / failed
            message    TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            detail TEXT
        );
        CREATE TABLE IF NOT EXISTS jq_rejects(
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT NOT NULL,
            endpoint TEXT NOT NULL,   -- 被拒的接口，如 /api/signal
            reason   TEXT NOT NULL,   -- 401/400/503 等分类
            detail   TEXT,            -- 具体原因
            ip       TEXT             -- 来源 IP
        );
        """)
        # v1.5.0 迁移：signals 表补 src_ip（来源 IP）列；老库幂等升级
        cols = [r[1] for r in _conn.execute("PRAGMA table_info(signals)").fetchall()]
        if "src_ip" not in cols:
            _conn.execute("ALTER TABLE signals ADD COLUMN src_ip TEXT")


# ---------------- 信号 ----------------

def insert_signal(payload):
    """插入信号。返回 (signal_row, duplicated)。jq_order_id 重复时不会重复插入。"""
    with _lock, _conn:
        cur = _conn.execute(
            "INSERT OR IGNORE INTO signals(jq_order_id, security, side, amount, price, jq_status, status, created_at, src_ip)"
            " VALUES(?,?,?,?,?,?,'pending',?,?)",
            (payload["order_id"], payload["security"], payload["side"],
             int(payload["amount"]), payload.get("price"), payload.get("jq_status"), _now(),
             payload.get("src_ip")))
        duplicated = (cur.rowcount == 0)
        row = _conn.execute("SELECT * FROM signals WHERE jq_order_id=?",
                            (payload["order_id"],)).fetchone()
        _conn.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)",
                      (_now(), "signal_duplicate" if duplicated else "signal_new",
                       "%s %s %s x%s" % (payload["security"], payload["side"],
                                         payload["order_id"], payload["amount"])))
    return row, duplicated


def take_pending(limit=10):
    """执行端拉单：原子地把 pending 信号标记为 dispatched 并返回。"""
    out = []
    with _lock, _conn:
        # 先把过期的旧信号作废（超过 TTL 还没被领走的，说明不该再执行了）
        deadline = (datetime.now() - timedelta(hours=config.SIGNAL_TTL_HOURS)
                    ).strftime("%Y-%m-%d %H:%M:%S")
        _conn.execute("UPDATE signals SET status='expired'"
                      " WHERE status='pending' AND created_at < ?", (deadline,))
        rows = _conn.execute(
            "SELECT * FROM signals WHERE status='pending' ORDER BY id LIMIT ?",
            (limit,)).fetchall()
        for r in rows:
            _conn.execute("UPDATE signals SET status='dispatched', dispatched_at=? WHERE id=?",
                          (_now(), r["id"]))
            out.append(dict(r))
        if out:
            _conn.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)",
                          (_now(), "dispatch",
                           "%d signals ids=%s" % (len(out), ",".join(str(r["id"]) for r in out))))
    return out


def set_signal_status(signal_id, status):
    with _lock, _conn:
        _conn.execute("UPDATE signals SET status=? WHERE id=?", (status, signal_id))


def clear_pending_signals():
    """清空「待派发」信号队列（status='pending'，尚未派发给执行端的）。返回删除条数。"""
    with _lock, _conn:
        cur = _conn.execute("DELETE FROM signals WHERE status='pending'")
        n = cur.rowcount
        _conn.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)",
                      (_now(), "signal_queue_cleared", "pending deleted=%d" % n))
    return n


def clear_all_signals():
    """清空整个 signals 表（含已派发/受理/失败/过期）。返回删除条数。"""
    with _lock, _conn:
        cur = _conn.execute("DELETE FROM signals")
        n = cur.rowcount
        _conn.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)",
                      (_now(), "signal_all_cleared", "all deleted=%d" % n))
    return n


def clear_all_jq_records():
    """清空聚宽链路全部记录（signals/feedback/events/jq_rejects）。返回各表删除数。"""
    with _lock, _conn:
        n_sig = _conn.execute("DELETE FROM signals").rowcount
        n_fb = _conn.execute("DELETE FROM feedback").rowcount
        n_ev = _conn.execute("DELETE FROM events").rowcount
        n_rj = _conn.execute("DELETE FROM jq_rejects").rowcount
        _conn.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)",
                      (_now(), "jq_records_cleared",
                       "signals=%d feedback=%d events=%d rejects=%d"
                       % (n_sig, n_fb, n_ev, n_rj)))
    return {"signals": n_sig, "feedback": n_fb, "events": n_ev, "jq_rejects": n_rj}


# ---------------- 回报 ----------------

def add_feedback(signal_id, result, message=""):
    with _lock, _conn:
        _conn.execute("INSERT INTO feedback(signal_id,result,message,created_at) VALUES(?,?,?,?)",
                      (signal_id, result, message, _now()))
        _conn.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)",
                      (_now(), "feedback", "signal#%s -> %s %s" % (signal_id, result, message)))


def heartbeat(name):
    with _lock, _conn:
        _conn.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)",
                      (_now(), "heartbeat", name))


# ---------------- 查询 ----------------

def status_summary():
    with _lock:
        def one(sql):
            return _conn.execute(sql).fetchone()[0]
        last_hb = _conn.execute(
            "SELECT ts, detail FROM events WHERE kind='heartbeat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "pending":   one("SELECT COUNT(*) FROM signals WHERE status='pending'"),
            "dispatched": one("SELECT COUNT(*) FROM signals WHERE status='dispatched'"),
            "accepted":  one("SELECT COUNT(*) FROM signals WHERE status='accepted'"),
            "filled":    one("SELECT COUNT(*) FROM signals WHERE status='filled'"),
            "failed":    one("SELECT COUNT(*) FROM signals WHERE status='failed'"),
            "expired":   one("SELECT COUNT(*) FROM signals WHERE status='expired'"),
            "last_heartbeat": dict(last_hb) if last_hb else None,
        }


def recent_signals(limit=20):
    with _lock:
        rows = _conn.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?",
                             (limit,)).fetchall()
        return [dict(r) for r in rows]


# ---------------- 聚宽交互详录（v1.5.0） ----------------

def record_reject(endpoint, reason, detail="", ip=""):
    """记录一条被拒绝的聚宽/执行端请求（401/400/503 等）。绝不抛异常影响主流程。"""
    try:
        with _lock, _conn:
            _conn.execute("INSERT INTO jq_rejects(ts,endpoint,reason,detail,ip) VALUES(?,?,?,?,?)",
                          (_now(), endpoint, reason, str(detail)[:300], ip))
    except Exception:
        pass


def recent_rejects(limit=50):
    with _lock:
        rows = _conn.execute("SELECT * FROM jq_rejects ORDER BY id DESC LIMIT ?",
                             (limit,)).fetchall()
        return [dict(r) for r in rows]


def reject_stats():
    """被拒请求统计：总数 + 按原因分组。"""
    with _lock:
        total = _conn.execute("SELECT COUNT(*) FROM jq_rejects").fetchone()[0]
        by_reason = {}
        for r in _conn.execute("SELECT reason, COUNT(*) c FROM jq_rejects GROUP BY reason"):
            by_reason[r["reason"]] = r["c"]
        return {"total": total, "by_reason": by_reason}


def query_signals(status=None, security=None, date=None, offset=0, limit=20):
    """按条件查询信号（供控制台「聚宽信号」页）。返回 (rows, total)。"""
    where, args = [], []
    if status:
        where.append("status=?"); args.append(status)
    if security:
        where.append("security LIKE ?"); args.append("%" + security.strip() + "%")
    if date:
        where.append("created_at LIKE ?"); args.append(date.strip() + "%")
    cond = (" WHERE " + " AND ".join(where)) if where else ""
    with _lock:
        total = _conn.execute("SELECT COUNT(*) FROM signals" + cond, args).fetchone()[0]
        rows = _conn.execute("SELECT * FROM signals" + cond +
                             " ORDER BY id DESC LIMIT ? OFFSET ?",
                             args + [int(limit), int(offset)]).fetchall()
        return [dict(r) for r in rows], total


def feedback_for(signal_ids):
    """批量取一组信号的回报明细（按 signal_id 分组，时间正序）。"""
    if not signal_ids:
        return {}
    marks = ",".join("?" * len(signal_ids))
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM feedback WHERE signal_id IN (%s) ORDER BY id" % marks,
            list(signal_ids)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["signal_id"], []).append(dict(r))
    return out


def signal_timeline(signal_id):
    """单条信号的完整时间线：信号本体 + 回报列表 + 相关 events（派发/防重等）。"""
    with _lock:
        row = _conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
        if not row:
            return None
        sig = dict(row)
        fb = [dict(r) for r in _conn.execute(
            "SELECT * FROM feedback WHERE signal_id=? ORDER BY id", (signal_id,))]
        evs = [dict(r) for r in _conn.execute(
            "SELECT * FROM events WHERE (kind='feedback' AND detail LIKE ?)"
            " OR (kind LIKE 'signal_%' AND detail LIKE ?)"
            " ORDER BY id",
            ("signal#%d %%" % signal_id, "%%" + sig["jq_order_id"] + "%%"))]
        return {"signal": sig, "feedback": fb, "events": evs}
