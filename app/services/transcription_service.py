"""Whisper ASR transcription service (PRD Section 9.5)."""

import threading

import librosa
import numpy as np
import torch
import whisper

from app.config import settings


class TranscriptionService:
    """Singleton wrapper around OpenAI Whisper for speech-to-text."""

    _instance: "TranscriptionService | None" = None
    _lock = threading.Lock()

    def __new__(cls, model_name: str | None = None) -> "TranscriptionService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._model = None
                cls._instance._model_name = model_name or settings.WHISPER_MODEL
                cls._instance._device = None
        return cls._instance

    def _ensure_model(self) -> None:
        """Lazy-load the Whisper model on first use."""
        if self._model is not None:
            return
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = device
        self._model = whisper.load_model(self._model_name, device=device)

    def transcribe(self, audio: np.ndarray | str, language: str | None = None) -> str:
        """Transcribe audio to text.

        Args:
            audio: Either a float32 numpy array at 16 kHz or a file path.
            language: Optional ISO 639-1 language code (e.g. "en", "sw").
                      None for auto-detect.

        Returns:
            Transcribed text string.
        """
        self._ensure_model()

        kwargs: dict = {}
        if language is not None:
            kwargs["language"] = language

        if isinstance(audio, np.ndarray):
            audio_input = audio.astype(np.float32)
        else:
            # Load file via librosa (avoids ffmpeg dependency that Whisper requires)
            audio_input, _ = librosa.load(str(audio), sr=16000, mono=True)
            audio_input = audio_input.astype(np.float32)

        result = self._model.transcribe(audio_input, **kwargs)
        return result["text"].strip()

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing with different model sizes)."""
        with cls._lock:
            cls._instance = None
