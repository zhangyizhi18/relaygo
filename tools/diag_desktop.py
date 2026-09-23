# -*- coding: utf-8 -*-
"""诊断：为什么 pywinauto 在本会话做 UI 自动化报 "no active desktop"。
对比当前进程 与 同花顺进程 的 session / 窗口站 / 桌面，并实测 SetCursorPos。"""
import ctypes
import sys

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
try:
    import win32api, win32gui, win32process
except Exception as e:
    print("pywin32 missing:", e)
    sys.exit(1)

UOI_NAME = 2


def desk_name(h):
    if not h:
        return "(null)"
    buf = ctypes.create_unicode_buffer(256)
    need = ctypes.c_ulong()
    ok = u32.GetUserObjectInformationW(h, UOI_NAME, buf, 512, ctypes.byref(need))
    return buf.value if ok else "(err %d)" % ctypes.get_last_error()


def session_of(pid):
    sid = ctypes.c_ulong()
    if k32.ProcessIdToSessionId(pid, ctypes.byref(sid)):
        return sid.value
    return -1


my_pid = k32.GetCurrentProcessId()
my_tid = k32.GetCurrentThreadId()
print("=== 当前进程 ===")
print("  pid=%d tid=%d session=%d" % (my_pid, my_tid, session_of(my_pid)))
hcur_desk = u32.GetThreadDesktop(my_tid)
print("  当前线程桌面:", desk_name(hcur_desk))
hsta = u32.GetProcessWindowStation()
print("  进程窗口站:", desk_name(hsta))

print("=== 输入桌面(Active Desktop) ===")
DESKTOP_READOBJECTS = 0x0001
DESKTOP_SWITCHDESKTOP = 0x0100
hinput = u32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
print("  OpenInputDesktop:", hinput, "name=", desk_name(hinput) if hinput else "FAILED(lasterr=%d)" % ctypes.get_last_error())

print("=== SetCursorPos 实测（pywinauto 报错根因） ===")
try:
    win32api.SetCursorPos((300, 300))
    print("  SetCursorPos OK -> 本进程在活动桌面，UI 鼠标可用")
except Exception as e:
    print("  SetCursorPos FAILED ->", repr(e), "（=> 非活动桌面，pywinauto 鼠标操作必败）")

print("=== 同花顺进程所处桌面 ===")
found = []


def cb(h, _):
    try:
        t = win32gui.GetWindowText(h)
        if t and "网上股票交易系统" in t:
            found.append(h)
    except Exception:
        pass
    return True


win32gui.EnumWindows(cb, None)
if not found:
    print("  未找到同花顺交易窗口")
for h in found[:3]:
    tid = win32process.GetWindowThreadProcessId(h)[0]
    pid = win32process.GetWindowThreadProcessId(h)[1]
    hd = u32.GetThreadDesktop(tid)
    print("  hwnd=%d pid=%d session=%d 桌面=%s 可见=%s iconic=%s 标题=%r"
          % (h, pid, session_of(pid), desk_name(hd),
             win32gui.IsWindowVisible(h), win32gui.IsIconic(h), win32gui.GetWindowText(h)))

print("=== 当前前台窗口 ===")
fg = win32gui.GetForegroundWindow()
if fg:
    tid = win32process.GetWindowThreadProcessId(fg)[0]
    pid = win32process.GetWindowThreadProcessId(fg)[1]
    print("  hwnd=%d pid=%d session=%d 桌面=%s 标题=%r"
          % (fg, pid, session_of(pid), desk_name(u32.GetThreadDesktop(tid)), win32gui.GetWindowText(fg)))
