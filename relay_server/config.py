# -*- coding: utf-8 -*-
"""
中转服务端配置 —— 所有可调参数都集中在这里。

配置来源优先级（先命中者胜）：
    系统环境变量  >  程序旁边的 .env 文件  >  本文件里的默认值

对普通用户来说只需要知道一件事：改程序旁边的 .env 就行（双击「中转服务-配置.bat」
或用记事本打开），不需要懂「环境变量」是什么。
所有参数对应的环境变量名见每行后面的注释。
"""
import os
import sys

import env_template
import envfile

# ---- 读取程序旁边的 .env（没有就自动生成一份带中文说明的，用户用记事本改即可）----
# 必须在下面所有 os.environ.get 之前执行。
ENV_INFO = envfile.setup(env_template.TEMPLATE, env_template.MARKER, "RELAY_ENV_FILE")

VERSION = "v1.7.13"   # 发版版本号，/api/status 与 Web 控制台页面都会显示它

# ============ 网络配置 ============
HOST = os.environ.get("RELAY_HOST", "0.0.0.0")   # 0.0.0.0 = 允许外部访问（聚宽云端要能连进来就必须是它）
PORT = envfile.get_int("RELAY_PORT", 5010)       # 别用 5000，避免和 thsauto 默认端口冲突

# ============ 鉴权密钥 ============
# 聚宽策略发信号用的密钥（聚宽端 joinquant/jq_signal.py 里的 API_KEY 必须和这里一致）
SIGNAL_API_KEY = os.environ.get("RELAY_SIGNAL_KEY", "jq-signal-key-2026-change-me")

# 执行端（本机跑的下单程序）拉取信号/回报用的密钥
EXECUTOR_API_KEY = os.environ.get("RELAY_EXECUTOR_KEY", "executor-key-2026-change-me")

# ============ 数据库 ============
# SQLite 数据库文件路径。优先级：环境变量 > exe所在目录（PyInstaller 打包时） > 本机开发默认路径
if os.environ.get("RELAY_DB_PATH"):
    DB_PATH = os.environ["RELAY_DB_PATH"]
elif getattr(sys, "frozen", False):   # PyInstaller exe 模式：库文件放在 exe 旁边
    DB_PATH = os.path.join(os.path.dirname(sys.executable), "relay_data.db")
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "relay_data.db")

# ============ 业务参数 ============
MAX_PENDING = envfile.get_int("RELAY_MAX_PENDING", 500)      # 积压信号上限
SIGNAL_TTL_HOURS = envfile.get_int("RELAY_TTL_HOURS", 24)    # 信号过期小时数


# ============ Web 控制台 ============
# 会话密钥：用于给登录 Cookie 签名。公网部署务必改掉（环境变量 RELAY_WEB_SECRET）
WEB_SECRET_KEY = os.environ.get("RELAY_WEB_SECRET", "relay-web-secret-change-me-2026")
# 登录有效期（小时）
WEB_SESSION_HOURS = envfile.get_int("RELAY_WEB_SESSION_HOURS", 12)
# 首次启动自动创建的默认管理员（登录后请立即改密码）：环境变量 RELAY_WEB_ADMIN / RELAY_WEB_ADMIN_PASSWORD
WEB_DEFAULT_ADMIN = os.environ.get("RELAY_WEB_ADMIN", "admin")
WEB_DEFAULT_ADMIN_PASSWORD = os.environ.get("RELAY_WEB_ADMIN_PASSWORD", "admin123")

# 控制台下发给执行端的任务：多久没回报算超时作废（秒）
TASK_TTL_SECONDS = envfile.get_int("RELAY_TASK_TTL", 180)
# 控制台接口等待执行端回报的最长时间（秒）
TASK_WAIT_SECONDS = envfile.get_int("RELAY_TASK_WAIT", 30)
# 执行端心跳多少秒内算"在线"（秒）
EXECUTOR_ALIVE_SECONDS = envfile.get_int("RELAY_EXECUTOR_ALIVE", 30)

# ============ 盘后对账（v1.7.0） ============
# 每个交易日(周一~五) RECONCILE_AT 时刻自动做一次「聚宽持仓 vs 券商持仓」对账；0=关闭
RECONCILE_AUTO = envfile.get_int("RELAY_RECONCILE_AUTO", 1)
# 自动对账时间（24 小时制 HH:MM），默认收盘后 15:05
RECONCILE_AT = os.environ.get("RELAY_RECONCILE_AT", "15:05")

# ============ 授权验证（对接 MT5LicenseWeb2.4 授权服务端，2026-09-12） ============
# 总开关：1=启用（默认），0=关闭（开发/自测用，测试脚本必须置 0）
LICENSE_ENABLED = envfile.get_int("RELAY_LICENSE_ENABLED", 1)

# ---- 正式版锁（2026-09-12）----
# 发布包（中转 exe / docker 源码包）在打包时由 build_all 流程自动打入一个
# release.lock 标记文件（exe 在运行时解压目录里，docker 在 relay_server/ 里）。
# 检测到它时，RELAY_LICENSE_ENABLED=0 一律强制无效 —— 防止拿到包的用户改
# .env（甚至什么都不改）就把整个授权验证关掉。开发仓库没有这个文件，
# 测试脚本可以正常用 0 关闭授权。
def _license_release_locked():
    # exe(onefile)：模块从运行时解压目录导入，__file__ 就在那里；源码模式取本文件目录
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.exists(os.path.join(base, "release.lock"))

LICENSE_SWITCH_LOCKED = False
if LICENSE_ENABLED == 0 and _license_release_locked():
    LICENSE_ENABLED = 1
    LICENSE_SWITCH_LOCKED = True

# 授权服务端地址（MT5LicenseWeb2.4.py 部署地址；本地调试可用 RELAY_LICENSE_URL 覆盖）
LICENSE_URL = os.environ.get("RELAY_LICENSE_URL", "http://127.0.0.1:9527")
# 与授权服务端 MT5LicenseWeb2.4.py 内置的 API_KEY 保持一致（服务端后台改了要同步）
LICENSE_API_KEY = os.environ.get("RELAY_LICENSE_API_KEY", "change-me-license-api-key")
# 授权产品名（服务端按 mt5_account + ea_name 两条维度管理授权）
LICENSE_EA_NAME = os.environ.get("RELAY_LICENSE_EA_NAME", "RelayGo")
# 授权账号不用配：由本机机器码派生 8 位纯数字，激活页上可见（license_guard.machine_account）
# 服务器失联时的离线宽限小时数（明确无效不吃宽限，仅失联时宽限）
LICENSE_GRACE_HOURS = envfile.get_int("RELAY_LICENSE_GRACE_HOURS", 72)
# 运行期复验间隔（秒），默认 6 小时
LICENSE_RECHECK_SECONDS = envfile.get_int("RELAY_LICENSE_RECHECK", 21600)
# 单次验证 HTTP 超时（秒）
LICENSE_TIMEOUT = envfile.get_int("RELAY_LICENSE_TIMEOUT", 5)


def startup_report():
    """启动时打印的配置摘要（密钥脱敏），让用户一眼确认自己的配置生效了没有。"""
    return env_template.report(sys.modules[__name__], ENV_INFO)
