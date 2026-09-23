# -*- coding: utf-8 -*-
"""
执行端 .env 模板 + 启动配置摘要。

TEMPLATE 是「首次运行时自动生成的 .env」的全部内容，面向不懂命令行的用户：
分区清晰、每行有中文说明、真正需要改的只有 3 项，其余默认注释掉（眼不见不吓人）。
MARKER 用来判断用户的 .env 里是否已包含本产品的配置段，避免重复追加。

注意：模板正文里不要出现花括号以外的百分号转义问题，改文案时保持普通字符串即可。
"""
import os

import envfile

MARKER = "JQ-EXECUTOR-CONFIG-v1"

_RAW = """# ============================================================
#   RelayGo · 执行端 配置文件
#   __MARKER__
#
#   怎么改：
#     1) 双击「执行端-配置.bat」，选 1 跟着提示填（推荐，不会用记事本就用这个）
#     2) 或者用记事本打开本文件直接改
#     3) 改完保存，重新启动执行端即可生效（不用重启电脑）
#
#   行首带 # 的行是说明，不生效。想让哪项生效，把行首的 # 去掉即可。
#   删掉某一行 = 该项恢复成默认值。
# ============================================================


# ============ 【1】必改：下面 3 项 ============

# 中转服务地址。
#   中转和执行端在同一台电脑 -> 保持默认即可
#   分在两台电脑 -> 改成中转那台的 IP，例如 EXECUTOR_RELAY_URL=http://192.168.1.88:5010
EXECUTOR_RELAY_URL=http://127.0.0.1:5010

# 执行端密钥。必须和中转服务 .env 里的 RELAY_EXECUTOR_KEY 一模一样，否则连不上。
EXECUTOR_API_KEY=executor-key-2026-change-me

# 同花顺下单程序路径（注意结尾是 xiadan.exe，不是同花顺主程序）。
# 不记得在哪：右键桌面同花顺图标 -> 打开文件所在位置，找到 xiadan.exe 复制完整路径。
EXECUTOR_THS_XIADAN_PATH=D:\\同花顺软件\\同花顺\\xiadan.exe

# 同花顺"行情主程序"路径（hexin.exe，和 xiadan.exe 在同一个目录）。
# 为什么要有它（2026-09-13 实测）：只启动 xiadan.exe 会被同花顺风控（下单时
# "证券代码查不出名称和价格"，之后所有单都卡住）；正确做法是**先启动行情软件、
# 再按 F12 拉起下单**，这样复用同一个登录会话，连下多单也不会被风控。
# 执行端已按下面的"启动方式"自动这么做，路径对就不用改。
EXECUTOR_THS_MAIN_PATH=D:\\同花顺软件\\同花顺\\hexin.exe

# 启动方式：
#   main_f12   = 先启动行情主程序，再按 F12 拉起下单（默认，推荐，不会被风控）
#   standalone = 直接启动 xiadan.exe（旧方式，会随机被风控，仅在排查时临时用）
#EXECUTOR_THS_LAUNCH_MODE=main_f12
# 起行情主程序后最多等多少秒它的窗口出现
#EXECUTOR_THS_MAIN_WAIT=90
# 按 F12 后最多重发几次（没等到下单窗口时）
#EXECUTOR_THS_F12_RETRY=3

# 同花顺启动后登录界面会弹「非注册用户」提示框（[立即注册]/[取消]），不点掉就
# 登录不了。执行端会自动点[取消]把它关掉，保持默认 1 即可；排查问题时改成 0。
#EXECUTOR_THS_AUTODISMISS=1

# 同花顺自己不会自动登录（实测会一直停在登录界面）。交易密码由同花顺自己保存，
# 执行端会在登录界面停留超过下面秒数后把它提交掉；点几次不成功或提示要验证码就
# 停手并告警（不会硬撞券商登录次数）。不想让它代劳就把下面这行改成 0。
#EXECUTOR_THS_LOGIN_ASSIST=1
#EXECUTOR_THS_LOGIN_ASSIST_IDLE=20
#EXECUTOR_THS_LOGIN_ASSIST_MAX=3

# 触发登录的方式与顺序，默认「先按 Alt+F4、再点[登录]」（Alt+F4 是用户在登录界面
# 验证过的方式；两种都试过还不行才会报"没生效"）。只想点[登录]就改成 click。
# 注意：Alt+F4 是"关窗口"，执行端只对**登录窗口本身**发、且会先把它切到前台确认，
# 一旦发现它把同花顺整个关掉就自动停用这种方式。
#EXECUTOR_THS_LOGIN_TRIGGERS=altf4,click
#EXECUTOR_THS_LOGIN_TRIGGER_WAIT=8

# 看门狗：同花顺"界面错乱"（填完代码读不出证券名称/价格、控件找不到、剪贴板读不到
# 表格、弹窗卡死、验证码连续失败）累计到阈值时，自动重启同花顺来重置界面状态。
# 只统计"界面异常"，风控拒绝等业务失败不计入（不会被高频信号带偏）。安全阀：
# 重启互斥 + 重启后冷却 + 每小时上限（超限只告警不再自动重启）。默认开着；排查
# 问题时把第 1 行改成 0 可关闭。
#EXECUTOR_THS_WATCHDOG=1
#EXECUTOR_THS_WATCHDOG_WINDOW=60
#EXECUTOR_THS_WATCHDOG_CONSECUTIVE=3
#EXECUTOR_THS_WATCHDOG_THRESHOLD=5
#EXECUTOR_THS_WATCHDOG_COOLDOWN=120
#EXECUTOR_THS_WATCHDOG_MAX_PER_HOUR=3


# ============ 【2】一般不用动 ============

# 执行模式：
#   dry_run  = 只模拟、不下真实单（默认，最安全，建议先用它跑通）
#   ths      = 同花顺客户端真实下单
#   thsauto  = 安卓模拟器方案（备用）
#   miniqmt  = 券商 QMT 官方接口（需要券商开通权限）
# 注意：用「启动执行端.bat」启动时，bat 里选的模式会盖过这里。
#EXECUTOR_MODE=dry_run

# 向中转服务拉取信号的间隔（秒）。数字小一点更实时，但别小于 1。
#EXECUTOR_POLL_INTERVAL=3


# ============ 【3】风控参数（想收紧就改，去掉行首 # 才生效） ============

# 单笔最多买多少股，超过直接拒绝
#EXECUTOR_MAX_SINGLE_AMOUNT=10000

# 当天累计最多买多少股，超过暂停
#EXECUTOR_MAX_DAILY_AMOUNT=50000

# 只允许买这些股票代码，用逗号分隔（例：600519,000001）。留空 = 不限制。
#EXECUTOR_CODE_WHITELIST=

# 信号价格与最新价偏差超过这个比例就拒绝下单（0.03 = 3%）
#EXECUTOR_MAX_PRICE_DEVIATION=0.03


# ============ 【4】另外两种下单方案的参数（用不到就不用管） ============

# --- 方案B：thsauto（安卓模拟器里跑同花顺 App）---
#EXECUTOR_THSAUTO_URL=http://127.0.0.1:5001
#EXECUTOR_THSAUTO_ADDR=emulator-5554

# --- 方案C：miniQMT（券商官方接口）---
#EXECUTOR_QMT_PATH=D:\\QMT交易端\\userdata_mini
#EXECUTOR_QMT_ACCOUNT_ID=你的资金账号
#EXECUTOR_QMT_ACCOUNT_TYPE=STOCK
"""

TEMPLATE = _RAW.replace("__MARKER__", MARKER)


def _exists_note(path):
    try:
        return "（已找到）" if os.path.exists(path) else "（未找到，请检查路径）"
    except Exception:
        return ""


def report(cfg, info):
    """拼出启动时打印的配置摘要（密钥脱敏）。"""
    L = [envfile.file_note(info)]
    L.append("执行模式  %s" % cfg.MODE)
    L.append("中转服务  %s" % cfg.RELAY_URL)
    L.append("接口密钥  %s" % envfile.mask(cfg.EXECUTOR_API_KEY))
    if cfg.MODE == "ths":
        L.append("同花顺    %s %s" % (cfg.THS_XIADAN_PATH, _exists_note(cfg.THS_XIADAN_PATH)))
        if cfg.THS_LAUNCH_MODE == "main_f12":
            L.append("启动方式  行情主程序 + F12（避风控）：%s %s"
                     % (cfg.THS_MAIN_PATH, _exists_note(cfg.THS_MAIN_PATH)))
        else:
            L.append("启动方式  直接启动 xiadan.exe（standalone，可能被风控）")
    elif cfg.MODE == "thsauto":
        L.append("thsauto   %s" % cfg.THSAUTO_URL)
    elif cfg.MODE == "miniqmt":
        L.append("QMT 账号  %s（%s）" % (cfg.QMT_ACCOUNT_ID, cfg.QMT_ACCOUNT_TYPE))
        L.append("QMT 目录  %s %s" % (cfg.QMT_PATH, _exists_note(cfg.QMT_PATH)))
    L.append("风控参数  单笔 %s 股 / 当日 %s 股 / 价格偏差 %s%%"
             % (cfg.MAX_SINGLE_AMOUNT, cfg.MAX_DAILY_AMOUNT,
                round(cfg.MAX_PRICE_DEVIATION * 100, 2)))
    if cfg.CODE_WHITELIST:
        L.append("代码白名单 %s" % ", ".join(cfg.CODE_WHITELIST))
    return envfile.banner("RelayGo · 执行端 %s" % cfg.VERSION, L)
