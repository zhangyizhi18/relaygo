# -*- coding: utf-8 -*-
"""
RelayGo · 中转服务端（Flask）

职责：接收聚宽策略发来的下单信号 -> 落库 -> 等执行端来拉取 -> 接收执行端回报。
它自己不碰任何券商/客户端，纯信号调度，所以非常稳定。

接口一览（全部要求 X-API-Key 头）：
  POST /api/signal        聚宽 -> 中转   上报信号（用 SIGNAL_API_KEY）
  POST /api/signal/poll   执行端 -> 中转 拉取待处理信号（用 EXECUTOR_API_KEY）
  POST /api/feedback      执行端 -> 中转 回报结果（用 EXECUTOR_API_KEY）
  POST /api/heartbeat     执行端 -> 中转 心跳（用 EXECUTOR_API_KEY）
  GET  /api/status        健康检查（免鉴权，只暴露统计数字）
  GET  /api/signals       最近信号列表（免鉴权，方便人工核对）

启动：python app.py
"""
import json
import sys

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

import config
import license_guard
import reconcile
import store
import store_web
import task_api
import web_console

app = Flask(__name__)

# 模块导入时即建表（幂等），gunicorn/docker 方式启动也能正常初始化
store.init()

# ---- Web 控制台 + 执行端任务通道（独立模块，非侵入式接入）----
# web_console: 控制台页面/用户管理/账户查询/下单/审计日志
# task_api   : 执行端拉取控制台任务的接口（/api/task/poll、/api/task/feedback）
web_console.register(app)
task_api.register(app)
reconcile.start_auto()   # v1.7.0 盘后自动对账（RELAY_RECONCILE_AUTO=0 可关闭）
# ---- 授权守卫（v1.8）：未激活时全站只显示激活页；RELAY_LICENSE_ENABLED=0 可关闭 ----
license_guard.register(app)


# ---------------- 鉴权 ----------------

def _check_key(required_key, endpoint=""):
    """校验请求头 X-API-Key。返回错误响应或 None。失败时记录到聚宽交互详录。"""
    key = request.headers.get("X-API-Key", "")
    if key != required_key:
        store.record_reject(endpoint or request.path, "401_auth",
                            "X-API-Key 无效（收到 %d 字符）" % len(key), _client_ip())
        return jsonify(ok=False, error="无效的 X-API-Key"), 401
    return None


def _get_json(endpoint=""):
    """安全解析 JSON body。失败时记录到聚宽交互详录。"""
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        store.record_reject(endpoint or request.path, "400_json",
                            "body 不是合法 JSON", _client_ip())
        return None, (jsonify(ok=False, error="body 不是合法 JSON"), 400)
    if not isinstance(data, dict):
        store.record_reject(endpoint or request.path, "400_json",
                            "body 必须是 JSON 对象", _client_ip())
        return None, (jsonify(ok=False, error="body 必须是 JSON 对象"), 400)
    return data, None


def _client_ip():
    """来源 IP（取真实远端；兼容挂在反代后面的场景）。"""
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr or "-")


# ---------------- 接口 ----------------

@app.route("/api/signal", methods=["POST"])
def api_signal():
    """聚宽上报信号。必填字段：order_id / security / side / amount，可选 price / jq_status。"""
    err = _check_key(config.SIGNAL_API_KEY, "/api/signal")
    if err:
        return err
    data, err = _get_json("/api/signal")
    if err:
        return err

    for f in ("order_id", "security", "side", "amount"):
        if not data.get(f):
            store.record_reject("/api/signal", "400_field",
                                "缺少必填字段: %s" % f, _client_ip())
            return jsonify(ok=False, error="缺少必填字段: %s" % f), 400
    if data["side"] not in ("buy", "sell"):
        store.record_reject("/api/signal", "400_field",
                            "side 非法: %s" % data["side"], _client_ip())
        return jsonify(ok=False, error="side 必须是 buy 或 sell"), 400
    try:
        data["amount"] = int(data["amount"])
    except (TypeError, ValueError):
        store.record_reject("/api/signal", "400_field",
                            "amount 非整数: %s" % data["amount"], _client_ip())
        return jsonify(ok=False, error="amount 必须是整数"), 400
    if data["amount"] <= 0:
        store.record_reject("/api/signal", "400_field",
                            "amount<=0: %s" % data["amount"], _client_ip())
        return jsonify(ok=False, error="amount 必须大于 0"), 400

    # 积压保护：执行端长时间不拉单就拒收，避免堆积的旧信号被突然批量执行
    summary = store.status_summary()
    if summary["pending"] >= config.MAX_PENDING:
        store.record_reject("/api/signal", "503_backlog",
                            "积压 %d 条已达上限" % summary["pending"], _client_ip())
        return jsonify(ok=False, error="积压信号已达上限，请先检查执行端"), 503

    data["src_ip"] = _client_ip()          # v1.5.0：记录信号来源 IP
    row, duplicated = store.insert_signal(data)
    return jsonify(ok=True, duplicated=duplicated, signal_id=row["id"],
                   status=row["status"])


@app.route("/api/signal/poll", methods=["POST"])
def api_poll():
    """执行端拉单：一次最多 limit 条，领走即标记 dispatched。"""
    err = _check_key(config.EXECUTOR_API_KEY, "/api/signal/poll")
    if err:
        return err
    data, err = _get_json("/api/signal/poll")
    if err:
        return err
    limit = min(int(data.get("limit", 10) or 10), 50)
    return jsonify(ok=True, signals=store.take_pending(limit))


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """执行端回报。字段：signal_id, result(accepted/filled/failed), message。"""
    err = _check_key(config.EXECUTOR_API_KEY, "/api/feedback")
    if err:
        return err
    data, err = _get_json("/api/feedback")
    if err:
        return err
    sid = data.get("signal_id")
    result = data.get("result", "")
    if not sid or result not in ("accepted", "filled", "failed"):
        store.record_reject("/api/feedback", "400_field",
                            "signal_id=%s result=%s" % (sid, result), _client_ip())
        return jsonify(ok=False, error="需要 signal_id 和 result(accepted/filled/failed)"), 400
    store.add_feedback(int(sid), result, str(data.get("message", ""))[:500])
    store.set_signal_status(int(sid), result)
    return jsonify(ok=True)


@app.route("/api/jq_positions", methods=["POST"])
def api_jq_positions():
    """聚宽上报持仓快照（盘后对账用）。
    body: {"positions":[{"security":"600519.XSHG","total_amount":100,...}]}"""
    err = _check_key(config.SIGNAL_API_KEY, "/api/jq_positions")
    if err:
        return err
    data, err = _get_json("/api/jq_positions")
    if err:
        return err
    pos = data.get("positions")
    if not isinstance(pos, list):
        store.record_reject("/api/jq_positions", "400_field",
                            "positions 不是数组", _client_ip())
        return jsonify(ok=False, error="positions 必须是数组"), 400
    rows = []
    for p in pos[:500]:
        code = reconcile.plain_code(p.get("security") or p.get("code") or "")
        try:
            amount = int(float(p.get("total_amount") or p.get("amount") or 0))
        except (TypeError, ValueError):
            amount = 0
        if not code.isdigit() or amount <= 0:
            continue
        rows.append({"code": code, "amount": amount,
                     "name": str(p.get("name") or "")[:30]})
    store_web.save_jq_positions(rows)
    return jsonify(ok=True, saved=len(rows))


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    err = _check_key(config.EXECUTOR_API_KEY, "/api/heartbeat")
    if err:
        return err
    data, _ = _get_json("/api/heartbeat")
    store.heartbeat(str((data or {}).get("name", "executor"))[:50])
    return jsonify(ok=True)


@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify(ok=True, version=config.VERSION,
                   license=license_guard.snapshot(), **store.status_summary())


@app.route("/api/signals", methods=["GET"])
def api_signals():
    return jsonify(ok=True, signals=store.recent_signals())


@app.errorhandler(HTTPException)
def on_http_error(e):
    """HTTP 异常（404/405 等）保持原状态码，返回 JSON。
    注意：必须注册在 Exception 之前，否则会被下面的兜底处理器一律变成 500，
    掩盖真实错误码（Web 控制台前端要靠 401/403/429/503 做分支提示）。"""
    return jsonify(ok=False, error=e.description), e.code


@app.errorhandler(Exception)
def on_error(e):
    app.logger.exception(e)
    return jsonify(ok=False, error="服务器内部错误: %s" % e), 500


if __name__ == "__main__":
    # 「中转服务-配置.bat」用它查看当前生效的配置（打印完就退出，不启动服务）
    if "--show-config" in sys.argv:
        print(config.startup_report())
        raise SystemExit(0)
    store.init()
    # 启动摘要：把生效的配置和来源打印出来（密钥已脱敏），方便用户确认自己改的配置生效了
    print(config.startup_report())
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)
