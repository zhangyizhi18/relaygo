# -*- coding: utf-8 -*-
"""
发布公共工具 —— 所有打包脚本共用，保证「名称-版本号-时间」三要素一致。

发版约定（自 v1.3.0 起，长期固定）：
  产物命名  : <组件>-<版本号>-<时间戳>-win64.<ext>
              组件: jq-relay（中转服务） / jq-executor（执行端） / jq-relay-docker（docker源码包）
              版本号: relay_server/config.py 的 VERSION，如 v1.3.0
              时间戳: 打包当时 YYYYMMDD-HHMM，如 20260911-1602
  发布目录  : release/<版本号>-<时间戳>/    （一次发版的所有产物集中放这里）
  使用方式  : 各脚本独立运行会自动取当前时间；用 build_all.py 统一发版时
              会用同一个 RELEASE_TS 环境变量锁定时间戳，保证三个包时间一致。
"""
import hashlib
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_DIR = os.path.join(ROOT, "release")
BUILD_DIR = os.path.join(RELEASE_DIR, "_build")     # 打包中间产物，勿放进版本目录

# 读中转服务的版本号（唯一版本源）
sys.path.insert(0, os.path.join(ROOT, "relay_server"))
import config  # noqa: E402

VER = config.VERSION                      # 如 v1.3.0


def release_ts():
    """发布用时间戳 YYYYMMDD-HHMM。build_all 会用环境变量锁定，保证三包一致。"""
    return os.environ.get("RELEASE_TS") or datetime.now().strftime("%Y%m%d-%H%M")


def out_dir(ts=None):
    """本次发版的输出目录 release/<版本>-<时间>/，不存在则创建。"""
    d = os.path.join(RELEASE_DIR, "%s-%s" % (VER, ts or release_ts()))
    os.makedirs(d, exist_ok=True)
    return d


def package_name(component, ts=None, ext="exe", platform="win64"):
    """标准产物文件名（不含目录）：<组件>-<版本>-<时间>[-<平台>].<ext>。
    ext 传空字符串则不带扩展名（供 PyInstaller --name 等场景使用）。"""
    parts = [component, VER, ts or release_ts()]
    if platform:
        parts.append(platform)
    name = "-".join(parts)
    return name + ("." + ext if ext else "")


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _has_modules(py, modules):
    """该解释器能否 import 指定的全部模块。"""
    code = "import " + ", ".join(modules)
    try:
        r = subprocess.run([py, "-c", code], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        return r.returncode == 0
    except Exception:
        return False


def _py_launcher_pythons():
    """通过 py 启动器列出本机所有 Python 路径（Windows）。"""
    out = []
    try:
        r = subprocess.run(["py", "-0p"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True)
        for line in (r.stdout or "").splitlines():
            for tok in line.split():
                if tok.lower().endswith("python.exe") and os.path.exists(tok):
                    out.append(tok)
    except Exception:
        pass
    return out


def pick_python(modules=()):
    """找一个同时装了 PyInstaller 与 modules 的 Python 解释器路径。

    优先级：环境变量 BUILD_PYTHON > 项目 venv > 当前解释器 > py 启动器列出的。
    找不到直接退出并给出明确提示，避免"用错解释器打出缺依赖的包"。
    """
    need = ("PyInstaller",) + tuple(modules)
    candidates = []
    if os.environ.get("BUILD_PYTHON"):
        candidates.append(os.environ["BUILD_PYTHON"])
    venv_py = os.path.join(ROOT, "venv", "Scripts", "python.exe")
    candidates.append(venv_py)
    candidates.append(sys.executable)
    candidates += _py_launcher_pythons()

    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if os.path.exists(c) and _has_modules(c, need):
            return c
    sys.exit(
        "[错误] 找不到同时具备 PyInstaller 与 %s 的 Python。\n"
        "       请在项目 venv 里执行: venv\\Scripts\\python.exe -m pip install pyinstaller\n"
        "       或设置环境变量 BUILD_PYTHON 指向合适解释器。" % ", ".join(modules))
