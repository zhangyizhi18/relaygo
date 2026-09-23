# -*- coding: utf-8 -*-
"""
隔离下单探针：不经中转/执行端，直接驱动同花顺下一笔限价单，验证
  1) 修复后 证券代码/价格/数量 是否各自填对（本次 bug 的回归验证）
  2) 下单是否真提交（以当日委托为准）
  3) 提交后撤单是否成功

价格取 现价-1%：不会成交、在涨跌停内、在风控 3% 偏差内。
运行: venv/Scripts/python.exe tools/ths_single_order_probe.py [代码] [价格] [数量]
"""
import os
import sys
import time

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)
os.environ["JQ_EDIT_DEBUG"] = "1"

import config as cfg
from broker_ths import ThsBroker
import market_price

args = sys.argv[1:]
code6 = args[0] if args else "510300"
jq_code = code6 + (".XSHG" if code6[0] in "56789" or code6.startswith("11") else ".XSHE")

latest = market_price.fetch_latest(code6)
if args[1:]:
    price = float(args[1])
else:
    price = round(latest * 0.99, 3) if latest else None
amount = int(args[2]) if args[2:] else 100

print("[测试参数] %s 最新价=%s 委托价=%s 数量=%s" % (jq_code, latest, price, amount), flush=True)

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()

print("\n[1] 下单（走与执行端完全相同的 _trade 路径）...", flush=True)
t0 = time.time()
r = b.buy(jq_code, price, amount)
print("[下单结果] %.1fs %s" % (time.time() - t0, r), flush=True)

print("\n[2] 弹窗记录（本进程内）...", flush=True)
try:
    pop = getattr(b.user, "_pop_log", None)
    if pop is None:
        import easytrader.clienttrader as ct
        pop = getattr(ct.ClientTrader, "_pop_log", [])
    print("    共 %d 条: %s" % (len(pop or []), pop), flush=True)
except Exception as e:
    print("    读取失败 %s" % str(e)[:80], flush=True)

print("\n[3] 查当日委托（重试避开剪贴板缓存）...", flush=True)
rows, mine = [], []
for attempt in range(5):
    rows = b.query_entrusts() or []
    mine = [x for x in rows if code6 in str(x.get("证券代码", ""))]
    if mine:
        break
    time.sleep(4)
for row in rows:
    print("    %s | %s | %s | x%s | @%s | %s" % (
        row.get("合同编号"), row.get("证券代码"), row.get("操作"),
        row.get("委托数量"), row.get("委托价格"), row.get("委托状态")), flush=True)

eno = (r or {}).get("entrust_no") or ""
mine = [x for x in (rows or []) if code6 in str(x.get("证券代码", ""))]
print("\n[核对] 本次委托号=%s；委托表里本代码 %d 笔" % (eno, len(mine)), flush=True)
for row in mine:
    try:
        ok_p = abs(float(row.get("委托价格") or 0) - float(price)) < 1e-6
    except (TypeError, ValueError):
        ok_p = False
    try:
        ok_a = int(float(row.get("委托数量") or 0)) == int(amount)
    except (TypeError, ValueError):
        ok_a = False
    print("    %s 价格%s 数量%s  -> 价格正确=%s 数量正确=%s" % (
        row.get("合同编号"), row.get("委托价格"), row.get("委托数量"), ok_p, ok_a), flush=True)

if not eno and not mine:
    print("\n[中止] 未提交成功，不执行撤单。", flush=True)
    sys.exit(1)

print("\n[4] 撤单（一键全撤，清掉本账户全部可撤委托）...", flush=True)
rc = b.cancel_all_orders()
print("[撤单结果] %s" % rc, flush=True)
time.sleep(2)
rows2 = b.query_entrusts()
print("[撤后委托] %d 笔: %s" % (len(rows2 or []),
      [(x.get("合同编号"), x.get("委托状态")) for x in (rows2 or [])]), flush=True)
