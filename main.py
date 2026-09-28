#!/usr/bin/env python3
"""
Doll Robot — 集成入口 (Phase 2: 麦克风 + 摄像头 + 屏幕 + 运动追踪)

启动摄像头 MJPEG HTTP 流 + 麦克风电平监控 + GC9D01 动画眼睛。
在浏览器打开 http://<pi-ip>:8080 查看。

用法:
    python3 main.py                              # 全功能（视频流 + 麦克风 + 眼睛）
    python3 main.py --port 9090 --console-vu     # 自定义端口 + 终端 VU
    python3 main.py --dual-eye                   # 双目模式
    python3 main.py --motion                     # 眼睛跟随运动物体
    python3 main.py --face                       # 眼睛跟随人脸（YOLO 不可用，使用 OpenCV DNN）
    python3 main.py --reactive-voice             # 反应式语音: 说话结束后随机播放情绪音频
    python3 main.py --realtime-voice             # 豆包/方舟实时对话（默认关闭的新增路径）
    python3 main.py --kws                        # 快反应关键词唤醒: 喊名字即时播放拟声+眼睛反应
    python3 main.py --no-camera                  # 仅麦克风 + 屏幕
    python3 main.py --no-mic                     # 仅摄像头 + 屏幕
    python3 main.py --no-eye                     # 仅麦克风 + 摄像头
"""

import argparse
from pathlib import Path
import sys
import threading
import time

from camera import CameraStreamer, create_server
from eye_display import EyeDisplay, GAZE_POLICIES
from eye_debug_overlay import EyeDebugOverlay
from eye_styles import list_styles
from motion_tracker import MotionTracker
from face_tracker import FaceTracker, GazeCalibration
from device_log import device_log

DEFAULT_GAZE_CONFIG = Path(__file__).with_name("config") / "gaze_calibration.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Doll Robot - 麦克风 + 摄像头 + 屏幕")
    parser.add_argument("--port", type=int, default=8080, help="HTTP 端口")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="HTTP 监听地址")
    parser.add_argument("--console-vu", action="store_true", help="显示终端 VU 表")
    parser.add_argument("--no-camera", action="store_true", help="不启动摄像头")
    parser.add_argument("--no-mic", action="store_true", help="不启动麦克风")
    parser.add_argument("--no-eye", action="store_true", help="不启动屏幕")
    parser.add_argument("--dual-eye", action="store_true", help="双目模式 (共享 SPI0)")
    parser.add_argument("--eye-style", type=str, default=None,
                        help=f"眼睛样式 (可用: {', '.join(n for n, _ in list_styles())})")
    parser.add_argument("--motion", action="store_true", help="启用摄像头运动检测，眼睛跟随运动物体")
    parser.add_argument("--face", action="store_true", help="启用人脸追踪，眼睛跟随人脸")
    parser.add_argument("--face-detector", choices=("yunet", "caffe"), default="yunet",
                        help="CPU 人脸检测器 (默认 yunet；caffe 用于 A/B)")
    parser.add_argument("--eye-sample-interval", type=float, default=None,
                        help="眼睛读取最新 tracker snapshot 的周期（秒）；Day 2 对照用 3.0 或 0.04")
    parser.add_argument("--gaze-policy", choices=GAZE_POLICIES, default="continuous",
                        help="眼神策略：continuous（默认）、fixed、blink_latched")
    parser.add_argument("--face-fps", type=float, default=2.0,
                        help="人脸检测目标频率 (默认 2 fps)")
    parser.add_argument("--face-threshold", type=float, default=0.7,
                        help="人脸置信度阈值 (默认 0.7)")
    parser.add_argument("--face-hold-timeout", type=float, default=0.3,
                        help="锁定目标丢失后仍保持注视的最长时间，秒 (默认 0.3)")
    parser.add_argument("--face-switch-ratio", type=float, default=1.3,
                        help="新人脸面积需达到锁定目标面积的多少倍才切换 (默认 1.3)")
    parser.add_argument("--face-iou-threshold", type=float, default=0.3,
                        help="判断同一人的 IOU 阈值 (默认 0.3)")
    parser.add_argument("--face-dominant-frames", type=int, default=2,
                        help="另一人脸面积持续显著更大多少帧后切换 (默认 2)")
    parser.add_argument("--gaze-config", type=str, default=str(DEFAULT_GAZE_CONFIG),
                        help="single JSON file containing reproducible gaze calibration")
    # Optional overrides are for a temporary A/B trial; normal runs edit only
    # config/gaze_calibration.json.
    parser.add_argument("--gaze-x-sign", type=float, choices=(-1.0, 1.0))
    parser.add_argument("--gaze-y-sign", type=float, choices=(-1.0, 1.0))
    parser.add_argument("--gaze-x-gain", type=float)
    parser.add_argument("--gaze-y-gain", type=float)
    parser.add_argument("--gaze-x-bias", type=float)
    parser.add_argument("--gaze-y-bias", type=float)
    parser.add_argument("--gaze-deadband", type=float,
                        help="temporary override for both deadband axes")
    parser.add_argument("--gaze-deadband-x", type=float)
    parser.add_argument("--gaze-deadband-y", type=float)
    parser.add_argument("--gaze-smoothing", type=float,
                        help="temporary display-only smoothing override")
    parser.add_argument("--mic-gain", type=float, default=8.0, help="麦克风增益 (默认 8x)")
    parser.add_argument("--no-voice", action="store_true", help="不启动语音模块")
    parser.add_argument("--reactive-voice", action="store_true",
                        help="启用反应式语音: 检测说话结束后随机播放预录情绪音频(无ASR/LLM/网络)")
    parser.add_argument("--realtime-voice", action="store_true",
                        help="启用豆包 ASR + 方舟 + 豆包 TTS 的实时对话（与旧语音模式互斥）")
    parser.add_argument("--realtime-config", type=str, default=None,
                        help="实时对话配置 JSON 路径（默认 config/realtime_voice.example.json）")
    parser.add_argument("--reactive-threshold", type=float, default=0.04,
                        help="反应式语音触发阈值 (0-1, 默认 0.04)")
    parser.add_argument("--reactive-silence", type=float, default=0.8,
                        help="反应式语音静音结束时长 (秒, 默认 0.5)")
    parser.add_argument("--reactive-volume", type=float, default=0.2,
                        help="反应式语音播放音量 (0-1, 默认 0.2)")
    parser.add_argument("--reactive-emotion", type=str, default=None,
                        help="反应式语音固定情绪, 不指定则随机")
    parser.add_argument("--eye-debug-overlay", action="store_true",
                        help="在左眼屏幕以极小半透明字体显示调试信息")
    parser.add_argument("--expression-debug", action="store_true",
                        help="启用仅供测试的表情 HTTP 命令和 timing 事件记录")
    parser.add_argument("--voice-asr", type=str, default=None, help="ASR 后端: dashscope|google")
    parser.add_argument("--voice-llm", type=str, default=None, help="LLM 后端: dashscope")
    parser.add_argument("--voice-tts", type=str, default=None, help="TTS 后端: preset|edge")
    parser.add_argument("--kws", action="store_true", help="启用快反应关键词唤醒: 检测到关键词后立刻播放拟声并改变眼睛表情")
    parser.add_argument("--kws-config", type=str, default=None, help="快反应配置文件路径 (默认 config/fast_reaction.json)")
    parser.add_argument("--kws-keyword", type=str, default=None, help="覆盖配置文件中的关键词")
    parser.add_argument("--kws-backend", choices=("dummy", "sherpa-onnx", "openwakeword"), default=None, help="覆盖 KWS backend")
    parser.add_argument("--kws-threshold", type=float, default=None, help="覆盖检测阈值 (0-1)")
    parser.add_argument("--kws-volume", type=float, default=0.3, help="快反应音频播放音量 (0-1, 默认 0.3)")
    parser.add_argument("--kws-no-eye", action="store_true", help="快反应触发时不改变眼睛表情")
    parser.add_argument("--record", type=int, default=0, help="录音秒数(仅录音模式)")
    parser.add_argument("--sleep-on-absence", action="store_true",
                        help="无人在场自动入睡：FaceTracker 判定无脸达到阈值后播放入睡动画，"
                             "人出现立刻睁眼")
    parser.add_argument("--sleep-absence-timeout", type=float, default=120.0,
                        help="无脸多久后开始入睡，秒 (默认 120)")
    parser.add_argument("--sleep-wake-confirm", type=float, default=0.15,
                        help="睡着后需连续有脸多少秒才唤醒（去抖），秒 (默认 0.15)")
    return parser.parse_args()


def main():
    args = parse_args()

    # The new path is opt-in and intentionally does not alter legacy voice/KWS
    # interactions. Mixing independent consumers would make response ownership
    # ambiguous, so fail before any device is started.
    if args.realtime_voice:
        if args.reactive_voice or args.kws or args.voice_asr or args.voice_llm or args.voice_tts:
            raise ValueError("--realtime-voice 不能与 --reactive-voice、--kws 或旧云端语音参数同时使用")
        if args.no_voice:
            raise ValueError("--realtime-voice 不能与 --no-voice 同时使用")
        if args.no_mic:
            print("[MAIN] --realtime-voice 需要麦克风，忽略 --no-mic")
            args.no_mic = False

    # ---- 初始化设备日志 ----
    device_log.startup(args)

    print("=" * 55)
    print("  Doll Robot - Phase 2: 麦克风 + 摄像头 + 屏幕")
    print("=" * 55)

    # ---- 录音模式 (录制完成后退出) ----
    if args.record > 0:
        from mic import find_i2s_device, record_to_wav
        device_log.info("进入录音模式", seconds=args.record)
        device = find_i2s_device()
        record_to_wav(args.record, device=device, gain=64.0, normalize=True)
        return

    # ---- 初始化 ----
    streamer = None
    mic = None
    eye = None
    httpd = None
    motion = None
    face = None
    calibration = None
    voice = None
    reactive = None
    fast_reaction = None
    realtime = None
    # The regular dashboard imports this later.  Initialise it here because the
    # optional real-time adapter may start before the HTTP server is created.
    speaker_module = None

    try:
        if not args.no_camera:
            print("\n--- 摄像头 ---")
            streamer = CameraStreamer()
            if args.face:
                # 人脸检测每帧推理耗时较长，降低分辨率以控制总 CPU
                streamer.start(res_idx=5)  # 320x240
            elif args.motion:
                # 运动检测需要频繁 JPEG 解码，降低分辨率以控制 CPU
                streamer.start(res_idx=4)  # 640x480
            else:
                streamer.start()
            device_log.module_status("camera", "ok", "摄像头已启动")

        if args.motion:
            print("\n--- 运动检测 ---")
            motion = MotionTracker(camera_streamer=streamer, width=240, height=180)
            motion.start()
            device_log.module_status("motion", "ok", "运动检测已启动")

        if args.face:
            print("\n--- 人脸追踪 ---")
            try:
                calibration = GazeCalibration.from_json(args.gaze_config)
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError(
                    f"cannot load gaze calibration {args.gaze_config}: {exc}"
                ) from exc
            calibration = calibration.with_overrides(
                x_sign=args.gaze_x_sign,
                y_sign=args.gaze_y_sign,
                x_gain=args.gaze_x_gain,
                y_gain=args.gaze_y_gain,
                x_bias=args.gaze_x_bias,
                y_bias=args.gaze_y_bias,
                deadband_x=(
                    args.gaze_deadband_x if args.gaze_deadband_x is not None
                    else args.gaze_deadband
                ),
                deadband_y=(
                    args.gaze_deadband_y if args.gaze_deadband_y is not None
                    else args.gaze_deadband
                ),
                smoothing=args.gaze_smoothing,
            )
            print(f"[FACE] gaze calibration: {calibration}")
            face = FaceTracker(
                camera_streamer=streamer,
                fps=args.face_fps,
                detector_name=args.face_detector,
                confidence_threshold=args.face_threshold,
                calibration=calibration,
                target_hold_timeout=args.face_hold_timeout,
                switch_area_ratio=args.face_switch_ratio,
                iou_threshold=args.face_iou_threshold,
                dominant_switch_frames=args.face_dominant_frames,
            )
            face.start()
            if face.running:
                device_log.module_status("face", "ok", "人脸追踪已启动")
            else:
                device_log.module_status(
                    "face", "error", face.error_message or "人脸追踪启动失败"
                )

        if args.reactive_voice and args.no_mic:
            print("[MAIN] --reactive-voice 需要麦克风，忽略 --no-mic")
            args.no_mic = False

        if not args.no_mic:
            from mic import MicMonitor, find_i2s_device, print_vu_bar
            print("\n--- 麦克风 ---")
            device = find_i2s_device()
            mic = MicMonitor(device=device, gain=args.mic_gain)
            mic.start()
            device_log.module_status("mic", "ok", "麦克风已启动", device=device)

        if args.reactive_voice and mic is not None:
            print("\n--- 反应式语音 ---")
            # Load this optional dependency only when the feature is enabled.
            from voice.reactive_module import ReactiveVoiceModule
            reactive = ReactiveVoiceModule(
                mic_monitor=mic,
                threshold=args.reactive_threshold,
                silence_duration=args.reactive_silence,
                volume=args.reactive_volume,
                emotion=args.reactive_emotion,
            )
            reactive.start()

        if not args.no_eye:
            print("\n--- 眼睛屏幕 ---")
            eye = EyeDisplay(
                mic_monitor=mic, dual=args.dual_eye,
                motion_tracker=motion, face_tracker=face,
                voice_module=reactive,
                style=args.eye_style,
                gaze_smoothing=calibration.smoothing if calibration else 0.35,
                sample_interval=(args.eye_sample_interval
                                 if args.eye_sample_interval is not None else 0.04),
                gaze_policy=args.gaze_policy,
                retarget_deadband_x=(
                    calibration.retarget_deadband_x if calibration else 0.06
                ),
                retarget_deadband_y=(
                    calibration.retarget_deadband_y if calibration else 0.06
                ),
                large_retarget_distance=(
                    calibration.large_retarget_distance if calibration else 0.35
                ),
                expression_debug=args.expression_debug,
                sleep_on_absence=args.sleep_on_absence,
                sleep_absence_timeout=args.sleep_absence_timeout,
                sleep_wake_confirm=args.sleep_wake_confirm,
            )
            eye.start()
            if reactive is not None:
                # reactive VAD has no ASR/LLM state machine; explicitly connect its
                # immediate listening cue and the display-side timing acknowledgement.
                reactive.set_timing_callback(eye.on_voice_timing_event)
                eye.set_voice_timing_callback(reactive.on_listening_cue_frame)
            device_log.module_status("eye", "ok", "眼睛屏幕已启动", dual=args.dual_eye, style=args.eye_style)
            if args.eye_debug_overlay:
                print("[EYE] 启用左眼调试叠加层")
                overlay = EyeDebugOverlay(
                    eye_display=eye,
                    mic_monitor=mic,
                    camera_streamer=streamer,
                    voice_module=reactive,
                    http_port=args.port,
                )
                eye.set_debug_overlay(overlay)
            # 等待屏幕初始化完成
            time.sleep(2)

        if (not args.no_voice and not args.reactive_voice and mic is not None
                and (args.voice_asr or args.voice_llm or args.voice_tts)):
            print("\n--- 语音模块 ---")
            # VoiceModule imports cloud backends such as dashscope.
            from voice.module import VoiceModule
            voice = VoiceModule(
                mic_monitor=mic,
                state_callback=eye.on_voice_state if eye else None,
            )
            voice.start()

        if args.realtime_voice and mic is not None:
            print("\n--- 实时语音 ---")
            from voice.realtime import RealtimeVoiceOrchestrator
            realtime = RealtimeVoiceOrchestrator(
                mic_monitor=mic,
                eye_display=eye,
                config_path=args.realtime_config,
                speaker_module=speaker_module,
            )
            realtime.start()
            device_log.module_status("realtime_voice", "ok", "实时语音已启动")

        if args.kws and mic is not None:
            print("\n--- 快反应关键词唤醒 ---")
            from voice.fast_reaction import FastReactionModule
            fast_reaction = FastReactionModule(
                mic_monitor=mic,
                eye_display=eye if eye and not args.kws_no_eye else None,
                config_path=args.kws_config,
                volume=args.kws_volume,
            )
            # Apply CLI overrides after loading the config file.
            if args.kws_keyword is not None:
                fast_reaction.cfg["keyword"] = args.kws_keyword
            if args.kws_backend is not None:
                fast_reaction.cfg["backend"] = args.kws_backend
            if args.kws_threshold is not None:
                fast_reaction.cfg["detection_threshold"] = args.kws_threshold
            # Rebuild backend when overrides changed anything that affects it.
            fast_reaction._backend = FastReactionModule._backend_factory(fast_reaction.cfg)
            fast_reaction._target_rate = fast_reaction._backend.sample_rate()
            fast_reaction.start()
            device_log.module_status("kws", "ok", "快反应关键词唤醒已启动",
                                     keyword=fast_reaction.keyword,
                                     backend=fast_reaction.backend_name)

        # ---- HTTP 服务 ----
        if streamer is not None:
            speaker_module = None
            if not args.no_mic:
                import speaker as speaker_module

            print("\n--- HTTP 服务 ---")
            httpd = create_server(args.host, args.port, streamer,
                                   mic_monitor=mic, speaker_module=speaker_module,
                                   motion_monitor=motion, face_monitor=face,
                                   eye_monitor=eye, voice_monitor=(realtime or reactive or voice))
            httpd_thread = threading.Thread(
                target=httpd.serve_forever, daemon=True, name="http-server"
            )
            httpd_thread.start()
            device_log.module_status("http", "ok", "HTTP 服务已启动", port=args.port)

            from camera import RESOLUTIONS
            w, h, cur_name = RESOLUTIONS[streamer.res_idx]
            print(f"  URL:      http://<pi-ip>:{args.port}")
            print(f"  分辨率:   {cur_name}")

        # ---- 主循环 ----
        print(f"\n{'=' * 55}")
        print("  运行中... 按 Ctrl+C 停止")
        print(f"{'=' * 55}\n")

        try:
            while True:
                if args.console_vu and mic and mic.running:
                    print_vu_bar(mic.rms, mic.peak)
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n\n[MAIN] 收到停止信号...")
    except Exception as e:
        device_log.error("主程序异常", error=str(e), error_type=type(e).__name__)
        raise
    finally:
        print("[MAIN] 正在停止所有组件...")
        if realtime:
            realtime.stop()
            device_log.module_status("realtime_voice", "stopped")
        if fast_reaction:
            fast_reaction.stop()
        if voice:
            voice.stop()
        if reactive:
            reactive.stop()
        if eye:
            eye.stop()
            device_log.module_status("eye", "stopped")
        if face:
            face.stop()
            device_log.module_status("face", "stopped")
        if motion:
            motion.stop()
            device_log.module_status("motion", "stopped")
        if mic:
            mic.stop()
            device_log.module_status("mic", "stopped")
        if streamer:
            streamer.cleanup()
            device_log.module_status("camera", "stopped")
        if httpd:
            httpd.shutdown()
            device_log.module_status("http", "stopped")
        device_log.shutdown()
        print("[MAIN] 已停止")


if __name__ == "__main__":
    main()
