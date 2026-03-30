"""gTTS text-to-speech service (PRD Section 9.6)."""

import pathlib
import tempfile
import uuid

import librosa
import numpy as np
import soundfile as sf
from gtts import gTTS


class TTSService:
    """Generates audio from text using Google Text-to-Speech."""

    def __init__(self, output_dir: str | None = None):
        if output_dir is None:
            self._output_dir = pathlib.Path(tempfile.gettempdir()) / "verivoice_tts"
        else:
            self._output_dir = pathlib.Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def synthesize(self, text: str, language: str = "en") -> str:
        """Generate an MP3 audio file from text.

        Args:
            text: Text to synthesize.
            language: gTTS language code (e.g. "en", "sw").

        Returns:
            Absolute file path to the generated MP3.
        """
        filename = f"{uuid.uuid4()}.mp3"
        filepath = self._output_dir / filename
        tts = gTTS(text=text, lang=language)
        tts.save(str(filepath))
        return str(filepath.absolute())

    def synthesize_to_wav(self, text: str, language: str = "en", sample_rate: int = 16000) -> str:
        """Generate a 16 kHz WAV audio file from text.

        Args:
            text: Text to synthesize.
            language: gTTS language code.
            sample_rate: Target sample rate (default 16000 Hz).

        Returns:
            Absolute file path to the generated WAV.
        """
        mp3_path = self.synthesize(text, language)

        # Load MP3 via librosa (handles format conversion) and resample
        audio, _ = librosa.load(mp3_path, sr=sample_rate, mono=True)
        audio = audio.astype(np.float32)

        wav_path = mp3_path.replace(".mp3", ".wav")
        sf.write(wav_path, audio, sample_rate)

        # Clean up the intermediate MP3
        pathlib.Path(mp3_path).unlink(missing_ok=True)

        return str(pathlib.Path(wav_path).absolute())
