"""
Minimal standalone test for the voice emotion dialogue module.

Usage:
    python3 voice/test_minimal.py

Requires:
    - DASHSCOPE_API_KEY env var or voice/.env file
    - INMP441 I2S mic + MAX98357A speaker wired to Pi
"""

from voice.module import VoiceModule

if __name__ == "__main__":
    import time

    print("=" * 50)
    print("语音情绪对话最小闭环测试")
    print("按 Ctrl+C 退出")
    print("=" * 50)

    def on_state(state):
        print(f"[STATE] {state.state} emotion={state.emotion} text={state.text}")

    mod = VoiceModule(state_callback=on_state)
    mod.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[EXIT] 已退出")
    finally:
        mod.stop()
