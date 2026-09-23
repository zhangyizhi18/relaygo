# -*- coding: utf-8 -*-
"""
Web 控制台 —— Flask 蓝图（页面 + API）。

职责：
  1. 用户登录 / 登出 / 改密码（会话用 Flask 签名 Cookie，服务端不存 token）
  2. 权限控制：viewer(只读) < trader(交易员) < admin(管理员)
  3. 账户信息与手动下单 —— 通过"任务队列"下发给执行端，再同步等待回报
  4. 用户管理、审计日志查询

非侵入式接入（app.py 只加两行）：
    import web_console
    web_console.register(app)

为什么用"任务队列"而不是直连券商：
  中转服务一般在云上，碰不到券商客户端；账户/下单只有本机执行端能做。
  控制台把需求写成任务 -> 执行端轮询领走 -> 执行 -> 回报，与聚宽信号同一条思路，
  好处是所有下单同样过执行端风控，且天然带操作人（审计可追溯）。

并发说明：查询/下单接口会阻塞等待执行端回报（默认最长 TASK_WAIT_SECONDS 秒）。
  开发服务器 threaded=True 不受影响；gunicorn 请保证 workers >= 2。
"""
import functools
import os
import shutil
import time
from datetime import datetime, timedelta

from flask import Blueprint, Response, g, jsonify, request, session

import config
import security_code
import store
import store_web
import web_ui

bp = Blueprint("console", __name__, url_prefix="/console")


@bp.after_request
def _no_cache(resp):
    """控制台页面与 API 一律禁缓存：服务端更新前端代码后浏览器立即生效，
    避免"改了没变化"（无 Cache-Control 时浏览器按启发式缓存旧 HTML）。"""
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

# 账户查询类型 -> 执行端任务类型
QUERY_KINDS = {
    "balance": "query_balance",
    "positions": "query_positions",
    "entrusts": "query_entrusts",
    "trades": "query_trades",
}


# ==================== 注册 ====================

def register(app):
    """把控制台挂到主 Flask app 上。"""
    app.secret_key = config.WEB_SECRET_KEY
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=config.WEB_SESSION_HOURS),
    )
    store_web.init()
    app.register_blueprint(bp)
    if config.WEB_SECRET_KEY.startswith("relay-web-secret-change-me"):
        print("[Web控制台] 提示：正在使用默认会话密钥，公网部署前请设置环境变量 RELAY_WEB_SECRET")
    if config.WEB_DEFAULT_ADMIN_PASSWORD == "admin123":
        print("[Web控制台] 提示：默认管理员密码仍为 admin123，请登录后立即在右上角修改密码")


# ==================== 工具 ====================

def _client_ip():
    """取真实客户端 IP（兼容反向代理）。"""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "-"


def _audit(action, target="", detail="", result="ok", username=None):
    store_web.audit(username or (g.user["username"] if getattr(g, "user", None) else "-"),
                    action, target=target, detail=detail, ip=_client_ip(), result=result)


def _session_user():
    """从会话取用户，并确认账号仍然存在且启用（禁用即刻生效）。"""
    username = session.get("u")
    if not username:
        return None
    exp = session.get("exp", 0)
    if time.time() > exp:
        session.clear()
        return None
    user = store_web.get_user(username)
    if not user or not int(user["enabled"]):
        session.clear()
        return None
    user.pop("password", None)
    user["role_label"] = store_web.ROLE_LABEL.get(user["role"], user["role"])
    user["level"] = store_web.ROLE_LEVEL.get(user["role"], 0)
    return user


def _body():
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


def require(min_level):
    """权限装饰器：要求登录且角色等级 >= min_level。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            user = _session_user()
            if not user:
                return jsonify(ok=False, error="未登录或会话已过期，请重新登录"), 401
            if user["level"] < min_level:
                store_web.audit(user["username"], "permission_denied", target=request.path,
                                detail="角色 %s 权限不足" % user["role"],
                                ip=_client_ip(), result="denied")
                need = "管理员" if min_level >= 3 else "交易员"
                return jsonify(ok=False, error="权限不足：该操作需要%s权限" % need), 403
            g.user = user
            return fn(*a, **kw)
        return wrapper
    return deco


def _executor_online(max_age=None):
    """执行端心跳是否新鲜。返回 (bool, 心跳信息)。"""
    max_age = max_age or config.EXECUTOR_ALIVE_SECONDS
    summary = store.status_summary()
    hb = summary.get("last_heartbeat")
    if not hb:
        return False, None
    try:
        t = datetime.strptime(hb["ts"], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False, hb
    hb = dict(hb)
    hb["seconds_ago"] = int((datetime.now() - t).total_seconds())
    return hb["seconds_ago"] <= max_age, hb


def _wait_task(task_id, timeout=None):
    """轮询等待任务完成（执行端轮询间隔 + 查询/下单耗时，默认最长 30 秒）。"""
    timeout = timeout or config.TASK_WAIT_SECONDS
    deadline = time.time() + timeout
    while True:
        t = store_web.get_task_parsed(task_id)
        if t and t["status"] in ("done", "failed", "timeout"):
            return t
        if time.time() >= deadline:
            return store_web.get_task_parsed(task_id)
        time.sleep(0.4)


# ==================== 页面 ====================

@bp.route("/", methods=["GET"])
@bp.route("", methods=["GET"])
def page():
    """控制台单页。未登录时前端自动显示登录框。"""
    return Response(web_ui.render(config.VERSION), mimetype="text/html; charset=utf-8")


# ==================== 会话 ====================

@bp.route("/api/login", methods=["POST"])
def api_login():
    d = _body()
    username = str(d.get("username", "")).strip()
    password = str(d.get("password", ""))
    if not username or not password:
        return jsonify(ok=False, error="请输入用户名和密码"), 400
    user, err = store_web.authenticate(username, password)
    if err:
        store_web.audit(username, "login_failed", target="控制台",
                        detail=err, ip=_client_ip(), result="failed")
        time.sleep(0.6)   # 轻微延时，降低暴力破解速度
        return jsonify(ok=False, error=err), 401
    session.permanent = True
    session["u"] = user["username"]
    session["exp"] = time.time() + config.WEB_SESSION_HOURS * 3600
    store_web.audit(user["username"], "login", target="控制台",
                    detail="角色 %s" % user["role"], ip=_client_ip(), result="ok")
    user["role_label"] = store_web.ROLE_LABEL.get(user["role"], user["role"])
    user["level"] = store_web.ROLE_LEVEL.get(user["role"], 0)
    return jsonify(ok=True, user=user)


@bp.route("/api/logout", methods=["POST"])
def api_logout():
    user = _session_user()
    if user:
        store_web.audit(user["username"], "logout", target="控制台", ip=_client_ip())
    session.clear()
    return jsonify(ok=True)


@bp.route("/api/me", methods=["GET"])
def api_me():
    user = _session_user()
    if not user:
        return jsonify(ok=False, error="未登录"), 401
    return jsonify(ok=True, user=user)


@bp.route("/api/password", methods=["POST"])
@require(1)
def api_password():
    d = _body()
    ok, msg = store_web.change_own_password(g.user["username"], str(d.get("old_password", "")),
                                            str(d.get("new_password", "")))
    _audit("change_password", target=g.user["username"], detail=msg,
           result="ok" if ok else "failed")
    return jsonify(ok=ok, message=msg if ok else None, error=None if ok else msg)


@bp.route("/api/init_hint", methods=["GET"])
def api_init_hint():
    """登录页提示：仅在默认管理员密码未被修改时给出（提示不包含密码本身）。"""
    admin = store_web.get_user(config.WEB_DEFAULT_ADMIN)
    hint = ""
    if admin and store_web.verify_password(config.WEB_DEFAULT_ADMIN_PASSWORD, admin["password"]):
        hint = ("首次使用：请用配置中的默认管理员账号 <code>%s</code> 登录"
                "（密码见 relay_server/config.py，默认 admin123），登录后请立即修改密码。"
                % config.WEB_DEFAULT_ADMIN)
    return jsonify(ok=True, hint=hint)


# ==================== 概览 ====================

@bp.route("/api/overview", methods=["GET"])
@require(1)
def api_overview():
    summary = store.status_summary()
    alive, hb = _executor_online()
    return jsonify(ok=True, version=config.VERSION,
                   stats=summary, last_heartbeat=hb or None, executor_online=alive,
                   tasks=store_web.task_stats(), signals=store.recent_signals(10))


# ==================== 账户查询（下发任务并等待结果） ====================

@bp.route("/api/account", methods=["GET"])
@require(1)
def api_account():
    kind = request.args.get("kind", "balance")
    if kind not in QUERY_KINDS:
        return jsonify(ok=False, error="未知查询类型: %s" % kind), 400

    alive, hb = _executor_online()
    if not alive:
        return jsonify(ok=False, error="执行端当前离线"
                       + ("（最后心跳 %s）" % hb["ts"] if hb else "（从未连接过）")
                       + "，请先启动执行端"), 503

    ok, msg, tid = store_web.create_task(QUERY_KINDS[kind], {}, g.user["username"])
    if not ok:
        return jsonify(ok=False, error=msg), 400
    task = _wait_task(tid)
    _audit("query", target=kind, detail=msg,
           result="ok" if task and task["status"] == "done" else "failed")

    if not task or task["status"] != "done":
        return jsonify(ok=False, error=(task or {}).get("message") or "执行端未在限定时间内返回结果",
                       task_id=tid)
    return jsonify(ok=True, data=task["result"], message=task.get("message", ""),
                   finished_at=task.get("finished_at"), task_id=tid)


# ==================== 手动下单 / 撤单 ====================

@bp.route("/api/order", methods=["POST"])
@require(2)
def api_order():
    d = _body()
    security_raw = str(d.get("security", "")).strip()
    side = str(d.get("side", "")).strip().lower()
    amount = d.get("amount")
    price = d.get("price")

    # ---- 参数校验 ----
    # 证券代码归一化：允许只填 6 位数字，自动补 .XSHG/.XSHE；
    # 判不出市场、或代码与手写后缀矛盾时一律拒绝（宁拒不错，见 security_code.py）。
    security, err = security_code.normalize_security(security_raw)
    if err:
        return jsonify(ok=False, error=err), 400
    if side not in ("buy", "sell"):
        return jsonify(ok=False, error="方向必须是 buy 或 sell"), 400
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="数量必须是整数"), 400
    if amount <= 0:
        return jsonify(ok=False, error="数量必须大于 0"), 400
    if price in ("", None):
        price = None
    else:
        try:
            price = float(price)
        except (TypeError, ValueError):
            return jsonify(ok=False, error="价格必须是数字，留空表示市价"), 400
        if price <= 0:
            return jsonify(ok=False, error="价格必须大于 0"), 400

    alive, hb = _executor_online()
    if not alive:
        return jsonify(ok=False, error="执行端离线，无法下单"
                       + ("（最后心跳 %s）" % hb["ts"] if hb else "")), 503

    # ---- 防重复点击：同类订单 15 秒内只允许一条 ----
    if store_web.recent_same_order(security, side, amount):
        return jsonify(ok=False, error="检测到 15 秒内已提交过相同的委托，已拦截（防重复下单）"), 429

    payload = {"security": security, "side": side, "amount": amount, "price": price,
               "operator": g.user["username"]}
    ok, msg, tid = store_web.create_task("place_order", payload, g.user["username"])
    if not ok:
        return jsonify(ok=False, error=msg), 400
    _audit("order_submit", target="%s %s x%s @%s" % (security, side, amount, price),
           detail="任务#%d%s" % (tid, ("（代码输入 %s 自动补全）" % security_raw
                                       if security_raw.upper() != security else "")),
           result="submitted")

    task = _wait_task(tid)
    status = (task or {}).get("status")
    _audit("order_result", target="%s %s x%s" % (security, side, amount),
           detail=(task or {}).get("message", ""), result="ok" if status == "done" else "failed")
    return jsonify(ok=True, task=task, task_id=tid)


@bp.route("/api/cancel", methods=["POST"])
@require(2)
def api_cancel():
    d = _body()
    entrust_no = str(d.get("entrust_no", "")).strip()
    if not entrust_no.isdigit():
        return jsonify(ok=False, error="委托号必须是纯数字（见当日委托列表）"), 400

    alive, hb = _executor_online()
    if not alive:
        return jsonify(ok=False, error="执行端离线，无法撤单"), 503

    ok, msg, tid = store_web.create_task("cancel_order", {"entrust_no": entrust_no},
                                         g.user["username"])
    if not ok:
        return jsonify(ok=False, error=msg), 400
    task = _wait_task(tid)
    status = (task or {}).get("status")
    _audit("order_cancel", target=entrust_no, detail=(task or {}).get("message", ""),
           result="ok" if status == "done" else "failed")
    if not task or status != "done":
        return jsonify(ok=False, error=(task or {}).get("message") or "撤单未完成", task_id=tid)
    return jsonify(ok=True, task=task, task_id=tid)


@bp.route("/api/cancel_all", methods=["POST"])
@require(2)
def api_cancel_all():
    """一键全撤：撤销当日全部可撤委托（执行端走同花顺撤单页[全撤]安全路径）。"""
    alive, hb = _executor_online()
    if not alive:
        return jsonify(ok=False, error="执行端离线，无法撤单"), 503

    ok, msg, tid = store_web.create_task("cancel_all_orders", {}, g.user["username"])
    if not ok:
        return jsonify(ok=False, error=msg), 400
    # 全撤涉及读撤单页+点按钮+确认弹窗，给双倍等待时间
    task = _wait_task(tid, timeout=config.TASK_WAIT_SECONDS * 2)
    status = (task or {}).get("status")
    _audit("order_cancel_all", target="当日全部可撤委托",
           detail=(task or {}).get("message", ""),
           result="ok" if status == "done" else "failed")
    if not task or status != "done":
        return jsonify(ok=False, error=(task or {}).get("message") or "一键全撤未完成", task_id=tid)
    return jsonify(ok=True, task=task, task_id=tid)


@bp.route("/api/clear", methods=["POST"])
@require(2)
def api_clear():
    """清场：点[重填]清空下单表单（执行端走同花顺[重填]按钮）。"""
    alive, hb = _executor_online()
    if not alive:
        return jsonify(ok=False, error="执行端离线，无法清场"), 503
    ok, msg, tid = store_web.create_task("clear_form", {}, g.user["username"])
    if not ok:
        return jsonify(ok=False, error=msg), 400
    task = _wait_task(tid)
    status = (task or {}).get("status")
    _audit("clear_form", target="下单表单", detail=(task or {}).get("message", ""),
           result="ok" if status == "done" else "failed")
    if not task or status != "done":
        return jsonify(ok=False, error=(task or {}).get("message") or "清场未完成", task_id=tid)
    return jsonify(ok=True, task=task, task_id=tid)


@bp.route("/api/restart", methods=["POST"])
@require(2)
def api_restart():
    """重启同花顺：会断开当前交易会话，需人工重新登录。仅 ths 模式有效。"""
    alive, hb = _executor_online()
    if not alive:
        return jsonify(ok=False, error="执行端离线，无法重启同花顺"), 503
    ok, msg, tid = store_web.create_task("restart_ths", {}, g.user["username"])
    if not ok:
        return jsonify(ok=False, error=msg), 400
    # 重启涉及杀进程+启动+等同花顺自动登录+重连，耗时较长：
    # 在双倍基础上再多给 60 秒，避免执行端还在等登录、控制台却提前报"未完成"。
    task = _wait_task(tid, timeout=config.TASK_WAIT_SECONDS * 2 + 60)
    status = (task or {}).get("status")
    _audit("restart_ths", target="同花顺下单程序",
           detail=(task or {}).get("message", ""),
           result="ok" if status == "done" else "failed")
    if not task or status != "done":
        return jsonify(ok=False, error=(task or {}).get("message") or "重启未完成（可能需手动登录）",
                       task_id=tid)
    return jsonify(ok=True, task=task, task_id=tid)


@bp.route("/api/guard/reset", methods=["POST"])
@require(3)
def api_guard_reset():
    """管理员手动解除 L3 熔断（连续失败熔断锁）。"""
    ok, msg, tid = store_web.create_task("guard_reset", {}, g.user["username"])
    if not ok:
        return jsonify(ok=False, error=msg), 400
    task = _wait_task(tid)
    status = (task or {}).get("status")
    _audit("guard_reset", target="熔断恢复", detail=(task or {}).get("message", ""),
           result="ok" if status == "done" else "failed")
    if not task or status != "done":
        return jsonify(ok=False, error=(task or {}).get("message") or "熔断恢复未完成", task_id=tid)
    return jsonify(ok=True, task=task, task_id=tid)


# ==================== 持仓对账（v1.7.0） ====================

@bp.route("/api/reconcile", methods=["GET"])
@require(1)
def api_reconcile_last():
    """最近一次对账结果 + 聚宽快照状态（页面打开时拉取）。"""
    last = store_web.last_reconcile()
    return jsonify(ok=True, last=last,
                   jq_count=len(store_web.get_jq_positions()),
                   auto_enabled=bool(config.RECONCILE_AUTO),
                   auto_at=config.RECONCILE_AT)


@bp.route("/api/reconcile/run", methods=["POST"])
@require(2)
def api_reconcile_run():
    """立即对账：读聚宽快照 -> 下发查持仓任务给执行端 -> 比对 -> 落库。"""
    import reconcile
    res = reconcile.run_reconcile(trigger=g.user["username"])
    _audit("reconcile_run", target="持仓对账", detail=res.get("summary", ""),
           result="ok" if res.get("status") == "ok" else res.get("status", "failed"))
    return jsonify(ok=True, result=res)


# ==================== 任务 / 用户 / 审计 ====================

@bp.route("/api/tasks", methods=["GET"])
@require(1)
def api_tasks():
    limit = min(int(request.args.get("limit", 30) or 30), 200)
    return jsonify(ok=True, tasks=store_web.list_tasks(limit),
                   stats=store_web.task_stats())


@bp.route("/api/users", methods=["GET"])
@require(3)
def api_users():
    users = []
    for u in store_web.list_users():
        u["role_label"] = store_web.ROLE_LABEL.get(u["role"], u["role"])
        users.append(u)
    return jsonify(ok=True, users=users, roles=store_web.ROLE_LABEL)


@bp.route("/api/users", methods=["POST"])
@require(3)
def api_user_create():
    d = _body()
    ok, msg = store_web.create_user(str(d.get("username", "")), str(d.get("password", "")),
                                    role=str(d.get("role", "viewer")),
                                    display_name=str(d.get("display_name", "")))
    _audit("user_create", target=str(d.get("username", "")), detail=msg,
           result="ok" if ok else "failed")
    return jsonify(ok=ok, message=msg if ok else None, error=None if ok else msg)


@bp.route("/api/users/update", methods=["POST"])
@require(3)
def api_user_update():
    d = _body()
    username = str(d.get("username", ""))
    ok, msg = store_web.update_user(
        username,
        role=d.get("role"),
        enabled=(None if d.get("enabled") is None else bool(d.get("enabled"))),
        display_name=d.get("display_name"))
    _audit("user_update", target=username,
           detail="%s -> %s" % (msg, {k: v for k, v in d.items() if k != "username"}),
           result="ok" if ok else "failed")
    return jsonify(ok=ok, message=msg if ok else None, error=None if ok else msg)


@bp.route("/api/users/password", methods=["POST"])
@require(3)
def api_user_password():
    d = _body()
    username = str(d.get("username", ""))
    ok, msg = store_web.set_password(username, str(d.get("password", "")))
    _audit("user_password_reset", target=username, detail=msg,
           result="ok" if ok else "failed")
    return jsonify(ok=ok, message=msg if ok else None, error=None if ok else msg)


@bp.route("/api/users/delete", methods=["POST"])
@require(3)
def api_user_delete():
    d = _body()
    username = str(d.get("username", ""))
    if username == g.user["username"]:
        return jsonify(ok=False, error="不能删除当前登录的账号"), 400
    ok, msg = store_web.delete_user(username)
    _audit("user_delete", target=username, detail=msg, result="ok" if ok else "failed")
    return jsonify(ok=ok, message=msg if ok else None, error=None if ok else msg)


@bp.route("/api/audit", methods=["GET"])
@require(3)
def api_audit():
    limit = min(int(request.args.get("limit", 100) or 100), 500)
    offset = max(int(request.args.get("offset", 0) or 0), 0)
    logs = store_web.list_audit(limit=limit, offset=offset,
                                username=request.args.get("username") or None,
                                action=request.args.get("action") or None,
                                keyword=request.args.get("keyword") or None)
    return jsonify(ok=True, logs=logs, summary=store_web.audit_summary())


# ==================== 聚宽信号详录（v1.5.0） ====================

@bp.route("/api/jq_signals", methods=["GET"])
@require(1)
def api_jq_signals():
    """聚宽信号列表（过滤 + 分页），每条附回报时间线。"""
    status = request.args.get("status") or None
    if status == "all":
        status = None
    security = request.args.get("security") or None
    date = request.args.get("date") or None
    limit = min(int(request.args.get("limit", 20) or 20), 100)
    offset = max(int(request.args.get("offset", 0) or 0), 0)
    rows, total = store.query_signals(status=status, security=security, date=date,
                                      offset=offset, limit=limit)
    fb_map = store.feedback_for([r["id"] for r in rows])
    for r in rows:
        r["feedback"] = fb_map.get(r["id"], [])
    return jsonify(ok=True, signals=rows, total=total,
                   limit=limit, offset=offset, rejects=store.reject_stats())


@bp.route("/api/jq_rejects", methods=["GET"])
@require(1)
def api_jq_rejects():
    """最近被拒绝的聚宽/执行端请求（密钥错、字段缺、积压拒收等）。"""
    limit = min(int(request.args.get("limit", 50) or 50), 200)
    return jsonify(ok=True, rejects=store.recent_rejects(limit),
                   stats=store.reject_stats())


@bp.route("/api/jq_signal/<int:sid>", methods=["GET"])
@require(1)
def api_jq_signal_detail(sid):
    """单条信号完整时间线（收单 -> 派发 -> 回报）。"""
    t = store.signal_timeline(sid)
    if not t:
        return jsonify(ok=False, error="信号不存在"), 404
    return jsonify(ok=True, **t)


# ==================== 清除类操作（危险，仅管理员） ====================

def _backup_db():
    """清记录前自动备份数据库（主库 + WAL/shm 附属文件），文件名带时间戳。
    返回备份主路径；失败返回错误信息字符串。"""
    try:
        src = config.DB_PATH
        if not os.path.exists(src):
            return None
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        dst = "%s.bak.%s" % (src, ts)
        shutil.copy2(src, dst)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(src + suffix):
                shutil.copy2(src + suffix, dst + suffix)
        return dst
    except Exception as e:
        return "备份失败: %s" % e


@bp.route("/api/jq_signals/clear", methods=["POST"])
@require(3)
def api_jq_signals_clear():
    """清除聚宽信号队列：默认只清 pending（待派发）；scope='all' 清空整表。"""
    body = _body()
    scope = body.get("scope", "pending") or "pending"
    if scope not in ("pending", "all"):
        return jsonify(ok=False, error="scope 只能是 pending 或 all"), 400
    if scope == "all":
        n = store.clear_all_signals()
        _audit("signal_queue_cleared", target="全部信号", detail="清空整表 deleted=%d" % n)
    else:
        n = store.clear_pending_signals()
        _audit("signal_queue_cleared", target="待派发队列", detail="deleted=%d" % n)
    return jsonify(ok=True, scope=scope, deleted=n)


@bp.route("/api/records/clear", methods=["POST"])
@require(3)
def api_records_clear():
    """一键清除记录：scope='jq' 清聚宽链路（signals/feedback/events/jq_rejects + jq_positions）；
    scope='all' 再清 tasks/audit_log/reconcile。清前自动备份 relay_data.db。绝不删 users。"""
    body = _body()
    scope = body.get("scope", "all") or "all"
    if scope not in ("jq", "all"):
        return jsonify(ok=False, error="scope 只能是 jq 或 all"), 400
    bak = _backup_db()
    jq = store.clear_all_jq_records()
    web = store_web.clear_all_records(scope)
    # 清后再写审计：scope='all' 时 audit_log 已清空，这条作为清除动作的唯一留存痕迹
    _audit("records_cleared", target=scope,
           detail="备份=%s jq=%s web=%s" % (bak or "-", jq, web), result="ok")
    return jsonify(ok=True, scope=scope, backup=bak, jq=jq, web=web)
