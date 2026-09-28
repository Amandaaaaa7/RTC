#!/bin/bash
# enable_camera.sh — 启用 Pi Camera
#
# 在 Pi Zero 2W 上启用摄像头接口。
# CSI 摄像头与 I2S 麦克风无引脚冲突。

set -e

echo "=== 启用 Pi Camera ==="

# Raspberry Pi OS Bookworm 使用 libcamera
# 摄像头通过 raspi-config 或 config.txt 启用

CONFIG_FILE="/boot/firmware/config.txt"

if grep -q "camera_auto_detect=1" "$CONFIG_FILE" 2>/dev/null; then
    echo "[OK] 摄像头已启用 (camera_auto_detect=1)"
else
    echo "[INFO] 添加 camera_auto_detect=1 到 $CONFIG_FILE"
    echo "camera_auto_detect=1" | sudo tee -a "$CONFIG_FILE"
fi

# 安装 rpicam-apps
echo "[INFO] 安装 rpicam-apps..."
sudo apt install -y rpicam-apps

echo ""
echo "=== 完成 ==="
echo "重启生效: sudo reboot"
echo ""
echo "重启后验证:"
echo "  rpicam-vid --list  # 应检测到摄像头"
echo "  libcamera-hello    # 预览测试 (需显示器)"
