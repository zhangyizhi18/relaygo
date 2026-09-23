# -*- coding: utf-8 -*-
"""探测同 control_id 的多个 Edit 实例：数量/可见性/写入回读。只填字段不提交。"""
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


def read(w):
    try:
        t = w.window_text()
        if not t:
            ts = w.texts()
            t = ts[0] if ts else ""
        return str(t).strip()
    except Exception as e:
        return "<err %r>" % str(e)[:60]


for cid, label, text in ((1032, "代码", "510300"), (1033, "价格", "4.160"), (1034, "数量", "100")):
    found = main.descendants(control_id=cid, class_name="Edit")
    print("\n== %s cid=%s: %d 个实例" % (label, cid, len(found)), flush=True)
    vis = [w for w in found if w.is_visible()]
    print("   可见: %d 个" % len(vis), flush=True)
    target = vis[0] if vis else (found[0] if found else None)
    if target is None:
        continue
    print("   选中实例 rect=%s" % target.rectangle(), flush=True)
    try:
        target.set_edit_text(text)
        time.sleep(0.3)
        print("   set_edit_text(%r) -> 回读 %r" % (text, read(target)), flush=True)
    except Exception as e:
        print("   set_edit_text 异常: %r" % str(e)[:80], flush=True)
    try:
        target.set_focus()
        target.type_keys("{End}{BS 20}%s" % text)
        time.sleep(0.3)
        print("   键盘清空+重打(%r) -> 回读 %r" % (text, read(target)), flush=True)
    except Exception as e:
        print("   键盘输入异常: %r" % str(e)[:80], flush=True)

# 恢复：清空价格数量
for cid in (1033, 1034):
    for w in main.descendants(control_id=cid, class_name="Edit"):
        if w.is_visible():
            try:
                w.set_edit_text("")
            except Exception:
                pass
print("\nDONE", flush=True)
