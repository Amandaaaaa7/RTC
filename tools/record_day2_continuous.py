#!/usr/bin/env python3
"""Collect Day 2 P0 metrics from a running Pi process and its /status endpoint."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent.parent


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[round((len(ordered) - 1) * q / 100)], 3)


def command(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=3).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def process_metrics(pid, previous=None):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        cpu_ticks = int(fields[11]) + int(fields[12])
        rss_mib = round(int(fields[21]) * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024, 3)
        status = Path(f"/proc/{pid}/status").read_text()
        match = re.search(r"^VmSwap:\s+(\d+)\s+kB$", status, re.MULTILINE)
        swap_mib = round(int(match.group(1)) / 1024, 3) if match else 0.0
    except (OSError, IndexError, ValueError) as exc:
        # Do not silently emit blank resource columns: this usually means that
        # --pid is stale or points at a process different from the HTTP server.
        return None, None, None, previous, f"pid_metrics_error={type(exc).__name__}: {exc}"
    current = (cpu_ticks, time.monotonic_ns())
    cpu = None
    if previous:
        elapsed_s = (current[1] - previous[1]) / 1_000_000_000
        if elapsed_s > 0:
            cpu = round((cpu_ticks - previous[0]) / os.sysconf("SC_CLK_TCK") / elapsed_s * 100, 3)
    return cpu, rss_mib, swap_mib, current, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8080/status")
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/benchmarks/day2")
    args = parser.parse_args()
    if args.seconds <= 0 or args.interval <= 0:
        parser.error("seconds and interval must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows, previous = [], None
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(args.url, timeout=3) as response:
                status, error = json.loads(response.read().decode()), None
        except Exception as exc:
            status, error = {}, str(exc)
        cpu, rss, swap, previous, process_error = process_metrics(args.pid, previous)
        if process_error:
            error = f"{error}; {process_error}" if error else process_error
        face, eye = status.get("face_metrics") or {}, status.get("eye_metrics") or {}
        voice = status.get("voice_metrics") or {}
        audio = status.get("audio_metrics") or {}
        row = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cpu_percent": cpu,
            "rss_mib": rss, "swap_mib": swap, "temperature": command("vcgencmd", "measure_temp"),
            "throttled": command("vcgencmd", "get_throttled"), "capture_fps": status.get("fps"),
            "tracker_effective_fps": face.get("tracker_effective_fps"),
            "capture_to_snapshot_p50_ms": (face.get("capture_to_snapshot_ms") or {}).get("p50"),
            "capture_to_snapshot_p95_ms": (face.get("capture_to_snapshot_ms") or {}).get("p95"),
            "snapshot_age_ms": eye.get("snapshot_age_ms"), "snapshot_stale_drops": eye.get("snapshot_stale_drops"),
            "snapshot_age_p95_ms": (eye.get("snapshot_age_ms_percentiles") or {}).get("p95"),
            "snapshot_to_policy_commit_p50_ms": (eye.get("snapshot_to_policy_commit_ms") or {}).get("p50"),
            "snapshot_to_policy_commit_p95_ms": (eye.get("snapshot_to_policy_commit_ms") or {}).get("p95"),
            "policy_commit_to_first_eye_frame_p50_ms": (eye.get("policy_commit_to_first_eye_frame_ms") or {}).get("p50"),
            "policy_commit_to_first_eye_frame_p95_ms": (eye.get("policy_commit_to_first_eye_frame_ms") or {}).get("p95"),
            "face_to_eye_first_frame_p50_ms": (eye.get("face_to_eye_first_frame_ms") or {}).get("p50"),
            "face_to_eye_first_frame_p95_ms": (eye.get("face_to_eye_first_frame_ms") or {}).get("p95"),
            "eye_effective_fps": eye.get("eye_effective_fps"),
            "eye_frame_p95_ms": (eye.get("eye_frame_interval_ms") or {}).get("p95"),
            "sample_interval_ms": eye.get("sample_interval_ms"), "error": error,
            "vad_to_listening_cue_p50_ms": (voice.get("vad_to_listening_cue_ms") or {}).get("p50"),
            "vad_to_listening_cue_p95_ms": (voice.get("vad_to_listening_cue_ms") or {}).get("p95"),
            "speech_end_to_silence_confirmed_p50_ms": (voice.get("speech_end_to_silence_confirmed_ms") or {}).get("p50"),
            "speech_end_to_silence_confirmed_p95_ms": (voice.get("speech_end_to_silence_confirmed_ms") or {}).get("p95"),
            "silence_confirmed_to_first_audio_sample_p50_ms": (voice.get("silence_confirmed_to_first_audio_sample_ms") or {}).get("p50"),
            "silence_confirmed_to_first_audio_sample_p95_ms": (voice.get("silence_confirmed_to_first_audio_sample_ms") or {}).get("p95"),
            "vad_to_listening_cue_count": voice.get("vad_to_listening_cue_count", 0),
            "audio_state": audio.get("state"), "audio_queued_count": audio.get("queued_count"),
            "audio_started_count": audio.get("started_count"),
            "audio_completed_count": audio.get("completed_count"),
            "audio_failed_count": audio.get("failed_count"),
            "audio_rejected_count": audio.get("rejected_count"),
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
        time.sleep(args.interval)
    csv_path = args.output_dir / f"{args.run_id}-continuous.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    numeric = ("cpu_percent", "rss_mib", "swap_mib", "capture_fps", "tracker_effective_fps", "capture_to_snapshot_p50_ms", "capture_to_snapshot_p95_ms", "snapshot_age_ms", "snapshot_age_p95_ms", "snapshot_to_policy_commit_p50_ms", "snapshot_to_policy_commit_p95_ms", "policy_commit_to_first_eye_frame_p50_ms", "policy_commit_to_first_eye_frame_p95_ms", "face_to_eye_first_frame_p50_ms", "face_to_eye_first_frame_p95_ms", "eye_effective_fps", "eye_frame_p95_ms", "vad_to_listening_cue_p50_ms", "vad_to_listening_cue_p95_ms", "speech_end_to_silence_confirmed_p50_ms", "speech_end_to_silence_confirmed_p95_ms", "silence_confirmed_to_first_audio_sample_p50_ms", "silence_confirmed_to_first_audio_sample_p95_ms")
    summary = {"run_id": args.run_id, "samples": len(rows), "raw_csv": str(csv_path), "metrics": {}, "throttled_values": sorted({r["throttled"] for r in rows if r["throttled"]}), "errors": [r["error"] for r in rows if r["error"]]}
    for key in numeric:
        values = [float(row[key]) for row in rows if row[key] is not None]
        summary["metrics"][key] = {"p50": percentile(values, 50), "p95": percentile(values, 95), "average": round(statistics.mean(values), 3) if values else None, "peak": round(max(values), 3) if values else None}
    summary_path = args.output_dir / f"{args.run_id}-continuous-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[CONTINUOUS] summary: {summary_path}")


if __name__ == "__main__":
    main()
