"""Keyword spotting backends for the fast reflex layer."""

from voice.kws.base import KWSBackend
from voice.kws.dummy import EnergyDummyKWS

try:
    from voice.kws.sherpa_onnx_kws import SherpaOnnxKWS
except Exception:  # pragma: no cover
    SherpaOnnxKWS = None

try:
    from voice.kws.openwakeword_kws import OpenWakeWordKWS
except Exception:  # pragma: no cover
    OpenWakeWordKWS = None

__all__ = [
    "KWSBackend",
    "EnergyDummyKWS",
    "SherpaOnnxKWS",
    "OpenWakeWordKWS",
]
