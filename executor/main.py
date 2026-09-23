# -*- coding: utf-8 -*-
"""
执行端主程序 —— 轮询中转服务，把信号交给风控和券商适配器执行，并回报结果。

处理流程（每个信号）：
  拉取信号 -> 风控校验 -> 下单 -> 回报 accepted(已提交)/failed(被拒/异常)
  成交确认(filled)由券商回报差异较大，本版本以"已提交"为准，
  成交状态请在券商端核对（后续版本可加成交查询闭环）。

启动：python main.py        （Ctrl+C 停止）
建议：dry_run 模式先跑通全链路，再切 MODE 到真实券商。
"""
import inspect
import json
import logging
import os
import sys
import threading
import time
import traceback

import requests

# 让 exe(PyInstaller onefile) 也能 import 到「exe 同级目录」下的第三方库（如 xtquant）。
# onefile 的 sys.path 只含临时解压目录 _MEIPASS，不含 exe 所在目录；手册指引的
# 「把 xtquant 文件夹复制到 exe 同目录」必须在这里手动补进 sys.path 才会生效（2026-09-15 修）。
_RUNTIME_DIR = (os.path.dirname(os.path.abspath(sys.executable))
                if getattr(sys, "frozen", False)
                else os.path.dirname(os.path.abspath(__file__)))
if _RUNTIME_DIR and _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)

# 打包自检时不要生成 .env，避免污染发布目录（必须在 import config 之前设置）
if os.environ.get("EXECUTOR_SELFCHECK") == "1":
    os.environ["JQ_ENVFILE_NOCREATE"] = "1"

import config
import risk
import tasks
import failure_guard
import ths_watchdog
from broker_base import to_plain_code

# 连续失败熔断实例（L0-L3）：主循环与信号处理共用同一把"闸"
guard = failure_guard.FailureGuard()

# ---------------- 日志 ----------------

def _log_file():
    """exe(frozen) 模式日志写到 exe 旁边；源码模式写当前目录。

    打包自检（EXECUTOR_SELFCHECK=1）时不往 exe 旁边落日志，
    避免在发布目录里留下构建期的 executor.log。
    """
    if os.environ.get("EXECUTOR_SELFCHECK") == "1":
        import tempfile
        return os.path.join(tempfile.gettempdir(), "jq_executor_selfcheck.log")
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "executor.log")
    return "executor.log"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(_log_file(), encoding="utf-8")])
log = logging.getLogger("executor")


# ---------------- 中转服务客户端 ----------------

class RelayClient(object):
    def __init__(self, base_url, api_key):
        self.base = base_url.rstrip("/")
        self.key = api_key
        # trust_env=False：不让系统代理环境变量劫持对中转服务（常为 127.0.0.1/内网）的请求
        self.session = requests.Session()
        self.session.trust_env = False
        # Connection: close：执行端长生命周期 Session 不复用 keep-alive 连接，
        # 避免 gunicorn 侧空闲连接被回收后，urllib3 复用到已断开的 socket 而抛
        # ConnectionResetError / RemoteDisconnected / ChunkedEncodingError（2026-09-14 加固）
        self.session.headers["Connection"] = "close"

    def _post(self, path, payload=None, timeout=15):
        r = self.session.post("%s%s" % (self.base, path),
                              json=payload or {},
                              headers={"X-API-Key": self.key},
                              timeout=timeout)
        r.raise_for_status()
        return r.json()

    def poll(self, limit=10):
        return self._post("/api/signal/poll", {"limit": limit}).get("signals", [])

    def feedback(self, signal_id, result, message=""):
        return self._post("/api/feedback",
                          {"signal_id": signal_id, "result": result,
                           "message": message})

    def heartbeat(self, name, timeout=15):
        return self._post("/api/heartbeat", {"name": name}, timeout=timeout)

    # ---- Web 控制台任务通道（查资金/持仓/委托/成交、下单、撤单）----

    def poll_tasks(self, limit=1):
        return self._post("/api/task/poll", {"limit": limit}).get("tasks", [])

    def task_feedback(self, task_id, ok, result=None, message=""):
        return self._post("/api/task/feedback",
                          {"task_id": task_id, "ok": bool(ok),
                           "result": result, "message": message})


# ---------------- 券商适配器工厂 ----------------

def build_broker():
    """按 config.MODE 创建对应的适配器；dry_run 返回 None（不下单）。

    只负责**构造**，不连接、不阻塞（原 create_broker 会在启动时等登录 180 秒、
    超时直接抛错退出，导致同花顺没起来时执行端整个下线、控制台连"重启同花顺"
    都点不了）。连接/重连交给主循环里的 ensure_broker_ready() 后台按需进行。
    """
    mode = config.MODE
    if mode == "dry_run":
        return None
    if mode == "ths":
        from broker_ths import ThsBroker
        return ThsBroker(config.THS_XIADAN_PATH)
    if mode == "thsauto":
        from broker_thsauto import ThsAutoBroker
        return ThsAutoBroker(config.THSAUTO_URL, config.THSAUTO_ADDR)
    if mode == "miniqmt":
        from broker_miniqmt import MiniQmtBroker
        return MiniQmtBroker(config.QMT_PATH, config.QMT_ACCOUNT_ID,
                             config.QMT_ACCOUNT_TYPE)
    raise ValueError("MODE 配置错误: %s（可选 dry_run/ths/thsauto/miniqmt）" % mode)


# ---------------- 券商就绪状态 · 后台心跳 · 连接自愈 ----------------

class BrokerSlot(object):
    """执行端与券商适配器的共享状态（主循环写、心跳线程只读）。

    核心约定：**执行端是否"在线"只取决于能否与中转服务通信，与同花顺是否
    已启动/已登录无关**。否则同花顺一旦没起来，控制台就显示"执行端离线"，
    连「重启同花顺」这个恢复按钮都会被 503 挡在门外 —— 只能跑到现场处理
    （2026-09-12 用户反馈的痛点）。
    """

    def __init__(self):
        self.broker = None
        self.build_error = ""          # 适配器构造失败的原因（构造失败也保持在线）
        # dry_run 不需要券商，天然就绪；ths/miniqmt 等要先连上才算就绪
        self.ready = (config.MODE == "dry_run")
        self.last_err = ""
        self.next_try = 0.0            # 下次连接/复查的时间戳（限频用）

    def readable(self):
        """给心跳文案用的简短状态（前端会原样显示在"执行端：xxx 在线"里）。"""
        if self.ready:
            return "dry_run" if config.MODE == "dry_run" else "券商已连接"
        return "同花顺未就绪"


def heartbeat_loop(slot, stop_event=None, relay=None):
    """后台心跳线程：独立于券商连接状态，持续上报"执行端在线"。

    为什么单独起线程：ensure_broker_ready() 里的 connect 可能阻塞数十秒
    （同花顺未运行时 ensure_started 要等窗口出现、重启后要等自动登录），
    若在主循环里顺序发心跳，这段时间控制台会误判"执行端离线"（前端阈值 30 秒）。
    心跳只发 HTTP、不碰 UI，与主循环无资源竞争（各自独立 Session）。
    """
    hb_relay = relay or RelayClient(config.RELAY_URL, config.EXECUTOR_API_KEY)
    interval = max(2, min(getattr(config, "HEARTBEAT_INTERVAL", 5), 20))
    while not (stop_event is not None and stop_event.is_set()):
        try:
            hb_relay.heartbeat(
                "executor-%s[%s]（%s%s）" % (config.MODE, guard.level_name(),
                                            slot.readable(),
                                            ths_watchdog.watchdog.state_suffix()),
                timeout=8)
        except Exception:
            pass      # 中转不可达时静默重试；主循环会对连接失败单独告警
        if stop_event is not None:
            stop_event.wait(interval)
        else:
            time.sleep(interval)


def _connect_broker(broker):
    """调用 broker.connect()，兼容不接受 verbose 参数的适配器。"""
    try:
        if "verbose" in inspect.signature(broker.connect).parameters:
            return broker.connect(verbose=False)     # ths 适配器：降噪日志
    except (TypeError, ValueError):
        pass
    return broker.connect()


def _recovery_hint():
    """券商未就绪时给用户的下一步指引 —— 按执行模式区分。

    旧文案一律写"可在控制台点「重启同花顺」恢复"，在 miniqmt/thsauto 模式下会
    误导用户去点一个与当前券商无关的按钮（该按钮在非 ths 模式会被直接拒绝），
    这正是"THS 与 miniqmt 互相干扰"的观感来源（2026-09-15 修）。
    """
    mode = getattr(config, "MODE", "")
    if mode == "miniqmt":
        return "请确认 miniQMT 极简客户端已启动并登录（执行端无法代其启动）"
    if mode == "thsauto":
        return "请确认雷电模拟器与 thsauto 服务已启动"
    if mode == "dry_run":
        return "dry_run 模式不连接券商"
    return "可在控制台点「重启同花顺」恢复"


def ensure_broker_ready(slot):
    """确保券商就绪（限频调用；失败不退出、稍后自动重试）。

    * 未就绪：每 BROKER_RETRY_INTERVAL 秒尝试一次 connect + health_check。
      connect 内部有 ensure_started（同花顺没运行会自动拉起），因此"同花顺
      没启动"也能自愈，无需人工干预。
    * 已就绪：按同一间隔复查，掉线（被关闭/崩溃/登出）自动转回未就绪并重连。
    单次尝试可能阻塞数十秒，故心跳由独立线程发送、执行端始终在线。
    """
    if slot.broker is None:
        return
    now = time.time()
    if now < slot.next_try:
        return
    interval = max(10, getattr(config, "BROKER_RETRY_INTERVAL", 20))
    try:
        if not slot.ready:
            _connect_broker(slot.broker)
        ok, msg = slot.broker.health_check()
    except Exception as e:
        ok, msg = False, str(e)
    if ok:
        if not slot.ready:
            log.info("券商连接自检: True %s", msg)
        slot.ready = True
        slot.last_err = ""
        slot.next_try = time.time() + interval
        return
    was_ready = slot.ready
    slot.ready = False
    slot.last_err = msg
    slot.next_try = time.time() + interval
    log.warning("券商%s（%s），%s 秒后重试；执行端保持在线。%s",
                "连接已断开" if was_ready else "尚未就绪", msg, interval,
                _recovery_hint())


def _maybe_watchdog_restart(slot):
    """界面级异常累计到阈值时，自动重启同花顺（仅 ths 模式）。

    与 failure_guard 的分工：本函数只处理**界面异常**（查不出名称/价格、控件
    找不到、剪贴板读不到、模态卡死、验证码连败），**不碰业务熔断**（风控拒绝
    不计入，故高频信号不会触发重启风暴）。

    执行位置：主循环线程内**同步**执行（券商 UI 操作必须串行，不能并发）。
    重启期间心跳线程照发、执行端保持在线；本轮不处理信号/任务，重启完成后
    下一轮继续。安全阀（互斥 / 冷却 / 每小时上限）由 ths_watchdog 把关。
    """
    if config.MODE != "ths" or slot.broker is None:
        return
    wd = ths_watchdog.watchdog
    if not wd.enabled:
        return
    if wd.limit_exceeded() and not wd.limit_notified:
        wd.limit_notified = True      # 只告警一次，避免刷屏
        log.error("看门狗：本小时自动重启同花顺已达上限（%d 次），暂停自动重启；"
                  "同花顺可能反复卡死或遭券商侧限制，请人工检查", wd.max_per_hour)
    if not wd.should_restart():
        return
    if not wd.begin_restart():        # 互斥：已有重启在进行
        return
    log.warning("看门狗触发：同花顺界面异常达阈值，自动重启同花顺...（%s）", wd.reason())
    ok, msg = False, ""
    try:
        r = slot.broker.restart_ths()
        ok = bool(r.get("ok"))
        msg = r.get("message", "")
    except Exception as e:
        ok, msg = False, str(e)
    wd.end_restart(ok)
    slot.next_try = 0.0               # 让下一轮 ensure_broker_ready 立即复查连接
    log.warning("看门狗自动重启同花顺：%s（%s）", "成功" if ok else "失败", msg)


# ---------------- 主循环 ----------------

def handle_signal(relay, broker, sig):
    """处理单条信号：风控 -> 下单 -> 回报。任何异常都不会让主循环退出。
    同时把成功/失败上报给熔断守卫（guard）：连续失败会升级 L1-L3，
    成功一次即清零该分类计数（L0-L2 自动降级）。"""
    sid = sig["id"]
    desc = "#%s %s %s %s x%s @%s" % (sid, sig["security"], sig["side"],
                                     sig.get("jq_status"), sig["amount"], sig.get("price"))
    log.info("收到信号 %s", desc)
    try:
        # dry_run：只打印不执行
        if broker is None:
            log.info("[DRY_RUN] 模拟执行 -> 风控校验 + 记录，不真实下单")
            ok, reason = risk.check(sig, broker=None)
            if ok:
                guard.record_success("signal")
                relay.feedback(sid, "accepted", "[DRY_RUN] " + reason)
                log.info("[DRY_RUN] 信号通过风控，已记为 accepted")
            else:
                guard.record("signal", exc=RuntimeError(reason))
                relay.feedback(sid, "failed", "[DRY_RUN] 风控拒绝: " + reason)
                log.warning("[DRY_RUN] 风控拒绝: %s", reason)
            return

        # 真实模式：风控 -> 下单
        ok, reason = risk.check(sig, broker=broker)
        if not ok:
            log.warning("风控拒绝 %s: %s", desc, reason)
            relay.feedback(sid, "failed", "风控拒绝: " + reason)
            return

        trade = broker.buy if sig["side"] == "buy" else broker.sell
        r = trade(sig["security"], sig.get("price"), int(sig["amount"]))
        risk._add_daily(int(sig["amount"]))   # 进入这里说明已尝试提交，计入当日额度
        if r.get("ok"):
            guard.record_success("signal")
            guard.record_success("trade")
            relay.feedback(sid, "accepted", r.get("message", ""))
            log.info("下单成功 %s -> %s [熔断等级 %s]", desc, r.get("message"), guard.level_name())
        else:
            errmsg = r.get("message", "未知失败")
            guard.record("signal", exc=Exception(errmsg))
            guard.record("trade", exc=Exception(errmsg))
            relay.feedback(sid, "failed", errmsg)
            log.error("下单失败 %s -> %s [熔断等级 %s]", desc, errmsg, guard.level_name())
    except Exception as e:
        guard.record("signal", exc=e)
        guard.record("trade", exc=e)
        log.error("处理信号异常 %s [熔断等级 %s]:\n%s", desc, guard.level_name(),
                  traceback.format_exc())
        try:
            relay.feedback(sid, "failed", "执行端异常: %s" % e)
        except Exception:
            log.error("回报失败且无法恢复，该信号 # %s 已标记 dispatched 不再重试", sid)


def handle_task(relay, runner, task):
    """处理一条 Web 控制台任务并把结果回报给中转服务（异常也不会让主循环退出）。"""
    tid = task.get("id")
    try:
        ok, result, message = runner.handle(task)
    except Exception as e:
        log.error("控制台任务 #%s 异常: %s", tid, e)
        ok, result, message = False, None, "执行端异常: %s" % e
    try:
        relay.task_feedback(tid, ok, result, message)
    except Exception as e:
        log.error("控制台任务 #%s 回报失败（控制台会在超时后放弃）: %s", tid, e)


def _selfcheck():
    """打包自检：只做模块导入（不连中转、不连券商），验证依赖确实打进了 exe。

    重点验证三类最容易漏打的东西：
      1) 券商适配器能否 import（easytrader 若没打进，broker_ths.easytrader 会是 None）；
      2) ddddocr 能否导入并加载模型（onnxruntime + cv2 + *.onnx 模型是否收全）；
      3) pywinauto / pywin32 是否可用。
    """
    import importlib

    for m in ("broker_base", "broker_ths", "broker_thsauto", "broker_miniqmt",
              "captcha", "pywinauto_compat", "foreground", "risk", "tasks"):
        importlib.import_module(m)
    import broker_ths
    assert getattr(broker_ths, "easytrader", None) is not None, "easytrader 未成功打包"
    import pywinauto          # noqa: F401
    import win32gui           # noqa: F401
    import ddddocr
    ddddocr.DdddOcr(show_ad=False)         # 真正加载 *.onnx 模型，验证模型文件已收全
    print("SELFCHECK_OK version=%s mode=%s" % (config.VERSION, config.MODE))


def main():
    # 「执行端-配置.bat」用它查看当前生效的配置（打印完就退出，不连中转、不下单）
    if "--show-config" in sys.argv:
        print(config.startup_report())
        return

    if os.environ.get("EXECUTOR_SELFCHECK") == "1":
        _selfcheck()
        return

    # 启动摘要：把生效的配置和来源打印出来（密钥已脱敏），方便用户确认自己改的配置生效了
    for line in config.startup_report().splitlines():
        log.info(line)
    relay = RelayClient(config.RELAY_URL, config.EXECUTOR_API_KEY)
    slot = BrokerSlot()
    # 1) 先起后台心跳线程：让"执行端在线"与同花顺状态彻底解耦。
    #    之后无论是构造适配器，还是等同花顺启动/登录，控制台都看得到执行端。
    threading.Thread(target=heartbeat_loop, args=(slot,),
                     name="executor-heartbeat", daemon=True).start()
    # 2) 构造适配器（不连接）。构造失败也不退出：执行端继续在线，
    #    控制台仍能看到它，并可通过任务通道（如"重启同花顺"）继续处置。
    try:
        slot.broker = build_broker()
    except Exception as e:
        slot.build_error = str(e)
        slot.ready = False
        log.error("券商适配器初始化失败（执行端保持在线，可继续使用控制台任务通道）: %s", e)

    runner = tasks.TaskRunner(          # Web 控制台任务执行器
        slot.broker, guard=guard,       # 传入熔断守卫，使"解除熔断"按钮生效
        dry_run=(config.MODE == "dry_run"),
        ready_probe=lambda: (slot.ready, slot.last_err or slot.build_error))
    ensure_broker_ready(slot)           # 首次连接尝试（dry_run 直接跳过）
    log.info("初始化完成，进入轮询循环（Ctrl+C 停止）")

    while True:
        try:
            relay_reachable = True
            # 1) 控制台任务优先：发起人在页面上等着结果，优先响应。
            #    且「重启同花顺」「清场」「解除熔断」等恢复手段必须在券商未就绪
            #    时也能下发 —— 否则"无人值守"就是空话（同花顺没起来时控制台
            #    就点不了任何东西，只能跑到现场）。
            #    ⚠️ 中转不可达（中转重启/被回收/网络抖动）**只**影响"收发消息"
            #    这一段，绝不能连带跳过下面的券商自愈与看门狗。2026-09-13 实测
            #    踩过：中转进程被回收后 poll_tasks 抛 RequestException，旧写法由
            #    外层 except 直接 continue，于是"同花顺没就绪也没人重试、看门狗
            #    一次都不评估"，现场表现就是**「拉不起下单程序，看门狗也没起作用」**。
            try:
                for task in relay.poll_tasks(1):
                    handle_task(relay, runner, task)
            except requests.RequestException as e:
                relay_reachable = False
                log.warning("连接中转服务失败（将重试）: %s", e)
            # 2) 券商连接自愈（限频）：连接失败/掉线都只记录并稍后重试，绝不退出。
            ensure_broker_ready(slot)
            # 2.5) 界面异常看门狗：界面错乱累计到阈值时自动重启同花顺（仅 ths 模式；
            #      与信号处理同线程串行，保证券商 UI 不被并发操作）。
            _maybe_watchdog_restart(slot)
            # 3) 聚宽策略信号：仅在券商就绪且未熔断时处理（L2/L3 暂停自动下单，
            #    避免"假死抖动"反复撞墙）。未就绪时静默跳过，由上面的限频告警负责提示。
            if not slot.ready:
                pass
            elif guard.can_trade():
                try:
                    signals = relay.poll()
                except requests.RequestException as e:
                    relay_reachable = False
                    signals = []
                    log.warning("连接中转服务失败（将重试）: %s", e)
                for sig in signals:
                    # 信号批次内也要给控制台任务让路：poll() 一次最多取 10 个信号，
                    # 且在本 for 里串行处理完（10 笔 ≈ 100 秒）。若只在批次之间轮询
                    # 任务，用户在页面上点「查询委托/一键全撤」会一直排到整批信号之后，
                    # 超过控制台 30 秒等待而报"执行端未在限定时间内返回结果"
                    # （2026-09-13 实测 10 笔连下时复现）。穿插后还能让"重启同花顺/
                    # 清场"等恢复手段在信号批次中途就生效。
                    # 注意：这里的异常必须就地吞掉。relay.poll() 取出的信号已在
                    # 中转侧标记为"已派发"，若让轮询任务的异常冒到外层 except，
                    # 批次里剩下的信号就再也没人处理了（静默丢单）。
                    try:
                        for task in relay.poll_tasks(1):
                            handle_task(relay, runner, task)
                    except Exception as e:
                        log.warning("信号批次中轮询控制台任务失败（忽略，继续下单）: %s", e)
                    handle_signal(relay, slot.broker, sig)
            else:
                log.warning("熔断/暂停中（%s），本周期跳过自动下单", guard.level_name())
        except requests.RequestException as e:
            log.warning("连接中转服务失败（将重试）: %s", e)
        except KeyboardInterrupt:
            log.info("收到停止指令，退出")
            break
        except Exception:
            log.error("主循环异常（不退出，继续）:\n%s", traceback.format_exc())
        time.sleep(max(config.POLL_INTERVAL, guard.backoff()))


if __name__ == "__main__":
    main()
