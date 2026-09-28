"""Distance-proxy state machine for face-driven eye interaction."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProximityUpdate:
    state: str
    approach_started: bool = False
    started_disengaging: bool = False


class ProximityBehavior:
    """Use normalized face area to distinguish approaching and retreating.

    Thresholds are intentionally conservative initial values for a 320x240
    face-tracking stream. They are ratios, so they do not depend on the source
    resolution; physical-distance calibration remains a deployment task.
    """

    EMA_ALPHA = 0.45
    # Near thresholds only decide whether a face has been close enough for a
    # later far-away disengagement. They do not trigger Interest.
    NEAR_ENTER_RATIO = 0.055
    NEAR_EXIT_RATIO = 0.035
    FAR_ENTER_RATIO = 0.020
    FAR_EXIT_RATIO = 0.028
    FAR_DWELL_S = 1.0
    # Interest primarily uses a relative jump: face area must grow by at
    # least 15% between two smoothed detector samples (normally 0.5 s at 2 FPS).
    APPROACH_RELATIVE_GROWTH = 0.15
    # Slow approaches still trigger once when the smoothed area crosses this
    # fallback boundary. Re-arm only after the person recedes below EXIT.
    INTEREST_FALLBACK_ENTER_RATIO = 0.055
    INTEREST_FALLBACK_EXIT_RATIO = 0.035
    RETREAT_VELOCITY = -0.012

    def __init__(self):
        self.state = "idle"
        self.area_ratio = None
        self._last_at_s = None
        self._seen_near = False
        self._far_since_s = None
        self._interest_armed = True

    def reset(self):
        self.state = "idle"
        self.area_ratio = None
        self._last_at_s = None
        self._seen_near = False
        self._far_since_s = None
        self._interest_armed = True

    def observe(self, raw_area_ratio: float, now_s: float) -> ProximityUpdate:
        """Consume one fresh face area measurement and return state changes."""
        raw = max(0.0, float(raw_area_ratio))
        if self.area_ratio is None:
            self.area_ratio = raw
            velocity = 0.0
            relative_growth = 0.0
            crossed_interest_fallback = False
        else:
            previous = self.area_ratio
            self.area_ratio += (raw - self.area_ratio) * self.EMA_ALPHA
            previous_at_s = now_s if self._last_at_s is None else self._last_at_s
            elapsed = max(1e-6, now_s - previous_at_s)
            velocity = (self.area_ratio - previous) / elapsed
            relative_growth = (self.area_ratio - previous) / max(previous, 0.005)
            crossed_interest_fallback = (
                previous < self.INTEREST_FALLBACK_ENTER_RATIO
                <= self.area_ratio
            )
        self._last_at_s = now_s

        if self.area_ratio <= self.INTEREST_FALLBACK_EXIT_RATIO:
            self._interest_armed = True
        approach_started = self._interest_armed and (
            relative_growth >= self.APPROACH_RELATIVE_GROWTH
            or crossed_interest_fallback
        )
        if approach_started:
            self._interest_armed = False
        started_disengaging = False
        if self.state == "disengaging":
            if self.area_ratio >= self.FAR_EXIT_RATIO:
                self.state = "engaged"
                self._far_since_s = None
            return ProximityUpdate(self.state)

        if self.state == "distant":
            if approach_started:
                self.state = "approaching"
            elif self.area_ratio >= self.FAR_EXIT_RATIO:
                self.state = "engaged"
            return ProximityUpdate(self.state, approach_started)

        if self.area_ratio >= self.NEAR_ENTER_RATIO:
            self._seen_near = True
            self._far_since_s = None
            self.state = "approaching" if approach_started else "engaged"
        elif self._seen_near and self.area_ratio <= self.FAR_ENTER_RATIO:
            if self._far_since_s is None:
                self._far_since_s = now_s
            elif now_s - self._far_since_s >= self.FAR_DWELL_S:
                self.state = "disengaging"
                started_disengaging = True
        else:
            self._far_since_s = None
            if approach_started:
                self.state = "approaching"
            elif self._seen_near and velocity <= self.RETREAT_VELOCITY:
                self.state = "retreating"
            else:
                self.state = "engaged"
        return ProximityUpdate(self.state, approach_started, started_disengaging)

    def finish_disengaging(self):
        if self.state == "disengaging":
            self.state = "distant"
