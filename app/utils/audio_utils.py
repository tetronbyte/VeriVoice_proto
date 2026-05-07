"""Audio loading and helper utilities for the preprocessing pipeline."""

import numpy as np
import librosa


class SilentAudioError(Exception):
    """Raised when the audio peak amplitude is below the rejection threshold."""


def load_audio(source: str | np.ndarray, sample_rate: int = 16000) -> np.ndarray:
    """Load audio from a file path or pass through a numpy array.

    Returns a mono float32 numpy array resampled to `sample_rate`.
    """
    if isinstance(source, np.ndarray):
        audio = source.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio

    audio, _ = librosa.load(source, sr=sample_rate, mono=True)
    return audio.astype(np.float32)


def spectral_subtract(
    audio: np.ndarray,
    sample_rate: int,
    noise_rms_threshold: float,
    noise_duration: float = 0.3,
    oversubtract: float = 1.5,
    n_fft: int = 2048,
    hop_length: int = 512,
) -> np.ndarray:
    """Apply spectral subtraction if the leading segment has a quiet noise floor.

    Only applied when the RMS of the first `noise_duration` seconds is below
    `noise_rms_threshold`, to avoid aggressive removal on clean audio.
    """
    noise_samples = int(noise_duration * sample_rate)
    if noise_samples > len(audio):
        return audio

    noise_segment = audio[:noise_samples]
    noise_rms = float(np.sqrt(np.mean(noise_segment ** 2)))
    if noise_rms >= noise_rms_threshold:
        return audio

    # Compute STFT
    stft = librosa.stft(audio, n_fft=n_fft, hop_length=hop_length)
    magnitude = np.abs(stft)
    phase = np.angle(stft)

    # Noise profile from the leading segment
    noise_stft = librosa.stft(noise_segment, n_fft=n_fft, hop_length=hop_length)
    noise_magnitude = np.mean(np.abs(noise_stft), axis=1, keepdims=True)

    # Subtract oversubtract * noise magnitude, floored at zero
    cleaned_magnitude = np.maximum(magnitude - oversubtract * noise_magnitude, 0.0)

    # Reconstruct
    cleaned_stft = cleaned_magnitude * np.exp(1j * phase)
    return librosa.istft(cleaned_stft, hop_length=hop_length, length=len(audio)).astype(np.float32)


def vad_trim(audio: np.ndarray, top_db: int = 25) -> np.ndarray:
    """Extract voiced segments using librosa VAD and concatenate them."""
    intervals = librosa.effects.split(audio, top_db=top_db)
    if len(intervals) == 0:
        raise SilentAudioError("No voiced segments detected by VAD (audio may be noise-only)")
    return np.concatenate([audio[start:end] for start, end in intervals]).astype(np.float32)
