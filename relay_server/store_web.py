# -*- coding: utf-8 -*-
"""
Web 控制台存储层 —— 用户 / 任务队列 / 审计日志。

设计原则（非侵入式）：
  * 独立文件、独立连接，不改动 store.py（聚宽信号库）一个字符；
  * 共用同一个 SQLite 文件，但表名（users/tasks/audit_log）与 signals/feedback/events 不冲突；
  * 所有异常都在本模块内消化成明确的返回值，不让控制台的问题影响信号链路。

三张表：
  users      控制台用户（角色 admin / trader / viewer）
  tasks      控制台下发给执行端的命令（查询资金/持仓/委托/成交、下单、撤单）
  audit_log  审计日志（谁、什么时候、从哪个 IP、做了什么、结果如何）

安全：密码用 PBKDF2-HMAC-SHA256 + 每用户随机盐，数据库里绝不出现明文密码。
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta

import config

_lock = threading.Lock()
_conn = None

# ---------------- 角色定义（数字越大权限越高） ----------------
ROLE_LEVEL = {"viewer": 1, "trader": 2, "admin": 3}
ROLE_LABEL = {"viewer": "只读", "trader": "交易员", "admin": "管理员"}

# ---------------- 任务状态 ----------------
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_DONE = "done"
TASK_FAILED = "failed"
TASK_TIMEOUT = "timeout"

# ---------------- 允许的任务类型（白名单，执行端只认这些） ----------------
TASK_KINDS = (
    "query_balance",     # 查资金
    "query_positions",   # 查持仓
    "query_entrusts",    # 查当日委托
    "query_trades",      # 查当日成交
    "place_order",       # 下单
    "cancel_order",      # 撤单
    "cancel_all_orders", # 一键全撤
    "clear_form",       # 清场（点重填清空下单表单）
    "restart_ths",      # 重启同花顺（断开会话需重登）
    "guard_reset",      # 解除 L3 熔断（管理员）
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ts(dt=None):
    return (dt or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


# ==================== 初始化 ====================

def init():
    """建表（幂等）+ 首次运行时创建默认管理员。"""
    global _conn
    if _conn is not None:
        return
    _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock, _conn:
        _conn.execute("PRAGMA journal_mode=WAL")   # 与 store.py 的多连接并存更稳
        _conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            username     TEXT UNIQUE NOT NULL,
            password     TEXT NOT NULL,          -- pbkdf2$盐$哈希
            role         TEXT NOT NULL DEFAULT 'viewer',
            display_name TEXT,
            enabled      INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL,
            last_login   TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks(
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            kind         TEXT NOT NULL,          -- 见 TASK_KINDS
            payload      TEXT,                   -- JSON 参数
            operator     TEXT,                   -- 发起人（审计用）
            status       TEXT NOT NULL DEFAULT 'pending',
            result       TEXT,                   -- JSON 结果
            message      TEXT,
            created_at   TEXT NOT NULL,
            dispatched_at TEXT,
            finished_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_log(
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT NOT NULL,
            username TEXT,
            role     TEXT,
            ip       TEXT,
            action   TEXT NOT NULL,
            target   TEXT,
            detail   TEXT,
            result   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE TABLE IF NOT EXISTS jq_positions(
            code       TEXT PRIMARY KEY,        -- 6 位纯数字代码
            name       TEXT,
            amount     INTEGER NOT NULL,        -- 聚宽策略持仓数量（股）
            updated_at TEXT NOT NULL            -- 快照时间
        );
        CREATE TABLE IF NOT EXISTS reconcile(
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT NOT NULL,              -- 对账时间
            trigger TEXT NOT NULL,              -- auto / 用户名
            status  TEXT NOT NULL,              -- ok/mismatch/no_snapshot/executor_offline/no_broker/failed
            detail  TEXT                        -- JSON: summary/items 等
        );
        """)
    _ensure_default_admin()


def _ensure_default_admin():
    """用户表为空时创建默认管理员（用户名/密码可用环境变量覆盖）。"""
    if count_users() > 0:
        return
    create_user(config.WEB_DEFAULT_ADMIN, config.WEB_DEFAULT_ADMIN_PASSWORD,
                role="admin", display_name="超级管理员")
    audit(config.WEB_DEFAULT_ADMIN, "system_init", target="默认管理员",
          detail="首次启动自动创建，请尽快修改密码", result="ok")


# ==================== 密码 ====================

_ITERATIONS = 120000


def hash_password(password, salt=None):
    """返回 'pbkdf2$盐hex$哈希hex'。"""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), _ITERATIONS)
    return "pbkdf2$%s$%s" % (salt, dk.hex())


def verify_password(password, stored):
    """恒定时间比较，防时序攻击。"""
    try:
        algo, salt, digest = str(stored).split("$")
        if algo != "pbkdf2":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                   bytes.fromhex(salt), _ITERATIONS).hex()
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False


def password_problem(password):
    """返回不合格原因，合格返回 None。"""
    if not password or len(password) < 6:
        return "密码至少 6 位"
    if len(password) > 64:
        return "密码过长（最多 64 位）"
    return None


# ==================== 用户 ====================

def create_user(username, password, role="viewer", display_name=""):
    """创建用户。返回 (ok, message)。"""
    username = (username or "").strip()
    if not username or len(username) > 32:
        return False, "用户名不能为空且不超过 32 字符"
    if not username.replace("_", "").replace("-", "").isalnum():
        return False, "用户名只能是字母、数字、下划线、连字符"
    if role not in ROLE_LEVEL:
        return False, "角色必须是 admin / trader / viewer"
    problem = password_problem(password)
    if problem:
        return False, problem
    with _lock, _conn:
        try:
            _conn.execute(
                "INSERT INTO users(username,password,role,display_name,enabled,created_at)"
                " VALUES(?,?,?,?,1,?)",
                (username, hash_password(password), role, display_name or username, _now()))
        except sqlite3.IntegrityError:
            return False, "用户已存在: %s" % username
    return True, "用户 %s 创建成功（角色：%s）" % (username, ROLE_LABEL.get(role, role))


def get_user(username):
    with _lock:
        row = _conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None


def list_users():
    with _lock:
        rows = _conn.execute(
            "SELECT id,username,role,display_name,enabled,created_at,last_login"
            " FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def count_users():
    with _lock:
        return _conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def count_enabled_admins(exclude=None):
    """统计启用状态的管理员数量（防止把最后一个管理员禁用/降级）。"""
    with _lock:
        sql = "SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1"
        args = []
        if exclude:
            sql += " AND username<>?"
            args.append(exclude)
        return _conn.execute(sql, args).fetchone()[0]


def update_user(username, role=None, enabled=None, display_name=None):
    """修改角色 / 启用状态 / 显示名。返回 (ok, message)。"""
    user = get_user(username)
    if not user:
        return False, "用户不存在: %s" % username
    if role is not None and role not in ROLE_LEVEL:
        return False, "角色必须是 admin / trader / viewer"

    # 防止把系统里最后一个可用管理员锁死
    losing_admin = (role is not None and role != "admin" and user["role"] == "admin") \
        or (enabled is not None and not int(enabled) and user["role"] == "admin")
    if losing_admin and count_enabled_admins(exclude=username) == 0:
        return False, "系统必须保留至少一个启用状态的管理员"

    sets, args = [], []
    if role is not None:
        sets.append("role=?")
        args.append(role)
    if enabled is not None:
        sets.append("enabled=?")
        args.append(1 if enabled else 0)
    if display_name is not None:
        sets.append("display_name=?")
        args.append(display_name)
    if not sets:
        return False, "没有需要修改的字段"
    args.append(username)
    with _lock, _conn:
        _conn.execute("UPDATE users SET %s WHERE username=?" % ",".join(sets), args)
    return True, "用户 %s 已更新" % username


def set_password(username, new_password):
    problem = password_problem(new_password)
    if problem:
        return False, problem
    if not get_user(username):
        return False, "用户不存在: %s" % username
    with _lock, _conn:
        _conn.execute("UPDATE users SET password=? WHERE username=?",
                      (hash_password(new_password), username))
    return True, "密码已重置"


def delete_user(username):
    if not get_user(username):
        return False, "用户不存在: %s" % username
    if count_users() <= 1:
        return False, "不能删除最后一个用户"
    if get_user(username)["role"] == "admin" and count_enabled_admins(exclude=username) == 0:
        return False, "系统必须保留至少一个启用状态的管理员"
    with _lock, _conn:
        _conn.execute("DELETE FROM users WHERE username=?", (username,))
    return True, "用户 %s 已删除" % username


def authenticate(username, password):
    """校验账号密码。返回 (user_dict, error)。"""
    user = get_user(username)
    if not user:
        return None, "用户名或密码错误"
    if not verify_password(password, user["password"]):
        return None, "用户名或密码错误"
    if not int(user["enabled"]):
        return None, "该账号已被禁用，请联系管理员"
    with _lock, _conn:
        _conn.execute("UPDATE users SET last_login=? WHERE username=?", (_now(), username))
    user.pop("password", None)
    return user, None


def change_own_password(username, old_password, new_password):
    user = get_user(username)
    if not user or not verify_password(old_password, user["password"]):
        return False, "原密码不正确"
    return set_password(username, new_password)


# ==================== 审计日志 ====================

def audit(username, action, target="", detail="", ip="", result="ok"):
    """写一条审计日志。日志自身出错绝不影响主流程。"""
    try:
        with _lock, _conn:
            user = _conn.execute("SELECT role FROM users WHERE username=?",
                                 (username,)).fetchone()
            _conn.execute(
                "INSERT INTO audit_log(ts,username,role,ip,action,target,detail,result)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (_now(), username or "-", (user["role"] if user else "-"),
                 ip or "-", action, str(target)[:200], str(detail)[:800], result))
    except Exception:
        pass


def list_audit(limit=100, offset=0, username=None, action=None, keyword=None):
    sql = "SELECT * FROM audit_log WHERE 1=1"
    args = []
    if username:
        sql += " AND username=?"
        args.append(username)
    if action:
        sql += " AND action=?"
        args.append(action)
    if keyword:
        sql += " AND (detail LIKE ? OR target LIKE ?)"
        args += ["%%%s%%" % keyword, "%%%s%%" % keyword]
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [int(limit), int(offset)]
    with _lock:
        return [dict(r) for r in _conn.execute(sql, args).fetchall()]


def audit_summary():
    with _lock:
        total = _conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        today = _conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE ts LIKE ?",
            (datetime.now().strftime("%Y-%m-%d") + "%",)).fetchone()[0]
        failed = _conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE result NOT IN ('ok')").fetchone()[0]
        return {"total": total, "today": today, "abnormal": failed}


# ==================== 任务队列（Web <-> 执行端） ====================

def create_task(kind, payload, operator):
    """创建任务。返回 (ok, message, task_id)。"""
    if kind not in TASK_KINDS:
        return False, "未知任务类型: %s" % kind, None
    with _lock, _conn:
        cur = _conn.execute(
            "INSERT INTO tasks(kind,payload,operator,status,created_at) VALUES(?,?,?,'pending',?)",
            (kind, json.dumps(payload or {}, ensure_ascii=False), operator, _now()))
        tid = cur.lastrowid
    return True, "任务已下发（#%d）" % tid, tid


def recent_same_order(security, side, amount, seconds=15):
    """防重复点击：N 秒内是否已有相同下单任务。返回 True/False。"""
    since = _ts(datetime.now() - timedelta(seconds=seconds))
    with _lock:
        rows = _conn.execute(
            "SELECT payload FROM tasks WHERE kind='place_order' AND created_at>=?"
            " AND status IN ('pending','running','done')", (since,)).fetchall()
    for r in rows:
        try:
            p = json.loads(r["payload"] or "{}")
        except Exception:
            continue
        if (str(p.get("security")) == str(security) and str(p.get("side")) == str(side)
                and str(p.get("amount")) == str(amount)):
            return True
    return False


def take_tasks(limit=1):
    """执行端拉任务：pending -> running，并把超时未完成的老任务标记 timeout。"""
    out = []
    deadline = _ts(datetime.now() - timedelta(seconds=config.TASK_TTL_SECONDS))
    with _lock, _conn:
        _conn.execute(
            "UPDATE tasks SET status='timeout', finished_at=?, message=COALESCE(message,'')||' [超时未回报]'"
            " WHERE status='running' AND dispatched_at < ?", (_now(), deadline))
        rows = _conn.execute(
            "SELECT * FROM tasks WHERE status='pending' ORDER BY id LIMIT ?",
            (limit,)).fetchall()
        for r in rows:
            _conn.execute("UPDATE tasks SET status='running', dispatched_at=? WHERE id=?",
                          (_now(), r["id"]))
            d = dict(r)
            d["status"] = "running"
            try:
                d["payload"] = json.loads(d.get("payload") or "{}")
            except Exception:
                d["payload"] = {}
            out.append(d)
    return out


def finish_task(task_id, ok, result=None, message=""):
    with _lock, _conn:
        _conn.execute(
            "UPDATE tasks SET status=?, result=?, message=?, finished_at=? WHERE id=?",
            ("done" if ok else "failed",
             json.dumps(result, ensure_ascii=False, default=str), str(message)[:800],
             _now(), int(task_id)))


def get_task(task_id):
    with _lock:
        row = _conn.execute("SELECT * FROM tasks WHERE id=?", (int(task_id),)).fetchone()
        return dict(row) if row else None


def get_task_parsed(task_id):
    """返回任务并把 payload/result 解析成对象（供 API 直接返回 JSON）。"""
    t = get_task(task_id)
    if not t:
        return None
    for key in ("payload", "result"):
        if t.get(key):
            try:
                t[key] = json.loads(t[key])
            except Exception:
                pass
    return t


def list_tasks(limit=30):
    with _lock:
        rows = _conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?",
                             (int(limit),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for key in ("payload", "result"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except Exception:
                    pass
        out.append(d)
    return out


def task_stats():
    with _lock:
        def one(sql):
            return _conn.execute(sql).fetchone()[0]
        return {
            "pending": one("SELECT COUNT(*) FROM tasks WHERE status='pending'"),
            "running": one("SELECT COUNT(*) FROM tasks WHERE status='running'"),
            "done": one("SELECT COUNT(*) FROM tasks WHERE status='done'"),
            "failed": one("SELECT COUNT(*) FROM tasks WHERE status IN ('failed','timeout')"),
        }


# ==================== 持仓对账（v1.7.0） ====================

def save_jq_positions(rows):
    """保存聚宽端上报的持仓快照（整表替换）。
    代码统一归一化为 6 位纯数字，非法/0 股行直接丢弃。rows: [{code,name,amount}]。"""
    clean = []
    for r in rows or []:
        try:
            code = str(r.get("code") or "").strip().split(".")[0]
            amount = int(float(r.get("amount") or 0))
        except (TypeError, ValueError):
            continue
        if not code.isdigit() or amount <= 0:
            continue
        clean.append({"code": code, "name": str(r.get("name", "")), "amount": amount})
    with _lock, _conn:
        _conn.execute("DELETE FROM jq_positions")
        for r in clean:
            _conn.execute(
                "INSERT OR REPLACE INTO jq_positions(code,name,amount,updated_at) VALUES(?,?,?,?)",
                (r["code"], r["name"], r["amount"], _now()))


def get_jq_positions():
    with _lock:
        rows = _conn.execute("SELECT * FROM jq_positions ORDER BY code").fetchall()
        return [dict(r) for r in rows]


def save_reconcile(trigger, status, detail):
    """落一条对账结果。detail 为可 JSON 序列化的 dict。"""
    with _lock, _conn:
        _conn.execute(
            "INSERT INTO reconcile(ts,trigger,status,detail) VALUES(?,?,?,?)",
            (_now(), str(trigger)[:50], str(status)[:30],
             json.dumps(detail, ensure_ascii=False)[:60000]))


def last_reconcile():
    """最近一次对账结果（detail 已解析），没有则 None。"""
    with _lock:
        r = _conn.execute("SELECT * FROM reconcile ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["detail"] = json.loads(d.get("detail") or "{}")
        except Exception:
            d["detail"] = {}
        return d


def clear_all_records(scope="all"):
    """清空 Web 侧记录（任务/审计/对账/持仓快照）。scope='jq' 只清 jq_positions；
    scope='all' 清 tasks/audit_log/reconcile/jq_positions。绝不删 users 表。
    返回各表删除数。"""
    with _lock, _conn:
        n_pos = _conn.execute("DELETE FROM jq_positions").rowcount
        if scope != "all":
            return {"jq_positions": n_pos}
        n_tasks = _conn.execute("DELETE FROM tasks").rowcount
        n_audit = _conn.execute("DELETE FROM audit_log").rowcount
        n_recon = _conn.execute("DELETE FROM reconcile").rowcount
    return {"tasks": n_tasks, "audit_log": n_audit,
            "reconcile": n_recon, "jq_positions": n_pos}
