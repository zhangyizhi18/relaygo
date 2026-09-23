# -*- coding: utf-8 -*-
"""诊断 _find_editor 路径：模拟 buy 流程中对代码框的写入，逐步打印。"""
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

print("== 1) descendants 视角 ==", flush=True)
found = main.descendants(control_id=1032, class_name="Edit")
print("实例数:", len(found), flush=True)
vis = [w for w in found if w.is_visible()]
print("可见数:", len(vis), flush=True)
for w in vis:
    print("  rect=%s enabled=%s" % (w.rectangle(), w.is_enabled()), flush=True)
target = vis[0]
print("set_edit_text('510300')...", flush=True)
target.set_edit_text("510300")
for delay in (0.05, 0.3, 1.0):
    time.sleep(delay)
    try:
        t = target.window_text()
        print("  +%.2fs 回读: %r" % (delay, t), flush=True)
    except Exception as e:
        print("  回读异常 %r" % str(e)[:60], flush=True)

print("\n== 2) child_window 视角 ==", flush=True)
try:
    editor = main.child_window(control_id=1032, class_name="Edit")
    print("  解析成功:", editor.rectangle(), flush=True)
except Exception as e:
    print("  解析失败:", str(e)[:100], flush=True)

print("\n== 3) 再看一遍全部可见 1032 的内容 ==", flush=True)
time.sleep(0.5)
for w in main.descendants(control_id=1032, class_name="Edit"):
    if w.is_visible():
        print("  rect=%s text=%r" % (w.rectangle(), w.window_text()), flush=True)
