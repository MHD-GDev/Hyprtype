#!/usr/bin/env python3
"""Device-aware microphone calibration and conservative recording normalization."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave
from array import array
from pathlib import Path

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
PROFILE_FILE = CONFIG_HOME / "hyprtype" / "microphones.json"
TARGET_RMS = 0.12
MAX_GAIN = 4.0
MIN_GAIN = 0.5


def default_source_identity() -> str:
    """Return a stable PipeWire node name, not a machine-specific numeric ID."""
    try:
        result = subprocess.run(
            ["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"],
            capture_output=True, text=True, timeout=3, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "default"
    for key in ("node.name", "device.name", "device.serial"):
        match = re.search(r'\b' + re.escape(key) + r'\s*=\s*"([^"]+)"', result.stdout)
        if match:
            return match.group(1)
    return "default"


def load_profiles() -> dict:
    try:
        data = json.loads(PROFILE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_profile(identity: str, profile: dict) -> None:
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    profiles = load_profiles()
    profiles[identity] = profile
    fd, temp_name = tempfile.mkstemp(prefix=".microphones-", suffix=".tmp", dir=PROFILE_FILE.parent)
    temp = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(profiles, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, PROFILE_FILE)
    finally:
        temp.unlink(missing_ok=True)


def clipping_ratio(path: Path, threshold: int = 32760) -> float:
    """Return the fraction of PCM samples at or very near digital full scale."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise RuntimeError("Calibration recording has an unexpected audio format.")
        frames = wav.readframes(wav.getnframes())
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise RuntimeError("The microphone recording was empty.")
    clipped = sum(1 for sample in samples if abs(sample) >= threshold)
    return clipped / len(samples)


def frame_rms_levels(path: Path, frame_ms: int = 20) -> list[float]:
    """Return short-window RMS levels so pauses do not hide otherwise clear speech."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise RuntimeError("Calibration recording has an unexpected audio format.")
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    frame_size = max(1, rate * frame_ms // 1000)
    levels = []
    for start in range(0, len(samples) - frame_size + 1, frame_size):
        chunk = samples[start:start + frame_size]
        rms = math.sqrt(sum(sample * sample for sample in chunk) / frame_size) / 32768.0
        levels.append(rms)
    if not levels:
        raise RuntimeError("The microphone recording was too short or empty.")
    return levels


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def capture(path: Path, seconds: float, rate: int, channels: int) -> None:
    command = [
        "pw-record", "--rate", str(rate), "--channels", str(channels),
        "--format", "s16", "--container", "wav", str(path),
    ]
    try:
        recorder = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise RuntimeError("pw-record was not found. Install PipeWire.") from exc
    try:
        time.sleep(0.45)
        if recorder.poll() is not None:
            error = recorder.stderr.read().decode(errors="replace").strip() if recorder.stderr else ""
            raise RuntimeError("Could not start microphone recording. " + error)
        time.sleep(seconds)
    finally:
        if recorder.poll() is None:
            recorder.send_signal(signal.SIGINT)
        try:
            recorder.wait(timeout=3)
        except subprocess.TimeoutExpired:
            recorder.kill()
            recorder.wait()
    if not path.is_file() or path.stat().st_size < 1000:
        raise RuntimeError("No microphone audio was captured. Check the default input device.")


def calibrate(rate: int = 16000, channels: int = 1) -> int:
    if channels != 1:
        raise RuntimeError("Microphone calibration currently requires mono recording.")
    if not shutil.which("wpctl") or not shutil.which("pw-record"):
        raise RuntimeError("PipeWire and WirePlumber tools (pw-record and wpctl) are required.")
    identity = default_source_identity()
    print(f"Microphone: {identity}")
    print("\nStep 1 of 2: Please remain quiet for 3 seconds.")
    input("Press Enter when ready...")
    with tempfile.TemporaryDirectory(prefix="hyprtype-mic-") as directory:
        quiet_path = Path(directory) / "quiet.wav"
        voice_path = Path(directory) / "voice.wav"
        capture(quiet_path, 3.0, rate, channels)
        quiet_clipping = clipping_ratio(quiet_path)
        noise_levels = frame_rms_levels(quiet_path)
        # Use a high percentile of the silence sample to account for intermittent
        # laptop fan/keyboard noise without letting one transient dominate.
        noise_rms = percentile(noise_levels, 0.90)
        print(f"Background noise measured: {20 * math.log10(max(noise_rms, 1e-7)):.1f} dBFS")
        print("\nStep 2 of 2: Speak naturally for 5 seconds at your usual distance.")
        input("Press Enter when ready...")
        capture(voice_path, 5.0, rate, channels)
        voice_levels = frame_rms_levels(voice_path)
        voice_clipping = clipping_ratio(voice_path)
        # Speech often contains pauses. Estimate speaking level from the
        # louder quarter of short frames instead of averaging all five seconds.
        louder_frames = sorted(voice_levels)[max(0, int(len(voice_levels) * 0.75)):]
        voice_rms = percentile(louder_frames, 0.50)

    # A small number of full-scale samples can be natural transients. A
    # sustained ratio indicates the ADC/input path is saturating; software
    # normalization cannot recover the waveform once it has clipped.
    clipping_limit = 0.001
    if quiet_clipping >= clipping_limit or voice_clipping >= clipping_limit:
        quiet_pct = quiet_clipping * 100
        voice_pct = voice_clipping * 100
        raise RuntimeError(
            "Microphone input appears to be clipping (digital saturation). "
            f"Near-full-scale samples: silence {quiet_pct:.2f}%, speech {voice_pct:.2f}%. "
            "Lower the microphone's hardware/input gain in your system audio settings "
            "or mixer (for example, alsamixer), then run calibration again. "
            "Hyprtype has not changed your system volume or saved a calibration profile."
        )

    separation = voice_rms / max(noise_rms, 1e-7)
    if voice_rms < max(noise_rms * 1.8, 0.0005):
        noise_db = 20 * math.log10(max(noise_rms, 1e-7))
        voice_db = 20 * math.log10(max(voice_rms, 1e-7))
        raise RuntimeError(
            "Could not clearly separate speech from background noise. "
            f"Measured silence: {noise_db:.1f} dBFS; speech: {voice_db:.1f} dBFS "
            f"(ratio {separation:.1f}x). Check the selected/default microphone, "
            "move closer, reduce background noise, and try again."
        )
    max_safe_gain = min(MAX_GAIN, TARGET_RMS / max(noise_rms * 3.0, 0.001))
    profile = {
        "version": 1,
        "noise_rms": round(noise_rms, 7),
        "voice_rms": round(voice_rms, 7),
        "max_gain": round(max(1.0, max_safe_gain), 3),
        "sample_rate": rate,
        "channels": channels,
        "updated_at": int(time.time()),
    }
    save_profile(identity, profile)
    suggested = min(profile["max_gain"], max(MIN_GAIN, TARGET_RMS / max(voice_rms, 0.001)))
    print("\nMicrophone calibration saved.")
    print(f"Voice level: {20 * math.log10(max(voice_rms, 1e-7)):.1f} dBFS")
    print(f"Maximum safe software gain: {profile['max_gain']:.2f}x")
    print(f"Estimated correction at calibration distance: {suggested:.2f}x")
    print(f"Profile: {PROFILE_FILE}")
    return 0


def normalize_recording(path: Path) -> None:
    """Apply bounded per-recording normalization without touching system volume."""
    identity = default_source_identity()
    profiles = load_profiles()
    profile = profiles.get(identity) or profiles.get("default") or {}
    try:
        with wave.open(str(path), "rb") as wav:
            if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
                return
            params = wav.getparams()
            frames = wav.readframes(wav.getnframes())
    except (OSError, wave.Error):
        return
    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0
    noise_floor = float(profile.get("noise_rms", 0.001))
    if rms <= max(noise_floor * 1.5, 0.0008):
        return
    max_gain = min(MAX_GAIN, max(MIN_GAIN, float(profile.get("max_gain", 2.0))))
    gain = min(max_gain, max(MIN_GAIN, TARGET_RMS / max(rms, 0.001)))
    peak = max(abs(sample) for sample in samples) / 32768.0
    if peak > 0:
        gain = min(gain, 0.89 / peak)
    if abs(gain - 1.0) < 0.03:
        return
    adjusted = array("h", (max(-32768, min(32767, round(sample * gain))) for sample in samples))
    if sys.byteorder != "little":
        adjusted.byteswap()
    temp = path.with_suffix(path.suffix + ".normalized")
    try:
        with wave.open(str(temp), "wb") as output:
            output.setparams(params)
            output.writeframes(adjusted.tobytes())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
