# -*- coding: utf-8 -*-
"""执行端"同花顺被拉起后假死/空白"修复的离线自测。

对应 2026-09-12 用户现场：
    "执行端拉起的同花顺窗口会假死，但能读到资金，其他空白，
     手动运行能直接自动登录全部显示正常。"

排查结论（真机日志 + 代码）：
  1) easytrader 在 connect() 里会执行 _close_prompt_windows()，把
     **所有可见 #32770 且标题 != "网上股票交易系统5.0"** 的窗口 close()
     （= SendMessage(WM_CLOSE)）。同花顺**登录窗口标题恰好是空串**，
     启动期公告/注册提示标题也常为空 -> 全被它关掉：
        * 关掉登录窗口 = 同花顺退出，随后又被拉起 -> "反复拉起关闭"；
        * 关在初始化中途 = 主窗口留着但内容空白、点了没反应 = "假死"，
          而原生子控件文本还在（所以资金仍读得到）。
     执行端日志特征：自动启动后 2.8 秒就报"已启动并恢复窗口"，紧跟两条
     标题为空的 `close window`，随后 `No windows for that process could be
     found`（进程已死）。
  2) 主框架「网上股票交易系统5.0」几秒就出现，内部工具栏/菜单树要几十秒
     才初始化完 —— "看到标题就算已启动"太早。
  3) os.startfile 让同花顺继承了执行端的工作目录（项目根），而双击/快捷
     方式给的是安装目录，两者上下文不一致。

本自测全部走 mock / 假 win32 世界，不启动同花顺、不点窗口、不下单。
用法：
    venv/Scripts/python.exe tools/test_ths_boot_fix.py
"""
import os
import sys
import types
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "executor"))
os.environ.setdefault("JQ_ENVFILE_NOCREATE", "1")   # 不要因自测去写 .env

import config                 # noqa: E402
import foreground as F        # noqa: E402
import broker_ths as B        # noqa: E402
import pywinauto_compat as PC  # noqa: E402
import dialog_guard as dg     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    flag = "  [OK]   " if cond else "  [FAIL] "
    print(flag + name + (("   <- " + str(extra)) if extra else ""))


XIADAN = r"D:\同花顺软件\同花顺\xiadan.exe"


# ---------------- 假 win32 世界（驱动 foreground） ----------------

class FakeGui(object):
    """假 win32gui：只实现 main_window_status 用到的那几个函数。"""

    def __init__(self, exists=True, title="网上股票交易系统5.0", visible=True,
                 classes=()):
        self.exists = exists
        self.title = title
        self.visible = visible
        self.classes = list(classes)      # 子控件类名

    def IsWindow(self, h):
        return bool(self.exists)

    def GetWindowText(self, h):
        return self.title

    def IsWindowVisible(self, h):
        return bool(self.visible)

    def GetClassName(self, c):
        return self.classes[int(c)]

    def EnumChildWindows(self, h, cb, extra):
        for i in range(len(self.classes)):
            if cb(i, extra) is False:
                return


class FakeCtypes(object):
    """假 ctypes：IsHungAppWindow 走 user32。flag=None 表示调用即抛异常。"""

    def __init__(self, flag=0):
        self.flag = flag
        outer = self

        class _User32(object):
            def IsHungAppWindow(self, hwnd):
                if outer.flag is None:
                    raise RuntimeError("模拟取不到")
                return outer.flag

        class _Windll(object):
            user32 = _User32()

        self.windll = _Windll()


def with_gui(gui, hung=0):
    return mock.patch.multiple(F, win32gui=gui, ctypes=FakeCtypes(hung))


# ---------------- 1) 交易窗口"真就绪"判定 ----------------

def test_ready_status():
    print("\n---- 1) 交易窗口就绪判定（foreground.main_window_status） ----")

    with with_gui(FakeGui(exists=False)):
        check("未启动/窗口不存在 -> 未见交易窗口",
              F.main_window_status(0) == "未见交易窗口（未启动/尚未创建）")
        check("句柄无效也报未见", not F.main_window_ready(12345))

    with with_gui(FakeGui(title="某个无关窗口")):
        check("标题不匹配 -> 不算就绪",
              F.main_window_status(1) == "窗口标题不匹配")

    with with_gui(FakeGui(visible=False)):
        check("托盘隐藏 -> 不算就绪",
              F.main_window_status(1) == "交易窗口不可见（缩在托盘）")

    with with_gui(FakeGui(classes=("ToolbarWindow32",)), hung=1):
        check("IsHungAppWindow=1 -> 判为假死",
              F.main_window_status(1) == "交易窗口未响应（假死）")

    with with_gui(FakeGui(classes=("Button", "Static")), hung=0):
        check("窗口在但工具栏/菜单树未出现 -> 仍在初始化",
              F.main_window_status(1) == "交易窗口仍在初始化（工具栏/菜单树未出现）")
        check("初始化中 -> main_window_ready=False", not F.main_window_ready(1))

    with with_gui(FakeGui(classes=("Static", "ToolbarWindow32")), hung=0):
        check("工具栏出现 -> ready", F.main_window_status(1) == "ready")
        check("ready 时 main_window_ready=True", F.main_window_ready(1))

    with with_gui(FakeGui(classes=("SysTreeView32",)), hung=0):
        check("只要菜单树出现也算 ready", F.main_window_ready(1))

    with with_gui(FakeGui(classes=("ToolbarWindow32",)), hung=None):
        check("IsHungAppWindow 取不到时按不假死处理（不误杀）",
              F.main_window_ready(1))


# ---------------- 2) 启动方式与"双击"对齐 ----------------

def test_launch_cwd():
    print("\n---- 2) 启动同花顺：工作目录=安装目录（与双击一致） ----")
    b = B.ThsBroker(XIADAN)
    calls = []
    # 本用例验证的是 **standalone** 启动路径（_launch 分派到 _launch_standalone）。
    # 必须显式把启动模式设为 standalone：默认 main_f12 会去启动行情主程序并发 F12，
    # 在装了同花顺的机器上就会真的动到用户的客户端（离线测试绝不允许）。
    _mode_backup = config.THS_LAUNCH_MODE
    config.THS_LAUNCH_MODE = "standalone"

    def fake_startfile(path, operation="open", arguments=None, cwd=None,
                       show_cmd=None):
        calls.append({"path": path, "cwd": cwd})

    with mock.patch.object(B.os, "startfile", fake_startfile):
        ok = b._launch()
    check("_launch 返回成功", ok is True)
    check("确实调了 os.startfile", len(calls) == 1, calls)
    check("cwd 被显式设成 exe 所在目录（消除与双击的差异）",
          calls and calls[0]["cwd"] == os.path.dirname(XIADAN), calls)
    check("启动的是配置里的 xiadan.exe", calls and calls[0]["path"] == XIADAN)

    # 老 Python（无 cwd 参数）-> 退化为默认启动，不能因此起不来
    calls2 = []

    def startfile_typeerror(path, *a, **kw):
        calls2.append(kw)
        if kw:
            raise TypeError("startfile() got an unexpected keyword 'cwd'")

    with mock.patch.object(B.os, "startfile", startfile_typeerror):
        ok2 = b._launch()
    check("startfile 不支持 cwd 时退化启动且仍算成功", ok2 is True)

    # 启动异常 -> 如实失败
    def startfile_boom(path, *a, **kw):
        raise OSError("找不到文件")

    with mock.patch.object(B.os, "startfile", startfile_boom):
        ok3 = b._launch()
    check("启动抛异常时如实返回 False", ok3 is False)
    config.THS_LAUNCH_MODE = _mode_backup


# ---------------- 3) ensure_started 不再谎报"已就绪" ----------------

def test_ensure_started():
    print("\n---- 3) ensure_started：只保证进程在跑，不谎报就绪 ----")
    old_timeout = config.THS_AUTOSTART_TIMEOUT
    config.THS_AUTOSTART_TIMEOUT = 1
    try:
        # 3a) 已就绪 -> force_foreground 恢复并报就绪
        launched, fg = [], []
        b = B.ThsBroker(XIADAN)
        b.dismiss_blocking_dialogs = lambda **kw: 0
        b.assist_login = lambda **kw: {"state": "no_login_window"}
        fg = []
        seq = {"n": 0}

        def fake_find_777(k):
            # 第 1 次调用是 ensure_started 开头的"是否已在运行"探测，必须返回 0
            #（否则会被当成已在运行、提前返回，走不到拉起分支）
            seq["n"] += 1
            return 777 if seq["n"] > 1 else 0

        with mock.patch.object(B.ThsBroker, "_launch",
                               lambda self: (launched.append(1), True)[1]), \
             mock.patch.object(B, "_process_running_by_path", lambda p: False), \
             mock.patch.object(B, "find_window_by_title", fake_find_777), \
             mock.patch.object(B, "main_window_ready", lambda h: True), \
             mock.patch.object(B, "force_foreground",
                               lambda h: (fg.append(h), True)[1]):
            ok = b.ensure_started()
        check("未运行时自动拉起并返回 True", ok is True)
        check("确实发起了启动", len(launched) == 1, launched)
        check("拉起后只在就绪时才置前", fg == [777], fg)
        check("记下就绪窗口到 self._hwnd", b._hwnd == 777)

        # 3b) 窗口出现但"未就绪" -> 仍返回 True（进程在），但绝不 force_foreground
        fg2 = []
        b2 = B.ThsBroker(XIADAN)
        b2.dismiss_blocking_dialogs = lambda **kw: 0
        b2.assist_login = lambda **kw: {}
        b2.force_foreground = lambda h: (fg2.append(h), True)[1]
        seq2 = {"n": 0}

        def fake_find_888(k):
            seq2["n"] += 1
            return 888 if seq2["n"] > 1 else 0

        with mock.patch.object(B.ThsBroker, "_launch", lambda self: True), \
             mock.patch.object(B, "_process_running_by_path", lambda p: False), \
             mock.patch.object(B, "find_window_by_title", fake_find_888), \
             mock.patch.object(B, "main_window_ready", lambda h: False), \
             mock.patch.object(B, "main_window_status",
                               lambda h: "交易窗口仍在初始化（工具栏/菜单树未出现）"):
            ok2 = b2.ensure_started()
        check("窗口在但未就绪：仍返回 True（进程确实起来了）", ok2 is True)
        check("未就绪时**不打扰**它初始化（不置前）", fg2 == [], fg2)
        check("未就绪分支也会记下窗口句柄", b2._hwnd == 888, b2._hwnd)

        # 3c) 完全没窗口 -> False
        b3 = B.ThsBroker(XIADAN)
        b3.dismiss_blocking_dialogs = lambda **kw: 0
        b3.assist_login = lambda **kw: {}
        with mock.patch.object(B.ThsBroker, "_launch", lambda self: True), \
             mock.patch.object(B, "_process_running_by_path", lambda p: False), \
             mock.patch.object(B, "find_window_by_title", lambda k: 0):
            ok3 = b3.ensure_started()
        check("拉起后始终没窗口 -> False", ok3 is False)

        # 3d) 已在运行 -> 不重复拉起
        launched2 = []
        b4 = B.ThsBroker(XIADAN)
        with mock.patch.object(
                B.ThsBroker, "_launch",
                lambda self: (launched2.append(1), True)[1]), \
             mock.patch.object(B, "_process_running_by_path", lambda p: True):
            ok4 = b4.ensure_started()
        check("已在运行时不重复拉起", ok4 is True and not launched2)
    finally:
        config.THS_AUTOSTART_TIMEOUT = old_timeout


# ---------------- 4) connect：就绪体检 + 不再提前破坏 ----------------

class FakeUser(object):
    def __init__(self):
        self.connected_with = None
        self.grid_strategy = None
        self._app = None             # easytrader connect 后会有 _app
        self._main = "TOP_WINDOW"    # 模拟 easytrader 用 top_window() 绑的（可能绑到外壳框架）
        self.toolbar_rebuilt = False

    def connect(self, path, **kw):
        self.connected_with = path

    def enable_type_keys_for_editor(self):
        pass

    def _init_toolbar(self):
        self.toolbar_rebuilt = True


class FakeEasyTrader(object):
    def __init__(self):
        self.user = FakeUser()
        self.grid_strategies = types.SimpleNamespace(Copy="COPY")

    def use(self, kind):
        self.kind = kind
        return self.user


def test_connect_gate():
    print("\n---- 4) connect：按就绪情况决定是否连接 ----")
    for status, expect_raise, expect_kw in (
            ("ready", False, None),
            ("交易窗口仍在初始化（工具栏/菜单树未出现）", False, None),
            ("交易窗口未响应（假死）", True, "假死"),
            ("交易窗口不可见（缩在托盘）", False, None)):
        b = B.ThsBroker(XIADAN)
        ft = FakeEasyTrader()
        b.ensure_started = lambda verbose=True: True
        b.dismiss_blocking_dialogs = lambda **kw: 0
        b.assist_login = lambda **kw: {}
        b.at_login_screen = lambda: False
        b._ensure_fg = lambda: True
        with mock.patch.object(B, "easytrader", ft), \
             mock.patch.object(B, "main_window_status", lambda h: status), \
             mock.patch.object(B, "find_window_by_title", lambda k: 111):
            err = None
            try:
                b.connect()
            except Exception as e:
                err = str(e)
        if expect_raise:
            check("状态=%s -> 拒绝连接并提示可重启" % status,
                  err is not None and expect_kw in err, err)
            check("状态=%s -> 未真正调用 easytrader.use" % status,
                  ft.user.connected_with is None)
        else:
            check("状态=%s -> 不阻塞，照常连接" % status, err is None, err)
            check("状态=%s -> 连接参数正确" % status,
                  ft.user.connected_with == XIADAN, ft.user.connected_with)
            check("状态=%s -> 把安装路径告知接管版关窗逻辑" % status,
                  getattr(ft.user, "_jq_xiadan_path", None) == XIADAN)

    # 还停在登录界面 -> 必须拦住（否则连接会触发关窗逻辑）
    b = B.ThsBroker(XIADAN)
    ft = FakeEasyTrader()
    b.ensure_started = lambda verbose=True: True
    b.dismiss_blocking_dialogs = lambda **kw: 0
    b.assist_login = lambda **kw: {}
    b.at_login_screen = lambda: True
    with mock.patch.object(B, "easytrader", ft):
        err = None
        try:
            b.connect()
        except Exception as e:
            err = str(e)
    check("仍停在登录界面 -> 拒绝连接", err is not None and "登录界面" in err, err)
    check("仍停在登录界面 -> 未调用 easytrader.use",
          ft.user.connected_with is None)

    # ---- 4b) connect 后把 _main 重绑到交易对话框（top_window 可能绑到外壳框架）----
    print("\n---- 4b) connect 后重绑主窗口到交易对话框 ----")

    class RebindApp(object):
        """模拟 pywinauto Application.window()：按 handle / title 返回哨兵对象"""
        def __init__(self, fail_handle=False):
            self.fail_handle = fail_handle

        def window(self, handle=None, title=None):
            if handle is not None:
                if self.fail_handle:
                    raise RuntimeError("handle 不在该进程里")
                return ("BY_HANDLE", handle)
            return ("BY_TITLE", title)

    def _mk_rebind_broker(app):
        bb = B.ThsBroker(XIADAN)
        f = FakeEasyTrader()
        f.user._app = app
        f.user._main = "TOP_WINDOW(外壳框架)"
        f.user.toolbar_rebuilt = False
        bb.ensure_started = lambda verbose=True: True
        bb.dismiss_blocking_dialogs = lambda **kw: 0
        bb.assist_login = lambda **kw: {}
        bb.at_login_screen = lambda: False
        bb._ensure_fg = lambda: True
        return bb, f

    # 4b-1) 有交易窗口句柄 -> 按句柄重绑 + 工具栏重建
    bb, f = _mk_rebind_broker(RebindApp())
    with mock.patch.object(B, "easytrader", f), \
         mock.patch.object(B, "main_window_status", lambda h: "ready"), \
         mock.patch.object(B, "find_window_by_title", lambda k: 111):
        bb.connect()
    check("top_window 绑错时 _main 被重绑到交易对话框（按句柄）",
          f.user._main == ("BY_HANDLE", 111), f.user._main)
    check("重绑后工具栏跟着重建", f.user.toolbar_rebuilt is True)

    # 4b-2) 句柄绑定失败 -> 按标题兜底（等价 easytrader login() 的绑定方式）
    bb2, f2 = _mk_rebind_broker(RebindApp(fail_handle=True))
    with mock.patch.object(B, "easytrader", f2), \
         mock.patch.object(B, "main_window_status", lambda h: "ready"), \
         mock.patch.object(B, "find_window_by_title", lambda k: 111):
        bb2.connect()
    check("句柄绑定失败时按标题兜底重绑",
          f2.user._main == ("BY_TITLE", "网上股票交易系统5.0"), f2.user._main)

    # 4b-3) 句柄+标题都失败 -> 保留原绑定且不抛异常（连接照常完成）
    class DeadApp(object):
        def window(self, handle=None, title=None):
            raise RuntimeError("not found")

    bb3, f3 = _mk_rebind_broker(DeadApp())
    with mock.patch.object(B, "easytrader", f3), \
         mock.patch.object(B, "main_window_status", lambda h: "ready"), \
         mock.patch.object(B, "find_window_by_title", lambda k: 0):
        err3 = None
        try:
            bb3.connect()
        except Exception as e:
            err3 = str(e)
    check("重绑不上也不影响连接（保留原绑定，等待自动重试）",
          err3 is None and f3.user._main == "TOP_WINDOW(外壳框架)" and
          f3.user.connected_with == XIADAN, (err3, f3.user._main))


# ---------------- 5) 核心：easytrader 的无脑关窗被接管 ----------------

class FakeWin(object):
    def __init__(self, title):
        self._title = title
        self.closed = 0

    def window_text(self):
        return self._title

    def close(self):
        self.closed += 1


class FakeApp(object):
    def __init__(self, wins):
        self._wins = wins

    def windows(self, **kw):
        return list(self._wins)


def test_prompt_window_patch():
    print("\n---- 5) easytrader 的 _close_prompt_windows 已被接管 ----")
    ok = PC.patch_easytrader_prompt_windows()
    check("补丁安装成功", ok is True)

    import easytrader.clienttrader as ct

    # 三个窗口：空标题的登录窗口、空标题的注册提示、主框架
    login = FakeWin("")
    notice = FakeWin("")
    main = FakeWin("网上股票交易系统5.0")
    trader = object.__new__(ct.ClientTrader)
    trader._app = FakeApp([login, notice, main])
    trader._config = types.SimpleNamespace(
        TITLE="网上股票交易系统5.0", DEFAULT_EXE_PATH=XIADAN)
    trader._jq_xiadan_path = XIADAN

    m_dismiss = mock.MagicMock(return_value=[])
    with mock.patch.object(dg, "dismiss_interference_dialogs", m_dismiss):
        import captcha
        with mock.patch.object(captcha, "handle_captcha",
                               lambda *a, **kw: "none"):
            trader._close_prompt_windows()
    check("**登录窗口（标题空）不再被 close**", login.closed == 0, login.closed)
    check("**注册提示（标题空）不再被 close**", notice.closed == 0, notice.closed)
    check("主框架本来就不会被关", main.closed == 0, main.closed)
    check("改为走白名单弹窗处理（dialog_guard）", m_dismiss.called)
    check("白名单处理使用了同花顺安装路径",
          m_dismiss.call_args[0][:1] == (XIADAN,)
          or m_dismiss.call_args[0] == (XIADAN,), m_dismiss.call_args)

    # 日志里要能看出"原来会关掉几个、现在不关了"。
    # 注意：不能 mock logging.getLogger —— 补丁安装时 _log 已经绑定成
    # getLogger("executor") 的返回值，事后再 mock 拿不到调用。改挂真 handler。
    import logging as _logging
    captured = []

    class _Capture(_logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    lg = _logging.getLogger("executor")
    handler = _Capture(level=_logging.INFO)
    lg.addHandler(handler)
    old_level = lg.level
    lg.setLevel(_logging.INFO)   # 测试进程里 NOTSET -> 生效 WARNING，info 会被过滤
    try:
        import captcha
        with mock.patch.object(captcha, "handle_captcha", lambda *a, **kw: "none"):
            trader._close_prompt_windows()
    finally:
        lg.removeHandler(handler)
        lg.setLevel(old_level)
    check("不再关窗这件事会留日志（可事后复盘）",
          any("WM_CLOSE" in m for m in captured), captured)


# ---------------- 6) 回归：用户报的两个现场都覆盖 ----------------

def test_regression_scenarios():
    print("\n---- 6) 回归：用户现场 ----")
    # 现场A：执行端日志里"自动启动后 2.8s 就报已启动" —— 现在不再谎报就绪
    src = open(os.path.join(ROOT, "executor", "broker_ths.py"), encoding="utf-8").read()
    check("broker_ths 不再出现误导性的'已自动启动并恢复窗口'文案",
          "已自动启动并恢复窗口" not in src)
    check("broker_ths 启动时显式指定工作目录（os.startfile(path, cwd=)）",
          "os.startfile(path, cwd=workdir)" in src)
    check("broker_ths 连接前做就绪体检（main_window_status）",
          "main_window_status(find_window_by_title" in src)
    check("broker_ths 安装了'接管无关窗'补丁",
          "patch_easytrader_prompt_windows()" in src)

    # 现场B：假死 -> 提示走"重启同花顺"自救
    b = B.ThsBroker(XIADAN)
    ft = FakeEasyTrader()
    b.ensure_started = lambda verbose=True: True
    b.dismiss_blocking_dialogs = lambda **kw: 0
    b.assist_login = lambda **kw: {}
    b.at_login_screen = lambda: False
    with mock.patch.object(B, "easytrader", ft), \
         mock.patch.object(B, "main_window_status",
                           lambda h: "交易窗口未响应（假死）"), \
         mock.patch.object(B, "find_window_by_title", lambda k: 222):
        err = ""
        try:
            b.connect()
        except Exception as e:
            err = str(e)
    check("假死时给出可照做的自救指引（点「重启同花顺」）",
          "重启同花顺" in err, err)


def main():
    print("=" * 74)
    print("执行端「同花顺假死/空白」修复 · 离线自测")
    print("=" * 74)
    test_ready_status()
    test_launch_cwd()
    test_ensure_started()
    test_connect_gate()
    test_prompt_window_patch()
    test_regression_scenarios()
    print("\n" + "=" * 74)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败清单:")
        for f in FAIL:
            print("  - " + f)
    print("=" * 74)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
