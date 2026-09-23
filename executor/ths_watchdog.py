# -*- coding: utf-8 -*-
"""
同花顺「界面级异常」看门狗（非侵入式独立模块，2026-09-13）。

与 failure_guard 的分工（务必分清，否则会误伤）：
  * failure_guard 管**业务失败**（风控拒绝 / 券商拒单 / 信号异常）→ 退避 / 暂停 / 熔断；
  * 本模块只管**界面级异常**（填完代码读不出证券名称/价格、控件找不到、
    剪贴板读不到表格、模态弹窗卡死、验证码连续失败、**行情主程序/交易模块拉不起来**
    即 launch_fail / f12_launch_fail）→ 累计到阈值后**自动重启同花顺**
    （由 main 主循环调用 broker.restart_ths()）。

为什么必须区分：高频信号会带来大量"风控拒绝"（价格偏差/持仓不足），那是**信号
本身**的问题，重启同花顺毫无帮助；若把它们计入，会在几秒内冲破阈值、把同花顺
重启到疯（= 重启风暴），比现状更糟。所以本模块**只**接受 broker 界面层的显式上报
（report_ui_error），业务失败一律不进这里。

安全阀（缺一不可，防重启风暴）：
  * 阈值：连续 N 次 或 窗口 M 秒内 ≥ K 次界面异常 → 触发；
  * 互斥：同一时刻只允许一个重启流程（begin_restart / end_restart）；
  * 冷却：重启完成后 COOLDOWN 秒内不再自动触发；
  * 上限：每小时最多 MAX_PER_HOUR 次，超限只告警、不再自动重启；
  * 成功即清零连续计数（界面恢复时不误判）。

非侵入约定：不改主流程；broker 只在此调用 report_ui_error()/report_ui_ok()，
重启动作由 main 主循环（与信号处理同一线程，保证券商 UI 串行）执行。
"""
import time

try:
    import config          # 只在有 config 时读默认值；测试可传显式参数绕开
except Exception:          # pragma: no cover - 缺 config 时仍可独立实例化
    config = None


class UiNotReadyError(RuntimeError):
    """同花顺界面未就绪（如提交前自检发现"证券名称/可买股数"均未登记）。

    独立异常类型，便于 broker 在下单重试里精准识别"界面类失败"，
    而不会与"券商明确拒单"等业务失败混淆。
    """


def _cfg(name, default):
    try:
        return getattr(config, name, default)
    except Exception:
        return default


def _int_cfg(name, default):
    try:
        return int(getattr(config, name, default))
    except Exception:
        return default


class UiWatchdog(object):
    """界面异常计数器 + 重启决策器（纯逻辑、无副作用、可单测）。"""

    def __init__(self, window=None, consecutive=None, threshold=None,
                 cooldown=None, max_per_hour=None, enabled=None):
        self.window = int(window if window is not None
                          else _int_cfg("THS_WATCHDOG_WINDOW", 60))
        self.consecutive_limit = int(consecutive if consecutive is not None
                                     else _int_cfg("THS_WATCHDOG_CONSECUTIVE", 3))
        self.window_limit = int(threshold if threshold is not None
                                else _int_cfg("THS_WATCHDOG_THRESHOLD", 5))
        self.cooldown = int(cooldown if cooldown is not None
                            else _int_cfg("THS_WATCHDOG_COOLDOWN", 120))
        self.max_per_hour = int(max_per_hour if max_per_hour is not None
                                else _int_cfg("THS_WATCHDOG_MAX_PER_HOUR", 3))
        self.enabled = bool(_cfg("THS_WATCHDOG", True) if enabled is None
                            else enabled)

        self._errors = []        # 界面异常时间戳（窗口内）
        self._consecutive = 0    # 连续界面异常次数（成功/重启即清零）
        self._restarts = []      # 重启时间戳（1 小时窗口内）
        self._cooldown_until = 0.0
        self._rebooting = False
        self.limit_notified = False   # 超上限只告警一次（去重用）

        self.last_kind = ""
        self.last_detail = ""
        self.last_error_ts = 0.0

    # ---------------- 记录 ----------------

    def report_ui_error(self, kind, detail=""):
        """上报一次界面级异常（只有 broker 的界面异常点应调用）。返回连续次数。"""
        now = time.time()
        self._errors.append(now)
        self._prune(now)
        self._consecutive += 1
        self.last_kind = str(kind or "")
        self.last_detail = str(detail or "")[:200]
        self.last_error_ts = now
        return self._consecutive

    def report_ui_ok(self):
        """界面恢复正常（一次成功的查询/下单）：清零连续计数。

        注意只清连续计数、不清窗口计数 —— 窗口内的偶发异常靠自然过期，
        避免"偶发失败夹着一次成功"把真正的连续故障掩盖掉。
        """
        self._consecutive = 0

    # ---------------- 查询 ----------------

    def _prune(self, now):
        cutoff = now - self.window
        self._errors = [t for t in self._errors if t >= cutoff]

    def _prune_restarts(self, now):
        cutoff = now - 3600
        self._restarts = [t for t in self._restarts if t >= cutoff]

    def error_count(self):
        self._prune(time.time())
        return len(self._errors)

    def consecutive(self):
        return self._consecutive

    def restart_count_last_hour(self):
        self._prune_restarts(time.time())
        return len(self._restarts)

    def reason(self):
        """当前触发原因的可读描述（用于日志）。"""
        return "连续 %d 次 / 窗口内 %d 次（阈值 连续%d / 窗口%d），最近异常=%s" % (
            self._consecutive, self.error_count(),
            self.consecutive_limit, self.window_limit,
            self.last_kind or "-")

    def limit_exceeded(self):
        """本小时重启次数是否已达上限（达到后只告警、不再自动重启）。"""
        return self.restart_count_last_hour() >= self.max_per_hour

    def in_cooldown(self):
        return time.time() < self._cooldown_until

    def rebooting(self):
        return self._rebooting

    def should_restart(self):
        """是否应当触发重启（综合阈值/互斥/冷却/上限）。"""
        if not self.enabled or self._rebooting:
            return False
        now = time.time()
        if now < self._cooldown_until:
            return False
        hit = (self._consecutive >= self.consecutive_limit
               or self.error_count() >= self.window_limit)
        if not hit:
            return False
        if self.restart_count_last_hour() >= self.max_per_hour:
            return False
        return True

    def state_suffix(self):
        """给心跳文案用的后缀（"" / "·看门狗重启中" / "·看门狗冷却"）。"""
        if self._rebooting:
            return "·看门狗重启中"
        if self.in_cooldown():
            return "·看门狗冷却"
        return ""

    def status(self):
        return {
            "enabled": self.enabled,
            "consecutive": self._consecutive,
            "errors_in_window": self.error_count(),
            "window": self.window,
            "consecutive_limit": self.consecutive_limit,
            "window_limit": self.window_limit,
            "cooldown_remaining": max(0, int(self._cooldown_until - time.time())),
            "restarts_last_hour": self.restart_count_last_hour(),
            "max_per_hour": self.max_per_hour,
            "rebooting": self._rebooting,
            "last_kind": self.last_kind,
            "last_detail": self.last_detail,
        }

    # ---------------- 重启生命周期（互斥） ----------------

    def begin_restart(self):
        """占位：返回 False 表示已有重启在进行（互斥，防并发）。"""
        if self._rebooting:
            return False
        self._rebooting = True
        return True

    def end_restart(self, ok=True):
        """重启流程结束：记一次重启、进入冷却、清零计数。"""
        self._rebooting = False
        now = time.time()
        self._restarts.append(now)
        self._prune_restarts(now)
        self._cooldown_until = now + self.cooldown
        self._errors = []
        self._consecutive = 0
        self.limit_notified = False


# 模块级单例：broker 与 main 共用同一个计数器/决策器
watchdog = UiWatchdog()
