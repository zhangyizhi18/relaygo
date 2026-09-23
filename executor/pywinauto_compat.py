# -*- coding: utf-8 -*-
"""
pywinauto 0.6.6 -> 0.6.8 兼容垫片。

背景：easytrader 0.23.x 强制依赖 pywinauto==0.6.6，但 0.6.6 在 64 位 Python 上
     有 SendInput 结构体对齐 bug（报 SendInput() inserted only 0 out of 2），
     0.6.8 已修复；但 0.6.8 移除了 win32functions 里的 SetForegroundWindow/ShowWindow，
     easytrader 的 grid_strategies 导入会报 ImportError。
用法：必须在 import easytrader 之前 import 本模块。
"""
import json
import os
import time

import win32gui


def _ensure_handle(w):
    """兼容传入句柄(int)或 pywinauto wrapper 对象两种情况。"""
    return getattr(w, "handle", w)


def _patched_set_foreground_window(w):
    return win32gui.SetForegroundWindow(_ensure_handle(w))


def _patched_show_window(w, cmd):
    return win32gui.ShowWindow(_ensure_handle(w), cmd)


def find_button(main, control_id, text=None):
    """在 main 下按 control_id 精确查找 Button（必须在 Python 侧手工过滤）。

    坑（2026-09-11/12 实测）：pywinauto 0.6.8 的
    `child_window(control_id=..)` / `descendants(control_id=..)` 会**静默忽略**
    control_id 过滤条件（返回该类全部实例），所以
    `main.child_window(control_id=1007, class_name="Button")` 完全可能拿到一个
    无关按钮（点错按钮的隐患）。这里一律枚举 Button 后按
    element_info.control_id 手工比对。

    text 给定时优先返回窗口文本包含该串的实例（如 [重填] 传 "重填"），
    找不到文本匹配就退回"第一个可见实例"，最后才用无可见性信息的第一项。

    返回 pywinauto wrapper 或 None（main 为空/枚举失败时）。
    """
    if main is None:
        return None
    cands = []
    try:
        for b in main.descendants(class_name="Button"):
            try:
                if int(getattr(b.element_info, "control_id", -1)) != int(control_id):
                    continue
            except (TypeError, ValueError):
                continue
            cands.append(b)
    except Exception:
        return None
    if not cands:
        return None
    if text:
        for b in cands:
            try:
                if text in (b.window_text() or ""):
                    return b
            except Exception:
                continue
    for b in cands:
        try:
            if b.is_visible():
                return b
        except Exception:
            continue
    return cands[0]


# ---------------- 看门狗上报助手（2026-09-13） ----------------
# 只做"上报"，不做任何决策；看门狗的阈值/重启在 ths_watchdog + main 里。
# 任何异常都吞掉：上报失败绝不能影响下单主流程。

def _watchdog_report(kind, detail=""):
    """上报一次界面级异常（填不出名称/价格、控件找不到等）。"""
    try:
        import ths_watchdog
        ths_watchdog.watchdog.report_ui_error(kind, detail)
    except Exception:
        pass


def _watchdog_ok():
    """上报一次"界面正常"（清零连续计数）。"""
    try:
        import ths_watchdog
        ths_watchdog.watchdog.report_ui_ok()
    except Exception:
        pass


def _ui_not_ready_error(msg):
    """构造 UiNotReadyError；ths_watchdog 缺失时退化为 RuntimeError（不影响抛出）。"""
    try:
        from ths_watchdog import UiNotReadyError
        return UiNotReadyError(msg)
    except Exception:
        return RuntimeError(msg)


def apply():
    import pywinauto.win32functions as _wf

    if not hasattr(_wf, "SetForegroundWindow"):
        _wf.SetForegroundWindow = _patched_set_foreground_window
    if not hasattr(_wf, "ShowWindow"):
        _wf.ShowWindow = _patched_show_window

    # ---- SendInput 逐事件补丁 ----
    # 本机存在输入过滤：批量 SendInput（按下+抬起 2 事件合并发送）会被静默拒绝
    # （返回 0、GetLastError=0），单事件发送正常。pywinauto 默认批量发送，
    # 因此这里把 >1 个事件的批量调用拆成逐个发送，对上层完全透明。
    if not getattr(_wf, "_single_event_patched", False):
        import ctypes
        from pywinauto import win32structures

        _orig_SendInput = _wf.SendInput

        def _SendInput_oneshot(cInputs, pInputs, cbSize):
            if cInputs <= 1:
                return _orig_SendInput(cInputs, pInputs, cbSize)
            arr = ctypes.cast(pInputs, ctypes.POINTER(win32structures.INPUT))
            total = 0
            for i in range(cInputs):
                n = _orig_SendInput(1, ctypes.byref(arr[i]), cbSize)
                if n != 1:
                    break
                total += 1
            return total

        _wf.SendInput = _SendInput_oneshot
        _wf._single_event_patched = True

    # ---- SetForegroundWindow 前台锁补丁 ----
    # easytrader 内部（如切"当日委托"菜单时 tree_ctrl.set_focus）会直接调
    # win32gui.SetForegroundWindow，绕过 foreground.py 的置前流程，被 Windows
    # 前台锁拒绝时报 (0, 'SetForegroundWindow', ...)。这里给 win32gui 模块级
    # 打补丁：最小化先还原 -> 失败用 ALT 键解锁后重试（与 foreground.py 同思路）。
    if not getattr(win32gui, "_sfw_patched", False):
        import ctypes
        import pywintypes

        _orig_sfw = win32gui.SetForegroundWindow

        def _safe_sfw(hwnd):
            hwnd = _ensure_handle(hwnd)
            try:
                return _orig_sfw(hwnd)
            except pywintypes.error:
                try:
                    if ctypes.windll.user32.IsIconic(hwnd):
                        win32gui.ShowWindow(hwnd, 9)   # SW_RESTORE
                        time.sleep(0.2)
                except Exception:
                    pass
                user32 = ctypes.windll.user32
                user32.keybd_event(0x12, 0, 0, 0)      # ALT down（取得前台权限）
                try:
                    return _orig_sfw(hwnd)
                finally:
                    user32.keybd_event(0x12, 0, 2, 0)  # ALT up

        win32gui.SetForegroundWindow = _safe_sfw
        win32gui._sfw_patched = True


def patch_easytrader_captcha():
    """
    把 easytrader 内置的 pytesseract 验证码识别替换为 ddddocr。
    背景：easytrader 的剪贴板读表格遇到验证码弹窗时会走 captcha_recognize（基于
    pytesseract，需要另装 Tesseract 程序）；本机没装时直接报 ModuleNotFoundError，
    连持仓/资金查询都会失败。补丁后与 executor/captcha.py 共用同一个 ddddocr 实例。
    必须在 import easytrader 之后调用。
    """
    try:
        import easytrader.grid_strategies as _gs
        from captcha import _get_ocr
        ocr = _get_ocr()
        if ocr is None:
            return False

        def _recognize(img_path):
            # 留样便于诊断识别率（executor/captcha_samples/ 下最多留 20 张）
            try:
                import glob
                import os
                import shutil
                sample_dir = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "captcha_samples")
                os.makedirs(sample_dir, exist_ok=True)
                old = sorted(glob.glob(os.path.join(sample_dir, "*.png")))
                if len(old) >= 20:
                    os.remove(old[0])
                shutil.copy(img_path, os.path.join(
                    sample_dir, "cap_%s.png" % time.strftime("%H%M%S")))
            except Exception:
                pass
            with open(img_path, "rb") as f:
                return ocr.classification(f.read())

        _gs.captcha_recognize = _recognize

        # 进一步：劫持 easytrader 剪贴板读取的验证码分支——它内置的 set_text+ENTER
        # 方式实测必被同花顺拒绝（识别再对也报"验证码错误"，且固定重试 5 轮，
        # 2026-09-11 实测日志里满屏 captcha 错误）。这里直接**绕过整条老路径**：
        # 先用 executor/captcha.py 的模拟人工强流程（逐字键盘输入+点确定）处理弹窗，
        # 再正常读剪贴板；只有兜底失败时才交回原实现。
        import pywinauto.clipboard as _pwc

        def _patched_get_clipboard_data(self):
            from captcha import find_dialog, handle_captcha
            for _i in range(3):
                try:
                    if find_dialog(self._trader) is not None:
                        handle_captcha(self._trader)     # 强流程：一次过的成功率实测很高
                        continue                          # 处理完再读一次
                except Exception:
                    pass
                try:
                    data = _pwc.GetData()
                    type(self)._need_captcha_reg = False  # 清掉"需验证码"标记
                    return data
                except Exception:
                    time.sleep(0.3)
            return _orig_get_clipboard_data(self)          # 兜底：交回原实现

        _orig_get_clipboard_data = _gs.Copy._get_clipboard_data
        _gs.Copy._get_clipboard_data = _patched_get_clipboard_data

        # 再进一步：劫持交易时的弹窗处理循环。easytrader 的 _handle_pop_dialogs
        # 不认识验证码弹窗（既关不掉也不退出），buy/sell 会死循环卡死
        # （2026-09-11 模拟盘实测）。替换版：验证码优先强流程 + 30 秒超时兜底。
        import easytrader.clienttrader as _ct
        import easytrader.pop_dialog_handler as _pd

        # 诊断：记录交易过程中出现的每个弹窗（标题+按钮文本），便于排查"委托疑似未提交"
        POP_LOG = []

        def _dump_dialog(dlg):
            try:
                info = {"title": dlg.window_text()}
                btns = []
                try:
                    for w in dlg.descendants():
                        t = w.window_text()
                        if t and t.strip():
                            btns.append(t.strip()[:20])
                except Exception:
                    pass
                info["texts"] = btns[:10]
                return info
            except Exception:
                return {"title": "<读不到>", "texts": []}

        def _patched_handle_pop_dialogs(self, handler_class=_pd.PopDialogHandler):
            from captcha import find_dialog, handle_captcha
            handler = handler_class(self._app)
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    if find_dialog(self) is not None:   # 验证码弹窗 -> 强流程
                        POP_LOG.append({"kind": "captcha"})
                        handle_captcha(self)
                        continue
                except Exception:
                    pass
                if not self.is_exist_pop_dialog():
                    return {"message": "success"}
                try:
                    title = self._get_pop_dialog_title()
                except Exception:
                    return {"message": "success"}
                # 取"当前置顶窗口"作为弹窗（不能取第一个 #32770：THS 有一堆隐藏的
                # 无名 #32770，会拿错对话框，导致判定与点击都作用在错误的框上）
                top = None
                try:
                    top = self._app.top_window()
                except Exception:
                    pass
                if top is not None and top.class_name() != "#32770":
                    top = None
                if top is None:
                    try:
                        top = self._app.window(class_name="#32770")
                    except Exception:
                        top = None
                info = _dump_dialog(top)
                info["kind"] = "pop"
                info["handled_by"] = type(handler).__name__
                POP_LOG.append(info)
                print("[弹窗] %s" % json.dumps(info, ensure_ascii=False), flush=True)

                # ── 弹窗判定：按【内容】而非标题 ──
                # 实测（2026-09-11）：THS 的弹窗标题读出来是空串（title=''），
                # easytrader 的 TradePopDialogHandler 靠 title 匹配（"委托确认"/
                # "提示信息"）→ 全部落空，什么都不做（历史"假成功"根源）。
                # 因此这里改用弹窗内可见文本判断，三类分别处理：
                #   1) 价格异常类（小数位/涨跌停）→ 点[否]拒绝，绝不带疑问价格提交
                #   2) 委托确认类 → 按 Alt+Y（与 easytrader 同策略）确认提交
                #   3) 其它 → 交回 easytrader 原生处理器
                texts = info.get("texts") or []
                joined = "".join(texts)
                dlg = top                 # 用上面定位到的置顶弹窗（勿重新取 #32770）

                PRICE_RISK = ("小数部分应为", "小数价格应为", "超出涨跌停",
                              "涨跌停限制", "停牌", "跌停", "涨停")
                CONFIRM = ("委托确认", "确认委托", "网上交易用户协议", "撤单确认")
                SUCCESS = ("委托成功", "已成功", "委托已提交", "撤单成功")

                def _click_button(prefix):
                    if dlg is None:
                        return False
                    for w in dlg.descendants(class_name="Button"):
                        bt = (w.window_text() or "").strip()
                        if bt.startswith(prefix):
                            try:
                                w.click_input()      # 真实鼠标点击（THS 需真实事件）
                            except Exception:
                                w.click()
                            return True
                    return False

                if any(k in joined for k in PRICE_RISK):
                    clicked = _click_button("否")
                    msg = ("THS 价格确认框已点[否]拒绝提交（价格异常不盲提）: %s"
                           % joined[:60])
                    POP_LOG.append({"kind": "declined", "clicked": clicked})
                    print("[弹窗] %s" % msg, flush=True)
                    return {"message": msg}

                if any(k in joined for k in CONFIRM):
                    # 标准「委托确认」框：按 Alt+Y = 是（真实键盘，兼容自绘按钮）
                    ok_y = False
                    try:
                        dlg.type_keys("%Y", set_foreground=False)
                        ok_y = True
                    except Exception:
                        pass
                    if not ok_y:
                        _click_button("是")
                    time.sleep(0.5)
                    POP_LOG.append({"kind": "confirmed", "by": "alt+y" if ok_y else "click"})
                    print("[弹窗] 已确认委托提交（Alt+Y）: %s" % joined[:60], flush=True)
                    continue     # 继续循环：可能还有「委托成功」提示框

                if any(k in joined for k in SUCCESS):
                    # 「您的买入委托已成功提交，合同编号：6254299085」→ 直接从弹窗正文
                    # 提取合同编号（比事后查当日委托可靠：剪贴板表格常返回缓存旧数据）
                    try:
                        import re as _re
                        full = "".join((w.window_text() or "")
                                       for w in (dlg.descendants(class_name="Static")
                                                 if dlg is not None else []))
                        m = _re.search(r"合同编号[：:]\s*([0-9]+)", full)
                        if m:
                            self._last_entrust_no = m.group(1)
                            POP_LOG.append({"kind": "entrust_no", "no": m.group(1)})
                            print("[弹窗] 已提取合同编号 %s" % m.group(1), flush=True)
                    except Exception:
                        pass
                    _click_button("确定")
                    POP_LOG.append({"kind": "success_tip"})
                    continue

                result = handler.handle(title)
                if result:
                    return result
            POP_LOG.append({"kind": "timeout"})
            print("[弹窗] 处理循环 30 秒超时兜底", flush=True)
            return {"message": "success"}   # 超时兜底，不让交易线程卡死

        _ct.ClientTrader._handle_pop_dialogs = _patched_handle_pop_dialogs
        _ct.ClientTrader._pop_log = POP_LOG
        return True
    except Exception:
        return False
    except Exception:
        return False


def patch_easytrader_prompt_windows():
    """接管 easytrader 的 `_close_prompt_windows()`：**绝不再对同花顺自己的窗口发 WM_CLOSE**。

    背景（2026-09-12 真机日志 + 用户现场）：easytrader 在 `connect()` 里会执行
        for window in self._app.windows(class_name="#32770", visible_only=True):
            if window.window_text() != self._config.TITLE:
                window.close()        # = SendMessage(WM_CLOSE)
    也就是把**所有可见 #32770 且标题 != "网上股票交易系统5.0"** 的窗口统统关掉。
    而同花顺的**登录窗口标题恰好是空串**（弹窗标题也经常为空），于是：
      * 关掉登录窗口 -> 同花顺直接退出 -> 下一轮又被拉起 = "反复拉起关闭"；
      * 关在它初始化的中途 -> 主窗口留着但内容一片空白、点了没反应 = 用户报的
        "假死"，而原生子控件文本还在（所以资金仍读得到）。
    执行端日志里的特征就是"自动启动后紧跟两条标题为空的 `close window`"，
    随后 `No windows for that process could be found`（进程已死）。
    连同"主窗口几秒就出现、内部要几十秒才就绪"这件事，构成了
    "执行端拉起的同花顺假死/空白，手动运行却一切正常"的根因。

    替换后的策略（宁可不动手，也不乱动手）：
      1) **一个 WM_CLOSE 都不发**；
      2) 真正需要清掉的干扰弹窗交给 `dialog_guard.dismiss_interference_dialogs()`
         —— 白名单匹配 + 只点[取消]/[关闭]/[否] + WindowFromPoint 校验，
         等价于人工点掉，比 WM_CLOSE 温和且可控；
      3) 若弹窗是验证码，走 `captcha.handle_captcha()`（与查询路径同一套强流程）；
      4) 其余对话框一律**只记日志、不碰**（需要人工时如实暴露问题，而不是
         把同花顺关掉/弄坏）。
    必须在 import easytrader 之后调用。
    """
    try:
        import logging

        import easytrader.clienttrader as _ct
        _log = logging.getLogger("executor")

        def _safe_close_prompt_windows(self):
            cfg = getattr(self, "_config", None)
            main_title = getattr(cfg, "TITLE", None)
            titles = []
            try:
                for w in self._app.windows(class_name="#32770", visible_only=True):
                    try:
                        titles.append(w.window_text() or "")
                    except Exception:
                        titles.append("<读不到>")
            except Exception:
                titles = []
            others = [t for t in titles if t != main_title]

            path = (getattr(self, "_jq_xiadan_path", None)
                    or getattr(cfg, "DEFAULT_EXE_PATH", None) or "")

            # 1) 白名单干扰弹窗：点安全按钮（等价人工操作）
            acted = 0
            try:
                import dialog_guard
                acted = len(dialog_guard.dismiss_interference_dialogs(path) or [])
            except Exception as e:
                _log.debug("白名单干扰弹窗处理跳过: %s", e)

            # 2) 验证码弹窗：走 captcha 的"模拟人工"强流程（原来会被直接 close 掉，
            #    等于把验证码框关了、查询必然失败）
            captcha_state = "none"
            try:
                from captcha import handle_captcha
                captcha_state = handle_captcha(self)
            except Exception as e:
                _log.debug("验证码处理跳过: %s", e)

            if others or acted or captcha_state not in ("none",):
                _log.info(
                    "easytrader 原本会把 %d 个同花顺对话框(%s)全部 WM_CLOSE 掉；"
                    "已改为安全策略：白名单弹窗处理 %d 个、验证码 %s、其余一律不碰",
                    len(others), others[:3], acted, captcha_state)
            return None

        _ct.ClientTrader._close_prompt_windows = _safe_close_prompt_windows
        return True
    except Exception:
        return False


def patch_easytrader_editor_typing():
    """
    修复 easytrader 往同花顺价格/数量框输入时「追加而非替换」的问题。
    背景：同花顺在识别出代码后会把「最新价」自动填进价格框；easytrader 用
    select()+type_keys 想替换，但 THS 自绘 Edit 上 select() 实测不生效 → 变成
    追加输入，且价格框有长度上限（"1276.00" + "1535.48" 被截成 "1276.001"），
    导致委托价错误并弹「小数部分应为 2 位」确认框卡住自动化（2026-09-11 实测）。
    补丁（2026-09-11 真机全流程实测后第 2 次重写，两条铁律）：
      - **必须键盘输入**：WM_SETTEXT 只改文本框显示、不触发 THS 的登记回调，
        界面看着有值但点[买入]被静默忽略（委托不进系统）。键盘方式＝
        先 {BACKSPACE 20} 清空（防追加）再逐字 type_keys。WM_SETTEXT 仅作兜底。
      - **必须按 control_id 精确取框**：pywinauto 0.6.8 的
        `descendants(control_id=...)` 静默忽略该过滤条件（请求 1032 会返回全部
        Edit），若不手工过滤，代码/价格/数量三次写入会全落到第一个框
        （外界看到"价格和数量都变成证券代码"）。
      - 每次写入后回读校验（数值等价）；THS 输入框不支持 WM_GETTEXT（恒返回
        空串），空回读=无法验证而非失败 → 放行，改由提交前守卫
        （patch_easytrader_submit_guard）查"证券名称/可买股数是否已登记"兜底。
    必须在 import easytrader 之后调用。
    """
    try:
        import easytrader.clienttrader as _ct

        def _find_editors(self, control_id):
            """当前窗口下 control_id 匹配且可见的 Edit 实例。

            ⚠ 关键坑（2026-09-11 实测定位）：pywinauto 0.6.8 的
            `descendants(control_id=...)` 会**静默忽略** control_id 过滤条件
            （请求 1032 会返回全部 22 个 Edit，含 1001/1037/1128...），
            导致 `_find_editors` 对 1032/1033/1034 返回同一个列表，
            三次写入全部落到列表第一个框（证券代码框）——外界看到的就是
            "价格和数量都被写成了证券代码"。必须在 Python 侧按
            element_info.control_id 手工过滤，不可改回 descendants(control_id=..)。
            """
            out = []
            try:
                for e in self._main.descendants(class_name="Edit"):
                    try:
                        cid = int(getattr(e.element_info, "control_id", -1))
                    except (TypeError, ValueError):
                        continue
                    if cid != int(control_id):
                        continue
                    try:
                        if e.is_visible():
                            out.append(e)
                    except Exception:
                        continue
            except Exception:
                pass
            if os.environ.get("JQ_EDIT_DEBUG"):
                try:
                    print("[输入诊断] cid=%s 匹配可见实例=%d rects=%s"
                          % (control_id, len(out), [str(e.rectangle()) for e in out]), flush=True)
                except Exception as _e:
                    print("[输入诊断] cid=%s 枚举异常 %r" % (control_id, str(_e)[:60]), flush=True)
            if out:
                return out
            return [self._main.child_window(control_id=control_id, class_name="Edit")]

        def _read_edit(editor):
            try:
                t = editor.window_text()
                if not t:
                    ts = editor.texts()
                    t = ts[0] if ts else ""
                return str(t).strip()
            except Exception:
                return None

        def _same(a, b):
            """数值等价比较；兼容 '510300,'（THS 代码框自动补的分隔符）。"""
            sa = str(a).replace(",", "").strip()
            sb = str(b).replace(",", "").strip()
            try:
                return abs(float(sa) - float(sb)) < 1e-6
            except (TypeError, ValueError):
                return sa == sb

        def _patched(self, control_id, text):
            editors = _find_editors(self, control_id)
            text = str(text)

            def _keyboard(editor):
                """键盘输入（主力方式）。
                为什么不用 WM_SETTEXT：2026-09-11 实测，WM_SETTEXT 只改变文本框的
                显示内容，**不触发同花顺的 EN_CHANGE 登记逻辑** —— 证券名称/可买(股)
                都不出现，点[买入]被静默忽略（委托根本不进系统）。
                必须用真实键盘事件：先退格清空（防追加），再逐字输入。"""
                editor.set_focus()
                time.sleep(0.15)
                try:
                    editor.type_keys("{BACKSPACE 20}", with_spaces=False)
                except Exception:
                    pass
                time.sleep(0.15)
                if text:                       # text="" 表示"只清空"，退格后即完成
                    editor.type_keys(text, with_spaces=False)

            def _wm_settext(editor):
                """WM_SETTEXT 兜底（键盘不可用时）。注意它不触发登记，
                提交前还有 patch_easytrader_submit_guard 兜底拦截。"""
                editor.set_edit_text(text)

            attempts = [_keyboard, _wm_settext]

            last_err = None
            # 逐实例 × 逐招法尝试：THS 多页签共享控件树，同 ID"可见"实例可能不止一个，
            # 只有能写入且回读一致的那个才是当前页面的真输入框
            for editor in editors:
                for i, fn in enumerate(attempts):
                    try:
                        fn(editor)
                    except Exception as e:
                        last_err = e
                        if os.environ.get("JQ_EDIT_DEBUG"):
                            print("[输入诊断] cid=%s 招法%d 异常 %r"
                                  % (control_id, i + 1, str(e)[:70]), flush=True)
                        continue
                    time.sleep(0.35)  # THS 收到代码后异步重格式化/自动填价，立即回读会读到空串
                    got = _read_edit(editor)
                    if os.environ.get("JQ_EDIT_DEBUG"):
                        print("[输入诊断] cid=%s 招法%d 回读 %r"
                              % (control_id, i + 1, got), flush=True)
                    if got is None:
                        continue           # 读不了，试下一招
                    if got == "" and text != "":
                        # THS 输入框不支持 WM_GETTEXT 回读，实测恒返回空串
                        # （2026-09-11 真机验证）。空回读=无法验证而非写入失败，放行。
                        # 真正的校验交给 patch_easytrader_submit_guard（提交前查
                        # 证券名称/可买股数是否已登记）。
                        # 注意：此处不可改成"判为不一致拒单"——会把所有正常单全部拒掉。
                        return
                    if _same(got, text):
                        return
                    last_err = RuntimeError("输入后回读不一致: 期望 %r 实际 %r"
                                            % (text, got))
            raise RuntimeError("同花顺编辑框输入失败(control_id=%s): %s"
                               % (control_id, last_err))

        _ct.ClientTrader._type_edit_control_keys = _patched
        return True
    except Exception:
        return False


def patch_easytrader_submit_guard():
    """
    提交前表单自检：THS 没登记表单就绝不点[买入]。

    背景（2026-09-11 真机实测）：WM_SETTEXT 写入的文本不会触发 THS 的登记逻辑，
    界面上看着有值，但点[买入]被静默忽略、委托不进系统（表现为"疑似未提交"）。
    判定方法：THS 一旦正确登记，会在「证券代码」框右侧出现**证券名称**文本
    （如 沪深300ETF华泰柏瑞），在「买入价格」框右侧出现**可买(股)**数值；
    两者都缺失 => 表单未登记 => 拒绝提交（宁可失败，也不产生假成功）。
    必须在 import easytrader 之后、patch_easytrader_editor_typing 之后调用。
    """
    try:
        import easytrader.clienttrader as _ct

        def _in_row(w, box, slack=8):
            """w 是否是 box 所在行的"值"文本（用于定位 证券名称 / 可买(股)）。

            实测坐标（2026-09-11 真机）：
              标签列 Static   left≈1075（证券代码/证券名称/买入价格/可买(股)...）
              输入框 Edit     left=1133
              值  Static      left=1133（与输入框左对齐）、top=框底+2
            => 判别式：left 在输入框左缘附近(>= box.left-4) 且纵向紧邻该框。
               不能只比 left（值不在框右侧），也不能只比 top（标签的 top 与框相同会被误收）。
            """
            try:
                r = w.rectangle()
                return (r.left >= box.left - 4 and
                        box.top - slack <= r.top <= box.bottom + slack)
            except Exception:
                return False

        def _registered(ctrl):
            """返回 (证券名称, 可买股数文本)：登记成功时两者至少有一个有值。"""
            main = getattr(ctrl, "_main", None)
            if main is None:
                return "", None
            boxes = {}
            try:
                for e in main.descendants(class_name="Edit"):
                    try:
                        cid = int(getattr(e.element_info, "control_id", -1))
                    except (TypeError, ValueError):
                        continue
                    if cid in (1032, 1033) and e.is_visible():
                        boxes[cid] = e.rectangle()
            except Exception:
                pass
            name, ke_mai = "", None
            try:
                for w in main.descendants(class_name="Static"):
                    try:
                        if not w.is_visible():
                            continue
                        t = (w.window_text() or "").strip()
                        if not t:
                            continue
                        if 1032 in boxes and _in_row(w, boxes[1032]):
                            name = t
                        elif 1033 in boxes and _in_row(w, boxes[1033]):
                            ke_mai = t
                    except Exception:
                        continue
            except Exception:
                pass
            return name, ke_mai

        _orig_submit = _ct.ClientTrader._submit_trade

        def _guarded_submit(self):
            # 回查哨兵：提交前读「证券名称 / 可买(股)」——THS 正确登记后这两者才会
            # 出现；两者皆空 = 界面没回查出名称/价格（界面错乱/被验证码打断）。
            name, ke_mai = _registered(self)
            ok = bool(name) or bool(ke_mai)
            if os.environ.get("JQ_EDIT_DEBUG"):
                print("[提交自检] 证券名称=%r 可买(股)=%r -> 已登记=%s"
                      % (name, ke_mai, ok), flush=True)
            if not ok:
                # 上报看门狗（界面级异常）：多次发生即触发自动重启同花顺。
                # 这里只上报，不重试——重试由 broker_ths._trade 的统一重试负责。
                _watchdog_report("not_registered",
                                 "证券名称/可买股数均为空（表单未登记，"
                                 "代码查不出名称/价格）")
                raise _ui_not_ready_error(
                    "同花顺未登记本次委托表单（证券名称/可买股数均为空，"
                    "代码查不出名称/价格），已阻止点击[买入]——请检查输入链路"
                    "（键盘输入是否生效）")
            _watchdog_ok()
            return _orig_submit(self)

        _ct.ClientTrader._submit_trade = _guarded_submit

        # ---- 填写前先点[重填]清空表单 ----
        # 实测：THS 表单会保留上一次的代码/价格（提交失败或被拦时尤其明显）。
        # 若本次输入链路失效而残留值还在，点[买入]可能提交"上一个标的"——先用
        # 重填按钮把表单清干净，保证提交的必然是本次写入的内容。
        _orig_set_params = _ct.ClientTrader._set_trade_params
        REFILL_BTN_ID = 1007      # 买入页 [重填] 按钮

        def _cleared_set_params(self, security, price, amount):
            # ⚠ 必须用 find_button 手工过滤 control_id：pywinauto 的
            # child_window(control_id=..) 会静默忽略该条件，可能点到无关按钮。
            try:
                btn = find_button(getattr(self, "_main", None),
                                  REFILL_BTN_ID, text="重填")
                if btn is not None:
                    try:
                        btn.click_input()  # 真实鼠标点击（BM_CLICK 对 THS 可能不生效）
                    except Exception:
                        btn.click()
            except Exception:
                pass          # 找不到重填按钮的版本：跳过，靠提交前自检兜底
            time.sleep(0.3)
            return _orig_set_params(self, security, price, amount)

        _ct.ClientTrader._set_trade_params = _cleared_set_params
        return True
    except Exception:
        return False


apply()
