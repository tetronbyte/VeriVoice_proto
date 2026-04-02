"""ASR transcription service — Whisper (English) + w2v-BERT (Swahili).

Uses OpenAI Whisper large-v3 for English and badrex/w2v-bert-2.0-swahili-as
for Swahili.  The Swahili model outperforms Whisper large-v3 on Swahili
speech and is selected automatically when language="sw".
"""

import threading

import librosa
import numpy as np
import torch

from app.config import settings


class TranscriptionService:
    """Singleton wrapper around dual ASR backends.

    - English (and other languages): OpenAI Whisper large-v3
    - Swahili: badrex/w2v-bert-2.0-swahili-as (CTC model via transformers)
    """

    _instance: "TranscriptionService | None" = None
    _lock = threading.Lock()

    def __new__(cls, model_name: str | None = None) -> "TranscriptionService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._whisper_model = None
                cls._instance._whisper_model_name = model_name or settings.WHISPER_MODEL
                cls._instance._sw_model = None
                cls._instance._sw_processor = None
                cls._instance._sw_model_name = settings.SWAHILI_ASR_MODEL
                cls._instance._device = None
        return cls._instance

    def _ensure_whisper(self) -> None:
        """Lazy-load the Whisper model on first use."""
        if self._whisper_model is not None:
            return
        import whisper

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = device
        self._whisper_model = whisper.load_model(self._whisper_model_name, device=device)

    def _ensure_swahili(self) -> None:
        """Lazy-load the w2v-BERT Swahili model on first use."""
        if self._sw_model is not None:
            return
        from transformers import AutoModelForCTC, AutoProcessor

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = device
        self._sw_processor = AutoProcessor.from_pretrained(self._sw_model_name)
        self._sw_model = AutoModelForCTC.from_pretrained(self._sw_model_name).to(device)

    def _load_audio(self, audio: np.ndarray | str) -> np.ndarray:
        """Normalise input to a float32 numpy array at 16 kHz."""
        if isinstance(audio, np.ndarray):
            return audio.astype(np.float32)
        audio_data, _ = librosa.load(str(audio), sr=16000, mono=True)
        return audio_data.astype(np.float32)

    def _transcribe_whisper(self, audio: np.ndarray, language: str | None) -> str:
        """Transcribe using Whisper."""
        self._ensure_whisper()
        kwargs: dict = {}
        if language is not None:
            kwargs["language"] = language
        result = self._whisper_model.transcribe(audio, **kwargs)
        return result["text"].strip()

    def _transcribe_swahili(self, audio: np.ndarray) -> str:
        """Transcribe using w2v-BERT Swahili model (CTC)."""
        self._ensure_swahili()
        inputs = self._sw_processor(
            audio, sampling_rate=16000, return_tensors="pt"
        )
        input_features = inputs.input_features.to(self._device)

        with torch.no_grad():
            logits = self._sw_model(input_features).logits

        predicted_ids = torch.argmax(logits, dim=-1)
        text = self._sw_processor.batch_decode(predicted_ids)[0]
        return text.strip()

    def transcribe(self, audio: np.ndarray | str, language: str | None = None) -> str:
        """Transcribe audio to text, selecting the best model for the language.

        Args:
            audio: Either a float32 numpy array at 16 kHz or a file path.
            language: ISO 639-1 language code. "sw" routes to w2v-BERT;
                      everything else (including None) routes to Whisper.

        Returns:
            Transcribed text string.
        """
        audio_data = self._load_audio(audio)

        if language == "sw":
            return self._transcribe_swahili(audio_data)

        return self._transcribe_whisper(audio_data, language)

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing with different model sizes)."""
        with cls._lock:
            cls._instance = None
