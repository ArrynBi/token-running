"""费用计算：模型价格表 + DeepSeek 波峰波谷定价。

- 价格单位：USD / 1M tokens（输入 / 输出 / 缓存命中）
- 波峰波谷：高峰时段（北京时间 9:00-12:00、14:00-18:00）按表价；其余空闲时段半价
- 默认价格来自 cc-switch 数据库隐含单价反推（本机真实账单），可在 UI 菜单修改
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

# 默认价格表：模型名 -> (input, output, cache_read) USD/1M tokens
# 数值来自 cc-switch DB 反推（见调研），用户可在菜单覆盖
DEFAULT_PRICES: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28, 0.0028),
    "deepseek-v4-pro": (1.68, 3.36, 0.14),
    "gpt-5.6-sol": (0.1071, 30.0, 0.5),
    "gpt-5.6-luna": (0.0055, 1.2, 0.02),
    "gpt-5.6-terra": (0.0457, 12.0, 0.2),
}

# 高峰时段（北京时间 24h 制）：[(start_hour, end_hour_exclusive)]
PEAK_SLOTS = [(9, 12), (14, 18)]


def _bj_hour(ts: float) -> int:
    """epoch 秒 -> 北京时间小时（UTC+8，无夏令时）。"""
    return int((ts + 8 * 3600) // 3600 % 24)


def is_peak(ts: float) -> bool:
    """是否为高峰时段（北京时间）。"""
    h = _bj_hour(ts)
    return any(start <= h < end for start, end in PEAK_SLOTS)


def price_multiplier(ts: float) -> float:
    """按时段返回价格倍率：高峰 1.0，空闲 0.5。"""
    return 1.0 if is_peak(ts) else 0.5


@dataclass
class PriceTable:
    """模型价格表；支持按模型名取价，未知模型回退到传入的默认价。"""

    prices: dict[str, tuple[float, float, float]] = field(
        default_factory=lambda: dict(DEFAULT_PRICES)
    )
    fallback: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def set(self, model: str, input_p: float, output_p: float, cache_read_p: float) -> None:
        self.prices[model] = (input_p, output_p, cache_read_p)

    def get(self, model: str) -> tuple[float, float, float]:
        return self.prices.get(model, self.fallback)

    def cost(self, model: str, input_t: int, output_t: int, cache_read_t: int, ts: float) -> float:
        """计算一条记录的费用（USD）。"""
        i, o, cr = self.get(model)
        mult = price_multiplier(ts)
        return (input_t / 1e6 * i + output_t / 1e6 * o + cache_read_t / 1e6 * cr) * mult

    def save_defaults(self) -> None:
        """将当前价格表持久化（便于 UI 修改后保留）。"""
        import json
        from pathlib import Path
        try:
            path = Path.home() / ".token-running" / "prices.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {m: {"input": p[0], "output": p[1], "cache_read": p[2]}
                       for m, p in self.prices.items()}
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def load_defaults(cls) -> "PriceTable":
        """从持久化文件加载价格表；不存在则用内置默认。"""
        import json
        from pathlib import Path
        path = Path.home() / ".token-running" / "prices.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                prices = {
                    m: (float(v["input"]), float(v["output"]), float(v["cache_read"]))
                    for m, v in data.items()
                }
                return cls(prices=prices)
            except (OSError, ValueError, KeyError):
                pass
        return cls()
