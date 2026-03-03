"""
DTMF tone generator for IVR navigation.

Generates dual-tone multi-frequency (DTMF) tones as base64-encoded
μ-law 8kHz audio, ready to be fed directly into AudioPlayer.
"""

import base64
import numpy as np

try:
    import audioop
except ImportError:
    import audioop_lts as audioop  # type: ignore  # Python 3.13+

# DTMF frequency pairs (row_hz, col_hz) for standard telephone keypad
DTMF_FREQS: dict[str, tuple[int, int]] = {
    '1': (697, 1209), '2': (697, 1336), '3': (697, 1477),
    '4': (770, 1209), '5': (770, 1336), '6': (770, 1477),
    '7': (852, 1209), '8': (852, 1336), '9': (852, 1477),
    '*': (941, 1209), '0': (941, 1336), '#': (941, 1477),
}

SAMPLE_RATE = 8000


def generate_dtmf_ulaw_b64(digit: str, duration_ms: int = 200) -> str:
    """
    Generate a DTMF tone for the given digit.

    Returns a base64-encoded μ-law 8kHz PCM string suitable for
    direct use as an AudioPlayer chunk (same format as ElevenLabs TTS output).

    Args:
        digit: One of 0-9, *, #
        duration_ms: Tone duration in milliseconds (default 200ms)
    """
    freqs = DTMF_FREQS.get(digit)
    if freqs is None:
        raise ValueError(f"Unknown DTMF digit: {digit!r}. Must be one of: {list(DTMF_FREQS)}")

    f1, f2 = freqs
    n_samples = int(SAMPLE_RATE * duration_ms / 1000)
    t = np.arange(n_samples) / SAMPLE_RATE

    # Sum of two sinusoids, normalized to prevent clipping, scaled to int16
    signal = np.sin(2 * np.pi * f1 * t) + np.sin(2 * np.pi * f2 * t)
    signal_int16 = (signal / 2.0 * 32767).astype(np.int16)

    pcm_bytes = signal_int16.tobytes()
    ulaw_bytes = audioop.lin2ulaw(pcm_bytes, 2)

    return base64.b64encode(ulaw_bytes).decode()
