# -*- coding: utf-8 -*-
"""输入方式对比探针：WM_SETTEXT vs 键盘输入，看 THS 是否登记表单（证券名称/可买股数变化）。"""
import os
import sys
import time

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)

import config as cfg
from broker_ths import ThsBroker

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()
main = b.user._main

try:
    b.user._switch_left_menus(["买入[F1]"])
except Exception:
    pass
time.sleep(1.2)


def vals(tag):
    """打印买入页所有可见 Static 的文本（含数值，如证券名称/可买股数）。"""
    out = []
    for w in main.descendants(class_name="Static"):
        try:
            if w.is_visible():
                t = (w.window_text() or "").strip()
                if t:
                    out.append(t)
        except Exception:
            pass
    print("[%s] Static: %s" % (tag, " | ".join(out)), flush=True)


def find(cid):
    return [e for e in main.descendants(class_name="Edit")
            if getattr(e.element_info, "control_id", None) == cid and e.is_visible()]


vals("初始")

print("\n>>> 方式A: WM_SETTEXT 写代码 510300", flush=True)
find(1032)[0].set_edit_text("510300")
time.sleep(1.5)
vals("WM_SETTEXT 之后")

print("\n>>> 清空 + 键盘输入 代码 510300", flush=True)
e = find(1032)[0]
e.set_focus()
time.sleep(0.2)
try:
    e.type_keys("{BACKSPACE 15}")     # 清空
except Exception as ex:
    print("   退格异常 %r" % str(ex)[:60], flush=True)
time.sleep(0.3)
e.type_keys("510300", with_spaces=False)
time.sleep(1.5)
vals("键盘输入代码后")

print("\n>>> 键盘输入价格 4.533", flush=True)
p = find(1033)[0]
p.set_focus()
time.sleep(0.2)
p.type_keys("{BACKSPACE 15}")
time.sleep(0.3)
p.type_keys("4.533")
time.sleep(1.2)

print("\n>>> 键盘输入数量 100", flush=True)
a = find(1034)[0]
a.set_focus()
time.sleep(0.2)
a.type_keys("{BACKSPACE 15}")
time.sleep(0.3)
a.type_keys("100")
time.sleep(1.2)
vals("价格/数量 键盘输入后")

print("\n[完成] 未点击买入。请人工看一眼同花顺界面：证券代码/价格/数量 三框内容是否正确、证券名称是否出现。", flush=True)
