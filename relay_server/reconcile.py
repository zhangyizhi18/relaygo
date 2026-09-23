# -*- coding: utf-8 -*-
"""
盘后自动对账（v1.7.0）—— 聚宽策略持仓 vs 券商实际持仓。

数据来源：
  聚宽侧  策略在 after_market_close 里调用 joinquant/jq_signal.py 的
          report_positions(context)，把持仓快照 POST 到 /api/jq_positions；
  券商侧  通过任务队列下发 query_positions，由执行端实时读券商客户端
          （复用既有通道，执行端零改动）。

对账逻辑：以 6 位代码为键做全量比对，输出差额与单边缺失明细。
  完全一致 -> ok；有差异 -> mismatch；聚宽没报过快照 -> no_snapshot；
  执行端离线 -> executor_offline；券商读到空表 -> no_broker；其他 -> failed。

自动调度：start_auto() 起一个守护线程，交易日(周一~五) RECONCILE_AT 时刻
自动跑一次；执行端离线只记一次结果，不重试、绝不阻塞主服务。
"""
import threading
import time
from datetime import datetime

import config
import store
import store_web


def plain_code(code):
    """任意格式代码 -> 6 位纯数字。600519.XSHG / 600519.SH / 600519 -> 600519"""
    return str(code or "").strip().split(".")[0]


def parse_broker_positions(rows):
    """把执行端查回的券商持仓行（THS/QMT 列名不一）规整成 {code: {name, amount}}。"""
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = plain_code(r.get("证券代码") or r.get("代码") or r.get("code"))
        if not code.isdigit():
            continue
        amount = (r.get("持仓数量") if r.get("持仓数量") is not None
                  else r.get("总数量") if r.get("总数量") is not None
                  else r.get("amount"))
        try:
            amount = int(float(amount))
        except (TypeError, ValueError):
            continue
        out[code] = {"name": str(r.get("证券名称") or r.get("name") or ""), "amount": amount}
    return out


def _compare(jq, broker):
    """纯函数，方便单测。jq/broker: {code: amount}。
    返回 (status, items)；diff = 券商 - 聚宽（正=券商多，负=券商少）。"""
    items = []
    for c in sorted(set(jq) | set(broker)):
        a, b = jq.get(c), broker.get(c)
        if a is None:
            items.append({"code": c, "name": "", "jq": 0, "broker": b,
                          "diff": b, "note": "聚宽快照中无此持仓"})
        elif b is None:
            items.append({"code": c, "name": "", "jq": a, "broker": 0,
                          "diff": -a, "note": "券商持仓中无此代码"})
        else:
            items.append({"code": c, "name": "", "jq": a, "broker": b,
                          "diff": b - a,
                          "note": "" if a == b else "数量不一致"})
    status = "mismatch" if any(it["diff"] != 0 for it in items) else "ok"
    return status, items


def run_reconcile(trigger="manual", wait=None):
    """执行一次对账，结果落库并返回 dict：
    {status, summary, items, jq_ts?}。任何分支都不抛异常。"""
    wait = wait or (config.TASK_WAIT_SECONDS + 10)

    # 1. 聚宽快照
    snap = store_web.get_jq_positions()
    if not snap:
        return _finish(trigger, "no_snapshot",
                       "聚宽端还没有上报持仓快照（请在策略里加 after_market_close 调 report_positions）")

    # 2. 执行端在线
    hb = store.status_summary().get("last_heartbeat")
    online = False
    if hb:
        try:
            t = datetime.strptime(hb["ts"], "%Y-%m-%d %H:%M:%S")
            online = (datetime.now() - t).total_seconds() <= config.EXECUTOR_ALIVE_SECONDS
        except Exception:
            pass
    if not online:
        return _finish(trigger, "executor_offline", "执行端离线，无法读取券商持仓")

    # 3. 下发查询任务并等待（复用控制台任务通道）
    ok, msg, tid = store_web.create_task("query_positions", {},
                                         "reconcile:%s" % trigger)
    if not ok:
        return _finish(trigger, "failed", "创建查询任务失败: %s" % msg)
    task = None
    deadline = time.time() + wait
    while time.time() < deadline:
        task = store_web.get_task_parsed(tid)
        if task and task["status"] in ("done", "failed", "timeout"):
            break
        time.sleep(1)
    if not task or task["status"] != "done":
        return _finish(trigger, "failed",
                       (task or {}).get("message") or "执行端查询持仓超时")

    # 4. 比对
    broker = parse_broker_positions(task.get("result") or [])
    if not broker:
        return _finish(trigger, "no_broker",
                       "券商侧读到空持仓（可能被弹窗拦截，或确实空仓）；请重试或人工核对")
    jq = {plain_code(r["code"]): int(r["amount"]) for r in snap}
    names = {plain_code(r["code"]): r.get("name", "") for r in snap}
    status, items = _compare(jq, {c: v["amount"] for c, v in broker.items()})
    bnames = {c: v["name"] for c, v in broker.items()}
    for it in items:
        it["name"] = names.get(it["code"]) or bnames.get(it["code"]) or ""
    diff_n = sum(1 for it in items if it["diff"] != 0)
    summary = ("持仓一致，共比对 %d 只" % len(items) if status == "ok"
               else "发现 %d 处差异（共比对 %d 只），请人工核实" % (diff_n, len(items)))
    return _finish(trigger, status, summary, items=items,
                   jq_ts=snap[0].get("updated_at", ""))


def _finish(trigger, status, summary, items=None, jq_ts=""):
    res = {"status": status, "summary": summary, "items": items or []}
    if jq_ts:
        res["jq_ts"] = jq_ts
    try:
        store_web.save_reconcile(trigger, status, res)
        store_web.audit(str(trigger), "reconcile", target="持仓对账",
                        detail=summary, result="ok" if status == "ok" else status)
    except Exception:
        pass
    return res


# ---------------- 自动调度 ----------------

def _is_trade_day(now):
    """周一~周五视为交易日（不识别法定节假日，节假日执行端查询照样安全，只是结果=当天真实持仓）。"""
    return now.weekday() < 5


def start_auto():
    """启动盘后自动对账守护线程（RECONCILE_AUTO=0 时不启动）。返回线程或 None。"""
    if not config.RECONCILE_AUTO:
        return None
    t = threading.Thread(target=_loop, name="reconcile-auto", daemon=True)
    t.start()
    return t


def _loop():
    last_date = None
    while True:
        try:
            now = datetime.now()
            if (_is_trade_day(now)
                    and now.strftime("%H:%M") == config.RECONCILE_AT
                    and last_date != now.date()):
                last_date = now.date()
                res = run_reconcile(trigger="auto")
                print("[盘后对账] %s -> %s" % (res.get("status"), res.get("summary", "")))
        except Exception as e:
            try:
                print("[盘后对账] 自动对账异常: %s" % e)
            except Exception:
                pass
        time.sleep(20)
