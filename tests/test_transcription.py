"""Phase 7 validation: TranscriptionService — ASR on generated audio."""

import time

import pytest

from app.services.transcription_service import TranscriptionService
from app.services.tts_service import TTSService


@pytest.fixture(scope="module", autouse=True)
def reset_singleton():
    """Ensure we get a fresh singleton with the tiny model for tests."""
    TranscriptionService.reset()
    yield
    TranscriptionService.reset()


@pytest.fixture(scope="module")
def service() -> TranscriptionService:
    # Use tiny model for test speed (large-v3 is ~3GB)
    svc = TranscriptionService(model_name="tiny")
    return svc


@pytest.fixture(scope="module")
def tts(tmp_path_factory) -> TTSService:
    return TTSService(output_dir=str(tmp_path_factory.mktemp("tts")))


@pytest.fixture(scope="module")
def hello_wav(tts: TTSService) -> str:
    """Generate a WAV file saying 'Hello VeriVoice' for transcription tests."""
    return tts.synthesize_to_wav("Hello VeriVoice", language="en")


class TestTranscribeFromFile:
    def test_result_is_nonempty_string(self, service: TranscriptionService, hello_wav: str):
        start = time.perf_counter()
        text = service.transcribe(hello_wav, language="en")
        elapsed = time.perf_counter() - start

        print(f"\n>>> Whisper model load + transcribe time: {elapsed:.2f}s")
        print(f">>> Transcription result: '{text}'")

        assert isinstance(text, str)
        assert len(text) > 0

    def test_transcription_contains_keywords(self, service: TranscriptionService, hello_wav: str):
        text = service.transcribe(hello_wav, language="en").lower()
        # gTTS + Whisper should roundtrip reasonably well on simple English
        assert "hello" in text or "verivoice" in text or "very" in text


class TestTranscribeFromArray:
    def test_numpy_input(self, service: TranscriptionService, hello_wav: str):
        import librosa
        audio, _ = librosa.load(hello_wav, sr=16000, mono=True)
        text = service.transcribe(audio, language="en")

        print(f"\n>>> Array transcription: '{text}'")

        assert isinstance(text, str)
        assert len(text) > 0


class TestSingleton:
    def test_same_instance(self):
        a = TranscriptionService(model_name="tiny")
        b = TranscriptionService(model_name="tiny")
        assert a is b
