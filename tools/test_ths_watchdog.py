# -*- coding: utf-8 -*-
"""ths_watchdog 逻辑单测（纯逻辑，不碰 GUI / 同花顺 / 网络）。

跑法：venv/Scripts/python.exe tools/test_ths_watchdog.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXEC = os.path.join(os.path.dirname(HERE), "executor")
sys.path.insert(0, EXEC)
os.environ.setdefault("JQ_ENVFILE_NOCREATE", "1")

import ths_watchdog                      # noqa: E402
from ths_watchdog import UiWatchdog      # noqa: E402

PASS = []
FAIL = []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


def new_wd(**kw):
    base = dict(window=60, consecutive=3, threshold=5,
                cooldown=120, max_per_hour=3, enabled=True)
    base.update(kw)
    return UiWatchdog(**base)


# 1) 连续阈值触发
wd = new_wd()
check("初始不触发", wd.should_restart() is False)
wd.report_ui_error("not_registered")
wd.report_ui_error("not_registered")
check("连续2次不触发", wd.should_restart() is False)
wd.report_ui_error("not_registered")
check("连续3次触发", wd.should_restart() is True)

# 2) 成功清零连续计数
wd2 = new_wd()
wd2.report_ui_error("a")
wd2.report_ui_error("a")
wd2.report_ui_ok()
wd2.report_ui_error("a")
check("成功清零连续计数后不触发", wd2.should_restart() is False)

# 3) 窗口阈值（连续被成功打断，但窗口累计到 5）
wd3 = new_wd(consecutive=99, threshold=5)
for _ in range(5):
    wd3.report_ui_error("b")
    wd3.report_ui_ok()          # 打断连续
check("窗口内5次触发(连续被打断)", wd3.should_restart() is True)

# 4) 未上报就不计数（纯空结果由 broker 保证不误报）
wd4 = new_wd()
check("未上报不触发且不计数",
      wd4.should_restart() is False and wd4.error_count() == 0)

# 5) 重启互斥
wd5 = new_wd()
wd5.report_ui_error("x")
wd5.report_ui_error("x")
wd5.report_ui_error("x")
check("begin_restart 首次 True", wd5.begin_restart() is True)
check("互斥中 should_restart False", wd5.should_restart() is False)
check("begin_restart 二次 False", wd5.begin_restart() is False)
wd5.end_restart(True)

# 6) 冷却期
check("重启后计数清零",
      wd5.consecutive() == 0 and wd5.error_count() == 0)
wd5.report_ui_error("x")
wd5.report_ui_error("x")
wd5.report_ui_error("x")
check("冷却期内不触发", wd5.should_restart() is False)

# 7) 每小时上限
wd6 = new_wd(cooldown=0)
for _ in range(3):
    wd6.report_ui_error("x")
    wd6.report_ui_error("x")
    wd6.report_ui_error("x")
    assert wd6.begin_restart()
    wd6.end_restart(True)
check("达每小时上限 limit_exceeded", wd6.limit_exceeded() is True)
wd6.report_ui_error("x")
wd6.report_ui_error("x")
wd6.report_ui_error("x")
check("超上限不再触发", wd6.should_restart() is False)

# 8) 总开关关闭
wd7 = new_wd(enabled=False)
wd7.report_ui_error("x")
wd7.report_ui_error("x")
wd7.report_ui_error("x")
check("enabled=False 不触发", wd7.should_restart() is False)

# 9) 异常类型（便于 broker 精准识别"界面未就绪"）
check("UiNotReadyError 是 RuntimeError 子类",
      issubclass(ths_watchdog.UiNotReadyError, RuntimeError))

# 10) 心跳状态文案
wd8 = new_wd()
check("无异常时 state_suffix 为空", wd8.state_suffix() == "")
wd8.begin_restart()
check("重启中 state_suffix", wd8.state_suffix() == "·看门狗重启中")
wd8.end_restart(True)
check("冷却中 state_suffix", wd8.state_suffix() == "·看门狗冷却")

print("\n结果：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
