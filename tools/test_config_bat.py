# -*- coding: utf-8 -*-
"""
两个「配置.bat」的端到端自测：真的双击式运行一遍（用管道喂输入），
检查它写出的 .env 内容正确（含 GBK 中文路径），并验证程序能读出生效值。

运行：venv\\Scripts\\python.exe tools\\test_config_bat.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
if not os.path.exists(PY):
    PY = sys.executable
sys.path.insert(0, os.path.join(ROOT, "executor"))
import envfile  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s%s" % ("[OK ]" if cond else "[FAIL]", name,
                         ("  -> " + str(detail)) if (detail and not cond) else ""))


def run_bat(bat_name, answers, workdir, timeout=45):
    """把 bat 复制到工作目录，用管道喂输入跑一遍，返回 (输出文本, 退出码)。

    两个测试专用处理（不影响正式文件）：
      * 摘掉 chcp 936 —— 实测 chcp 会把重定向的 stdin 弄失效，导致 set /p 读不到输入
        而菜单空转。真实双击时 stdin 是控制台，chcp 完全正常，所以正式 bat 保留它。
      * 末尾补一串 0 —— EOF 后 set /p 立刻返回空值，补足 0 让它走到「退出」。
    超时则强杀并把已捕获输出带出来，方便定位卡在哪一步。
    """
    src = os.path.join(ROOT, bat_name)
    # 注意 newline=""：bat 必须是 CRLF，否则 cmd 解析 goto/标签会错乱
    text = open(src, encoding="gbk", newline="").read()
    runnable = text.replace("chcp 936 >nul", "rem chcp skipped in automated test")
    with open(os.path.join(workdir, bat_name), "w", encoding="gbk", newline="") as f:
        f.write(runnable)

    inp = os.path.join(workdir, "_input.txt")
    with open(inp, "w", encoding="ascii") as f:
        f.write(answers + "0\n" * 30)
    with open(inp, "rb") as fin:
        p = subprocess.Popen(["cmd", "/c", bat_name], cwd=workdir, stdin=fin,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    code = 0
    try:
        raw, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        raw, _ = p.communicate()
        raw = (raw or b"") + b"\n<<<TIMEOUT>>>"
        code = -1
    return (raw or b"").decode("gbk", "replace"), code


def read_env(path):
    raw = open(path, "rb").read()
    text, enc = envfile.decode(raw)
    return envfile.read(path), enc, text


print("\n[1] 执行端-配置.bat：走一遍问答，检查写出的 .env")
tmp1 = tempfile.mkdtemp(prefix="jq_bat_exec_")
for bat in ("执行端-配置.bat", "中转服务-配置.bat"):
    src = open(os.path.join(ROOT, bat), encoding="gbk", newline="").read()
    check("%s 是 GBK 且含 chcp 与 CRLF（正式文件未被改动）" % bat,
          "chcp 936" in src and "\r\n" in src, bat)
out, code = run_bat("执行端-配置.bat",
                    "1\n\nmy-secret-key-1234\n\n2\n\n0\n", tmp1)
env1 = os.path.join(tmp1, ".env")
check("bat 正常结束", code == 0, code)
check(".env 已生成", os.path.exists(env1), tmp1)
data1, enc1, _ = read_env(env1)
check("编码判为 GBK（cmd 写的）", enc1 == "gbk", enc1)
check("中转地址写入正确",
      data1.get("EXECUTOR_RELAY_URL") == "http://127.0.0.1:5010", data1.get("EXECUTOR_RELAY_URL"))
check("密钥写入正确",
      data1.get("EXECUTOR_API_KEY") == "my-secret-key-1234", data1.get("EXECUTOR_API_KEY"))
check("★ 中文默认路径未乱码",
      data1.get("EXECUTOR_THS_XIADAN_PATH") == "D:\\同花顺软件\\同花顺\\xiadan.exe",
      data1.get("EXECUTOR_THS_XIADAN_PATH"))
check("选 2 -> 模式 ths", data1.get("EXECUTOR_MODE") == "ths", data1.get("EXECUTOR_MODE"))
check("输出里有写入提示", "已写入配置文件" in out, out[-200:])

print("\n[2] 程序能否读出生效值（用 bat 写的那份配置）")
env = {k: v for k, v in os.environ.items() if not k.startswith(("EXECUTOR_", "RELAY_", "JQ_"))}
env["EXECUTOR_ENV_FILE"] = env1
env["PYTHONIOENCODING"] = "utf-8"
r = subprocess.run([PY, "-c",
                    "import config;"
                    "print('MODE', config.MODE);"
                    "print('KEY', config.EXECUTOR_API_KEY);"
                    "print('PATH', config.THS_XIADAN_PATH);"
                    "print(config.startup_report())"],
                   cwd=os.path.join(ROOT, "executor"), env=env,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   encoding="utf-8", errors="replace", timeout=120)
o = r.stdout or ""
check("模式生效", "MODE ths" in o, o)
check("密钥生效", "KEY my-secret-key-1234" in o, o)
check("★ 中文路径生效且未乱码",
      "PATH D:\\同花顺软件\\同花顺\\xiadan.exe" in o, o)
idx = o.find("=" * 66)
report = o[idx:] if idx >= 0 else o
check("★ 启动摘要里密钥已脱敏（只露尾号）",
      "my-secret-key-1234" not in report and "尾号 1234" in report, report)
check("启动摘要说明了配置来源", "配置来源" in report, report)

print("\n[3] 已存在配置文件时，向导不覆盖")
out2, code2 = run_bat("执行端-配置.bat", "1\n\n\n0\n", tmp1)
data1b, enc1b, _ = read_env(env1)
check("原有取值未被改动",
      data1b.get("EXECUTOR_API_KEY") == "my-secret-key-1234", data1b.get("EXECUTOR_API_KEY"))
check("程序补充说明后仍是单一编码（GBK 未被写坏）", enc1b == "gbk", enc1b)
check("补充的说明段确实写进去了", "自动补充" in open(env1, encoding="gbk", errors="replace").read()
      or "本程序自动补充" in open(env1, encoding="gbk", errors="replace").read(),
      open(env1, encoding="gbk", errors="replace").read()[-200:])
check("给出了改用记事本的提示", "已经存在" in out2 and "记事本" in out2, out2[-300:])

print("\n[4] 中转服务-配置.bat：走一遍问答")
tmp2 = tempfile.mkdtemp(prefix="jq_bat_relay_")
out3, code3 = run_bat("中转服务-配置.bat",
                      "1\nsig-key-8888\nexe-key-9999\npwd-2026\n\n0\n", tmp2)
env2 = os.path.join(tmp2, ".env")
check("bat 正常结束", code3 == 0, code3)
check(".env 已生成", os.path.exists(env2), tmp2)
data2, enc2, _ = read_env(env2)
check("编码判为 GBK", enc2 == "gbk", enc2)
check("聚宽密钥写入正确", data2.get("RELAY_SIGNAL_KEY") == "sig-key-8888",
      data2.get("RELAY_SIGNAL_KEY"))
check("执行端密钥写入正确", data2.get("RELAY_EXECUTOR_KEY") == "exe-key-9999",
      data2.get("RELAY_EXECUTOR_KEY"))
check("管理员密码写入正确", data2.get("RELAY_WEB_ADMIN_PASSWORD") == "pwd-2026",
      data2.get("RELAY_WEB_ADMIN_PASSWORD"))
check("端口为默认 5010", data2.get("RELAY_PORT") == "5010", data2.get("RELAY_PORT"))

print("\n[5] 程序读中转配置并脱敏显示")
env = {k: v for k, v in os.environ.items() if not k.startswith(("EXECUTOR_", "RELAY_", "JQ_"))}
env["RELAY_ENV_FILE"] = env2
env["PYTHONIOENCODING"] = "utf-8"
r = subprocess.run([PY, "-c",
                    "import config; print('PORT', config.PORT); print(config.startup_report())"],
                   cwd=os.path.join(ROOT, "relay_server"), env=env,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   encoding="utf-8", errors="replace", timeout=120)
o = r.stdout or ""
check("端口生效", "PORT 5010" in o, o)
check("★ 密钥与密码都脱敏了",
      "sig-key-8888" not in o and "pwd-2026" not in o and "8888" in o, o)

print("\n" + "=" * 62)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：%s" % FAIL)
    sys.exit(1)
print("全部通过")
