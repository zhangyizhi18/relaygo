# -*- coding: utf-8 -*-
"""最终验证：模拟同花顺缩在托盘（隐藏窗口）时，修复后的 connect 能否自动恢复并连上。"""
import sys
import time
import traceback

sys.path.insert(0, "executor")
import pywinauto_compat  # noqa: F401
import win32gui
import win32con
import broker_ths
import config
from foreground import find_window_by_title, THS_TITLE_KEYWORD

h = find_window_by_title(THS_TITLE_KEYWORD)
print("找到窗口 hwnd=%s visible=%s" % (h, win32gui.IsWindowVisible(h)), flush=True)
if h:
    win32gui.ShowWindow(h, win32con.SW_HIDE)   # 模拟缩到托盘
    time.sleep(0.5)
    print("已模拟托盘visible=%s iconic=%s" % (win32gui.IsWindowVisible(h), win32gui.IsIconic(h)), flush=True)

b = broker_ths.ThsBroker(config.THS_XIADAN_PATH)
try:
    b.connect()                                  # 修复后：内部应先恢复窗口再 connect
    print("connect: OK", flush=True)
    print("health_check:", b.health_check(), flush=True)
    if h:
        print("连接后窗口 visible=%s" % win32gui.IsWindowVisible(h), flush=True)
except Exception:
    print("EXCEPTION:", flush=True)
    traceback.print_exc()
