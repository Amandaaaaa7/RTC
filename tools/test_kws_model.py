"""Unified KWS model comparison and standalone test script.

Usage examples:

    # Test the dummy backend with a live microphone for 10 seconds
    python3 tools/test_kws_model.py --backend dummy --duration 10

    # Test a sherpa-onnx model against a WAV file
    python3 tools/test_kws_model.py --backend sherpa-onnx \
        --model models/kws/sherpa-onnx \
        --keyword 小小熊 \
        --audio rec_name_call.wav

    # Test an openwakeword model against a WAV file
    python3 tools/test_kws_model.py --backend openwakeword \
        --model models/kws/openwakeword/xiaoxiaoxiong.onnx \
        --keyword 小小熊 \
        --audio rec_name_call.wav

    # Compare all available backends on the same audio file
    python3 tools/test_kws_model.py --compare \
        --audio rec_name_call.wav \
        --keyword 小小熊 \
        --model models/kws/sherpa-onnx

The script prints per-backend latency percentiles and detection results.
"""

import argparse
import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np

# Make sure the project root is on sys.path when running from tools/.
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from voice.kws import EnergyDummyKWS, SherpaOnnxKWS, OpenWakeWordKWS
from voice.config import SAMPLE_RATE


def _load_wav(path: str) -> tuple[np.ndarray, int]:
    """Load a mono WAV and return (float32 [-1, 1], sample_rate)."""
    with wave.open(path, "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    if width == 1:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128
        scale = 1.0 / 128.0
    elif width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
        scale = 1.0 / 32768.0
    elif width == 3:
        buf = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        raw24 = buf[:, 0].astype(np.uint32) | \
                (buf[:, 1].astype(np.uint32) << 8) | \
                (buf[:, 2].astype(np.uint32) << 16)
        data = np.where(raw24 > 2 ** 23 - 1, raw24 - 2 ** 24, raw24).astype(np.float64)
        scale = 1.0 / (2 ** 23)
    elif width == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float64)
        scale = 1.0 / (2 ** 31)
    else:
        raise ValueError(f"Unsupported sample width: {width}")

    if channels > 1:
        data = data.reshape(-1, channels)[:, 0]

    return (data * scale).astype(np.float32), rate


def _resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return data
    ratio = dst_rate / src_rate
    x_old = np.linspace(0, 1, len(data))
    x_new = np.linspace(0, 1, int(len(data) * ratio))
    return np.interp(x_new, x_old, data)


def _make_backend(backend_name: str, keyword: str, model_path: str | None,
                  threshold: float):
    backend_name = backend_name.lower()
    if backend_name == "sherpa-onnx":
        if model_path is None:
            model_path = "models/kws/sherpa-onnx"
        return SherpaOnnxKWS(keyword=keyword, model_dir=model_path, threshold=threshold)
    if backend_name == "openwakeword":
        if model_path is None:
            model_path = "models/kws/openwakeword/xiaoxiaoxiong.onnx"
        return OpenWakeWordKWS(keyword=keyword, model_path=model_path, threshold=threshold)
    if backend_name == "dummy":
        return EnergyDummyKWS(keyword=keyword, threshold=threshold)
    raise ValueError(f"Unknown backend: {backend_name}")


def _run_file(backend, audio_path: str):
    """Run the backend over a WAV file and return detection results."""
    pcm, rate = _load_wav(audio_path)
    pcm = _resample(pcm, rate, backend.sample_rate())
    backend.reset()

    # Offline test: feed the whole utterance at once (like the official example),
    # then finalize. Streaming real-time mode uses small chunks via _run_live.
    inference_times = []
    results = []
    t0 = time.monotonic()
    keyword, confidence = backend.detect(pcm)
    inference_times.append((time.monotonic() - t0) * 1000.0)
    if keyword is not None:
        results.append({
            "sample": 0,
            "time_s": 0.0,
            "keyword": keyword,
            "confidence": round(float(confidence), 3),
        })
        print(f"  Detected '{keyword}' at 0.00s (confidence={confidence:.3f})")

    # Offline tail flush.
    t1 = time.monotonic()
    keyword, confidence = backend.finalize_stream()
    inference_times.append((time.monotonic() - t1) * 1000.0)
    if keyword is not None:
        results.append({
            "sample": len(pcm),
            "time_s": len(pcm) / backend.sample_rate(),
            "keyword": keyword,
            "confidence": round(float(confidence), 3),
        })
        print(f"  Detected '{keyword}' at {len(pcm) / backend.sample_rate():.2f}s "
              f"(confidence={confidence:.3f}) [finalize]")

    return results, inference_times


def _run_live(backend, duration: float):
    """Run the backend on live microphone audio for ``duration`` seconds."""
    from mic import find_i2s_device, MicMonitor

    mic = MicMonitor(device=find_i2s_device(), gain=8.0)
    mic.start()
    print(f"[TEST] 监听 {duration}s，请说关键词...")
    backend.reset()

    results = []
    inference_times = []

    def cb(frame):
        # Resample one MicMonitor block (48 kHz int32) to backend rate.
        # Convert int32 to float32 using the fixed int32 range, not per-frame peak.
        pcm = frame.astype(np.float32) / 2147483648.0
        pcm = _resample(pcm, SAMPLE_RATE, backend.sample_rate())
        t0 = time.monotonic()
        keyword, confidence = backend.detect(pcm)
        inference_times.append((time.monotonic() - t0) * 1000.0)
        if keyword is not None:
            now_s = len(results) * (len(pcm) / backend.sample_rate())
            results.append({
                "sample": len(results),
                "time_s": now_s,
                "keyword": keyword,
                "confidence": round(float(confidence), 3),
            })
            print(f"  Detected '{keyword}' at {now_s:.2f}s "
                  f"(confidence={confidence:.3f})")

    mic.register_audio_callback(cb)
    try:
        time.sleep(duration)
    finally:
        mic.unregister_audio_callback(cb)
        mic.stop()

    return results, inference_times


def _percentiles(values):
    if not values:
        return {"p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    return {
        "p50": round(ordered[int((len(ordered) - 1) * 0.50)], 2),
        "p95": round(ordered[int((len(ordered) - 1) * 0.95)], 2),
        "max": round(ordered[-1], 2),
    }


def _test_backend(name: str, backend, audio_path: str | None, duration: float):
    print(f"\n[TEST] Backend: {name} ({backend.name})")
    print(f"       Sample rate: {backend.sample_rate()} Hz")
    if audio_path:
        if not os.path.exists(audio_path):
            print(f"       ERROR: audio file not found: {audio_path}")
            return None
        results, times = _run_file(backend, audio_path)
    else:
        results, times = _run_live(backend, duration)

    latency = _percentiles(times)
    print(f"       Detections: {len(results)}")
    print(f"       Inference latency (ms): {latency}")
    return {
        "backend": name,
        "available": True,
        "detections": results,
        "latency_ms": latency,
    }


def main():
    parser = argparse.ArgumentParser(description="Test and compare KWS backends")
    parser.add_argument("--backend", choices=("dummy", "sherpa-onnx", "openwakeword"),
                        default="dummy", help="KWS backend to test")
    parser.add_argument("--model", type=str, default=None,
                        help="Model directory (sherpa-onnx) or model file (openwakeword)")
    parser.add_argument("--keyword", type=str, default="小小熊", help="Keyword to detect")
    parser.add_argument("--threshold", type=float, default=0.5, help="Detection threshold")
    parser.add_argument("--audio", type=str, default=None,
                        help="Path to mono WAV file; if omitted, use live microphone")
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Live microphone test duration in seconds")
    parser.add_argument("--compare", action="store_true",
                        help="Run dummy, sherpa-onnx, and openwakeword on the same audio")
    parser.add_argument("--output", type=str, default=None,
                        help="Optional JSON file to write results")
    args = parser.parse_args()

    report = []

    if args.compare:
        backends = []
        for name in ("dummy", "sherpa-onnx", "openwakeword"):
            try:
                backend = _make_backend(name, args.keyword, args.model, args.threshold)
                backends.append((name, backend))
            except Exception as exc:
                print(f"[TEST] {name} unavailable: {exc}")
                report.append({"backend": name, "available": False, "error": str(exc)})
        for name, backend in backends:
            result = _test_backend(name, backend, args.audio, args.duration)
            if result is not None:
                report.append(result)
    else:
        backend = _make_backend(args.backend, args.keyword, args.model, args.threshold)
        result = _test_backend(args.backend, backend, args.audio, args.duration)
        if result is not None:
            report.append(result)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[TEST] Results written to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
