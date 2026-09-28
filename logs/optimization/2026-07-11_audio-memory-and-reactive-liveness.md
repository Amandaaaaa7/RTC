# 优化记录：音频播放内存（OOM）+ 反应式语音活性

**日期**：2026-07-11
**设备**：Pi 2 (192.168.31.188) · Raspberry Pi Zero 2W · 415MB RAM
**分支**：`voice/pi-minions-running`
**相关提交**：`8db7324`、`3de7ca6`、`3483ccd`

---

## 背景

更新音频库（`audio_assets/vo/generated_doubao/` + `sing/` 共 176 个文件）后，发现两类问题：

1. 播放长歌曲后程序"卡死"——不再出声
2. 反应式语音偶发卡死在"开始监听"状态

排查后发现是两个**独立**问题，分别对应**内存（OOM）**和**活性（僵死状态）**。

---

## 问题一：OOM Killer 杀进程（内存优化）

### 现象

程序"卡死"，但眼睛线程仍在动。`ps` 发现 python3 进程已消失，日志冻结，最后一行是 `[SPK] 播放: xxx.wav` 而**没有** `[SPK] 播放完成`。

### 证据（dmesg）

```
Out of memory: Killed process 1761 (python3) total-vm:1539784kB,
anon-rss:92076kB, UID:1000
```

进程虚拟内存涨到 1.5GB，在歌曲播放时被内核 OOM 杀掉。

### 根因

旧 `play_wav` 把整首 wav 一次性读入 numpy，做"读取 → 重采样 → int32 → 立体声 → tobytes"多重转换，且 `play_buffer` 再做一次 float64 回转。实测峰值：

```
播放 special_memo/detune_perfect_v2_cat.wav (3.5MB)
优化前峰值内存 delta = 130484 KB  （≈ 文件大小的 37 倍）
```

峰值大头在 `_resample`：`np.linspace` 生成两个全长度 float64 坐标数组（14MB + 21MB），叠加输入/输出数组共 ~70MB。

### 优化方案

`play_wav` 改用 `ffmpeg | aplay` 流式管道（设备上 `random_audio_player.py` 已验证的方式）：

```
ffmpeg -i file.wav -af volume=0.3 -f wav -ar 48000 -ac 2 -c:a pcm_s32le - | aplay -D hw:1,0 -
```

同时把 ALSA 写入抽成公共函数 `_write_alsa()` 供 `play_buffer` 复用。

### 效果（实测）

| 指标 | 优化前 | 第一次优化(numpy 去重) | 最终(ffmpeg 流式) |
|------|--------|----------------------|-------------------|
| 播放峰值内存 delta | 130 MB | 78 MB | **0.25 MB** |
| 内存与文件大小关系 | 37× 文件大小 | ~22× | **恒定 ~10MB** |

- 连续多首歌曲 `播放完成`，main.py RSS 稳定 97MB 无峰值
- dmesg 无新 OOM，无 ffmpeg/aplay 残留进程
- 再大的歌曲也不会 OOM（内存与文件大小彻底解耦）

---

## 问题二：反应式语音卡死（活性优化）

### 现象

唱完歌后不再出声，但 15s 定时播放仍工作。用户反馈"一直卡在开始监听，没法通过说话触发"。

### 证据

- 噪声底线实测：`min=0.036 median=0.0413 max=0.047`，**中位数已高于固定阈值 0.04**
- 日志：歌曲后 `开始监听` 1 次，`触发播放` 0 次

### 根因

固定阈值 0.04 时，环境噪声漂移到 0.041 后，超过一半的帧都压线，`_wait_for_silence` 永远凑不满 0.8s 静音 → 卡死。而 15s 空闲自动播放的检查只存在于 `_wait_for_voice`，`_wait_for_silence` **没有超时逃逸机制**。

### 优化方案（两项）

1. **静音等待逃逸**：`_wait_for_silence` 也加 15s 空闲计时，卡住超时就主动触发播放并退回等待状态
2. **自适应噪声底线**：EMA 跟踪环境噪声（时间常数 ~10s），动态计算阈值
   - 语音阈值 `vth = max(配置下限, floor + 0.03)`
   - 静音阈值 `= max(配置下限×0.5, floor + 0.01)`
   - 用全部帧更新底线但限制单帧上限，突发语音不抬高基线，环境噪声缓升可被跟踪

### 效果（实测）

```
floor 收敛到 0.0405（与实测噪声一致），vth = 0.0705
静音阈值 0.0505 > 噪声峰值 0.047 → 静音可凑满 ✅
```

播放序列恢复正常（语音↔歌曲交替）：

```
030_boredom_long → 020_triumph_mid → 🎵detune_v2_perfect_cat →
015_sympathy_long → 073_adoration_short → 🎵detune_offkey_v2_cat → ...
```

---

## 附：歌曲池机制（功能，非性能）

- `preset.py` 递归扫描 `sing/` 为歌曲池（21 首），新增 `play_random_song()`
- `reactive_module.py` 每 60s 的下一次随机播放从歌曲池抽取，其余从语音池抽取
- 歌曲不独立开线程，并入现有随机播放，避免额外线程开销

---

## 遗留观察项

1. **系统内存偏紧**：swap 长期占用 ~220MB，可用 RAM 仅 ~130MB。若再遇 OOM，可关桌面 GUI（wf-panel-pi/wayfire）或加大 swap。
2. **onomatotts 10 个文件**未入随机池（用户决定暂不管）。
3. Pi 1 如需同步：`git pull origin voice/pi-minions-running`。

---

## 关键命令速查

```bash
# 查看 OOM 记录
dmesg | grep -iE 'oom|out of memory'

# 测量播放峰值内存
python3 -c "import resource; from speaker import play_wav; \
p0=resource.getrusage(2).ru_maxrss; \
play_wav('歌曲路径', volume=0.3, blocking=True); \
p1=resource.getrusage(2).ru_maxrss; print(f'delta={p1-p0}KB')"

# 监控进程内存轨迹
watch -n 5 "ps -o pid,rss,cmd --sort=-rss | head -5"

# 安全重启（避免 pkill 自杀：用 main[.]py 正则技巧）
ssh pi2@192.168.31.188 "pkill -f 'main[.]py'; sleep 2; \
cd /home/pi2/pi_affe_sys && setsid nohup python3 -u main.py \
--dual-eye --face --reactive-voice --reactive-threshold 0.04 \
--eye-debug-overlay >> logs/doll-robot.log 2>&1 < /dev/null &"
```
