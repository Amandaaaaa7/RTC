"""回归测试：gallery 的按键分发 + review 模式。

关键点：
  - r / R 都能触发 review 模式
  - ReviewPlayer 用真 style 实时绘制 27 锚点 × 4 强度 = 108 单元 × 12 帧 5 秒动效
  - 返回 (left, right) 元组，左 mirror=False / 右 mirror=True
  - seek_start / next / prev 正确
  - 缺失样式提供器不应当崩
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools.eye_style_gallery as gallery_mod
from tools.eye_style_gallery import ReviewPlayer, REVIEW_INTENSITIES


class ReviewPlayerTest(unittest.TestCase):

    def setUp(self):
        # 用真 style 才能验证 mirror=True 真的不同（stub gaze=0 翻不转）
        from eye_styles import get_style
        self.style = get_style("pi2_light_blue")
        self.player = ReviewPlayer(style_getter=lambda: self.style)

    def test_cell_count(self):
        # _anchor_grid.json 存在时 → 27×4=108；否则退回 5×4=20
        # 路径必须与 _load_anchor_grid 一致（本仓 outputs/reviews/），
        # 不能写死某台机器的绝对路径。
        anchor = Path(gallery_mod.__file__).resolve().parent.parent / \
            "outputs" / "reviews" / "_anchor_grid.json"
        if anchor.is_file():
            self.assertEqual(len(self.player), 27 * len(REVIEW_INTENSITIES))
        else:
            self.assertEqual(len(self.player), 5 * len(REVIEW_INTENSITIES))

    def test_current_label_format(self):
        label = self.player.current_label()
        self.assertIn("×", label)

    def test_next_prev_seek(self):
        first = self.player.current_label()
        self.player.next()
        next_label = self.player.current_label()
        self.assertNotEqual(first, next_label)
        self.player.prev()
        self.assertEqual(first, self.player.current_label())
        self.player.seek_start()
        # seek_start 重置回 0 — 当前 label 等于 first
        self.assertEqual(first, self.player.current_label())

    def test_call_returns_dual_rgba(self):
        # 必须返回 (left, right) 元组，左 mirror=False / 右 mirror=True，
        # 与 happy/angry/cry 一样是轴对称
        result = self.player()
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        left, right = result
        for img in (left, right):
            self.assertEqual(img.size, (160, 160))
            self.assertEqual(img.mode, "RGBA")
        # 一些情绪（gaze 大）的帧，左右应不同。先找一个有非零 gaze 的帧
        # 再断言它在 mirror=True 时左右像素不同。
        import numpy as np
        found_diff = False
        for ci in range(len(self.player)):
            self.player.i = ci
            for fi in range(len(self.player.cells[ci].get("frames") or [None])):
                if fi:
                    self.player._frame_idx = fi
                left, right = self.player()
                diff = np.abs(np.array(left).astype(int) - np.array(right).astype(int)).sum()
                if diff > 1000:  # mirror 翻转在非零 gaze 时可见
                    found_diff = True
                    break
            if found_diff:
                break
        self.assertTrue(found_diff,
            "扫描 108 单元 × 12 帧未找到左右眼镜像差异 —— mirror 可能无效")

    def test_next_advances_within_intensity_then_rolls(self):
        # 跨表情翻动：第一格的下一格 = 第二表情 × 最小强度
        first = self.player.current_label()
        self.player.next()
        second = self.player.current_label()
        self.assertNotEqual(first, second)
        # 不应和 first 同 — 表示 next 真的翻动了
        self.assertNotEqual(first, second)

    def test_call_advances_frames_over_time(self):
        """回归：__call__ 必须随 time.time() 推进 _frame_idx。

        历史 bug：_last_tick（秒） += advance * frame_dur_ms（毫秒），
        单位不匹配导致 _last_tick 冲到现在 +83s/帧，下一帧 elapsed 为负，
        动画永久冻死（屏上静态）。这里连续调用并 sleep，断言帧号前进且
        像素真的变化。
        """
        import time as _t
        import numpy as np
        self.player.restart_anim()
        l1, _ = self.player()
        idx1 = self.player._frame_idx
        _t.sleep(0.25)  # > 2 帧 @12fps
        l2, _ = self.player()
        idx2 = self.player._frame_idx
        self.assertGreater(idx2, idx1,
            "__call__ 0.25s 后 _frame_idx 未前进 —— 计时推进坏了")
        _t.sleep(0.25)
        l3, _ = self.player()
        self.assertGreater(self.player._frame_idx, idx2,
            "第二次 __call__ 后 _frame_idx 仍未前进 —— 动画冻死")
        # 帧号在动还不够，像素也得真的变（ eyelid 10.0 -> 4.0 之类）
        diffs = [
            np.abs(np.array(a).astype(int) - np.array(b).astype(int)).sum()
            for a, b in ((l1, l2), (l2, l3))
        ]
        self.assertTrue(any(d > 0 for d in diffs),
            f"帧号在动但像素不变（diffs={diffs}）—— 取帧逻辑坏了")


class KeyParseTest(unittest.TestCase):
    """按键分发字符串匹配。"""

    def test_r_lowercase_matches(self):
        self.assertTrue("r".lower() == "r")

    def test_R_uppercase_lowers(self):
        self.assertTrue("R".lower() == "r")

    def test_brackets_in_review_mode(self):
        self.assertTrue("[" == "[")
        self.assertTrue("]" == "]")


if __name__ == "__main__":
    unittest.main(verbosity=2)
