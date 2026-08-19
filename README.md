# token-running

秒级动态浮窗 token 消耗监测 —— 实时查看 DeepSeek Harness / Claude Code / Codex / Opencode 的 token 用量。

## 功能

- **实时柱状图**：每秒/5秒/分钟级窗口，新柱左进右移
- **多渠道合并**：DSH（Harness 轨迹）、Claude Code（JSONL）、Codex（CLI rollout）、Opencode（本地 DB）——全部直读原始日志，秒级可见
- **分渠道多图**：上下排列各渠道独立柱状图
- **统计口径可选**：今日（自然日/近24h）、总量（全时段/近7/30/90天）
- **人物动画**：独立小窗贴在主窗下方左对齐，随 token 速率呈现 站立/慢走/快走/慢跑/快跑/喘气（跑久骤停会喘粗气，数秒恢复）——右键菜单可开关
- 透明置顶无边框浮窗，可拖动；右键菜单控制显示

## 运行

```bash
pip install -r requirements.txt
python main.py
```

数据源（自动发现，无需配置）：
- `~/.dsh/sessions/**/session*.jsonl.zstd` —— DeepSeek Harness 轨迹
- `~/.claude/projects/**/*.jsonl` —— Claude Code 会话
- `~/.codex/sessions/**/rollout-*.jsonl` —— Codex CLI 原始会话日志（token_count 事件，秒级增量）
- `~/.local/share/opencode/opencode.db` —— Opencode 本地库 message 表（逐条 usage，rowid 增量）

## 打包 exe（Windows）

```bash
python -m PyInstaller --onefile --windowed --name token-running --paths src main.py
```

产物在 `dist/token-running.exe`（约 50MB，含 PySide6）。

## Mac 运行与打包

源码已做跨平台兼容（时区统一按北京时间 UTC+8 计算“今日”与峰谷定价；DWM 去阴影仅 Windows 生效、其他平台自动跳过）。

**直接运行**（需要 Python 3.10+）：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

或双击仓库根目录的 `start.command`（首次会自动装依赖）。若提示“无法打开，因为来自身份不明的开发者”，右键 → 打开 一次即可。

**打包 .app**（必须在 Mac 上执行；PyInstaller 不支持跨平台交叉编译）：

```bash
source venv/bin/activate
python -m PyInstaller --onefile --windowed --name token-running --paths src main.py
# 产物 dist/token-running.app（或单文件二进制）
```

> 注意：Docker 无法运行 macOS 应用（Apple EULA 禁止、无官方 macOS 镜像），黑苹果/OSX-KVM 属灰色地带，.app 的构建与验证需要一台真 Mac（本机或云 Mac 如 MacStadium）。

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
- 依赖 Codex CLI / Opencode / Claude Code / DSH 生成相应数据文件（cc-switch 不再是必需项）
