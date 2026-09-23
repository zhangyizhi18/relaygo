# -*- coding: utf-8 -*-
"""
方案A：easytrader 驱动同花顺客户端（UI 自动化）。

前置条件（务必逐条确认）：
  1. pip install easytrader pillow pywinauto==0.6.8
     （必须用 0.6.8 覆盖 easytrader 锁定的 0.6.6：0.6.6 在 64位Python 上批量
      SendInput 会被部分机器的输入过滤静默拒绝，0.6.8 无此问题；
      同时本模块导入 pywinauto_compat 兼容垫片）
  2. 同花顺客户端已安装、已登录交易账号，且"自动升级"已关闭（版本一升级控件就可能失效！）
  3. THS_XIADAN_PATH 指向下单程序 xiadan.exe（不是同花顺主程序）
  4. 本脚本与客户端同机运行，且不能锁屏（UI 自动化的固有限制）

本模块针对实战踩坑做的加固：
  - pywinauto_compat：SendInput 逐事件补丁（部分安全软件拦截批量注入）
  - Copy 剪贴板读表格：新版同花顺不再支持"导出临时 Excel"
  - _ensure_fg：每次操作前强制窗口置前（绕过 Windows 前台锁）
  - close_captcha_dialog：自动检测并关闭验证码弹窗（自动化频繁会触发风控，
    检测到时自动关闭并降低操作频率；若验证码导致表格读取失败会如实报错）
  - cancel_order：经实测 cancel_entrust 的双击定位在新版控件上失效，
    改用"读撤单表 -> 确认唯一目标 -> 点[全撤]"的安全路径
"""
import os
import time
import datetime
import logging

log = logging.getLogger("executor")

from broker_base import BrokerBase, normalize_price, to_plain_code
import market_price
import config
from foreground import (THS_TITLE_KEYWORD, find_window_by_title, force_foreground,
                        main_window_ready, main_window_status)
import pywinauto_compat   # noqa: F401  必须在 easytrader 之前导入
import ths_watchdog       # 界面异常看门狗（非侵入：此处只上报，重启决策在 main）

try:
    import easytrader
    pywinauto_compat.patch_easytrader_captcha()      # 验证码识别改用 ddddocr（免装 Tesseract）
    pywinauto_compat.patch_easytrader_prompt_windows()  # 禁止 easytrader 无脑关同花顺窗口
    if not os.environ.get("JQ_NO_TYPING_PATCH"):     # A/B 对照诊断开关
        pywinauto_compat.patch_easytrader_editor_typing()  # 键盘清空+逐字输入（防追加错价/表单未登记）
        pywinauto_compat.patch_easytrader_submit_guard()   # 提交前自检：表单未登记则不点[买入]
except ImportError:
    easytrader = None


class ThsBroker(BrokerBase):
    name = "ths(easytrader)"

    _verify_ts_warned = False      # 「委托时间」解析失败只告警一次，避免刷屏

    def __init__(self, xiadan_path):
        self.xiadan_path = xiadan_path
        self.user = None
        self._hwnd = 0
        self._last_captcha_ts = 0.0   # 上次遇到验证码的时间，用于降频

    # ---------------- 基础 ----------------

    def _ui_error(self, kind, detail=""):
        """上报一次界面级异常给看门狗（非侵入：只上报，重启决策在 main）。

        仅在"界面层"异常时调用：填不出证券名称/价格、控件找不到、剪贴板读不到
        表格、弹窗卡死、验证码连续失败，以及**行情主程序/交易模块拉不起来**
        （launch_fail / f12_launch_fail —— 2026-09-13 补：此前这类失败完全不上报，
        看门狗看不见，自然也不会自动重启）。**业务失败（风控拒绝/券商拒单）绝不
        在此上报**——否则高频信号会把同花顺重启到疯（重启风暴）。
        """
        try:
            ths_watchdog.watchdog.report_ui_error(kind, detail)
        except Exception:
            pass
        log.warning("界面异常[%s] %s", kind, str(detail)[:160])

    def _ui_ok(self):
        """上报一次"界面正常"（清零看门狗连续计数）。"""
        try:
            ths_watchdog.watchdog.report_ui_ok()
        except Exception:
            pass

    def _ensure_fg(self):
        """每次操作前确保下单窗口在前台（绕过 Windows 前台锁）。"""
        if not self._hwnd:
            self._hwnd = find_window_by_title(THS_TITLE_KEYWORD)
        ok = force_foreground(self._hwnd)
        if not ok:
            # 窗口句柄可能变了（客户端重启），重找一次
            self._hwnd = find_window_by_title(THS_TITLE_KEYWORD)
            ok = force_foreground(self._hwnd)
        time.sleep(0.2)
        return ok

    def _launch(self):
        """按配置的启动模式拉起同花顺交易界面（见 _launch_main / _launch_standalone）。"""
        if config.THS_LAUNCH_MODE == "standalone":
            return self._launch_standalone()
        return self._launch_main()

    def _launch_standalone(self):
        """直接启动下单程序 xiadan.exe —— 关键：**工作目录必须设成安装目录**。

        为什么（2026-09-12 用户现场："执行端拉起的同花顺假死、内容空白，
        手动运行却自动登录、一切正常"）：
          执行端由 启动执行端.bat 以 `cd /d "%~dp0"`（= 项目根）启动，于是
          os.startfile 默认让同花顺**继承项目根**作为工作目录；而用户双击 /
          点快捷方式时，Windows（资源管理器）给同花顺的工作目录是**安装目录**。
          同花顺及其内嵌浏览器（旧版 CEF 的 cef84\\xdCache、新版 QtWebEngine 的
          xiadan-plus\\cfg 等）都按相对路径找自己的配置/缓存/子模块，工作目录不对
          时就会出现"进程起来了，但界面空白、点了没反应、也不会自动登录"，
          即"程序启动 vs 手动启动行为不一致"。这里显式传 cwd=exe 所在目录，
          让程序内启动与双击启动在上下文上等价。
        """
        path = self.xiadan_path
        workdir = os.path.dirname(os.path.abspath(path))
        try:
            if os.path.isdir(workdir):
                try:
                    os.startfile(path, cwd=workdir)   # Python 3.10+ 才支持 cwd
                    log.info("同花顺启动命令已发出（工作目录=%s）", workdir)
                    return True
                except TypeError:
                    pass                              # 老版本 Python：退化为默认
            os.startfile(path)
            log.info("同花顺启动命令已发出（未能指定工作目录，退回默认）")
            return True
        except Exception as e:
            log.error("自动启动同花顺失败（启动程序异常）: %s", e)
            return False

    def _start_process(self, path):
        """启动指定的同花顺可执行文件（工作目录设为 exe 所在目录）。

        理由同 _launch_standalone：工作目录不对会让同花顺"进程起来了但界面空白、
        不会自动登录"。这里把该逻辑抽出来，供行情主程序（hexin.exe）复用。
        """
        workdir = os.path.dirname(os.path.abspath(path))
        try:
            if os.path.isdir(workdir):
                try:
                    os.startfile(path, cwd=workdir)   # Python 3.10+ 才支持 cwd
                    log.info("启动命令已发出: %s（工作目录=%s）", path, workdir)
                    return True
                except TypeError:
                    pass                              # 老版本 Python：退化为默认
            os.startfile(path)
            log.info("启动命令已发出: %s（未能指定工作目录，退回默认）", path)
            return True
        except Exception as e:
            log.error("启动失败: %s -> %s", path, e)
            return False

    def _launch_main(self, force_restart_main=False):
        """启动「行情主程序」并按 F12 拉起交易模块 —— 避风控路径（推荐）。

        为什么必须这样（2026-09-13 用户实测）：
          * 直接启动 xiadan.exe：交易模块要**自建一条独立会话**，并靠"按需查询"去要
            代码→名称/最新价；这类高频主动查询会触发同花顺前端风控 —— 表现就是
            "代码查不出名称和价格，之后所有单都卡住"（随机发生，重启只能临时缓解）。
          * 从行情主程序 hexin.exe 按 F12 打开的交易模块，**复用行情主程序已建立的
            登录会话与行情推送通道（订阅式，服务器主动推价）**，不触发风控。
            实测：这样连续下 10 单全部正确提交。
        流程：行情没在跑就先起它 → 等它主窗口**真正可用** → 置前 → 发 F12 → 等交易窗口。
        绝不重复启动第二个行情主程序（同花顺单实例保护会把新进程并到旧实例上）。

        2026-09-13 加固（用户反馈"行情还没完全启动就发 F12，有时候拉不起下单程序，
        而看门狗也没起作用"）：
          1) 发 F12 前先过「主窗口就绪门槛」（_wait_main_window）：可见 + 面积够大 +
             消息循环有响应 + 尺寸连续稳定 —— 不再"窗口一出现就发"；
          2) **每次重试前重新定位行情主窗口**：冷启动期间句柄会变（闪屏 → 登录窗 →
             主窗），拿旧句柄发 F12 等于发给已销毁的窗口；
          3) 每轮最多重发 THS_F12_RETRY 次、总时长不超过 THS_F12_TOTAL_WAIT 秒，
             发送前后校验前台窗口（_send_f12）；
          4) 彻底拉不起来时**上报看门狗**（launch_fail / f12_launch_fail），让主循环
             能按阈值触发自动重启 —— 此前这条路径完全不上报，看门狗等于瞎的。

        force_restart_main=True：深度恢复 —— 连行情主程序一起结束再重启。
          只在"F12 反复拉不起交易模块"时由 restart_ths 使用。因为行情主程序自身也
          可能卡在异常状态（登录会话失效、模块没加载），那时只重发 F12 是白费力气。
          注意：ensure_started 的常规自愈**不用**深度恢复（不能把用户正常开着的
          行情软件反复杀掉），只有控制台「重启同花顺」/看门狗才会走到这里。
        """
        main_path = getattr(config, "THS_MAIN_PATH", "")
        if not main_path or not os.path.isfile(main_path):
            log.error("行情主程序路径不存在：%s（请在 .env 里配置 "
                      "EXECUTOR_THS_MAIN_PATH，或把 EXECUTOR_THS_LAUNCH_MODE 设为 "
                      "standalone 退回旧方式）", main_path)
            self._ui_error("launch_fail", "行情主程序路径不存在: %s" % main_path)
            return False

        main_wait = _cfg_int("THS_MAIN_WAIT", 90)
        f12_retry = _cfg_int("THS_F12_RETRY", 6)
        f12_total = _cfg_int("THS_F12_TOTAL_WAIT", 150)
        f12_per = _cfg_int("THS_F12_PER_WAIT", 20)
        main_exit_wait = _cfg_int("THS_MAIN_EXIT_WAIT", 15)

        try:
            main_running = bool(_pids_of_xiadan(main_path))
        except Exception as e:
            log.warning("行情主程序进程探测失败（%s），保守按已运行处理、不重复拉起", e)
            main_running = True

        if force_restart_main and main_running:
            log.warning("深度恢复：先结束行情主程序（PID %s），再重新启动",
                        _pids_of_xiadan(main_path))
            _kill_by_path(main_path)
            deadline = time.time() + max(5, main_exit_wait)
            while time.time() < deadline:
                try:
                    if not _pids_of_xiadan(main_path):
                        break
                except Exception:
                    break
                time.sleep(0.5)
            main_running = False

        if main_running:
            log.info("行情主程序已在运行，直接按 F12 拉起交易模块")
        else:
            log.info("行情主程序未运行，自动启动: %s", main_path)
            if not self._start_process(main_path):
                self._ui_error("launch_fail", "启动行情主程序失败: %s" % main_path)
                return False

        # 等主窗口"真正可用"再发 F12：不能窗口一出现就发（闪屏/登录框句柄还会变，
        # 发过去的 F12 会随窗口一起消失）。
        main_hwnd = _wait_main_window(main_path, max(10, main_wait))
        if main_hwnd:
            log.info("行情主窗口已就绪（连续稳定 %s 次采样，%s），发送 F12 拉起交易模块",
                     _cfg_int("THS_MAIN_READY_STABLE", 3),
                     "hwnd=%s %s" % (main_hwnd, _describe_window(main_hwnd)))
        else:
            log.warning("行情主程序 %s 秒内未见可用的主窗口，仍尝试发送 F12"
                        "（每轮都会重新定位窗口）", main_wait)

        attempts = max(1, f12_retry)
        total_deadline = time.time() + max(30, f12_total)
        sent = 0
        for i in range(attempts):
            if time.time() >= total_deadline:
                log.warning("已达 F12 总时长上限（%s 秒），停止重试", f12_total)
                break
            # 关键：每轮重新定位行情主窗口 —— 冷启动期间句柄会变
            # （闪屏 → 登录窗 → 主窗），拿旧句柄发 F12 等于石沉大海。
            hwnd = _pick_main_window(main_path, _cfg_int("THS_MAIN_MIN_AREA", 120000))
            if not hwnd:
                try:
                    wins = _windows_of_path(main_path)
                except Exception:
                    wins = []
                hwnd = wins[0] if wins else 0   # 面积不够大也先试一次（好过完全不发）
            if not hwnd:
                log.warning("第 %s 次尝试：仍未找到行情主窗口，1 秒后重试", i + 1)
                time.sleep(1)
                continue
            sent += 1
            if not _send_f12(hwnd):
                log.warning("第 %s 次发送 F12 未生效（窗口未置前/句柄已变），稍后重试", i + 1)
                time.sleep(2)
                continue
            # 一次尝试只发一次 F12（重发太密会与"交易模块正在拉起"相互干扰），
            # 用它后面的确认窗口来判断是否真起来了：命中一次不算成功，
            # 必须连续稳定命中（见 _confirm_trading_window）。
            wh, wexe = _confirm_trading_window(max(3.0, min(8.0, float(f12_per))))
            if wh:
                log.info("F12 已拉起交易窗口（第 %s 次尝试；行情窗口 hwnd=%s，"
                         "交易窗口 hwnd=%s 归属 %s）",
                         i + 1, hwnd, wh, os.path.basename(wexe) or "?")
                self._ui_ok()
                return True
            now_hwnd = find_window_by_title(THS_TITLE_KEYWORD)
            if now_hwnd:
                # 这种"起来了又没了"最容易被误判成成功（旧实现就是），必须留痕：
                # 通常意味着 F12 发得太早 —— 行情还在登录，交易模块被拉起后随即自退。
                log.warning("第 %s 次 F12 后交易窗口出现过但未能稳定存活（当前 hwnd=%s），"
                            "行情可能仍在登录阶段；重新定位行情窗口后重试", i + 1, now_hwnd)
            else:
                log.warning("第 %s 次 F12 后仍未出现交易窗口，重新定位行情窗口后重试", i + 1)
            time.sleep(2)
        wh, wexe = _confirm_trading_window(max(3.0, min(8.0, float(f12_per))))
        if wh:
            log.info("交易窗口已出现（末次确认；hwnd=%s 归属 %s）",
                     wh, os.path.basename(wexe) or "?")
            self._ui_ok()
            return True
        log.error("共发送 %s 次 F12 仍未拉起交易模块（行情主程序%s）",
                  sent, "在运行" if main_running else "已重新启动")
        self._ui_error("f12_launch_fail",
                       "发送 %d 次 F12 仍未出现交易窗口（行情主程序%s）"
                       % (sent, "在运行" if main_running else "已重新启动"))
        return False

    def _relaunch_trading_module(self):
        """交易窗口迟迟不来（或来了又自退）时，重新定位行情主窗口并重发一次 F12。

        为什么必须有这条"外层兜底"（2026-09-13 现场实测）：
          _launch_main 内部虽然会重试 F12，但它用 _confirm_trading_window 判成功
          —— 交易窗口只要"稳定存活 1 秒"就算数。实测 19:31:21 xiadan 启动、
          19:31:24 自己退出（此时行情其实还在登录阶段），_launch_main 却在
          19:31:23 就报了「F12 已拉起交易窗口」并返回 True；随后 ensure_started
          的等待循环**只会干等满 60 秒**（期间不重发 F12），等不到才报失败 ——
          白白浪费掉一次自愈机会。现场表现就是用户说的
          "重启执行端后能启动行情，还是无法拉起下单程序"。

        本方法让等待循环在"交易窗口不见了"时立刻重发 F12（而不是干等）：
        重新定位行情主窗口 → 发 F12 → 确认交易窗口稳定存活。

        返回 (ok, hwnd)：ok=True 表示本次重发后交易窗口已确认稳定存活。
        """
        main_path = getattr(config, "THS_MAIN_PATH", "")
        if not main_path or not os.path.isfile(main_path):
            return False, 0
        # 每轮都**重新定位**行情主窗口：冷启动期间句柄会变（闪屏 → 登录窗 → 主窗），
        # 拿旧句柄发 F12 等于石沉大海（与 _launch_main 里同款说明）。
        # 只认"像主框架"的窗口，避免把 F12 又发给登录框（见 _looks_like_main_frame）。
        hwnd = _pick_main_window(main_path, _cfg_int("THS_MAIN_MIN_AREA", 120000),
                                 main_frame_only=True)
        if not hwnd:
            hwnd = _pick_main_window(main_path, 0, main_frame_only=True)
        if not hwnd:
            log.warning("交易窗口未出现/已消失，但当前找不到可用的行情主窗口，稍后再试")
            return False, 0
        log.info("交易窗口未出现/已消失，重新定位行情主窗口（hwnd=%s %s）并重发 F12",
                 hwnd, _describe_window(hwnd))
        if not _send_f12(hwnd):
            log.warning("重发 F12 未生效（窗口未置前/句柄已变），稍后重试")
            return False, 0
        # 单次确认预算取 THS_F12_PER_WAIT 的一半（3~8 秒）：
        # 外层循环很快还会再来一轮，这里宁可早点返回让它重发，不要长时间独占。
        wh, wexe = _confirm_trading_window(
            max(3.0, min(8.0, float(_cfg_int("THS_F12_PER_WAIT", 20)) / 2.0)))
        if wh:
            log.info("重发 F12 后交易窗口已确认稳定存活（hwnd=%s 归属 %s）",
                     wh, os.path.basename(wexe) or "?")
            return True, wh
        return False, 0

    def ensure_started(self, verbose=True):
        """执行端连接前确保同花顺下单程序已运行：未运行则自动拉起。

        判定"已运行"：交易窗口已存在(含托盘隐藏态)或对应进程已在运行，
        任一成立即视为已启动，不重复拉起。自动拉起后轮询等待交易窗口出现
        并恢复可见，再交给 connect 去 attach。

        注意两点语义（2026-09-12 修正）：
          1) 本方法只保证"进程在跑、窗口在"，**不代表同花顺已就绪**！
             "交易窗口就绪"由 foreground.main_window_ready() 判定，在
             connect() 里作为硬门槛 —— 因为主框架几秒就出现，内部要几十秒
             才好，过早连接会把同花顺弄成"假死+空白"（见 _launch 与
             main_window_status 的说明）。
          2) 未登录时后续 health_check 会如实反馈，需人工登录或由
             assist_login 代点[登录]。
          3) 拉起来后会在 THS_AUTOSTART_TIMEOUT 内轮询等待；期间若交易窗口
             迟迟不出现、或出现后随即自退，会每隔 THS_RESEND_F12_GAP 秒
             **重发一次 F12**（见 _relaunch_trading_module）—— 而不是干等满
             超时（2026-09-13 用户反馈"能启动行情，却一直拉不起下单程序"）。"""
        if not config.THS_AUTOSTART:
            log.info("THS_AUTOSTART 关闭，跳过自动启动检查")
            return True
        # 用交易窗口关键字 + 进程路径双重判定"已运行"，任一成立即视为已启动、
        # 不重复拉起。进程检测走 win32（绕过被拦截的 wmic / 缺失的 psutil），
        # 即使同花顺停在登录界面或缩在托盘也能正确识别，避免误判后反复拉起。
        # 进程检测失败（win32 异常/被拦截）时保守视为"已在运行"：宁可不拉起，
        # 也绝不因为误判而启动第二个同花顺下单实例（两个实例同时挂单会互相干扰）。
        try:
            proc_running = _process_running_by_path(self.xiadan_path)
        except Exception as e:
            log.warning("同花顺进程检测失败（%s），保守按已运行处理、不重复拉起", e)
            proc_running = True
        running = (find_window_by_title(THS_TITLE_KEYWORD) != 0) or proc_running
        if running:
            if verbose:   # 连接重试循环里每 5 秒喊一次会把日志刷爆
                log.info("同花顺下单程序已在运行（含登录界面/托盘），无需重复拉起")
            return True
        if config.THS_LAUNCH_MODE == "standalone":
            log.info("同花顺下单程序未运行，自动启动: %s", self.xiadan_path)
        else:
            log.info("未检测到交易界面，按「行情主程序 + F12」方式拉起（行情: %s）",
                     config.THS_MAIN_PATH)
        if not self._launch():
            return False
        deadline = time.time() + config.THS_AUTOSTART_TIMEOUT
        hwnd = 0
        ready = False
        # 交易窗口消失超过该秒数就重发一次 F12。默认 8 秒：既够"被拉起后随即自退"
        # 的窗口暴露出来，又不至于 F12 发得太密、与"交易模块正在拉起"相互干扰
        # （见 _launch_main 里"一次尝试只发一次 F12"的说明）。
        resend_gap = max(5, _cfg_int("THS_RESEND_F12_GAP", 8))
        last_resend = time.time()
        window_seen = False   # 本轮是否曾检测到交易窗口出现（用于"出现后消失即重发"）
        while time.time() < deadline:
            # 登录界面可能被「非注册用户」注册提示等模态弹窗挡着：不点掉它，
            # 登录按钮点不动、账号不会自动登录，交易窗口永远不出现（2026-09-12
            # 用户真机实测）。所以"等窗口"期间顺带把这类干扰弹窗点掉。
            self.dismiss_blocking_dialogs(verbose=False)
            # 同花顺自己不会自动登录（实测干等 140 秒无动静）：停在登录界面
            # 超过阈值就替人点一下[登录]（密码由同花顺自己保存填写）。
            self.assist_login(verbose=False)
            hwnd = find_window_by_title(THS_TITLE_KEYWORD)
            if hwnd:
                # 窗口在：就绪就直接收工；否则（还在初始化/登录）**不要**打扰它。
                window_seen = True
                if main_window_ready(hwnd):
                    ready = True
                    break
            else:
                # 窗口不在：分两种情况，避免一律干等 resend_gap 白白浪费一次自愈机会。
                if window_seen:
                    # 交易窗口曾出现、此刻又消失（典型就是"F12 发早了、行情还在
                    # 登录、交易模块被拉起随即自退"）：**立即**重发 F12，不要等。
                    # 这正是用户反馈"第一次拉起又马上关闭、要等很久才拉第二次"的根因。
                    log.info("交易窗口出现过后又消失，立即重发 F12 重新拉起")
                    ok, wh = self._relaunch_trading_module()
                    window_seen = False
                    last_resend = time.time()
                    if ok:
                        hwnd, ready = wh, True
                        break
                elif time.time() - last_resend >= resend_gap:
                    # 从未出现过：按原节流间隔重发，避免 F12 发太密相互干扰。
                    ok, wh = self._relaunch_trading_module()
                    last_resend = time.time()
                    if ok:
                        hwnd, ready = wh, True
                        break
            time.sleep(1)
        if ready:
            force_foreground(hwnd)
            self._hwnd = hwnd
            time.sleep(0.5)
            # 此刻交易窗口已确认就绪=界面级正常：上报 ok，把此前可能累计的
            # 界面异常计数清零（例如上一轮 _launch_main 曾报过 f12_launch_fail）。
            self._ui_ok()
            log.info("同花顺已自动启动，交易窗口已就绪（hwnd=%s）", hwnd)
            # 交易窗口就绪后，把行情主程序窗口缩到角落，避免大行情窗口遮挡交易界面
            # （窗口保持存活，会话/行情订阅通道不断，F12 拉起路径仍然有效）。
            self._park_main_window()
            return True
        if hwnd:
            # 窗口在但还没就绪：**不要**在这里 force_foreground 打扰它初始化，
            # 也不要谎报"已就绪"；交给 connect 的就绪门槛继续等。
            self._hwnd = hwnd
            st = main_window_status(hwnd)
            log.warning("同花顺已启动，但 %s 秒内交易窗口仍未就绪（%s），"
                        "继续等待其完成登录/初始化",
                        config.THS_AUTOSTART_TIMEOUT, st)
            # 只有"假死"才算界面级异常（该重启）；
            # "仍在初始化 / 缩在托盘"是登录过程的正常过渡态（要几十秒），
            # 上报会把正在登录的同花顺反复重启 —— 反而永远登不上。
            if st == "交易窗口未响应（假死）":
                self._ui_error("window_hung",
                               "交易窗口存在但消息循环无响应: %s" % _describe_window(hwnd))
            return True
        log.error("自动启动同花顺后 %s 秒内仍未出现交易窗口",
                  config.THS_AUTOSTART_TIMEOUT)
        # 必须上报看门狗。此前这里只 log.error 就 return False，"拉不起下单程序"
        # 这条路径**一次都不上报**，看门狗完全看不见 → 现场表现就是
        # "拉不起下单程序，看门狗也没起作用"（2026-09-13 用户反馈）。
        self._ui_error("launch_fail",
                       "启动同花顺后 %s 秒内仍未出现交易窗口（拉不起下单程序）"
                       % config.THS_AUTOSTART_TIMEOUT)
        return False

    def _park_main_window(self):
        """交易窗口就绪后，把行情主程序(hexin)窗口缩到很小并挪到屏幕角落。

        为什么（用户 2026-09-13 要求）：行情主程序窗口往往占满半屏，会盖住交易
        界面、也容易被误点。但它必须**保持存活**——交易模块是从它按 F12 拉起、
        复用它的登录会话与行情订阅通道（见 _launch_main）。所以只缩小+挪角落，
        不关闭、不最小化（最小化状态下 F12 同样收不到，见 _window_usable）。
        可用配置：THS_PARK_MAIN_WINDOW(默认1=开)、THS_PARK_MAIN_SIZE(默认100)。
        """
        if not _cfg_int("THS_PARK_MAIN_WINDOW", 1):
            return
        main_path = getattr(config, "THS_MAIN_PATH", "")
        if not main_path or not os.path.isfile(main_path):
            return
        size = max(40, _cfg_int("THS_PARK_MAIN_SIZE", 100))
        try:
            import win32gui, win32con, win32api
        except Exception as e:
            log.warning("缩小行情主程序窗口失败（缺 win32 模块: %s）", e)
            return
        hwnd = _pick_main_window(main_path, 0, main_frame_only=True)
        if not hwnd:
            # 退一步：取该进程面积最大的顶层窗口
            try:
                wins = _windows_of_path(main_path)
            except Exception:
                wins = []
            best, best_area = 0, -1
            for w in wins:
                try:
                    l, t, r, b = win32gui.GetWindowRect(w)
                    area = (r - l) * (b - t)
                except Exception:
                    area = 0
                if area > best_area:
                    best_area, best = area, w
            hwnd = best
        if not hwnd:
            return
        try:
            sx = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            sy = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        except Exception:
            sx, sy = 1920, 1080
        x = max(0, sx - size - 4)
        y = max(0, sy - size - 4)
        try:
            win32gui.SetWindowPos(
                hwnd, 0, x, y, size, size,
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
            log.info("行情主程序窗口已缩小并移至角落（%dx%d @ %d,%d，hwnd=%s）",
                     size, size, x, y, hwnd)
        except Exception as e:
            log.warning("缩小行情主程序窗口失败（%s），不影响交易", e)

    def dismiss_blocking_dialogs(self, verbose=True):
        """点掉同花顺的「干扰弹窗」（如登录界面的"非注册用户"注册提示）。

        为什么必须有这一步：执行端自动拉起同花顺后，登录界面会先弹一个模态的
        注册提示（正文"系统检测到您当前还不是同花顺注册用户…"，按钮
        [立即注册]/[取消]）。不点掉它，登录表单点不动、账号不会自动登录，
        交易窗口「网上股票交易系统5.0」永远不出现 —— 表现就是"同花顺被拉起来
        了但被弹窗挡住、执行端识别不了"；人工点一下[取消]立刻正常
        （2026-09-12 用户真机实测 + 截图）。

        实现见 dialog_guard 模块：纯 win32 枚举，**不依赖 self.user** —— 这个
        弹窗出现在登录之前，那时还没有已连接会话，captcha.py 那条路必失效。
        没有命中弹窗时是只读扫描、不做任何操作（幂等，可放心反复调用）。
        返回本次关闭的弹窗条数。
        """
        if not getattr(config, "THS_AUTODISMISS", True):
            return 0
        try:
            import dialog_guard
            recs = dialog_guard.dismiss_interference_dialogs(
                self.xiadan_path, verbose=verbose)
            return len(recs or [])
        except Exception as e:
            if verbose:
                log.warning("处理同花顺干扰弹窗时出错（忽略，不影响连接）: %s", e)
            return 0

    def assist_login(self, verbose=True):
        """同花顺停在登录界面时，替人把登录提交掉（见 dialog_guard.assist_login）。

        真机实测（2026-09-12）：同花顺拉起后停在登录窗口**不会自动登录**，
        干等 140 秒都没动静；而交易密码已由同花顺自己保存并填好，替人点一下
        [登录] 立刻登录成功（主框架"网上股票交易系统5.0"变可见）。
        另有用户反馈"这个界面按 Alt+F4 就能自动登录，不用点[登录]"，故默认先试
        Alt+F4 再点[登录]（顺序可用 EXECUTOR_THS_LOGIN_TRIGGERS 调整）。
        之所以不把 Alt+F4 当唯一手段：它本质是"关闭窗口"，落在同花顺主框架上会把
        整个程序关掉（实测进程消失），必须"发了之后确认登录窗口是否真的消失"。
        密码始终由同花顺保管，执行端不接触。
        """
        if not getattr(config, "THS_LOGIN_ASSIST", True):
            return {"state": "off", "message": "自动登录辅助已关闭", "attempts": 0}
        try:
            import dialog_guard
            return dialog_guard.assist_login(
                self.xiadan_path,
                idle_seconds=getattr(config, "THS_LOGIN_ASSIST_IDLE", 20),
                max_attempts=getattr(config, "THS_LOGIN_ASSIST_MAX", 3),
                triggers=getattr(config, "THS_LOGIN_TRIGGERS", None),
                trigger_wait=getattr(config, "THS_LOGIN_TRIGGER_WAIT", None),
                verbose=verbose)
        except Exception as e:
            if verbose:
                log.warning("登录辅助出错（忽略，不影响连接）: %s", e)
            return {"state": "error", "message": str(e), "attempts": 0}

    def close_captcha_dialog(self):
        """
        检测验证码弹窗并处理：
        1) 优先用 ddddocr 自动识别填入（见 captcha.py，实测识别率高）；
        2) 自动识别失败会蜂鸣+存图转人工，90 秒内人工输入也认；
        3) 全都不行才点[取消]关闭弹窗，并记录时间用于降频。
        返回 True=出现过弹窗。
        """
        from captcha import handle_captcha
        result = handle_captcha(self.user)
        if result == "none":
            return False
        self._last_captcha_ts = time.time()
        time.sleep(0.3)
        # 验证码未解决（自动识别 5 轮失败 + 转人工仍失败/放弃）→ 界面级异常，
        # 计入看门狗（高频验证码连败=界面风暴态，累计到阈值触发自动重启）。
        if result in ("failed", "closed"):
            self._ui_error("captcha_fail", "验证码未解决(%s)" % result)
        return True

    def at_login_screen(self):
        """同花顺是否还停在登录界面（标题空、含 站点列表:/交易密码:/[登录]）。"""
        try:
            import dialog_guard
            return dialog_guard.find_login_window(self.xiadan_path) is not None
        except Exception:
            return False

    def connect(self, verbose=True):
        if easytrader is None:
            raise RuntimeError(
                "未安装 easytrader，请执行: pip install easytrader pillow pywinauto==0.6.8")
        # 启动前确保同花顺已运行，未运行则自动拉起（重试场景用 verbose=False 降噪）
        self.ensure_started(verbose=verbose)
        # 登录界面上的"非注册用户"等模态弹窗要先点掉，否则登录表单点不动、
        # 账号不会自动登录（见 dismiss_blocking_dialogs）。
        self.dismiss_blocking_dialogs(verbose=verbose)
        # 同花顺自己不会自动登录：停在登录界面超过阈值就替人点[登录]。
        self.assist_login(verbose=verbose)
        # ── 关键：还停在登录界面时**绝不能**往下走 easytrader.connect() ──
        # easytrader 的 _close_prompt_windows() 会把"标题 != 网上股票交易系统5.0 的
        # 可见 #32770 窗口"一律 close()；而登录窗口标题恰好是空串 -> 被它关掉，
        # 同花顺随之退出（实测：进程消失）-> 下一轮 ensure_started 又拉起 ->
        # 就是用户看到的"反复拉起关闭"，且永远等不到登录成功
        # （2026-09-12 真机日志 11:02–11:05 每 16 秒重复一次）。
        # 正确顺序：先让登录完成（自动点[登录] / 人工登录），登录窗口消失后再连接。
        if self.at_login_screen():
            raise RuntimeError(
                "同花顺仍停在登录界面（正在自动代点[登录]，见 EXECUTOR_THS_LOGIN_ASSIST）"
                "；若需人工输入交易密码/验证码，请到本机登录界面处理后再等待重连")
        # ── 就绪体检（只做判断与告警，不再是硬门槛）──
        # 主框架「网上股票交易系统5.0」几秒就会出现，内部的工具栏/左侧菜单树
        # 要几十秒才初始化完。历史上正是"看到标题就当成已启动、立刻连接"导致
        # easytrader 的 _close_prompt_windows() 在同花顺初始化中途把它自己的
        # 窗口 WM_CLOSE 掉（**登录窗口标题是空串**，照样被关）：
        #   * 关掉登录窗口 -> 同花顺直接退出 -> 又被拉起 = "反复拉起关闭"；
        #   * 关在半初始化状态 -> 主窗口留着但内容空白、点了没反应 = 用户报的
        #     "假死"，而原生子控件文本还在（所以资金仍读得到）。
        # 2026-09-12 执行端日志特征：自动启动后 2.8 秒即报"已启动并恢复窗口"，
        # 紧接两条标题为空的 `close window`，随后 `No windows for that process
        # could be found`（进程已死）。
        # 现在**这条破坏性路径已被 patch_easytrader_prompt_windows 彻底拔掉**，
        # 所以"没完全就绪就先连"最多是连不上、由外层 wait_until_ready 5 秒后
        # 重试，不会再伤害同花顺。因此这里只对**真假死**硬拒（那需要人工/重启），
        # 其余情况如实记日志后照常尝试连接。
        status = main_window_status(find_window_by_title(THS_TITLE_KEYWORD))
        if status != "ready":
            if "假死" in status:
                raise RuntimeError(
                    "同花顺交易窗口未响应（假死）：%s。请在控制台点「重启同花顺」"
                    "恢复（本端已不再自动关它的窗口，可安全重试）" % status)
            log.info("同花顺交易窗口尚未完全就绪（%s），先尝试连接（失败会自动重试）",
                     status)
        # 关键顺序：必须先把同花顺从托盘恢复成「可见窗口」，再 connect。
        # easytrader.connect() 内部走 pywinauto 的 top_window()，它只认可见窗口；
        # 同花顺缩在托盘时主窗口是 ShowWindow(SW_HIDE) 隐藏的，会直接抛
        # "No windows for that process could be found" —— 导致无人值守/重启后
        # （新实例默认缩托盘）连不上。原实现把恢复动作放在 connect 之后，太晚。
        self._ensure_fg()
        self.user = easytrader.use("universal_client")   # 通用同花顺客户端模式
        # 告诉"接管版"的 _close_prompt_windows 别乱关（见 pywinauto_compat）：
        # 它需要知道同花顺安装目录，才能确保只碰同花顺自己的窗口。
        try:
            self.user._jq_xiadan_path = self.xiadan_path
        except Exception:
            pass
        # 新版同花顺不往临时目录导 Excel，必须用剪贴板(Ctrl+C)方式读表格
        self.user.grid_strategy = easytrader.grid_strategies.Copy
        self.user.connect(self.xiadan_path)
        # ── 关键修正（2026-09-12）：把 easytrader 主窗口重绑到交易对话框 ──
        # easytrader.connect() 内部用 self._app.top_window() 绑定 _main——它取
        # 该进程"当前活动/顶层"的窗口。工作目录修复后同花顺完整启动，进程里
        # 会出现空标题的 Afx 外壳框架等其它顶层窗口，top_window() 就会绑到
        # 外壳而不是交易对话框「网上股票交易系统5.0」(#32770)。左侧菜单树
        # （SysTreeView32 / control_id=129）只在交易对话框里，绑错后永远找不到，
        # 表现恰是"同花顺界面一切正常、执行端却报连接异常 {'control_id': 129,
        # 'class_name': 'SysTreeView32', ...}"。easytrader 自己的 login() 路径
        # 就是按标题精确绑定的（universal_clienttrader.py:59），这里对齐它。
        self._rebind_main_window()
        # 允许向编辑框输入文字（同花顺部分控件必须开启这个才能填价格/数量）
        self.user.enable_type_keys_for_editor()
        self._ensure_fg()   # 连接后再确保一次（防连接过程中被最小化/切走）

    def _rebind_main_window(self):
        """把 easytrader 的 _main 从 top_window() 重绑到交易对话框（按标题定位）。

        详见 connect() 内注释。任何失败都不抛出——重绑不上就保留原绑定，
        由外层 wait_until_ready 的重试兜底（宁可不完美，不可引入新故障）。
        """
        try:
            user = self.user
            app = getattr(user, "_app", None)
            if app is None:
                return
            main = None
            hwnd = find_window_by_title(THS_TITLE_KEYWORD)
            if hwnd:
                try:
                    main = app.window(handle=hwnd)   # 句柄在连接的进程里才有效
                except Exception:
                    main = None
            if main is None:
                try:   # 兜底：按标题在该进程里找（等价 easytrader login() 的绑定方式）
                    main = app.window(title="网上股票交易系统5.0")
                except Exception:
                    main = None
            if main is None:
                log.warning("未能把 easytrader 主窗口重绑到交易对话框，保留 top_window() 绑定")
                return
            user._main = main
            try:
                user._init_toolbar()   # _toolbar 是从 _main 派生的，必须跟着重建
            except Exception as e:
                log.warning("重绑主窗口后重建工具栏失败（不影响后续重试）: %s", e)
            log.info("easytrader 主窗口已重绑到交易对话框（修正 top_window() 绑到外壳框架的问题）")
        except Exception as e:
            log.warning("重绑 easytrader 主窗口失败（保留原绑定，等待自动重试）: %s", e)

    def wait_until_ready(self, timeout=None):
        """循环 connect + health_check，直到同花顺就绪（已登录）或超时。

        与 main.ensure_broker_ready 的等待语义一致，供「重启同花顺」等在线场景复用：
        同花顺重启后需要几十秒自动登录，期间 connect 必然失败；若只尝试一次就
        报错，就会出现"重启后不能正确识别、无法后续操作"。返回 (ok, message)。
        """
        if timeout is None:
            timeout = getattr(config, "THS_LOGIN_WAIT", 180)
        deadline = time.time() + timeout
        last = "未就绪"
        started = time.time()
        next_log = 0.0
        while time.time() < deadline:
            try:
                self.connect(verbose=False)   # verbose=False：不重复刷"已在运行"
                ok, msg = self.health_check()
                if ok:
                    return True, msg
                last = msg
            except Exception as e:
                last = str(e)
            # 首轮完整打印一次原因（便于定位），之后每 20 秒报一次进度，避免刷屏
            now = time.time()
            if now >= next_log:
                waited = int(now - started)
                if waited == 0:
                    log.warning("同花顺尚未就绪（%s），继续等待登录...", last)
                else:
                    log.warning("同花顺尚未就绪（已等待 %d 秒）: %s", waited, last)
                next_log = now + 20
            time.sleep(5)
        return False, last

    # ---------------- 页面切换 ----------------

    def _switch_page(self, direction):
        """切到下单页：buy -> 买入[F1]，sell -> 卖出[F2]。

        为什么必须显式切页：下单表单（代码1032/价格1033/数量1034/买入1006/
        重填1007）**只在买入/卖出页存在**。停在查询/撤单等页面时，同 ID 的 Edit
        控件会解析到别处、输入全部落空（2026-09-11 实测），而且 [重填](1007)
        根本不存在 —— 清场会直接报"未找到[重填]按钮"（2026-09-12 实测任务 #124）。
        返回 True 表示切页动作已发出；菜单名不同的版本按"可能已在正确页面"放行。
        """
        if self.user is None:
            return False
        names = ["买入[F1]"] if direction == "buy" else ["卖出[F2]"]
        keys = "{F1}" if direction == "buy" else "{F2}"
        try:
            self.user._switch_left_menus(names)
            return True
        except Exception:
            pass
        try:
            self.user._switch_left_menus_by_shortcut(keys)
            return True
        except Exception:
            return False

    # ---------------- 交易 ----------------

    def _trade(self, direction, jq_code, price, amount):
        code = to_plain_code(jq_code)
        price = normalize_price(jq_code, price)   # 规范价位，避免同花顺弹"小数部分应为2位"确认框
        if price is None:
            # 市价信号（price 为空/0/非法）：取最新价下单，取不到就拒绝——绝不带错价提交
            price = market_price.fetch_latest(code)
            if price is None:
                return self._result(False, "市价信号且取不到最新价（%s），已拒绝下单" % code)
            price = normalize_price(jq_code, price)
        self._ensure_fg()
        self.close_captcha_dialog()
        # 关键：查询类操作会把界面切到「查询/撤单」页，而下单表单只在买入/卖出页。
        # 停在查询页时同 ID 的 Edit 控件会解析到别处，输入全部落空、委托静默不提交
        # （2026-09-11 实测）。easytrader 的 buy/sell 内部虽也切页，但这里再
        # 显式切一次（幂等、零成本），保证表单页先就位。
        self._switch_page(direction)
        # 本次下单的起始时刻：供 _verify_submitted 只认"此时刻之后"的委托，
        # 避免把当天更早的历史委托（尤其是已撤单）当成本次提交成功。
        t_submit = time.time()
        try:
            if direction == "buy":
                r = self.user.buy(security=code, price=price, amount=amount)
            else:
                r = self.user.sell(security=code, price=price, amount=amount)
            entrust_no = ""
            if isinstance(r, dict):
                entrust_no = r.get("entrust_no", "") or ""
            if not entrust_no:
                # 弹窗标题为空导致 easytrader 拿不到编号时，用补丁从
                # 「委托已成功提交，合同编号：XXX」弹窗正文里提取的编号兜底
                entrust_no = str(getattr(self.user, "_last_entrust_no", "") or "")
                try:
                    self.user._last_entrust_no = ""     # 用完即清，防止串号
                except Exception:
                    pass
            if entrust_no:
                self._ui_ok()
                return self._result(True, "已通过同花顺客户端提交 %s %s x%s @%s"
                                    % (direction, code, amount, price), entrust_no)
            # 拿不到合同编号≠成功：同花顺对「价格偏离较大/废单风险」弹确认框，
            # easytrader 弹窗处理器点掉后 buy/sell 正常返回——必须实证查当日委托
            verified_no = self._verify_submitted(code, price, amount, direction,
                                                 since=t_submit - 30)
            if verified_no:
                self._ui_ok()
                return self._result(True, "已提交 %s %s x%s @%s（合同编号 %s，弹窗后补证）"
                                    % (direction, code, amount, price, verified_no),
                                    verified_no)
            return self._result(
                False, "委托疑似未提交（无合同编号且当日委托查无此单）。"
                       "常见原因：同花顺弹确认框被自动点掉/券商拒绝。"
                       "请人工核对当日委托后再决定是否重发，切勿盲目重发。")
        except Exception as e:
            # 界面级异常上报看门狗（累计到阈值时由 main 自动重启同花顺）。
            # 注意：只报"界面类"失败——风控拒绝/券商明确拒单不会走到这里（它们
            # 经弹窗返回、不抛异常），所以不会把高频信号的风控拒绝误计成界面异常。
            if self._is_modal_stuck():
                self._ui_error("modal_stuck", "下单失败时仍卡在模态弹窗")
                self._send_esc()          # 只到 ESC 层，绝不自动重启
                self._ensure_fg()
            elif not isinstance(e, ths_watchdog.UiNotReadyError):
                # "名称/价格未登记"(UiNotReadyError) 已在提交自检里上报，不重复计数；
                # 其余异常（控件找不到/超时等）同属界面异常。
                self._ui_error("trade_retry", "下单失败待重试: %s" % str(e)[:120])
            # 常见失败：客户端弹确认框/控件定位失败/界面未就绪。**重建下单页**后重试
            # 一次：切页可强制同花顺重绘表单、重置焦点，比单纯 refresh 更能治
            # "代码查不出名称/价格"（验证码打断后焦点错位的情形）。
            try:
                self._ensure_fg()
                self.close_captcha_dialog()
                self._switch_page(direction)
                time.sleep(0.6)
                if direction == "buy":
                    r = self.user.buy(security=code, price=price, amount=amount)
                else:
                    r = self.user.sell(security=code, price=price, amount=amount)
                entrust_no = r.get("entrust_no", "") if isinstance(r, dict) else ""
                if not entrust_no:
                    entrust_no = str(getattr(self.user, "_last_entrust_no", "") or "")
                    try:
                        self.user._last_entrust_no = ""
                    except Exception:
                        pass
                if entrust_no:
                    self._ui_ok()
                    return self._result(True, "重试后提交成功(首次失败:%s)" % e, entrust_no)
                verified_no = self._verify_submitted(code, price, amount, direction,
                                                     since=t_submit - 30)
                if verified_no:
                    self._ui_ok()
                    return self._result(True, "重试后提交成功(首次失败:%s，合同编号 %s)"
                                        % (e, verified_no), verified_no)
                return self._result(False, "同花顺下单失败(首次:%s)，重试后仍未查到委托，"
                                    "请人工核对当日委托" % e)
            except Exception as e2:
                return self._result(False, "同花顺下单失败: %s / 重试也失败: %s" % (e, e2))

    @staticmethod
    def _entrust_time_ts(row):
        """当日委托行的「委托时间」转成时间戳；解析不了返回 None。

        同花顺当日委托的委托时间形如 "18:24:55"（只有时分秒、没有日期），
        按"今天"补全日期后再比较，用于判断"这行是不是本次刚下的单"。
        """
        raw = str(row.get("委托时间") or "").strip()
        if not raw:
            return None
        for fmt in ("%H:%M:%S", "%H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.datetime.strptime(raw, fmt)
            except ValueError:
                continue
            if dt.year == 1900:                    # 只给了时分秒 -> 补今天的日期
                now = datetime.datetime.now()
                dt = dt.replace(year=now.year, month=now.month, day=now.day)
            return dt.timestamp()
        return None

    @staticmethod
    def _is_cancelled_row(row):
        """该行是否为已撤单记录（同花顺当日委托会保留已撤记录）。"""
        if "撤" in str(row.get("备注") or ""):
            return True
        try:
            return float(row.get("撤消数量") or 0) > 0
        except (TypeError, ValueError):
            return False

    def _verify_submitted(self, code, price, amount, direction, timeout=12, since=None):
        """当日委托里实证查找"刚提交"的委托（代码+买卖+价格+数量全匹配）。

        找到返回合同编号字符串；找不到返回 ''。

        为什么还要过滤（2026-09-13 真实测试踩坑，导致"未提交却报成功"）：
          1. **必须跳过已撤单记录**。同花顺当日委托会保留已撤记录（备注="全部撤单"、
             撤消数量>0）。同一策略反复用"同代码同价同量"跑多轮时，第 1 轮那笔已撤
             委托会**永久满足匹配**，后续每轮都匹配到它 → 全部假成功。实测第 2 轮
             510500 实际根本没提交（当日委托里查无此单、全撤只撤到 9 笔），却因匹配
             到第 1 轮已撤的那笔 510500 而返回"下单成功"。
          2. **必须只看本次下单时段**。委托时间要 ≥ since（默认取调用前 120 秒），
             防止匹配到当天更早的历史委托。
          时间戳解析不出来时**保守判为未提交**（宁可报"疑似未提交"让人工核对，
          也不能把没下的单报成成功）。
        """
        if since is None:
            since = time.time() - 120
        deadline = time.time() + timeout
        want_op = "买" if direction == "buy" else "卖"
        while time.time() < deadline:
            try:
                rows = self.query_entrusts() or []
            except Exception:
                rows = []
            for r in rows:
                try:
                    if not (code in str(r.get("证券代码", "")) and
                            want_op in str(r.get("操作", "")) and
                            abs(float(r.get("委托价格") or 0) - float(price)) < 1e-6 and
                            int(float(r.get("委托数量") or 0)) == int(amount)):
                        continue
                except (TypeError, ValueError):
                    continue
                if self._is_cancelled_row(r):
                    continue                      # 已撤单不能证明"本次提交成功"
                ts = self._entrust_time_ts(r)
                if ts is None:
                    if not ThsBroker._verify_ts_warned:
                        ThsBroker._verify_ts_warned = True
                        log.warning("当日委托缺少可解析的「委托时间」(收到 %r)，"
                                    "无法按时间校验新委托，已保守判为未提交",
                                    r.get("委托时间"))
                    continue
                if ts < since:
                    continue
                return str(r.get("合同编号") or "")
            time.sleep(2)
        return ""

    def buy(self, jq_code, price, amount):
        return self._trade("buy", jq_code, price, amount)

    def sell(self, jq_code, price, amount):
        return self._trade("sell", jq_code, price, amount)

    @staticmethod
    def _is_alive_entrust(row):
        """当日委托里的某行是否仍是"活单"（可撤）。

        关键：同花顺当日委托**保留已撤记录**（备注="全部撤单"、撤消数量>0），
        所以判断"是否还有活委托"绝不能看行数，要综合 成交数量/撤消数量/备注。
        （2026-09-12 实测：15 行里只有 1 行是活单）
        解析不了的行保守当作活单（宁可多撤一次，不可漏撤）。
        """
        remark = str(row.get("备注") or "")
        if "撤" in remark:
            return False
        try:
            qty = float(row.get("委托数量") or 0)
            done = float(row.get("成交数量") or 0)
            cancel = float(row.get("撤消数量") or 0)
        except (TypeError, ValueError):
            return True
        return (done + cancel) < qty

    def _read_cancellable(self):
        """读"可撤委托"，并与当日委托交叉验证，返回 (rows, inconclusive)。

        为什么要交叉验证（2026-09-12 实测）：刚提交完委托时，同花顺的委托/撤单
        表格会有一段时间读到空（测试里连读 6 秒都是 0 行）。若直接把"0 行"当真，
        一键全撤就会报"无可撤委托，无需操作"——**假成功**，用户以为已撤干净、
        实际还有活单在券商那边。
        inconclusive=True 表示"撤单页读到 0 行、但当日委托里还有活单"，
        即读取不可靠，调用方绝不能据此报成功。
        """
        rows = self._read_with_retry(lambda: self.user.cancel_entrusts, "撤单")
        if rows:
            return rows, False
        alive = []
        try:
            alive = [r for r in (self.query_entrusts() or [])
                     if self._is_alive_entrust(r)]
        except Exception:
            pass
        return [], bool(alive)

    def cancel_order(self, entrust_no):
        """
        撤单（安全路径）：
        读撤单页表格 -> 必须恰好找到目标委托且可撤委托唯一 -> 点[全撤]。
        （easytrader 自带 cancel_entrust 的双击定位在新版同花顺控件上实测失效）
        """
        self._ensure_fg()
        self.close_captcha_dialog()
        try:
            rows, inconclusive = self._read_cancellable()
            if inconclusive:
                return self._result(
                    False, "撤单页没读到可撤委托，但当日委托里还有活动委托："
                           "表格读取不可靠，为安全未执行全撤，请稍后重试或人工核对")
            matching = [r for r in (rows or [])
                        if str(r.get("合同编号")) == str(entrust_no)]
            if len(rows or []) != 1 or len(matching) != 1:
                return self._result(
                    False, "撤单页可撤委托=%d行、匹配目标=%d行，非唯一，为安全不执行全撤"
                    % (len(rows or []), len(matching)))
            self.user.cancel_all_entrusts()
            return self._result(True, "已通过[全撤]撤销委托 %s" % entrust_no)
        except Exception as e:
            self.close_captcha_dialog()
            return self._result(False, "撤单失败: %s" % e)

    def cancel_all_orders(self):
        """
        一键全撤（安全路径）：
        读撤单页 -> 无可撤委托直接返回；有则点同花顺[全撤]按钮，点完再复核一次。
        与 cancel_order 同一页面同一按钮，只是不做"唯一性"校验——全撤本来就要全撤，
        点[全撤]前无需人工挑选委托号，误撤风险仅限"当日可撤委托"范围。

        2026-09-12 加固：① 读取与当日委托交叉验证，避免"读到 0 行"被当成
        "没有委托可撤"（假成功）；② 点完[全撤]后复核，确认真的撤干净了才报成功。
        """
        self._ensure_fg()
        self.close_captcha_dialog()
        try:
            rows, inconclusive = self._read_cancellable()
            if inconclusive:
                return self._result(
                    False, "撤单页没读到可撤委托，但当日委托里还有活动委托："
                           "表格读取不可靠，为安全未执行全撤，请稍后重试或人工核对")
            n = len(rows or [])
            if n == 0:
                return self._result(True, "撤单页无可撤委托，无需操作")
            self.user.cancel_all_entrusts()
            # 复核：撤单页应清空、且当日委托里不再有活单
            time.sleep(1.0)
            left, inconclusive2 = self._read_cancellable()
            if left or inconclusive2:
                return self._result(
                    False, "已点[全撤]，但复核时仍有活动委托（撤单页 %d 行）——"
                           "请人工核对是否已全部撤销" % len(left or []))
            return self._result(True, "已通过[全撤]撤销 %d 笔可撤委托（已复核）" % n)
        except Exception as e:
            self.close_captcha_dialog()
            return self._result(False, "一键全撤失败: %s" % e)

    # ---------------- 清场 / 重启 / 假死自愈 ----------------

    def _find_button(self, control_id, text=None):
        """按 control_id 精确查找按钮（实现在 pywinauto_compat.find_button）。

        坑：pywinauto 0.6.8 的 `child_window(control_id=..)` / `descendants(control_id=..)`
        会静默忽略 control_id 过滤条件，导致明明存在(如[重填] 1007)却报找不到。
        必须枚举 Button 后「在 Python 侧」按 element_info.control_id 手工过滤
        （与 pywinauto_compat 的输入控件处理同源）。优先可见者；text 给定时
        优先文本匹配者。"""
        try:
            return pywinauto_compat.find_button(
                getattr(self.user, "_main", None), control_id, text=text)
        except Exception:
            return None

    def _clear_inputs_by_keyboard(self):
        """键盘清空 代码(1032)/价格(1033)/数量(1034) 三个输入框。

        清场兜底手段（当版本差异导致找不到 [重填] 按钮时用）。与下单输入同源：
        必须真实键盘事件（set_focus + 退格），WM_SETTEXT 只改显示不触发 THS
        登记回调（见 pywinauto_compat）。返回清空动作成功发出的框数量。
        """
        main = getattr(self.user, "_main", None)
        if main is None:
            return 0
        done = 0
        for cid in (1032, 1033, 1034):
            try:
                editors = [e for e in main.descendants(class_name="Edit")
                           if int(getattr(e.element_info, "control_id", -1)) == cid
                           and e.is_visible()]
            except Exception:
                editors = []
            for ed in editors:
                try:
                    ed.set_focus()
                    time.sleep(0.12)
                    ed.type_keys("{BACKSPACE 20}", with_spaces=False)
                    done += 1
                    break
                except Exception:
                    continue
        return done

    def clear_form(self):
        """清场：切到买入页后点[重填]清空下单表单（REFILL_BTN_ID=1007）。

        2026-09-12 实测坑（任务 #124）：[重填] 只存在于买入/卖出页，同花顺停在
        其它页时原实现直接报"未找到[重填]按钮(1007)"。现在先显式切到买入页、
        等页面控件渲染、最多找 3 次；万一仍没有（版本差异）就退化为键盘清空
        代码/价格/数量三个输入框。
        """
        if self.user is None or getattr(self.user, "_main", None) is None:
            return self._result(False, "同花顺未连接，清场失败（请先连接券商）")
        self._ensure_fg()
        self._switch_page("buy")        # [重填] 只在买入/卖出页存在
        time.sleep(0.6)                 # 等切页后的控件渲染完成，避免读到旧控件树
        try:
            btn = None
            for _ in range(3):
                btn = self._find_button(1007, text="重填")
                if btn is not None:
                    break
                time.sleep(0.5)
            if btn is not None:
                try:
                    btn.click_input()   # 真实鼠标点击（BM_CLICK 对 THS 可能不生效）
                except Exception:
                    btn.click()
                return self._result(True, "已清空下单表单（切到买入页后点[重填]）")
            n = self._clear_inputs_by_keyboard()
            if n:
                return self._result(True, "已清空下单表单（键盘清空 %d 个输入框）" % n)
            return self._result(False,
                                "未找到[重填]按钮(1007)，键盘清空也未生效，"
                                "请确认同花顺处于下单页")
        except Exception as e:
            return self._result(False, "清场失败: %s" % e)

    def _is_modal_stuck(self):
        """是否卡在某个模态弹窗（对话框）。用于下单失败时的轻量自愈判断。"""
        try:
            return bool(self.user.is_exist_pop_dialog())
        except Exception:
            return False

    def _send_esc(self):
        """仅对真正的模态对话框(#32770)发 ESC 关闭；绝不触碰交易表单。"""
        try:
            top = self.user._app.top_window()
        except Exception:
            return False
        if top is None or top.class_name() != "#32770":
            return False
        try:
            top.type_keys("{ESC}", set_foreground=False)
            return True
        except Exception:
            return False

    def restart_ths(self):
        """重启同花顺（行情主程序 + 交易模块一起重启）。

        **已在运行则先把行情和下单程序都结束、再完整重拉；未在运行则直接启动。**

        main_f12 模式下默认「先起行情主程序(hexin.exe)、再按 F12 拉交易模块(xiadan.exe)」
        这套**完整重拉**路径 —— 用户明确要求"重启执行端时应把行情和下单软件一起重启"，
        而不是只重拉交易模块（只重拉 xiadan 复用的还是旧行情会话，若行情自身异常则
        F12 拉不起；二者一起重拉能保证拿到一套全新、干净的避风控会话）。
        standalone 模式下仍直接启动下单程序 xiadan.exe（旧方式）。

        这是控制台的"远程恢复"按钮与看门狗自动重启的共同入口：同花顺被关掉、卡死、
        缩在托盘连不上时，管理员不想到现场就点它 / 看门狗触发它。因此有三条硬要求
        （2026-09-12 用户需求）：
          1) 不依赖已有连接：同花顺从未启动过 / 从未连上（self.user 为 None）时
             也必须能跑 —— 所以不再先调 close_captcha_dialog（它需要 self.user，
             旧实现在这种情况下会直接抛异常，等于"识别不到就彻底没救"）。
          2) 未运行 -> 直接启动：不再"识别不到就什么都不做"。
          3) 进程探测失败 -> 跳过结束进程但仍强制启动，并如实记日志。
        最后等待其自动登录并重连成功（wait_until_ready）。

        注意：常规「自动自愈」(_relaunch_trading_module / ensure_started) 仍走更轻量的
        "只杀交易模块 + 重发 F12"，避免在交易时段误杀整行情；只有本方法（显式重启
        / 看门狗）才会连行情一起重启。
        """
        running = False
        pids = []
        try:
            pids = _pids_of_xiadan(self.xiadan_path)
            running = bool(pids)
        except Exception as e:
            log.warning("重启前进程探测失败（%s）：跳过结束进程，直接启动", e)

        if running:
            if config.THS_LAUNCH_MODE == "standalone":
                killed = _kill_by_path(self.xiadan_path)
                log.info("同花顺正在运行（PID %s），结束进程后重启", killed or pids)
            else:
                # main_f12：连行情主程序(hexin)和交易模块(xiadan)一起结束，再完整重拉。
                # 用户要求"重启执行端时应把行情和下单软件一起重启"，而不是只重拉交易模块
                # （只重拉 xiadan 复用的还是旧行情会话，若行情自身异常则 F12 拉不起）。
                main_path = getattr(config, "THS_MAIN_PATH", "")
                killed_x = _kill_by_path(self.xiadan_path)
                killed_m = _kill_by_path(main_path) if main_path else []
                log.info("已结束交易模块（PID %s）与行情主程序（PID %s），稍后完整重拉",
                         killed_x or pids, killed_m)
            # 等同花顺完全退出再启动：否则其单实例保护可能把新进程合并到旧实例上，
            # 出现"点了重启但其实没换进程"的假重启。
            deadline = time.time() + 10
            while time.time() < deadline:
                try:
                    alive = _pids_of_xiadan(self.xiadan_path)
                    if config.THS_LAUNCH_MODE == "main_f12":
                        mp = getattr(config, "THS_MAIN_PATH", "")
                        if mp:
                            alive = alive or _pids_of_xiadan(mp)
                    if not alive:
                        break
                except Exception:
                    break
                time.sleep(0.5)
        else:
            if config.THS_LAUNCH_MODE == "main_f12":
                log.info("同花顺未在运行（或未能识别），直接完整启动"
                         "（行情主程序 + F12 拉交易模块）")
            else:
                log.info("同花顺下单程序未在运行（或未能识别），直接启动: %s",
                         self.xiadan_path)

        if config.THS_LAUNCH_MODE == "standalone":
            # standalone：直接启动下单程序 xiadan.exe（工作目录=安装目录）。
            if not self._launch_standalone():
                return self._result(
                    False, "启动同花顺失败（启动程序异常），请检查路径: %s"
                    % self.xiadan_path)
        else:
            # main_f12：完整重拉 —— 先起行情主程序、再按 F12 拉交易模块。
            # force_restart_main=True 兜底：万一行情主程序没被上面杀干净，这里连它
            # 一起结束再重启，保证"行情+下单"是同一套全新会话（避风控）。
            if not self._launch_main(force_restart_main=True):
                return self._result(
                    False,
                    "启动同花顺失败（行情主程序与交易模块完整重拉仍失败），"
                    "请检查路径: %s" % getattr(config, "THS_MAIN_PATH", ""))

        # THS 启动较慢，且默认可能缩在托盘。先轮询定位主窗口（交易窗口关键字，
        # 托盘里的隐藏窗口也能命中），并且**只在它"真就绪"后**才 force_foreground
        # / 交给 wait_until_ready 连接：主框架几秒就出现但内部还要几十秒，
        # 提前连接会被 easytrader 关掉同花顺自己的窗口（见 connect 的说明）。
        deadline = time.time() + config.THS_AUTOSTART_TIMEOUT
        new_hwnd = 0
        ready = False
        while time.time() < deadline:
            self.dismiss_blocking_dialogs(verbose=False)   # 别让"注册提示"挡住自动登录
            self.assist_login(verbose=False)               # 同花顺不会自己登录：替人点[登录]
            new_hwnd = find_window_by_title(THS_TITLE_KEYWORD)
            if new_hwnd and main_window_ready(new_hwnd):
                ready = True
                break
            time.sleep(1)
        if ready:
            force_foreground(new_hwnd)
            self._hwnd = new_hwnd
            time.sleep(0.5)
        else:
            log.warning("启动后 %s 秒内交易窗口尚未就绪（%s），继续等待其完成登录/初始化",
                        config.THS_AUTOSTART_TIMEOUT,
                        main_window_status(new_hwnd))
        # 启动后同花顺要几十秒自动登录，期间 connect 必然失败：这里循环等待就绪，
        # 而不是只试一次就报"重连失败"（否则重启按钮永远报错、无法无人值守）。
        ok, msg = self.wait_until_ready(timeout=config.THS_RESTART_WAIT)
        if not ok:
            return self._result(False,
                                "已启动同花顺，但等待其就绪超时: %s（请确认能自动登录）" % msg)
        # 与 ensure_started 行为对齐：重启成功、交易窗口连上后，同样把行情主程序
        # 窗口缩到角落（2026-09-13 用户反馈"控制台点重启同花顺后行情窗口没有缩角"
        # —— 此前缩角只挂在 ensure_started，restart_ths 这条路径漏了）。
        self._park_main_window()
        return self._result(True,
                            "已重启同花顺下单程序并连接成功" if running
                            else "已启动同花顺下单程序并连接成功")

    # ---------------- 查询 ----------------

    @staticmethod
    def _clear_clipboard():
        """清空剪贴板：防止同花顺复制失败时把陈旧剪贴板内容误当表格数据。"""
        try:
            import win32clipboard
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.CloseClipboard()
        except Exception:
            pass

    @staticmethod
    def _valid_rows(rows):
        """校验读回的表格是不是真实交易数据（过滤控制台文本等剪贴板垃圾）。"""
        if not rows:
            return False
        sample = "".join(list(rows[0].keys()) + [str(v) for v in rows[0].values()])
        for bad in ("====", "一键启动", "聚宽中转", "启动中转", "pip", "Flask"):
            if bad in sample:
                return False   # 命中垃圾特征整批作废
        return True

    @staticmethod
    def _drop_empty_rows(rows):
        """过滤全空行：剪贴板读表格偶尔混入'合同编号/证券代码全空'的垃圾行
        （2026-09-11 实测当日委托出现 3 行全空），会污染去重/对账/撤单匹配。"""
        out = []
        for r in rows or []:
            if any(str(v).strip() not in ("", "0", "0.0", "None", "None.")
                   for v in r.values()):
                out.append(r)
        return out

    def _read_with_retry(self, reader, name):
        """查询通用骨架：清剪贴板 -> 读 -> 校验 -> (遇到验证码先解决) 重试。

        界面异常上报（看门狗）：读表抛异常（如剪贴板 "That format is not
        available"）或连续读到疑似垃圾数据 → 记一次界面异常；读到有效表格 → 记为
        "界面正常"。**纯空结果不算异常**（可能是真的没有数据，如空持仓/无委托），
        否则正常的空查询会被误判成界面故障、把同花顺重启到疯。
        """
        rows = []
        garbage = False
        for attempt in range(4):
            self._ensure_fg()
            try:
                self.user.refresh()
            except Exception:
                pass
            self.close_captcha_dialog()   # 弹窗出现的第一时间用模拟人工方式解决
            self._clear_clipboard()
            try:
                rows = list(reader() or [])
            except Exception as e:
                if attempt == 3:
                    self._ui_error("read_error", "查询 %s 读取异常: %s" % (name, e))
                    raise
                rows = []
            rows = self._drop_empty_rows(rows)
            if self._valid_rows(rows):
                self._ui_ok()
                return rows
            if rows:                 # 非空但被判为垃圾（剪贴板混入控制台文本等）
                garbage = True
            time.sleep(1.5)
        if garbage:
            self._ui_error("read_garbage", "查询 %s 连续读到疑似垃圾数据" % name)
        return rows   # 多轮都是空/垃圾也如实返回（调用方按空数据处理）

    def query_positions(self):
        return self._read_with_retry(lambda: self.user.position, "持仓")

    def query_balance(self):
        rows = self._read_with_retry(lambda: [self.user.balance], "资金")
        return dict(rows[0]) if rows else {}

    def query_entrusts(self):
        """当日委托（Web 控制台任务用）。"""
        return self._read_with_retry(lambda: self.user.today_entrusts, "当日委托")

    def query_trades(self):
        """当日成交（Web 控制台任务用）。"""
        return self._read_with_retry(lambda: self.user.today_trades, "当日成交")

    def cooldown_needed(self):
        """距离上次验证码不足 120 秒时返回剩余秒数（调用方可决定是否降频）。"""
        remain = 120 - (time.time() - self._last_captcha_ts)
        return max(0, remain)


def _cfg_int(name, default):
    """读 config 里的整数项；该项缺失/非法时退回默认值。

    为什么不用 config.XXX 直取：离线测试常用 SimpleNamespace 裁剪版的 config 桩，
    直取新加的配置项会 AttributeError 把整条路径打断（2026-09-13 实测）。
    """
    try:
        return int(getattr(config, name, default))
    except Exception:
        return default


def _windows_of_path(exe_path, visible_only=True):
    """返回该 EXE 路径所属进程的顶层窗口句柄列表（按窗口面积从大到小）。

    用途：定位「行情主程序 hexin.exe」的主窗口 —— 它没有固定的标题关键字，
    只能按 exe 路径找；行情主窗口是该进程里面积最大的那个可见顶层窗口。
    与 _pids_of_xiadan 同样走 win32（沙箱拦截 wmic、venv 无 psutil）。
    """
    import win32gui, win32process, win32api
    target = os.path.normcase(os.path.abspath(exe_path))
    hits = []

    def _cb(h, _):
        try:
            if visible_only and not win32gui.IsWindowVisible(h):
                return True
            pid = win32process.GetWindowThreadProcessId(h)[1]
            if not pid:
                return True
            hp = win32api.OpenProcess(0x0400 | 0x0010, False, pid)   # QUERY_INFORMATION|VM_READ
            try:
                p = win32process.GetModuleFileNameEx(hp, 0)
            finally:
                win32api.CloseHandle(hp)
            if os.path.normcase(os.path.abspath(p)) == target:
                l, t, r, b = win32gui.GetWindowRect(h)
                hits.append((max(0, r - l) * max(0, b - t), h))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    hits.sort(key=lambda x: x[0], reverse=True)
    return [h for _, h in hits]


def _window_area(hwnd):
    """窗口面积(px²)；取不到返回 0。"""
    try:
        import win32gui
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return max(0, r - l) * max(0, b - t)
    except Exception:
        return 0


def _window_responds(hwnd, timeout_ms=800):
    """窗口的消息循环是否有响应（WM_NULL + SendMessageTimeout）。

    为什么不用 IsHungAppWindow：系统要等约 5 秒才把窗口标成"无响应"，
    而 hexin 冷启动时主框架已经画出来了、消息循环还没转起来，那几秒里
    IsHungAppWindow 仍返回 False（不认为是卡死），但发过去的 F12 会被丢掉。
    带 800ms 超时的 SendMessageTimeout 能更早、更准地识别这种"半启动"状态。
    探测不可用时返回 True（不阻断，退化为旧行为）。
    """
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        res = ctypes.c_size_t(0)
        SMTO_BLOCK, SMTO_ABORTIFHUNG, WM_NULL = 0x0001, 0x0002, 0x0000
        ok = user32.SendMessageTimeoutW(
            wintypes.HWND(hwnd), WM_NULL, 0, 0,
            SMTO_BLOCK | SMTO_ABORTIFHUNG, int(timeout_ms), ctypes.byref(res))
        return bool(ok)
    except Exception:
        return True


# 判断"这个窗口是不是行情主程序的真正主框架"用的特征（2026-09-13 新增）：
#   * #32770 是 Windows 标准对话框类 —— 同花顺的「登录到全部行情主站」「提示」
#     「注册」这些过渡框都是它；
#   * 真主框架：旧版 MFC（Afx:xxxxxxxx:b:...，标题形如「同花顺(9.60.60) - 自选股」）、
#     新版 Chromium（Chrome_WidgetWin_1）。
_DIALOG_CLASSES = ("#32770",)
_MAIN_FRAME_PREFIXES = ("Afx:", "Chrome_WidgetWin_")
_MAIN_FRAME_TITLE_BLOCK = ("登录", "提示", "注册", "Menu", "划词", "资讯中心", "复制识别")


def _looks_like_main_frame(hwnd):
    """像不像"行情主程序的真正主框架窗口"（而不是登录框/闪屏/工具窗）。

    为什么必须区分（2026-09-13 用 tools/ths_launch_trace.py 抓到的决定性证据）：
        19:27:05 把 F12 发给了行情主程序的**登录对话框**
                 （#32770 / title='登录到全部行情主站' / 600x430）——
            → xiadan.exe 19:27:08 启动，19:27:11 就自己退出（窗口 resp=False），
              60 秒后交易窗口消失、easytrader 报 Process ... not found。
        19:28:44 等 hexin 真正主窗口出来后再发 F12
                 （Afx:... / title='同花顺(9.60.60) - 自选股' / 1934x1054）——
            → xiadan 稳定存活，4 秒后「券商连接自检: True 连接正常」。
    结论：行情还在登录阶段时，交易模块即使被 F12 拉起来也会随即自退；必须等主框架。

    探测不到类名/标题时返回 True（不阻断）—— 兼容不同版本与离线测试桩。
    """
    try:
        import win32gui
        cls = win32gui.GetClassName(hwnd) or ""
        title = win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return True
    if cls in _DIALOG_CLASSES:
        return False
    if any(k in title for k in _MAIN_FRAME_TITLE_BLOCK):
        return False
    if cls.startswith(_MAIN_FRAME_PREFIXES):
        return True
    return True          # 未知类名：不因"认不出"而否定（版本兼容优先）


def _window_usable(hwnd, min_area=0, main_frame_only=False):
    """窗口是否"可用"：存在 + 可见 + 未最小化 + 面积达标 + 消息循环有响应。

    main_frame_only=True 时额外要求它像"行情主程序的主框架"（滤掉登录框/闪屏）。
    """
    try:
        import win32gui
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False
        if not win32gui.IsWindowVisible(hwnd):
            return False
        if win32gui.IsIconic(hwnd):        # 最小化状态下 F12 同样收不到
            return False
        if min_area and _window_area(hwnd) < min_area:
            return False
        if main_frame_only and not _looks_like_main_frame(hwnd):
            return False
        return _window_responds(hwnd)
    except Exception:
        return False


def _pick_main_window(exe_path, min_area=0, main_frame_only=False):
    """从该 exe 的顶层窗口里挑第一个"可用"的（面积降序）；没有则返回 0。"""
    try:
        wins = _windows_of_path(exe_path)
    except Exception:
        wins = []
    for h in wins:
        if _window_usable(h, min_area, main_frame_only=main_frame_only):
            return h
    return 0


def _window_exe(hwnd):
    """该窗口所属进程的 EXE 完整路径（探测不到返回 ""）。"""
    try:
        import win32process, win32api
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if not pid:
            return ""
        h = win32api.OpenProcess(0x0400 | 0x0010, False, pid)   # QUERY_INFORMATION|VM_READ
        try:
            return win32process.GetModuleFileNameEx(h, 0)
        finally:
            win32api.CloseHandle(h)
    except Exception:
        return ""


def _describe_window(hwnd):
    """把窗口描述成一行短文本（归属EXE/类名/标题/尺寸），让日志能自证。

    2026-09-13 教训：日志只写"行情主窗口已就绪（hwnd=1115898）"，事后根本
    无法判断它到底是行情主窗、登录框还是闪屏 —— 排查只能靠猜。带上这几个
    字段后，日志自己就能说清"当时发 F12 的对象是谁"。
    """
    try:
        import win32gui
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return "%s class=%s title=%r %sx%s" % (
            os.path.basename(_window_exe(hwnd)) or "?",
            win32gui.GetClassName(hwnd),
            (win32gui.GetWindowText(hwnd) or "")[:32],
            max(0, r - l), max(0, b - t))
    except Exception:
        return "?"


def _confirm_trading_window(timeout=6.0, stable=2, interval=1.0):
    """确认交易窗口"真的起来了"（而不是闪现一下），返回 (hwnd, 归属EXE)。

    为什么不能"命中一次就算成功"（2026-09-13 现场实测）：
        19:12:03 日志写「F12 已拉起交易窗口」，
        19:13:03 又写「自动启动同花顺后 60 秒内仍未出现交易窗口」
        —— 一真一假。说明同标题窗口可能刚创建就被销毁（交易模块自检后退出/
        被行情端重置）。此时执行端若报"成功"，上游会以为单子挂上了，实际什么都没
        发生；而且因为"没报失败"，看门狗也收不到任何异常上报（= 瞎的）。

    判据：同一句柄连续 stable 次采样都命中；中途消失/换句柄即判失败，
    由调用方重发 F12 或升级为深度恢复。
    """
    deadline = time.time() + max(1.0, float(timeout))
    hwnd = 0
    streak = 0
    while time.time() < deadline:
        try:
            cur = find_window_by_title(THS_TITLE_KEYWORD)
        except Exception:
            cur = 0
        if cur and cur == hwnd:
            if not _window_responds(cur, 600):
                # 窗口在但消息循环不响应 —— 典型"刚被拉起来就准备自退"的状态
                # （2026-09-13 实测：xiadan 窗口 resp=False，3.5 秒后进程消失）。
                # 这种不算成功，继续等/让调用方重发 F12。
                hwnd, streak = 0, 0
                time.sleep(max(0.2, interval))
                continue
            streak += 1
            if streak >= max(1, stable):
                return hwnd, _window_exe(hwnd)
        elif cur:
            hwnd, streak = cur, 1
        else:
            hwnd, streak = 0, 0
        time.sleep(max(0.2, interval))
    return 0, ""


def _wait_main_window(exe_path, timeout, min_area=None, stable=None):
    """等「行情主窗口真正可用」（可安全接受 F12），返回其 hwnd；超时返回 0。

    为什么不能只看"窗口出现了"（2026-09-13 用户反馈"行情还没完全启动就发 F12，
    有时候拉不起下单程序"）：hexin 冷启动会先冒出闪屏/登录框，主窗口句柄随后
    还会变，此时发 F12 等于发给一个即将销毁的窗口 —— F12 就这么丢了。
    就绪判据（全部满足）：
      1) 可见、未最小化、面积 ≥ min_area（滤掉闪屏/小提示窗）；
      2) 消息循环有响应（见 _window_responds）；
      3) 尺寸连续 stable 次采样不变（布局已定型，不再切换/重画）。
    每轮都重新枚举，句柄变化（闪屏 → 登录窗 → 主窗）能自愈。
    """
    min_area = _cfg_int("THS_MAIN_MIN_AREA", 120000) if min_area is None else min_area
    stable = _cfg_int("THS_MAIN_READY_STABLE", 3) if stable is None else stable
    deadline = time.time() + max(3.0, float(timeout))
    last_rect = None
    streak = 0
    while time.time() < deadline:
        # 只认"主框架"：登录框（#32770 '登录到全部行情主站'）也可见、也够大、
        # 也稳定，但它出现时行情还在登录 —— 此时发 F12，交易模块会被拉起来
        # 随即自退（2026-09-13 实测，见 _looks_like_main_frame 的说明）。
        hwnd = _pick_main_window(exe_path, min_area, main_frame_only=True)
        if hwnd:
            try:
                import win32gui
                rect = tuple(win32gui.GetWindowRect(hwnd))
            except Exception:
                rect = None
            if rect is not None and rect == last_rect:
                streak += 1
            else:
                last_rect = rect
                streak = 1
            if streak >= max(1, stable):
                return hwnd
        else:
            last_rect = None
            streak = 0
        time.sleep(0.5)
    # 等不到"像主框架"的窗口就返回 0（调用方会打警告并仍按 F12 → 由"确认窗口
    # 稳定且响应"的重试逻辑兜底）。这里**故意不做"退而求其次挑任意窗口"**：
    # 那会削弱"尺寸稳定才算就绪"的保证，把还在乱变布局的窗口也当成就绪。
    # 版本兼容由 _looks_like_main_frame 负责（认不出的类名不否定）。
    return 0


def _foreground_matches(hwnd):
    """当前前台窗口是否就是该窗口（或其所属顶层窗口）。

    F12 只作用于前台窗口：置前后必须复核，否则按键会打进别的程序 ——
    既拉不起交易模块，也可能在无关程序里乱按（F12 在不少软件里有别的含义）。

    注意**只拦"确知前台是别的窗口"**这一种情况：
      * GetForegroundWindow() 返回 0（锁屏 / 远程桌面断开 / 会话无活动桌面，
        2026-09-13 实测本机就出现过）时不能据此判失败 —— 那会把"可能成功"
        直接变成"必然失败"，永远发不出 F12。此时返回 True（放行，退化为旧行为）。
      * 探测本身抛异常同样放行。
    """
    try:
        import win32gui
        fg = win32gui.GetForegroundWindow()
        if not fg:
            return True         # 无前台窗口：无从判断，不阻断
        if fg == hwnd:
            return True
        GA_ROOTOWNER = 3
        root = win32gui.GetAncestor(hwnd, GA_ROOTOWNER)
        return bool(root) and (fg == root
                               or win32gui.GetAncestor(fg, GA_ROOTOWNER) == root)
    except Exception:
        return True     # 探测不了就不阻断（退化为旧的"发了就算"行为）


def _send_f12(hwnd=0):
    """向同花顺行情主窗口发送 F12（= 手动按 F12 打开交易/下单模块）。

    2026-09-13 加固（用户反馈"行情还没完全启动就发 F12，有时拉不起下单程序"）：
    置前之后**复核前台窗口**，只有确认焦点真的落在行情主窗口上才注入按键；
    否则返回 False，让调用方重新定位窗口后重试 —— 而不是把 F12 打进别的程序。

    F12 是"当前前台窗口"的快捷键，后台窗口收不到，所以先置前再发。
    键盘注入用 pywinauto.keyboard.send_keys（与验证码输入同一套通路，
    已被 pywinauto_compat 的 SendInput 逐事件补丁加固）。
    传 hwnd=0 时退化为"发给当前前台窗口"（不做复核）。
    """
    if not hwnd:
        try:
            from pywinauto.keyboard import send_keys
            send_keys("{F12}")
            return True
        except Exception as e:
            log.warning("发送 F12 失败: %s", e)
            return False
    try:
        force_foreground(hwnd)
        time.sleep(0.3)
        if not _foreground_matches(hwnd):
            log.warning("F12 未发送：置前后前台窗口不是行情主窗口（hwnd=%s），"
                        "可能行情还没起来或窗口句柄已失效", hwnd)
            return False
        from pywinauto.keyboard import send_keys
        send_keys("{F12}")
        return True
    except Exception as e:
        log.warning("发送 F12 失败: %s", e)
        return False



def _pids_of_xiadan(xiadan_path):
    """返回所有「exe 路径落在该同花顺安装目录」的进程 PID（win32 实现）。

    不依赖 psutil/wmic：    用 EnumWindows 收集所有可见/隐藏(托盘)窗口的所属 PID，
    再比对每个 PID 的 exe 完整路径是否「精确等于」xiadan_path（与原本 psutil
    的语义一致，避免目录前缀匹配误伤同目录下的其它版本，例如新版
    xiadan-plus 进程落在同花顺 xiadan-plus 子目录下、却被旧版目录前缀命中）。
    沙箱拦截了 wmic、venv 未装 psutil，此实现是稳定通路。"""
    import win32gui, win32process, win32api
    target = xiadan_path.lower()
    pids = set()

    def _cb(h, _):
        try:
            pid = win32process.GetWindowThreadProcessId(h)[1]
            if pid:
                pids.add(pid)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    hit = []
    for pid in pids:
        try:
            h = win32api.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY_INFORMATION | VM_READ
            path = win32process.GetModuleFileNameEx(h, 0).lower()
            win32api.CloseHandle(h)
            if path == target:
                hit.append(pid)
        except Exception:
            continue
    return hit


def _kill_by_path(xiadan_path):
    """按 EXE 所在目录精确结束相关进程（兼容新旧双版本；win32 实现，绕开 wmic）。"""
    import win32api, win32con
    killed = []
    for pid in _pids_of_xiadan(xiadan_path):
        try:
            h = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
            win32api.TerminateProcess(h, 0)
            win32api.CloseHandle(h)
            killed.append(pid)
        except Exception:
            continue
    return killed


def _process_running_by_path(xiadan_path):
    """判断该 EXE 路径的进程是否已在运行（win32 实现，不依赖 psutil/wmic）。"""
    return bool(_pids_of_xiadan(xiadan_path))


if __name__ == "__main__":
    # 单独调试本模块：python broker_ths.py
    import config as cfg
    b = ThsBroker(cfg.THS_XIADAN_PATH)
    b.connect()
    print("资金:", b.query_balance())
    print("持仓:", b.query_positions())
