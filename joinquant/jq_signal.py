# -*- coding: utf-8 -*-
"""
【聚宽策略端代码】把本文件全部内容复制到聚宽策略的"最开头"即可。

作用：包装聚宽的 order / order_target / order_value / order_target_value 四个下单函数，
     每次聚宽侧下单成功后，自动把订单信息 POST 到你的中转服务器。策略主体逻辑零改动。

使用前必须修改的三行：
  RELAY_URL  -> 你的中转服务地址（本机测试 http://127.0.0.1:5010，
                云服务器则填 http://服务器IP:5010，并在云控制台放行该端口）
  API_KEY    -> 与 relay_server/config.py 里的 SIGNAL_API_KEY 一致
  （可选）DRY -> True 时只打印不上报，用于策略端自测

注意：
  1. 聚宽策略环境支持 requests；上报失败只记日志不影响策略运行。
  2. order_id 做幂等键，中转端自动去重，网络重试不会造成重复下单。
  3. 只上报"已提交(open)/已成交(held)"的订单，撤单/废单不上报。
"""

import json
import time
import requests

# ================== 修改这三行 ==================
RELAY_URL = "http://127.0.0.1:5010"
API_KEY = "jq-signal-key-2026-change-me"
DRY = False          # True=只打印不上报（联调用）
# ================================================

# 回测/模拟里 order_obj.price 是【历史模拟价】（回测跑的是过去的数据），
# 拿它去真实下单必然与现价对不上、被同花顺拒绝（2026-09-13 实测 16.21 vs 现价 7.74）。
# FORCE_MARKET=True 时上报信号一律不带价格（price=None），
# 执行端会自动取【实时最新价】下单 —— 与控制台手动市价下单完全同一条路径。
# 如果你确实想按信号里的价格限价下单，把它改成 False。
FORCE_MARKET = True

# 云端部署走 Cloudflare 等 CDN 时必须伪装成浏览器 UA，否则被
# Bot 防护拦为 403 error 1010（请求根本到不了中转服务）；
# 聚宽云端机房到 CDN 边缘的路由较慢，超时也不能太紧（实测 3s 会超时）。
_HEADERS = {
    "X-API-Key": API_KEY,
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Content-Type": "application/json",
}
_TIMEOUT = 10


def _post(path, payload):
    """POST 上报：失败自动重试一次（order_id 幂等，重试不会重复下单）。"""
    url = RELAY_URL.rstrip("/") + path
    try:
        return requests.post(url, json=payload, headers=_HEADERS,
                             timeout=_TIMEOUT)
    except Exception as first:
        print("[信号] 上报失败，重试一次: %s" % str(first)[:120])
        return requests.post(url, json=payload, headers=_HEADERS,
                             timeout=_TIMEOUT)


def send_test_signal():
    """连通性自测：在 initialize() 里调用一次，上报一条假信号。

    证券代码故意用 999999.XSHG（不存在），执行端即使收到也会拒单，
    不会产生真实委托。测试成功的标志：
      1. 聚宽日志打印 [信号测试] 已上报 ... {"ok": true, ...}
      2. 中转控制台「信号时间线」出现 order_id=test- 开头的记录
         （若执行端在线，状态会变成"失败/拒单"，同样是链路通的证明）
    """
    payload = {
        "order_id": "test-%d" % int(time.time()),
        "security": "999999.XSHG",
        "side": "buy",
        "amount": 100,
        "price": None,
        "jq_status": "open",
    }
    if DRY:
        print("[信号测试DRY] %s" % json.dumps(payload, ensure_ascii=False))
        return
    try:
        r = _post("/api/signal", payload)
        print("[信号测试] 已上报 %s -> HTTP %s %s" % (payload["order_id"], r.status_code, r.text[:120]))
    except Exception as e:
        print("[信号测试] 失败（不影响策略）: %s" % e)


def _report_order(order_obj):
    """把聚宽订单对象转成中转服务的信号格式并上报。"""
    try:
        if order_obj is None:
            return
        status = str(order_obj.status)          # open=已提交 held=全部成交
        if status not in ("open", "held"):
            return                              # 撤单/废单/未成交不上报
        payload = {
            "order_id": str(order_obj.order_id),        # 幂等键，防重复
            "security": str(order_obj.security),        # 600519.XSHG
            "side": "buy" if order_obj.is_buy else "sell",
            "amount": int(order_obj.amount),
            # FORCE_MARKET=True：不传回测/模拟里的历史价，执行端自动取实时价下单
            "price": None if FORCE_MARKET else (float(order_obj.price) if order_obj.price else None),
            "jq_status": status,
        }
        if DRY:
            print("[信号DRY] %s" % json.dumps(payload, ensure_ascii=False))
            return
        r = _post("/api/signal", payload)
        print("[信号] 已上报 %s -> %s" % (payload["order_id"], r.text[:120]))
    except Exception as e:
        # 上报失败绝不能影响策略本身，但要留下痕迹方便排查
        print("[信号] 上报失败（不影响策略）: %s" % e)


# ---------------- 盘后对账（v1.7.0，可选但强烈建议） ----------------
# 在策略里加下面两行，每个交易日收盘后自动把持仓快照发给中转服务，
# 控制台「账户查询 → 持仓对账」就能自动比对聚宽与券商的持仓是否一致：
#     def after_market_close(context):
#         report_positions(context)

def report_positions(context):
    """把聚宽策略当前持仓快照 POST 给中转服务（用于盘后对账）。"""
    try:
        positions = []
        for sec, p in context.portfolio.positions.items():
            positions.append({
                "security": str(sec),
                "name": str(getattr(p, "security_name", "") or "")[:30],
                "total_amount": int(getattr(p, "total_amount", 0)),
                "closeable_amount": int(getattr(p, "closeable_amount", 0)),
            })
        r = _post("/api/jq_positions", {"positions": positions})
        print("[信号] 持仓快照已上报 %d 只 -> %s" % (len(positions), r.text[:120]))
    except Exception as e:
        # 上报失败不影响策略，控制台会显示「无聚宽快照」提醒你检查
        print("[信号] 持仓快照上报失败（不影响策略）: %s" % e)


# ---------------- 包装聚宽下单函数（非侵入式） ----------------

_orig_order = order
_orig_order_target = order_target
_orig_order_value = order_value
_orig_order_target_value = order_target_value


def order(security, amount, style=None, side='long'):
    result = _orig_order(security, amount, style=style, side=side)
    _report_order(result)
    return result


def order_target(security, amount, style=None, side='long'):
    result = _orig_order_target(security, amount, style=style, side=side)
    _report_order(result)
    return result


def order_value(security, value, style=None, side='long'):
    result = _orig_order_value(security, value, style=style, side=side)
    _report_order(result)
    return result


def order_target_value(security, value, style=None, side='long'):
    result = _orig_order_target_value(security, value, style=style, side=side)
    _report_order(result)
    return result


print("[信号中转] 下单函数包装完成 -> %s" % RELAY_URL)

def initialize(context):
    send_test_signal()   