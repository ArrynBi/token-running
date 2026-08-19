# token-running 交接文档（Handoff）

> 本文档交付给 **DeepSeek harness** 执行。项目背景、已验证事实、设计决策、实施计划均已固化在仓库内。请先通读本文档，再按 [实施计划](docs/superpowers/plans/2026-08-17-token-running-mvp.md) 逐任务执行。
>
> 交接日期：2026-08-17

---

## 1. 任务一句话

在 `c:\Users\admin\Desktop\Coding\token-running\` 实现一个 **秒级动态浮窗 token 消耗监测小程序（MVP）**：轮询 cc-switch 的本地 SQLite 数据库，以**流动柱状图**（新柱从左侧进入、整体右移，最近 60 秒窗口）呈现**每秒消耗量**，以**数字**呈现**当日总消耗量**。目标是**验证实时链路是否成立**，UI 无需美化。

## 2. 项目位置与状态

- 项目根：`C:\Users\admin\Desktop\Coding\token-running\`（**独立 git 仓库**，已初始化）
- 已提交：设计文档 + 实施计划（提交 `1503e3e`、`23563be`）
- 待实现：`src/token_running/{source.py, realtime_ui.py}`、`tests/test_source.py`、`main.py`、`requirements.txt`、`src/token_running/__init__.py`
- 参考项目：`C:\Users\admin\Desktop\Coding\token-monitor\`（PySide6 悬浮球，可借鉴窗口模式与格式化函数，**不得修改它**）

## 3. 数据源（已实验验证，勿重复验证）

| 项 | 事实 |
|---|---|
| 数据库 | `C:\Users\admin\.cc-switch\cc-switch.db`（SQLite，**由 cc-switch 程序写入**，本项目只读轮询） |
| 核心表 | `proxy_request_logs`，字段：`rowid, created_at, app_type, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, total_cost_usd, status_code, error_message` |
| 同步链路 | Claude Code 消息完成 → **即时**写入 `~/.claude/projects/**/*.jsonl` → cc-switch **固定 60 秒周期**扫描入库（日志显示整分钟触发，非事件驱动）→ 空闲期静止 |
| 同步延迟实测 | **cc-switch 入库：最长 ~60 秒**（2026-08-17 实测：JSONL 22:50:21 写入 → DB 22:50:22 入库，恰为下一个整分钟同步周期）；**JSONL 直读：0.4–1.5 秒**（JsonlSource 增量解析实测） |
| 并发 | SQLite 只读轮询（每秒 1 次）无冲突；JSONL 增量按字节偏移读取，追加式无冲突 |
| 时间戳 | `created_at` 为 epoch **秒**（请求完成时刻），与本地时区一致；JSONL 用 `timestamp`（ISO 8601 UTC）转 epoch 秒 |
| 数据规模 | 现有约 12 万条记录，含 claude / codex / opencode 三种 app_type |

**实时性结论（已修正）**：cc-switch DB 路径延迟 ~60 秒（分钟级同步）；项目已加 **JsonlSource 直读 JSONL 兜底，实测 0.4–1.5 秒**，浮窗秒级显示。原交接文档声称的"事件驱动 2–7 秒"与实测不符，已废弃。

## 4. 已确认的设计决策（用户拍板，勿更改）

1. 数据源：**cc-switch 数据库**（非网络请求）+ **JSONL 直读兜底**（2026-08-17 用户拍板新增：`JsonlSource` 增量解析 `~/.claude/projects/**/*.jsonl`，实测秒级；UI 默认用它，JSONL 不可用时回退 DB）
2. 每秒消耗口径：**含缓存全量** = `input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens`，按 `created_at` 秒分桶
3. 当日总消耗：本地时区**今日 0 点（epoch 秒）起**的全量 tokens 之和
4. 统计范围：**全部工具**（不按 app_type 过滤）
5. 柱状图：最近 **60 秒**窗口，**新柱从左侧进入、整体右移**（最新在最左）
6. 浮窗：PySide6，`FramelessWindowHint | WindowStaysOnTopHint | Tool` + `WA_TranslucentBackground`，可拖动
7. 扩展现有 token-monitor 还是新建？——**新建独立项目 token-running**（本仓库），借鉴 token-monitor 代码（窗口模式、`_format_compact_int` 等）
8. MVP **不做**：UI 美化、设置页、JSONL 直读、打包（exe）、成本显示

## 5. 实施计划

**按 [docs/superpowers/plans/2026-08-17-token-running-mvp.md](docs/superpowers/plans/2026-08-17-token-running-mvp.md) 逐任务执行**，该计划含全部代码、命令与预期输出，采用 TDD（测试先行）：

1. **Task 1**：写 `tests/test_source.py`（5 个用例：增量轮询、含缓存全量求和、当日从 0 点起、跨天重置、库缺失离线）+ `requirements.txt` + 包骨架 → 运行测试确认红
2. **Task 2**：实现 `src/token_running/source.py`（`TokenSource` 类）→ 测试转绿 → 真实库冒烟
3. **Task 3**：实现 `src/token_running/realtime_ui.py`（`RealtimeWindow`）+ `main.py` → 冒烟运行 + 测试不回归
4. **Task 4**：端到端验证（见 §7）

**执行纪律**：每任务结束 commit（提交信息模板见计划）；不修改已提交的设计/计划文档（验证记录追加除外）。

## 6. 环境（已确认可用）

- Windows 11，Shell 为 Git Bash；Python 3.12.10（`python` 命令可用）
- **PySide6 6.11.0 已装、pytest 9.0.2 已装**，无需安装
- 测试运行：在项目根执行 `python -m pytest tests/ -v`
- 注意：Windows 下 `python` 直接可用；无需虚拟环境（系统环境已具备依赖）

## 7. 验收标准（Task 4 实测）

1. `python main.py` 启动浮窗（后台运行）
2. 在 Claude Code 中发起一轮对话（用户配合）
3. 消息完成后 **2–7 秒内**，柱状图**左侧出现新柱**、旧柱右移
4. 「今日 tokens」数字随之增长；状态点为绿（cc-switch 在线）
5. 空闲 30 秒柱状图静止、不崩溃；拖动浮窗正常
6. 将验证结果（日期、通过与否、实际延迟秒数）追加到计划文档末尾

## 8. 已知坑位

- `sqlite3.connect()` 对不存在的路径会**创建空文件**而不报错——`TokenSource._init()` 必须依赖后续查询（no such table）抛错来判定离线，或先校验表存在（计划 Task 2 Step 2 有说明）
- cc-switch 程序**必须保持运行**，数据才有更新；未运行时 UI 显示「离线」状态点（红），属预期行为
- 计划中 Task 1 的 `test_daily_total_resets_across_midnight` 已改为「同一 source 跨天」正确写法，以计划文件内为准
- 提交时 `git` 在 Windows 会有 LF→CRLF 警告，忽略即可

## 9. 完成判定

以下全部满足即为完成：

- [x] `python -m pytest tests/ -v` → 5 passed（实际 29 passed，含 4 个审查修复 + 1 个北京时区测试）
- [x] `python main.py` 浮窗正常显示，柱状图左进右移
- [x] 真机验证：对话后 2–7 秒新柱出现、今日数字增长
- [x] 4 个任务均有 commit，工作区干净（`git status` 无未提交改动）
- [x] 验证结果已记录在计划文档末尾

## 10. 数据源演进（2026-08-18 追加）

- **Codex / Opencode 改为直读原始日志**，不再依赖 cc-switch DB：
  - `CodexSource`（src/token_running/codex_source.py）：增量解析 `~/.codex/sessions/**/rollout-*.jsonl` 的 `token_count` 事件，用 `last_token_usage`（已验证为增量：文件内差分一致）；模型取自 `session_meta.payload.base_instructions.provenance.model` / `turn_context.payload.model`，按文件隔离
  - `OpencodeSource`（src/token_running/opencode_source.py）：rowid 增量拉 `~/.local/share/opencode/opencode.db` 的 message 表，解析 data JSON 的 `tokens{total,input,output,reasoning,cache{read,write}}`；`total = input+output+reasoning+cache.read+cache.write`（已验证），reasoning 计入总量、费用按 input/output/cache_read 计
  - UI `_channels`：`"codex": CodexSource()`、`"opencode": OpencodeSource()`
  - 真机冒烟：Codex 全盘 598 个 rollout 解析 133k 事件、offset 推进与独立解析零 mismatch（无重复）；Opencode 总量 383,404 与 cc-switch 完全一致
  - 测试：`tests/test_codex_source.py`（增量/新文件/半行/模型隔离）+ `tests/test_opencode_source.py`（增量/breakdown/跨天/离线），全套 **35 passed**

## 11. Mac 兼容（2026-08-17 追加）


- **时区统一**：新增 `beijing_day_start()` / `beijing_today()`（`src/token_running/source.py`），"今日 0 点"与峰谷定价一致按北京 UTC+8 计算；`TokenSource` / `JsonlSource` / `DeepseekTraceSource` / `RealtimeWindow._today_start_epoch` 全部改用该口径（提交 `ba51b66`）。跨时区机器（Mac 非北京时间）行为一致。
- **平台专属代码**：仅 `realtime_ui.py` 的 `showEvent` 内有 `ctypes.windll.dwmapi`（去 DWM 阴影），import 在 try/except 内、运行时才触发，Mac 上自动跳过——模块导入安全（已冒烟验证 `IMPORT_OK`）。
- **启动器**：`start.command`（Mac 双击运行，git 可执行位已设；首次自动 `pip install -r requirements.txt`）。
- **打包**：README 新增「Mac 运行与打包」章节。PyInstaller **不支持跨平台交叉编译**，`.app` 必须在真 Mac 上构建；Docker 无法运行 macOS（Apple EULA、无官方镜像），黑苹果/OSX-KVM 属灰色地带，不采用。
- 测试：`python -m pytest tests/ -v` → **29 passed**。

