"""Synchronise visual/social state with dialogue output without joint control."""

from __future__ import annotations

from .contracts import DialogueEvent, SocialMotionIntent


class ExpressionCoordinator:
    def __init__(self, doll_adapter, a3_adapter=None) -> None:
        self.doll = doll_adapter
        self.a3 = a3_adapter

    def execute_intents(self, intents: tuple[SocialMotionIntent, ...] | list[SocialMotionIntent]) -> None:
        for intent in intents:
            self.doll.execute(intent)
            if self.a3 is not None:
                self.a3.execute(intent)

    def on_dialogue_event(self, event: DialogueEvent):
        if event.type == "cloud_state":
            self.doll.apply_cloud_state(str(event.payload.get("state", "idle")))
            return None
        if event.type == "response_audio":
            return self.doll.play_pcm(
                event.payload["pcm"],
                int(event.payload["sample_rate"]),
                int(event.payload["channels"]),
                int(event.payload["sample_width"]),
            )
        if event.type == "error":
            self.doll.apply_cloud_state("error")
        return None

    def interrupt(self) -> None:
        self.doll.interrupt()

    def health(self) -> dict:
        return {
            "doll": self.doll.health(),
            "a3": self.a3.health() if self.a3 is not None else None,
        }
