# -*- coding: utf-8 -*-
"""枚举同花顺交易窗口下的按钮控件（control_id / 文本），定位"重填"真实控件号。
只读，用 win32gui，不触发任何输入。"""
import sys
import ctypes

sys.path.insert(0, "executor")
import win32gui
from foreground import find_window_by_title, THS_TITLE_KEYWORD

user32 = ctypes.windll.user32
main = find_window_by_title(THS_TITLE_KEYWORD)
print("主窗口 hwnd =", main, "title =", win32gui.GetWindowText(main) if main else None)

rows = []


def cb(h, _):
    cls = win32gui.GetClassName(h)
    txt = win32gui.GetWindowText(h)
    cid = user32.GetDlgCtrlID(h)
    rows.append((cid, cls, txt, h))
    return True


if main:
    win32gui.EnumChildWindows(main, cb, None)

print("子控件总数 =", len(rows))
print("=== Button 类（或文本含 重填/刷新/买入/卖出）===")
for cid, cls, txt, h in rows:
    if cls == "Button" or ("重填" in txt) or ("刷新" in txt) or ("买" in txt) or ("卖" in txt):
        print("  cid=%-6s class=%-18s text=%r" % (cid, cls, txt))
print("=== 全部 cid<=1020 的控件 ===")
for cid, cls, txt, h in sorted(rows, key=lambda r: (r[0] if r[0] >= 0 else 99999)):
    if 0 <= cid <= 1020:
        print("  cid=%-6s class=%-18s text=%r" % (cid, cls, txt))
