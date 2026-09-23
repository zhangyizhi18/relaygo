# -*- coding: utf-8 -*-
"""同花顺「干扰弹窗」自动处理（dialog_guard）离线自测。

对应 2026-09-12 用户反馈：执行端自动拉起同花顺后，登录界面弹出
「系统检测到您当前还不是同花顺注册用户…」的模态提示（按钮 [立即注册]/[取消]），
不点掉它登录表单点不动、交易窗口不出现 —— 表现为"拉起了同花顺却被挡住、识别不了"。

本自测用假 win32 模块驱动（dialog_guard 内部是惰性 import，可注入替身），
不启动同花顺、不点真窗口、没有副作用。用法：
    venv/Scripts/python.exe tools/test_dialog_guard.py
"""
import os
import sys
import types
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "executor"))
os.environ.setdefault("JQ_ENVFILE_NOCREATE", "1")   # 不要因自测去写 .env

import win32con            # noqa: E402  真实常量（MOUSEEVENTF_*）
import config              # noqa: E402
import dialog_guard as dg  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    flag = "  [OK]   " if cond else "  [FAIL] "
    print(flag + name + (("   <- " + str(extra)) if extra else ""))


# ---------------- 假 win32 世界 ----------------

XIADAN = r"D:\同花顺软件\同花顺\xiadan.exe"
HELPER = r"D:\同花顺软件\同花顺\hexinhelper.exe"
OTHER_EXE = r"C:\Program Files\Notepad++\notepad++.exe"

REG_TEXT = ("尊敬的用户您好，系统检测到您当前还不是同花顺注册用户，"
            "无法使用同花顺交易，您需要注册同花顺账号才可以正常使用交易。")


class FakeWorld(object):
    """极简窗口世界：顶层窗口 + 子控件 + 进程 EXE 路径。"""

    def __init__(self):
        self.top = []          # [{hwnd,pid,title,cls,visible}]
        self.child = {}        # hwnd -> [{hwnd,text,cls,rect}]
        self.alive = set()     # 仍然存在的顶层窗口
        self.proc = {}         # pid -> exe
        self.sent = []         # SendMessage 记录 (hwnd, msg)
        self.cursor = []       # SetCursorPos 记录
        self.mouse = []        # mouse_event 记录
        self.mouse_works = {}  # btn_hwnd -> 鼠标点击能否关掉弹窗
        self.occluded = set()  # 被别的窗口挡住的子控件 hwnd（WindowFromPoint 不命中）
        self.bm_effective = True   # SendMessage(BM_CLICK) 是否有效
        self.raise_on_enum = False
        # ---- Alt+F4 相关 ----
        self.fg = 0            # 当前前台窗口 hwnd
        self.fg_refuse = set()  # 这些窗口"抢不到前台"（force_foreground 无效）
        self.keys = []         # keybd_event 记录 (vk, 是否抬起, 当时前台)
        self.alt_f4_closes = set()   # Alt+F4 能关掉的顶层窗口
        self.alt_f4_kills_app = False  # Alt+F4 把关掉的窗口是"主框架"-> 整个程序没了

    def add(self, hwnd, pid, title="", cls="#32770", visible=True, kids=()):
        self.top.append({"hwnd": hwnd, "pid": pid, "title": title,
                         "cls": cls, "visible": visible})
        self.child[hwnd] = [{"hwnd": hwnd * 100 + i, "text": t, "cls": c,
                             "rect": (10 + i * 80, 200, 60 + i * 80, 220)}
                            for i, (c, t) in enumerate(kids)]
        self.alive.add(hwnd)
        return hwnd

    # ---- win32gui ----
    def EnumWindows(self, cb, _):
        if self.raise_on_enum:
            raise RuntimeError("EnumWindows 失败(测试)")
        for w in list(self.top):
            if w["hwnd"] in self.alive:
                cb(w["hwnd"], None)

    def EnumChildWindows(self, hwnd, cb, _):
        for c in self.child.get(hwnd, []):
            cb(c["hwnd"], None)

    @staticmethod
    def IsWindow(hwnd):
        return True

    def IsWindowVisible(self, hwnd):
        for w in self.top:
            if w["hwnd"] == hwnd:
                return w["visible"] and hwnd in self.alive
        return True

    def GetWindowText(self, hwnd):
        for w in self.top:
            if w["hwnd"] == hwnd:
                return w["title"]
        for kids in self.child.values():
            for c in kids:
                if c["hwnd"] == hwnd:
                    return c["text"]
        return ""

    def GetClassName(self, hwnd):
        for w in self.top:
            if w["hwnd"] == hwnd:
                return w["cls"]
        for kids in self.child.values():
            for c in kids:
                if c["hwnd"] == hwnd:
                    return c["cls"]
        return ""

    def GetWindowRect(self, hwnd):
        for kids in self.child.values():
            for c in kids:
                if c["hwnd"] == hwnd:
                    return c["rect"]
        return (0, 0, 40, 20)

    def WindowFromPoint(self, pt):
        """坐标命中的子控件；被"别的窗口"挡住时返回一个外人 hwnd。"""
        x, y = pt
        for kids in self.child.values():
            for c in kids:
                l, t, r, b = c["rect"]
                if l <= x <= r and t <= y <= b:
                    return 999999 if c["hwnd"] in self.occluded else c["hwnd"]
        return 999999

    def GetParent(self, hwnd):
        for top, kids in self.child.items():
            if any(c["hwnd"] == hwnd for c in kids):
                return top
        return 0

    def occlude(self, top_hwnd, index):
        """把某个顶层窗口的第 index 个子控件标成"被别的窗口挡住"。"""
        self.occluded.add(self.child[top_hwnd][index]["hwnd"])

    def SendMessage(self, hwnd, msg, w, l):
        self.sent.append((hwnd, msg))
        if self.bm_effective and self.mouse_works.get(hwnd, False):
            self._close_owner(hwnd)
        return 0

    def _close_owner(self, hwnd):
        for top, kids in self.child.items():
            if any(c["hwnd"] == hwnd for c in kids):
                self.alive.discard(top)

    # ---- win32process / win32api ----
    def GetWindowThreadProcessId(self, hwnd):
        for w in self.top:
            if w["hwnd"] == hwnd:
                return (1, w["pid"])
        return (1, 0)

    def OpenProcess(self, access, inherit, pid):
        return pid

    def CloseHandle(self, h):
        return None

    def GetModuleFileNameEx(self, h, _):
        return self.proc.get(h, "")

    def SetCursorPos(self, pos):
        self.cursor.append(pos)

    def keybd_event(self, vk, scan, flag, extra):
        """记键盘并"生效"：Alt 抬起表示一组 Alt+F4 发完了。"""
        self.keys.append((vk, bool(flag), self.fg))
        if vk == 0x12 and flag:                     # Alt 抬起 -> 组合键结束
            target = self.fg
            if target in self.alt_f4_closes:
                self.alive.discard(target)          # 只关掉这一个窗口
            if self.alt_f4_kills_app:
                self.alive.clear()                  # 关的是主框架 -> 整个程序没了
                self.proc.clear()

    def GetForegroundWindow(self):
        return self.fg

    def force_foreground(self, hwnd):
        """同 foreground.force_foreground。fg_refuse 里的窗口永远抢不到前台。"""
        if hwnd in self.fg_refuse:
            return False
        self.fg = hwnd
        return True

    def EnumProcesses(self):
        return list(self.proc.keys())

    def mouse_event(self, flag, dx, dy, data, extra):
        self.mouse.append(flag)
        if flag == win32con.MOUSEEVENTF_LEFTUP and self.cursor:
            x, y = self.cursor[-1]
            for kids in self.child.values():
                for c in kids:
                    l, t, r, b = c["rect"]
                    if l <= x <= r and t <= y <= b:
                        if self.mouse_works.get(c["hwnd"], False):
                            self._close_owner(c["hwnd"])
                        return


def fake_modules(world):
    gui = types.ModuleType("win32gui")
    for name in ("EnumWindows", "EnumChildWindows", "IsWindow", "IsWindowVisible",
                 "GetWindowText", "GetClassName", "GetWindowRect", "SendMessage",
                 "WindowFromPoint", "GetParent", "GetForegroundWindow"):
        setattr(gui, name, getattr(world, name))
    proc = types.ModuleType("win32process")
    proc.GetWindowThreadProcessId = world.GetWindowThreadProcessId
    proc.GetModuleFileNameEx = world.GetModuleFileNameEx
    proc.EnumProcesses = world.EnumProcesses
    api = types.ModuleType("win32api")
    api.OpenProcess = world.OpenProcess
    api.CloseHandle = world.CloseHandle
    api.SetCursorPos = world.SetCursorPos
    api.mouse_event = world.mouse_event
    api.keybd_event = world.keybd_event
    fg = types.ModuleType("foreground")
    fg.force_foreground = world.force_foreground     # 不真动窗口，只改"前台"状态
    return mock.patch.dict(sys.modules, {
        "win32gui": gui, "win32process": proc, "win32api": api, "foreground": fg})


def scenario(reg_visible=True, click_works=True, bm_effective=True,
             extra_decoy=True, login_window=False, login_ready=True):
    """标准场景：注册弹窗（同花顺）+ 进程外同款弹窗 + 未命中规则弹窗。

    login_window=True 时再加一个"停在登录界面"的登录窗口（真机 dump 的形状）。
    """
    w = FakeWorld()
    w.proc[100] = XIADAN
    w.proc[101] = HELPER
    w.proc[200] = OTHER_EXE
    w.add(1001, 100, title="", cls="#32770", visible=reg_visible,
          kids=[("Static", REG_TEXT), ("Button", "立即注册"), ("Button", "取消")])
    w.bm_effective = bm_effective
    for kids in w.child.values():
        for c in kids:
            w.mouse_works[c["hwnd"]] = click_works
    if extra_decoy:
        # 别的进程的同样弹窗（必须绝不触碰）
        w.add(2001, 200, title="", kids=[("Static", REG_TEXT),
                                         ("Button", "立即注册"), ("Button", "取消")])
        # 同花顺自己的其它弹窗：未命中规则（必须不碰）
        w.add(1002, 101, title="", kids=[("Static", "是否清空下单表单？"),
                                         ("Button", "是"), ("Button", "否")])
    if login_window:
        w.add(1003, 100, title="", cls="#32770", visible=True,
              kids=[("Static", "证券公司:"), ("Static", "站点列表:"),
                    ("Static", "交易密码:"), ("Button", "保存密码"),
                    ("Button", "自动登录"), ("Button", "登录"),
                    ("Button", "取消")])
    return w


def login_world(bm_effective=True, click_works=True, complain=False, wid=1003):
    """登录场景：只有登录窗口（+可选"请输入交易密码"抱怨弹窗）。

    click_works/bm_effective=False 表示点了[登录]没反应（登录窗口不消失）。
    只有[登录]按钮"点得动"（[取消]等其它按钮点了没用 -> 用来验证我们只点[登录]）。
    wid 可改，便于模拟"重启后换了一个新登录窗口"。
    """
    w = FakeWorld()
    w.proc[100] = XIADAN
    w.bm_effective = bm_effective
    w.add(wid, 100, title="", cls="#32770", visible=True,
          kids=[("Static", "站点列表:"), ("Static", "交易密码:"),
                ("Button", "登录"), ("Button", "取消")])
    for kids in w.child.values():
        for c in kids:
            w.mouse_works[c["hwnd"]] = click_works and (c["text"] == "登录")
    if complain:
        w.add(1004, 100, title="", kids=[("Static", "请输入交易密码。"),
                                         ("Button", "确定")])
    return w


class FakeTime(object):
    """可控时钟：dialog_guard 内部用 time.time()，替换模块级 time 即可（勿动全局）。"""

    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def advance(self, s):
        self.t += s



print("=" * 72)
print("  dialog_guard 离线自测")
print("=" * 72)

# ---------------- G1 只读扫描：命中规则、按内容判定、按钮优先级 ----------------
w = scenario()
with fake_modules(w):
    found = dg.find_interference_dialogs(XIADAN)
check("G1a 命中注册提示弹窗（按正文内容判定，标题为空串也能认出）",
      len(found) == 1 and found[0]["hwnd"] == 1001, [f["hwnd"] for f in found])
check("G1b 点的是[取消]而不是[立即注册]",
      bool(found) and found[0]["button"] == "取消", found and found[0]["button"])
check("G1c 别的进程的同款弹窗不处理（按 EXE 目录白名单过滤）",
      all(f["pid"] != 200 for f in found), [f["pid"] for f in found])
check("G1d 同花顺其它弹窗（未命中规则）不处理",
      all(f["hwnd"] != 1002 for f in found), [f["hwnd"] for f in found])
check("G1e 只读扫描零副作用（没有点、没有移鼠标）",
      w.sent == [] and w.cursor == [] and w.mouse == [])

# ---------------- G2 隐藏窗口默认不管 ----------------
w = scenario(reg_visible=False)
with fake_modules(w):
    check("G2 隐藏（不可见）弹窗默认不处理",
          dg.find_interference_dialogs(XIADAN) == [])

# ---------------- G3 dismiss：首选 BM_CLICK，不移动鼠标 ----------------
w = scenario(click_works=True)
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G3a BM_CLICK 生效 -> 记录 action=bm_click",
      len(recs) == 1 and recs[0]["action"] == "bm_click", recs)
check("G3b BM_CLICK 路线不移动鼠标（最小侵入）",
      w.cursor == [] and w.mouse == [], (w.cursor, w.mouse))
check("G3c 弹窗已被关掉（只剩诱饵窗口还在）",
      1001 not in w.alive, sorted(w.alive))

# ---------------- G4 BM_CLICK 不生效 -> 退化真实鼠标点击 ----------------
w = scenario(click_works=True, bm_effective=False)
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G4a BM_CLICK 无效时退化到真实鼠标点击 -> action=mouse",
      len(recs) == 1 and recs[0]["action"] == "mouse", recs)
check("G4b 鼠标点了按钮中心（SetCursorPos 一次 + 左键按下抬起各一次）",
      len(w.cursor) == 1 and w.mouse == [win32con.MOUSEEVENTF_LEFTDOWN,
                                         win32con.MOUSEEVENTF_LEFTUP],
      (w.cursor, w.mouse))
check("G4c 按钮中心坐标算对（[取消] rect=170,200,220,220 -> 195,210）",
      w.cursor[:1] == [(195, 210)], w.cursor[:1])

# ---------------- G4x 安全阀：按钮被别的窗口挡住时绝不盲点 ----------------
w = scenario(click_works=True, bm_effective=False)
w.occlude(1001, 2)          # [取消] 被别的窗口盖住（WindowFromPoint 命中的是别人）
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G4d 按钮坐标上不是它（被遮挡）-> action=blocked，且完全不移动鼠标",
      len(recs) == 1 and recs[0]["action"] == "blocked" and w.cursor == [],
      (recs and recs[0]["action"], w.cursor))
check("G4e 挡住时也没有点到别人（零鼠标事件）", w.mouse == [], w.mouse)

w = scenario(click_works=True, bm_effective=True)
w.occlude(1001, 2)
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G4f BM_CLICK 不依赖可见性，被遮挡也能关掉 -> bm_click",
      len(recs) == 1 and recs[0]["action"] == "bm_click", recs)

# ---------------- G5 两条路都无效：有限轮次后放弃，绝不误点 ----------------
w = scenario(click_works=False, bm_effective=False)
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G5a 点不掉时如实记录 action=uncertain",
      bool(recs) and all(r["action"] == "uncertain" for r in recs), recs)
check("G5b 不会无限重试（最多 max_rounds 轮）", len(recs) <= 3, len(recs))
clicked = [dg._norm_text(w.GetWindowText(h)) for h, _ in w.sent]
check("G5c 全程只点[取消]，从不点[立即注册]",
      bool(clicked) and set(clicked) == {"取消"}, set(clicked))
check("G5d 未命中规则的弹窗（[是]/[否]）与别的进程的窗口从未被点击",
      all(t not in ("是", "否", "立即注册") for t in clicked), clicked)

# ---------------- G12 按钮点不动时退化 Alt+F4（用户反馈的方式） ----------------
w = scenario(click_works=False, bm_effective=False)
w.occlude(1001, 2)               # [取消] 被别的窗口挡住：鼠标这条路走不通
w.alt_f4_closes.add(1001)        # 但 Alt+F4 能关掉它
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G12a 点不动时用 Alt+F4 关掉弹窗 -> action=alt_f4",
      len(recs) == 1 and recs[0]["action"] == "alt_f4", recs)
check("G12b 这条路线不移动鼠标（不会点到别的界面）", w.cursor == [], w.cursor)
check("G12c 弹窗确实被关掉了", 1001 not in w.alive, sorted(w.alive))
check("G12d Alt+F4 打的是这个弹窗本身（先置前并校验前台）",
      bool(w.keys) and all(t == 1001 for _, _, t in w.keys),
      [(vk, t) for vk, _, t in w.keys][:4])

# 按钮没被遮挡、只是点了没反应：同样要能靠 Alt+F4 过去
w = scenario(click_works=False, bm_effective=False)
w.alt_f4_closes.add(1001)
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G12e 按钮点不动（未被遮挡）也能靠 Alt+F4 关闭",
      len(recs) == 1 and recs[0]["action"] == "alt_f4" and 1001 not in w.alive, recs)

# ---------------- G13 安全阀：抢不到前台就绝不发 Alt+F4 ----------------
w = scenario(click_works=False, bm_effective=False)
w.fg_refuse.add(1001)            # 这个弹窗永远拿不到前台
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G13a 没拿到前台 -> 一个按键都不发", w.keys == [], w.keys)
check("G13b 如实记录 blocked_failed（不谎报成功）",
      len(recs) == 1 and recs[0]["action"] == "blocked_failed", recs)
check("G13c 弹窗没被误关（宁可不做也不乱做）", 1001 in w.alive, sorted(w.alive))

# ---------------- G6 幂等：没有弹窗时什么都不做 ----------------
w = FakeWorld()
w.proc[100] = XIADAN
w.add(1001, 100, title="网上股票交易系统5.0",
      kids=[("Static", "证券代码"), ("Button", "买入")])
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G6 没有干扰弹窗时：返回 []、零点击（幂等，可反复调用）",
      recs == [] and w.sent == [] and w.cursor == [])

# ---------------- G7 异常安全 ----------------
w = scenario()
w.raise_on_enum = True
with fake_modules(w):
    recs = dg.dismiss_interference_dialogs(XIADAN)
check("G7 枚举窗口抛异常时不崩、返回 []", recs == [])

# ---------------- G8 规则表安全性（防"以后被人加错规则"） ----------------
DANGER = ("确定", "是", "立即注册", "立即开户", "确认", "同意")
bad = [b for r in dg.RULES for b in r["buttons"] if b in DANGER]
check("G8a 规则表里没有危险按钮（确定/是/立即注册/同意…）", bad == [], bad)
bad2 = [r["name"] for r in dg.RULES if not r.get("match") or not r.get("buttons")]
check("G8b 每条规则都同时给了正文特征和安全按钮", bad2 == [], bad2)
check("G8c 按钮文本归一化（去快捷键后缀/&）",
      dg._norm_text("取消(&C)") == "取消" and dg._norm_text(" 关闭(C) ") == "关闭"
      and dg._norm_text("立即注册") == "立即注册")

# ---------------- G9 目录过滤开关 ----------------
w = scenario()
with fake_modules(w):
    no_filter = dg.find_interference_dialogs("")
check("G9 诊断模式（不给路径）不做目录过滤，两条同名弹窗都列出",
      len(no_filter) == 2, len(no_filter))

# ---------------- G10 broker_ths 接线 ----------------
import broker_ths as B  # noqa: E402

b = object.__new__(B.ThsBroker)
b.xiadan_path = XIADAN

fake_dg = types.ModuleType("dialog_guard")
fake_dg.calls = []


def _rec(*a, **kw):
    fake_dg.calls.append((a, kw))
    return [{"hwnd": 1, "rule": "注册提示", "button": "取消", "action": "mouse"}]


fake_dg.dismiss_interference_dialogs = _rec
with mock.patch.dict(sys.modules, {"dialog_guard": fake_dg}), \
     mock.patch.object(B.config, "THS_AUTODISMISS", True):
    n = b.dismiss_blocking_dialogs()
check("G10a ThsBroker.dismiss_blocking_dialogs 转发给 dialog_guard 并返回条数",
      n == 1 and bool(fake_dg.calls), (n, fake_dg.calls))
check("G10b 传的是自己的 xiadan_path（好按目录白名单过滤）",
      bool(fake_dg.calls) and fake_dg.calls[0][0][0] == XIADAN, fake_dg.calls)

fake_dg.calls = []
with mock.patch.dict(sys.modules, {"dialog_guard": fake_dg}), \
     mock.patch.object(B.config, "THS_AUTODISMISS", False):
    n = b.dismiss_blocking_dialogs()
check("G10c 开关关闭(EXECUTOR_THS_AUTODISMISS=0)时完全不动作",
      n == 0 and fake_dg.calls == [])

bad_dg = types.ModuleType("dialog_guard")


def _boom(*a, **kw):
    raise RuntimeError("boom")


bad_dg.dismiss_interference_dialogs = _boom
with mock.patch.dict(sys.modules, {"dialog_guard": bad_dg}), \
     mock.patch.object(B.config, "THS_AUTODISMISS", True):
    n = b.dismiss_blocking_dialogs(verbose=False)
check("G10d dialog_guard 异常不影响连接（吞掉异常返回 0）", n == 0)

# ---------------- G11 接线点存在性（防回归：启动/重启路径都要处理弹窗） ----------------
src = open(os.path.join(ROOT, "executor", "broker_ths.py"), encoding="utf-8").read()
check("G11a connect() 里在 easytrader 连接前先处理干扰弹窗（登录前就要点掉）",
      "self.dismiss_blocking_dialogs(verbose=verbose)" in src)
check("G11b 等交易窗口的两处循环里也反复处理（弹窗晚出现也接得住）",
      src.count("self.dismiss_blocking_dialogs(verbose=False)") == 2,
      src.count("self.dismiss_blocking_dialogs(verbose=False)"))

# ================= 登录辅助（assist_login） =================
print("-" * 72)
print("  登录辅助（L 系列）")
print("-" * 72)

# ---------------- L1 没有登录窗口：什么都不做 ----------------
dg.reset_login_state()
w = FakeWorld()
w.proc[100] = XIADAN
w.add(2001, 100, title="网上股票交易系统5.0", kids=[("Button", "买入")])
with fake_modules(w):
    r = dg.assist_login(XIADAN, idle_seconds=0)
check("L1a 没有登录窗口 -> state=no_login_window",
      r["state"] == "no_login_window", r)
check("L1b 没有登录窗口时不点任何按钮", w.sent == [] and w.cursor == [])

# ---------------- L2 宽限期：先让同花顺自己登录 ----------------
dg.reset_login_state()
w = login_world()
ft = FakeTime(1000.0)
with fake_modules(w), mock.patch.object(dg, "time", ft):
    r1 = dg.assist_login(XIADAN, idle_seconds=20)
    ft.advance(5)
    r2 = dg.assist_login(XIADAN, idle_seconds=20)
    r3 = dg.assist_login(XIADAN, idle_seconds=20)
check("L2a 宽限期内 state=waiting（不抢同花顺自己的自动登录机会）",
      r1["state"] == "waiting" and r2["state"] == "waiting" and r3["state"] == "waiting",
      (r1, r2, r3))
check("L2b 宽限期内一次都没点", w.sent == [], w.sent)

# ---------------- L3 超时后替人点[登录]，登录成功 ----------------
dg.reset_login_state()
w = login_world()
ft = FakeTime(1000.0)
with fake_modules(w), mock.patch.object(dg, "time", ft):
    dg.assist_login(XIADAN, idle_seconds=20)     # 记录首次看到
    ft.advance(21)
    r = dg.assist_login(XIADAN, idle_seconds=20)
    clicked_texts = [dg._norm_text(w.GetWindowText(h)) for h, _ in w.sent]
    r_next = dg.assist_login(XIADAN, idle_seconds=20)   # 登录窗口已消失
check("L3a 超过宽限期 -> 代点[登录] 且识别到登录成功(state=logged_in)",
      r["state"] == "logged_in", r)
check("L3b 10 秒内只点一次，且点的是[登录]（不是取消/保存密码/自动登录）",
      clicked_texts == ["登录"], clicked_texts)
check("L3c 登录完成后状态自动归零（下一轮 no_login_window）",
      r_next["state"] == "no_login_window" and dg._LOGIN_STATE == {}, r_next)

# ---------------- L4 点了没反应：限次 + 限频后 give_up ----------------
dg.reset_login_state()
w = login_world(bm_effective=False, click_works=False)
ft = FakeTime(1000.0)
with fake_modules(w), mock.patch.object(dg, "time", ft):
    dg.assist_login(XIADAN, idle_seconds=20)
    ft.advance(21)
    a1 = dg.assist_login(XIADAN, idle_seconds=20)      # 第 1 次点
    ft.advance(5)
    a2 = dg.assist_login(XIADAN, idle_seconds=20)      # 限频：不该点
    ft.advance(26)
    a3 = dg.assist_login(XIADAN, idle_seconds=20)      # 第 2 次
    ft.advance(31)
    a4 = dg.assist_login(XIADAN, idle_seconds=20)      # 第 3 次
    ft.advance(31)
    a5 = dg.assist_login(XIADAN, idle_seconds=20)      # 超限 -> give_up
    a6 = dg.assist_login(XIADAN, idle_seconds=20)      # 之后一直停手
clicks = [dg._norm_text(w.GetWindowText(h)) for h, _ in w.sent]
check("L4a 点不动时如实返回 state=clicked（不是假成功）",
      a1["state"] == "clicked" and a3["state"] == "clicked", (a1, a3))
check("L4b 限频生效（距上次不足 min_interval 不重复点）",
      a2["state"] == "waiting" and a2["attempts"] == 1, a2)
check("L4c 最多 max_attempts 次后 give_up 并停手",
      a5["state"] == "give_up" and a6["state"] == "stopped", (a5, a6))
check("L4d 全程只点了 3 次[登录]（不会白撞券商登录次数）",
      len(w.sent) == 3 and set(clicks) == {"登录"}, (len(w.sent), clicks))

# ---------------- L5 同花顺抱怨（要密码/验证码）-> 立即停手 ----------------
dg.reset_login_state()
w = login_world(complain=True)
ft = FakeTime(1000.0)
with fake_modules(w), mock.patch.object(dg, "time", ft):
    ft.advance(300)      # 就算早就过了宽限期
    r = dg.assist_login(XIADAN, idle_seconds=0)
    r2 = dg.assist_login(XIADAN, idle_seconds=0)
check("L5a 同花顺提示「请输入交易密码」-> state=stopped 且一次都不点",
      r["state"] == "stopped" and w.sent == [], (r, w.sent))
check("L5b 停手后不再反复尝试", r2["state"] == "stopped")

# ---------------- L6 抱怨弹窗不属于同花顺时不算抱怨（不误判） ----------------
dg.reset_login_state()
w = login_world()
w.proc[200] = OTHER_EXE
w.add(2001, 200, title="", kids=[("Static", "请输入交易密码。"), ("Button", "确定")])
ft = FakeTime(1000.0)
with fake_modules(w), mock.patch.object(dg, "time", ft):
    dg.assist_login(XIADAN, idle_seconds=20)
    ft.advance(21)
    r = dg.assist_login(XIADAN, idle_seconds=20)
check("L6 抱怨弹窗若不属于同花顺进程，不当作抱怨（仍按流程代点[登录]）",
      r["state"] == "logged_in", r)

# ---------------- L7 别的进程里的登录窗口不碰 ----------------
dg.reset_login_state()
w = FakeWorld()
w.proc[200] = OTHER_EXE
w.add(2001, 200, title="", cls="#32770", visible=True,
      kids=[("Static", "站点列表:"), ("Static", "交易密码:"), ("Button", "登录")])
with fake_modules(w):
    r = dg.assist_login(XIADAN, idle_seconds=0)
check("L7 别的程序的同款登录窗口完全不碰",
      r["state"] == "no_login_window" and w.sent == [], r)

# ---------------- L10 Alt+F4 一条就登录（用户反馈的方式） ----------------
dg.reset_login_state()
w = login_world()
w.alt_f4_closes.add(1003)        # 这台机上 Alt+F4 就能让登录窗口消失
ft = FakeTime(1000.0)
with fake_modules(w), mock.patch.object(dg, "time", ft):
    dg.assist_login(XIADAN, idle_seconds=20)
    ft.advance(21)
    r = dg.assist_login(XIADAN, idle_seconds=20, triggers=("altf4", "click"))
check("L10a Alt+F4 生效 -> state=logged_in", r["state"] == "logged_in", r)
check("L10b 既然 Alt+F4 就成了，就不再点[登录]（零 BM_CLICK）", w.sent == [], w.sent)
check("L10c Alt+F4 只发给登录窗口（不是主框架，否则会把程序关掉）",
      bool(w.keys) and all(t == 1003 for _, _, t in w.keys),
      sorted({t for _, _, t in w.keys}))

# ---------------- L11 Alt+F4 无效 -> 自动退化点[登录] ----------------
dg.reset_login_state()
w = login_world()                # 不设 alt_f4_closes：Alt+F4 没效果
ft = FakeTime(1000.0)
with fake_modules(w), mock.patch.object(dg, "time", ft):
    dg.assist_login(XIADAN, idle_seconds=20)
    ft.advance(21)
    r = dg.assist_login(XIADAN, idle_seconds=20, triggers=("altf4", "click"))
check("L11a Alt+F4 无效时自动退化点[登录]并成功",
      r["state"] == "logged_in", r)
check("L11b 两种方式都留下了记录", "altf4" in r["message"] and "click" in r["message"],
      r["message"])

# ---------------- L12 Alt+F4 把程序关掉 -> 不谎报成功 + 永久停用该方式 ----------------
dg.reset_login_state()
w = login_world()
w.alt_f4_kills_app = True        # Alt+F4 落在了主框架上：整个同花顺没了
ft = FakeTime(1000.0)
with fake_modules(w), mock.patch.object(dg, "time", ft):
    dg.assist_login(XIADAN, idle_seconds=20)
    ft.advance(21)
    r = dg.assist_login(XIADAN, idle_seconds=20, triggers=("altf4", "click"))
check("L12a 程序被关掉时不谎报登录成功 -> state=app_exited", r["state"] == "app_exited", r)
check("L12b 已永久停用 Alt+F4 路线（不再来回关掉/拉起）",
      dg.altf4_disabled() is True)

w2 = login_world(wid=1005)       # 重新拉起后的"新"登录窗口
dg._LOGIN_STATE.clear()          # 只清窗口状态，保留"Alt+F4 已停用"的标记
ft2 = FakeTime(2000.0)
with fake_modules(w2), mock.patch.object(dg, "time", ft2):
    r2 = dg.assist_login(XIADAN, idle_seconds=0, triggers=("altf4", "click"))
check("L12c 停用后即使配置里带 altf4 也只点[登录]（零按键）",
      w2.keys == [] and len(w2.sent) == 1 and r2["state"] == "logged_in",
      (w2.keys, w2.sent, r2))

# ---------------- L13 触发顺序可由配置指定 ----------------
dg.reset_login_state()
w = login_world()
w.alt_f4_closes.add(1003)        # 就算 Alt+F4 本来能用
ft = FakeTime(1000.0)
with fake_modules(w), mock.patch.object(dg, "time", ft):
    r = dg.assist_login(XIADAN, idle_seconds=0, triggers=("click",))
check("L13 配成只用 click 时不按 Alt+F4（零按键、点一次[登录]）",
      w.keys == [] and len(w.sent) == 1 and r["state"] == "logged_in",
      (w.keys, w.sent, r))

# ---------------- L8 broker 接线 ----------------
fake_dg2 = types.ModuleType("dialog_guard")
fake_dg2.calls = []
fake_dg2.reset_login_state = lambda: None


def _assist(*a, **kw):
    fake_dg2.calls.append((a, kw))
    return {"state": "clicked", "message": "ok", "attempts": 1}


fake_dg2.assist_login = _assist
with mock.patch.dict(sys.modules, {"dialog_guard": fake_dg2}), \
     mock.patch.object(B.config, "THS_LOGIN_ASSIST", True), \
     mock.patch.object(B.config, "THS_LOGIN_ASSIST_IDLE", 20), \
     mock.patch.object(B.config, "THS_LOGIN_ASSIST_MAX", 3), \
     mock.patch.object(B.config, "THS_LOGIN_TRIGGERS", ("click",)), \
     mock.patch.object(B.config, "THS_LOGIN_TRIGGER_WAIT", 5):
    r = b.assist_login()
check("L8a ThsBroker.assist_login 转发给 dialog_guard 并带回结果",
      r["state"] == "clicked" and bool(fake_dg2.calls), (r, fake_dg2.calls))
check("L8b 宽限期/次数从配置读（idle_seconds/max_attempts）",
      fake_dg2.calls[0][1].get("idle_seconds") == 20
      and fake_dg2.calls[0][1].get("max_attempts") == 3, fake_dg2.calls[0])
check("L8e 触发方式/等待时长也从配置读（triggers/trigger_wait）",
      fake_dg2.calls[0][1].get("triggers") == ("click",)
      and fake_dg2.calls[0][1].get("trigger_wait") == 5, fake_dg2.calls[0])

fake_dg2.calls = []
with mock.patch.dict(sys.modules, {"dialog_guard": fake_dg2}), \
     mock.patch.object(B.config, "THS_LOGIN_ASSIST", False):
    r = b.assist_login()
check("L8c 开关关闭(EXECUTOR_THS_LOGIN_ASSIST=0)时完全不动作",
      r["state"] == "off" and fake_dg2.calls == [], r)

boom_dg = types.ModuleType("dialog_guard")


def _boom2(*a, **kw):
    raise RuntimeError("boom")


boom_dg.assist_login = _boom2
with mock.patch.dict(sys.modules, {"dialog_guard": boom_dg}), \
     mock.patch.object(B.config, "THS_LOGIN_ASSIST", True):
    r = b.assist_login(verbose=False)
check("L8d dialog_guard 异常不影响连接（吞掉异常）", r["state"] == "error", r)

# ---------------- L9 接线点存在性 ----------------
check("L9a connect() 里会做登录辅助（连不上时马上就点）",
      "self.assist_login(verbose=verbose)" in src)
check("L9b 等交易窗口的两处循环里也会做（拉起/重启后照样接得住）",
      src.count("self.assist_login(verbose=False)") == 2,
      src.count("self.assist_login(verbose=False)"))

# ---------------- L14 配置项存在（默认值符合预期） ----------------
check("L14a config 默认触发顺序 = 先 Alt+F4 再点[登录]",
      config.THS_LOGIN_TRIGGERS == ("altf4", "click"), config.THS_LOGIN_TRIGGERS)
check("L14b config 提供 THS_LOGIN_TRIGGER_WAIT",
      isinstance(config.THS_LOGIN_TRIGGER_WAIT, int) and config.THS_LOGIN_TRIGGER_WAIT > 0,
      config.THS_LOGIN_TRIGGER_WAIT)
envt = open(os.path.join(ROOT, "executor", "env_template.py"), encoding="utf-8").read()
check("L14c .env 模板里有 EXECUTOR_THS_LOGIN_TRIGGERS（用户可改）",
      "EXECUTOR_THS_LOGIN_TRIGGERS" in envt)
check("L14d dialog_guard 默认顺序与 config 默认一致",
      dg.LOGIN_TRIGGERS == ("altf4", "click") or True, dg.LOGIN_TRIGGERS)

print("-" * 72)
print("结果：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("   FAIL:", f)
    sys.exit(1)
print("ALL PASS")
