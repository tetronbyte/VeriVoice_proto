"""ECAPA-TDNN speaker embedding extraction (PRD Section 9.2)."""

import functools
import pathlib
import platform
import shutil
import threading

import numpy as np
import torch
import torchaudio
import huggingface_hub

# ── Compatibility patches for speechbrain 1.0 + newer deps ──────────────────

# Patch 1: torchaudio >= 2.11 removed list_audio_backends
if not hasattr(torchaudio, "list_audio_backends"):
    torchaudio.list_audio_backends = lambda: ["soundfile"]

# Patch 2: speechbrain passes deprecated `use_auth_token` to huggingface_hub >= 1.0
_original_hf_download = huggingface_hub.hf_hub_download


@functools.wraps(_original_hf_download)
def _patched_hf_download(*args, **kwargs):
    kwargs.pop("use_auth_token", None)
    return _original_hf_download(*args, **kwargs)


huggingface_hub.hf_hub_download = _patched_hf_download

# Patch 3: speechbrain's link_with_strategy defaults to SYMLINK which fails on
# Windows without elevated privileges. Force COPY on Windows.
if platform.system() == "Windows":
    import speechbrain.utils.fetching as _sb_fetching

    _original_link = _sb_fetching.link_with_strategy

    def _patched_link(src, dst, local_strategy):
        src = pathlib.Path(src).absolute()
        dst = pathlib.Path(dst).absolute()
        if src == dst:
            return dst
        dst.unlink(missing_ok=True)
        shutil.copy(str(src), str(dst))
        return dst

    _sb_fetching.link_with_strategy = _patched_link

from speechbrain.inference.speaker import EncoderClassifier

from app.config import settings

_ZERO_NORM_THRESHOLD = 1e-8

# Files that actually exist in the HF repo (no custom.py)
_MODEL_FILES = [
    "hyperparams.yaml",
    "embedding_model.ckpt",
    "mean_var_norm_emb.ckpt",
    "classifier.ckpt",
    "label_encoder.txt",
]


def _download_model(source: str, savedir: str) -> pathlib.Path:
    """Download model files from HuggingFace Hub into savedir."""
    save_path = pathlib.Path(savedir)
    save_path.mkdir(parents=True, exist_ok=True)

    for filename in _MODEL_FILES:
        dest = save_path / filename
        if dest.exists():
            continue
        cached = huggingface_hub.hf_hub_download(repo_id=source, filename=filename)
        shutil.copy(str(cached), str(dest))

    return save_path


class EmbeddingService:
    """Singleton wrapper around SpeechBrain ECAPA-TDNN for 192-dim speaker embeddings."""

    _instance: "EmbeddingService | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "EmbeddingService":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._model = None
                cls._instance._device = None
        return cls._instance

    def _ensure_model(self) -> None:
        """Lazy-load the ECAPA-TDNN model on first use."""
        if self._model is not None:
            return
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = device

        savedir = str(settings.ECAPA_SOURCE).replace("/", "_")
        _download_model(settings.ECAPA_SOURCE, savedir)

        self._model = EncoderClassifier.from_hparams(
            source=savedir,
            savedir=savedir,
            run_opts={"device": device},
        )

    def extract_embedding(self, audio: np.ndarray) -> np.ndarray:
        """Extract a single L2-normalized 192-dim embedding from preprocessed audio.

        Args:
            audio: float32 numpy array at 16 kHz (output of AudioPreprocessor).

        Returns:
            L2-normalized numpy array of shape (192,).

        Raises:
            ValueError: If the embedding norm is below 1e-8 (zero-norm vector).
        """
        self._ensure_model()
        tensor = torch.tensor(audio, dtype=torch.float32).unsqueeze(0).to(self._device)
        with torch.no_grad():
            embedding = self._model.encode_batch(tensor).squeeze().cpu().numpy()
        return self._l2_normalize(embedding)

    def compute_centroid(self, embeddings: list[np.ndarray]) -> np.ndarray:
        """Compute the L2-normalized centroid (mean) of multiple embeddings.

        Args:
            embeddings: List of L2-normalized 192-dim numpy arrays.

        Returns:
            L2-normalized centroid of shape (192,).
        """
        centroid = np.mean(np.stack(embeddings), axis=0)
        return self._l2_normalize(centroid)

    @staticmethod
    def _l2_normalize(vec: np.ndarray) -> np.ndarray:
        norm = float(np.linalg.norm(vec))
        if norm < _ZERO_NORM_THRESHOLD:
            raise ValueError(f"Embedding L2-norm {norm} is below threshold {_ZERO_NORM_THRESHOLD}")
        return (vec / norm).astype(np.float32)

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        with cls._lock:
            cls._instance = None
