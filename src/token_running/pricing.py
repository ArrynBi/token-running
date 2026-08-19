"""费用计算：模型价格表（美元/人民币两套独立计价）+ DeepSeek 波峰波谷定价。

- 价格单位：USD 或 CNY / 1M tokens（输入 / 输出 / 缓存命中）
- 美元与人民币分别计价（DeepSeek 官方双货币定价，不做汇率换算），可在价格设置一键切换
- 波峰波谷（仅 DeepSeek 系列生效）：高峰时段（北京时间 9:00-12:00、14:00-18:00）按表价；
  其余空闲时段半价；其他模型（gpt 系列等）恒定表价
- 价格表存高峰价（原价）；空闲时段由 cost() 自动半价
- DeepSeek 人民币默认价按官方手册（api-docs.deepseek.com/quick_start/pricing，2026-08-17 峰谷定价生效）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# 默认价格表：模型名 -> (input, output, cache_read) /1M tokens
# 美元来自 cc-switch DB 反推（本机真实账单）；人民币按 DeepSeek 官方双货币计价风格
DEFAULT_PRICES_USD: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28, 0.0028),
    "deepseek-v4-pro": (1.68, 3.36, 0.14),
    "gpt-5.6-sol": (0.1071, 30.0, 0.5),
    "gpt-5.6-luna": (0.0055, 1.2, 0.02),
    "gpt-5.6-terra": (0.0457, 12.0, 0.2),
}

DEFAULT_PRICES_CNY: dict[str, tuple[float, float, float]] = {
    # DeepSeek 官方峰谷定价（元/百万 tokens，表内为高峰价）：
    # Flash: 高峰 输入3.0/缓存0.10/输出9.0；空闲半价
    # Pro:   高峰 输入9.0/缓存0.30/输出27.0；空闲半价
    "deepseek-v4-flash": (3.0, 9.0, 0.10),
    "deepseek-v4-pro": (9.0, 27.0, 0.30),
    "gpt-5.6-sol": (0.77, 216.0, 3.6),
    "gpt-5.6-luna": (0.04, 8.64, 0.14),
    "gpt-5.6-terra": (0.33, 86.4, 1.44),
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


def price_multiplier(ts: float, model: str = "") -> float:
    """按时段返回价格倍率：高峰 1.0，空闲 0.5。仅 DeepSeek 系列模型启用峰谷，其他模型恒定 1.0。"""
    if not model.startswith("deepseek"):
        return 1.0
    return 1.0 if is_peak(ts) else 0.5


CURRENCY_SYMBOL = {"usd": "$", "cny": "¥"}


@dataclass
class PriceTable:
    """双货币模型价格表；支持美元/人民币切换。"""

    prices_usd: dict[str, tuple[float, float, float]] = field(
        default_factory=lambda: dict(DEFAULT_PRICES_USD)
    )
    prices_cny: dict[str, tuple[float, float, float]] = field(
        default_factory=lambda: dict(DEFAULT_PRICES_CNY)
    )
    currency: str = "usd"   # "usd" | "cny"
    fallback: tuple[float, float, float] = (0.0, 0.0, 0.0)

    def _active(self) -> dict[str, tuple[float, float, float]]:
        return self.prices_cny if self.currency == "cny" else self.prices_usd

    def get_active(self) -> dict[str, tuple[float, float, float]]:
        """当前货币的完整价格表（用于弹窗展示）。"""
        return dict(self._active())

    def set_currency(self, currency: str) -> None:
        self.currency = currency if currency in ("usd", "cny") else "usd"

    def set(self, model: str, input_p: float, output_p: float, cache_read_p: float) -> None:
        """设置当前货币下某模型的价格。"""
        self._active()[model] = (input_p, output_p, cache_read_p)

    def get(self, model: str) -> tuple[float, float, float]:
        return self._active().get(model, self.fallback)

    def symbol(self) -> str:
        return CURRENCY_SYMBOL.get(self.currency, "$")

    def cost(self, model: str, input_t: int, output_t: int, cache_read_t: int, ts: float) -> float:
        """计算一条记录的费用（当前货币）；DeepSeek 模型按峰谷自动半价。"""
        i, o, cr = self.get(model)
        mult = price_multiplier(ts, model)
        return (input_t / 1e6 * i + output_t / 1e6 * o + cache_read_t / 1e6 * cr) * mult

    def save_defaults(self) -> None:
        """持久化双货币价格表。"""
        import json
        from pathlib import Path
        try:
            path = Path.home() / ".token-running" / "prices.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "currency": self.currency,
                "usd": {m: {"input": p[0], "output": p[1], "cache_read": p[2]} for m, p in self.prices_usd.items()},
                "cny": {m: {"input": p[0], "output": p[1], "cache_read": p[2]} for m, p in self.prices_cny.items()},
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    def load_defaults(cls) -> "PriceTable":
        """从持久化文件加载；不存在则用内置默认。"""
        import json
        from pathlib import Path
        path = Path.home() / ".token-running" / "prices.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                def parse(block):
                    return {m: (float(v["input"]), float(v["output"]), float(v["cache_read"]))
                            for m, v in (block or {}).items()}
                return cls(
                    prices_usd=parse(data.get("usd")) or dict(DEFAULT_PRICES_USD),
                    prices_cny=parse(data.get("cny")) or dict(DEFAULT_PRICES_CNY),
                    currency=data.get("currency", "usd"),
                )
            except (OSError, ValueError, KeyError):
                pass
        return cls()