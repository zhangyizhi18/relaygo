# -*- coding: utf-8 -*-
"""
同花顺「登录阶段」的无人值守辅助（非侵入式独立模块）。

覆盖两个真机实测（2026-09-12）的拦路虎，都是**纯 UI 层、与交易无关**，
且都出现在 easytrader 连接成功**之前** —— 那会儿还没有 self.user/_main，
所以不能用 captcha.py / close_captcha_dialog 那套（它们依赖已连接会话）：

  1) 干扰弹窗：登录界面先弹一个模态的"注册提示"
       正文：尊敬的用户您好，系统检测到您当前还不是同花顺注册用户，无法使用
             同花顺交易，您需要注册同花顺账号才可以正常使用交易。
       按钮：[立即注册] [取消]
     不点掉它，后面的一切都动不了（执行端表现为"拉起了同花顺却被挡住"）。
     人工点一下[取消]立刻正常。-> dismiss_interference_dialogs()

  2) 登录界面等待人工点[登录]：同花顺拉起后停在登录窗口（标题为空字符串，
     内有"站点列表:/交易密码:/[登录]"），实测**它自己不会自动登录**，
     会一直干等（140 秒无动静）；而交易密码已由同花顺自己保存并填好，
     替人点一下[登录] 立刻登录成功、主框架"网上股票交易系统5.0"变可见。
     -> assist_login()

  3) Alt+F4（2026-09-12 用户反馈："这个界面按下 alt+f4 就能自动登录，不用点[登录]"）：
     真机实测它是"窗口关闭键"，按到哪个窗口结果完全不同，所以**必须先把目标窗口
     置前并校验前台确实是它**，否则宁可不发：
       * 落在同花顺主框架「网上股票交易系统5.0」上 -> 整个同花顺被关掉（进程消失），
         接着又被执行端拉起 -> 就是"反复拉起关闭"；
       * 落在「注册提示」这类弹窗上 -> 等价于点[取消]，弹窗关掉（用户就是靠这个过去的）；
       * 落在登录窗口上 -> 该版本忽略，无副作用。
     因此本模块只对**已命中白名单规则的弹窗**和**已识别的登录窗口**发 Alt+F4，
     且作为"点不动时的备选路线"（见 _alt_f4 / _TRIGGER_STATE）。
     一旦某次 Alt+F4 造成"目标窗口没了、程序也没了"，就永久停用这条路线，
     避免"关掉->拉起->再关掉"的死循环。

安全原则（无人值守环境下的"宁可不动手，也不乱动手"）：
  * 纯 win32 枚举；只处理**同花顺安装目录下进程**的窗口，绝不碰别的程序。
  * 按【内容】判定，不按标题 —— 同花顺弹窗标题常是空串。
  * 白名单规则：只有规则里明确列出的安全按钮才点（绝不点"立即注册/确定/是"）。
  * 真实鼠标点击前用 WindowFromPoint 确认坐标上就是目标按钮，否则放弃点击
    （防止窗口被遮挡时把鼠标点到别人的界面上）。
  * 自动点[登录] 有克制的三道闸：先等 idle_seconds 让同花顺自己有机会登录；
    同一窗口最多试 max_attempts 次且两次间隔 ≥ min_interval 秒；一旦同花顺
    抱怨（请输入交易密码/密码错误/验证码/动态口令…）立即停手不再试 —— 那是
    需要人工介入的信号，绝不硬撞（会白耗券商登录次数）。

命令行自检（现场排查用）：
    python dialog_guard.py            # 只读扫描：同花顺有哪些弹窗 / 是否停在登录界面
    python dialog_guard.py --dismiss  # 扫描并点掉命中的干扰弹窗
    python dialog_guard.py --login    # 只做一次"登录辅助"判断（按闸门规则）
"""
import logging
import os
import re
import time

log = logging.getLogger("executor")

BM_CLICK = 0x00F5

# ---- 干扰弹窗规则表（白名单）----
#   match   : 命中任一关键字即认为是该弹窗（正文/按钮文本里出现即可）
#   buttons : 允许点击的按钮文本，按优先级排列（只点这里列出的）
RULES = (
    {
        "name": "注册提示",
        "hint": "同花顺「非注册用户」提示（不点掉会挡住登录界面）",
        "match": ("还不是同花顺注册用户", "无法使用同花顺交易", "立即注册"),
        "buttons": ("取消", "关闭", "否"),
    },
)

# ---- 登录窗口识别 / 登录辅助 ----
LOGIN_TITLE_MARKERS = ("站点列表", "交易密码")   # 登录窗口特征（标题为空，只能按内容认）
LOGIN_BUTTON = "登录"                          # 替人点的那个按钮
# 同花顺一旦说这些话，说明需要人工（密码没了/要验证码），立即停手不再尝试
LOGIN_STOP_MARKERS = ("请输入交易密码", "密码错误", "登录失败", "账号或密码",
                      "请输入验证码", "验证码错误", "动态口令", "短信验证码",
                      "口令矩阵")

# 去掉按钮文本里的快捷键后缀，如 "取消(&C)" / "取消(C)" -> "取消"
_ACCEL_RE = re.compile(r"[\(（]\s*&?[A-Za-z0-9]\s*[\)）]")

# 登录辅助状态：登录窗口 hwnd -> {"first": 首次看到的时间, "clicks": [], "stopped": 原因}
_LOGIN_STATE = {}

# 触发方式状态。altf4_app_exit：Alt+F4 曾经把同花顺整个关掉过 -> 永久停用这条路
# 线（否则"关掉->被拉起->再关掉"，就是用户见过的"反复拉起关闭"）。
_TRIGGER_STATE = {"altf4_app_exit": False}

# 登录触发顺序（可在 .env 用 EXECUTOR_THS_LOGIN_TRIGGERS 覆盖）：
#   altf4 = 对登录窗口按 Alt+F4（用户反馈的方式；实测部分版本忽略）
#   click = 点[登录]（实测有效）
# 先 altf4 再 click：两者都试过才报"没生效"，任一成功即返回。
LOGIN_TRIGGERS = ("altf4", "click")
LOGIN_TRIGGER_WAIT = 8        # 每次触发后最多等几秒看登录窗口是否消失


def _norm_text(t):
    t = (t or "").strip()
    t = _ACCEL_RE.sub("", t)
    return t.replace("&", "").strip()


def reset_login_state():
    """清空登录辅助状态（同花顺被重启/换窗口后调用）。主要给测试用。"""
    _LOGIN_STATE.clear()
    _TRIGGER_STATE["altf4_app_exit"] = False


def altf4_disabled():
    """Alt+F4 路线是否已被永久停用（曾把同花顺关掉过）。"""
    return bool(_TRIGGER_STATE["altf4_app_exit"])


def _exe_of(pid):
    """返回进程 EXE 完整路径（失败返回空串）。"""
    try:
        import win32api
        import win32process
        h = win32api.OpenProcess(0x0400 | 0x0010, False, pid)   # QUERY_INFORMATION|VM_READ
        try:
            return win32process.GetModuleFileNameEx(h, 0)
        finally:
            win32api.CloseHandle(h)
    except Exception:
        return ""


def _allowed_roots(xiadan_path):
    """允许操作的 EXE 目录（同花顺安装目录）。

    传 xiadan_path（...\\同花顺\\xiadan.exe）时取它的目录做前缀匹配，这样
    xiadan.exe、hexinhelper.exe 等同一安装目录下的进程都覆盖到；
    路径为空则返回 None 表示"不做目录过滤"（仅用于离线测试/诊断）。
    """
    if not xiadan_path:
        return None
    return os.path.normcase(os.path.dirname(os.path.abspath(xiadan_path)))


def _child_snapshot(hwnd):
    """返回 (全部文本列表, [按钮(hwnd, 文本)] )，枚举所有后代控件。"""
    import win32gui
    texts, buttons = [], []

    def _cb(c, _):
        try:
            t = win32gui.GetWindowText(c)
            cls = win32gui.GetClassName(c)
        except Exception:
            return True
        if t:
            texts.append(t.strip())
            if cls.lower() == "button":
                nt = _norm_text(t)
                if nt:
                    buttons.append((c, nt))
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        pass
    return texts, buttons


def _collect(xiadan_path=None, include_hidden=False):
    """枚举"同花顺自己的"顶层窗口，带回文本与按钮。

    每项：{hwnd, pid, exe, class_name, title, texts, buttons}
    """
    import win32gui
    import win32process

    root = _allowed_roots(xiadan_path)
    exe_cache = {}
    out = []

    def _cb(h, _):
        try:
            pid = win32process.GetWindowThreadProcessId(h)[1]
            if not pid:
                return True
            if pid not in exe_cache:
                exe_cache[pid] = _exe_of(pid)
            exe = exe_cache[pid]
            if not exe:
                return True
            if root is not None and not os.path.normcase(exe).startswith(root):
                return True                       # 不是同花顺的窗口，绝不碰
            if not include_hidden and not win32gui.IsWindowVisible(h):
                return True
            texts, buttons = _child_snapshot(h)
            out.append({
                "hwnd": h, "pid": pid, "exe": exe,
                "class_name": win32gui.GetClassName(h),
                "title": win32gui.GetWindowText(h),
                "texts": texts, "buttons": buttons,
            })
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return out


# ---------------- 1) 干扰弹窗 ----------------

def _match_rule(title, texts, buttons):
    """按内容匹配规则；返回 (rule, 目标按钮(hwnd,text)) 或 (None, None)。"""
    blob = " | ".join([title or ""] + list(texts) + [b[1] for b in buttons])
    for rule in RULES:
        if not any(k in blob for k in rule["match"]):
            continue
        for want in rule["buttons"]:
            for h, t in buttons:
                if t == want:
                    return rule, (h, t)
    return None, None


def find_interference_dialogs(xiadan_path=None, include_hidden=False):
    """只读扫描：返回同花顺进程中命中干扰规则的弹窗列表（不点击、无副作用）。

    每项：{hwnd, pid, exe, title, class_name, texts, button_hwnd, button,
            rule, hint}
    """
    found = []
    for w in _collect(xiadan_path, include_hidden):
        rule, btn = _match_rule(w["title"], w["texts"], w["buttons"])
        if rule is None:
            continue
        found.append({
            "hwnd": w["hwnd"], "pid": w["pid"], "exe": w["exe"],
            "title": w["title"], "class_name": w["class_name"],
            "texts": w["texts"],
            "button_hwnd": btn[0], "button": btn[1],
            "rule": rule["name"], "hint": rule["hint"],
        })
    return found


def _still_open(hwnd):
    try:
        import win32gui
        return bool(win32gui.IsWindow(hwnd)) and bool(win32gui.IsWindowVisible(hwnd))
    except Exception:
        return False


def _point_hits_button(btn_hwnd, pt):
    """坐标 pt 处的窗口是不是目标按钮（或其后代）。拿不到信息时按"是"处理。

    这是**无人值守下的安全阀**：真实鼠标点击会移动光标，如果弹窗被别的窗口
    挡住（比如中转控制台浮在最上层），盲点下去就点到别的程序上了。所以点之前
    先用 WindowFromPoint 确认该坐标确实属于目标按钮，否则宁可不点。
    """
    try:
        import win32gui
        cur = win32gui.WindowFromPoint(pt)
    except Exception:
        return True
    for _ in range(8):
        if not cur:
            return False
        if cur == btn_hwnd:
            return True
        try:
            cur = win32gui.GetParent(cur)
        except Exception:
            return False
    return False


def _click_button(btn_hwnd, dlg_hwnd):
    """点按钮。返回：
        'bm_click'  —— 标准消息点击成功
        'mouse'     —— 真实鼠标点击成功
        'blocked'   —— 按钮坐标上不是它（被别的窗口挡着），为安全放弃点击
        'uncertain' —— 两条路都点了但弹窗没消失
        'failed:..' —— 点击过程抛异常
    """
    import win32api
    import win32con
    import win32gui

    # 1) 标准 BM_CLICK：最轻，不移动鼠标、不需要窗口在前台
    #    （实测同花顺登录窗口的[登录]按钮 BM_CLICK 就有效）
    try:
        win32gui.SendMessage(btn_hwnd, BM_CLICK, 0, 0)
        time.sleep(0.5)
        if not _still_open(dlg_hwnd):
            return "bm_click"
    except Exception:
        pass

    # 2) 先把窗口带到前台，再取按钮中心
    try:
        from foreground import force_foreground
        force_foreground(dlg_hwnd)
        time.sleep(0.25)
    except Exception:
        pass
    try:
        left, top, right, bottom = win32gui.GetWindowRect(btn_hwnd)
    except Exception as e:
        # 按钮句柄取不到，常见原因是"上一步的点击其实已经生效、对话框正在销毁"
        # （同花顺登录成功后就是这样，真机 2026-09-12）。此时如实报成功。
        if not _still_open(dlg_hwnd):
            return "bm_click"
        return "failed: %s" % e
    cx, cy = (left + right) // 2, (top + bottom) // 2

    # 3) 安全阀：坐标上必须真是这个按钮，否则绝不盲点
    if not _point_hits_button(btn_hwnd, (cx, cy)):
        return "blocked"

    # 4) 真实鼠标点击（BM_CLICK 对部分同花顺按钮不生效，必须备这条路）
    try:
        win32api.SetCursorPos((cx, cy))
        time.sleep(0.1)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.5)
        return "mouse" if not _still_open(dlg_hwnd) else "uncertain"
    except Exception as e:
        return "failed: %s" % e


def _proc_alive(xiadan_path):
    """同花顺安装目录下是否还有进程在跑（win32 枚举，不依赖 psutil/wmic）。

    返回 True/False；探测不出来时返回 True（保守按"活着"处理，
    绝不因为探测失败就误判成"程序被关掉了"）。
    """
    root = _allowed_roots(xiadan_path)
    if root is None:
        return True
    try:
        import win32process
        for pid in win32process.EnumProcesses():
            p = _exe_of(pid)
            if p and os.path.normcase(p).startswith(root):
                return True
    except Exception:
        return True
    return False


def _alt_f4(hwnd):
    """给 hwnd 发 Alt+F4（真实键盘）。返回：
        'alt_f4'   —— 已发出（效果由调用方检查窗口是否消失）
        'blocked'  —— 目标窗口没能拿到前台，**为安全不发键**
        'failed:..'—— 发送过程抛异常

    为什么必须有 'blocked' 这道闸：Alt+F4 由"当前前台窗口"处理，盲发会打到
    别的窗口上 —— 实测打在同花顺主框架上会把整个同花顺关掉（进程消失），
    表现就是"反复拉起关闭"。所以先 force_foreground 再校验前台确实是它。
    """
    import win32api
    import win32con
    import win32gui

    try:
        try:
            from foreground import force_foreground
            force_foreground(hwnd)
        except Exception:
            pass
        time.sleep(0.3)
        if win32gui.GetForegroundWindow() != hwnd:
            return "blocked"
        VK_MENU, VK_F4 = 0x12, 0x73
        up = win32con.KEYEVENTF_KEYUP
        win32api.keybd_event(VK_MENU, 0, 0, 0)
        time.sleep(0.08)
        win32api.keybd_event(VK_F4, 0, 0, 0)
        time.sleep(0.08)
        win32api.keybd_event(VK_F4, 0, up, 0)
        time.sleep(0.08)
        win32api.keybd_event(VK_MENU, 0, up, 0)
        return "alt_f4"
    except Exception as e:
        return "failed: %s" % e


def dismiss_interference_dialogs(xiadan_path=None, max_rounds=3, verbose=True):
    """检测并点掉干扰弹窗，返回本次处理记录（空列表 = 没有弹窗、什么都没做）。

    幂等且安全：没有命中弹窗时不会点击任何东西；点不掉的弹窗最多试
    max_rounds 轮后放弃并如实记录（绝不误点"确定/立即注册"）。
    """
    records = []
    stuck = set()
    for _ in range(max(1, max_rounds)):
        targets = find_interference_dialogs(xiadan_path)
        if not targets:
            break
        progressed = False
        for d in targets:
            if d["hwnd"] in stuck:
                continue
            action = _click_button(d["button_hwnd"], d["hwnd"])
            if action not in ("bm_click", "mouse"):
                # 按钮点不动 / 被别的窗口挡住时，退化用"用户教的 Alt+F4"。
                # 只对**本模块已经命中白名单规则**的弹窗发，且必须先拿到前台。
                alt = _alt_f4(d["hwnd"])
                if alt == "alt_f4" and not _still_open(d["hwnd"]):
                    action = "alt_f4"
                elif alt == "alt_f4" and action != "blocked":
                    action = "uncertain"       # 发了但弹窗没消失
                elif alt == "blocked" and action == "uncertain":
                    action = "blocked_failed"  # 点也没反应、Alt+F4 又拿不到前台
            records.append({
                "hwnd": d["hwnd"], "pid": d["pid"], "title": d["title"],
                "rule": d["rule"], "button": d["button"], "action": action,
                "text": (d["texts"][0] if d["texts"] else d["title"]),
            })
            if action in ("bm_click", "mouse", "alt_f4"):
                progressed = True
                if verbose:
                    if action == "alt_f4":
                        log.warning("已自动关闭同花顺弹窗[%s]（%s）：点[%s] 无效，"
                                    "改用 Alt+F4 关闭成功", d["rule"], d["hint"],
                                    d["button"])
                    else:
                        log.warning("已自动关闭同花顺弹窗[%s]（%s）：点[%s] 成功",
                                    d["rule"], d["hint"], d["button"])
            else:
                stuck.add(d["hwnd"])
                if verbose:
                    if action == "blocked":
                        log.warning("同花顺弹窗[%s] 被其它窗口挡住，为安全不盲点[%s]"
                                    "（可点任务栏/关掉遮挡窗口后重试）",
                                    d["rule"], d["button"])
                    elif action == "blocked_failed":
                        log.warning("同花顺弹窗[%s] 点[%s] 无反应、Alt+F4 又没拿到前台，"
                                    "为安全不盲发按键（请把同花顺切到前台）",
                                    d["rule"], d["button"])
                    else:
                        log.warning("同花顺弹窗[%s] 点[%s] 与 Alt+F4 均未生效（%s），"
                                    "不再重复尝试", d["rule"], d["button"], action)
        if not progressed:
            break
        time.sleep(0.4)
    return records


# ---------------- 2) 登录界面辅助 ----------------

def find_login_window(xiadan_path=None):
    """返回仍在等人工登录的同花顺登录窗口（标题为空、含"站点列表:/交易密码:/[登录]"）。

    找不到返回 None。只读，无副作用。
    """
    for w in _collect(xiadan_path):
        if LOGIN_BUTTON not in [b[1] for b in w["buttons"]]:
            continue
        if not any(any(m in t for m in LOGIN_TITLE_MARKERS) for t in w["texts"]):
            continue
        btn = [b for b in w["buttons"] if b[1] == LOGIN_BUTTON]
        w["button_hwnd"], w["button"] = btn[0]
        return w
    return None


def _login_stop_reason(xiadan_path, login_hwnd):
    """同花顺是否在"抱怨"（要密码/验证码）——只在**别的**弹窗里找，避免把
    登录窗口自身其它标签页的"验证码/动态口令"字样误判成抱怨。"""
    for w in _collect(xiadan_path):
        if w["hwnd"] == login_hwnd:
            continue
        blob = " | ".join([w["title"] or ""] + w["texts"])
        for m in LOGIN_STOP_MARKERS:
            if m in blob:
                return m
    return ""


def assist_login(xiadan_path=None, idle_seconds=20, max_attempts=3,
                 min_interval=30, verbose=True, triggers=None, trigger_wait=None):
    """替人把登录提交掉（点[登录]，或按 Alt+F4），同花顺自己不会自动登录时。

    三道闸（都为了"绝不给券商白撞登录次数"）：
      1) 登录窗口出现后先等 idle_seconds 秒，给同花顺自己的自动登录机会；
      2) 同一登录窗口最多尝试 max_attempts 轮（每轮把各触发方式依次试一遍），
         两轮间隔 ≥ min_interval 秒；
      3) 同花顺一旦抱怨（请输入交易密码/密码错误/验证码/动态口令…）立即停手。

    触发方式 triggers（默认 LOGIN_TRIGGERS = 先 Alt+F4 再点[登录]）：
      * altf4 —— 用户反馈"登录界面按 Alt+F4 就能自动登录"；实测有版本会忽略，
                 所以必须"发了之后看登录窗口消没消失"，不消失就换下一种；
      * click —— 实测有效的点[登录]。
    两种都试过仍不见效才如实报"没生效"（绝不假成功）。

    返回 {"state": ..., "message": ..., "attempts": n}，state 取值：
        no_login_window 没有登录窗口（已登录/未启动）
        waiting         还在宽限期内，先不动手
        clicked         已触发过登录（等结果）
        logged_in       登录窗口消失且程序还在（登录成功或用户自己处理了）
        app_exited      触发登录时同花顺被关掉了（Alt+F4 落错窗口的后果）
        stopped         同花顺在抱怨，需人工（不再尝试）
        give_up         已尝试 max_attempts 次仍未成功
    """
    order = tuple(t for t in (triggers if triggers is not None else LOGIN_TRIGGERS) if t)
    if trigger_wait is None:
        trigger_wait = LOGIN_TRIGGER_WAIT

    win = find_login_window(xiadan_path)
    if win is None:
        if _LOGIN_STATE:
            _LOGIN_STATE.clear()          # 登录窗口没了 -> 状态归零
        return {"state": "no_login_window", "message": "未见登录窗口", "attempts": 0}
    if _LOGIN_STATE and win["hwnd"] not in _LOGIN_STATE:
        _LOGIN_STATE.clear()              # 换了个登录窗口（重启过）-> 重新计时

    st = _LOGIN_STATE.setdefault(win["hwnd"],
                                 {"first": time.time(), "clicks": [], "stopped": ""})
    if st["stopped"]:
        return {"state": "stopped", "message": "同花顺在等人工处理（%s）" % st["stopped"],
                "attempts": len(st["clicks"])}

    stop = _login_stop_reason(xiadan_path, win["hwnd"])
    if stop:
        st["stopped"] = stop
        # 同"点[登录]"一样不看 verbose：停手是需要人工介入的信号，必须留痕
        log.warning("同花顺登录需要人工介入（检测到「%s」），已停止自动登录辅助", stop)
        return {"state": "stopped", "message": "需人工：%s" % stop,
                "attempts": len(st["clicks"])}

    now = time.time()
    if now - st["first"] < idle_seconds:
        return {"state": "waiting",
                "message": "登录界面出现 %d 秒，先等同花顺自己登录"
                           % int(now - st["first"]),
                "attempts": len(st["clicks"])}

    if len(st["clicks"]) >= max_attempts:
        log.warning("已尝试 %d 次仍未见登录成功，不再尝试；"
                    "请人工检查同花顺登录界面（可能需要输入交易密码/验证码）",
                    len(st["clicks"]))
        st["stopped"] = "多次尝试未成功"
        return {"state": "give_up", "message": "已尝试 %d 次仍未成功"
                % len(st["clicks"]), "attempts": len(st["clicks"])}

    if st["clicks"] and now - st["clicks"][-1] < min_interval:
        return {"state": "waiting", "message": "距上次尝试登录不足 %d 秒" % min_interval,
                "attempts": len(st["clicks"])}

    # ---- 依次尝试各触发方式，任一生效即返回 ----
    todo = [t for t in order if t != "altf4" or not altf4_disabled()]
    if not todo:
        todo = ["click"]          # Alt+F4 已被停用且没配别的 -> 退回点[登录]
    tried = []
    st["clicks"].append(time.time())      # 一次"尝试"= 把下面所有触发方式轮一遍
    for trig in todo:
        if trig == "altf4":
            action = _alt_f4(win["hwnd"])
            how = "替人按 Alt+F4"
            note = ""
        else:
            action = _click_button(win["button_hwnd"], win["hwnd"])
            how = "替人点[登录]"
            note = "—— 交易密码由同花顺自己保存填写，无需执行端保管密码"
        # 这一条**不看 verbose**：动了登录界面的动作必须留在日志里可供事后复盘
        # （重试循环里 verbose=False，但事件本身不能静默）。
        log.warning("同花顺停在登录界面已 %d 秒，%s（第 %d 次，%s）%s",
                    int(now - st["first"]), how, len(st["clicks"]), action, note)
        tried.append("%s->%s" % (trig, action))
        if _still_open(win["hwnd"]):
            if action == "blocked" or str(action).startswith("failed"):
                continue          # 压根没按下去（如没拿到前台）-> 直接换下一种
            deadline = time.time() + max(1, trigger_wait)
            while time.time() < deadline:
                if not _still_open(win["hwnd"]):
                    break
                time.sleep(0.5)
        if not _still_open(win["hwnd"]):
            if _proc_alive(xiadan_path):
                return {"state": "logged_in",
                        "message": "已触发登录并生效（%s）" % ", ".join(tried),
                        "attempts": len(st["clicks"])}
            # 触发登录反而把同花顺整个关掉了（Alt+F4 落到主框架上就是这个后果）
            if trig == "altf4":
                _TRIGGER_STATE["altf4_app_exit"] = True
            st["stopped"] = "触发登录时程序被关闭"
            log.warning("触发登录时同花顺被关闭（%s）—— 已停用该方式；"
                        "程序会被自动重新拉起后改用点[登录]", ", ".join(tried))
            return {"state": "app_exited",
                    "message": "触发登录时同花顺被关闭（%s）" % ", ".join(tried),
                    "attempts": len(st["clicks"])}
    return {"state": "clicked", "message": "已尝试 %s，登录窗口仍在" % "、".join(tried),
            "attempts": len(st["clicks"])}


# ---------------- 命令行自检 ----------------

def _main():
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import config
        path = config.THS_XIADAN_PATH
    except Exception:
        path = ""

    print("同花顺路径: %s" % path)
    print("登录触发顺序: %s（Alt+F4 已停用=%s）"
          % (", ".join(LOGIN_TRIGGERS), altf4_disabled()))
    found = find_interference_dialogs(path)
    print("命中干扰规则的弹窗: %d 个" % len(found))
    for d in found:
        print("  hwnd=%s pid=%s title=%r class=%s -> 规则[%s] 按钮[%s]"
              % (d["hwnd"], d["pid"], d["title"], d["class_name"],
                 d["rule"], d["button"]))
        print("      文本: %s" % " | ".join(d["texts"])[:300])

    win = find_login_window(path)
    if win is None:
        print("登录窗口: 无（已登录或未启动）")
    else:
        print("登录窗口: hwnd=%s 按钮=[%s] 文本=%s"
              % (win["hwnd"], win["button"], " | ".join(win["texts"])[:200]))
        print("  抱怨检查: %r" % _login_stop_reason(path, win["hwnd"]))

    if "--dismiss" in sys.argv:
        for r in dismiss_interference_dialogs(path):
            print("  处理: [%s] 点[%s] -> %s" % (r["rule"], r["button"], r["action"]))
    elif "--login" in sys.argv:
        print("登录辅助:", assist_login(path, idle_seconds=0))
    else:
        print("（只读扫描，未点击。要处理请加 --dismiss / --login）")


if __name__ == "__main__":
    _main()
