# -*- coding: utf-8 -*-
"""
执行端连续失败熔断（failure_guard）。

为什么需要：自动化交易最怕两类事故——漏单（信号丢了没人管）和重复下单。
除了信号层面的回收，执行端自身也应有一道"失败计数 → 分级"的闸门：
连续失败会逐级升级 L0→L1→L2→L3，对应 退避 / 暂停下单 / 熔断(需人工恢复)。
任何单次异常都不会让进程退出（主循环已兜底），这里只决定"还敢不敢继续自动下单"。

分级（按分类独立计数，取所有分类中的最大值）：
  L0  正常
  L1  窗口内失败 ≥ L1 阈值  → 拉大轮询间隔（退避），给客户端/网络喘息
  L2  窗口内失败 ≥ L2 阈值  → 暂停自动下单（仍处理 Web 控制台任务与心跳）
  L3  窗口内失败 ≥ L3 阈值  → 熔断：完全停止自动下单，必须管理员 guard_reset 或重启进程

设计要点（与项目"非侵入式"约定一致）：
  * 成功一次即清零该分类的失败计数（L0-L2 自动降级）；
  * L3 是"熔断锁"，进入后不会因后续成功自动恢复（防"假死→恢复→又假死"抖动），
    只有 guard_reset() 或重启进程能解除；
  * 计数窗口（默认 120s）之外的旧失败会自动归零，避免长时间不活动后误判。
"""
import time

try:
    import config
except Exception:
    config = None


def _val(name, default):
    """从 config 读整型配置，缺失用默认值（config 尚未定义时也不崩）。"""
    try:
        return int(getattr(config, name, default))
    except Exception:
        return default


class FailureGuard(object):
    def __init__(self):
        self.window = _val("FAILURE_GUARD_WINDOW", 120)   # 计数窗口(秒)
        self.l1 = _val("FAILURE_GUARD_L1", 3)             # ≥N 次/窗 → L1
        self.l2 = _val("FAILURE_GUARD_L2", 6)             # ≥N 次/窗 → L2
        self.l3 = _val("FAILURE_GUARD_L3", 12)            # ≥N 次/窗 → L3
        self._fails = {}        # category -> [timestamp, ...]
        self._halted = False    # L3 熔断锁（只有 reset 能解除）

    # ---------------- 记录 ----------------

    def record(self, category, exc=None):
        """记一次失败。返回当前该分类的等级（0-3）。"""
        now = time.time()
        lst = self._fails.setdefault(category, [])
        lst.append(now)
        self._prune(category, now)
        lvl = self._cat_level(category)
        if lvl >= 3:
            self._halted = True
        return lvl

    def record_success(self, category):
        """记一次成功：清零该分类失败计数（L0-L2 自动降级）。L3 熔断锁不在此清除。"""
        self._fails.pop(category, None)

    def reset(self):
        """手动解除熔断（管理员 guard_reset 或重启进程时调用）。"""
        self._fails.clear()
        self._halted = False

    # ---------------- 查询 ----------------

    def _prune(self, category, now):
        """丢弃窗口外的旧失败时间戳。"""
        cutoff = now - self.window
        self._fails[category] = [t for t in self._fails.get(category, []) if t >= cutoff]

    def _cat_level(self, category):
        n = len(self._fails.get(category, []))
        if n >= self.l3:
            return 3
        if n >= self.l2:
            return 2
        if n >= self.l1:
            return 1
        return 0

    def level(self):
        """当前总等级 0-3（取所有分类最大值；L3 熔断锁优先）。"""
        if self._halted:
            return 3
        lv = 0
        for cat in self._fails:
            lv = max(lv, self._cat_level(cat))
        return lv

    def level_name(self):
        return "L%d" % self.level()

    def halted(self):
        """是否处于 L3 熔断（主循环只保留任务通道 + 心跳）。"""
        return self._halted

    def can_trade(self):
        """是否允许自动下单（L2/L3 暂停）。"""
        return not self._halted and self.level() < 2

    def backoff(self):
        """当前退避秒数（L1 及以上拉大轮询间隔）。"""
        if self.level() >= 1:
            return max(1, _val("POLL_INTERVAL", 3) * 3)
        return 0
