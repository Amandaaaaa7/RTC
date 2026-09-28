#!/usr/bin/env python3
"""随机循环播放 audio_assets/vo 下的 MP3 文件，每 3 秒一次。"""
import os
import random
import subprocess
import time
import glob
import signal
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, "audio_assets", "vo")
DEVICE = "hw:1,0"
INTERVAL = 3.0


def find_files():
    pattern = os.path.join(AUDIO_DIR, "**", "*.mp3")
    files = sorted(glob.glob(pattern, recursive=True))
    if not files:
        raise RuntimeError(f"在 {AUDIO_DIR} 下未找到 .mp3 文件")
    return files


def play_file(path):
    # 解码为 I2S 声卡支持的 S32_LE 格式，再经 aplay 输出
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", path,
        "-af", "volume=0.3",
        "-f", "wav", "-ar", "48000", "-ac", "2",
        "-c:a", "pcm_s32le", "-"
    ]
    aplay_cmd = ["aplay", "-D", DEVICE, "-"]
    ffmpeg_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    aplay_proc = subprocess.Popen(aplay_cmd, stdin=ffmpeg_proc.stdout)
    ffmpeg_proc.stdout.close()
    aplay_proc.wait()
    ffmpeg_proc.wait()
    return aplay_proc.returncode


def main():
    files = find_files()
    print(f"找到 {len(files)} 个音频文件，每 {INTERVAL}s 随机播放一个，设备 {DEVICE}")
    print("按 Ctrl+C 停止")

    def stop(signum, frame):
        print("\n收到停止信号，退出")
        sys.exit(0)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    while True:
        f = random.choice(files)
        print(f"[{time.strftime('%H:%M:%S')}] 播放: {f}")
        try:
            play_file(f)
        except Exception as e:
            print(f"播放失败: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
