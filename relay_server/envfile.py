# -*- coding: utf-8 -*-
"""
.env 配置文件加载器（零依赖，纯标准库）。

为什么自己写、不用现成的 python-dotenv：
  Windows 上用户习惯用记事本改配置，另存时可能是 ANSI(GBK)，也可能是 UTF-8。
  本模块能自动判别编码，用户不必操心。同时也省掉一个第三方依赖
  （Docker 镜像与 PyInstaller 都不用额外处理）。

两个配置文件，各司其职：
  .env        程序首次运行自动生成（UTF-8 带 BOM，中文说明完整）。用户可以用记事本改。
  .env.local  「配置向导.bat」写入（纯 cmd 生成的 GBK/ASCII 文本）。
              优先级高于 .env —— 因为它是用户明确填写的答案。
  ★ 分开两个文件的原因：cmd 只能写 GBK，Python 写 UTF-8。混在同一个文件里会出现
    「一个文件两种编码」，届时无法可靠判别（GBK 汉字按 UTF-8 解有时竟然能解出来，
    但得到的是错字，例如「同」会变成「ͬ」）。分成两个文件后每个文件都是单一编码，
    判别 100% 可靠。

四条铁律（改动前请先读一遍）：
  1. 优先级：系统环境变量 > .env.local > .env > 代码默认值。
     配置文件只填「系统里还没有的键」，绝不覆盖真实环境变量 —— 否则
     「启动执行端.bat」里选的执行模式、打包自检用的临时变量都会被配置带偏，
     可能造成真实下单事故。
  2. 首次自动生成：程序旁边没有 .env 时自动写一份（带中文说明）。
     用户不需要知道「环境变量」是什么，用记事本打开改就行。
  3. 只补不覆盖：.env 已存在时不改动用户写下的任何内容；只有当本产品配置段缺失时，
     才把「用户文件里还没有的配置项」以注释参考的形式补在文件末尾。
  4. 解析错误不崩：坏行只记录警告并跳过，绝不让程序起不来（配置写错是最常见的新手问题）。
"""
import os
import sys

# 启动过程中收集的警告（配置项写错、文件不可写等），由程序启动时打印给用户看
WARNINGS = []

# 配置向导写入的文件名后缀（优先级高于主配置）
LOCAL_NAME = ".env.local"

_BOM = b"\xef\xbb\xbf"


def warn(msg):
    """记录一条给用户看的警告（自动去重，避免同一问题刷屏）。"""
    if msg not in WARNINGS:
        WARNINGS.append(msg)


def warning_text():
    """把警告拼成可直接打印的多行文本；没有警告时返回空串。"""
    if not WARNINGS:
        return ""
    return "\n".join("  [!] " + w for w in WARNINGS)


# ---------------- 路径 ----------------

def base_dir():
    """程序所在目录。

    exe（PyInstaller onefile）模式：exe 所在目录 —— 注意不是 sys._MEIPASS，
    那是运行时解压出来的临时目录，把配置写进去重启就没了。
    源码模式：本文件所在目录的上一级（即项目根目录）。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_paths(explicit_env_var=None):
    """返回要读取的配置文件列表（按优先级从低到高）。

    显式指定了路径（如 RELAY_ENV_FILE=D:\\relay\\my.env）时只读那一个，
    方便把中转端与执行端放同一目录却各用各的配置。
    """
    if explicit_env_var:
        p = os.environ.get(explicit_env_var, "").strip()
        if p:
            return [os.path.abspath(p)]
    d = base_dir()
    return [os.path.join(d, ".env"), os.path.join(d, LOCAL_NAME)]


# ---------------- 编码判别 ----------------

def decode(raw):
    """判别并解码文件内容，返回 (文本, 编码名)。

    规则（按顺序）：
      1. 有 UTF-8 BOM  -> 一定是 UTF-8（程序自己生成的文件都带 BOM）。
      2. 能按 UTF-8 解出，且结果里没有 U+0080~U+07FF 区间的字符 -> 就是 UTF-8。
         因为配置文件里要么是 ASCII，要么是中文（U+4E00 以上）；
         出现拉丁扩展/组合符号说明这是 GBK 汉字被当成 UTF-8 读的结果。
      3. 否则按 GBK 解（记事本「另存为 ANSI」就是这个编码）。
      4. 都不行则按 UTF-8 容错解码，保证不抛异常。
    """
    if raw.startswith(_BOM):
        return raw[len(_BOM):].decode("utf-8", "replace"), "utf-8"
    try:
        text = raw.decode("utf-8")
        if not any(0x80 <= ord(c) <= 0x7FF for c in text):
            return text, "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gbk"), "gbk"
    except Exception:
        return raw.decode("utf-8", "replace"), "utf-8"


def _parse(text):
    """把文本解析成 {键: 值}。同名以最后一次出现的为准。"""
    data = {}
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(";"):
            continue
        if s.lower().startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            warn("配置文件第 %d 行不是「配置项=值」的写法，已跳过：%s" % (i, s[:40]))
            continue
        key, val = s.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if not key:
            warn("配置文件第 %d 行缺少配置项名称，已跳过" % i)
            continue
        data[key] = val
    return data


def read(path):
    """读并解析一个配置文件；读不到返回空字典（不抛异常）。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}
    except Exception as e:
        warn("读取配置文件失败：%s（%s）" % (path, e))
        return {}
    return _parse(decode(raw)[0])


# ---------------- 生成 / 加载 ----------------

def _comment_out_known(template, known_keys):
    """把模板里「用户文件已经有」的生效行注释掉，避免补充段覆盖用户已有的取值。"""
    out = []
    for line in template.splitlines():
        s = line.lstrip()
        if s and not s.startswith(("#", ";")) and "=" in s:
            if s.split("=", 1)[0].strip() in known_keys:
                line = "# " + line
        out.append(line)
    return "\r\n".join(out)


def ensure(path, template, marker=None):
    """保证主配置文件存在。

    返回动作：created（首次生成）/ appended（补充了缺失段）/ exists（已有，未动）/
    failed（读写失败，仅警告不中断）。
    """
    if not os.path.exists(path):
        try:
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            # utf-8-sig：带 BOM，保证 Windows 记事本打开中文注释不乱码
            with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
                f.write(template)
            return "created"
        except Exception as e:
            warn("无法生成配置文件 %s：%s" % (path, e))
            return "failed"

    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception as e:
        warn("读取配置文件失败：%s（%s）" % (path, e))
        return "failed"

    text, enc = decode(raw)
    if marker and marker in text:
        return "exists"        # 本产品的配置段已存在

    known = _parse(text)
    body = _comment_out_known(template, known)
    note = ("\r\n\r\n"
            "# ------------------------------------------------------------\r\n"
            "# 以下为本程序自动补充的配置项说明。\r\n"
            "# 你原有配置文件里已经写过的项，已自动加上 # 号避免覆盖你的取值。\r\n"
            "# 需要使用这里的某项时，把行首的 # 去掉即可。\r\n"
            "# ------------------------------------------------------------\r\n")
    try:
        # 按文件原有编码追加，保持单一编码（见模块开头说明）
        with open(path, "ab") as f:
            f.write((note + body + "\r\n").encode(enc, "replace"))
        return "appended"
    except Exception as e:
        warn("无法向配置文件补充说明：%s（%s）" % (path, e))
        return "failed"


def load(paths):
    """按优先级合并读取配置，写入 os.environ（系统已有的键一律跳过）。

    返回 (合并后的全部键值, 本次真正生效的键列表)。
    """
    merged = {}
    for p in paths:
        merged.update(read(p))          # 后面的文件覆盖前面的（.env.local 优先）
    applied = []
    for k, v in merged.items():
        if k in os.environ:
            continue                    # 铁律 1：绝不覆盖真实环境变量
        os.environ[k] = v
        applied.append(k)
    return merged, applied


def setup(template=None, marker=None, explicit_env_var=None):
    """一步到位：定位文件 -> 首次自动生成 -> 读取生效。返回给启动报告用的小字典。"""
    paths = resolve_paths(explicit_env_var)
    main_path = paths[0]
    action = "missing"
    if template:
        if os.environ.get("JQ_ENVFILE_NOCREATE") == "1":
            # Docker 等场景禁止自动生成（容器里写了也没意义），但仍会读取已存在的文件
            action = "exists" if os.path.exists(main_path) else "disabled"
        else:
            action = ensure(main_path, template, marker)
    elif os.path.exists(main_path):
        action = "exists"
    data, applied = load(paths)
    return {"path": main_path,
            "paths": paths,
            "action": action,
            "parsed": data,
            "applied": applied,
            "local_used": os.path.exists(paths[-1]) if len(paths) > 1 else False}


def file_note(info):
    """把 setup() 的结果转成一行给用户看的中文说明。"""
    path = info.get("path", "")
    action = info.get("action")
    n = len(info.get("applied") or [])
    tail = "" if not info.get("local_used") else "，并叠加了 %s" % LOCAL_NAME
    if action == "created":
        return "配置来源  %s（首次运行已自动生成，双击「配置.bat」或用记事本即可修改）" % path
    if action == "appended":
        return "配置来源  %s（已自动补充缺失项的说明，你填过的值未被改动）%s" % (path, tail)
    if action == "exists":
        return "配置来源  %s（本次生效 %d 项）%s" % (path, n, tail)
    if action == "disabled":
        return "配置来源  未找到 %s，且已禁用自动生成（使用系统环境变量或代码默认值）" % path
    return "配置来源  %s（未找到，使用系统环境变量或代码默认值）" % path


# ---------------- 取值（带安全转换） ----------------

def mask(value):
    """敏感值脱敏显示：只露尾号，绝不回显明文密钥。"""
    v = "" if value is None else str(value).strip()
    if not v:
        return "(未设置)"
    if len(v) <= 4:
        return "已设置"
    return "已设置（尾号 %s）" % v[-4:]


def get_int(key, default):
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        warn("%s = %s 不是整数，已改用默认值 %s" % (key, raw, default))
        return default


def get_float(key, default):
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        warn("%s = %s 不是数字，已改用默认值 %s" % (key, raw, default))
        return default


def get_list(key, default=None):
    """逗号（或分号）分隔的列表；留空表示不限制。"""
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        return list(default or [])
    return [x.strip() for x in str(raw).replace(";", ",").split(",") if x.strip()]


def get_str(key, default=""):
    raw = os.environ.get(key)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip()


# ---------------- 启动报告 ----------------

def banner(title, lines):
    """统一的启动横幅，方便用户一眼确认自己配的东西生效了没有。"""
    head = "=" * 66
    out = [head, "  " + title, head]
    out.extend("  " + l for l in lines if l)
    warn_txt = warning_text()
    if warn_txt:
        out.append("  以下配置项有问题（不影响启动，已按默认值继续）：")
        out.append(warn_txt)
    out.append(head)
    return "\n".join(out)
