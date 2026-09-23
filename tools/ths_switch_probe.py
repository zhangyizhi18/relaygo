# -*- coding: utf-8 -*-
"""单测 _switch_left_menus 切到买入页：抓异常、看切页结果。"""
import os
import sys
import time

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)

import config as cfg
from broker_ths import ThsBroker

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()
b._ensure_fg()
main = b.user._main


def state(tag):
    vis = [(str(w.rectangle()), w.window_text()[:12])
           for w in main.descendants(control_id=1032, class_name="Edit") if w.is_visible()]
    print("%s 可见cid1032=%s" % (tag, vis), flush=True)


state("初始")

# 1) easytrader 自带切换
try:
    b.user._switch_left_menus(["买入[F1]"])
    print("_switch_left_menus(买入[F1]) 无异常", flush=True)
except Exception as e:
    import traceback
    print("_switch_left_menus 异常: %r" % str(e)[:150], flush=True)
    traceback.print_exc()
time.sleep(0.5)
state("切换后")

# 2) 直接点树节点
try:
    trees = [t for t in main.descendants(class_name="SysTreeView32") if t.is_visible()]
    print("可见树: %d" % len(trees), flush=True)
    if trees:
        item = trees[0].get_item(["买入[F1]"])
        print("get_item 成功: %r" % item.text(), flush=True)
        item.select()
        time.sleep(0.5)
        state("直点树节点后")
except Exception as e:
    print("直点树节点异常: %r" % str(e)[:150], flush=True)
