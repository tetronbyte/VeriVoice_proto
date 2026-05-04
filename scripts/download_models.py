"""Pre-download all ML model weights so they are cached locally.

Run this once before starting the server:
    python scripts/download_models.py

Downloads:
  1. ECAPA-TDNN (speechbrain/spkrec-ecapa-voxceleb) — ~90MB
  2. OpenAI Whisper large-v3 — ~3GB
  3. w2v-BERT 2.0 Swahili ASR — ~4.5GB
"""

import sys
from pathlib import Path

# Add project root to path so app imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings


def download_ecapa():
    """Download ECAPA-TDNN model files."""
    print("[1/3] Downloading ECAPA-TDNN speaker verification model...")
    import huggingface_hub
    import shutil

    savedir = Path(str(settings.ECAPA_SOURCE).replace("/", "_"))
    savedir.mkdir(parents=True, exist_ok=True)

    files = [
        "hyperparams.yaml",
        "embedding_model.ckpt",
        "mean_var_norm_emb.ckpt",
        "classifier.ckpt",
        "label_encoder.txt",
    ]
    for f in files:
        dest = savedir / f
        if dest.exists():
            print(f"  [cached] {f}")
            continue
        print(f"  Downloading {f}...")
        cached = huggingface_hub.hf_hub_download(
            repo_id=settings.ECAPA_SOURCE, filename=f
        )
        shutil.copy(str(cached), str(dest))
    print("  Done.\n")


def download_whisper():
    """Download Whisper model weights."""
    print(f"[2/3] Downloading Whisper {settings.WHISPER_MODEL}...")
    import whisper

    whisper.load_model(settings.WHISPER_MODEL, device="cpu")
    print("  Done.\n")


def download_swahili():
    """Download w2v-BERT Swahili ASR model."""
    print(f"[3/3] Downloading {settings.SWAHILI_ASR_MODEL}...")
    from transformers import Wav2Vec2BertForCTC, Wav2Vec2BertProcessor

    Wav2Vec2BertProcessor.from_pretrained(settings.SWAHILI_ASR_MODEL)
    Wav2Vec2BertForCTC.from_pretrained(settings.SWAHILI_ASR_MODEL)
    print("  Done.\n")


if __name__ == "__main__":
    print("=" * 60)
    print("VeriVoice — Pre-downloading ML model weights")
    print("=" * 60)
    print()

    download_ecapa()
    download_whisper()
    download_swahili()

    print("=" * 60)
    print("All models downloaded and cached. Ready for server start.")
    print("=" * 60)
