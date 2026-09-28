"""
Emotion engine: maps LLM emotion labels to eye animation parameters
and audio response strategies.
"""

from dataclasses import dataclass


@dataclass
class EmotionProfile:
    """Parameters for a given emotion."""

    label: str
    pupil_scale: float = 1.0
    gaze_jitter_amp: float | None = None
    blink_rate_multiplier: float = 1.0
    preferred_response_type: str = "preset"  # preset | tts


EMOTION_PROFILES: dict[str, EmotionProfile] = {
    "Adoration": EmotionProfile("Adoration", pupil_scale=1.08, blink_rate_multiplier=1.2),
    "Amusement": EmotionProfile("Amusement", pupil_scale=1.06, blink_rate_multiplier=1.3),
    "Anger": EmotionProfile("Anger", pupil_scale=1.12, gaze_jitter_amp=0.012, blink_rate_multiplier=0.6),
    "Awe": EmotionProfile("Awe", pupil_scale=1.10, blink_rate_multiplier=0.5),
    "Awkwardness": EmotionProfile("Awkwardness", pupil_scale=0.98, blink_rate_multiplier=1.4),
    "Boredom": EmotionProfile("Boredom", pupil_scale=0.92, blink_rate_multiplier=0.8),
    "Calmness": EmotionProfile("Calmness", pupil_scale=1.0, blink_rate_multiplier=1.0),
    "Confusion": EmotionProfile("Confusion", pupil_scale=1.04, gaze_jitter_amp=0.008, blink_rate_multiplier=1.1),
    "Contemplation": EmotionProfile("Contemplation", pupil_scale=0.96, blink_rate_multiplier=0.7),
    "Contempt": EmotionProfile("Contempt", pupil_scale=1.02, blink_rate_multiplier=0.9),
    "Craving": EmotionProfile("Craving", pupil_scale=1.08, blink_rate_multiplier=1.1),
    "Desire": EmotionProfile("Desire", pupil_scale=1.08, blink_rate_multiplier=1.1),
    "Disappointment": EmotionProfile("Disappointment", pupil_scale=0.94, blink_rate_multiplier=0.8),
    "Disgust": EmotionProfile("Disgust", pupil_scale=0.96, blink_rate_multiplier=0.7),
    "Excitement": EmotionProfile("Excitement", pupil_scale=1.14, gaze_jitter_amp=0.010, blink_rate_multiplier=1.4),
    "Fear": EmotionProfile("Fear", pupil_scale=1.12, gaze_jitter_amp=0.014, blink_rate_multiplier=0.4),
    "Horror": EmotionProfile("Horror", pupil_scale=1.14, gaze_jitter_amp=0.016, blink_rate_multiplier=0.3),
    "Interest": EmotionProfile("Interest", pupil_scale=1.06, blink_rate_multiplier=1.2),
    "Pain": EmotionProfile("Pain", pupil_scale=0.96, blink_rate_multiplier=0.6),
    "Realization": EmotionProfile("Realization", pupil_scale=1.10, blink_rate_multiplier=0.8),
    "Relief": EmotionProfile("Relief", pupil_scale=1.02, blink_rate_multiplier=1.0),
    "Sadness": EmotionProfile("Sadness", pupil_scale=0.92, blink_rate_multiplier=0.7),
    "Satisfaction": EmotionProfile("Satisfaction", pupil_scale=1.0, blink_rate_multiplier=1.0),
    "Surprise": EmotionProfile("Surprise", pupil_scale=1.16, gaze_jitter_amp=0.010, blink_rate_multiplier=0.3),
    "Sympathy": EmotionProfile("Sympathy", pupil_scale=1.02, blink_rate_multiplier=0.9),
    "Tiredness": EmotionProfile("Tiredness", pupil_scale=0.88, blink_rate_multiplier=0.5),
    "Triumph": EmotionProfile("Triumph", pupil_scale=1.12, blink_rate_multiplier=1.2),
}


def get_profile(emotion: str) -> EmotionProfile:
    """Return the profile for an emotion, falling back to Calmness."""
    return EMOTION_PROFILES.get(emotion, EMOTION_PROFILES["Calmness"])


def allowed_emotions() -> list[str]:
    """Return the ordered list of supported emotion labels."""
    return list(EMOTION_PROFILES.keys())
