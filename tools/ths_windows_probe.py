# -*- coding: utf-8 -*-
"""枚举交易程序全部顶层窗口：标题/矩形/可见性/下单控件数量，找出真正的下单窗口。"""
import os
import sys

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)

import config as cfg
from broker_ths import ThsBroker

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()

app = b.user._app
wins = app.windows(class_name_re="Afx.*|#32770")
print("顶层窗口数:", len(wins), flush=True)
for w in wins:
    try:
        if not w.is_visible():
            continue
        edits = [e for e in w.descendants(control_id=1032, class_name="Edit") if e.is_visible()]
        rects = [str(e.rectangle()) for e in edits]
        print("%r rect=%s cid1032可见=%d %s" % (w.window_text(), w.rectangle(),
                                                len(edits), rects[:3]), flush=True)
    except Exception as e:
        print("<枚举异常 %r>" % str(e)[:60], flush=True)

# 对比: easytrader 认定的主窗口
main = b.user._main
print("\neasytrader._main =", main.window_text(), main.rectangle(), flush=True)
