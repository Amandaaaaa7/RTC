# 性能优化记录

本文件夹记录项目的性能优化过程与实测效果。每次优化一个文件，按日期命名。

## 命名规范

`YYYY-MM-DD_<主题>.md`

## 记录内容模板

- **背景**：触发优化的场景
- **现象**：可观测的问题表现
- **证据**：日志 / dmesg / 实测数据
- **根因**：为什么会这样
- **优化方案**：怎么改的
- **效果**：优化前后对比（尽量给数字）
- **遗留观察项**

## 索引

| 日期 | 文件 | 主题 | 核心效果 |
|------|------|------|----------|
| 2026-07-11 | [audio-memory-and-reactive-liveness](2026-07-11_audio-memory-and-reactive-liveness.md) | 歌曲播放 OOM + 反应式语音卡死 | 播放峰值内存 130MB → 0.25MB（~500×）；消除两类僵死状态 |
| 2026-07-12 | [eye-blink-frame-jitter](2026-07-12_eye-blink-frame-jitter.md) | 眼睛不眨眼（帧抖动下眨眼可靠性） | HOLD 32→90ms，全闭渲染成功率 ~48% → 82-100% |

## 常用排查命令

```bash
dmesg | grep -iE 'oom|out of memory'   # OOM 记录
free -m                                 # 内存/swap
ps -eo pid,rss,cmd --sort=-rss | head  # 按内存排序进程
```
