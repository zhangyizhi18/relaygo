# -*- coding: utf-8 -*-
"""
中转服务 .env 模板 + 启动配置摘要。

TEMPLATE 是「首次运行时自动生成的 .env」的全部内容，面向不懂命令行的用户：
只有 4 项需要留意（两个密钥 + 控制台密码 + 会话密钥），其余默认注释掉。
MARKER 用来判断用户的 .env 里是否已包含本产品的配置段，避免重复追加。
"""
import envfile

MARKER = "JQ-RELAY-CONFIG-v1"

_RAW = """# ============================================================
#   RelayGo · 中转服务 配置文件
#   __MARKER__
#
#   怎么改：
#     1) 双击「中转服务-配置.bat」，选 1 跟着提示填（推荐）
#     2) 或者用记事本打开本文件直接改
#     3) 改完保存，重新启动中转服务即可生效
#
#   行首带 # 的行是说明，不生效。想让哪项生效，把行首的 # 去掉即可。
# ============================================================


# ============ 【1】必须改：密钥和密码（改掉默认值，别用示例值） ============

# 聚宽策略发信号用的密钥。必须和聚宽端脚本里的 API_KEY 一致。
RELAY_SIGNAL_KEY=jq-signal-key-2026-change-me

# 执行端拉取信号用的密钥。必须和执行端 .env 里的 EXECUTOR_API_KEY 一致。
RELAY_EXECUTOR_KEY=executor-key-2026-change-me

# Web 控制台管理员密码。仅在「第一次启动、还没有任何用户」时用来创建 admin 账号；
# 之后想改密码，请登录控制台在「用户管理」里改，改这里已经不起作用了。
RELAY_WEB_ADMIN_PASSWORD=admin123

# 登录会话签名密钥。随便改成一段别人猜不到的字符即可（公网部署必须改）。
RELAY_WEB_SECRET=relay-web-secret-change-me-2026


# ============ 【2】一般不用动 ============

# 监听地址。0.0.0.0 = 允许外部访问（要让聚宽云端连进来，必须保持 0.0.0.0）
#RELAY_HOST=0.0.0.0

# 监听端口。别改成 5000，会和别的程序冲突。
#RELAY_PORT=5010

# 控制台登录账号名（仅首次启动创建时生效）
#RELAY_WEB_ADMIN=admin

# 控制台登录有效期（小时）
#RELAY_WEB_SESSION_HOURS=12


# ============ 【3】容量与超时（一般不用动） ============

# 数据库文件位置。默认放在程序旁边的 relay_data.db，一般不用填。
#RELAY_DB_PATH=

# 最多允许积压多少条未处理信号
#RELAY_MAX_PENDING=500

# 信号多少小时后过期作废
#RELAY_TTL_HOURS=24

# 下发给执行端的任务多久没回报算超时（秒）
#RELAY_TASK_TTL=180

# 控制台接口等待执行端回报的最长时间（秒）
#RELAY_TASK_WAIT=30

# 执行端心跳多少秒内算「在线」（秒）
#RELAY_EXECUTOR_ALIVE=30


# ============ 【4】盘后对账（一般不用动） ============

# 每个交易日自动对账一次「聚宽持仓 vs 券商持仓」：1=开启（默认），0=关闭
#RELAY_RECONCILE_AUTO=1

# 自动对账时间（24 小时制 HH:MM），默认收盘后 15:05
#RELAY_RECONCILE_AT=15:05


# ============ 【5】授权验证（一般不用动） ============

# 卡密授权验证开关：1=启用（默认），0=关闭（开发/测试用）
#RELAY_LICENSE_ENABLED=1

# 授权服务端地址（默认已指向线上授权服务端，本地调试时才需要改）
#RELAY_LICENSE_URL=http://127.0.0.1:9527

# 授权账号无需填写：由本机机器码自动算出（8 位纯数字，打开首页激活页即可看到），
# 把账号发给管理员领取卡密，在激活页输入卡密即可完成激活。

# 授权服务器短暂失联时的离线宽限（小时），宽限期内可正常交易；明确过期/禁用不受宽限
#RELAY_LICENSE_GRACE_HOURS=72
"""

TEMPLATE = _RAW.replace("__MARKER__", MARKER)


def report(cfg, info):
    """拼出启动时打印的配置摘要（密钥与密码一律脱敏）。"""
    L = [envfile.file_note(info)]
    L.append("监听地址  http://%s:%s" % (cfg.HOST, cfg.PORT))
    L.append("Web 控制台 http://127.0.0.1:%s/console/   （账号 %s，密码 %s）"
             % (cfg.PORT, cfg.WEB_DEFAULT_ADMIN, envfile.mask(cfg.WEB_DEFAULT_ADMIN_PASSWORD)))
    L.append("聚宽密钥  %s" % envfile.mask(cfg.SIGNAL_API_KEY))
    L.append("执行端密钥 %s" % envfile.mask(cfg.EXECUTOR_API_KEY))
    L.append("数据库    %s" % cfg.DB_PATH)
    L.append("盘后对账  %s（交易日 %s 自动执行）"
             % ("开启" if cfg.RECONCILE_AUTO else "关闭", cfg.RECONCILE_AT))
    if getattr(cfg, "LICENSE_ENABLED", 0):
        try:
            import license_guard
            L.append("授权验证  开启（本机授权账号 %s，服务端 %s）"
                     % (license_guard.machine_account(), cfg.LICENSE_URL))
        except Exception:
            L.append("授权验证  开启（服务端 %s）" % cfg.LICENSE_URL)
    else:
        L.append("授权验证  关闭（RELAY_LICENSE_ENABLED=0）")
    return envfile.banner("RelayGo · 中转服务 %s" % cfg.VERSION, L)
