# token-running

秒级动态浮窗 token 消耗监测 —— 实时查看 DeepSeek Harness / Claude Code / Codex / Opencode 的 token 用量。

## 功能

- **实时柱状图**：每秒/5秒/分钟级窗口，新柱左进右移
- **多渠道合并**：DSH（DeepSeek Harness 轨迹）、Claude Code（JSONL）、Codex / Opencode（cc-switch DB）
- **分渠道多图**：上下排列各渠道独立柱状图
- **统计口径可选**：今日（自然日/近24h）、总量（全时段/近7/30/90天）
- 透明置顶无边框浮窗，可拖动；右键菜单控制显示

## 运行

```bash
pip install -r requirements.txt
python main.py
```

数据源（自动发现，无需配置）：
- `~/.dsh/sessions/**/session.jsonl.zstd` —— DeepSeek Harness 轨迹
- `~/.claude/projects/**/*.jsonl` —— Claude Code 会话
- `~/.cc-switch/cc-switch.db` —— cc-switch（codex / opencode）

## 打包 exe

```bash
python -m PyInstaller --onefile --windowed --name token-running --paths src main.py
```

产物在 `dist/token-running.exe`（约 50MB，含 PySide6）。

## 测试

```bash
python -m pytest tests/ -v
```

## 交互

| 操作 | 行为 |
|---|---|
| 左键双击 | 切换视图粒度（秒级 / 5秒级 / 分钟级） |
| 右键 | 菜单：显示方式、统计口径、渠道多选、退出 |
| 左键按住 | 拖动窗口 |

## 说明

- token 口径 = 含缓存全量（input + output + cache_read + cache_creation / reasoning）
- 各渠道数据源独立维护增量偏移，勾选渠道只控制显示、数据不丢失
- 依赖 cc-switch / Claude Code / DSH 生成相应数据文件
