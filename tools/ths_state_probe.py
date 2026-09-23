# -*- coding: utf-8 -*-
"""同花顺状态探针：看代码是否被识别（证券名称）、有无阻塞弹窗、买入按钮状态。"""
import os
import sys
import time

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)
os.environ["JQ_EDIT_DEBUG"] = "1"

import config as cfg
from broker_ths import ThsBroker

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()
main = b.user._main


def statics(tag):
    print("--- %s 可见 Static/Button 文本 ---" % tag, flush=True)
    seen = []
    for w in main.descendants():
        try:
            if not w.is_visible():
                continue
            cls = w.class_name()
            if cls not in ("Static", "Button"):
                continue
            t = (w.window_text() or "").strip()
            if t:
                seen.append("[%s]%s" % (cls[:3], t[:16]))
        except Exception:
            pass
    print("   " + " | ".join(seen[:40]), flush=True)


def buttons(tag):
    print("--- %s 按钮(含控件号) ---" % tag, flush=True)
    for w in main.descendants(class_name="Button"):
        try:
            if not w.is_visible():
                continue
            t = (w.window_text() or "").strip()
            if t:
                print("   cid=%s %r enabled=%s" % (w.element_info.control_id, t[:16],
                                                   w.is_enabled()), flush=True)
        except Exception:
            pass


try:
    b.user._switch_left_menus(["买入[F1]"])
except Exception as e:
    print("切页异常 %s" % str(e)[:60], flush=True)
time.sleep(1.2)
statics("切到买入页")

print("\n>>> 写代码 510300", flush=True)
b.user._type_edit_control_keys(1032, "510300")
time.sleep(1.5)
statics("写代码后(WM_SETTEXT)")

print("\n>>> 试按 TAB 让 THS 处理代码", flush=True)
try:
    eds = [e for e in main.descendants(class_name="Edit")
           if getattr(e.element_info, "control_id", None) == 1032 and e.is_visible()]
    if eds:
        eds[0].set_focus()
        eds[0].type_keys("{TAB}")
except Exception as e:
    print("   TAB 异常 %s" % str(e)[:60], flush=True)
time.sleep(1.5)
statics("按 TAB 后")

buttons("当前页按钮")

print("\n--- 顶层/弹窗枚举（含不可见）---", flush=True)
try:
    import win32process
    for w in b.user._app.windows():
        try:
            pid = win32process.GetWindowThreadProcessId(w.handle)[1]
            print("   %r cls=%s vis=%s pid=%s rect=%s" % (
                w.window_text()[:30], w.class_name(), w.is_visible(), pid, w.rectangle()), flush=True)
        except Exception:
            pass
except Exception as e:
    print("   枚举失败 %s" % str(e)[:80], flush=True)

print("\n--- #32770 对话框内容 ---", flush=True)
try:
    for d in b.user._app.windows(class_name="#32770"):
        try:
            texts = []
            for c in d.descendants():
                t = (c.window_text() or "").strip()
                if t:
                    texts.append(t[:24])
            print("   标题=%r 可见=%s 文本=%s" % (d.window_text()[:30], d.is_visible(), texts[:12]), flush=True)
        except Exception as e:
            print("   <异常 %r>" % str(e)[:50], flush=True)
except Exception as e:
    print("   枚举失败 %s" % str(e)[:80], flush=True)
