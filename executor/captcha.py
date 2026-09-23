# -*- coding: utf-8 -*-
"""
同花顺验证码弹窗自动处理（解决"验证码拦截"问题的独立模块，非侵入式）。

弹窗结构（与 easytrader grid_strategies.py 中实测一致）：
  - 弹窗窗口：top_window()，标题含 "验证码"
  - 验证码图片：control_id=0x965, class_name="Static"（点击图片可换一张）
  - 输入框：    control_id=0x964, class_name="Edit"
  - 错误提示：  control_id=0x966, class_name="Static"（弹窗存在时才读得到）
  - 确认：      向弹窗发送 {ENTER}
  - 取消：      Button2 / 标题含"取消"的按钮

处理策略（三级降级）：
  1. ddddocr 自动识别（pip install ddddocr，实测同花顺验证码识别率高）
  2. pytesseract 识别（需另装 Tesseract 程序，一般没有）
  3. 人工兜底：蜂鸣报警 + 验证码图片存盘 + 等待人工在弹窗里输入/点确定（最长 90 秒）

用法：
  from captcha import handle_captcha
  handle_captcha(user)   # user = easytrader 的 universal_client 对象
"""
import logging
import os
import time

log = logging.getLogger("captcha")

# 弹窗控件 ID（同花顺通用，与 easytrader 内置一致）
CTRL_IMAGE = 0x965
CTRL_EDIT = 0x964
CTRL_MSG = 0x966

AUTO_ROUNDS = 5          # OCR 自动尝试轮数（失败一次图片会自动刷新）
MANUAL_WAIT_SEC = 90     # 自动识别全失败后，等人工输入的最长秒数
IMG_SAVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "captcha_last.png")

_ocr = None
_ocr_failed = False


def _get_ocr():
    """惰性初始化 ddddocr（加载模型约 1 秒，只做一次）。"""
    global _ocr, _ocr_failed
    if _ocr is not None or _ocr_failed:
        return _ocr
    try:
        import ddddocr
        _ocr = ddddocr.DdddOcr(show_ad=False)
        log.info("ddddocr 已加载，验证码自动识别可用")
    except Exception as e:
        _ocr_failed = True
        log.warning("ddddocr 不可用(%s)，将降级为人工输入验证码", e)
    return _ocr


def _ocr_image(pil_img):
    """按可用性依次尝试 ddddocr / pytesseract。返回识别串或 ''。"""
    ocr = _get_ocr()
    if ocr is not None:
        try:
            import io
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            text = ocr.classification(buf.getvalue())
            return "".join(str(text).split())
        except Exception as e:
            log.warning("ddddocr 识别异常: %s", e)
    try:
        import pytesseract
        return "".join(
            pytesseract.image_to_string(pil_img.convert("L")).split())
    except Exception:
        return ""


def find_dialog(user):
    """返回验证码弹窗 wrapper；不存在返回 None。"""
    try:
        top = user.app.top_window()
        if "验证码" in (top.window_text() or ""):
            return top
        dlg = top.window(class_name="Static", title_re="验证码")
        if dlg.exists(timeout=0.3):
            return top
    except Exception:
        pass
    return None


def _fill_and_confirm(dlg, code):
    """
    填入验证码并确认 —— 模拟人工方式（实测关键）：
    easytrader 内置的 set_text 直写 + 发 ENTER 会被同花顺判"验证码错误"（即使
    识别完全正确，2026-09-11 模拟盘实测 5 连错）；改为清空后逐字键盘输入 +
    鼠标点[确定]按钮后一次通过。pywinauto 已打逐事件补丁，键盘事件可正常注入。
    """
    import pywinauto.keyboard
    edit = dlg.window(control_id=CTRL_EDIT, class_name="Edit")
    try:
        edit.set_focus()
        pywinauto.keyboard.send_keys("^a{DELETE}")   # 清空可能残留的内容
        pywinauto.keyboard.send_keys(str(code))      # 逐字键盘输入
    except Exception:
        try:
            edit.set_text(str(code))                 # 兜底：直写文本
        except Exception:
            pass
    time.sleep(0.2)
    # 优先鼠标点[确定]按钮（等效人工点击），失败再回退 ENTER
    try:
        dlg.window(class_name="Button", title_re="确定.*").click_input()
        return
    except Exception:
        pass
    try:
        dlg.set_focus()
        pywinauto.keyboard.send_keys("{ENTER}")
    except Exception:
        pass


def _manual_fallback(user, dlg):
    """人工兜底：报警 + 存图 + 等人在弹窗里完成输入。"""
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1000, 300)
            time.sleep(0.2)
    except Exception:
        pass
    try:
        dlg.window(control_id=CTRL_IMAGE, class_name="Static") \
           .capture_as_image().save(IMG_SAVE_PATH)
        log.warning("验证码图片已保存: %s（请在客户端弹窗中输入后点确定，"
                    "或在 %s 秒内完成）", IMG_SAVE_PATH, MANUAL_WAIT_SEC)
    except Exception:
        log.warning("无法保存验证码图片，请直接看客户端弹窗输入")
    deadline = time.time() + MANUAL_WAIT_SEC
    while time.time() < deadline:
        if find_dialog(user) is None:
            return True            # 人点掉了
        try:
            cur = dlg.window(control_id=CTRL_EDIT, class_name="Edit") \
                     .window_text().strip()
            if len(cur) >= 4:      # 人填好了，帮TA按回车
                _fill_and_confirm(dlg, cur)
                time.sleep(0.8)
                if find_dialog(user) is None:
                    return True
        except Exception:
            if find_dialog(user) is None:
                return True
        time.sleep(1)
    return False


def handle_captcha(user, auto_rounds=AUTO_ROUNDS):
    """
    检测并处理验证码弹窗。
    返回: 'none'=没有弹窗 / 'solved'=已解决 / 'closed'=放弃并关闭 / 'failed'=未解决
    """
    dlg = find_dialog(user)
    if dlg is None:
        return "none"

    log.warning("检测到验证码弹窗，开始自动识别...")
    for i in range(auto_rounds):
        try:
            img_ctrl = dlg.window(control_id=CTRL_IMAGE, class_name="Static")
            img = img_ctrl.capture_as_image()
            code = _ocr_image(img)
            log.info("第%d次识别结果: %r", i + 1, code)
            if 4 <= len(code) <= 6 and code.isalnum():
                _fill_and_confirm(dlg, code)
                time.sleep(1.0)
                if find_dialog(user) is None:
                    log.info("验证码已通过（第%d次尝试）", i + 1)
                    return "solved"
                # 没过：点一下图片刷新，准备下一次
                try:
                    img_ctrl.click()
                    time.sleep(0.5)
                except Exception:
                    pass
        except Exception as e:
            log.warning("第%d次自动识别异常: %s", i + 1, e)
            time.sleep(0.5)
            if find_dialog(user) is None:
                return "solved"
            dlg = find_dialog(user)

    # 自动识别失败 -> 人工兜底
    if _manual_fallback(user, dlg):
        return "solved"

    # 仍卡着 -> 点取消关闭，避免阻塞后续操作（调用方会拿到失败并如实回报）
    try:
        dlg = find_dialog(user)
        if dlg is not None:
            try:
                dlg.window(class_name="Button", title_re="取消.*").click_input()
            except Exception:
                dlg.Button2.click_input()
            time.sleep(0.5)
            return "closed"
    except Exception:
        pass
    return "failed"


def is_available():
    """ddddocr 是否可用（供启动时打印状态）。"""
    return _get_ocr() is not None


if __name__ == "__main__":
    # 单独调试：python captcha.py —— 连上客户端并处理可能存在的弹窗
    import pywinauto_compat   # noqa: F401
    import easytrader
    import config as cfg
    user = easytrader.use("universal_client")
    user.grid_strategy = easytrader.grid_strategies.Copy
    user.connect(cfg.THS_XIADAN_PATH)
    print("ddddocr 可用:", is_available())
    print("处理结果:", handle_captcha(user))
