#!/usr/bin/env python3
"""Repeatable QVGA MJPEG A/B benchmark for the Caffe SSD and YuNet adapters.

The preferred input is the raw ``.mjpeg`` file created by ``rpicam-vid``.  It
contains exactly the JPEG bytes produced by the camera, so JPEG decode is
measured independently from detection.  A directory of ``.jpg``/``.jpeg``
files is supported too.

Example (on the Pi):
    mkdir -p outputs/benchmarks/day2
    rpicam-vid --codec mjpeg --width 320 --height 240 --framerate 15 \
      --rotation 180 --timeout 60000 --nopreview --output \
      outputs/benchmarks/day2/qvga-ab.mjpeg
    python3 tools/benchmark_face_detectors.py \
      --input outputs/benchmarks/day2/qvga-ab.mjpeg --samples 600
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import resource
import subprocess
import time
from typing import Iterable

import cv2
import numpy as np

from face_detectors import create_face_detector


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "outputs" / "benchmarks" / "day2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure Caffe SSD and YuNet against the same QVGA MJPEG frames."
    )
    parser.add_argument("--input", required=True, type=Path,
                        help="raw .mjpeg file or directory containing .jpg/.jpeg frames")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--samples", type=int, default=600,
                        help="timed frames per detector; frames repeat deterministically")
    parser.add_argument("--warmup", type=int, default=30,
                        help="untimed frames per detector")
    parser.add_argument("--threshold", type=float, default=0.7,
                        help="same confidence threshold for both adapters")
    parser.add_argument("--order", default="caffe,yunet",
                        help="comma-separated detector order; default caffe,yunet")
    parser.add_argument("--labels", type=Path,
                        help="optional CSV: frame_id,scenario,face_expected (0 or 1)")
    parser.add_argument("--run-id", default=time.strftime("%Y-%m-%dT%H%M%S"),
                        help="file-name prefix for this run")
    args = parser.parse_args()
    if args.samples <= 0 or args.warmup < 0:
        parser.error("--samples must be > 0 and --warmup must be >= 0")
    args.order = [item.strip() for item in args.order.split(",") if item.strip()]
    if sorted(args.order) != ["caffe", "yunet"]:
        parser.error("--order must contain caffe and yunet exactly once")
    return args


def mjpeg_frames(path: Path) -> Iterable[tuple[str, bytes]]:
    data = path.read_bytes()
    pos = 0
    index = 0
    while True:
        start = data.find(b"\xff\xd8", pos)
        if start < 0:
            return
        end = data.find(b"\xff\xd9", start + 2)
        if end < 0:
            raise ValueError(f"truncated JPEG frame in {path}")
        yield f"{path.name}:{index:06d}", data[start:end + 2]
        index += 1
        pos = end + 2


def load_frames(path: Path) -> list[tuple[str, bytes]]:
    if path.is_dir():
        files = sorted(
            item for item in path.iterdir()
            if item.is_file() and item.suffix.lower() in {".jpg", ".jpeg"}
        )
        frames = [(item.name, item.read_bytes()) for item in files]
    else:
        frames = list(mjpeg_frames(path))
    if not frames:
        raise ValueError("input contains no JPEG frames")
    for frame_id, jpeg in frames[:10]:
        decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if decoded is None:
            raise ValueError(f"cannot decode frame {frame_id}")
        if decoded.shape[1::-1] != (320, 240):
            raise ValueError(
                f"{frame_id} is {decoded.shape[1]}x{decoded.shape[0]}, expected QVGA 320x240"
            )
    return frames


def percentile(values: list[float], q: float) -> float:
    return round(float(np.percentile(np.asarray(values, dtype=float), q)), 3)


def read_rss_mib() -> float:
    for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 3)
    return 0.0


def command_output(*command: str) -> str | None:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL,
                                       timeout=3).strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def pi_health() -> dict[str, str | None]:
    return {
        "temperature": command_output("vcgencmd", "measure_temp"),
        "throttled": command_output("vcgencmd", "get_throttled"),
    }


def load_labels(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"frame_id", "scenario", "face_expected"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("labels CSV must have frame_id,scenario,face_expected columns")
        return {row["frame_id"]: row for row in reader}


def quality_summary(rows: list[dict[str, object]], labels: dict[str, dict[str, str]]) -> dict[str, object]:
    if not labels:
        return {"status": "not measured: no labels CSV supplied"}
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        label = labels.get(str(row["frame_id"]))
        if label is None:
            continue
        grouped.setdefault(label["scenario"].strip().lower(), []).append(
            {**row, "face_expected": label["face_expected"].strip() == "1"}
        )
    result: dict[str, object] = {}
    for scenario, items in grouped.items():
        expected = [item for item in items if item["face_expected"]]
        unexpected = [item for item in items if not item["face_expected"]]
        result[scenario] = {
            "labelled_samples": len(items),
            "detection_rate_percent": round(
                100 * sum(bool(item["detected"]) for item in expected) / len(expected), 2
            ) if expected else None,
            "false_positive_count": sum(bool(item["detected"]) for item in unexpected),
        }
    return result or {"status": "no benchmark frame IDs matched the labels CSV"}


def run_detector(name: str, frames: list[tuple[str, bytes]], args: argparse.Namespace,
                 labels: dict[str, dict[str, str]]) -> dict[str, object]:
    detector = create_face_detector(name, confidence_threshold=args.threshold)
    for index in range(args.warmup):
        jpeg = frames[index % len(frames)][1]
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        detector.detect(frame)

    health_before = pi_health()
    wall_started = time.monotonic()
    cpu_started = time.process_time()
    rows: list[dict[str, object]] = []
    peak_rss_mib = read_rss_mib()
    for index in range(args.samples):
        frame_id, jpeg = frames[index % len(frames)]
        started = time.monotonic_ns()
        decode_started = started
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        decoded = time.monotonic_ns()
        if frame is None:
            raise RuntimeError(f"decode unexpectedly failed for {frame_id}")
        detection = detector.detect(frame)
        ended = time.monotonic_ns()
        peak_rss_mib = max(peak_rss_mib, read_rss_mib())
        rows.append({
            "frame_id": frame_id,
            "sample": index,
            "decode_ms": (decoded - decode_started) / 1_000_000,
            "inference_ms": (ended - decoded) / 1_000_000,
            "end_to_end_ms": (ended - started) / 1_000_000,
            "detected": detection is not None,
            "confidence": round(detection.confidence, 6) if detection else None,
        })
    wall_elapsed = time.monotonic() - wall_started
    cpu_elapsed = time.process_time() - cpu_started
    health_after = pi_health()

    decode = [float(row["decode_ms"]) for row in rows]
    inference = [float(row["inference_ms"]) for row in rows]
    end_to_end = [float(row["end_to_end_ms"]) for row in rows]
    return {
        "detector": name,
        "samples": args.samples,
        "warmup": args.warmup,
        "confidence_threshold": args.threshold,
        "input_frames": len(frames),
        "decode_ms": {"p50": percentile(decode, 50), "p95": percentile(decode, 95)},
        "inference_ms": {"p50": percentile(inference, 50), "p95": percentile(inference, 95)},
        "end_to_end_ms": {"p50": percentile(end_to_end, 50), "p95": percentile(end_to_end, 95)},
        "effective_fps": round(args.samples / wall_elapsed, 3),
        # CPU is this benchmark process and can exceed 100% when it uses >1 core.
        "process_cpu_percent": round(100 * cpu_elapsed / wall_elapsed, 2),
        "rss_mib_peak": peak_rss_mib,
        "health_before": health_before,
        "health_after": health_after,
        "detected_frames": sum(bool(row["detected"]) for row in rows),
        "quality": quality_summary(rows, labels),
        "rows": rows,
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "frame_id", "sample", "decode_ms", "inference_ms", "end_to_end_ms",
            "detected", "confidence",
        ])
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    frames = load_frames(args.input)
    labels = load_labels(args.labels)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "run_id": args.run_id,
        "input": str(args.input),
        "input_sha256": __import__("hashlib").sha256(args.input.read_bytes()).hexdigest()
        if args.input.is_file() else None,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "opencv_version": cv2.__version__,
        "results": {},
    }
    for name in args.order:
        print(f"[AB] running {name}: {args.samples} timed frames after {args.warmup} warmup")
        result = run_detector(name, frames, args, labels)
        rows = result.pop("rows")
        write_csv(args.output_dir / f"{args.run_id}-{name}-samples.csv", rows)
        run["results"][name] = result
        print(json.dumps(result, ensure_ascii=False, indent=2))
    summary_path = args.output_dir / f"{args.run_id}-summary.json"
    summary_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[AB] summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
