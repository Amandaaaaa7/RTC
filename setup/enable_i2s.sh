#!/bin/bash
# enable_i2s.sh — 启用 I2S 麦克风 (INMP441)
#
# 在 Pi Zero 2W 上配置 googlevoicehat-soundcard 驱动。
# 此驱动使用 GPIO18(PCM_CLK), GPIO19(PCM_FS), GPIO20(PCM_DIN)。
#
# 重要: 此驱动与 spi1-3cs (GPIO16~21) 冲突。
# 如果之前启用过 spi1-3cs，需要先移除。

set -e

CONFIG_FILE="/boot/firmware/config.txt"

echo "=== 启用 I2S 麦克风 (INMP441) ==="

# 1. 检查是否已启用
if grep -q "googlevoicehat-soundcard" "$CONFIG_FILE" 2>/dev/null; then
    echo "[OK] googlevoicehat-soundcard 已启用"
else
    echo "[INFO] 添加 dtoverlay=googlevoicehat-soundcard 到 $CONFIG_FILE"
    echo "dtoverlay=googlevoicehat-soundcard" | sudo tee -a "$CONFIG_FILE"
fi

# 2. 检查并移除冲突的 spi1-3cs
if grep -q "spi1-3cs" "$CONFIG_FILE" 2>/dev/null; then
    echo "[WARN] 检测到 spi1-3cs (与 I2S 冲突!)"
    echo "  需要手动移除 $CONFIG_FILE 中的 dtoverlay=spi1-3cs"
    echo "  或用 sed 自动移除:"
    echo "    sudo sed -i '/spi1-3cs/d' $CONFIG_FILE"
    echo "  然后 sudo reboot"
fi

# 3. 安装 Python 库
echo "[INFO] 安装依赖..."
sudo apt install -y python3-numpy python3-alsaaudio python3-pil python3-venv

echo ""
echo "=== 完成 ==="
echo "重启生效: sudo reboot"
echo ""
echo "重启后验证:"
echo "  arecord -l  # 应显示 googlevoicehat-soundcard"
echo "  python3 main.py  # 启动集成测试"
