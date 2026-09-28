"""Low-risk, local-only reactions that never make semantic claims."""

from __future__ import annotations

from .contracts import Affect, SocialMotionIntent, VoiceEvent


class LocalReflexEngine:
    """Map VAD events to short passive intents, independent of cloud health."""

    def on_voice_event(self, event: VoiceEvent) -> list[SocialMotionIntent]:
        if event.type == "voice_onset":
            return [self._intent(event, "listen", 1_500, "orient", Affect(0.1, 0.35))]
        if event.type == "voice_end":
            return [self._intent(event, "acknowledge", 700, "acknowledge", Affect(0.1, 0.2))]
        return []

    @staticmethod
    def _intent(event: VoiceEvent, kind: str, deadline_ms: int, commitment_level: str,
                affect: Affect) -> SocialMotionIntent:
        return SocialMotionIntent(
            trace_id=event.trace_id,
            turn_id=event.turn_id,
            kind=kind,  # type: ignore[arg-type]
            target_person_id=None,
            created_at_ns=event.created_at_ns,
            deadline_ms=deadline_ms,
            expires_at_ns=event.created_at_ns + deadline_ms * 1_000_000,
            commitment_level=commitment_level,  # type: ignore[arg-type]
            interruptibility="immediate",
            safety_class="passive",
            affect=affect,
            source="local_reflex",
            priority=10,
        )
