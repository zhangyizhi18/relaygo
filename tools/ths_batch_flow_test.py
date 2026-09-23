# -*- coding: utf-8 -*-
"""
RelayGo 批量全流程下单测试（多轮 × 多单，真实提交到同花顺）

链路：模拟聚宽上报信号 -> 中转服务 -> 执行端 -> 同花顺 -> 查当日委托核对 -> 撤单

安全设计：
  * 买入价 = 现价 × 0.99（低于现价 1%，挂单不会成交，也不触发 3% 价格偏差风控）
  * 默认每轮结束一键全撤，不在账上留下隔夜挂单
  * 核对用"下单前合同编号基线"取差集，避免历史委托干扰

进度探测用 /api/status 的计数（accepted+failed），不靠频繁查委托 ——
同花顺高频查询会弹验证码，每轮只在核对阶段查 1~2 次。

用法：
  venv/Scripts/python.exe tools/ths_batch_flow_test.py --rounds 3 --per-round 10
  venv/Scripts/python.exe tools/ths_batch_flow_test.py --rounds 3 --per-round 10 --keep  # 不自动撤单
"""
import argparse
import os
import sys
import time
import uuid

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "executor"))

BASE = "http://127.0.0.1:5010"
SIGNAL_KEY = "jq-signal-key-2026-change-me"
ADMIN, ADMIN_PWD = "admin", "admin123"

# 10 只常见 ETF（价格低、成交活跃；买单挂低价不会成交）
POOL = [
    ("510300", "XSHG", "沪深300ETF"),
    ("510500", "XSHG", "中证500ETF"),
    ("510050", "XSHG", "上证50ETF"),
    ("588000", "XSHG", "科创50ETF"),
    ("512880", "XSHG", "证券ETF"),
    ("512480", "XSHG", "半导体ETF"),
    ("159915", "XSHE", "创业板ETF"),
    ("159949", "XSHE", "创业板50ETF"),
    ("159928", "XSHE", "消费ETF"),
    ("512690", "XSHG", "酒ETF"),
]

S = requests.Session()
S.trust_env = False


def login():
    r = S.post(BASE + "/console/api/login",
               json={"username": ADMIN, "password": ADMIN_PWD}, timeout=15)
    return r.status_code == 200 and r.json().get("ok"), r.text[:150]


def status():
    r = S.get(BASE + "/api/status", timeout=10)
    return r.json()


def entrusts():
    r = S.get(BASE + "/console/api/account", params={"kind": "entrusts"}, timeout=90)
    j = r.json()
    if not j.get("ok"):
        return None, j.get("error")
    return j.get("data") or [], None


def cancel_all():
    r = S.post(BASE + "/console/api/cancel_all", json={}, timeout=120)
    return r.status_code == 200 and r.json().get("ok"), r.text[:200]


def latest_price(code):
    import market_price
    return market_price.fetch_latest(code)


def _num(v, default=-1):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3, help="轮数")
    ap.add_argument("--per-round", type=int, default=10, help="每轮单数")
    ap.add_argument("--amount", type=int, default=100, help="每笔股数")
    ap.add_argument("--side", default="buy", choices=["buy", "sell"], help="下单方向")
    ap.add_argument("--keep", action="store_true", help="每轮结束不自动撤单")
    ap.add_argument("--wait", type=int, default=300, help="每轮等待执行的最长秒数")
    a = ap.parse_args()

    ok, msg = login()
    print("控制台登录: %s %s" % (ok, msg), flush=True)
    if not ok:
        return 2

    st = status()
    print("执行端心跳: %s" % (st.get("last_heartbeat") or {}).get("detail"), flush=True)
    if not st.get("last_heartbeat"):
        print("!! 执行端不在线，中止")
        return 2

    total_ok = total_fail = 0
    all_fail_detail = []

    for rnd in range(1, a.rounds + 1):
        print("\n" + "=" * 70, flush=True)
        print("第 %d/%d 轮：准备发 %d 笔 %s" % (rnd, a.rounds, a.per_round, a.side), flush=True)
        print("=" * 70, flush=True)

        # ---- 基线委托 ----
        before, err = entrusts()
        if before is None:
            print("  查委托失败: %s，跳过本轮" % err, flush=True)
            total_fail += a.per_round
            continue
        before_no = {str(x.get("合同编号")) for x in before}
        print("  下单前当日委托 %d 笔" % len(before), flush=True)

        # ---- 取价 + 发信号 ----
        # 计数基线必须"本轮开跑前"重新取：/api/status 的 accepted/failed 是从中转
        # 启动起累计的，若沿用开场快照，第 2 轮起 done 会立刻 ≥ 单数而"秒退"，
        # 导致还没等执行端下单就去查委托（查询任务被信号批次挡住 → 等待超时）。
        st_round = status()
        base_acc = st_round["accepted"] + st_round["failed"]
        plans = []
        for code, mkt, name in POOL[:a.per_round]:
            latest = latest_price(code)
            if not latest or latest <= 0:
                print("  !! %s %s 取不到现价，跳过" % (code, name), flush=True)
                total_fail += 1
                all_fail_detail.append((rnd, code, "取不到现价"))
                continue
            if a.side == "buy":
                price = int(latest * 0.99 * 1000) / 1000.0
            else:
                price = int(latest * 1.01 * 1000) / 1000.0
            oid = "BATCH-R%d-%s-%s" % (rnd, code, uuid.uuid4().hex[:4])
            body = {"order_id": oid, "security": "%s.%s" % (code, mkt), "side": a.side,
                    "amount": a.amount, "price": price, "jq_status": "held"}
            try:
                r = S.post(BASE + "/api/signal", json=body,
                           headers={"X-API-Key": SIGNAL_KEY}, timeout=15)
                accepted = r.status_code == 200 and r.json().get("ok")
            except Exception as e:
                accepted, r = False, None
                print("    信号上报异常 %s: %s" % (code, e), flush=True)
            plans.append((code, name, price, oid, accepted))
            print("    -> %s %-10s @%-8s %s" % (code, name, price,
                                                 "已受理" if accepted else "上报失败"), flush=True)

        # ---- 等执行端处理完（看 /api/status 计数增量，不碰同花顺 UI）----
        deadline = time.time() + a.wait
        done = 0
        while time.time() < deadline:
            time.sleep(4)
            cur = status()
            done = (cur["accepted"] + cur["failed"]) - base_acc
            if done >= len(plans):
                break
        cur = status()
        print("  执行端处理完成 %d/%d 笔（accepted 累计 %s / failed 累计 %s）"
              % (done, len(plans), cur["accepted"], cur["failed"]), flush=True)
        if done < len(plans):
            print("  !! 等待超时，本轮 %d 笔未全部处理完" % (len(plans) - done), flush=True)

        # ---- 核对委托 ----
        time.sleep(2)
        after, err = entrusts()
        if after is None:
            print("  查委托失败: %s" % err, flush=True)
            total_fail += len(plans)
            continue
        new = [x for x in after if str(x.get("合同编号")) not in before_no]
        print("  本轮新增委托 %d 笔:" % len(new), flush=True)
        for x in new:
            print("    #%s %s %s %s x%s 状态=%s 备注=%s"
                  % (x.get("合同编号"), x.get("证券代码"), x.get("操作"),
                     x.get("委托价格"), x.get("委托数量"),
                     x.get("委托状态") or x.get("备注"), x.get("备注")), flush=True)

        by_code = {}
        for x in new:
            by_code.setdefault(str(x.get("证券代码", ""))[:6], x)

        rnd_ok = 0
        for code, name, price, oid, accepted in plans:
            x = by_code.get(code)
            if not x:
                print("    FAIL %s %s 未出现在当日委托" % (code, name), flush=True)
                all_fail_detail.append((rnd, code, "未出现在当日委托"))
                continue
            p = _num(x.get("委托价格"))
            amt = _num(x.get("委托数量"))
            side_ok = ("买" in str(x.get("操作") or "")) == (a.side == "buy")
            if abs(p - price) < 1e-6 and int(amt) == a.amount and side_ok:
                rnd_ok += 1
            else:
                d = "字段不符: 价%s(期望%s) 量%s(期望%s) 方向%s" % (
                    x.get("委托价格"), price, x.get("委托数量"), a.amount, x.get("操作"))
                print("    FAIL %s %s %s" % (code, name, d), flush=True)
                all_fail_detail.append((rnd, code, d))
        print("  第 %d 轮核对通过 %d/%d" % (rnd, rnd_ok, len(plans)), flush=True)
        total_ok += rnd_ok
        total_fail += len(plans) - rnd_ok

        # ---- 撤单 ----
        if not a.keep:
            ok, m = cancel_all()
            print("  一键全撤: %s %s" % (ok, m), flush=True)
            time.sleep(3)
            left, err = entrusts()
            if left is not None:
                active = [x for x in left
                          if _num(x.get("撤消数量"), 0) == 0
                          and _num(x.get("成交数量"), 0) == 0]
                print("  全撤后剩余活委托: %d 笔" % len(active), flush=True)
                for x in active[:12]:
                    print("    ! #%s %s %s x%s %s" % (x.get("合同编号"), x.get("证券代码"),
                                                      x.get("委托价格"), x.get("委托数量"),
                                                      x.get("委托状态")), flush=True)
            if rnd < a.rounds:
                print("  等待 5 秒进入下一轮...", flush=True)
                time.sleep(5)

    print("\n" + "=" * 70, flush=True)
    print("总结果：通过 %d 笔 / 失败 %d 笔（共 %d 笔）"
          % (total_ok, total_fail, total_ok + total_fail), flush=True)
    if all_fail_detail:
        print("失败明细：", flush=True)
        for rnd, code, d in all_fail_detail:
            print("  第%d轮 %s : %s" % (rnd, code, d), flush=True)
    print("=" * 70, flush=True)
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
