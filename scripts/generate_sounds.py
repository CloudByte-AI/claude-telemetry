import wave
import struct
import math
import os
from pathlib import Path

def write_wav(filename, samples, sample_rate=44100):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with wave.open(filename, 'wb') as w:
        w.setnchannels(1)  # Mono
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        data = struct.pack(f'<{len(samples)}h', *samples)
        w.writeframes(data)

def generate_chime():
    # A pleasant C-major arpeggio chime: C5 (523Hz), E5 (659Hz), G5 (784Hz)
    sample_rate = 44100
    notes = [
        {"freq": 523.25, "start": 0.0, "duration": 0.2},
        {"freq": 659.25, "start": 0.15, "duration": 0.2},
        {"freq": 783.99, "start": 0.3, "duration": 0.4},
    ]
    
    total_duration = 0.7
    num_samples = int(total_duration * sample_rate)
    samples = [0] * num_samples
    
    for note in notes:
        freq = note["freq"]
        start_idx = int(note["start"] * sample_rate)
        dur_samples = int(note["duration"] * sample_rate)
        
        for i in range(dur_samples):
            idx = start_idx + i
            if idx >= num_samples:
                break
            t = i / sample_rate
            envelope = math.exp(-6 * t)
            val = math.sin(2 * math.pi * freq * t) * envelope
            samples[idx] += val
            
    max_val = max(abs(s) for s in samples) if samples else 1
    if max_val == 0:
        max_val = 1
    normalized = [int((s / max_val) * 16000) for s in samples]
    return normalized

def generate_soft():
    # A single soft, warm notification tone at 440Hz with a sine-squared envelope.
    sample_rate = 44100
    duration = 0.35
    num_samples = int(duration * sample_rate)
    samples = []
    
    for i in range(num_samples):
        t = i / sample_rate
        envelope = math.sin(math.pi * t / duration) ** 2
        val = int(12000 * envelope * math.sin(2 * math.pi * 440 * t))
        samples.append(val)
    return samples

def generate_urgent():
    # Two rapid high-pitched beeps at 880Hz.
    sample_rate = 44100
    beep_dur = 0.08
    gap_dur = 0.04
    
    total_duration = beep_dur * 2 + gap_dur
    num_samples = int(total_duration * sample_rate)
    samples = []
    
    for i in range(num_samples):
        t = i / sample_rate
        in_first_beep = (t < beep_dur)
        in_second_beep = (t >= beep_dur + gap_dur and t < total_duration)
        
        if in_first_beep:
            t_beep = t
            envelope = math.sin(math.pi * t_beep / beep_dur)
            val = int(18000 * envelope * math.sin(2 * math.pi * 880 * t_beep))
        elif in_second_beep:
            t_beep = t - (beep_dur + gap_dur)
            envelope = math.sin(math.pi * t_beep / beep_dur)
            val = int(18000 * envelope * math.sin(2 * math.pi * 880 * t_beep))
        else:
            val = 0
        samples.append(val)
    return samples

if __name__ == "__main__":
    sounds_dir = Path(__file__).parent.parent / "src" / "sounds"
    print(f"Generating built-in sounds in: {sounds_dir}")
    
    write_wav(str(sounds_dir / "chime.wav"), generate_chime())
    print("Generated chime.wav")
    
    write_wav(str(sounds_dir / "soft.wav"), generate_soft())
    print("Generated soft.wav")
    
    write_wav(str(sounds_dir / "urgent.wav"), generate_urgent())
    print("Generated urgent.wav")
    
    print("All sounds generated successfully.")
