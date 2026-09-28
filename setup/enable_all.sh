#!/bin/bash
# enable_all.sh — Doll Robot 一键环境配置
#
# 配置 Pi Zero 2W 的全部外设:
#   - I2S 麦克风 (INMP441) via googlevoicehat-soundcard
#   - Pi Camera (CSI)
# 注意: 屏幕 SPI 配置在 Phase 2 添加

set -e

echo "========================================"
echo "  Doll Robot - 环境配置"
echo "========================================"

# 确保在正确的目录运行
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# 安装系统依赖
echo ""
echo ">>> 安装系统依赖..."
sudo apt update
sudo apt install -y \
    python3-numpy python3-alsaaudio python3-pil \
    python3-venv rpicam-apps

# 配置 I2S 麦克风
echo ""
echo ">>> 配置 I2S 麦克风..."
bash "$SCRIPT_DIR/enable_i2s.sh"

# 配置摄像头
echo ""
echo ">>> 配置摄像头..."
bash "$SCRIPT_DIR/enable_camera.sh"

echo ""
echo "========================================"
echo "  配置完成! 请重启: sudo reboot"
echo "========================================"
