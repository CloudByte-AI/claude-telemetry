"""
Generate the bundled alert.wav used by the PermissionRequest hook.

Produces a short, attention-grabbing two-tone alert:
  - 200 ms of 1000 Hz
  -  60 ms silence
  - 300 ms of 1200 Hz
  -  60 ms silence

Format: 16-bit PCM, 22050 Hz, mono.  Total ~0.62 s, ~26 KB on disk.

Run from the project root:
    uv run python scripts/generate_alert_wav.py

Stdlib only — no extra dependencies.  Re-run any time the tone needs tweaking;
the alert.wav file itself is committed to the repo and is what the hook
actually plays at runtime.
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

SAMPLE_RATE = 22050        # Hz
BIT_DEPTH   = 16           # bits per sample
CHANNELS    = 1            # mono

# Tone envelope (linear ramp) avoids the audible "click" you'd otherwise get
# at the start/end of each sine wave.  RAMP_MS is short enough not to colour
# the perceived pitch.
RAMP_MS = 8                # fade-in/out window per tone
AMPLITUDE = 0.6            # 0..1 — leaves headroom so peaks don't clip


def _tone(freq_hz: float, duration_ms: int) -> list[int]:
    """Return a list of 16-bit signed PCM samples for a single sine tone
    with a short linear ramp at each end."""
    n_samples = int(SAMPLE_RATE * duration_ms / 1000)
    ramp_n = int(SAMPLE_RATE * RAMP_MS / 1000)
    peak = int(32767 * AMPLITUDE)

    out: list[int] = []
    for i in range(n_samples):
        sample = math.sin(2.0 * math.pi * freq_hz * i / SAMPLE_RATE)
        # Linear fade in for the first ramp_n samples, fade out for the last.
        if i < ramp_n:
            sample *= i / ramp_n
        elif i >= n_samples - ramp_n:
            sample *= (n_samples - i) / ramp_n
        out.append(int(sample * peak))
    return out


def _silence(duration_ms: int) -> list[int]:
    return [0] * int(SAMPLE_RATE * duration_ms / 1000)


def build_samples() -> list[int]:
    """Assemble the two-tone alert in sample order."""
    return (
        _tone(1000, 200)
        + _silence(60)
        + _tone(1200, 300)
        + _silence(60)
    )


def write_wav(path: Path) -> None:
    """Write the alert samples to `path` as a 16-bit mono WAV."""
    samples = build_samples()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(BIT_DEPTH // 8)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    print(f"Wrote {path}  ({len(samples)} samples, {path.stat().st_size} bytes)")


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    out_path = project_root / "src" / "assets" / "sounds" / "alert.wav"
    write_wav(out_path)


if __name__ == "__main__":
    main()
