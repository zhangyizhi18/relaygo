# -*- coding: utf-8 -*-
"""
【聚宽模拟器 GUI】本地完全模拟聚宽的下单函数与账户，方便脱离聚宽环境调试整条中转链路。

启动（需要带 tkinter 的 Python，官网安装勾选 tcl/tk 即可）：
    双击同目录下的「聚宽模拟器.bat」
    或: python jq_sim_gui.py
    或: python jq_sim_gui.py --smoke    (自检模式：自动下单验证后退出)

能做什么：
  1. 模拟聚宽账户：初始资金、持仓(total/closeable/avg_cost)、订单历史、T+1 可卖约束、整手校验。
  2. 完整模拟聚宽四个下单函数的语义（资金不足/持仓不足返回 None，与聚宽一致）：
       order(security, amount)
       order_target(security, amount)
       order_value(security, value)
       order_target_value(security, value)
  3. 信号格式与 jq_signal.py 完全一致（order_id/security/side/amount/price/jq_status），
     可选择 真实上报到中转 / DRY 只打印。支持"重发选中信号"来验证中转端幂等去重（409）。
  4. 市价单立即成交(held)、限价单挂单(open)可手工撮合/撤单——与真实 jq_signal 一样，
     挂单期间上报 open，撮合后不再上报（真实聚宽也只在下单瞬间上报一次）。
  5. 「上报持仓快照」按钮等价于策略里 after_market_close 调 report_positions(context)，
     配合控制台「持仓对账」页做盘后对账联调。
  6. 行情表可手工添加/改价/一键随机波动，模拟盘中价格变化。

注意：模拟器产生的所有持仓/资金变化只存在于本窗口内，与真实账户无关。
"""

import json
import os
import random
import sys
import threading
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError
from urllib.request import ProxyHandler, build_opener

# tkinter 仅 GUI 需要引擎部分；无头环境（自测/CI）没有 tkinter 也能用 SimEngine
try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:          # 无头环境
    tk = ttk = messagebox = None

# 与 jq_signal.py 保持一致的默认值
DEF_URL = "http://127.0.0.1:5010"
DEF_KEY = "jq-signal-key-2026-change-me"
CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jq_sim_gui.json")

# 免代理直连（本机调试常被系统代理劫持，这里显式绕过）
_OPENER = build_opener(ProxyHandler({}))


# ==================== 模拟引擎（纯逻辑，不依赖 tkinter） ====================

class SimEngine(object):
    """模拟聚宽账户与下单语义。"""

    def __init__(self, cash=1000000.0, t1=True, lot_check=True):
        self.cash = float(cash)          # 初始资金
        self.init_cash = float(cash)
        self.t1 = bool(t1)               # True=当日买入不可卖（A 股 T+1）
        self.lot_check = bool(lot_check) # True=买入必须整手(100股)
        self._oid = 1000000000           # 聚宽 order_id 是自增整数
        self.securities = {}             # code -> {"name":, "price":}
        self.positions = {}              # code -> {"name","total_amount","today_buy","avg_cost"}
        self.orders = []                 # 订单历史，元素见 _new_order
        self._frozen_sell = {}           # code -> 挂卖单冻结的股数（防重复挂单超卖）
        self._frozen_cash = 0.0          # 挂买单冻结的资金（防重复挂单超买）

    # ---------- 行情 ----------
    def add_security(self, code, name="", price=10.0):
        code = str(code).strip()
        try:
            price = float(price)
        except (TypeError, ValueError):
            return False
        if not code or price <= 0:
            return False                 # 价格必须为正数（负价/0价会污染账户计算）
        self.securities[code] = {"name": str(name)[:20], "price": price}
        return True

    def set_price(self, code, price):
        try:
            price = float(price)
        except (TypeError, ValueError):
            return False
        if code in self.securities and price > 0:
            self.securities[code]["price"] = price
            return True
        return False

    def random_walk(self, pct=0.01):
        """所有标的随机波动 ±pct，模拟盘中行情。"""
        for s in self.securities.values():
            s["price"] = round(max(0.01, s["price"] * (1 + random.uniform(-pct, pct))), 3)

    # ---------- 内部 ----------
    def _new_order(self, security, is_buy, amount, price, status):
        self._oid += 1
        o = {"order_id": self._oid, "security": security, "side": "buy" if is_buy else "sell",
             "amount": int(amount), "price": float(price), "status": status,
             "filled_price": None, "ts": ""}
        import time
        o["ts"] = time.strftime("%H:%M:%S")
        self.orders.append(o)
        return o

    def _pos(self, code, name=""):
        if code not in self.positions:
            self.positions[code] = {"name": name or (self.securities.get(code, {}) or {}).get("name", ""),
                                    "total_amount": 0, "today_buy": 0, "avg_cost": 0.0}
        return self.positions[code]

    def closeable(self, code):
        """可卖数量 = 总持仓 - 当日买入（T+1 时） - 挂卖单冻结；T+1 关闭则只减冻结。"""
        p = self.positions.get(code)
        if not p:
            return 0
        return p["total_amount"] - (p["today_buy"] if self.t1 else 0) \
               - self._frozen_sell.get(code, 0)

    def available_cash(self):
        """可用资金 = 现金 - 挂买单冻结。"""
        return self.cash - self._frozen_cash

    def _fill(self, o):
        """按订单成交，更新资金与持仓。"""
        p = self.securities[o["security"]]["price"]
        o["status"] = "held"
        o["filled_price"] = p
        pos = self._pos(o["security"])
        if o["side"] == "buy":
            cost = o["amount"] * p
            self.cash -= cost
            total = pos["total_amount"] + o["amount"]
            pos["avg_cost"] = (pos["avg_cost"] * pos["total_amount"] + cost) / total if total else 0.0
            pos["total_amount"] = total
            pos["today_buy"] = pos.get("today_buy", 0) + o["amount"]   # 记当日买入，供 T+1 判断
        else:
            self.cash += o["amount"] * p
            pos["total_amount"] -= o["amount"]
            pos["today_buy"] = min(pos.get("today_buy", 0), pos["total_amount"])
            if pos["total_amount"] <= 0:
                self.positions.pop(o["security"], None)

    # ---------- 四个下单函数（语义对齐聚宽：失败返回 None） ----------
    def order(self, security, amount, limit_price=None):
        """amount>0 买入 amount 股；amount<0 卖出 |amount| 股。"""
        security = str(security).strip()
        if security not in self.securities or amount == 0:
            return None
        amount = int(amount)
        is_buy = amount > 0
        n = abs(amount)
        p = self.securities[security]["price"]
        if limit_price is not None:
            p = float(limit_price)
            if p <= 0:
                return None                       # 限价必须为正（0/负价是无效委托）
        if is_buy:
            if self.lot_check and n % 100 != 0:
                return None                       # 买入必须整手
            if n * p > self.available_cash():
                return None                       # 资金不足（含挂买单冻结）
        else:
            if n > self.closeable(security):
                return None                       # 可卖不足（含 T+1 与挂卖单冻结）
        status = "held" if limit_price is None else "open"   # 市价立即成交，限价挂单
        o = self._new_order(security, is_buy, n, p, status)
        if status == "held":
            self._fill(o)
        else:
            # 挂单冻结资源，防止重复挂单超买/超卖（与真实券商一致）
            if is_buy:
                self._frozen_cash += n * p
            else:
                self._frozen_sell[security] = self._frozen_sell.get(security, 0) + n
        return o

    def order_target(self, security, amount, limit_price=None):
        """把持仓调整到 amount 股，差额下单；已持仓且目标相同返回 None（聚宽语义）。"""
        security = str(security).strip()
        if security not in self.securities:
            return None
        cur = self._pos(security).get("total_amount", 0)
        diff = int(amount) - cur
        if diff == 0:
            return None
        return self.order(security, diff, limit_price=limit_price)

    def order_value(self, security, value, limit_price=None):
        """按金额下单：value>0 买入价值 value 的股数（整手）；value<0 卖出价值 |value| 的股数。"""
        security = str(security).strip()
        if security not in self.securities or value == 0:
            return None
        p = float(limit_price) if limit_price is not None else self.securities[security]["price"]
        n = int(abs(value) / p)
        if value > 0:
            n = n - n % 100                        # 买入取整手
        return self.order(security, n if value > 0 else -n, limit_price=limit_price)

    def order_target_value(self, security, value, limit_price=None):
        """把持仓市值调整到 value；差额按金额下单；目标等于当前市值返回 None。"""
        security = str(security).strip()
        if security not in self.securities:
            return None
        p = float(limit_price) if limit_price is not None else self.securities[security]["price"]
        cur = self._pos(security).get("total_amount", 0)
        target_n = int(value / p)
        diff = target_n - cur
        if diff == 0:
            return None
        return self.order(security, diff, limit_price=limit_price)

    # ---------- 挂单操作 ----------
    def pending_orders(self):
        return [o for o in self.orders if o["status"] == "open"]

    def match_order(self, order_id):
        """撮合挂单（按当前行情价成交），释放对应冻结资源。"""
        for o in self.pending_orders():
            if o["order_id"] == int(order_id):
                p = self.securities[o["security"]]["price"]
                # 本单自己的冻结不计入约束（冻结就是为这一单留的）
                if o["side"] == "buy" and \
                        o["amount"] * p > self.available_cash() + o["amount"] * o["price"]:
                    return False, "资金不足，撮合失败"
                if o["side"] == "sell" and \
                        o["amount"] > self.closeable(o["security"]) + o["amount"]:
                    return False, "可卖不足，撮合失败"
                # 先释放冻结（_fill 按成交价记账，冻结按委托价计）
                if o["side"] == "buy":
                    self._frozen_cash -= o["amount"] * o["price"]
                    if self._frozen_cash < 1e-9:
                        self._frozen_cash = 0.0
                else:
                    self._frozen_sell[o["security"]] = \
                        self._frozen_sell.get(o["security"], 0) - o["amount"]
                    if self._frozen_sell[o["security"]] <= 0:
                        self._frozen_sell.pop(o["security"], None)
                self._fill(o)
                return True, "已成交"
        return False, "未找到该挂单"

    def cancel_order(self, order_id):
        """撤单，释放对应冻结资源。"""
        for o in self.pending_orders():
            if o["order_id"] == int(order_id):
                o["status"] = "canceled"
                if o["side"] == "buy":
                    self._frozen_cash -= o["amount"] * o["price"]
                    if self._frozen_cash < 1e-9:
                        self._frozen_cash = 0.0
                else:
                    self._frozen_sell[o["security"]] = \
                        self._frozen_sell.get(o["security"], 0) - o["amount"]
                    if self._frozen_sell[o["security"]] <= 0:
                        self._frozen_sell.pop(o["security"], None)
                return True
        return False

    # ---------- 快照 ----------
    def positions_snapshot(self):
        """与 jq_signal.report_positions 相同结构的持仓列表。"""
        out = []
        for code, p in sorted(self.positions.items()):
            out.append({"security": code, "name": p.get("name", ""),
                        "total_amount": int(p["total_amount"]),
                        "closeable_amount": int(self.closeable(code))})
        return out

    def reset(self, cash):
        """重置账户资金/持仓/订单/冻结，但保留行情表与开关设置。"""
        keep_sec = dict(self.securities)
        keep_t1, keep_lot = self.t1, self.lot_check
        self.__init__(cash=cash, t1=keep_t1, lot_check=keep_lot)
        self.securities = keep_sec

    # ---------- 上报（格式与 jq_signal.py 完全一致） ----------
    @staticmethod
    def build_payload(o):
        return {"order_id": str(o["order_id"]), "security": str(o["security"]),
                "side": o["side"], "amount": int(o["amount"]),
                "price": float(o["price"]) if o["price"] is not None else None,
                "jq_status": o["status"]}

    @staticmethod
    def post(path, payload, relay_url, api_key, timeout=3):
        """POST JSON 到中转服务，返回 (ok, 响应文本)。任何异常都不抛出。"""
        url = relay_url.rstrip("/") + path
        try:
            req = urlrequest.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-API-Key": api_key},
                method="POST")
            r = _OPENER.open(req, timeout=timeout)
            return True, "%s %s" % (r.status, r.read().decode("utf-8", "replace")[:200])
        except HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                body = ""
            return False, "HTTP %s %s" % (e.code, body)
        except (URLError, OSError) as e:
            return False, "连接失败: %s" % e


# ==================== GUI ====================

BG = "#f4f5f7"


class App(object):
    def __init__(self, root):
        self.root = root
        root.title("RelayGo · 聚宽模拟器（调试用）")
        root.geometry("1150x760")
        root.configure(bg=BG)

        self.eng = SimEngine()
        # 默认演示标的
        self.eng.add_security("510300.XSHG", "沪深300ETF", 3.92)
        self.eng.add_security("600519.XSHG", "贵州茅台", 1520.0)
        self.eng.add_security("000001.XSHE", "平安银行", 11.35)

        self.cfg = self._load_cfg()

        self._build_top()
        self._build_body()
        self._build_bottom()
        self.refresh_all()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 配置持久化 ----------
    def _load_cfg(self):
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_cfg(self):
        try:
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump({"relay_url": self.var_url.get().strip(),
                           "api_key": self.var_key.get().strip()}, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    # ---------- 顶部：连接设置 ----------
    def _build_top(self):
        f = tk.Frame(self.root, bg=BG)
        f.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(f, text="中转地址", bg=BG).pack(side="left")
        self.var_url = tk.StringVar(value=self.cfg.get("relay_url", DEF_URL))
        tk.Entry(f, textvariable=self.var_url, width=30).pack(side="left", padx=(4, 12))
        tk.Label(f, text="API Key", bg=BG).pack(side="left")
        self.var_key = tk.StringVar(value=self.cfg.get("api_key", DEF_KEY))
        tk.Entry(f, textvariable=self.var_key, width=32, show="").pack(side="left", padx=4)
        self.var_dry = tk.BooleanVar(value=False)
        tk.Checkbutton(f, text="DRY（只打印不上报）", variable=self.var_dry, bg=BG).pack(side="left", padx=8)
        tk.Button(f, text="测试连接", command=self.test_conn).pack(side="left", padx=4)

    # ---------- 主体：左操作 / 右状态 ----------
    def _build_body(self):
        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=4)

        # ===== 左栏 =====
        left = tk.LabelFrame(body, text=" 行情 ", bg=BG)
        left.pack(side="left", fill="y", padx=(0, 8))

        cols = ("code", "name", "price")
        self.tv_sec = ttk.Treeview(left, columns=cols, show="headings", height=7)
        for c, w, t in (("code", 110, "代码"), ("name", 90, "名称"), ("price", 70, "现价")):
            self.tv_sec.heading(c, text=t)
            self.tv_sec.column(c, width=w, anchor="center")
        self.tv_sec.pack(fill="x", padx=4, pady=4)
        self.tv_sec.bind("<<TreeviewSelect>>", self._pick_security)

        fr = tk.Frame(left, bg=BG)
        fr.pack(fill="x", padx=4)
        self.var_ncode = tk.StringVar()
        self.var_nname = tk.StringVar()
        self.var_nprice = tk.StringVar()
        tk.Entry(fr, textvariable=self.var_ncode, width=13).pack(side="left")
        tk.Entry(fr, textvariable=self.var_nname, width=8).pack(side="left", padx=2)
        tk.Entry(fr, textvariable=self.var_nprice, width=6).pack(side="left")
        tk.Button(fr, text="+添加", command=self.add_security).pack(side="left", padx=2)

        fr2 = tk.Frame(left, bg=BG)
        fr2.pack(fill="x", padx=4, pady=3)
        self.var_newprice = tk.StringVar()
        tk.Label(fr2, text="改价", bg=BG).pack(side="left")
        tk.Entry(fr2, textvariable=self.var_newprice, width=8).pack(side="left", padx=3)
        tk.Button(fr2, text="改所选", command=self.chg_price).pack(side="left")
        tk.Button(fr2, text="随机波动±1%", command=self.rand_walk).pack(side="left", padx=6)

        # ===== 下单区 =====
        od = tk.LabelFrame(body, text=" 下单（模拟聚宽四个函数） ", bg=BG)
        od.pack(side="left", fill="both", expand=True, padx=(0, 8))

        g1 = tk.Frame(od, bg=BG)
        g1.pack(fill="x", padx=6, pady=(6, 2))
        tk.Label(g1, text="函数", bg=BG).pack(side="left")
        self.var_func = tk.StringVar(value="order")
        cb = ttk.Combobox(g1, textvariable=self.var_func, width=19, state="readonly",
                          values=("order", "order_target", "order_value", "order_target_value"))
        cb.pack(side="left", padx=(4, 12))
        tk.Label(g1, text="代码", bg=BG).pack(side="left")
        self.var_code = tk.StringVar()
        tk.Entry(g1, textvariable=self.var_code, width=13).pack(side="left", padx=4)
        tk.Label(g1, text="数量/金额", bg=BG).pack(side="left")
        self.var_amt = tk.StringVar()
        tk.Entry(g1, textvariable=self.var_amt, width=12).pack(side="left", padx=4)

        g2 = tk.Frame(od, bg=BG)
        g2.pack(fill="x", padx=6, pady=2)
        tk.Label(g2, text="委托价(空=市价)", bg=BG).pack(side="left")
        self.var_price = tk.StringVar()
        tk.Entry(g2, textvariable=self.var_price, width=9).pack(side="left", padx=4)
        self.var_t1 = tk.BooleanVar(value=True)
        tk.Checkbutton(g2, text="T+1(当日买不可卖)", variable=self.var_t1, bg=BG,
                       command=lambda: setattr(self.eng, "t1", self.var_t1.get())).pack(side="left", padx=6)
        self.var_lot = tk.BooleanVar(value=True)
        tk.Checkbutton(g2, text="买入整手校验", variable=self.var_lot, bg=BG,
                       command=lambda: setattr(self.eng, "lot_check", self.var_lot.get())).pack(side="left")

        g3 = tk.Frame(od, bg=BG)
        g3.pack(fill="x", padx=6, pady=6)
        tk.Button(g3, text="下 单", width=10, bg="#2563eb", fg="white",
                  command=self.do_order).pack(side="left")
        tk.Label(g3, text=" 提示：order 传数量（负数=卖）；order_value 传金额（负数=卖出该金额）；"
                          "target 系列传目标值，无差额返回 None", bg=BG, fg="#888").pack(side="left", padx=8)

        # ===== 右栏 =====
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)

        # -- 持仓 --
        f1 = tk.Frame(nb, bg="white")
        nb.add(f1, text=" 账户/持仓 ")
        top1 = tk.Frame(f1, bg="white")
        top1.pack(fill="x", padx=4, pady=4)
        self.lbl_cash = tk.Label(top1, text="", bg="white", fg="#111",
                                 font=("Microsoft YaHei", 10, "bold"))
        self.lbl_cash.pack(side="left")
        cols = ("code", "name", "total", "closeable", "avg")
        self.tv_pos = ttk.Treeview(f1, columns=cols, show="headings", height=6)
        for c, w, t in (("code", 110, "代码"), ("name", 90, "名称"), ("total", 70, "总持仓"),
                        ("closeable", 80, "可卖"), ("avg", 80, "成本价")):
            self.tv_pos.heading(c, text=t)
            self.tv_pos.column(c, width=w, anchor="center")
        self.tv_pos.pack(fill="both", expand=True, padx=4, pady=4)

        # -- 挂单 --
        f2 = tk.Frame(nb, bg="white")
        nb.add(f2, text=" 挂单(限价open) ")
        cols = ("oid", "code", "side", "amount", "price")
        self.tv_pend = ttk.Treeview(f2, columns=cols, show="headings", height=6)
        for c, w, t in (("oid", 110, "委托号"), ("code", 110, "代码"), ("side", 60, "方向"),
                        ("amount", 70, "数量"), ("price", 80, "委托价")):
            self.tv_pend.heading(c, text=t)
            self.tv_pend.column(c, width=w, anchor="center")
        self.tv_pend.pack(fill="both", expand=True, padx=4, pady=(4, 2))
        fb2 = tk.Frame(f2, bg="white")
        fb2.pack(fill="x", padx=4, pady=4)
        tk.Button(fb2, text="撮合所选", command=lambda: self._pend_act(True)).pack(side="left")
        tk.Button(fb2, text="撤单所选", command=lambda: self._pend_act(False)).pack(side="left", padx=6)
        tk.Label(fb2, text="撤单/撮合后都不会再上报（与真实 jq_signal 一致：只在下单瞬间上报一次）",
                 bg="white", fg="#888").pack(side="left", padx=8)

        # -- 订单历史 --
        f3 = tk.Frame(nb, bg="white")
        nb.add(f3, text=" 订单历史 ")
        cols = ("oid", "code", "side", "amount", "price", "status", "ts")
        self.tv_ord = ttk.Treeview(f3, columns=cols, show="headings", height=10)
        for c, w, t in (("oid", 105, "委托号"), ("code", 105, "代码"), ("side", 55, "方向"),
                        ("amount", 65, "数量"), ("price", 75, "价格"), ("status", 70, "状态"), ("ts", 70, "时间")):
            self.tv_ord.heading(c, text=t)
            self.tv_ord.column(c, width=w, anchor="center")
        self.tv_ord.pack(fill="both", expand=True, padx=4, pady=4)

        # -- 信号日志 --
        f4 = tk.Frame(nb, bg="white")
        nb.add(f4, text=" 信号上报日志 ")
        cols = ("ts", "oid", "code", "status", "result")
        self.tv_sig = ttk.Treeview(f4, columns=cols, show="headings", height=10)
        for c, w, t in (("ts", 70, "时间"), ("oid", 105, "委托号"), ("code", 105, "代码"),
                        ("status", 65, "上报状态"), ("result", 330, "中转响应 / 说明")):
            self.tv_sig.heading(c, text=t)
            self.tv_sig.column(c, width=w, anchor="center" if c != "result" else "w")
        self.tv_sig.pack(fill="both", expand=True, padx=4, pady=(4, 2))
        self.sig_payloads = {}     # oid -> payload（供重发）
        fb4 = tk.Frame(f4, bg="white")
        fb4.pack(fill="x", padx=4, pady=4)
        tk.Button(fb4, text="重发选中信号（测幂等去重）", command=self.resend).pack(side="left")
        tk.Button(fb4, text="上报持仓快照(等价 after_market_close)", command=self.report_positions).pack(side="left", padx=6)

        # -- 底部日志 --
        self.txt = tk.Text(self.root, height=5, bg="#1e1e1e", fg="#d4d4d4",
                           insertbackground="white", font=("Consolas", 9))
        self.txt.pack(fill="x", padx=8, pady=(0, 4))

    def _build_bottom(self):
        f = tk.Frame(self.root, bg=BG)
        f.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(f, text="重置初始资金", bg=BG).pack(side="left")
        self.var_cash = tk.StringVar(value="1000000")
        tk.Entry(f, textvariable=self.var_cash, width=11).pack(side="left", padx=4)
        tk.Button(f, text="重置账户", command=self.reset).pack(side="left")
        tk.Label(f, text="   模拟数据仅存于本窗口，与真实账户无关", bg=BG, fg="#888").pack(side="left", padx=10)

    # ---------- 行为 ----------
    def log(self, msg):
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")

    def _relay(self):
        return self.var_url.get().strip(), self.var_key.get().strip()

    def _report(self, o):
        """把订单按 jq_signal._report_order 同样规则上报（open/held 才报，失败不影响本地）。"""
        if o["status"] not in ("open", "held"):
            return
        payload = SimEngine.build_payload(o)
        ts = o["ts"]
        if self.var_dry.get():
            self._sig_row(ts, payload, "DRY", json.dumps(payload, ensure_ascii=False))
            return
        url, key = self._relay()
        ok, resp = SimEngine.post("/api/signal", payload, url, key)
        self._sig_row(ts, payload, "已上报" if ok else "失败", resp)

    def _sig_row(self, ts, payload, status, result):
        oid = payload["order_id"]
        self.sig_payloads[oid] = payload
        self.tv_sig.insert("", 0, values=(ts, oid, payload["security"], status, result))

    def _pick_security(self, _e=None):
        sel = self.tv_sec.selection()
        if sel:
            self.var_code.set(self.tv_sec.item(sel[0])["values"][0])

    def add_security(self):
        code = self.var_ncode.get().strip()
        try:
            price = float(self.var_nprice.get() or 10)
        except ValueError:
            messagebox.showwarning("提示", "现价必须是数字")
            return
        if not code:
            return
        if not self.eng.add_security(code, self.var_nname.get().strip(), price):
            messagebox.showwarning("提示", "价格必须是正数（如 3.92、1520.00）")
            return
        self.refresh_all()

    def chg_price(self):
        sel = self.tv_sec.selection()
        if not sel:
            messagebox.showinfo("提示", "先在行情表选中一行")
            return
        try:
            p = float(self.var_newprice.get())
        except ValueError:
            messagebox.showwarning("提示", "价格必须是数字")
            return
        code = self.tv_sec.item(sel[0])["values"][0]
        if not self.eng.set_price(code, p):
            messagebox.showwarning("提示", "价格必须是正数")
            return
        self.refresh_all()

    def rand_walk(self):
        self.eng.random_walk(0.01)
        self.refresh_all()

    def do_order(self):
        code = self.var_code.get().strip()
        arg = self.var_amt.get().strip()
        price = self.var_price.get().strip()
        limit_price = None
        if price:
            try:
                limit_price = float(price)
            except ValueError:
                messagebox.showwarning("提示", "委托价必须是数字或留空")
                return
        try:
            x = float(arg)
        except ValueError:
            messagebox.showwarning("提示", "数量/金额必须是数字")
            return
        fn = self.var_func.get()
        kw = {"limit_price": limit_price}
        if fn == "order":
            r = self.eng.order(code, int(x), **kw)
        elif fn == "order_target":
            r = self.eng.order_target(code, int(x), **kw)
        elif fn == "order_value":
            r = self.eng.order_value(code, x, **kw)
        else:
            r = self.eng.order_target_value(code, x, **kw)
        if r is None:
            self.log("[%s] %s %s %s -> None（资金不足/可卖不足/整手不符/无差额，与聚宽一致）" % (fn, code, arg, "限价" + price if price else "市价"))
        else:
            self.log("[%s] 下单成功 委托号=%s %s %s %d股 @%s -> %s"
                     % (fn, r["order_id"], code, r["side"], r["amount"], r["price"], r["status"]))
            self._report(r)
        self.refresh_all()

    def _pend_act(self, do_match):
        sel = self.tv_pend.selection()
        if not sel:
            messagebox.showinfo("提示", "先选中一条挂单")
            return
        oid = self.tv_pend.item(sel[0])["values"][0]
        if do_match:
            ok, msg = self.eng.match_order(oid)
        else:
            ok = self.eng.cancel_order(oid)
            msg = "已撤单" if ok else "撤单失败（不存在或已不是挂单状态）"
        self.log("挂单 %s %s: %s" % (oid, "撮合" if do_match else "撤单", msg))
        if not ok:
            messagebox.showwarning("提示", msg)
        self.refresh_all()

    def resend(self):
        """重发选中信号 -> 验证中转端 order_id 幂等去重（应被拒绝）。"""
        sel = self.tv_sig.selection()
        if not sel:
            messagebox.showinfo("提示", "先在信号日志选中一条记录")
            return
        oid = str(self.tv_sig.item(sel[0])["values"][1])
        payload = self.sig_payloads.get(oid)
        if not payload:
            return
        if self.var_dry.get():
            self._sig_row("重发", payload, "DRY", json.dumps(payload, ensure_ascii=False))
            return
        url, key = self._relay()
        ok, resp = SimEngine.post("/api/signal", payload, url, key)
        self._sig_row("重发", payload, "已上报" if ok else "被拒", resp)
        self.log("重发信号 %s -> %s" % (oid, resp))

    def report_positions(self):
        snap = self.eng.positions_snapshot()
        payload = {"positions": snap}
        if self.var_dry.get():
            self._sig_row("快照", {"order_id": "-", "security": "%d只" % len(snap)}, "DRY",
                          json.dumps(payload, ensure_ascii=False))
            return
        url, key = self._relay()
        ok, resp = SimEngine.post("/api/jq_positions", payload, url, key)
        self._sig_row("快照", {"order_id": "-", "security": "%d只" % len(snap)},
                      "已上报" if ok else "失败", resp)
        self.log("持仓快照 %d 只 -> %s" % (len(snap), resp))

    def test_conn(self):
        url, key = self._relay()
        try:
            r = _OPENER.open(url.rstrip("/") + "/api/status", timeout=3)
            self.log("连接成功 %s -> %s" % (url, r.read().decode("utf-8", "replace")[:120]))
        except Exception as e:
            self.log("连接失败 %s -> %s" % (url, e))

    def reset(self):
        try:
            cash = float(self.var_cash.get())
        except ValueError:
            messagebox.showwarning("提示", "初始资金必须是数字")
            return
        self.eng.reset(cash)
        self.refresh_all()
        self.log("账户已重置：初始资金 %.2f" % cash)

    def _on_close(self):
        self._save_cfg()
        self.root.destroy()

    # ---------- 刷新 ----------
    def refresh_all(self):
        # 行情
        sel = self.tv_sec.selection()
        self.tv_sec.delete(*self.tv_sec.get_children())
        for code, s in sorted(self.eng.securities.items()):
            self.tv_sec.insert("", "end", values=(code, s["name"], s["price"]))
        for i in sel:
            if self.tv_sec.exists(i):
                self.tv_sec.selection_add(i)
        # 账户
        self.lbl_cash.config(text="可用资金: {:,.2f}".format(self.eng.cash))
        self.tv_pos.delete(*self.tv_pos.get_children())
        for code, p in sorted(self.eng.positions.items()):
            self.tv_pos.insert("", "end", values=(code, p["name"], p["total_amount"],
                                                  self.eng.closeable(code), round(p["avg_cost"], 3)))
        # 挂单
        self.tv_pend.delete(*self.tv_pend.get_children())
        for o in self.eng.pending_orders():
            self.tv_pend.insert("", "end", values=(o["order_id"], o["security"], o["side"],
                                                   o["amount"], o["price"]),
                                tags=("buy" if o["side"] == "buy" else "sell",))
        # 订单历史
        self.tv_ord.delete(*self.tv_ord.get_children())
        for o in reversed(self.eng.orders[-200:]):
            self.tv_ord.insert("", "end", values=(o["order_id"], o["security"], o["side"], o["amount"],
                                                  o["price"], o["status"], o["ts"]),
                               tags=("buy" if o["side"] == "buy" else "sell",))
        self.tv_ord.tag_configure("buy", foreground="#c0392b")   # 买红
        self.tv_ord.tag_configure("sell", foreground="#1e8449")  # 卖绿
        self.tv_pend.tag_configure("buy", foreground="#c0392b")
        self.tv_pend.tag_configure("sell", foreground="#1e8449")


def smoke():
    """自检：不开完整界面也能验证引擎与上报格式。"""
    e = SimEngine()
    e.add_security("600519.XSHG", "贵州茅台", 100.0)
    o1 = e.order("600519.XSHG", 200)                       # 市价买 200 股
    assert o1 and o1["status"] == "held" and o1["side"] == "buy"
    assert abs(e.cash - (1000000 - 200 * 100)) < 1e-6
    assert e.closeable("600519.XSHG") == 0                 # T+1 当日买不可卖
    assert e.order("600519.XSHG", -100) is None            # T+1 下卖出被拒 -> None
    e.t1 = False
    o2 = e.order("600519.XSHG", -100)
    assert o2 and o2["side"] == "sell" and e.positions["600519.XSHG"]["total_amount"] == 100
    assert e.order("600519.XSHG", 99999999) is None        # 资金不足 -> None
    assert e.order("600519.XSHG", 150) is None             # 非整手 -> None
    o3 = e.order_target("600519.XSHG", 300)                # 目标300 -> 买200
    assert o3 and o3["side"] == "buy" and o3["amount"] == 200
    assert e.order_target("600519.XSHG", 300) is None      # 已达标 -> None
    o4 = e.order_value("600519.XSHG", 50000)               # 买 500 股
    assert o4 and o4["amount"] == 500
    o5 = e.order_target_value("600519.XSHG", 0)            # 清仓
    assert o5 and "600519.XSHG" not in e.positions
    o6 = e.order("600519.XSHG", 100, limit_price=99.0)     # 限价挂单
    assert o6["status"] == "open" and len(e.pending_orders()) == 1
    p = SimEngine.build_payload(o6)                        # 下单瞬间的上报内容
    assert p == {"order_id": str(o6["order_id"]), "security": "600519.XSHG", "side": "buy",
                 "amount": 100, "price": 99.0, "jq_status": "open"}
    e.set_price("600519.XSHG", 98.0)
    ok, msg = e.match_order(o6["order_id"])                # 撮合
    assert ok and o6["status"] == "held" and o6["filled_price"] == 98.0
    assert o6["price"] == 99.0                             # 委托价不被撮合覆盖
    assert e.cancel_order(o6["order_id"]) is False         # 已成交不能再撤
    snap = e.positions_snapshot()
    assert snap and snap[0]["security"] == "600519.XSHG"
    print("SMOKE_OK engine 11/11")
    return 0


def smoke_gui():
    """GUI 冒烟：起界面 1.5 秒后自动退出。"""
    root = tk.Tk()
    app = App(root)
    root.after(1500, root.destroy)
    root.mainloop()
    print("SMOKE_OK gui")
    return 0


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        sys.exit(smoke())
    if "--smoke-gui" in sys.argv or tk is None:
        if tk is None:
            print("[错误] 当前 Python 没有 tkinter，GUI 无法启动；"
                  "引擎自检可用 --smoke。")
            sys.exit(1)
        sys.exit(smoke_gui())
    root = tk.Tk()
    App(root)
    root.mainloop()
