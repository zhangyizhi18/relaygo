# -*- coding: utf-8 -*-
"""
【模拟聚宽】没有聚宽账号也能跑通全链路 —— 本脚本扮演"聚宽云端"，向中转服务发信号。

两种用法：

1) 交互模式（推荐，像聊天一样下单）：
     python tools/jq_simulator.py
   支持的命令：
     buy  600519.XSHG 100 1500.5      -> 买入 贵州茅台 100股 @1500.5
     sell 000001.XSHE 200 12.5        -> 卖出 平安银行 200股 @12.5
     buy  510300.XSHG 300             -> 不填价格 = 让执行端自己决定（市价意图）
     status                           -> 查看中转服务的信号统计和最近信号
     demo                             -> 自动发 3 条示例信号
     help / quit

2) 一键演示模式（不开交互，适合快速验证）：
     python tools/jq_simulator.py demo

前置：中转服务已启动（relay_server/app.py）。
可选：执行端已启动（executor/main.py, MODE=dry_run）时，发出的信号会被
     执行端拉走、过风控、记为 accepted，形成完整闭环。
"""
import json
import random
import sys
import time
from datetime import datetime

import requests

# ============ 连接配置（与 relay_server/config.py 保持一致） ============
RELAY_URL = "http://127.0.0.1:5010"
SIGNAL_API_KEY = "jq-signal-key-2026-change-me"
# ======================================================================

HDR = {"X-API-Key": SIGNAL_API_KEY}


def send_signal(security, side, amount, price=None):
    """发一条信号，order_id 自动生成（SIM-日期-时间-随机码，保证唯一）。"""
    order_id = "SIM-%s-%04d" % (datetime.now().strftime("%Y%m%d%H%M%S"),
                                random.randint(1, 9999))
    payload = {
        "order_id": order_id,
        "security": security,
        "side": side,
        "amount": int(amount),
        "price": float(price) if price else None,
        "jq_status": "open",          # 模拟"聚宽已接受订单"状态
    }
    try:
        r = requests.post(RELAY_URL.rstrip("/") + "/api/signal",
                          json=payload, headers=HDR, timeout=5)
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            tag = "（重复信号，已被幂等去重）" if data.get("duplicated") else ""
            print("  ✔ 信号已送达中转服务  单号=%s  %s %s x%s %s%s"
                  % (order_id, side.upper(), security, amount,
                     "@%s " % price if price else "", tag))
        else:
            print("  ✘ 中转服务拒绝: HTTP %s %s" % (r.status_code, r.text[:150]))
    except requests.RequestException as e:
        print("  ✘ 连不上中转服务（%s）" % e)
        print("    请先启动: cd relay_server && python app.py")


def show_status():
    try:
        s = requests.get(RELAY_URL + "/api/status", timeout=5).json()
        print("  中转服务状态: 待处理=%s 已发放=%s 已受理=%s 已成交=%s 失败=%s 作废=%s"
              % (s.get("pending"), s.get("dispatched"), s.get("accepted"),
                 s.get("filled"), s.get("failed"), s.get("expired")))
        hb = s.get("last_heartbeat")
        if hb:
            print("  执行端心跳: %s（%s）" % (hb.get("ts"), hb.get("detail")))
        else:
            print("  执行端心跳: 无 —— 执行端未启动，信号会一直积压在中转服务")
        sigs = requests.get(RELAY_URL + "/api/signals", timeout=5).json().get("signals", [])
        if sigs:
            print("  最近信号:")
            for x in sigs[:8]:
                print("   #%s  %s  %s  %s x%s @%s  状态=%s  [%s]"
                      % (x["id"], x["created_at"], x["security"], x["side"],
                         x["amount"], x["price"], x["status"], x["jq_order_id"]))
    except requests.RequestException as e:
        print("  ✘ 连不上中转服务: %s" % e)


def send_demo():
    print("发送 3 条示例信号（模拟聚宽调仓）……")
    send_signal("600519.XSHG", "buy", 100, 1500.5)
    send_signal("510300.XSHG", "buy", 300, 4.1)
    send_signal("000001.XSHE", "sell", 200, 12.5)
    print("完成。用 status 命令或 python tools/test_signal.py 查看处理结果。")


HELP = """  可用命令：
    buy  <代码> <数量> [价格]      例: buy 600519.XSHG 100 1500.5
    sell <代码> <数量> [价格]      例: sell 000001.XSHE 200 12.5
    demo                           发送 3 条示例信号
    status                         查看中转服务状态和最近信号
    help                           显示本帮助
    quit / exit                    退出"""


def repl():
    print("=" * 60)
    print("模拟聚宽 · 交互模式   目标中转: %s" % RELAY_URL)
    print(HELP)
    print("=" * 60)
    while True:
        try:
            line = input("模拟聚宽> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n退出")
            return
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        if cmd in ("quit", "exit", "q"):
            print("退出")
            return
        elif cmd == "help":
            print(HELP)
        elif cmd == "demo":
            send_demo()
        elif cmd == "status":
            show_status()
        elif cmd in ("buy", "sell"):
            if len(parts) < 3:
                print("  ✘ 格式: %s <代码> <数量> [价格]，例: %s 600519.XSHG 100 1500.5"
                      % (cmd, cmd))
                continue
            sec, amount = parts[1], parts[2]
            price = parts[3] if len(parts) > 3 else None
            try:
                amount = int(amount)
            except ValueError:
                print("  ✘ 数量必须是整数"); continue
            if price is not None:
                try:
                    price = float(price)
                except ValueError:
                    print("  ✘ 价格必须是数字"); continue
            send_signal(sec, cmd, amount, price)
        else:
            print("  ✘ 不认识的命令，输入 help 查看用法")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "demo":
        send_demo()
        time.sleep(1)
        show_status()
    else:
        repl()
