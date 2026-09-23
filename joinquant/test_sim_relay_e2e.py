# -*- coding: utf-8 -*-
"""
真实链路 e2e：模拟器 -> 中转(5099) -> dry_run 执行端 -> 回报。
覆盖：市价/限价/挂单状态信号、重发幂等、错key、怪参数、持仓快照、回报闭环。
运行: venv python joinquant/test_sim_relay_e2e.py
"""
import json
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from jq_sim_gui import SimEngine  # noqa: E402

URL = "http://127.0.0.1:5099"
KEY = "jq-signal-key-2026-change-me"
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("OK   %s" % name)
    else:
        FAIL += 1
        print("FAIL %s  %s" % (name, detail))


def resp_code(ret):
    """SimEngine.post 返回 '200 {...}' 或 'HTTP 401 {...}'。统一取状态码。"""
    if not ret:
        return ""
    parts = ret.split()
    return parts[1] if parts[0] == "HTTP" else parts[0]


def wait_status(oid, want, timeout=15):
    """轮询 /api/signals 直到该 order_id 的 status 变为 want。"""
    import urllib.request
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            r = urllib.request.build_opener(
                urllib.request.ProxyHandler({})).open(URL + "/api/signals", timeout=3)
            rows = json.loads(r.read().decode("utf-8")).get("signals", [])
            for row in rows:
                if str(row.get("jq_order_id")) == str(oid):
                    if row.get("status") == want:
                        return True, row
        except Exception as e:
            last_err = e
        time.sleep(1)
    return False, last_err or "timeout"


def main():
    # 服务在线
    import urllib.request
    op = __import__("urllib.request", fromlist=["build_opener"])
    r = op.build_opener(op.ProxyHandler({})).open(URL + "/api/status", timeout=3)
    st = json.loads(r.read().decode())
    check("中转服务在线 %s" % st.get("version"), bool(st.get("ok")))

    e = SimEngine()
    e._oid = int(time.time())          # 每次运行唯一，避免与上次临时库撞 order_id
    e.add_security("600519.XSHG", "贵州茅台", 1500.0)
    e.add_security("510300.XSHG", "沪深300ETF", 3.923)

    # ---- 1. 市价买入（held, price=最新价） ----
    o1 = e.order("600519.XSHG", 100)
    ok1, r1 = SimEngine.post("/api/signal", SimEngine.build_payload(o1), URL, KEY)
    check("市价信号受理 200", ok1 and resp_code(r1) == "200", r1)
    sid1 = json.loads(r1[r1.index("{"):])["signal_id"] if ok1 else None

    # ---- 2. 限价卖出挂单（open, price=委托价） ----
    e.t1 = False
    o2 = e.order("600519.XSHG", -50, limit_price=1600.0)
    ok2, r2 = SimEngine.post("/api/signal", SimEngine.build_payload(o2), URL, KEY)
    check("限价挂单信号受理 200(jq_status=open)",
          ok2 and json.loads(r2[r2.index("{"):]).get("duplicated") is False, r2)

    # ---- 3. 重发幂等 ----
    ok3, r3 = SimEngine.post("/api/signal", SimEngine.build_payload(o1), URL, KEY)
    check("重发被识别幂等 duplicated=true",
          ok3 and json.loads(r3[r3.index("{"):]).get("duplicated") is True, r3)

    # ---- 4. 错 key 401 ----
    ok4, r4 = SimEngine.post("/api/signal", SimEngine.build_payload(o1), URL, "wrong-key")
    check("错 key 被 401 拒绝", (not ok4) and resp_code(r4) == "401", r4)

    # ---- 5. 怪参数 ----
    ok5, r5 = SimEngine.post("/api/signal",
                             {"order_id": "T0-" + str(int(time.time()*10)), "security": "510300.XSHG",
                              "side": "buy", "amount": 100, "price": 0,
                              "jq_status": "held"}, URL, KEY)
    check("price=0 受理（执行端按市价兜底）", ok5, r5)
    ok6, r6 = SimEngine.post("/api/signal",
                             {"order_id": "T1-" + str(int(time.time()*10)), "security": "600519.XSHG",
                              "side": "buy", "amount": -5}, URL, KEY)
    check("amount 负数被 400 拒", (not ok6) and resp_code(r6) == "400", r6)
    ok7, r7 = SimEngine.post("/api/signal",
                             {"order_id": "T2-" + str(int(time.time()*10)), "security": "600519.XSHG",
                              "side": "long", "amount": 100}, URL, KEY)
    check("side 非法被 400 拒", (not ok7) and resp_code(r7) == "400", r7)
    ok8, r8 = SimEngine.post("/api/signal",
                             {"order_id": "T3-" + str(int(time.time()*10)), "security": "600519.XSHG",
                              "side": "buy", "amount": "300", "price": 1501.55},
                             URL, KEY)
    check("amount 字符串数字被归一受理", ok8, r8)
    ok9, r9 = SimEngine.post("/api/signal", [1, 2, 3], URL, KEY)
    check("body 数组被 400 拒", (not ok9) and resp_code(r9) == "400", r9)

    # ---- 6. 持仓快照 ----
    okp, rp = SimEngine.post("/api/jq_positions",
                             {"positions": e.positions_snapshot()}, URL, KEY)
    check("持仓快照上报 saved>=1",
          okp and json.loads(rp[rp.index("{"):]).get("saved", 0) >= 1, rp)

    # ---- 7. 回报闭环（dry_run 执行端拉单并回报 accepted） ----
    got, row = wait_status(o1["order_id"], "accepted", timeout=20)
    check("市价信号被执行端回报 accepted", got, row)

    print("\n===== 链路 e2e: %d 过 / %d 挂 =====" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
