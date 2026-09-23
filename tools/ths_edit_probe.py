# -*- coding: utf-8 -*-
"""
输入框写入方式实况探测（只填字段，绝不点下单按钮）。
对 代码1032/价格1033/数量1034 三个编辑框逐一测试三种写入方式并回读：
  1 set_edit_text (WM_SETTEXT)   2 键盘清空+重打   3 select+type_keys
"""
import os
import sys
import time

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)

import config as cfg
from broker_ths import ThsBroker
import easytrader.clienttrader as _ct

print("输入补丁是否生效:", hasattr(_ct.ClientTrader, "_type_edit_control_keys") and
      getattr(_ct.ClientTrader._type_edit_control_keys, "__name__", "") == "_patched", flush=True)

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()
b._ensure_fg()
main = b.user._main


def read(editor):
    try:
        t = editor.window_text()
        if not t:
            ts = editor.texts()
            t = ts[0] if ts else ""
        return str(t).strip()
    except Exception as e:
        return "<err %s>" % e


def probe(cid, label, text):
    editor = main.child_window(control_id=cid, class_name="Edit")
    print("\n== %s (control_id=%s) 目标=%r ==" % (label, cid, text), flush=True)
    print("  初始内容回读: %r" % read(editor), flush=True)

    # 方式1: WM_SETTEXT
    try:
        editor.set_edit_text(text)
        time.sleep(0.3)
        print("  1 set_edit_text  -> 回读 %r" % read(editor), flush=True)
    except Exception as e:
        print("  1 set_edit_text  -> 异常 %s" % e, flush=True)

    # 方式2: 键盘清空+重打
    try:
        editor.set_focus()
        editor.type_keys("{End}{BS 20}")
        editor.type_keys(text)
        time.sleep(0.3)
        print("  2 键盘清空+重打  -> 回读 %r" % read(editor), flush=True)
    except Exception as e:
        print("  2 键盘清空+重打  -> 异常 %s" % e, flush=True)

    # 方式3: select+type_keys（easytrader 原生）
    try:
        editor.set_focus()
        editor.select()
        editor.type_keys(text)
        time.sleep(0.3)
        print("  3 select+type    -> 回读 %r" % read(editor), flush=True)
    except Exception as e:
        print("  3 select+type    -> 异常 %s" % e, flush=True)


try:
    probe(1032, "证券代码框", "510300")
    time.sleep(1)     # 等 THS 自动填最新价
    probe(1033, "价格框(已被THS自动填最新价)", "4.533")
    probe(1034, "数量框", "100")
finally:
    # 清空价格/数量，避免残留误提交
    for cid in (1033, 1034):
        try:
            main.child_window(control_id=cid, class_name="Edit").set_edit_text("")
        except Exception:
            pass
print("\nDONE（未点下单按钮）", flush=True)
