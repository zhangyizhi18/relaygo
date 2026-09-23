# -*- coding: utf-8 -*-
"""探测 xiadan 当前页面状态：主窗口、左侧树、以及是否存在下单 Edit 控件。"""
import os
import sys

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)

import config as cfg
from broker_ths import ThsBroker

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()
b._ensure_fg()
main = b.user._main
print("主窗口:", main.window_text(), main.class_name(), flush=True)

# 枚举全部 Edit 控件
edits = main.descendants(class_name="Edit")
print("Edit 控件数量:", len(edits), flush=True)
for w in edits[:10]:
    try:
        print("  cid=%s text=%r" % (w.control_id(), w.window_text()), flush=True)
    except Exception as e:
        print("  <err %s>" % e, flush=True)

# 看左侧树的可见项（找 买入/卖出 页）
try:
    tree = main.child_window(class_name="SysTreeView32")
    items = [t for t in tree.item_names() if t]
    print("\n左侧菜单项:", items[:15], flush=True)
except Exception as e:
    print("左侧树读取失败:", e, flush=True)

# 尝试枚举所有顶层对话框标题
try:
    tops = [w.window_text() for w in b.user._app.windows()]
    print("\n进程顶层窗口:", tops[:10], flush=True)
except Exception as e:
    print("枚举顶层窗口失败:", e, flush=True)
