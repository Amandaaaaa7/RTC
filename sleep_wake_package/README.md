# sleep → wake 动图包

打包内容（可直接 zip 后发给同事）：

```
sleep_wake_package/
├── README.md                   # 本文件
├── gifs/
│   ├── sleep/                  # 40 sleep 动画 GIF + sleep_index.html
│   └── wake/                   # 40 wake 动画 GIF
├── scripts/                    # 生成器脚本
│   ├── sleep_gifs_cry.py       # 睡眠动画生成器
│   ├── wake_gifs_cry.py        # 唤醒动画生成器
│   └── reverse_gif.py          # 把 sleep GIF 反向帧成 wake GIF 的小工具
└── runtime/                    # 脚本依赖的渲染底层
    ├── eye_render.py
    ├── eye_styles.py
    └── eye_lid.py
```

## 一、只看不生成

直接打开 `gifs/sleep/sleep_index.html` 用浏览器看 sleep 全部 40 张动图。
wake 这边没有 HTML 索引，但 40 个 GIF 都在 `gifs/wake/`，每个文件单独拖进浏览器都能看。

## 二、重新生成 GIF

需要 Python 3.10+ 和 Pillow。

```bash
# 1) 安装依赖
pip install pillow

# 2) 让 scripts/ 能找到 runtime/ 的渲染底层
#    把 scripts/ 和 runtime/ 放在同一目录下根根（保持现在的结构即可）

# 3) 生成 sleep
python scripts/sleep_gifs_cry.py --output-dir gifs/sleep

# 4) 生成 wake（两种方法）
#    方法 A：独立渲染（脚本里默认使用 SLEEP 轨迹）
python scripts/wake_gifs_cry.py --output-dir gifs/wake

#    方法 B：反向 sleep 帧（保持源 sleep 的 duration）
python scripts/reverse_gif.py \
    --src gifs/sleep \
    --dst gifs/wake \
    --suffix _wake \
    --last-pause-ms 0
```

## 三、动画时轴

**sleep**（20s / 500 帧）：

| 段时间 | target_y 轨迹 | apex_y 轨迹 |
|---|---|---|
| 0-2s   SOFTEN    | 117 → 84   | 107 → 77 |
| 2-7s   Blink 1   | 84 → 100 → 92（不回全） | 77 → 91.67 → 84.33 |
| 7-13s  Blink 2   | 92 → 115 → 110（回得更少） | 84.33 → 105.42 → 100.83 |
| 13-14s HOLD      | 110（不再回 soft-close）| 100.83 |
| 14-19s TRANSITION | 110 → 131 | 100.83 → 120 |
| 19-20s FULLY_CLOSED | 131     | 120 |

**wake** = sleep 的反向帧（起点 FULLY_CLOSED → 终点 OPEN），时长与 sleep 完全一致（20s）。

## 四、文件名约定

- `<style>_sleep.gif`     单眼 sleep
- `<style>_sleep_dual.gif` 双眼 sleep
- `<style>_wake.gif`      单眼 wake（同名 style，去掉 `_sleep`，加 `_wake`）
- `<style>_sleep_dual_wake.gif` 双眼 wake

## 依赖（Pillow）

```bash
pip install pillow
```

运行时模块不依赖任何外部资源。
