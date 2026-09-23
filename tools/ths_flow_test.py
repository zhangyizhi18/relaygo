# -*- coding: utf-8 -*-
"""
RelayGo 全流程真实下单测试（信号 -> 中转 -> 执行端 -> 同花顺 -> 查委托 -> 撤单）
重点验证：同花顺界面里 证券代码/委托价格/委托数量 三个框是否各自填对
          （2026-09-11 bug：价格和数量被写成了证券代码）

安全设计：价格取现价 -1%（既在涨跌停内、又在风控 3% 偏差内），且挂单不可能成交；
         全流程结束自动撤单，不留下委托。

运行: venv/Scripts/python.exe tools/ths_flow_test.py
"""
import json
import sys
import time
import uuid

import requests

BASE = "http://127.0.0.1:5010"
SIGNAL_KEY = "jq-signal-key-2026-change-me"
ADMIN, ADMIN_PWD = "admin", "admin123"

S = requests.Session()
S.trust_env = False        # 绕开系统代理

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("OK   %s" % name, flush=True)
    else:
        FAIL += 1
        print("FAIL %s  %s" % (name, detail), flush=True)
    return cond


def entrusts():
    r = S.get(BASE + "/console/api/account", params={"kind": "entrusts"}, timeout=60)
    j = r.json()
    if not j.get("ok"):
        print("     查委托失败:", j.get("error"), flush=True)
        return []
    return j.get("data") or []


def main():
    # ---------- 1. 登录控制台 ----------
    r = S.post(BASE + "/console/api/login",
               json={"username": ADMIN, "password": ADMIN_PWD}, timeout=15)
    check("控制台登录", r.status_code == 200 and r.json().get("ok"), r.text[:120])

    # ---------- 2. 取最新价，算安全的测试价 ----------
    sys.path.insert(0, "executor")
    import market_price
    latest = market_price.fetch_latest("510300")
    check("取 510300 最新价", latest is not None, latest)
    price = round(latest * 0.99, 3)          # 现价-1%：挂单不成交，且不触发风控/涨跌停
    print("     最新价=%s  测试委托价=%s（现价-1%%）" % (latest, price), flush=True)

    # ---------- 3. 记录基线委托 ----------
    before = entrusts()
    before_no = {str(x.get("合同编号")) for x in before}
    print("     下单前委托 %d 笔: %s" % (len(before),
          [(x.get("合同编号"), x.get("证券代码"), x.get("操作"),
            x.get("委托价格"), x.get("委托数量")) for x in before]), flush=True)

    # ---------- 4. 发信号（聚宽 → 中转）----------
    oid = "FLOW-%s-%s" % (time.strftime("%m%d%H%M%S"), uuid.uuid4().hex[:4])
    body = {"order_id": oid, "security": "510300.XSHG", "side": "buy",
            "amount": 100, "price": price, "jq_status": "held"}
    r = S.post(BASE + "/api/signal", json=body,
               headers={"X-API-Key": SIGNAL_KEY}, timeout=15)
    check("信号上报被受理", r.status_code == 200 and r.json().get("ok"),
          "%s %s" % (r.status_code, r.text[:120]))
    print("     order_id=%s" % oid, flush=True)

    # ---------- 5. 等执行端下单并出现在当日委托 ----------
    target = None
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(8)      # 查询频率放低：同花顺高频操作会弹验证码
        cur = entrusts()
        new = [x for x in cur if str(x.get("合同编号")) not in before_no
               and "510300" in str(x.get("证券代码", ""))]
        if new:
            target = new[0]
            break
    if not check("委托出现在同花顺当日委托", target is not None,
                 "90 秒内未查到 510300 新委托"):
        return 1

    print("     新委托: 合同编号=%s 代码=%s 操作=%s 价格=%s 数量=%s 状态=%s"
          % (target.get("合同编号"), target.get("证券代码"), target.get("操作"),
             target.get("委托价格"), target.get("委托数量"), target.get("委托状态")), flush=True)

    # ---------- 6. 核心断言：三个字段各自正确 ----------
    check("证券代码正确(510300)", "510300" in str(target.get("证券代码", "")),
          target.get("证券代码"))
    check("委托方向正确(买入)", "买" in str(target.get("操作", "")), target.get("操作"))
    try:
        got_price = float(target.get("委托价格") or 0)
    except (TypeError, ValueError):
        got_price = -1
    check("委托价格正确(%s，未被写成代码)" % price,
          abs(got_price - price) < 1e-6, "实际=%s" % target.get("委托价格"))
    try:
        got_amt = int(float(target.get("委托数量") or 0))
    except (TypeError, ValueError):
        got_amt = -1
    check("委托数量正确(100，未被写成代码)", got_amt == 100, "实际=%s" % target.get("委托数量"))

    # ---------- 7. 撤单 ----------
    eno = str(target.get("合同编号") or "")
    others = [x for x in entrusts() if str(x.get("合同编号")) != eno]
    if others:
        print("     账户还有 %d 笔其它委托，走[一键全撤]" % len(others), flush=True)
        r = S.post(BASE + "/console/api/cancel_all", json={}, timeout=90)
        check("一键全撤执行", r.status_code == 200 and r.json().get("ok"),
              "%s %s" % (r.status_code, r.text[:160]))
        time.sleep(3)
        left = entrusts()
        # 注意：同花顺当日委托会保留已撤记录（备注"全部撤单"、撤消数量>0），
        # 所以不能按"列表为空"判断，要看还有没有"未撤且未成交"的活委托
        active = [x for x in left
                  if float(x.get("撤消数量") or 0) == 0
                  and float(x.get("成交数量") or 0) == 0
                  and "撤" not in str(x.get("备注") or "")]
        check("全撤后无剩余活委托", len(active) == 0,
              [(x.get("合同编号"), x.get("备注")) for x in active])
    else:
        r = S.post(BASE + "/console/api/cancel", json={"entrust_no": eno}, timeout=90)
        check("撤单执行", r.status_code == 200 and r.json().get("ok"),
              "%s %s" % (r.status_code, r.text[:160]))
        time.sleep(3)
        left = [x for x in entrusts() if str(x.get("合同编号")) == eno]
        check("撤单后该委托已消失或已撤",
              not left or "撤" in str(left[0].get("委托状态", "")), left)

    print("\n===== 全流程测试: %d 过 / %d 挂 =====" % (PASS, FAIL), flush=True)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
