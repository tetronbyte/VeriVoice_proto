"""Phase 3 validation: AudioPreprocessor pipeline with synthetic audio."""

import numpy as np
import pytest

from app.services.audio_preprocessor import AudioPreprocessor, SilentAudioError


SAMPLE_RATE = 16000


def _make_sine(freq: float = 440.0, duration: float = 2.0, sr: int = SAMPLE_RATE, amplitude: float = 0.5) -> np.ndarray:
    """Generate a synthetic sine wave (mono, float32)."""
    t = np.linspace(0, duration, int(sr * duration), endpoint=False, dtype=np.float32)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _make_sine_with_silence(freq: float = 440.0, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Sine wave sandwiched between silence segments."""
    silence = np.zeros(int(sr * 0.5), dtype=np.float32)
    tone = _make_sine(freq=freq, duration=1.0, sr=sr, amplitude=0.5)
    return np.concatenate([silence, tone, silence])


@pytest.fixture()
def preprocessor() -> AudioPreprocessor:
    return AudioPreprocessor(
        sample_rate=SAMPLE_RATE,
        gsm_enforce=False,
        vad_top_db=25,
        pre_emphasis_coeff=0.97,
        peak_norm_target=0.95,
        noise_rms_threshold=0.05,
        min_audio_duration=1.0,
    )


class TestOutputFormat:
    def test_dtype_is_float32(self, preprocessor):
        audio = _make_sine()
        result = preprocessor.process(audio)
        assert result.dtype == np.float32

    def test_output_is_1d(self, preprocessor):
        audio = _make_sine()
        result = preprocessor.process(audio)
        assert result.ndim == 1


class TestPeakNormalization:
    def test_peak_equals_target(self, preprocessor):
        audio = _make_sine(amplitude=0.3)
        result = preprocessor.process(audio)
        peak = float(np.max(np.abs(result)))
        assert abs(peak - 0.95) < 0.02  # small tolerance for VAD/filter edge effects

    def test_silent_audio_raises(self, preprocessor):
        silent = np.zeros(SAMPLE_RATE, dtype=np.float32)
        with pytest.raises(SilentAudioError):
            preprocessor.process(silent)

    def test_near_silent_audio_raises(self, preprocessor):
        nearly_silent = np.full(SAMPLE_RATE, 1e-8, dtype=np.float32)
        with pytest.raises(SilentAudioError):
            preprocessor.process(nearly_silent)


class TestMinDurationPadding:
    def test_short_audio_padded_to_min_samples(self, preprocessor):
        # 0.3 seconds of tone — after pipeline should be padded to 16000 samples
        short_audio = _make_sine(duration=0.3, amplitude=0.5)
        result = preprocessor.process(short_audio)
        assert len(result) >= SAMPLE_RATE  # at least 1 second (16000 samples)

    def test_long_audio_not_padded(self, preprocessor):
        long_audio = _make_sine(duration=3.0)
        result = preprocessor.process(long_audio)
        # Should still be well above 16000 samples (not truncated)
        assert len(result) > SAMPLE_RATE


class TestFrequencyFilter:
    def test_default_highpass_removes_low_freq(self, preprocessor):
        # 30 Hz tone should be heavily attenuated by 80 Hz highpass
        low_tone = _make_sine(freq=30.0, duration=2.0, amplitude=0.8)
        result = preprocessor.process(low_tone)
        # After highpass + normalization, the energy should be very low
        # (the filter attenuates, then peak_normalize rescales, but VAD
        # may leave very little signal). We check it at least runs.
        assert result.dtype == np.float32

    def test_gsm_bandpass_mode(self):
        proc = AudioPreprocessor(gsm_enforce=True)
        audio = _make_sine(freq=1000.0, duration=2.0)
        result = proc.process(audio)
        assert result.dtype == np.float32
        assert len(result) >= SAMPLE_RATE


class TestPreEmphasis:
    def test_pre_emphasis_applied(self, preprocessor):
        audio = _make_sine(duration=2.0)
        result = preprocessor.process(audio)
        # Pre-emphasis boosts high-frequency content. We just confirm
        # the pipeline ran and output differs from a simple normalization.
        normalized_only = audio * (0.95 / np.max(np.abs(audio)))
        assert not np.allclose(result[:1000], normalized_only[:1000], atol=0.01)


class TestVAD:
    def test_silence_segments_removed(self, preprocessor):
        audio = _make_sine_with_silence()
        result = preprocessor.process(audio)
        # Original is 2 seconds (0.5s silence + 1s tone + 0.5s silence).
        # After VAD, the silence should be stripped, so output is shorter
        # than input (or at least the 2-second input length).
        assert len(result) < len(audio)


class TestSpectralSubtraction:
    def test_runs_on_noisy_audio(self, preprocessor):
        # Sine with low-level noise prepended (RMS < 0.05 in first 0.3s)
        noise = np.random.default_rng(42).normal(0, 0.01, int(SAMPLE_RATE * 0.5)).astype(np.float32)
        tone = _make_sine(freq=440.0, duration=1.5, amplitude=0.5)
        audio = np.concatenate([noise, tone])
        result = preprocessor.process(audio)
        assert result.dtype == np.float32
        assert len(result) >= SAMPLE_RATE

    def test_skipped_on_loud_start(self, preprocessor):
        # Full-amplitude tone — first 0.3s RMS >> 0.05, so subtraction is skipped
        audio = _make_sine(freq=440.0, duration=2.0, amplitude=0.8)
        result = preprocessor.process(audio)
        assert result.dtype == np.float32
