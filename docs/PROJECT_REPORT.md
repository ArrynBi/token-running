# token-running 项目报告

> 生成日期：2026-08-18
> 用途：供 Codex 代码审查
> 仓库：https://github.com/ArrynBi/token-running

---

## 1. 项目概述

**token-running** 是一个 Windows 桌面浮窗工具，实时监测 AI 工具（DeepSeek Harness / Claude Code / Codex / Opencode）的 token 消耗与费用。

- **形态**：PySide6 透明置顶无边框浮窗，自绘柱状图
- **数据源**：本地文件/数据库增量轮询（无网络请求）
- **目标**：秒级可视化各工具的 token 用量与费用，验证实时链路

## 2. 技术栈与环境

| 项 | 值 |
|---|---|
| 语言 | Python 3.12.10 |
| GUI | PySide6 6.11.0 |
| 测试 | pytest 9.0.2（24 passed） |
| 打包 | PyInstaller 6.19.0（单文件 exe ~50MB） |
| 平台 | Windows 11 |
| 依赖 | 仅标准库 + PySide6 + zstandard |

## 3. 架构

```
┌─ 数据源（每秒轮询，增量） ─────────────────────────────┐
│ DeepseekTraceSource   ~/.dsh/sessions/**/*.jsonl.zstd  │
│ JsonlSource           ~/.claude/projects/**/*.jsonl     │
│ TokenSource           ~/.cc-switch/cc-switch.db         │
│   └ app_types=[codex/opencode]（避免与 JSONL 重复）      │
└──────────────────────────┬──────────────────────────────┘
                           │ poll() -> UsageEvent[]
                           ▼
┌─ RealtimeWindow（PySide6 浮窗）────────────────────────┐
│ 每渠道独立桶（秒/5秒/分钟）+ 费用桶                      │
│ 合并/分渠道双视图 · 皮肤系统 · 隐藏开关 · 窗口跨度        │
│ 二级右键菜单（子菜单点击不关闭）                         │
└────────────────────────────────────────────────────────┘
```

## 4. 数据源设计

### 4.1 三个数据源（同接口：poll / daily_total / total / online / set_windows）

| 源 | 位置 | 格式 | 延迟 |
|---|---|---|---|
| DeepseekTraceSource | `~/.dsh/sessions/**/session.jsonl.zstd` | zstd 压缩 JSONL 事件流 | 秒级 |
| JsonlSource | `~/.claude/projects/**/*.jsonl` | JSONL 会话文件 | 秒级 |
| TokenSource | `~/.cc-switch/cc-switch.db` | SQLite | 60s（cc-switch 同步周期） |

### 4.2 关键设计

- **增量读取**：各源维护偏移/已读位置，poll 只返回新事件（DSH 按已解析行数、JSONL 按字节偏移、DB 按 rowid）
- **模型识别**：UsageEvent 携带 model 与 token 明细（input/output/cache_read/cache_create），费用计算依赖此
- **去重**：cc-switch DB 的 claude 记录与 Claude JSONL 同源，DB 源用 `app_types=[codex,opencode]` 过滤避免重复
- **口径过滤**：`set_windows(today_since, total_since)` 支持今日/总量窗口重算

### 4.3 关键发现（与交接文档差异）

- 交接文档声称 cc-switch 同步延迟 2-7s，**实测为固定 60s 周期**（日志整分钟触发）
- 因此新增 JsonlSource 直读 JSONL 实现秒级，实测 0.4-1.5s
- DSH 用量在 `~/.deepseek/sessions/*.json` 及 `~/.dsh/sessions/**/session.jsonl.zstd` 轨迹中

## 5. 费用计算（pricing.py）

- **价格表**：USD/CNY 两套独立默认价（不做汇率换算，DeepSeek 双货币计价），模型名 → (输入/输出/缓存命中) 每 1M tokens
- **默认价格来源**：从 cc-switch DB 隐含单价反推（真实账单），含 deepseek-v4-flash/pro、gpt-5.6-sol/luna/terra
- **波峰波谷**：北京 9:00-12:00、14:00-18:00 高峰按表价，其余空闲半价（UTC+8 硬编码计算）
- **持久化**：`~/.token-running/prices.json`

## 6. UI 功能清单

### 6.1 视图

- 粒度：秒级（60s）/ 5秒级（300s）/ 分钟级（60min）
- 窗口跨度：完整（60s, 430px）/ ½版（30s, 215px）/ ⅓版（20s, 143px）
- 显示方式：合并单图 / 分渠道多图（上下排列，3 档刻度）
- 显示内容：Tokens / 费用（USD/CNY）

### 6.2 显示元素（可独立隐藏）

- 标题（隐藏时状态点保留）· 汇总数字 · 时间标 · 纵轴数字 · 横线

### 6.3 皮肤

- 深色 / 全透明（自动按背景亮度切黑/白字）/ 全透明（黑）
- 透明自动：采样窗口背景亮度（加权 0.299R+0.587G+0.114B），阈值 0.5 切黑/白

### 6.4 交互

- 左键双击：切换视图粒度
- 右键：二级菜单（子菜单点击选项不关闭，拦截 MouseButtonRelease 手动触发）
- 按住左键拖动（拖动后自动重采样背景亮度）

### 6.5 统计口径

- 今日：自然日（0点起）/ 近24小时
- 总量：全时段 / 近7天 / 近30天 / 近90天

## 7. 关键技术点与已知坑

### 7.1 PySide6 6.11 已知问题（均已规避）

1. **`drawText(rect, AlignRight)` 不渲染**：右对齐文本像素级验证为 0，改用手动 `QFontMetrics.horizontalAdvance` 定位
2. **`QMenu.addMenu()` 子菜单 GC 问题**：子菜单对象被回收，需显式 `QMenu(title, parent)` 构造
3. **QMenu 点击选项自动关闭**：事件级拦截（Close/Hide）无效，改为拦截 `MouseButtonRelease` 手动触发 action
4. **Windows DWM 阴影/圆角白边**：`showEvent` 里 `DwmSetWindowAttribute` 设 `DWMNCRENDERING_POLICY` 与 `DWMNCP_DONOTROUND`

### 7.2 其他

- SQLite `connect()` 对不存在路径会创建空文件：用 `PRAGMA table_info` 检测 model 列存在性
- 浮窗窗口可能被拖出屏幕：提供启动定位到主屏中央的能力
- 费用从浮窗启动时刻累计（历史费用不含启动前，因源只聚合 token 总数无模型明细）

## 8. 测试

24 个测试全部通过：

- `test_source.py`（9）：增量轮询、含缓存求和、当日 0 点、跨天重置、离线、app_types 过滤、窗口口径
- `test_jsonl_source.py`（7）：JSONL 解析、增量追加、当日/总量、离线、total
- `test_deepseek_trace.py`（4）：轨迹解析、增量、当日/总量、离线
- `test_combined_source.py`（4）：合并事件、求和、渠道过滤

## 9. 代码规模

| 文件 | 行数 | 职责 |
|---|---|---|
| realtime_ui.py | 823 | 浮窗 UI、绘制、菜单、皮肤、跨度 |
| deepseek_trace.py | 206 | DSH zstd 轨迹解析 |
| jsonl_source.py | 186 | Claude JSONL 解析 |
| source.py | 173 | cc-switch DB 轮询 |
| pricing.py | 128 | 价格表/波峰波谷/费用 |
| combined_source.py | 79 | 渠道合并/过滤 |
| main.py | 18 | 入口 |

## 10. Git 与发布

- 56 个提交，覆盖设计→TDD 红→实现→增强→修复全流程
- GitHub：https://github.com/ArrynBi/token-running（public）
- Release：v0.1.0（tokens 版）、v0.2.0（费用版）各附 exe；v0.3.0 tag 已建（exe 上传因代理受限未完成）
- 一键启动：`start.cmd`（双击，pythonw 无控制台）

## 11. 待改进 / 已知限制

- 费用历史：重启浮窗后今日费用从 0 累计（需重放历史事件按模型明细重算）
- v0.3.0 的 exe 附件未上传（代理对 uploads.github.com 大文件传输不通）
- `BAR_DIM`/`bar_dim` 皮肤字段已不再使用（旧柱渐暗已移除）
- 设置（皮肤/跨度/隐藏项）未持久化，重启恢复默认
- `_channel_label()` 方法已无调用方（标题不再显示渠道组合）

## 12. 审查关注点

请 Codex 重点审查：

1. **数据源增量正确性**：偏移/行数维护在文件重建、跨天、并发追加下的正确性
2. **费用计算准确性**：波峰波谷时段、模型识别（DSH 轨迹经 request/header 关联）、双货币切换
3. **UI 线程模型**：QTimer 每秒 tick 与绘制的一致性、子菜单事件过滤器的副作用
4. **跨天/窗口口径**：set_windows 与 poll 增量累计的交互（可能重复或漏计）
5. **性能**：split 多图每 tick 的桶清理与合并开销
6. **代码质量**：realtime_ui.py 已 823 行，是否需拆分；死代码清理

---

*本报告由 DeepSeek harness 生成，供 Codex 审查参考。*