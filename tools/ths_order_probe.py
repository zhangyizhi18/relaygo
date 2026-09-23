# -*- coding: utf-8 -*-
"""单发一单诊断：限价买 510300，打印每个弹窗的标题和按钮，定位提交被拦的原因。
用法: python tools/ths_order_probe.py [--no-patch] [--price 4.53]
"""
import os
import sys
import time

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)

if "--no-patch" in sys.argv:
    os.environ["JQ_NO_TYPING_PATCH"] = "1"

import config as cfg
from broker_ths import ThsBroker
import market_price
import pywinauto_compat

q = market_price.fetch_latest("510300")
price = round(q * 0.99, 3)
if "--price" in sys.argv:
    price = float(sys.argv[sys.argv.index("--price") + 1])
print("现价=%s 下单价=%s 补丁=%s" % (q, price,
      "开" if not os.environ.get("JQ_NO_TYPING_PATCH") else "关"), flush=True)

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()
t0 = time.time()
r = b.buy("510300.XSHG", price, 100)
print("耗时 %.1fs 结果: %s" % (time.time() - t0, r), flush=True)

import easytrader.clienttrader as _ct
print("\n弹窗日志:", flush=True)
for item in getattr(_ct.ClientTrader, "_pop_log", []):
    print(" ", item, flush=True)

time.sleep(2)
print("\n当日委托核对:", flush=True)
try:
    for row in (b.query_entrusts() or [])[:6]:
        print("  %s %s %s x%s @%s [%s %s]" % (row.get("合同编号"), row.get("证券代码"),
                                              row.get("操作"), row.get("委托数量"),
                                              row.get("委托价格"), row.get("委托状态"),
                                              row.get("备注")), flush=True)
except Exception as e:
    print("查询失败:", e, flush=True)
