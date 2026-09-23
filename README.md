# 聚宽中转下单系统

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](#许可证)

聚宽云端策略 → 自建 Flask 中转 API → Windows 执行端 → 券商下单。
一套中转服务，**三种下单方式一键切换**（`executor/config.py` 里改 `MODE` 即可）。

> **发行版**：一次发布同时产出 中转服务 exe + 执行端 exe + Docker 源码包 + 详细用户手册(HTML)，
> 统一命名「名称-版本号-时间」。一键发版：`python release/build_all.py`。
> 本 README 面向开发/运维；面向最终用户的完整说明见发布包里的《用户手册》。

## 目录结构

```
聚宽中转下单/
├── relay_server/          中转服务端（Flask + SQLite）
│   ├── app.py             主入口：接收信号 / 发放信号 / 接收回报
│   ├── config.py          端口、密钥、数据库路径、Web 控制台配置
│   ├── store.py           SQLite 落库（信号幂等去重、回报、事件日志）
│   ├── web_console.py     ★ Web 控制台后端：登录/用户管理/账户查询/下单/审计
│   ├── web_ui.py          ★ Web 控制台前端（内联单页，无外部依赖，便于 exe 打包）
│   ├── store_web.py       ★ 控制台存储：users / tasks / audit_log 三张表
│   └── task_api.py        ★ 执行端任务通道：/api/task/poll、/api/task/feedback
├── executor/              执行端（跑在有券商客户端的 Windows 机器上）
│   ├── main.py            主循环：控制台任务 + 聚宽信号 -> 风控 -> 下单 -> 回报
│   ├── tasks.py           ★ 控制台任务执行器（查询/下单/撤单映射到券商适配器）
│   ├── config.py          MODE 切换 + 风控参数 + 各券商连接参数
│   ├── risk.py            风控：白名单/单笔上限/日限额/持仓核对/价格偏差
│   ├── broker_base.py     适配器基类 + 代码格式转换
│   ├── broker_ths.py      方案A：easytrader 驱动同花顺客户端 xiadan.exe
│   ├── broker_thsauto.py  方案B：HTTP 调用 thsauto（安卓模拟器）
│   ├── broker_miniqmt.py  方案C：xtquant 官方接口（最推荐）
│   ├── captcha.py         验证码自动识别（ddddocr，三级降级）
│   ├── foreground.py      窗口强制置前（绕过 Windows 前台锁）
│   └── pywinauto_compat.py  pywinauto 兼容垫片 + easytrader 补丁
├── joinquant/
│   └── jq_signal.py       聚宽策略开头粘贴的信号包装代码（策略主体零改动）
├── tools/
│   ├── test_signal.py     阶段0 联调脚本（不需要券商，验证中转全链路）
│   ├── test_console.py    ★ Web 控制台自测（36 项，不需要券商账号）
│   ├── jq_simulator.py    模拟聚宽发信号（没有聚宽账号也能玩全链路）
│   └── test_ths_broker.py 同花顺客户端分级测试（只读 -> 挂单 -> 撤单）
├── release/               ★ 发版脚本与产物
│   ├── build_all.py       一键发版（三包 + 手册 + 自检）
│   ├── build_exe.py       中转服务 exe
│   ├── build_executor_exe.py  执行端 exe
│   ├── build_docker.py    docker 源码包
│   ├── verify_release.py  包自检（结构/一致性/内容/版本）
│   ├── manual_template.html   用户手册模板
│   └── 升级说明.txt        本次更新说明（发版时带版本号落盘）
└── requirements.txt
```

## 快速开始（三步，全程不需要券商账号）

### 方式一：bat 一键启动（推荐，全自动）

双击项目根目录下的 **`启动全部.bat`**——首次运行会自动创建虚拟环境、安装依赖，
然后拉起中转服务和执行端两个窗口；执行端会询问模式（1=dry_run 模拟 / 2=ths 同花顺实单）。

也可以分开启动：`启动中转服务.bat`、`启动执行端.bat`。
脚本用 `%~dp0` 相对路径，整个项目文件夹拷到别的机器也能直接用（机器上需装 Python 3.10+）。

### 方式二：手动命令行

```powershell
# 1. 安装依赖（建议用虚拟环境）—— 必须两步，easytrader 锁定 pywinauto 0.6.6
pip install -r requirements.txt
pip install pywinauto==0.6.8     # 覆盖到 0.6.8（64位Python 批量 SendInput bug 修复版）

# 2. 启动中转服务（窗口1）
cd relay_server
python app.py
# 看到 "Running on http://0.0.0.0:5010" 即成功

# 3. 运行联调测试（窗口2）
cd tools
python test_signal.py
# 自动模拟：聚宽上报3条信号(含1条重复) -> 密钥校验 -> 执行端拉单 -> 回报 -> 状态汇总
# 最后一行 "全部通过" 即链路正常
```

然后启动执行端做端到端演练（默认 `MODE="dry_run"`，只模拟不下单）：

```powershell
cd executor
python main.py
```

### 没有聚宽账号？用「模拟聚宽」玩全链路

```powershell
cd tools
python jq_simulator.py demo      # 一键发 3 条示例信号，并显示处理结果
python jq_simulator.py           # 交互模式，像聊天一样发信号
```

交互模式支持的命令：

```
buy  600519.XSHG 100 1500.5      买入（代码 数量 价格，价格可省略）
sell 000001.XSHE 200 12.5        卖出
status                           查看信号统计、执行端心跳、最近信号
demo                             发送 3 条示例信号
```

只要中转服务 + 执行端（dry_run）在运行，模拟发出的信号会在几秒内被拉取、
过风控、记为 accepted——这就是将来聚宽真实接入后的完整流程，唯一区别是
信号来源从"模拟脚本"换成"聚宽策略"（届时改用 `joinquant/jq_signal.py`）。

## 切换三种下单方式

编辑 `executor/config.py` 的 `MODE`：

| MODE | 方案 | 还需要做什么 |
|------|------|--------------|
| `dry_run` | 模拟模式（默认） | 无，先跑通链路 |
| `ths` | A. 同花顺客户端 | `pip install easytrader pillow`；同花顺客户端登录并**关闭自动升级**；填 `THS_XIADAN_PATH` 指向 `xiadan.exe` |
| `thsauto` | B. 安卓模拟器 | 装雷电模拟器+同花顺APP；`thsauto run --host=0.0.0.0 --port=5001`；填 `THSAUTO_URL` |
| `miniqmt` | C. QMT 官方接口（最推荐） | 券商开通 QMT；登录 miniQMT；填 `QMT_PATH`、`QMT_ACCOUNT_ID`；把 QMT 目录里的 `xtquant` 文件夹复制到 executor 目录 |

单独调试某个适配器：`python broker_ths.py` / `python broker_thsauto.py` / `python broker_miniqmt.py`（都会打印资金和持仓）。

## Web 控制台（浏览器里管账户、下单、看审计）

中转服务自带网页控制台，不用装任何客户端：

```
http://<中转服务IP>:5010/console/          # 本机就是 http://127.0.0.1:5010/console/
```

默认管理员 `admin` / `admin123`（可在 `relay_server/config.py` 或环境变量 `RELAY_WEB_ADMIN_PASSWORD` 里改），**登录后请立刻点右上角「改密码」**。

### 能做什么

| 页面 | 功能 | 最低角色 |
|------|------|---------|
| 概览 | 中转版本、执行端在线状态、信号与任务统计、最近信号 | 只读 |
| 手动下单 | 填代码/方向/数量/价格下单；按委托号撤单 | 交易员 |
| 账户查询 | 资金、持仓、当日委托、当日成交（实时向券商客户端查） | 只读 |
| 任务队列 | 每次操作的执行记录与结果 | 只读 |
| 用户管理 | 新建用户、改角色、禁用/启用、重置密码、删除 | 管理员 |
| 审计日志 | 谁、何时、从哪个 IP、做了什么、结果如何 | 管理员 |

三种角色：**viewer 只读**（看概览与账户）/ **trader 交易员**（+ 下单撤单）/ **admin 管理员**（+ 用户与审计）。

### 它是怎么工作的

控制台本身不碰券商——中转服务一般在云上，够不着你本机的同花顺。链路是：

```
浏览器 → 中转服务(写入 tasks 表) → 本机执行端(轮询领取) → 券商客户端 → 回报 → 浏览器显示
```

所以**控制台能用的前提是执行端在线**；离线时会明确提示"执行端离线，请先启动执行端"，不会静默失败。

三点特别说明：

1. **控制台下单同样过风控**——走的是和聚宽信号同一套 `risk.py`（白名单/单笔上限/日限额/持仓核对/价格偏差），不会因为是人工点的就跳过。
2. **价格留空 = 用最新价**——A 股客户端只支持限价委托，留空时执行端自动取腾讯行情最新价；取不到会提示你填价格。
3. **防重复下单**——15 秒内相同的委托会被拦截（防手抖双击）；执行端离线时下单直接拒绝（503），不会把任务堆在队列里等着。

### 控制台自测

```powershell
# 中转服务 + dry_run 执行端都在运行时（不需要券商账号）
python tools/test_console.py
# 覆盖 36 项：页面/登录/会话/权限/用户管理/审计/账户查询/下单/撤单/防重复
```

## 聚宽端接入

1. 打开 `joinquant/jq_signal.py`，改 `RELAY_URL`（云端部署就填服务器 IP）和 `API_KEY`。
2. 把整个文件内容复制到聚宽策略**代码最开头**。
3. 策略主体的 `order()` / `order_target()` / `order_value()` / `order_target_value()` 调用不用改任何一行——下单成功会自动把信号发到中转服务。
4. 联调时可把 `DRY = True`，只打印不上报。

## 安全设计

- **双密钥**：聚宽用 `SIGNAL_API_KEY`，执行端用 `EXECUTOR_API_KEY`，互不通用（请求头 `X-API-Key`）。
- **幂等**：以聚宽 `order_id` 唯一索引落库，重复信号自动去重，网络重试不会重复下单。
- **积压保护**：待处理信号超过 `MAX_PENDING`(500) 说明执行端挂了，中转拒收新信号。
- **信号过期**：超过 24 小时没人领的信号自动作废，防止隔夜旧单被突然执行。
- **执行端风控**（`risk.py`）：代码白名单、单笔上限、当日累计上限、卖出必须核对持仓、信号价偏离最新价超 3% 拒绝。
- **审计**：所有信号/回报/心跳都落在 `relay_data.db`（SQLite），执行端另有 `executor.log`。
- **控制台账号**：密码用 PBKDF2-HMAC-SHA256 + 每用户随机盐存储，数据库里没有明文。
- **控制台权限**：viewer 只读 / trader 可下单 / admin 可管用户；每次请求都校验账号仍处于启用状态（禁用即刻生效）。
- **控制台审计**：登录、登录失败、查询、下单、撤单、用户增删改全部记入 `audit_log`（含操作人、角色、IP、结果）。
- **控制台会话**：登录态为 Flask 签名 Cookie（HttpOnly + SameSite=Lax），默认 12 小时过期；公网部署务必设置 `RELAY_WEB_SECRET`。
- **控制台防重与兜底**：15 秒内相同委托拒收；执行端离线时查询/下单直接拒绝；任务超时自动作废。

## 方案A实战踩坑记录（2026-09-11 本机实测）

在你的机器（64位 Python 3.13 + 新版同花顺5.0客户端）上做全自动测试时，
依次遇到并解决了以下问题，修复已全部固化进代码：

| 问题 | 现象 | 解决（已固化位置） |
|------|------|--------------------|
| **批量 SendInput 被静默拦截** | pywinauto 报 `SendInput() inserted only 0 out of 2 keyboard events`；实测单事件注入正常、按下+抬起批量注入 0/2（GetLastError=0），疑似本机安全软件过滤 | 升级 pywinauto 到 0.6.8 + `executor/pywinauto_compat.py 把批量调用拆成逐事件发送（broker_ths.py 已强制导入） |
| **easytrader 锁定 pywinauto==0.6.6，0.6.8 缺函数名 | `ImportError: cannot import name 'SetForegroundWindow' | `pywinauto_compat.py 注入兼容垫片 |
| **新版同花顺不导出临时 Excel | 查询报 `No such file ... tmp*.xls | `user.grid_strategy = easytrader.grid_strategies.Copy（剪贴板读表格） |
| **Windows 前台锁 | `SetForegroundWindow 失败，按键无效果 | `executor/foreground.py ALT解锁+AttachThreadInput 强制置前，每次操作前调用 |
| **委托价必须落在涨跌停内 | 挂现价 50% 被拒："超过涨跌限制 | 测试脚本改为挂现价 92%（约 -8%） |
| **cancel_entrust 双击定位失效 | 新版表格控件 CVirtualGridCtrl 坐标定位不到 | 改用"读撤单表→确认唯一→点[全撤]"路径（broker_ths.cancel_order） |
| **委托刚提交时列表读不到 | today_entrusts 返回 0 条但单子是活的 | 用"资金冻结 ≈ 委托金额+佣金"兜底判断（420.43 = 420.30 + 0.13 佣金） |
| **验证码风控 | 自动操作频繁后弹验证码窗口，拦截表格复制，查询返回空 | `executor/captcha.py` 三级处理：ddddocr 自动识别 → 蜂鸣+存图转人工（90秒）→ 全失败才关弹窗。触发后自动降频 120 秒 |
| **验证码 set_text 必败（关键） | 识别结果完全正确仍报"验证码错误"：easytrader 内置用 WM_SETTEXT 直写+发ENTER，同花顺不认（模拟盘实测5连错） | `captcha.py` 改模拟人工方式：清空后逐字键盘输入 + 鼠标点[确定]，实测一次通过 |
| **交易线程卡死在弹窗循环 | buy/sell 卡死不动：easytrader `_handle_pop_dialogs` 不认识验证码弹窗，死循环（每0.5s查一次永不退出） | `pywinauto_compat` 劫持该循环：验证码优先走强流程 + 30秒超时兜底 |
| **剪贴板垃圾被当表格数据 | 持仓查询读回控制台文本等无关内容：同花顺复制失败时剪贴板残留旧数据 | 查询前清空剪贴板 + 数据合法性校验 + 重试（broker_ths._read_with_retry） |
| **easytrader 验证码分支依赖 pytesseract | 未装 Tesseract 时持仓查询直接报 ModuleNotFoundError | `pywinauto_compat.patch_easytrader_captcha` 把其识别函数替换为 ddddocr，并劫持其验证码分支 |

**实测结论（2026-09-11 模拟账号全流程）：信号 → 中转 → 执行端 → 同花顺真实委托（6254219522，510300 买入100股 @4.21）→ 回报 accepted → 全自动通过 2 次验证码 → 撤单 → 资金解冻 0 差值，全程零人工干预。**
方案A在本机已具备接入执行端的条件（`MODE="ths"`）。生产环境建议：
- 轮询间隔别低于 5 秒，降低验证码风控触发概率
- 开机自启同花顺并保持已登录、关闭自动升级
- 更稳定的长久之计仍是 miniQMT（方案C）

## 同花顺验证码自动处理（executor/captcha.py）

同花顺检测到频繁自动操作会弹验证码窗口拦截，这是无法根除的反机器人机制，
本项目用"三级降级"把它的影响降到最低：

1. **自动识别**（主力）：`ddddocr` 深度学习 OCR 抓图识别 → 填入 → 回车，
   最多重试 5 轮（识别错一次图片会自动刷新）。对真实验证码实测识别准确。
2. **人工兜底**：连续识别失败时，蜂鸣报警 3 声 + 验证码图片存到
   `executor/captcha_last.png`，等你在客户端弹窗里输入（90 秒内都认，
   检测到你填好会自动帮你按回车）。
3. **最后手段**：点取消关闭弹窗，操作如实报 failed，绝不带病重试。

另外每处理一次验证码，执行端自动降频 120 秒，减少再次触发。
可用 `python executor/captcha.py` 单独自测（只检测弹窗，不做任何交易）。

## 中转服务部署：exe 或 Docker

中转服务是纯信号调度（不碰券商客户端），适合 7x24 跑在云端。两种打包方式：

> **发版约定（自 v1.3.0 起）**：所有产物统一命名 `名称-版本号-时间`，
> 例如 `relaygo-relay-v1.7.0-<时间>-win64.exe`。版本号取自 `relay_server/config.py`
> 的 `VERSION`，时间戳取打包当时 `YYYYMMDD-HHMM`。一次发版同时产出
> **中转 exe + 执行端 exe + docker 源码包 + 用户手册 HTML**，统一放进
> `release/<版本号-时间>/` 目录并附 `md5.txt`。一键发版：`python release/build_all.py`。
> 失败后只补收尾（包已单独构建好）：`set SKIP_BUILD=1 & python release/build_all.py`；
> 指定时间戳：`set RELEASE_TS=20260911-1602 & python release/build_all.py`。
> 打包中间产物都落在 `release/_build/`，可随时整目录删除，不影响发布包。

### exe 部署（Windows 服务器/NAS）

```powershell
# 重新打包（版本号取自 relay_server/config.py 的 VERSION，时间戳自动生成）
python release/build_exe.py
# 产物: release/<版本-时间>/relaygo-relay-<版本>-<时间>-win64.exe
#       （自带自检：真跑 exe 验证 /api/status 与 /console/ 控制台页面）

# 部署: exe 放任意目录双击即可，数据库 relay_data.db 自动生成在 exe 旁边。
# 第一次运行会自动生成带中文说明的 .env；双击「中转服务-配置.bat」→ 选 1 问答式填写，
# 保存重启生效（也可用记事本改 .env，或走环境变量——高级用户）:
set RELAY_SIGNAL_KEY=你的聚宽密钥
set RELAY_EXECUTOR_KEY=你的执行端密钥
set RELAY_WEB_SECRET=一段随机字符串
set RELAY_WEB_ADMIN_PASSWORD=你的控制台管理员密码
relaygo-relay-v1.7.0-<时间>-win64.exe
# 然后浏览器打开 http://127.0.0.1:5010/console/
```

### Docker 部署（Linux 服务器，推荐云上用这个）

```bash
# 方式一：直接用仓库源码
docker compose up -d --build          # 构建并启动
curl http://127.0.0.1:5010/api/status # 看到 version 即成功
# 密钥在 docker-compose.yml 的 environment 里改
# 数据落在 named volume jq-relay-data，容器重建不丢

# 方式二：用发布的 docker 源码包（无需仓库）
# 解压 relaygo-relay-docker-<版本>-<时间>.zip 后，进入目录执行:
docker compose up -d --build
```

打包 docker 源码包：`python release/build_docker.py`。

### 执行端打包 exe（Windows 上跑，UI 自动化需要桌面）

```powershell
# 用带 easytrader/ddddocr/pywinauto 的 venv 打包（脚本会自动用 venv 的 python）
python release/build_executor_exe.py
# 产物: release/<版本-时间>/relaygo-executor-<版本>-<时间>-win64.exe
# 部署: 放到执行端机器任意目录，双击即可。
# 第一次运行会自动在 exe 旁边生成带中文说明的 .env 配置文件；
# 双击「执行端-配置.bat」→ 选 1 问答式填写（中转地址/密钥/同花顺路径），保存重启生效。
# 也可以用记事本直接改 .env，或走环境变量（高级用户）:
set EXECUTOR_MODE=ths
set EXECUTOR_RELAY_URL=http://中转服务IP:5010
set EXECUTOR_API_KEY=与中转服务 RELAY_EXECUTOR_KEY 一致
relaygo-executor-v1.7.0-<时间>-win64.exe
# 日志 executor.log 生成在 exe 旁边
```

### 不用命令行改配置（.env，v1.4.0 起推荐）

- 程序首次运行自动在 exe 旁生成 `.env`（UTF-8 带 BOM，记事本中文不乱码），已存在则**绝不覆盖**。
- 发布目录附带两个双击式配置 bat：`1`=问答式填写（写前自动备份 `.env.bak`），`2`=记事本打开 `.env`。
- 优先级：**系统环境变量 > .env > 代码默认值**（.env 只补空缺，不覆盖已设环境变量）。
- 启动时打印**配置摘要**：每项生效值 + 来源（环境变量/.env/默认）+ 密钥脱敏；写错类型（如数字写成中文）不崩，用默认值并中文提示哪一行错了。
- 执行端原有 4 个可配参数之外，本次补齐 10 个：`EXECUTOR_MAX_SINGLE_AMOUNT / MAX_DAILY_AMOUNT / CODE_WHITELIST / MAX_PRICE_DEVIATION / THS_XIADAN_PATH / THSAUTO_URL / THSAUTO_ADDR / QMT_PATH / QMT_ACCOUNT_ID / QMT_ACCOUNT_TYPE`。

所有配置项都支持环境变量覆盖：`RELAY_HOST / RELAY_PORT / RELAY_DB_PATH /
RELAY_SIGNAL_KEY / RELAY_EXECUTOR_KEY / RELAY_MAX_PENDING / RELAY_TTL_HOURS /
RELAY_WEB_SECRET / RELAY_WEB_ADMIN / RELAY_WEB_ADMIN_PASSWORD / RELAY_TASK_WAIT / RELAY_TASK_TTL`。
（执行端同样支持：`EXECUTOR_MODE / EXECUTOR_RELAY_URL / EXECUTOR_API_KEY / EXECUTOR_POLL_INTERVAL` 及上述 10 个新键。）
（执行端无法 docker 化——UI 自动化必须跑在有同花顺/miniQMT 的 Windows 桌面上。）

## 部署到云服务器（可选）

聚宽云端访问 `http://127.0.0.1` 是不行的，两种选择：

1. **中转服务上云**：买最便宜的云服务器，`python app.py`（`HOST="0.0.0.0"`），在云控制台安全组放行 5010 端口，聚宽填 `http://服务器IP:5010`；执行端在自己电脑上改 `RELAY_URL` 指向它。
2. **全部本机 + 内网穿透**：本机跑中转+执行端，用 frp/花生壳等把 5010 映射到公网（新手建议直接买云服务器，更省心）。

⚠️ 云服务器上务必：改掉默认密钥、只放行 5010 端口。

## 上线路径（重要）

1. **阶段0**：`dry_run` + 聚宽模拟盘，连续 5 个交易日信号 100% 到达、无重复。
2. **阶段1**：切真实 MODE，100 股级最小仓位，每笔人工核对（在中转 `GET /api/signals` 和券商客户端双向核对）。
3. **阶段2**：核对 `risk.py` 参数后放开限额；补成交查询闭环。
4. **合规**：2025-07 起程序化交易需向券商报告后方可开展，先联系客户经理完成首次报告。

## 常见问题

| 现象 | 排查 |
|------|------|
| 聚宽上报 401 | `jq_signal.py` 的 API_KEY 与 relay_server/config.py 不一致 |
| 执行端拉不到单 | EXECUTOR_API_KEY 不一致；或中转服务没启动；`python tools/test_signal.py` 复测 |
| easytrader 连接失败 | 路径必须是 `xiadan.exe`；用管理员运行；客户端必须已登录交易账号 |
| miniQMT connect 失败 | miniQMT 客户端没登录；QMT_PATH 不是 userdata_mini 目录 |
| 同花顺下单偶发失败 | 客户端弹窗/升级导致控件失效；锁版本、关自动升级，失败会自动回报 failed |

## 许可证

本项目采用 **MIT License**，全文见 [LICENSE](LICENSE)。第三方组件的许可证与商标
归属见 [NOTICE](NOTICE)。

## 关于「授权验证」（重要）

`relay_server/license_guard.py` 是作者为自己发行版准备的卡密授权模块，对接一个自建的
卡密服务端。开源仓库里它**是完整源码**，但服务端地址与 API Key 的默认值已被替换成
占位值（`http://127.0.0.1:9527` / `change-me-license-api-key`），因此**开箱即用时会
停在激活页**。两种处理方式：

- **你只是想用这套系统**：在 `.env` 里加一行 `RELAY_LICENSE_ENABLED=0` 关掉授权校验
  （开发仓库没有 `release.lock`，这个开关是生效的）。
- **你想自己接一套卡密服务**：改 `relay_server/config.py` 的 `LICENSE_URL` 与
  `LICENSE_API_KEY` 指向你自己的服务端。

注意：源码公开即意味着这个开关任何人都能关掉。作者正式发行版靠打包时写入的
`release.lock` 标记文件把该开关强制锁为开启（见 `relay_server/config.py`
里的 `_license_release_locked()`），源码仓库里没有这个文件。

## 风险提示

- 这是会**真实下单**的工具。请先在 `dry_run` 模式下跑通全链路，再用最小仓位实盘验证。
- 2025-07 起，程序化交易需向券商完成报告后方可开展，请先联系客户经理办理首次报告。
- 「一键全撤」「清除全部记录」等控制台危险操作请谨慎使用。
