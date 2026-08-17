# token-running — 秒级动态浮窗 Token 消耗监测（MVP 设计）

- 日期：2026-08-17
- 状态：已批准（用户确认，含两处修改：柱状图方向、独立新项目）

## 1. 背景与目标

在 cc-switch 基础上做一个秒级动态浮窗 token 消耗监测小程序。MVP 目标是**验证实时链路是否成立**：Claude Code 每次消息完成 → cc-switch 同步入库 → 浮窗在数秒内以流动柱状图呈现。

已有项目 [token-monitor](../../token-monitor/)（PySide6 悬浮球，查 OpenAI 兼容网关额度）为**参考实现**：复用其 PySide6 透明置顶浮窗模式与格式化工具，但**内核逻辑不同**——本项目读本地 cc-switch 数据库，不发起任何网络请求。

## 2. 数据源（已验证）

- `~/.cc-switch/cc-switch.db`（SQLite），表 `proxy_request_logs`：
  - 字段：`rowid`、`created_at`（秒级时间戳，请求完成时刻）、`app_type`、`input_tokens`、`output_tokens`、`cache_read_tokens`、`cache_creation_tokens`、`total_cost_usd`、`status_code`、`error_message`
- 同步机制（实验验证结论）：Claude Code 消息完成即时写入 JSONL → cc-switch 事件驱动、2–7 秒内解析入库 → 空闲期静止。SQLite 只读轮询无并发问题。
- 依赖 cc-switch 运行；未运行时数据静止（UI 显示离线状态点）。

## 3. 统计口径（用户已确认）

| 项 | 口径 |
|---|---|
| 每秒消耗量 | 该秒（`created_at`）内完成请求的 input + output + cache_read + cache_creation tokens 之和（含缓存全量） |
| 当日总消耗量 | 本地时区今日 0 点起所有请求的全量 tokens 之和 |
| 统计范围 | 全部工具（claude / codex / opencode，不按 app_type 过滤） |

## 4. 架构

```
cc-switch 数据库 (~/.cc-switch/cc-switch.db)
        │ 每秒轮询，WHERE rowid > last_max 增量拉取
        ▼
src/token_running/source.py —— 按 created_at 分桶 → 每秒桶值；当日累计（启动时 SQL 聚合 + 增量累加，跨天重置）
        │ QTimer 1s tick
        ▼
src/token_running/realtime_ui.py —— RealtimeWindow：透明/无边框/置顶/可拖动；paintEvent 自绘
        ▲
src/token_running/main.py —— 入口
```

### 组件

1. **`source.py`**（无 GUI 依赖，可独立测试）
   - `TokenSource` 类：持 `last_rowid`，`poll() -> list[Event]` 每秒调用；失败静默重试
   - 当日累计：启动时 `SELECT SUM(...) WHERE created_at >= 今日0点`，之后增量累加；日期变化自动重置
2. **`realtime_ui.py`**
   - `RealtimeWindow(QWidget)`：`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`，半透明圆角（复用 token-monitor 的窗口模式）
   - `paintEvent` 自绘：
     - 流动柱状图：最近 60 秒每秒消耗量，**新柱从左侧进入、整体右移**（用户指定方向）；无请求的秒为 0
     - 当日总消耗：大数字 +「今日」标签
     - 状态点：数据源在线（绿）/ 离线（灰红）
   - `QTimer(1000ms)`：poll → 更新环形桶 → `update()`
   - 可拖动（复用 MonitorWindow 的 mousePress/Move 逻辑）
3. **`main.py`**：创建 QApplication + RealtimeWindow，show + exec

## 5. 目录结构

```text
token-running/
  main.py
  requirements.txt          # PySide6
  src/token_running/
    __init__.py
    source.py
    realtime_ui.py
  docs/superpowers/specs/2026-08-17-token-running-mvp-design.md
```

## 6. 错误处理与边界

- 数据库文件不存在 / 被占用：捕获异常，保留旧数据，下一轮重试；UI 状态点置离线
- `last_rowid` 回退（库重建）：重启时重新全量聚合当日
- 跨天：当日累计归零重算
- 同步延迟 2–7 秒为预期行为（非错误），柱状图按完成时刻分桶，视觉平滑

## 7. MVP 范围外（明确不做）

- UI 美化、设置页、主题
- JSONL 直读 / 双源
- 修改 token-monitor 现有功能
- 打包（exe/dmg）
- codex 单独视图、成本显示（MVP 只显示 token 量）

## 8. 验证标准

1. `python main.py` 启动浮窗
2. 在 Claude Code 中跑若干轮对话
3. 每轮消息完成后 **2–7 秒内**柱状图左侧出现新柱，当日总量随之跳动
4. 空闲 30 秒柱状图静止但不崩溃
