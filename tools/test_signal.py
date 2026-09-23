# -*- coding: utf-8 -*-
"""
联调测试脚本（阶段0 用）：模拟聚宽上报信号 + 模拟执行端拉单/回报，全链路验证中转服务。
不需要安装 easytrader/xtquant，不需要真实券商。

用法：
  1. 先启动中转服务:  python relay_server/app.py
  2. 再运行本脚本:    python tools/test_signal.py
"""
import json

import requests

RELAY = "http://127.0.0.1:5010"
SIGNAL_KEY = "jq-signal-key-2026-change-me"      # 与 relay_server/config.py 一致
EXECUTOR_KEY = "executor-key-2026-change-me"


def hdr(key):
    return {"X-API-Key": key}


def main():
    print("=" * 56)
    print("步骤1: 模拟聚宽上报 3 条信号（第 2 条与第 1 条同 order_id，验证幂等）")
    sigs = [
        {"order_id": "TEST-0001", "security": "600519.XSHG", "side": "buy",
         "amount": 100, "price": 1500.0, "jq_status": "open"},
        {"order_id": "TEST-0001", "security": "600519.XSHG", "side": "buy",
         "amount": 100, "price": 1500.0, "jq_status": "open"},   # 重复信号
        {"order_id": "TEST-0002", "security": "000001.XSHE", "side": "sell",
         "amount": 200, "price": 12.5, "jq_status": "held"},
    ]
    for s in sigs:
        r = requests.post(RELAY + "/api/signal", json=s, headers=hdr(SIGNAL_KEY), timeout=5)
        print("  上报 %s -> HTTP %s %s" % (s["order_id"], r.status_code, r.text[:100]))

    print("\n步骤2: 验证错误密钥被拒")
    r = requests.post(RELAY + "/api/signal", json=sigs[0], headers=hdr("wrong-key"), timeout=5)
    print("  wrong-key -> HTTP %s（预期 401）" % r.status_code)

    print("\n步骤3: 模拟执行端拉单")
    r = requests.post(RELAY + "/api/signal/poll", json={"limit": 10},
                      headers=hdr(EXECUTOR_KEY), timeout=5)
    data = r.json()
    got = data.get("signals", [])
    print("  拉到 %d 条（预期 2 条：重复的那条已被幂等去重）" % len(got))
    for s in got:
        print("   - #%s %s %s %s x%s @%s" % (s["id"], s["security"], s["side"],
                                             s["jq_status"], s["amount"], s["price"]))

    print("\n步骤4: 模拟执行端回报")
    for s in got:
        result = "accepted"
        msg = "[测试] 已模拟提交"
        r = requests.post(RELAY + "/api/feedback",
                          json={"signal_id": s["id"], "result": result, "message": msg},
                          headers=hdr(EXECUTOR_KEY), timeout=5)
        print("  回报 #%s -> HTTP %s %s" % (s["id"], r.status_code, r.text[:80]))

    print("\n步骤5: 心跳 + 状态汇总")
    requests.post(RELAY + "/api/heartbeat", json={"name": "test-runner"},
                  headers=hdr(EXECUTOR_KEY), timeout=5)
    r = requests.get(RELAY + "/api/status", timeout=5)
    print("  " + json.dumps(r.json(), ensure_ascii=False, indent=2))

    print("\n全部通过 => 中转服务链路正常！"
          "\n下一步: 启动执行端 python executor/main.py（MODE=dry_run）做端到端演练。")


if __name__ == "__main__":
    main()
