"""
Chinese TTS + STT example using Kokoro (text→speech) and Deepgram nova-2 (speech→text).

Usage:
    python examples/chinese_tts_stt.py

Requirements:
    - DEEPGRAM_API_KEY in .env (for STT)
    - kokoro installed: uv sync --extra kokoro  (or: uv add kokoro)
    - sounddevice installed: uv add sounddevice

The script:
  1. Synthesizes Chinese text → PCM audio via Kokoro (zf_xiaobei voice, lang_code=z)
  2. Saves the audio to chinese_output.wav
  3. Sends the audio to Deepgram nova-2 for Chinese STT → prints the transcript
"""

import asyncio
import os
import wave
import struct
import sys

# ---------------------------------------------------------------------------
# 1. TEXT → SPEECH  (Kokoro)
# ---------------------------------------------------------------------------

CHINESE_TEXT = "你好，我是一个人工智能语音助手。今天天气怎么样？"

KOKORO_SAMPLE_RATE = 24000   # Kokoro output rate
OUTPUT_WAV = "chinese_output.wav"


def synthesize_to_wav(text: str, voice: str = "zf_xiaobei", lang_code: str = "z") -> str:
    """Synthesize Chinese text to a WAV file. Returns the output path."""
    from kokoro import KPipeline
    import numpy as np

    print(f"[TTS] Synthesizing: {text!r}")
    pipeline = KPipeline(lang_code=lang_code)

    all_pcm = []
    for _gs, _ps, audio in pipeline(text, voice=voice):
        import torch
        if isinstance(audio, torch.Tensor):
            audio = audio.cpu().numpy()
        pcm = (audio * 32767).clip(-32768, 32767).astype("int16")
        all_pcm.append(pcm)

    if not all_pcm:
        raise RuntimeError("Kokoro produced no audio")

    import numpy as np
    combined = np.concatenate(all_pcm)

    with wave.open(OUTPUT_WAV, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)           # 16-bit
        wf.setframerate(KOKORO_SAMPLE_RATE)
        wf.writeframes(combined.tobytes())

    duration = len(combined) / KOKORO_SAMPLE_RATE
    print(f"[TTS] Saved {duration:.2f}s of audio to {OUTPUT_WAV!r}")
    return OUTPUT_WAV


# ---------------------------------------------------------------------------
# 2. SPEECH → TEXT  (Deepgram nova-2, Chinese)
# ---------------------------------------------------------------------------

async def transcribe_wav(wav_path: str, language: str = "zh") -> str:
    """Send a WAV file to Deepgram nova-2 and return the transcript."""
    from deepgram import AsyncDeepgramClient

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise EnvironmentError("DEEPGRAM_API_KEY is not set")

    client = AsyncDeepgramClient(api_key=api_key)

    with open(wav_path, "rb") as f:
        audio_data = f.read()

    print(f"[STT] Sending {wav_path!r} to Deepgram nova-2 (language={language!r})...")
    response = await client.listen.v1.media.transcribe_file(
        request=audio_data,
        model="nova-2",
        language=language,
        smart_format=True,
        punctuate=True,
    )

    transcript = (
        response.results.channels[0].alternatives[0].transcript
        if response.results and response.results.channels
        else ""
    )
    print(f"[STT] Transcript: {transcript!r}")
    return transcript


# ---------------------------------------------------------------------------
# 3. Main
# ---------------------------------------------------------------------------

async def main() -> None:
    # Load .env if python-dotenv is available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    # Step 1: TTS
    wav_path = synthesize_to_wav(CHINESE_TEXT)

    # Optional: play the audio if sounddevice is available
    try:
        import sounddevice as sd
        import numpy as np
        with wave.open(wav_path, "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            rate = wf.getframerate()
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        print(f"[Play] Playing audio ({rate} Hz)...")
        sd.play(audio, samplerate=rate)
        sd.wait()
    except ImportError:
        print("[Play] sounddevice not installed — skipping playback (WAV saved to disk)")

    # Step 2: STT
    transcript = await transcribe_wav(wav_path)

    # Step 3: Compare
    print()
    print("─" * 50)
    print(f"Original:   {CHINESE_TEXT}")
    print(f"Transcript: {transcript}")
    print("─" * 50)


if __name__ == "__main__":
    asyncio.run(main())
