"""Abstract base for keyword spotting backends used by FastReactionModule."""

from abc import ABC, abstractmethod
from typing import Tuple


class KWSBackend(ABC):
    """Keyword spotting backend.

    A backend consumes raw audio frames from MicMonitor and returns the
    detected keyword plus confidence when one is spotted.  All heavy work
    must happen outside the MicMonitor callback thread; FastReactionModule
    takes care of the worker thread.
    """

    @abstractmethod
    def sample_rate(self) -> int:
        """Expected input sample rate in Hz."""
        ...

    @abstractmethod
    def reset(self):
        """Reset any internal streaming state (e.g., between utterances)."""
        ...

    @abstractmethod
    def detect(self, pcm_float32: "numpy.ndarray") -> Tuple[str | None, float]:
        """Run one streaming inference step.

        Args:
            pcm_float32: 1-D float32 PCM, already resampled to ``sample_rate()``
                and normalized to the range [-1.0, 1.0].

        Returns:
            (keyword, confidence) where ``keyword`` is None when nothing is
            detected.  ``confidence`` is in [0.0, 1.0].
        """
        ...

    def finalize_stream(self) -> Tuple[str | None, float]:
        """Optional end-of-stream flush for offline testing.

        Default no-op; streaming real-time inference should not call this.
        Backends that need a tail decode (e.g. sherpa-onnx) can override it.
        """
        return None, 0.0

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend display name."""
        ...

    @property
    @abstractmethod
    def available(self) -> bool:
        """Whether the backend can be instantiated on this platform."""
        ...
