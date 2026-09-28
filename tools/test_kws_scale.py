import wave
import numpy as np
import sherpa_onnx


MODEL_DIR = (
    "models/kws/sherpa-onnx/"
    "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
)

TOKENS = f"{MODEL_DIR}/tokens.txt"

ENCODER = (
    f"{MODEL_DIR}/"
    "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
)

DECODER = (
    f"{MODEL_DIR}/"
    "decoder-epoch-12-avg-2-chunk-16-left-64.onnx"
)

JOINER = (
    f"{MODEL_DIR}/"
    "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
)

KEYWORDS = f"{MODEL_DIR}/test_wavs/test_keywords.txt"

WAV = f"{MODEL_DIR}/test_wavs/3.wav"


def create_spotter():
    return sherpa_onnx.KeywordSpotter(
        tokens=TOKENS,
        encoder=ENCODER,
        decoder=DECODER,
        joiner=JOINER,
        keywords_file=KEYWORDS,
        num_threads=1,
        sample_rate=16000,
        feature_dim=80,
        max_active_paths=4,
        keywords_score=1.0,
        keywords_threshold=0.25,
        num_trailing_blanks=1,
        provider="cpu",
    )


with wave.open(WAV, "rb") as wf:
    rate = wf.getframerate()

    pcm_int16 = np.frombuffer(
        wf.readframes(wf.getnframes()),
        dtype=np.int16,
    )

print("WAV:", WAV)
print("rate:", rate)
print("samples:", len(pcm_int16))
print()


def run_test(name, samples):

    print("=" * 60)
    print(name)

    print(
        "dtype:",
        samples.dtype,
        "min:",
        samples.min(),
        "max:",
        samples.max(),
    )

    spotter = create_spotter()
    stream = spotter.create_stream()

    chunk_size = 320

    detections = []

    for start in range(0, len(samples), chunk_size):

        chunk = samples[start:start + chunk_size]

        stream.accept_waveform(
            rate,
            chunk,
        )

        while spotter.is_ready(stream):

            spotter.decode_stream(stream)

            result = spotter.get_result(stream)

            if result:
                t = (start + len(chunk)) / rate

                print(
                    f"DETECTED @ {t:.3f}s: {result}"
                )

                detections.append(
                    (t, result)
                )

    print("detections:", detections)


# ============================================================
# TEST A
# ============================================================

run_test(
    "A: float32 WITHOUT normalization",
    pcm_int16.astype(np.float32),
)


# ============================================================
# TEST B
# ============================================================

run_test(
    "B: float32 WITH /32768 normalization",
    pcm_int16.astype(np.float32) / 32768.0,
)
