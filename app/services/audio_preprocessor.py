"""8-stage audio preprocessing pipeline as defined in PRD Section 13."""

import numpy as np
from scipy.signal import butter, filtfilt

from app.config import settings
from app.utils.audio_utils import load_audio, spectral_subtract, vad_trim


class SilentAudioError(Exception):
    """Raised when the audio peak amplitude is below the rejection threshold."""


class AudioPreprocessor:
    def __init__(
        self,
        sample_rate: int = settings.SAMPLE_RATE,
        gsm_enforce: bool = settings.GSM_ENFORCE,
        vad_top_db: int = settings.VAD_TOP_DB,
        pre_emphasis_coeff: float = settings.PRE_EMPHASIS_COEFF,
        peak_norm_target: float = settings.PEAK_NORM_TARGET,
        noise_rms_threshold: float = settings.NOISE_RMS_THRESHOLD,
        min_audio_duration: float = settings.MIN_AUDIO_DURATION,
    ):
        self.sample_rate = sample_rate
        self.gsm_enforce = gsm_enforce
        self.vad_top_db = vad_top_db
        self.pre_emphasis_coeff = pre_emphasis_coeff
        self.peak_norm_target = peak_norm_target
        self.noise_rms_threshold = noise_rms_threshold
        self.min_samples = int(min_audio_duration * sample_rate)

    def process(self, source: str | np.ndarray) -> np.ndarray:
        """Run the full 8-stage pipeline. Returns float32 numpy array at self.sample_rate."""
        # Stage 1: Load audio at 16 kHz, mono, float32
        audio = load_audio(source, self.sample_rate)

        # Stage 2: Frequency filtering (4th-order Butterworth, zero-phase)
        audio = self._frequency_filter(audio)

        # Stage 3: DC-offset removal
        audio = audio - np.mean(audio)

        # Stage 4: Spectral subtraction (conditional on noise floor)
        audio = spectral_subtract(
            audio,
            sample_rate=self.sample_rate,
            noise_rms_threshold=self.noise_rms_threshold,
        )

        # Stage 5: Pre-emphasis
        audio = self._pre_emphasis(audio)

        # Stage 6: Peak normalization (reject silent files)
        audio = self._peak_normalize(audio)

        # Stage 7: Voice Activity Detection
        audio = vad_trim(audio, top_db=self.vad_top_db)

        # Stage 8: Minimum duration padding
        audio = self._pad_to_min_duration(audio)

        return audio

    def _frequency_filter(self, audio: np.ndarray) -> np.ndarray:
        nyquist = self.sample_rate / 2.0
        if self.gsm_enforce:
            # GSM bandpass: 300–3400 Hz
            low = 300.0 / nyquist
            high = 3400.0 / nyquist
            b, a = butter(4, [low, high], btype="band")
        else:
            # Default: 80 Hz highpass
            cutoff = 80.0 / nyquist
            b, a = butter(4, cutoff, btype="high")
        return filtfilt(b, a, audio).astype(np.float32)

    def _pre_emphasis(self, audio: np.ndarray) -> np.ndarray:
        return np.append(audio[0], audio[1:] - self.pre_emphasis_coeff * audio[:-1]).astype(np.float32)

    def _peak_normalize(self, audio: np.ndarray) -> np.ndarray:
        peak = float(np.max(np.abs(audio)))
        if peak < 1e-6:
            raise SilentAudioError(f"Audio peak amplitude {peak} is below rejection threshold 1e-6")
        return (audio * (self.peak_norm_target / peak)).astype(np.float32)

    def _pad_to_min_duration(self, audio: np.ndarray) -> np.ndarray:
        if len(audio) < self.min_samples:
            padded = np.zeros(self.min_samples, dtype=np.float32)
            padded[: len(audio)] = audio
            return padded
        return audio
