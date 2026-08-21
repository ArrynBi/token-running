# token-running 人物动画 + 开发者模式 交接文档（Handoff）

> 本分支（`feature/running-animation`）交付给 **DeepSeek harness** 继续执行或审查。
> 交接日期：2026-08-20
>
> 分支基线：`main` 上已完成核心监测功能（数据源/浮窗/费用/时区统一），本分支在其上新增**人物动画**与**开发者模式**。

---

## 1. 任务一句话

给秒级 token 消耗浮窗加一个**像素小人动画**：随 token 消耗速率呈现 站立/慢走/快走/慢跑/快跑，跑久骤停会喘粗气、过一会儿恢复；另提供**开发者模式**（嵌入完整使用窗 + 模拟速率滑杆）用于调参测试。

## 2. 当前状态（2026-08-20）

- 分支 `feature/running-animation` 已推送 GitHub（`ArrynBi/token-running`），工作区干净
- **52 个测试全部通过**（`python -m pytest tests/ -q`）
- 正式悬浮窗与开发者模式均在本机运行中（pythonw 进程）
- 最近提交（新→旧）：
  - `6397333` feat: 模拟器升级为开发者模式——嵌入完整 token 使用窗
  - `4a7ed01` chore: 移除误提交的调试脚本
  - `0737999` fix: 模拟器自包含路径 + 窗口居中 + showEvent 同步小人
  - `7f56b81` feat: Runner 动画模拟器（sim_runner.py）
  - `7261090` tweak: 加快速度过渡（SMOOTH 0.3 -> 0.6）
  - `968f540` fix: 动画卡顿 + 速度过渡平滑
  - `0146332` fix: Runner 去除白边 + 空闲不消失
  - `3463804` feat: 人物跑步动画（首个功能提交）

## 3. 新增文件

| 文件 | 说明 |
|---|---|
| `src/token_running/runner_anim.py` | **RunnerWindow**：独立透明置顶无边框浮窗，多帧动画播放 + 速度→动作状态机 + 喘气 |
| `src/token_running/realtime_ui.py`（改动） | `RealtimeWindow` 增加 `embedded` 参数；`_feed_runner` 每秒喂速率；`moveEvent`/`showEvent` 同步小人位置；右键菜单加"人物动画"开关 |
| `assets/runner/{idle,run,jump}/*.png` | 素材：OpenGameArt CC0 "Runner character"（idle 5 帧 / run 8 帧 / jump 4 帧，32×32），裁剪为独立 PNG |
| `assets/RUNNER_ATTRIBUTION.txt` | CC0 署名文件（作者 David Harrington / Blue Yeti Studios，无需强制署名） |
| `dev_mode.py` | **开发者模式**：嵌入完整 RealtimeWindow + `SimSource` 模拟数据源 + 滑杆/按钮 + 小人 |
| `tests/test_runner_anim.py` | 动画逻辑测试（9 个：速度映射/平滑/喘气/开关/帧加载） |
| `tests/test_dev_mode.py` | 开发者模式测试（8 个：SimSource/滑杆驱动/图表联动/嵌入模式） |
| `HANDOFF_ANIMATION.md` | 本文档 |

## 4. 设计决策（用户拍板，勿更改）

1. **素材路线 A+B 结合**：先用现成 CC0 素材跑通（已实现），ComfyUI 生图作为后续升级（本地 ComfyUI 已装但模型目录全空，需下载 base 模型 + 配置 workflow；已找到现成 pipeline [mor-o/comfyui-2d-character-pipeline](https://github.com/mor-o/comfyui-2d-character-pipeline)）
2. **风格**：像素风跑步小人
3. **显示位置**：独立浮窗贴在主窗**下方、左对齐**（不是叠加在窗口内）；开发者模式下同样贴其窗口下方
4. **快慢 = 播放帧率**：walk/run 共用一组帧，快慢用 FPS 区分，不额外出图
5. **喘气 = 程序化**：idle 帧 + 正弦呼吸位移（大幅高频 6 秒），不额外生图
6. **过渡平滑**：速度做指数平滑（`SMOOTH=0.6`，约 1 秒到位），速率突变时动作渐进变化；帧率映射分段连续（walk 2→8、run 8→24 FPS 无缝衔接），idle 固定最低帧率避免回弹

## 5. 关键技术细节

### 5.1 RunnerWindow（runner_anim.py）

- **常量**（文件顶部，调参直接改这里）：
  - `SPEED_IDLE=1`、`SPEED_WALK=400`、`SPEED_RUN=2000`（t/s 档位阈值）
  - `FPS_MIN=2`、`WALK_FPS_MAX=8`、`RUN_FPS_MAX=16`、`FPS_MAX=24`
  - `SMOOTH=0.6`（指数平滑系数）
  - `BREATH_SECS=6.0`、`BREATH_AMPL=6`、`BREATH_RATE=3.0`、`IDLE_BREATH_AMPL=2`
- **状态机**：`set_speed(tps)` 每秒被调用 → 指数平滑 → 按档位设 `_action`/`_fps`；运动骤停（`was_active and not active`）触发喘气计时
- **播放**：20Hz QTimer → `_advance` 用**真实流逝时间**累积相位（`_phase += now - _last_tick`），`while _phase >= interval` 换帧并保留余量（不丢帧、事件循环忙不卡顿）
- **绘制**：`paintEvent` 画当前帧；idle 时叠加正弦呼吸位移 `dy = amp*(0.5-0.5*cos(2π*rate*now))`
- **窗口**：`FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowTransparentForInput`（不挡鼠标）；`showEvent` 里 DWM 去阴影/圆角（Windows 专用，try/except，Mac 安全）
- **空闲不消失**：无 token 时保持显示 idle 动画（用户明确要求），只有菜单开关关闭才隐藏

### 5.2 与主窗集成（realtime_ui.py）

- `__init__`：`self._runner = RunnerWindow()`（`embedded=False` 时）
- `_feed_runner(now)`：汇总启用渠道最近 5 秒 token 总量 / 5 → `_runner.set_speed()`
- `_sync_runner_geometry()`：`runner.move(g.left(), g.bottom())` 贴下方左对齐；`moveEvent`/`showEvent`/`_refresh_height` 均调用
- 右键菜单：`runner_act`（人物动画）可勾选开关
- **`embedded=True` 模式**：开发者模式使用——不设窗口 flags（非顶层）、不自动建 runner（宿主管理）、`_sync_runner_geometry`/`_toggle_runner` 用 `getattr` 防 None

### 5.3 开发者模式（dev_mode.py）

- `SimSource`：`poll()` 按滑杆速率每秒产出 `UsageEvent`（model=deepseek-v4-flash，input/output 拆半），维护 `daily_total()/total()/online()/set_windows()`
- `DevModeWindow`：`RealtimeWindow(source=SimSource, embedded=True)` 嵌入顶部（图表/今日/总量/费用/右键菜单全功能），下方滑杆（0-8000 t/s）+ 快捷按钮（站立/慢走/快走/慢跑/快跑）+ 档位标签；100ms QTimer 同时喂小人 `set_speed`
- 自包含：脚本内 `sys.path.insert(0, src)`，任意目录直接 `python dev_mode.py` 运行
- 首次显示居中屏幕；`moveEvent`/`showEvent` 同步小人贴窗下方

## 6. 已踩坑（勿重蹈）

- **PySide6 `_phase` 清零 bug**：原实现固定步长 + 换帧清零，事件循环忙时节奏漂移卡顿 → 改真实时间差累积 + 保留余量
- **帧率跳变**：原映射在 2000 t/s 处 walk(24fps)→run(2fps) 断崖 → 分段连续化
- **idle 帧率回弹**：速度降入 idle 时 fps 从 2 跳到 3/6 → 固定 idle=FPS_MIN，喘气剧烈感由呼吸位移承担
- **窗口白边**：Tool 窗口在 Windows 有 DWM 阴影/圆角 → showEvent `DwmSetWindowAttribute`（与主窗相同方案）
- **模拟器窗口"消失"**：残留进程把窗口移到屏幕外 → 首次显示居中 + 杀掉残留进程重启
- **DevModeWindow 初始化顺序**：SimSource 的 rate_fn 引用 `_slider`，必须先建 slider 再建 sim/chart（否则 AttributeError）
- **误提交调试脚本**：`_dbg_sim.py` 误提交后被 `git rm` 移除（commit `4a7ed01`）——注意清理临时脚本

## 7. 运行方式

```bash
# 正式悬浮窗
python main.py            # （经 start.cmd 设 PYTHONPATH=src）

# 开发者模式（调参测试，自包含路径）
python dev_mode.py

# 测试
python -m pytest tests/ -q    # 52 passed
```

## 8. 后续可做（未拍板）

- **ComfyUI 生成自定义角色**：下载 SD base 模型（2-7GB）+ 配置 mor-o pipeline，替换/补充像素小人（A+B 路线第二阶段）
- **正式悬浮窗加"开发者模式"入口**：右键菜单一项直接启动 dev_mode.py（已向用户提议，未确认）
- 更多动作素材（若找到 8 帧 walk 循环可补真实走路动画，当前 walk 复用 run 帧降帧率）
- 喘气效果微调（时长/幅度/频率）——直接改 runner_anim.py 顶部常量

## 9. 完成判定

- [x] `python -m pytest tests/ -q` → 52 passed
- [x] 正式悬浮窗：小人贴窗下方左对齐、空闲站立不消失、随速率 站立/走/跑/喘气
- [x] 开发者模式：完整使用窗 + 滑杆驱动图表与小人
- [x] 分支已推送，工作区干净
