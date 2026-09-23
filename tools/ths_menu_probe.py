# -*- coding: utf-8 -*-
"""探测左侧菜单树条目 + F1 切页效果。"""
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


def visible_1032():
    return [(str(w.rectangle()), w.window_text())
            for w in main.descendants(control_id=1032, class_name="Edit") if w.is_visible()]


print("当前可见 cid1032:", visible_1032(), flush=True)

# 左侧菜单树
trees = main.descendants(class_name="SysTreeView32")
print("SysTreeView32 数量:", len(trees), flush=True)
for t in trees:
    try:
        vis = t.is_visible()
        print("  tree visible=%s" % vis, flush=True)
        if vis:
            roots = t.roots() if hasattr(t, "roots") else []
            print("  roots:", [r.text() for r in roots][:12], flush=True)
            for r in roots[:12]:
                try:
                    print("   [%s] children=%s" % (r.text(), [c.text() for c in r.children()][:6]), flush=True)
                except Exception:
                    pass
    except Exception as e:
        print("  tree 异常:", str(e)[:80], flush=True)

# 用 easytrader 自己的方式拿菜单树
try:
    h = b.user._get_left_menus_handle()
    print("\neasytrader 菜单句柄:", h.window_text(), flush=True)
    names = h.item_names()
    print("菜单项:", names[:15], flush=True)
except Exception as e:
    print("easytrader 菜单句柄失败:", str(e)[:120], flush=True)

# F1 切页测试
try:
    b.user._switch_left_menus_by_shortcut("{F1}")
except Exception as e:
    print("F1 切页异常:", str(e)[:80], flush=True)
time.sleep(1)
print("\nF1 后可见 cid1032:", visible_1032(), flush=True)
