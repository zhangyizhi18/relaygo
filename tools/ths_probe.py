# -*- coding: utf-8 -*-
"""探测 THS 账户当前状态：当日委托/可撤委托/资金，确认测试单是否真实挂上。"""
import os
import sys

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)

import config as cfg
from broker_ths import ThsBroker

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()

bal = b.query_balance()
print("可用资金:", bal.get("可用金额") or bal.get("可用资金"), flush=True)

print("\n-- 当日委托 --", flush=True)
for r in (b.query_entrusts() or []):
    print({k: r.get(k) for k in ("合同编号", "证券代码", "操作", "委托数量",
                                  "成交数量", "委托价格", "委托状态", "备注")}, flush=True)

print("\n-- 可撤委托(撤单页) --", flush=True)
try:
    for r in (b.user.cancel_entrusts or []):
        print({k: r.get(k) for k in ("合同编号", "证券代码", "操作", "委托数量",
                                      "委托价格")}, flush=True)
except Exception as e:
    print("读撤单页失败:", e, flush=True)
