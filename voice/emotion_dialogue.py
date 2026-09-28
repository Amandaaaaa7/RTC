"""
Emotion dialogue engine (legacy entry point).

New code should use voice.module.VoiceModule directly.
This module is kept for backwards compatibility with voice/test_minimal.py.
"""

import time

from voice.module import VoiceModule


def run_once() -> str:
    """Run one dialogue turn using a temporary VoiceModule."""
    mod = VoiceModule()
    mod.start()
    try:
        # Wait for one full interaction cycle
        previous = mod.state.state
        stable_start = None
        while True:
            time.sleep(0.1)
            current = mod.state.state
            if current == "idle":
                now = time.time()
                if stable_start is None:
                    stable_start = now
                elif now - stable_start > 1.5:
                    break
            else:
                stable_start = None
            if current != previous:
                print(f"[STATE] {current}")
                previous = current
        return mod.state.emotion or "Calmness"
    finally:
        mod.stop()


def run_loop():
    """Continuous dialogue loop."""
    print("=" * 50)
    print("语音情绪对话模块已启动")
    print("按 Ctrl+C 退出")
    print("=" * 50)
    mod = VoiceModule()
    mod.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[EXIT] 已退出")
    finally:
        mod.stop()
