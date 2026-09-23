# -*- coding: utf-8 -*-
"""
两端 config.py 的 .env 端到端自测（真实子进程导入 config，验证配置确实生效）。

覆盖：
  * 执行端 .env 里改的 10 个原本没有环境变量入口的项是否真的生效
  * 中转服务 .env 是否生效
  * 系统环境变量是否压过 .env
  * 写错的值是否回落默认值并给出中文告警
  * 启动摘要是否正常输出且密钥脱敏

运行：venv\\Scripts\\python.exe tools\\test_env_config.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable

TMP = tempfile.mkdtemp(prefix="jq_env_cfg_")
PASS, FAIL = [], []


def _read_version(py_dir):
    """从 <py_dir>/config.py 读出 VERSION。

    版本号是发版时改动的，硬编码进断言会导致每次发版都假失败——这里动态读取。
    """
    import re
    path = os.path.join(ROOT, py_dir, "config.py")
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*VERSION\s*=\s*[\"']([^\"']+)", line)
            if m:
                return m.group(1)
    raise RuntimeError("读不到 VERSION: %s" % path)


EXEC_VER = _read_version("executor")
RELAY_VER = _read_version("relay_server")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[OK ]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if (detail and not cond) else ""))


def run_config(py_dir, var, env_path, code, extra=None):
    """在指定子目录里用子进程导入 config 并执行 code，返回 stdout。

    先剔除本机可能存在的 EXECUTOR_*/RELAY_* 环境变量，保证测试结果只由 .env 决定。
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("EXECUTOR_", "RELAY_", "JQ_"))}
    env["PYTHONIOENCODING"] = "utf-8"
    env[var] = env_path
    if extra:
        env.update(extra)
    r = subprocess.run([PY, "-c", code], cwd=os.path.join(ROOT, py_dir), env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.stdout or ""


EXEC_ENV = os.path.join(TMP, "executor.env")
with open(EXEC_ENV, "w", encoding="utf-8") as f:
    f.write("# 执行端测试配置\n"
            "EXECUTOR_MODE=ths\n"
            "EXECUTOR_RELAY_URL=http://192.168.1.88:5010\n"
            "EXECUTOR_API_KEY=my-secret-key-9999\n"
            "EXECUTOR_POLL_INTERVAL=7\n"
            "EXECUTOR_MAX_SINGLE_AMOUNT=2000\n"
            "EXECUTOR_MAX_DAILY_AMOUNT=8000\n"
            "EXECUTOR_CODE_WHITELIST=600519, 000001\n"
            "EXECUTOR_MAX_PRICE_DEVIATION=0.05\n"
            "EXECUTOR_THS_XIADAN_PATH=D:\\同花顺软件\\同花顺\\xiadan.exe\n")

print("\n[1] 执行端：.env 里的取值是否生效")
out = run_config("executor", "EXECUTOR_ENV_FILE", EXEC_ENV,
                 "import config;"
                 "print('MODE', config.MODE);"
                 "print('URL', config.RELAY_URL);"
                 "print('POLL', config.POLL_INTERVAL);"
                 "print('SINGLE', config.MAX_SINGLE_AMOUNT);"
                 "print('DAILY', config.MAX_DAILY_AMOUNT);"
                 "print('WHITE', config.CODE_WHITELIST);"
                 "print('DEV', config.MAX_PRICE_DEVIATION);"
                 "print('THS', config.THS_XIADAN_PATH)")
check("执行模式生效", "MODE ths" in out, out)
check("中转地址生效", "URL http://192.168.1.88:5010" in out, out)
check("轮询间隔生效", "POLL 7" in out, out)
check("单笔限额生效（原本没有环境变量入口）", "SINGLE 2000" in out, out)
check("当日限额生效（原本没有环境变量入口）", "DAILY 8000" in out, out)
check("白名单解析成列表", "WHITE ['600519', '000001']" in out, out)
check("价格偏差生效", "DEV 0.05" in out, out)
check("中文路径未乱码", "THS D:\\同花顺软件\\同花顺\\xiadan.exe" in out, out)

print("\n[2] 执行端：启动摘要（含脱敏）")
out = run_config("executor", "EXECUTOR_ENV_FILE", EXEC_ENV,
                 "import config; print(config.startup_report())")
check("打印了执行端标题", ("执行端 %s" % EXEC_VER) in out, out)
check("密钥已脱敏", "my-secret-key-9999" not in out and "9999" in out, out)
check("显示了配置来源文件", "executor.env" in out, out)
check("报告了同花顺路径是否找到", "未找到" in out or "已找到" in out, out)

print("\n[3] 执行端：系统环境变量压过 .env")
out = run_config("executor", "EXECUTOR_ENV_FILE", EXEC_ENV,
                 "import config; print('MODE', config.MODE, 'SINGLE', config.MAX_SINGLE_AMOUNT)",
                 extra={"EXECUTOR_MODE": "dry_run", "EXECUTOR_MAX_SINGLE_AMOUNT": "500"})
check("bat 传入的模式压过 .env", "MODE dry_run" in out, out)
check("系统环境变量压过 .env", "SINGLE 500" in out, out)

print("\n[4] 执行端：写错的值回落默认值并告警")
BAD_ENV = os.path.join(TMP, "bad.env")
with open(BAD_ENV, "w", encoding="utf-8") as f:
    f.write("EXECUTOR_POLL_INTERVAL=三秒\n这不是配置行\nEXECUTOR_MAX_SINGLE_AMOUNT=很多股\n")
out = run_config("executor", "EXECUTOR_ENV_FILE", BAD_ENV,
                 "import config; print('POLL', config.POLL_INTERVAL, "
                 "'SINGLE', config.MAX_SINGLE_AMOUNT); print(config.startup_report())")
check("整数写错回落默认值 3", "POLL 3" in out, out)
check("整数写错回落默认值 10000", "SINGLE 10000" in out, out)
check("坏行有中文告警", "[!]" in out and "已跳过" in out, out)
check("程序没有崩（能打印报告）", ("执行端 %s" % EXEC_VER) in out, out)

print("\n[5] 中转服务：.env 是否生效")
RELAY_ENV = os.path.join(TMP, "relay.env")
with open(RELAY_ENV, "w", encoding="utf-8") as f:
    f.write("RELAY_PORT=5099\n"
            "RELAY_SIGNAL_KEY=signal-secret-7777\n"
            "RELAY_MAX_PENDING=88\n"
            "RELAY_WEB_ADMIN_PASSWORD=pwd-4321\n")
out = run_config("relay_server", "RELAY_ENV_FILE", RELAY_ENV,
                 "import config;"
                 "print('PORT', config.PORT);"
                 "print('PENDING', config.MAX_PENDING);"
                 "print('SECRET', config.WEB_SECRET_KEY);"
                 "print(config.startup_report())")
check("端口生效", "PORT 5099" in out, out)
check("积压上限生效", "PENDING 88" in out, out)
check("会话密钥回落到默认值", "relay-web-secret-change-me-2026" in out, out)
check("中转摘要标题", ("中转服务 %s" % RELAY_VER) in out, out)
check("信号密钥已脱敏", "signal-secret-7777" not in out and "7777" in out, out)
check("控制台密码已脱敏", "pwd-4321" not in out and "4321" in out, out)

print("\n[6] 中转服务：端口写错回落默认值")
BADR = os.path.join(TMP, "badrelay.env")
with open(BADR, "w", encoding="utf-8") as f:
    f.write("RELAY_PORT=五零一零\n")
out = run_config("relay_server", "RELAY_ENV_FILE", BADR,
                 "import config; print('PORT', config.PORT)")
check("端口写错回落 5010 而不是崩溃", "PORT 5010" in out, out)

print("\n" + "=" * 62)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：%s" % FAIL)
    sys.exit(1)
print("全部通过")
