# -*- coding: utf-8 -*-
"""诊断脚本：逐个尝试四种窗口置前方法，并验证按键注入是否恢复。"""
import sys
sys.path.insert(0, r"<项目根目录>\executor")

import time
import win32com.client
import win32con
import win32gui

from foreground import THS_TITLE_KEYWORD, find_window_by_title

hwnd = find_window_by_title(THS_TITLE_KEYWORD)
print("目标窗口 hwnd=%s 标题=%r" % (hwnd, win32gui.GetWindowText(hwnd)))
print("当前前台窗口: %r" % win32gui.GetWindowText(win32gui.GetForegroundWindow()))

if not hwnd:
    print("未找到同花顺下单窗口！"); sys.exit(1)

# 0. 还原最小化
if win32gui.IsIconic(hwnd):
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.5)

# 方法1: 直接 SetForegroundWindow
try:
    win32gui.SetForegroundWindow(hwnd)
except Exception as e:
    print("方法1 直接SetForeground: 异常 %s" % e)
print("方法1 直接SetForeground: %s" % (win32gui.GetForegroundWindow() == hwnd))

# 方法2: ALT 键解锁
if win32gui.GetForegroundWindow() != hwnd:
    import win32api
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception as e:
        print("方法2 ALT解锁: 异常 %s" % e)
    print("方法2 ALT解锁: %s" % (win32gui.GetForegroundWindow() == hwnd))

# 方法3: WScript.Shell AppActivate
if win32gui.GetForegroundWindow() != hwnd:
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        r = shell.AppActivate(hwnd)
        time.sleep(0.3)
        print("方法3 AppActivate: %s" % (win32gui.GetForegroundWindow() == hwnd))
    except Exception as e:
        print("方法3 AppActivate: 异常 %s" % e)

# 方法4: AttachThreadInput
if win32gui.GetForegroundWindow() != hwnd:
    import ctypes
    user32 = ctypes.windll.user32
    fg = win32gui.GetForegroundWindow()
    cur_tid = user32.GetWindowThreadProcessId(fg, None)
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(cur_tid, tgt_tid, True)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    user32.AttachThreadInput(cur_tid, tgt_tid, False)
    print("方法4 AttachThreadInput: %s" % (win32gui.GetForegroundWindow() == hwnd))

# 最终验证：前台归属 + pywinauto 键盘注入是否恢复
print("最终前台: %r" % win32gui.GetWindowText(win32gui.GetForegroundWindow()))
if win32gui.GetForegroundWindow() == hwnd:
    try:
        import pywinauto.keyboard as kb
        kb.send_keys("{ESC}")   # 发一个无害的 ESC 测试注入
        print("按键注入测试: SendInput 成功")
    except Exception as e:
        print("按键注入测试: 失败 %s" % e)
else:
    print("所有置前方法均失败 —— 请确认没有锁屏/远程桌面断开，或前台存在别的全屏窗口")
