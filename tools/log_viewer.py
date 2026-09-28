#!/usr/bin/env python3
"""
LogViewer — 查看 / 聚合多个 Doll Robot 设备的运行日志

用法:
    python3 tools/log_viewer.py                  # 显示 logs/ 下所有设备最近 20 条
    python3 tools/log_viewer.py -n 100           # 显示最近 100 条
    python3 tools/log_viewer.py --device pi-01   # 仅显示指定设备
    python3 tools/log_viewer.py --event error    # 仅显示错误事件
"""

import argparse
import json
from datetime import datetime
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(description="Doll Robot 多设备日志查看器")
    parser.add_argument("--log-dir", default="logs", help="日志目录")
    parser.add_argument("-n", type=int, default=20, help="每个设备显示最近 N 条")
    parser.add_argument("--device", help="仅显示指定 device_id")
    parser.add_argument("--event", help="仅显示指定事件类型")
    parser.add_argument("--level", help="仅显示指定日志级别")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    return parser.parse_args()


def load_records(log_dir: Path, device_filter: str = None) -> list:
    records = []
    if not log_dir.exists():
        return records

    for path in log_dir.glob("*.jsonl"):
        device_id = path.stem
        if device_filter and device_id != device_filter:
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rec["_source_file"] = path.name
                    records.append(rec)
                except json.JSONDecodeError:
                    continue
    records.sort(key=lambda r: r.get("timestamp", ""))
    return records


def format_record(rec: dict) -> str:
    ts = rec.get("timestamp", "")
    # 简化 ISO 时间显示
    try:
        dt = datetime.fromisoformat(ts)
        ts = dt.strftime("%m-%d %H:%M:%S")
    except Exception:
        pass

    device = rec.get("device_id", "unknown")
    branch = rec.get("branch", "?")
    commit = rec.get("commit", "?")
    event = rec.get("event", "?")
    level = rec.get("level", "INFO")
    msg = rec.get("message") or ""

    extra = ""
    if "extra" in rec:
        extras = []
        for k, v in rec["extra"].items():
            if k in ("message",):
                continue
            extras.append(f"{k}={v}")
        if extras:
            extra = " | " + ", ".join(extras[:3])

    return f"[{ts}] [{level:5}] {device:12} ({branch}/{commit}) {event:15} {msg}{extra}"


def main():
    args = parse_args()
    log_dir = Path(args.log_dir)
    records = load_records(log_dir, args.device)

    if args.event:
        records = [r for r in records if r.get("event") == args.event]
    if args.level:
        records = [r for r in records if r.get("level") == args.level]

    # 每个设备限制 N 条：按设备分组取最近 N 条
    if args.device:
        records = records[-args.n:]
    else:
        by_device = {}
        for r in records:
            by_device.setdefault(r.get("device_id", "unknown"), []).append(r)
        filtered = []
        for dev, recs in by_device.items():
            filtered.extend(recs[-args.n:])
        records = sorted(filtered, key=lambda r: r.get("timestamp", ""))

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return

    if not records:
        print(f"未在 {log_dir} 找到日志记录")
        return

    print(f"{'=' * 80}")
    print(f"  Doll Robot 运行日志 ({len(records)} 条)")
    print(f"{'=' * 80}")
    for rec in records:
        print(format_record(rec))


if __name__ == "__main__":
    main()
