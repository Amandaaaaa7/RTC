"""Opt-in real-time dialogue integration for the doll robot.

This package is deliberately isolated from the legacy VoiceModule and
ReactiveVoiceModule. Importing it alone does not open ALSA devices, start
network sessions, or change existing robot behaviour.
"""

from .contracts import Affect, DialogueEvent, SocialMotionIntent, VoiceEvent
from .orchestrator import RealtimeVoiceOrchestrator

__all__ = [
    "Affect",
    "DialogueEvent",
    "RealtimeVoiceOrchestrator",
    "SocialMotionIntent",
    "VoiceEvent",
]
