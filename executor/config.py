# -*- coding: utf-8 -*-
"""
执行端配置 —— 三种下单方式通过 MODE 一键切换，其余按所用模式填写对应配置段。

MODE 取值：
  dry_run   模拟模式：只打印/记录，不真正下单（阶段0 验证链路用，默认值）
  ths       方案A：easytrader 驱动同花顺客户端 xiadan.exe（UI 自动化）
  thsauto   方案B：HTTP 调用 thsauto（雷电模拟器里跑同花顺APP）
  miniqmt   方案C：xtquant 官方接口（需要券商开通 QMT/miniQMT 权限）

配置来源优先级（先命中者胜）：
    系统环境变量  >  程序旁边的 .env 文件  >  本文件里的默认值
普通用户只需要改程序旁边的 .env（双击「执行端-配置.bat」或用记事本打开），
不需要懂「环境变量」是什么。每一项对应的环境变量名见每行后面的注释。
"""
import os

import env_template
import envfile

# ---- 读取程序旁边的 .env（没有就自动生成一份带中文说明的，用户用记事本改即可）----
# 必须在下面所有 os.environ.get 之前执行。
ENV_INFO = envfile.setup(env_template.TEMPLATE, env_template.MARKER, "EXECUTOR_ENV_FILE")

# MODE 可用环境变量 EXECUTOR_MODE 覆盖（启动 bat 会用它来选模式），默认 dry_run
MODE = os.environ.get("EXECUTOR_MODE", "dry_run")

# 执行端版本号：发版时与 relay_server/config.py 的 VERSION 保持一致（release/verify_release.py 会校验）
VERSION = "v1.7.13"

# ============ 中转服务连接 ============
RELAY_URL = os.environ.get("EXECUTOR_RELAY_URL", "http://127.0.0.1:5010")   # 中转服务地址；云端部署则填服务器 IP/域名
EXECUTOR_API_KEY = os.environ.get("EXECUTOR_API_KEY", "executor-key-2026-change-me")  # 必须与 relay_server 一致
POLL_INTERVAL = envfile.get_int("EXECUTOR_POLL_INTERVAL", 3)                # 轮询间隔（秒）

# ============ 风控参数（risk.py 使用） ============
MAX_SINGLE_AMOUNT = envfile.get_int("EXECUTOR_MAX_SINGLE_AMOUNT", 10000)     # 单笔最大股数，超过直接拒绝
MAX_DAILY_AMOUNT = envfile.get_int("EXECUTOR_MAX_DAILY_AMOUNT", 50000)       # 当日累计最大股数，超过暂停并告警
CODE_WHITELIST = envfile.get_list("EXECUTOR_CODE_WHITELIST", [])             # 只允许这些代码下单；留空 = 不限制
MAX_PRICE_DEVIATION = envfile.get_float("EXECUTOR_MAX_PRICE_DEVIATION", 0.03)  # 信号价与最新价偏差超过则拒绝

# ============ 连续失败熔断（failure_guard.py） ============
FAILURE_GUARD_WINDOW = envfile.get_int("EXECUTOR_GUARD_WINDOW", 120)  # 失败计数窗口(秒)，超出窗口的旧失败自动归零
FAILURE_GUARD_L1 = envfile.get_int("EXECUTOR_GUARD_L1", 3)   # ≥3 次/窗口 → L1：拉大轮询间隔退避
FAILURE_GUARD_L2 = envfile.get_int("EXECUTOR_GUARD_L2", 6)   # ≥6 次/窗口 → L2：暂停自动下单
FAILURE_GUARD_L3 = envfile.get_int("EXECUTOR_GUARD_L3", 12)  # ≥12 次/窗口 → L3：熔断，须管理员 guard_reset 或重启

# ============ 同花顺界面异常看门狗（2026-09-13，ths_watchdog.py） ============
# 与上面的熔断分工不同：熔断管"业务失败"（风控拒绝/券商拒单）；看门狗只管"界面级
# 异常"（填完代码读不出证券名称/价格、控件找不到、剪贴板读不到、模态卡死、验证码
# 连续失败），累计到阈值后**自动重启同花顺**（界面错乱时重启可重置界面状态）。
# 安全阀：连续/窗口阈值 + 重启互斥 + 冷却 + 每小时上限（超限只告警不再重启）。
# 注意：风控拒绝**绝不**计入，否则高频信号会把同花顺重启到疯（重启风暴）。
THS_WATCHDOG = envfile.get_int("EXECUTOR_THS_WATCHDOG", 1) == 1                 # 1=开 / 0=关（排查问题时关）
THS_WATCHDOG_WINDOW = envfile.get_int("EXECUTOR_THS_WATCHDOG_WINDOW", 60)       # 计数窗口(秒)
THS_WATCHDOG_CONSECUTIVE = envfile.get_int("EXECUTOR_THS_WATCHDOG_CONSECUTIVE", 3)  # 连续 ≥N 次 → 重启
THS_WATCHDOG_THRESHOLD = envfile.get_int("EXECUTOR_THS_WATCHDOG_THRESHOLD", 5)  # 窗口内 ≥N 次 → 重启
THS_WATCHDOG_COOLDOWN = envfile.get_int("EXECUTOR_THS_WATCHDOG_COOLDOWN", 120)  # 重启后冷却(秒)内不再自动重启
THS_WATCHDOG_MAX_PER_HOUR = envfile.get_int("EXECUTOR_THS_WATCHDOG_MAX_PER_HOUR", 3)  # 每小时最多自动重启次数，超限只告警

# ============ 方案A：同花顺客户端（easytrader） ============
THS_XIADAN_PATH = envfile.get_str("EXECUTOR_THS_XIADAN_PATH",
                                  r"D:\同花顺软件\同花顺\xiadan.exe")   # 同花顺下单程序完整路径（是 xiadan.exe）
# 行情主程序（hexin.exe）—— 「行情 → F12 拉起下单」启动模式用。
# 为什么必须走这条路（2026-09-13 用户实测）：单独启动 xiadan.exe 会自建一条独立会话，
# 并靠"按需查询"去要 代码→名称/最新价；这类高频主动查询会触发同花顺前端风控，
# 表现为"代码查不出名称和价格 → 后续全部阻塞"。而从行情主程序按 F12 拉起的交易模块
# **复用已登录会话 + 行情推送通道（订阅式）**，不触发风控（实测连下 10 单全成功）。
THS_MAIN_PATH = envfile.get_str("EXECUTOR_THS_MAIN_PATH",
                                r"D:\同花顺软件\同花顺\hexin.exe")     # 同花顺行情主程序 hexin.exe 完整路径
# 启动模式：
#   main_f12   先启动行情主程序 hexin.exe，再按其 F12 快捷键拉起交易模块（推荐，避风控）
#   standalone 直接启动 xiadan.exe（旧行为，会随机被风控，仅兼容/排查时用）
THS_LAUNCH_MODE = envfile.get_str("EXECUTOR_THS_LAUNCH_MODE", "main_f12").strip().lower()
THS_MAIN_WAIT = envfile.get_int("EXECUTOR_THS_MAIN_WAIT", 90)      # 起行情主程序后最多等(秒)其主窗口"真正可用"
# 行情主窗口"可发 F12"的判据（2026-09-13 用户反馈"行情还没完全启动就发 F12，有时拉不起下单程序"）：
# 只判断"窗口存在"是不够的 —— hexin 冷启动时先出闪屏/登录框，那时句柄还会变，F12 发过去就丢了。
# 这里要求窗口**可见 + 面积够大 + 能响应消息(不卡死) + 尺寸连续 N 次采样不变**，才认定可以发 F12。
THS_MAIN_MIN_AREA = envfile.get_int("EXECUTOR_THS_MAIN_MIN_AREA", 120000)  # 视为行情主窗口的最小面积(px²)，滤掉闪屏/小提示窗
THS_MAIN_READY_STABLE = envfile.get_int("EXECUTOR_THS_MAIN_READY_STABLE", 3)  # 尺寸连续稳定 N 次采样(约 N*0.5 秒)才发 F12
THS_F12_RETRY = envfile.get_int("EXECUTOR_THS_F12_RETRY", 6)       # 发 F12 后未见交易窗口时的重发次数
THS_F12_TOTAL_WAIT = envfile.get_int("EXECUTOR_THS_F12_TOTAL_WAIT", 150)  # 反复发 F12 拉交易窗口的总时长上限(秒)
THS_F12_PER_WAIT = envfile.get_int("EXECUTOR_THS_F12_PER_WAIT", 20)   # 每次 F12 后等交易窗口出现的时长(秒)
THS_MAIN_EXIT_WAIT = envfile.get_int("EXECUTOR_THS_MAIN_EXIT_WAIT", 15)  # 深度恢复时等行情主程序真正退出的时长(秒)
THS_AUTOSTART = envfile.get_int("EXECUTOR_THS_AUTOSTART", 1) == 1       # 执行端启动时若同花顺未运行则自动拉起(1=开/0=关)
THS_AUTOSTART_TIMEOUT = envfile.get_int("EXECUTOR_THS_AUTOSTART_TIMEOUT", 60)  # 自动拉起后最多等待(秒)交易窗口出现
# 拉起后等待期间，交易窗口"消失/一直不出"超过该秒数就重发一次 F12（2026-09-13 用户反馈
# "能启动行情，却一直拉不起下单程序"：旧实现在这里干等满超时、从不重发）。
THS_RESEND_F12_GAP = envfile.get_int("EXECUTOR_THS_RESEND_F12_GAP", 8)  # 交易窗口不在时，每隔该秒数重发一次 F12
# 交易窗口就绪后，把行情主程序窗口缩到角落（避免大行情窗口遮挡交易界面；窗口保持存活）
THS_PARK_MAIN_WINDOW = envfile.get_int("EXECUTOR_THS_PARK_MAIN_WINDOW", 1)  # 1=开启缩角/0=不处理
THS_PARK_MAIN_SIZE = envfile.get_int("EXECUTOR_THS_PARK_MAIN_SIZE", 100)    # 缩角后的边长(px)，建议≥40
THS_LOGIN_WAIT = envfile.get_int("EXECUTOR_THS_LOGIN_WAIT", 180)  # 单次等待(秒)同花顺完成登录并可读资金；超时不退出，转为后台按 BROKER_RETRY_INTERVAL 重试
THS_RESTART_WAIT = envfile.get_int("EXECUTOR_THS_RESTART_WAIT", 120)  # 「重启同花顺」按钮最多等待(秒)其自动登录并重连成功

# ============ 执行端在线与券商自愈（2026-09-12） ============
# 「执行端在线」只取决于能否与中转服务通信，与同花顺是否启动/登录无关：
# 连接不上券商时执行端保持在线，只是不处理下单信号，好让控制台的
# 「重启同花顺」等恢复手段随时可用（否则就得跑到现场处理）。
BROKER_RETRY_INTERVAL = envfile.get_int("EXECUTOR_BROKER_RETRY_INTERVAL", 20)  # 券商未就绪时的重试间隔 / 已就绪时的掉线复查间隔（秒）
HEARTBEAT_INTERVAL = envfile.get_int("EXECUTOR_HEARTBEAT_INTERVAL", 5)  # 后台心跳间隔（秒）；必须明显小于中转判定离线的 30 秒

# ============ 同花顺干扰弹窗自动处理（2026-09-12） ============
# 同花顺启动后登录界面会弹「非注册用户」注册提示（按钮 [立即注册]/[取消]）；
# 它是模态的，不点掉就登录不了、交易窗口不出现，表现为"拉起了同花顺却被挡住"。
# 打开后执行端会自动点[取消]关掉它（白名单规则，绝不点"立即注册/确定"）。
THS_AUTODISMISS = envfile.get_int("EXECUTOR_THS_AUTODISMISS", 1) == 1  # 1=自动关闭干扰弹窗 / 0=关（排查问题时用）

# ============ 同花顺登录辅助（2026-09-12） ============
# 真机实测：同花顺拉起后停在登录窗口，**它自己不会自动登录**（干等 140 秒无动静）；
# 而交易密码已由同花顺自己保存并填好，替人点一下[登录] 立刻登录成功。
# 打开后执行端会在登录界面停留超过下面秒数时代点[登录]（密码始终由同花顺保管，
# 执行端不接触密码）。点几次不成功、或同花顺提示要密码/验证码就停手并告警。
THS_LOGIN_ASSIST = envfile.get_int("EXECUTOR_THS_LOGIN_ASSIST", 1) == 1     # 1=自动点[登录] / 0=关
THS_LOGIN_ASSIST_IDLE = envfile.get_int("EXECUTOR_THS_LOGIN_ASSIST_IDLE", 20)  # 登录界面停留多少秒后才代点（先给同花顺自己的机会）
THS_LOGIN_ASSIST_MAX = envfile.get_int("EXECUTOR_THS_LOGIN_ASSIST_MAX", 3)   # 同一个登录窗口最多代点几次
# 触发登录的方式与顺序（2026-09-12 用户反馈"登录界面按 Alt+F4 就能自动登录"）：
#   altf4 = 对**登录窗口**按 Alt+F4（先把窗口置前并校验前台，否则宁可不发键）
#   click = 点[登录]（实测有效，作为兜底）
# 先 altf4 再 click：两种都试过才报"没生效"。若某次 Alt+F4 把同花顺整个关掉，
# 执行端会自动停用该方式（避免"关掉->拉起->再关掉"）。
THS_LOGIN_TRIGGERS = tuple(
    t.strip().lower() for t in
    envfile.get_str("EXECUTOR_THS_LOGIN_TRIGGERS", "altf4,click").split(",")
    if t.strip())
THS_LOGIN_TRIGGER_WAIT = envfile.get_int("EXECUTOR_THS_LOGIN_TRIGGER_WAIT", 8)  # 每次触发后最多等几秒看登录窗口是否消失

# ============ 方案B：thsauto（安卓模拟器） ============
THSAUTO_URL = envfile.get_str("EXECUTOR_THSAUTO_URL", "http://127.0.0.1:5001")  # thsauto run --port=5001
THSAUTO_ADDR = envfile.get_str("EXECUTOR_THSAUTO_ADDR", "emulator-5554")       # 雷电模拟器 adb 地址

# ============ 方案C：miniQMT（xtquant） ============
QMT_PATH = envfile.get_str("EXECUTOR_QMT_PATH", r"D:\QMT交易端\userdata_mini")  # miniQMT 的 userdata_mini 目录
QMT_ACCOUNT_ID = envfile.get_str("EXECUTOR_QMT_ACCOUNT_ID", "你的资金账号")         # 券商资金账号
QMT_ACCOUNT_TYPE = envfile.get_str("EXECUTOR_QMT_ACCOUNT_TYPE", "STOCK")           # STOCK=股票账户


def startup_report():
    """启动时打印的配置摘要（密钥脱敏），让用户一眼确认自己的配置生效了没有。"""
    import sys
    return env_template.report(sys.modules[__name__], ENV_INFO)
