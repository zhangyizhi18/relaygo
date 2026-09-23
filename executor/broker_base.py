# -*- coding: utf-8 -*-
"""
券商适配基类 —— 三种下单方式都实现同一套接口，main.py 不关心底层是哪家。
子类必须实现：connect / buy / sell / query_positions / query_balance。

代码格式约定（内部统一用"聚宽格式" 600519.XSHG，各子类自行转换）：
  聚宽/中转信号: 600519.XSHG / 000001.XSHE / 510300.XSHG
  同花顺/thsauto: 600519 / 000001 / 510300            （6位纯数字）
  miniQMT:       600519.SH / 000001.SZ / 510300.SH
"""


def to_plain_code(jq_code):
    """600519.XSHG -> 600519"""
    return jq_code.split(".")[0]


def to_xt_code(jq_code):
    """600519.XSHG -> 600519.SH （miniQMT 格式）"""
    code, market = jq_code.split(".")
    return "%s.%s" % (code, "SH" if market == "XSHG" else "SZ")


def market_of(jq_code):
    """返回 'SH' / 'SZ' / None(无法识别)"""
    if jq_code.endswith(".XSHG"):
        return "SH"
    if jq_code.endswith(".XSHE"):
        return "SZ"
    return None


def price_tick(jq_code):
    """
    按证券类型返回价格最小变动单位（A 股价位规则）：
      ETF/LOF/封闭基金: 5开头(沪) 15/16/18开头(深)  -> 0.001 元
      可转债: 110/111/113/118(沪) 123/127/128(深)   -> 0.001 元
      其余(股票等)                                   -> 0.01 元
    """
    c = to_plain_code(jq_code)
    if c[:2] in ("50", "51", "52", "56", "58", "15", "16", "18"):
        return 0.001
    if c[:3] in ("110", "111", "113", "118", "123", "127", "128"):
        return 0.001
    return 0.01


def normalize_price(jq_code, price):
    """
    把委托价取整到该证券的最小价位。
    作用：同花顺对股票只收 2 位小数，遇到 3 位小数会弹
    「委托价格的小数部分应为 2 位，是否继续?」确认框卡住自动化——
    下单前统一在这里规范化，从源头避免弹窗。
    price 为 None 或 <=0 视为市价信号，返回 None（由调用方取最新价）。
    """
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    tick = price_tick(jq_code)
    return round(round(p / tick) * tick, 3)


class BrokerBase(object):
    """所有执行端适配器的公共骨架。"""

    name = "base"

    def connect(self):
        """建立与券商/客户端的连接，失败应抛异常。"""
        raise NotImplementedError

    def buy(self, jq_code, price, amount):
        """买入。返回 dict: {ok: bool, entrust_no: str, message: str}"""
        raise NotImplementedError

    def sell(self, jq_code, price, amount):
        """卖出。返回 dict 同上。"""
        raise NotImplementedError

    def query_positions(self):
        """返回持仓列表（格式尽量贴近聚宽，用于对账）。"""
        raise NotImplementedError

    def query_balance(self):
        """返回资金 dict。"""
        raise NotImplementedError

    # ---------------- 可选能力（Web 控制台任务用；未实现的适配器走默认兜底） ----------------

    def query_entrusts(self):
        """当日委托列表。默认返回空（不影响既有适配器）。"""
        return []

    def query_trades(self):
        """当日成交列表。默认返回空（不影响既有适配器）。"""
        return []

    def cancel_order(self, entrust_no):
        """撤单。默认不支持。"""
        return self._result(False, "当前券商适配器暂不支持撤单")

    def cancel_all_orders(self):
        """一键全撤（撤销当日全部可撤委托）。默认不支持。"""
        return self._result(False, "当前券商适配器暂不支持一键全撤")

    def clear_form(self):
        """清场：清空下单表单（同花顺 UI 专用）。默认不支持。

        放在基类是为了让非 ths 适配器（miniqmt/thsauto）调用时得到一句
        明确的"不支持"答复，而不是 AttributeError（2026-09-15 修）。
        """
        return self._result(False, "当前券商适配器不支持清场（清场是同花顺 UI 专用操作）")

    # ---------------- 公共工具 ----------------

    def _result(self, ok, message="", entrust_no=""):
        return {"ok": bool(ok), "message": str(message)[:300], "entrust_no": str(entrust_no)}

    def health_check(self):
        """开盘前自检：能查到资金/持仓即视为连接正常。"""
        try:
            self.query_balance()
            return True, "%s 连接正常" % self.name
        except Exception as e:
            return False, "%s 连接异常: %s" % (self.name, e)
