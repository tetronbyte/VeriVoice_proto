"""Phase 7 validation: TTSService — file generation and audio format."""

import pathlib

import soundfile as sf
import pytest

from app.services.tts_service import TTSService


@pytest.fixture()
def tts(tmp_path) -> TTSService:
    return TTSService(output_dir=str(tmp_path))


class TestSynthesize:
    def test_mp3_file_created(self, tts: TTSService):
        path = tts.synthesize("Hello VeriVoice")
        assert pathlib.Path(path).exists()
        assert path.endswith(".mp3")
        assert pathlib.Path(path).stat().st_size > 0


class TestSynthesizeToWav:
    def test_wav_file_created(self, tts: TTSService):
        path = tts.synthesize_to_wav("Hello VeriVoice")
        assert pathlib.Path(path).exists()
        assert path.endswith(".wav")

    def test_wav_is_16khz(self, tts: TTSService):
        path = tts.synthesize_to_wav("Hello VeriVoice", sample_rate=16000)
        info = sf.info(path)
        assert info.samplerate == 16000

    def test_wav_is_mono(self, tts: TTSService):
        path = tts.synthesize_to_wav("Hello VeriVoice")
        info = sf.info(path)
        assert info.channels == 1

    def test_mp3_cleaned_up(self, tts: TTSService):
        path = tts.synthesize_to_wav("Hello VeriVoice")
        mp3_path = path.replace(".wav", ".mp3")
        assert not pathlib.Path(mp3_path).exists()

    def test_swahili_language(self, tts: TTSService):
        path = tts.synthesize_to_wav("Habari yako", language="sw")
        assert pathlib.Path(path).exists()
        info = sf.info(path)
        assert info.samplerate == 16000
