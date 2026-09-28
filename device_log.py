#!/usr/bin/env python3
"""
DeviceLog — 多树莓派运行日志模块

为 Doll Robot 项目提供按设备区分的结构化日志：
- 设备唯一标识（可配置 DOLL_ROBOT_ID，默认 hostname）
- 当前 Git 分支 + commit hash
- 各模块启动/停止/错误事件
- 本地 JSON Lines 存储，便于后续聚合分析

用法:
    from device_log import device_log
    device_log.startup(args)
    device_log.module_status("camera", "ok", "摄像头已启动")
    device_log.error("人脸追踪模型缺失", path="models/deploy.prototxt")
"""

import json
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class DeviceLog:
    """按设备记录结构化运行日志。"""

    def __init__(
        self,
        log_dir: str = "logs",
        device_id: Optional[str] = None,
        project_dir: Optional[str] = None,
    ):
        self.hostname = socket.gethostname()
        self.ip = self._get_ip()
        self.mac = self._get_mac()
        self.device_id = device_id or os.environ.get("DOLL_ROBOT_ID") or self.hostname

        self.project_dir = Path(project_dir) if project_dir else Path(__file__).resolve().parent
        self.branch = self._git_branch()
        self.commit = self._git_commit()
        self.dirty = self._git_dirty()

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / f"{self.device_id}.jsonl"

        self._lock = threading.Lock()
        self._start_time = time.time()

    # ------------------------------------------------------------------
    # 设备信息采集
    # ------------------------------------------------------------------
    def _get_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            # 不需要真正可达，只要触发路由选择即可
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def _get_mac(self) -> str:
        try:
            # 优先读取 eth0 / wlan0 MAC
            for iface in ("eth0", "wlan0"):
                path = f"/sys/class/net/{iface}/address"
                if os.path.exists(path):
                    with open(path) as f:
                        return f.read().strip()
            # 回退：从 hostname 派生一个伪 MAC 前缀
            return "00:00:00:00:00:00"
        except Exception:
            return "00:00:00:00:00:00"

    def _git_branch(self) -> str:
        return self._run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"])

    def _git_commit(self) -> str:
        return self._run_git(["git", "rev-parse", "--short", "HEAD"])

    def _git_dirty(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
            )
            return len(result.stdout.strip()) > 0
        except Exception:
            return False

    def _run_git(self, cmd: list) -> str:
        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_dir,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            return "unknown"

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def log(
        self,
        event: str,
        level: str = "INFO",
        message: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """写入一条结构化日志记录。"""
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device_id": self.device_id,
            "hostname": self.hostname,
            "ip": self.ip,
            "mac": self.mac,
            "branch": self.branch,
            "commit": self.commit,
            "dirty": self.dirty,
            "event": event,
            "level": level,
            "message": message,
            "uptime": round(time.time() - self._start_time, 3),
        }
        # 过滤 None 值，保持日志紧凑
        record = {k: v for k, v in record.items() if v is not None}
        if kwargs:
            record["extra"] = kwargs

        with self._lock:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def startup(self, args: Any = None) -> Dict[str, Any]:
        """记录程序启动事件。"""
        extra = {}
        if args is not None:
            try:
                extra["args"] = vars(args)
            except Exception:
                extra["args"] = str(args)
        return self.log("startup", message="Doll Robot 启动", **extra)

    def shutdown(self) -> Dict[str, Any]:
        return self.log("shutdown", message="Doll Robot 停止")

    def module_status(
        self, module: str, status: str, message: Optional[str] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        """记录模块状态变化，如 camera/ok、face/error。"""
        return self.log(
            "module_status",
            message=message,
            module=module,
            status=status,
            **kwargs,
        )

    def error(self, message: str, **kwargs: Any) -> Dict[str, Any]:
        return self.log("error", level="ERROR", message=message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> Dict[str, Any]:
        return self.log("warning", level="WARN", message=message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> Dict[str, Any]:
        return self.log("info", level="INFO", message=message, **kwargs)

    # ------------------------------------------------------------------
    # 日志读取辅助
    # ------------------------------------------------------------------
    def read_recent(self, n: int = 50) -> list:
        """读取最近 n 条日志记录。"""
        if not self.log_file.exists():
            return []
        with open(self.log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        records = []
        for line in lines[-n:]:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records


# 默认单例，方便直接导入使用
# 如需自定义 device_id 或 log_dir，可重新实例化：
#   device_log = DeviceLog(log_dir="/var/log/doll-robot", device_id="pi-01")
device_log = DeviceLog()
