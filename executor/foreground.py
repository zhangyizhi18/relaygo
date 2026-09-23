# -*- coding: utf-8 -*-
"""
窗口置前工具 —— 解决 Windows 前台锁导致的 SetForegroundWindow 失败。

背景：Windows 规定，只有"刚收到用户输入"的进程才能把窗口设为前台。
     从后台服务/脚本启动的程序调用 SetForegroundWindow 会被系统拒绝，
     报错 (0, 'SetForegroundWindow', ...)，后续 SendInput 也会被拦截。
解法（业界标准）：
     1. 若窗口最小化先还原；
     2. 直接尝试 SetForegroundWindow；
     3. 失败则用"ALT 键解锁"技巧：先按一下 ALT 再调 SetForegroundWindow
        （按下 ALT 会给当前进程授予前台权限）；
     4. 仍失败再用 AttachThreadInput 把自己挂到前台线程共享输入状态。
"""
import ctypes
import time

import win32api
import win32con
import win32gui

THS_TITLE_KEYWORD = "网上股票交易系统"   # 同花顺「已登录后」交易窗口标题关键字（下单操作定位用）

# 交易窗口"真的可用"时才会出现的子控件类名（登录完成后才建出来）：
#   ToolbarWindow32 —— easytrader 连接时 _init_toolbar 就要它
#   SysTreeView32   —— 左侧功能菜单树（查询/买入/撤单…切页靠它）
READY_CHILD_CLASSES = ("ToolbarWindow32", "SysTreeView32")


def is_window_hung(hwnd):
    """窗口是否"假死"（消息循环长时间不响应）。

    pywin32 的 win32gui **没有导出 IsHungAppWindow**（实测 AttributeError），
    必须走 ctypes 调 user32。
    """
    try:
        return bool(ctypes.windll.user32.IsHungAppWindow(int(hwnd)))
    except Exception:
        return False


def _has_child_class(hwnd, names):
    """窗口下是否已存在指定类名的子控件（枚举到即停）。"""
    hit = [False]

    def _cb(child, _):
        try:
            if win32gui.GetClassName(child) in names:
                hit[0] = True
                return False      # 停止枚举
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        pass
    return hit[0]


def main_window_status(hwnd, keyword=THS_TITLE_KEYWORD):
    """交易窗口的就绪状态，返回一个可直接写进日志的中文短语。

    为什么需要它（2026-09-12 真机 + 用户现场）：同花顺拉起后主框架窗口
    「网上股票交易系统5.0」**几秒内就会出现**（所以"标题存在"根本不能当
    "已启动"），但内部的工具栏/左侧菜单树要几十秒才初始化完。在它没就绪时
    让 easytrader 连接，会触发 easytrader 的 _close_prompt_windows() 把
    同花顺自己的窗口（登录窗口标题是空串！）WM_CLOSE 掉 -> 同花顺要么整个
    退出（反复拉起/关闭），要么停在"窗口在、内容一片空白、点了没反应"的
    半死状态 —— 而原生子控件的文本还在，所以资金还读得到（用户报的现场）。
    因此"能不能让 easytrader 连接"必须按这里的就绪条件判定。
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return "未见交易窗口（未启动/尚未创建）"
    try:
        title = win32gui.GetWindowText(hwnd)
    except Exception:
        title = ""
    if not title or keyword not in title:
        return "窗口标题不匹配"
    if not win32gui.IsWindowVisible(hwnd):
        return "交易窗口不可见（缩在托盘）"
    if is_window_hung(hwnd):
        return "交易窗口未响应（假死）"
    if not _has_child_class(hwnd, READY_CHILD_CLASSES):
        return "交易窗口仍在初始化（工具栏/菜单树未出现）"
    return "ready"


def main_window_ready(hwnd, keyword=THS_TITLE_KEYWORD):
    """交易窗口是否"真的可用"（可以交给 easytrader 连接）。"""
    return main_window_status(hwnd, keyword) == "ready"


def find_window_by_title(keyword):
    """按标题关键字查找同花顺窗口句柄。找不到返回 0。

    重要：同花顺「最小化到托盘」时主窗口并未关闭，而是被 Hide
    （IsWindowVisible=False）或 Iconic，但窗口对象仍然存在。为支持从托盘
    恢复，这里不再强制要求 IsWindowVisible——匹配标题即收录；优先返回可见
    窗口，其次返回隐藏（托盘）窗口。这样既不影响正常可见窗口的查找，
    也能在窗口缩在托盘时仍定位到它。
    """
    visible, hidden = [], []

    def _cb(hwnd, _):
        if win32gui.IsWindow(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and keyword in title:
                if win32gui.IsWindowVisible(hwnd):
                    visible.append(hwnd)
                else:
                    hidden.append(hwnd)
        return True

    win32gui.EnumWindows(_cb, None)
    if visible:
        return visible[0]
    if hidden:
        return hidden[0]
    return 0


def force_foreground(hwnd):
    """强制把窗口带到前台并聚焦。返回 True=成功。"""
    if not hwnd:
        return False
    # 1. 托盘/最小化（不可见或 Iconic）先恢复显示
    try:
        if win32gui.IsIconic(hwnd) or (not win32gui.IsWindowVisible(hwnd)):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.4)
            # 兜底：SW_RESTORE 后仍不可见（部分客户端用 Hide 实现托盘），
            # 再用 SW_SHOW 强制把窗口显示出来
            if not win32gui.IsWindowVisible(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                time.sleep(0.3)
    except Exception:
        pass

    # 2. 直接尝试
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    # 3. ALT 键解锁
    try:
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)      # ALT down
        win32api.keybd_event(win32con.VK_MENU, 0,
                             win32con.KEYEVENTF_KEYUP, 0)    # ALT up
        time.sleep(0.05)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    # 4. AttachThreadInput 共享输入队列
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        foreground = win32gui.GetForegroundWindow()
        cur_tid = user32.GetWindowThreadProcessId(foreground, None)
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        if cur_tid and target_tid:
            user32.AttachThreadInput(cur_tid, target_tid, True)
            try:
                win32gui.SetForegroundWindow(hwnd)
            finally:
                user32.AttachThreadInput(cur_tid, target_tid, False)
    except Exception:
        pass
    return win32gui.GetForegroundWindow() == hwnd


def focus_ths_window():
    """一站式：找到同花顺下单窗口并置前。返回 (ok, hwnd)。"""
    hwnd = find_window_by_title(THS_TITLE_KEYWORD)
    if not hwnd:
        return False, 0
    return force_foreground(hwnd), hwnd
