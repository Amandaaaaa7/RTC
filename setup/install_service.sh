#!/bin/bash
# 安装并启用 Doll Robot 开机自启 systemd 服务
# 用法: sudo bash setup/install_service.sh [--style STYLE]

set -e

SERVICE_NAME=doll-robot.service
# 自动检测项目目录（脚本位于 setup/ 下）
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"

# 运行用户优先使用当前目录所有者，回退到当前用户
RUN_USER="$(stat -c '%U' "${PROJECT_DIR}" 2>/dev/null || id -un)"
RUN_GROUP="$(stat -c '%G' "${PROJECT_DIR}" 2>/dev/null || id -gn)"

# 解析参数
EYE_STYLE=""
RANDOM_AUDIO=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --style)
            EYE_STYLE="$2"
            shift 2
            ;;
        --random-audio)
            RANDOM_AUDIO="--random-audio"
            shift
            ;;
        *)
            echo "[INSTALL] 未知参数: $1" >&2
            exit 1
            ;;
    esac
done

# 构造启动参数
EXEC_ARGS="--dual-eye --face --reactive-voice"
if [[ -n "${EYE_STYLE}" ]]; then
    EXEC_ARGS="${EXEC_ARGS} --eye-style ${EYE_STYLE}"
fi
if [[ -n "${RANDOM_AUDIO}" ]]; then
    EXEC_ARGS="${EXEC_ARGS} --random-audio"
fi

# 确保日志目录存在并归运行用户所有
mkdir -p "${PROJECT_DIR}/logs"
chown -R "${RUN_USER}:${RUN_GROUP}" "${PROJECT_DIR}/logs"

# 生成 service 文件
sudo tee "${SERVICE_DST}" > /dev/null <<EOF
[Unit]
Description=Doll Robot - voice/minimal-loop
After=network.target sound.target
Wants=network.target sound.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${PROJECT_DIR}
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/bin/python3 -u main.py ${EXEC_ARGS}
ExecStop=/bin/kill -INT \$MAINPID
TimeoutStopSec=10
Restart=on-failure
RestartSec=5
StandardOutput=append:${PROJECT_DIR}/logs/doll-robot.log
StandardError=append:${PROJECT_DIR}/logs/doll-robot.log

[Install]
WantedBy=multi-user.target
EOF

sudo chmod 644 "${SERVICE_DST}"

echo "[INSTALL] 项目目录: ${PROJECT_DIR}"
echo "[INSTALL] 运行用户: ${RUN_USER}"
echo "[INSTALL] 启动参数: ${EXEC_ARGS}"
echo "[INSTALL] 已写入 ${SERVICE_DST}"
echo "[INSTALL] 重新加载 systemd..."
sudo systemctl daemon-reload

echo "[INSTALL] 启用开机自启..."
sudo systemctl enable "${SERVICE_NAME}"

echo "[INSTALL] 完成。"
echo "[INSTALL] 手动启动: sudo systemctl start ${SERVICE_NAME}"
echo "[INSTALL] 查看状态: sudo systemctl status ${SERVICE_NAME}"
echo "[INSTALL] 查看日志: tail -f ${PROJECT_DIR}/logs/doll-robot.log"
