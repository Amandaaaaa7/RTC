# 优化记录：眼睛不眨眼（帧抖动下眨眼可靠性）

**日期**：2026-07-11 诊断 → 2026-07-12 部署
**设备**：Pi 2 (192.168.31.188) · Raspberry Pi Zero 2W
**分支**：`voice/pi-minions-running`
**相关提交**：`e11500e`（眨眼修复）、部署后 `e46ed3c`

---

## 背景

`doll-eye-styles` 仓库里的眼睛预览（GIF 生成器）能正常眨眼，但 Pi 2 生产环境的眼睛**看起来不眨眼，只是静态样式**。

## 现象

- 实体屏幕眼睛静止，观察不到眨眼动作
- 样式（`p2_linlin`）能正常显示，说明渲染管线通

## 排查过程

1. **渲染层验证**：本地 `draw_eye(eyelid=0)` vs `draw_eye(eyelid=120)` 像素差 550 万 → 渲染层正常响应眼睑 ✅
2. **眨眼周期渲染**：本地渲染睁开→闭合→睁开帧序列，眨眼动画完整 ✅
3. **代码版本对比**：Pi 2 跑的是旧代码 `3483ccd`，`BLINK_HOLD_MS = 32`；HEAD `e11500e` 已修复为 `90`
4. **状态机对比**：新旧两版眨眼状态机都存在，唯一差别是 HOLD 值

## 根因

**不是功能缺失，是帧抖动导致眨眼"闭不全"。**

- 眨眼全闭保持时间 `BLINK_HOLD_MS` 旧值仅 **32ms**
- 25fps 理论帧间隔 40ms，但 Pi Zero 2W 同时跑摄像头+人脸检测+双 SPI 屏，**实际帧间隔漂移到 60~120ms**
- 全闭窗口 `[120ms, 120+HOLD)` 宽仅 32ms < 帧间隔 → 帧经常"跳过"全闭那一帧

### 仿真验证（5000 次眨眼，含帧抖动）

| 实际帧间隔 | HOLD=32（旧）能渲染全闭 | HOLD=90（新）能渲染全闭 |
|-----------|------------------------|------------------------|
| 40-60ms   | 70%                    | 100%                   |
| 40-80ms   | **53%**                | 95%                    |
| 40-120ms  | **48%**                | 82%                    |

旧代码重负载下约一半眨眼闭不全 → 眼睛只眯一下就睁开，看起来像"基本不眨眼"。

## 修复方案

`BLINK_HOLD_MS` 32ms → **90ms**（> 帧间隔 40ms，保证至少 1~2 帧渲染到全闭状态）。该修复由用户在 `e11500e` 完成，本次仅负责**部署到 Pi 2**。

## 部署步骤

```bash
# Pi 2 上 main.py / reactive_module.py 是之前 scp 的（内容已在远端），
# 先丢弃本地"改动"再 fast-forward 拉取
git checkout -- main.py voice/reactive_module.py
git pull --ff-only origin voice/pi-minions-running   # → e46ed3c
sudo systemctl restart doll-robot.service
```

## 效果

- `BLINK_HOLD_MS = 90` 已生效，systemd `active`
- 眨眼可靠性从 ~48-70% → 82-100%
- 顺带同步了渲染层（eye_render.py 441 行更新）、样式库、表情动画系统

## 遗留观察项

1. 眨眼间隔 `BLINK_INTERVAL_MIN/MAX = 2.0~3.0s`，如觉得不够明显可调小
2. `DOUBLE_BLINK_CHANCE = 0.08`（8% 概率双眨）
3. Pi 1 如需同款修复：`git pull` + 重启其 systemd 服务
