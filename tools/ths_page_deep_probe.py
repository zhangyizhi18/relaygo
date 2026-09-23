# -*- coding: utf-8 -*-
"""深度探针：主窗口下全部 Edit 的 cid/可见性 + 可见控件的文本/类别，判断当前停在哪个页面。"""
import os
import sys

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)

import config as cfg
from broker_ths import ThsBroker

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()

main = b.user._main
print("主窗口:", main.window_text(), main.rectangle(), "class=", main.class_name(), flush=True)

# 0) 先切到买入页（与 _trade 相同路径），再枚举
try:
    b.user._switch_left_menus(["买入[F1]"])
    print("已切到买入页", flush=True)
except Exception as e:
    print("切页失败: %s" % str(e)[:100], flush=True)
    try:
        b.user._switch_left_menus_by_shortcut("{F1}")
        print("已用 F1 快捷键切页", flush=True)
    except Exception as e2:
        print("F1 也失败: %s" % str(e2)[:100], flush=True)
import time
time.sleep(1.0)

# 1) 全部 Edit（含不可见）
edits = main.descendants(class_name="Edit")
print("\nEdit 总数:", len(edits), flush=True)
from collections import Counter
cid_count = Counter()
for e in edits:
    try:
        cid = e.element_info.control_id
        vis = e.is_visible()
        txt = ""
        try:
            txt = (e.window_text() or "")[:20]
        except Exception:
            pass
        cid_count[cid] += 1
        if vis or cid in (1032, 1033, 1034):
            print("  cid=%s vis=%s txt=%r rect=%s" % (cid, vis, txt, e.rectangle()), flush=True)
    except Exception as ex:
        print("  <异常 %s>" % str(ex)[:50], flush=True)
print("cid 分布(前10):", cid_count.most_common(10), flush=True)

# 2) 可见控件的文本概览（判断当前页签）
print("\n可见控件文本:", flush=True)
seen = set()
n = 0
for w in main.descendants():
    try:
        if not w.is_visible():
            continue
        t = (w.window_text() or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        cls = w.class_name()
        if cls in ("Button", "ComboBox", "Static", "SysHeader32", "TreeItem", "ListItem", "TabItem"):
            print("  [%s] %r" % (cls, t[:30]), flush=True)
            n += 1
        if n > 60:
            break
    except Exception:
        continue
print("done", flush=True)
