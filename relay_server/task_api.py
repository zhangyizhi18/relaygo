# -*- coding: utf-8 -*-
"""
执行端任务通道 —— Web 控制台 <-> 执行端的命令接口（与聚宽信号通道并行、互不干扰）。

接口（都要求 X-API-Key = EXECUTOR_API_KEY）：
  POST /api/task/poll      执行端拉任务：领走 pending 任务并标记 running
  POST /api/task/feedback  执行端回报任务结果

为什么单独成文件：这条通道服务于"人在控制台上的操作"（查资金/查持仓/下单/撤单），
与聚宽策略信号（signals 表）是两条独立链路，分开以后互不影响，也便于单独排查。

非侵入式接入（app.py 只加两行）：
    import task_api
    task_api.register(app)
"""
from flask import Blueprint, jsonify, request

import config
import store_web

bp = Blueprint("taskapi", __name__)


def _check_key():
    if request.headers.get("X-API-Key", "") != config.EXECUTOR_API_KEY:
        return jsonify(ok=False, error="无效的 X-API-Key"), 401
    return None


@bp.route("/api/task/poll", methods=["POST"])
def api_task_poll():
    """执行端拉取任务。建议一次只领 1 条（券商 UI 操作是串行的）。"""
    err = _check_key()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        limit = int(data.get("limit", 1) or 1)
    except (TypeError, ValueError):
        limit = 1
    return jsonify(ok=True, tasks=store_web.take_tasks(max(1, min(limit, 5))))


@bp.route("/api/task/feedback", methods=["POST"])
def api_task_feedback():
    """执行端回报任务结果。字段：task_id, ok(bool), result(任意 JSON), message。"""
    err = _check_key()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    if task_id is None:
        return jsonify(ok=False, error="缺少 task_id"), 400
    try:
        task_id = int(task_id)
    except (TypeError, ValueError):
        return jsonify(ok=False, error="task_id 必须是整数"), 400
    store_web.finish_task(task_id, bool(data.get("ok")),
                          result=data.get("result"),
                          message=str(data.get("message", "")))
    return jsonify(ok=True)


def register(app):
    app.register_blueprint(bp)
