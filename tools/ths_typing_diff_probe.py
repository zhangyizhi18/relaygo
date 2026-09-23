# -*- coding: utf-8 -*-
"""对比探针：找出为什么补丁里的键盘输入没生效（怀疑 _ensure_fg 的 ALT 键残留）。"""
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


def statics(tag):
    out = []
    for w in main.descendants(class_name="Static"):
        try:
            if w.is_visible():
                t = (w.window_text() or "").strip()
                if t:
                    out.append(t)
        except Exception:
            pass
    print("[%s] %s" % (tag, " | ".join(out[:12])), flush=True)


def find(cid):
    return [e for e in main.descendants(class_name="Edit")
            if getattr(e.element_info, "control_id", None) == cid and e.is_visible()]


print("=== 场景1：不做 _ensure_fg，直接手工键盘输入 ===", flush=True)
try:
    b.user._switch_left_menus(["买入[F1]"])
except Exception:
    pass
time.sleep(1.0)
e = find(1032)[0]
e.set_focus()
time.sleep(0.2)
e.type_keys("{BACKSPACE 20}", with_spaces=False)
time.sleep(0.2)
e.type_keys("510300", with_spaces=False)
time.sleep(1.5)
statics("场景1 手工键盘(无ensure_fg)")

print("\n=== 场景2：先 _ensure_fg()，再手工键盘输入 ===", flush=True)
b._ensure_fg()
time.sleep(0.5)
e = find(1032)[0]
e.set_focus()
time.sleep(0.2)
e.type_keys("{BACKSPACE 20}", with_spaces=False)
time.sleep(0.2)
e.type_keys("510300", with_spaces=False)
time.sleep(1.5)
statics("场景2 手工键盘(有ensure_fg)")

print("\n=== 场景3：走补丁函数 _type_edit_control_keys ===", flush=True)
b.user._type_edit_control_keys(1032, "510300")
time.sleep(1.5)
statics("场景3 补丁函数")

print("\n=== 场景4：补丁函数 + 完成后立刻检查可买/名称 ===", flush=True)
b._ensure_fg()
try:
    b.user._switch_left_menus(["买入[F1]"])
except Exception:
    pass
time.sleep(0.8)
b.user._type_edit_control_keys(1032, "510300")
time.sleep(1.2)
statics("场景4 补丁函数(切页+ensure_fg)")
