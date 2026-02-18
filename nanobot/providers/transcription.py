"""Voice transcription providers using Groq API or local Whisper."""

import os
from pathlib import Path
from typing import Any

import httpx
from loguru import logger


def get_transcription_provider(
    provider: str = "local",
    groq_api_key: str | None = None,
    model_size: str = "base",
    device: str = "auto",
    compute_type: str = "auto",
):
    """
    Get transcription provider based on configuration.

    Args:
        provider: "local" or "groq"
        groq_api_key: API key for Groq (required if provider is "groq")
        model_size: Whisper model size for local transcription
        device: Device for local transcription
        compute_type: Compute type for local transcription

    Returns:
        Transcription provider instance (GroqTranscriptionProvider or LocalWhisperTranscriptionProvider)
    """
    if provider == "groq":
        return GroqTranscriptionProvider(api_key=groq_api_key)
    else:  # default to local
        return LocalWhisperTranscriptionProvider(
            model_size=model_size,
            device=device,
            compute_type=compute_type,
        )


class GroqTranscriptionProvider:
    """
    Voice transcription provider using Groq's Whisper API.

    Groq offers extremely fast transcription with a generous free tier.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.api_url = "https://api.groq.com/openai/v1/audio/transcriptions"

    async def transcribe(self, file_path: str | Path) -> str:
        """
        Transcribe an audio file using Groq.

        Args:
            file_path: Path to the audio file.

        Returns:
            Transcribed text.
        """
        if not self.api_key:
            logger.warning("Groq API key not configured for transcription")
            return ""

        path = Path(file_path)
        if not path.exists():
            logger.error(f"Audio file not found: {file_path}")
            return ""

        try:
            async with httpx.AsyncClient() as client:
                with open(path, "rb") as f:
                    files = {
                        "file": (path.name, f),
                        "model": (None, "whisper-large-v3"),
                    }
                    headers = {
                        "Authorization": f"Bearer {self.api_key}",
                    }

                    response = await client.post(
                        self.api_url, headers=headers, files=files, timeout=60.0
                    )

                    response.raise_for_status()
                    data = response.json()
                    return data.get("text", "")

        except Exception as e:
            logger.error(f"Groq transcription error: {e}")
            return ""


class LocalWhisperTranscriptionProvider:
    """
    Voice transcription provider using local Whisper model.

    Uses faster-whisper for efficient local transcription with GPU/CPU support.
    Much faster than openai-whisper and lower memory usage.
    """

    def __init__(self, model_size: str = "base", device: str = "auto", compute_type: str = "auto"):
        """
        Initialize local Whisper transcription provider.

        Args:
            model_size: Model size - "tiny", "base", "small", "medium", "large-v2", "large-v3"
                       (default: "base" - good balance of speed and accuracy)
            device: Device to run on - "cpu", "cuda", or "auto" (auto-detect GPU)
            compute_type: Computation type - "int8", "float16", "float32", or "auto"
                         (int8 is faster, float16/32 more accurate)
        """
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load_model(self):
        """Lazy-load the Whisper model on first use."""
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            logger.error("faster-whisper not installed. Install with: pip install faster-whisper")
            return None

        try:
            # Auto-detect device if needed
            device = self.device
            compute_type = self.compute_type

            if device == "auto":
                try:
                    import torch

                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"

            if compute_type == "auto":
                # Use int8 for CPU (faster), float16 for GPU (better quality)
                compute_type = "int8" if device == "cpu" else "float16"

            logger.info(
                f"Loading Whisper model '{self.model_size}' on {device} "
                f"with {compute_type} precision..."
            )

            self._model = WhisperModel(
                self.model_size,
                device=device,
                compute_type=compute_type,
                download_root=str(Path.home() / ".cache" / "whisper"),
            )

            logger.info("Whisper model loaded successfully")
            return self._model

        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            return None

    async def transcribe(self, file_path: str | Path) -> str:
        """
        Transcribe an audio file using local Whisper model.

        Args:
            file_path: Path to the audio file.

        Returns:
            Transcribed text.
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(f"Audio file not found: {file_path}")
            return ""

        model = self._load_model()
        if model is None:
            return ""

        try:
            # Run transcription in thread pool since faster-whisper is synchronous
            import asyncio

            def _transcribe_sync():
                segments, info = model.transcribe(
                    str(path),
                    beam_size=5,
                    language=None,  # Auto-detect language
                    vad_filter=True,  # Voice activity detection
                )

                # Combine all segments into full text
                text = " ".join(segment.text.strip() for segment in segments)
                return text.strip()

            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, _transcribe_sync)

            if text:
                logger.info(f"Transcribed audio locally: {text[:50]}...")

            return text

        except Exception as e:
            logger.error(f"Local Whisper transcription error: {e}")
            return ""
