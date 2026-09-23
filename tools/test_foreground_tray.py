# -*- coding: utf-8 -*-
"""foreground.py 托盘态逻辑测试（不依赖真机，mock win32gui）。

验证修复后：
  T1. find_window_by_title 能找到"隐藏/托盘"窗口（旧版只找可见窗口会漏掉）
  T2. force_foreground 对"隐藏窗口"先 ShowWindow(SW_RESTORE) 再兜底 SW_SHOW 恢复
  T3. force_foreground 对"正常可见窗口"不触发任何 ShowWindow（下单路径零副作用）
  T4. force_foreground 对"Iconic 最小化窗口"也走 ShowWindow 恢复
"""
import sys
import importlib.util


class FakeCon:
    SW_RESTORE = 9
    SW_SHOW = 5
    VK_MENU = 18
    KEYEVENTF_KEYUP = 2


class FakeGui:
    def __init__(self):
        self.calls = []
        self.hwnds = {}
        self.fg = 0

    def IsWindow(self, h):
        return h in self.hwnds

    def IsWindowVisible(self, h):
        return self.hwnds[h].get("visible", True)

    def IsIconic(self, h):
        return self.hwnds[h].get("iconic", False)

    def GetWindowText(self, h):
        return self.hwnds[h].get("title", "")

    def ShowWindow(self, h, cmd):
        self.calls.append(("ShowWindow", h, cmd))

    def SetForegroundWindow(self, h):
        self.calls.append(("SetForeground", h))

    def GetForegroundWindow(self):
        return self.fg

    def EnumWindows(self, cb, _):
        for h in self.hwnds:
            cb(h, None)


def load_fg(gui):
    sys.modules["win32gui"] = gui
    sys.modules["win32con"] = FakeCon()
    spec = importlib.util.spec_from_file_location(
        "foreground_t", r"<项目根目录>\executor\foreground.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- 场景1：托盘隐藏窗口 ----
gui = FakeGui()
HWND = 1001
gui.hwnds[HWND] = {"title": "网上股票交易系统5.0", "visible": False, "iconic": False}
gui.fg = HWND  # 模拟恢复后成为前台
fg = load_fg(gui)

got = fg.find_window_by_title("网上股票交易系统")
print("T1 find hidden tray window:", "PASS" if got == HWND else "FAIL got=%s" % got)

ok = fg.force_foreground(HWND)
show_cmds = [c[2] for c in gui.calls if c[0] == "ShowWindow"]
print("T2 force hidden -> ShowWindow called:", "PASS" if show_cmds else "FAIL",
      "cmds=", show_cmds, "result=", ok)

# ---- 场景2：正常可见窗口（应无 ShowWindow 副作用）----
gui2 = FakeGui()
HWND2 = 2002
gui2.hwnds[HWND2] = {"title": "网上股票交易系统5.0", "visible": True, "iconic": False}
gui2.fg = HWND2
fg2 = load_fg(gui2)
ok2 = fg2.force_foreground(HWND2)
show_cmds2 = [c[2] for c in gui2.calls if c[0] == "ShowWindow"]
print("T3 force visible -> no ShowWindow (无副作用):",
      "PASS" if not show_cmds2 else "FAIL cmds=%s" % show_cmds2, "result=", ok2)

# ---- 场景3：Iconic 最小化窗口 ----
gui3 = FakeGui()
HWND3 = 3003
gui3.hwnds[HWND3] = {"title": "网上股票交易系统5.0", "visible": True, "iconic": True}
gui3.fg = HWND3
fg3 = load_fg(gui3)
ok3 = fg3.force_foreground(HWND3)
show_cmds3 = [c[2] for c in gui3.calls if c[0] == "ShowWindow"]
print("T4 force iconic -> ShowWindow called:", "PASS" if show_cmds3 else "FAIL",
      "cmds=", show_cmds3, "result=", ok3)

allpass = (got == HWND) and bool(show_cmds) and (not show_cmds2) and bool(show_cmds3)
print("\nALL:", "PASS" if allpass else "FAIL")
sys.exit(0 if allpass else 1)
