# -*- coding: utf-8 -*-
"""落位探针：模拟 easytrader _set_trade_params 的三次写入（代码/价格/数量），
每步后 dump 所有可见 Edit 的 control_id + 文本 + 位置，看值落进了哪个框。
**绝不点击买入按钮**，安全。
"""
import os
import sys
import time

EXEC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "executor")
sys.path.insert(0, EXEC_DIR)
os.environ["JQ_EDIT_DEBUG"] = "1"     # 打开补丁的逐招法诊断

import config as cfg
from broker_ths import ThsBroker

CODE, PRICE, AMOUNT = "510300", "4.533", "100"

b = ThsBroker(cfg.THS_XIADAN_PATH)
b.connect()

app = b.user._app
main = b.user._main
print("[主窗口] %r rect=%s" % (main.window_text(), main.rectangle()), flush=True)

print("\n[全部顶层窗口]", flush=True)
try:
    import win32process
    for w in app.windows():
        try:
            if not w.is_visible():
                continue
            pid = win32process.GetWindowThreadProcessId(w.handle)[1]
            print("  %r vis=%s pid=%s rect=%s" % (w.window_text()[:30], w.is_visible(), pid,
                                                  w.rectangle()), flush=True)
        except Exception as e:
            print("  <异常 %r>" % str(e)[:50], flush=True)
except Exception as e:
    print("  枚举失败 %s" % str(e)[:80], flush=True)


def dump(tag):
    print("--- %s ---" % tag, flush=True)
    try:
        eds = main.descendants(class_name="Edit")
    except Exception as e:
        print("  枚举失败", e, flush=True)
        return
    n = 0
    for e in eds:
        try:
            if not e.is_visible():
                continue
            cid = e.element_info.control_id
            txt = e.window_text()
            print("  cid=%s txt=%r rect=%s" % (cid, txt, e.rectangle()), flush=True)
            n += 1
        except Exception:
            pass
    print("  (可见 Edit 共 %d 个)" % n, flush=True)


# 切到买入页
try:
    b.user._switch_left_menus(["买入[F1]"])
except Exception as e:
    print("切页异常 %s" % str(e)[:80], flush=True)
time.sleep(1.2)
dump("切到买入页·写入前")

# === 模拟 _set_trade_params 的三次写入（不点提交）===
print("\n>>> 写入 代码=%s" % CODE, flush=True)
b.user._type_edit_control_keys(1032, CODE)
time.sleep(0.6)
dump("写代码后")

print("\n>>> 写入 价格=%s" % PRICE, flush=True)
b.user._type_edit_control_keys(1033, PRICE)
time.sleep(0.6)
dump("写价格后")

print("\n>>> 写入 数量=%s" % AMOUNT, flush=True)
b.user._type_edit_control_keys(1034, AMOUNT)
time.sleep(0.6)
dump("写数量后")

print("\n[完成] 未点击买入，THS 界面已填好但没有提交。", flush=True)
