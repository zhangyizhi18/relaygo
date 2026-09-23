# -*- coding: utf-8 -*-
"""
方案B：thsauto（安卓模拟器方案）。

原理：Windows 上跑雷电模拟器，模拟器里装同花顺 APP 并登录，thsauto 通过 adb 驱动 APP
下单，并在本机暴露一个 HTTP 服务。本适配器只是 thsauto HTTP 接口的"翻译层"。

前置条件：
  1. 安装雷电模拟器 + 同花顺 APP + 登录交易账号（thsauto 文档：github.com/wukan1986/thsauto）
  2. pip install requests
  3. 启动 thsauto 服务：  thsauto run --host=0.0.0.0 --port=5001
     （端口建议 5001，避开中转服务的 5010）
  4. config.py 里 THSAUTO_URL / THSAUTO_ADDR 与实际一致

注意：thsauto 的 HTTP 路由与其 cli.py 一一对应；如果作者更新了接口导致这里 404，
打开 thsauto/cli.py 对照修改 _call 的路径即可（代码很短）。
"""
import requests

from broker_base import BrokerBase, normalize_price, to_plain_code
import market_price

try:
    import requests  # noqa: F401
except ImportError:
    requests = None


class ThsAutoBroker(BrokerBase):
    name = "thsauto"

    def __init__(self, base_url, addr="emulator-5554"):
        self.base = base_url.rstrip("/")
        self.addr = addr

    # ---------------- HTTP 封装 ----------------

    def _call(self, path, payload=None, timeout=60):
        """统一 POST JSON 到 thsauto，返回解析后的 dict；失败抛异常。"""
        if requests is None:
            raise RuntimeError("未安装 requests，请执行: pip install requests")
        url = "%s/%s" % (self.base, path.lstrip("/"))
        body = dict(payload or {})
        body.setdefault("addr", self.addr)
        resp = requests.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    # ---------------- 生命周期 ----------------

    def connect(self):
        """连接模拟器并做一次刷新，确认 thsauto 服务在线。"""
        r = self._call("connect", {"addr": self.addr}, timeout=30)
        self._call("refresh")
        return r

    # ---------------- 交易 ----------------

    def _trade(self, direction, jq_code, price, amount):
        code = to_plain_code(jq_code)
        price = normalize_price(jq_code, price)   # 规范价位，避免同花顺 APP 弹"小数部分应为2位"确认框
        if price is None:
            # 市价信号（price 为空/0/非法）：取最新价下单，取不到就拒绝
            price = market_price.fetch_latest(code)
            if price is None:
                return self._result(False, "市价信号且取不到最新价（%s），已拒绝下单" % code)
            price = normalize_price(jq_code, price)
        try:
            r = self._call(direction, {"code": code, "price": float(price),
                                       "amount": int(amount)})
            ok = not (isinstance(r, dict) and r.get("error"))
            return self._result(ok, str(r)[:300])
        except Exception as e:
            return self._result(False, "thsauto %s 失败: %s" % (direction, e))

    def buy(self, jq_code, price, amount):
        return self._trade("buy", jq_code, price, amount)

    def sell(self, jq_code, price, amount):
        return self._trade("sell", jq_code, price, amount)

    def cancel(self, index):
        """按当日委托列表下标撤单。"""
        try:
            return self._result(True, str(self._call("cancel", {"index": int(index)}))[:200])
        except Exception as e:
            return self._result(False, "thsauto 撤单失败: %s" % e)

    # ---------------- 查询 ----------------

    def query_balance(self):
        return self._call("get_balance")

    def query_positions(self):
        return self._call("get_positions")

    def query_orders(self):
        return self._call("get_orders")


if __name__ == "__main__":
    # 单独调试本模块：python broker_thsauto.py
    import config as cfg
    b = ThsAutoBroker(cfg.THSAUTO_URL, cfg.THSAUTO_ADDR)
    print("connect:", b.connect())
    print("资金:", b.query_balance())
    print("持仓:", b.query_positions())
