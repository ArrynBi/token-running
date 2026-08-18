"""透明置顶浮窗：流动柱状图（每秒消耗量，左进右移）+ 当日总消耗数字。"""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from token_running.deepseek_trace import DeepseekTraceSource
from token_running.jsonl_source import JsonlSource
from token_running.pricing import PriceTable
from token_running.source import TokenSource

WIDTH, HEIGHT = 410, 170   # 收窄左侧空白；底部留空间给横轴时间刻度
BAR_WINDOW = 60          # 柱状图窗口：最近 60 秒
CHART_LEFT, CHART_TOP = 30, 52    # 左侧仅留 14px 给纵轴刻度（刻度数字贴边即可）
CHART_RIGHT, CHART_BOTTOM = 396, 146  # 图表区向下扩展，占用更多空间
SPLIT_ROW_H = 80             # 分渠道多图：每渠道一行的高度（含顶部渠道名行与纵轴区）
BG_COLOR = QColor(13, 17, 28, 252)  # 接近不透明（alpha 252），减少桌面透光导致的时暗时亮
BAR_COLOR = QColor(94, 200, 130, 210)
BAR_DIM = QColor(94, 200, 130, 60)
TEXT_COLOR = QColor(235, 240, 248, 230)
MUTED = QColor(140, 150, 165, 200)
GREEN = QColor(94, 200, 130)
RED = QColor(230, 90, 90)


def _format_compact(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _format_cost(value: float, symbol: str = "$") -> str:
    """费用格式化：统一两位小数（当前货币符号）。"""
    return symbol + format(value, ".2f")


class RealtimeWindow(QWidget):
    def __init__(self, source: TokenSource | None = None) -> None:
        super().__init__(None)
        # 渠道：name -> 数据源（每渠道独立桶，勾选只控制显示，数据始终保留）
        if source is not None:
            self._channels: dict[str, object] = {"custom": source}
        else:
            self._channels = {
                "dsh": DeepseekTraceSource(),
                "claude": JsonlSource(),
                "codex": TokenSource(app_types=["codex"]),      # DB 排除 claude 避免与 JSONL 重复
                "opencode": TokenSource(app_types=["opencode"]),
            }
        self._enabled: set[str] = set(self._channels.keys())  # 默认全选
        self._bar_order = list(self._channels.keys())
        self._bars_sec: dict[str, dict[int, int]] = {n: {} for n in self._channels}  # 渠道 -> ts(秒) -> tokens
        self._bars_5s: dict[str, dict[int, int]] = {n: {} for n in self._channels}
        self._bars_min: dict[str, dict[int, int]] = {n: {} for n in self._channels}
        self._cost_sec: dict[str, dict[int, float]] = {n: {} for n in self._channels}  # 渠道 -> ts(秒) -> 费用 USD
        self._cost_5s: dict[str, dict[int, float]] = {n: {} for n in self._channels}
        self._cost_min: dict[str, dict[int, float]] = {n: {} for n in self._channels}
        self._prices = PriceTable.load_defaults()      # 模型价格表（可 UI 修改）
        self._mode: str = "sec"              # "sec" 秒级 60s 窗口 | "5s" 5 秒级 300s 窗口 | "min" 分钟级 60min 窗口
        self._view_mode: str = "combined"    # "combined" 合并单图 | "split" 分渠道多图（上下排列）
        self._display: str = "tokens"        # 显示内容："tokens" | "cost"（费用 USD）
        self._today_caliber: str = "day"     # 今日口径："day" 自然日 | "24h" 近24小时
        self._total_caliber: str = "all"     # 总量口径："all" 全时段 | "7d" 近7天 | "30d" 近30天 | "90d" 近90天
        self._drag_offset: QPoint | None = None
        self._cost_daily = 0.0   # 今日累计费用（USD）
        self._cost_total = 0.0   # 全时段累计费用（USD）

        self.setWindowTitle("Token Running")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFixedSize(WIDTH, HEIGHT)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def showEvent(self, event) -> None:  # noqa: N802
        """窗口显示后移除 DWM 阴影与圆角（避免四周白边）。"""
        super().showEvent(event)
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = int(self.winId())
            # DWMWA_NCRENDERING_POLICY=2 (DWMNCRP_DISABLED)：禁用非客户区渲染（去阴影）
            # DWMWA_WINDOW_CORNER_PREFERENCE=33 (DWMWCP_DONOTROUND=1)：禁系统圆角
            for attr, val in ((2, 2), (33, 1)):
                v = ctypes.c_int(val)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), attr, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass

    # ---- 数据 ----

    def _tick(self) -> None:
        now = int(time.time())
        sec_cutoff = now - BAR_WINDOW
        cutoff_5s = (now // 5 * 5) - BAR_WINDOW * 5
        min_cutoff = (now // 60 * 60) - BAR_WINDOW * 60
        for name, src in self._channels.items():
            for ev in src.poll():
                cost = self._prices.cost(ev.model, ev.input_t, ev.output_t, ev.cache_read_t, ev.ts)
                self._cost_total += cost
                if ev.ts >= self._today_start_epoch():
                    self._cost_daily += cost
                b = self._bars_sec[name]
                b[ev.ts] = b.get(ev.ts, 0) + ev.tokens
                cb = self._cost_sec[name]
                cb[ev.ts] = cb.get(ev.ts, 0) + cost
                k5 = ev.ts // 5 * 5
                b5 = self._bars_5s[name]
                b5[k5] = b5.get(k5, 0) + ev.tokens
                c5 = self._cost_5s[name]
                c5[k5] = c5.get(k5, 0) + cost
                mk = ev.ts // 60 * 60
                bm = self._bars_min[name]
                bm[mk] = bm.get(mk, 0) + ev.tokens
                cm = self._cost_min[name]
                cm[mk] = cm.get(mk, 0) + cost
            # 各渠道独立清理窗口外的桶（tokens 与 cost 同步）
            for ts in [t for t in self._bars_sec[name] if t < sec_cutoff]:
                del self._bars_sec[name][ts]
                del self._cost_sec[name][ts]
            for k in [t for t in self._bars_5s[name] if t < cutoff_5s]:
                del self._bars_5s[name][k]
                del self._cost_5s[name][k]
            for m in [t for t in self._bars_min[name] if t < min_cutoff]:
                del self._bars_min[name][m]
                del self._cost_min[name][m]
        self.update()

    # ---- 拖动 ----

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # 左键双击：循环切换 秒级 -> 5秒级 -> 分钟级
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            self._cycle_mode()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            # 右键释放：打开菜单
            self._drag_offset = None
            self._show_menu(event.globalPosition().toPoint())
            event.accept()
            return
        self._drag_offset = None
        event.accept()

    # ---- 模式与菜单 ----

    def _cycle_mode(self) -> None:
        order = ("sec", "5s", "min")
        self._mode = order[(order.index(self._mode) + 1) % len(order)]
        self.update()

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self.update()

    def _set_channel(self, channel: str, enabled: bool) -> None:
        # 只切换显示开关；各渠道桶数据始终保留，重新勾选立即恢复显示
        if enabled:
            self._enabled.add(channel)
        else:
            self._enabled.discard(channel)
        self._refresh_height()
        self.update()

    def _set_view_mode(self, mode: str) -> None:
        self._view_mode = mode
        self._refresh_height()
        self.update()

    def _set_display(self, display: str) -> None:
        self._display = display
        self.update()

    def _set_currency(self, currency: str) -> None:
        """切换显示货币：价格表切货币，并重置费用累计（旧累计是旧货币）。"""
        self._prices.set_currency(currency)
        self._cost_daily = 0.0
        self._cost_total = 0.0
        self.update()

    def _open_price_dialog(self) -> None:
        """价格设置：美元/人民币一键切换，各自编辑输入/输出/缓存命中价格。"""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
                                       QLabel, QComboBox, QHBoxLayout)
        dlg = QDialog(self)
        dlg.setWindowTitle("价格设置（/ 1M tokens）")
        root = QFormLayout(dlg)

        # 货币切换行
        cur_row = QHBoxLayout()
        cur_row.addWidget(QLabel("货币："))
        combo = QComboBox()
        combo.addItem("USD（美元）", "usd")
        combo.addItem("CNY（人民币）", "cny")
        combo.setCurrentIndex(0 if self._prices.currency == "usd" else 1)
        cur_row.addWidget(combo)
        cur_row.addWidget(QLabel("  （美元/人民币分别计价）"))
        root.addRow(cur_row)
        root.addRow(QLabel("波峰 9:00-12:00、14:00-18:00（北京）；空闲时段半价"))

        editors = {}

        def rebuild():
            # 清空旧的模型行，按当前货币重建
            while root.rowCount() > 2:
                root.removeRow(root.rowCount() - 1)
            editors.clear()
            for model, (i, o, cr) in sorted(self._prices.get_active().items()):
                row = [QLineEdit(str(i)), QLineEdit(str(o)), QLineEdit(str(cr))]
                for w in row:
                    w.setFixedWidth(80)
                root.addRow(model, row[0])
                root.addRow("   输出", row[1])
                root.addRow("   缓存命中", row[2])
                editors[model] = row

        def on_currency(idx):
            # 保存当前货币已编辑的值，再切货币重建
            self._save_price_edits(editors)
            self._prices.set_currency(combo.itemData(idx))
            rebuild()

        combo.currentIndexChanged.connect(on_currency)
        rebuild()

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        root.addRow(btns)
        if dlg.exec():
            self._save_price_edits(editors)
            self._prices.set_currency(combo.itemData(combo.currentIndex()))
            self._prices.save_defaults()
            self._cost_daily = 0.0
            self._cost_total = 0.0
            self.update()

    def _save_price_edits(self, editors: dict) -> None:
        """把弹窗中当前货币的编辑值写回价格表。"""
        for model, row in editors.items():
            try:
                self._prices.set(model, float(row[0].text()), float(row[1].text()), float(row[2].text()))
            except ValueError:
                pass

    def _set_caliber(self, which: str, value: str) -> None:
        if which == "today":
            self._today_caliber = value
        else:
            self._total_caliber = value
        self._apply_caliber()

    def _apply_caliber(self) -> None:
        now = int(time.time())
        if self._today_caliber == "24h":
            today_since = now - 86400
        else:
            today_since = None  # 自然日 0 点（各源默认）
        total_map = {"all": None, "7d": now - 7 * 86400, "30d": now - 30 * 86400, "90d": now - 90 * 86400}
        total_since = total_map.get(self._total_caliber, None)
        for src in self._channels.values():
            if hasattr(src, "set_windows"):
                src.set_windows(today_since, total_since)
        self.update()

    SPLIT_AXIS_H = 20           # 分渠道模式底部给横轴时间刻度预留的高度

    def _refresh_height(self) -> None:
        """合并模式固定高度；分渠道模式按启用渠道数增高（每渠道一行 + 底部横轴区）。"""
        if self._view_mode == "split":
            n = sum(1 for c in self._bar_order if c in self._enabled) or 1
            self.setFixedSize(WIDTH, CHART_TOP + n * SPLIT_ROW_H + self.SPLIT_AXIS_H)
        else:
            self.setFixedSize(WIDTH, HEIGHT)

    # ---- 渠道聚合辅助 ----

    def _is_channel_enabled(self, channel: str) -> bool:
        return channel in self._enabled

    def _merged(self, per_channel: dict[str, dict[int, int]], cost_per_channel: dict[str, dict[int, float]] | None = None) -> dict:
        """合并已启用渠道的桶（同槽求和）。display=cost 时按费用桶。"""
        merged: dict[int, float] = {}
        src_buckets = cost_per_channel if self._display == "cost" and cost_per_channel is not None else per_channel
        for name in self._bar_order:
            if name not in self._enabled:
                continue
            for ts, val in src_buckets.get(name, {}).items():
                merged[ts] = merged.get(ts, 0.0) + val
        return merged

    def _today_start_epoch(self) -> int:
        t = time.localtime()
        return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))

    def _daily_total(self):
        if self._display == "cost":
            return self._cost_daily
        return sum(src.daily_total() for name, src in self._channels.items() if name in self._enabled)

    def _total(self):
        if self._display == "cost":
            return self._cost_total
        return sum(src.total() for name, src in self._channels.items() if name in self._enabled)

    def _online(self) -> bool:
        return any(src.online() for name, src in self._channels.items() if name in self._enabled)

    def _channel_label(self) -> str:
        label_map = {"dsh": "DSH", "claude": "CLAUDE", "codex": "CODEX", "opencode": "OPENCODE", "custom": "CUSTOM"}
        parts = [label_map.get(n, n.upper()) for n in self._bar_order if n in self._enabled]
        return "+".join(parts) if parts else "OFF"

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for key, label in (("sec", "秒级（60s 窗口）"), ("5s", "5 秒级（300s 窗口）"), ("min", "分钟级（60min 窗口）")):
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(self._mode == key)
            act.triggered.connect(lambda checked=False, k=key: self._set_mode(k))
            group.addAction(act)
            menu.addAction(act)
        menu.addSeparator()
        # 显示方式：合并单图 / 分渠道多图
        vtitle = QAction("显示方式", menu)
        vtitle.setEnabled(False)
        menu.addAction(vtitle)
        vgroup = QActionGroup(menu)
        vgroup.setExclusive(True)
        for key, label in (("combined", "合并显示"), ("split", "分渠道多图（上下）")):
            vact = QAction(label, menu)
            vact.setCheckable(True)
            vact.setChecked(self._view_mode == key)
            vact.triggered.connect(lambda checked=False, k=key: self._set_view_mode(k))
            vgroup.addAction(vact)
            menu.addAction(vact)
        menu.addSeparator()
        # 统计口径：今日 / 总量
        ctitle = QAction("统计口径", menu)
        ctitle.setEnabled(False)
        menu.addAction(ctitle)
        tgroup = QActionGroup(menu)
        tgroup.setExclusive(True)
        for key, label in (("day", "今日 · 自然日（0点起）"), ("24h", "今日 · 近24小时")):
            tact = QAction(label, menu)
            tact.setCheckable(True)
            tact.setChecked(self._today_caliber == key)
            tact.triggered.connect(lambda checked=False, k=key: self._set_caliber("today", k))
            tgroup.addAction(tact)
            menu.addAction(tact)
        ogroup = QActionGroup(menu)
        ogroup.setExclusive(True)
        for key, label in (("all", "总量 · 全时段"), ("7d", "总量 · 近7天"), ("30d", "总量 · 近30天"), ("90d", "总量 · 近90天")):
            oact = QAction(label, menu)
            oact.setCheckable(True)
            oact.setChecked(self._total_caliber == key)
            oact.triggered.connect(lambda checked=False, k=key: self._set_caliber("total", k))
            ogroup.addAction(oact)
            menu.addAction(oact)
        menu.addSeparator()
        # 显示内容：tokens / 费用
        dtitle = QAction("显示内容", menu)
        dtitle.setEnabled(False)
        menu.addAction(dtitle)
        dgroup = QActionGroup(menu)
        dgroup.setExclusive(True)
        for key, label in (("tokens", "显示 Tokens"), ("cost", "显示费用（USD）")):
            dact = QAction(label, menu)
            dact.setCheckable(True)
            dact.setChecked(self._display == key)
            dact.triggered.connect(lambda checked=False, k=key: self._set_display(k))
            dgroup.addAction(dact)
            menu.addAction(dact)
        price_act = QAction("价格设置…", menu)
        price_act.triggered.connect(self._open_price_dialog)
        menu.addAction(price_act)
        menu.addSeparator()
        # 渠道多选：只显示选中的来源（DSH / Claude Code / Codex）
        title_act = QAction("显示渠道", menu)
        title_act.setEnabled(False)
        menu.addAction(title_act)
        for ch, label in (
            ("dsh", "DSH（DeepSeek）"),
            ("claude", "Claude Code"),
            ("codex", "Codex"),
            ("opencode", "Opencode"),
        ):
            if ch not in self._channels:
                continue
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(self._is_channel_enabled(ch))
            act.toggled.connect(lambda checked, c=ch: self._set_channel(c, checked))
            menu.addAction(act)
        menu.addSeparator()
        quit_act = menu.addAction("退出")
        quit_act.triggered.connect(self.close)
        return menu

    def _show_menu(self, pos: QPoint) -> None:
        self._build_menu().exec(pos)

    # ---- 绘制 ----

    def _draw_text_right(self, p: QPainter, x_right: int, y: int, text: str) -> None:
        """右对齐文本：PySide6 6.11 的 drawText(rect, AlignRight) 有 bug 不渲染，改用手动宽度定位。"""
        fm = p.fontMetrics()
        p.drawText(x_right - fm.horizontalAdvance(text), y, text)

    def _slot_of(self, mode: str, cur: int, i: int) -> int:
        """按模式计算第 i 个槽（offset=0 最新）对应的时间起点。"""
        if mode == "min":
            return cur - i * 60
        if mode == "5s":
            return cur - i * 5
        return cur - i

    def _mode_cur(self, mode: str, now_sec: int) -> int:
        if mode == "min":
            return now_sec // 60 * 60
        if mode == "5s":
            return now_sec // 5 * 5
        return now_sec

    def _mode_unit(self, mode: str) -> str:
        return {"min": "tok/min", "5s": "tok/5s"}.get(mode, "tok")

    def _fmt_value(self, val: float) -> str:
        """按显示模式格式化数值：tokens 用 K/M（一位小数），cost 用当前货币（两位）。"""
        if self._display == "cost":
            return _format_cost(val, self._prices.symbol())
        if val < 1:
            return f"{val:.1f}" if val > 0 else "0"
        if val >= 1_000_000:
            return f"{val / 1_000_000:.1f}M"
        if val >= 1_000:
            return f"{val / 1_000:.1f}K"
        # 小于 1000：整数显示整数，否则保留一位小数（避免 1.5 -> 2 与满刻度重复）
        if abs(val - round(val)) < 1e-9:
            return f"{val:.0f}"
        return f"{val:.1f}"

    def _nice_ticks(self, max_val: float) -> list[float]:
        """生成整数友好的纵轴挡位（0 / 1/4 / 1/2 / 3/4 / 满），满刻度向上取整到 1/2/5×10^n。"""
        import math
        if max_val <= 0:
            return [0.0, 0.0, 0.0, 0.0, 0.0]
        # 确定步长数量级：取 max 的 1/4 向上凑到 1/2/5 系列
        quarter = max_val / 4
        mag = 10 ** math.floor(math.log10(quarter)) if quarter > 0 else 1
        for mult in (1, 2, 5, 10):
            step = mult * mag
            if step >= quarter:
                break
        # 满刻度 = step * 4（保证 1/4 挡是整数）
        nice_max = step * 4
        return [0.0, nice_max / 4, nice_max / 2, nice_max * 3 / 4, nice_max]

    def _paint_combined_chart(self, p: QPainter) -> None:
        """合并单图：所有启用渠道求和后一张柱状图。"""
        now_sec = int(time.time())
        chart_w = CHART_RIGHT - CHART_LEFT
        chart_h = CHART_BOTTOM - CHART_TOP
        step = chart_w / BAR_WINDOW
        bar_w = max(1.0, step * 0.6)
        if self._mode == "min":
            buckets = self._merged(self._bars_min, self._cost_min)
        elif self._mode == "5s":
            buckets = self._merged(self._bars_5s, self._cost_5s)
        else:
            buckets = self._merged(self._bars_sec, self._cost_sec)
        cur = self._mode_cur(self._mode, now_sec)
        max_val = max(buckets.values(), default=0) or 1

        # 纵轴刻度：整数友好挡位（0 / 1/4 / 1/2 / 满）
        ticks = self._nice_ticks(max_val)
        p.setPen(MUTED)
        fy = QFont("Segoe UI", 7)
        p.setFont(fy)
        for frac, val in zip((0.0, 0.25, 0.5, 0.75, 1.0), ticks):  # frac 升序对应 ticks 升序
            y = CHART_BOTTOM - int(chart_h * frac)
            self._draw_text_right(p, CHART_LEFT - 6, y - 4, self._fmt_value(val))
        p.setPen(QColor(255, 255, 255, 25))
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = CHART_BOTTOM - int(chart_h * frac)
            p.drawLine(CHART_LEFT, y, CHART_RIGHT, y)

        for offset in range(BAR_WINDOW):
            val = buckets.get(self._slot_of(self._mode, cur, offset), 0)
            x = CHART_LEFT + offset * step
            h = max(2.0, chart_h * val / ticks[-1])
            p.setBrush(BAR_DIM if offset > 40 else BAR_COLOR)
            p.drawRect(int(x), int(CHART_BOTTOM - h), int(bar_w), int(h))

        p.setPen(QColor(255, 255, 255, 30))
        p.drawLine(CHART_LEFT, CHART_BOTTOM + 2, CHART_RIGHT, CHART_BOTTOM + 2)
        # 横轴时间刻度
        self._paint_x_axis(p, now_sec, chart_w, CHART_BOTTOM + 4)

    def _paint_split_charts(self, p: QPainter) -> None:
        """分渠道多图：每个启用渠道一个上下排列的柱状图。"""
        now_sec = int(time.time())
        chart_w = CHART_RIGHT - CHART_LEFT
        step = chart_w / BAR_WINDOW
        bar_w = max(1.0, step * 0.6)
        enabled = [n for n in self._bar_order if n in self._enabled]
        n = len(enabled) or 1
        row_h = (self.height() - CHART_TOP - self.SPLIT_AXIS_H) / n
        label_map = {"dsh": "DSH", "claude": "CLAUDE", "codex": "CODEX", "opencode": "OPENCODE", "custom": "CUSTOM"}
        cur = self._mode_cur(self._mode, now_sec)

        for i, name in enumerate(enabled):
            y_top = CHART_TOP + i * row_h
            y_bot = y_top + row_h
            # 渠道名独占顶部一行（不跟纵轴数字同带）
            p.setPen(MUTED)
            p.setFont(QFont("Segoe UI", 8))
            p.drawText(4, y_top, 60, 14, Qt.AlignmentFlag.AlignLeft, label_map.get(name, name.upper()))
            # 柱状图区（渠道名行之下，留足间距避免纵轴与渠道名重叠）
            chart_top = y_top + 26
            chart_bot = y_bot - 2
            row_h_chart = chart_bot - chart_top
            if self._mode == "min":
                buckets = self._bars_min.get(name, {})
                cost_buckets = self._cost_min.get(name, {})
            elif self._mode == "5s":
                buckets = self._bars_5s.get(name, {})
                cost_buckets = self._cost_5s.get(name, {})
            else:
                buckets = self._bars_sec.get(name, {})
                cost_buckets = self._cost_sec.get(name, {})
            if self._display == "cost":
                buckets = cost_buckets
            max_val = max(buckets.values(), default=0) or 1
            # 纵轴：整数友好挡位（0 / 1/4 / 1/2 / 满），画在柱状图区左侧（渠道名带之下），配四条横线
            # 分渠道多图：纵轴只用 3 挡（0 / 1/2 / 满）
            ticks = self._nice_ticks(max_val)
            ticks_3 = [ticks[0], ticks[2], ticks[4]]  # 0, 1/2, 满
            nice_max = ticks[-1]
            p.setFont(QFont("Segoe UI", 7))
            for frac, val in zip((0.0, 0.5, 1.0), ticks_3):  # frac 升序对应值升序
                y = chart_bot - int(row_h_chart * frac)
                self._draw_text_right(p, CHART_LEFT - 6, y - 4, self._fmt_value(val))
            p.setPen(QColor(255, 255, 255, 25))
            for frac in (0.0, 0.5, 1.0):
                y = chart_bot - int(row_h_chart * frac)
                p.drawLine(CHART_LEFT, y, CHART_RIGHT, y)
            # 柱状图（按 nice_max 归一化，满刻度与最高柱对齐）
            for offset in range(BAR_WINDOW):
                val = buckets.get(self._slot_of(self._mode, cur, offset), 0)
                x = CHART_LEFT + offset * step
                h = max(2.0, row_h_chart * val / nice_max)
                p.setBrush(BAR_DIM if offset > 40 else BAR_COLOR)
                p.drawRect(int(x), int(chart_bot - h), int(bar_w), int(h))
            # 行底分隔线
            p.setPen(QColor(255, 255, 255, 25))
            p.drawLine(CHART_LEFT, int(y_bot), CHART_RIGHT, int(y_bot))

        # 底部预留区画横轴刻度（避免被遮挡）
        self._paint_x_axis(p, now_sec, chart_w, int(self.height() - self.SPLIT_AXIS_H + 4))

    def _paint_x_axis(self, p: QPainter, now_sec: int, chart_w: float, y: int) -> None:
        p.setPen(MUTED)
        fx = QFont("Segoe UI", 7)
        p.setFont(fx)
        if self._mode == "min":
            window_val, x_unit = 60, "min"
            x_ticks = [0, 15, 30, 45, 60]
        elif self._mode == "5s":
            window_val, x_unit = 300, "s"
            x_ticks = [0, 75, 150, 225, 300]
        else:
            window_val, x_unit = 60, "s"
            x_ticks = [0, 15, 30, 45, 60]
        x_label_w = 30
        for age in x_ticks:
            x = CHART_LEFT + (age / window_val) * chart_w
            x = max(CHART_LEFT, min(CHART_RIGHT - x_label_w, x))
            p.drawText(int(x), y, x_label_w, 10, Qt.AlignmentFlag.AlignLeft, f"{age}{x_unit}")

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(BG_COLOR)
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 16, 16)

        # 标题行：状态点 + 名称（显示当前渠道组合）
        online = self._online()
        p.setBrush(GREEN if online else RED)
        p.drawEllipse(22, 12, 8, 8)
        p.setPen(MUTED)
        f = QFont("Segoe UI", 9)
        p.setFont(f)
        mode_text = {"sec": "秒级", "5s": "5秒级", "min": "分钟级"}.get(self._mode, "秒级")
        unit_tag = "COST" if self._display == "cost" else "TOKENS"
        p.drawText(38, 20, f"TOKEN RUNNING · {unit_tag} · " + ("实时" if online else "离线") + f" · {mode_text}")

        # 右上角：今日（8pt 小字）与数字（17pt 大字）同排，右缘对齐；下方总量紧跟无空行
        f3 = QFont("Segoe UI", 8)
        f2 = QFont("Segoe UI", 17, QFont.Weight.DemiBold)
        p.setFont(f2)
        p.setPen(TEXT_COLOR)
        fm2 = p.fontMetrics()
        num_text = self._fmt_value(self._daily_total())
        num_w = fm2.horizontalAdvance(num_text)
        p.drawText((WIDTH - 4) - num_w, 26, num_text)  # 数字基线 y=26（下移一行）
        p.setFont(f3)
        p.setPen(MUTED)
        fm3 = p.fontMetrics()
        p.drawText((WIDTH - 4) - num_w - 6 - fm3.horizontalAdvance("今日"), 24, "今日")  # 8pt 小字贴数字左侧
        self._draw_text_right(p, WIDTH - 4, 40, f"总量 {self._fmt_value(self._total())}")  # 紧跟今日行下方

        # 柱状图主体：按显示方式（合并 / 分渠道多图）
        if self._view_mode == "split":
            self._paint_split_charts(p)
        else:
            self._paint_combined_chart(p)
