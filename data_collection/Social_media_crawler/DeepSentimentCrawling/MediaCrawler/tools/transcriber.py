import os
import logging
import shutil
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger("Transcriber")

# Check for FunASR
try:
    from funasr import AutoModel
    FUNASR_AVAILABLE = True
except ImportError:
    FUNASR_AVAILABLE = False

class VideoTranscriber:
    _model = None

    @classmethod
    def get_model(cls):
        if not FUNASR_AVAILABLE:
            logger.error("funasr not installed. Please run: pip install funasr modelscope torchaudio")
            return None

        try:
            import config  # MediaCrawler config (optional)
        except Exception:
            config = None  # type: ignore[assignment]
            
        if cls._model is None:
            logger.info("Loading SenseVoiceSmall model ...")
            try:
                import torch
                device = "cpu"
                configured_device = (getattr(config, "ASR_DEVICE", "auto") if config else "auto") or "auto"
                configured_device = str(configured_device).strip().lower()
                if configured_device == "cuda":
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                elif configured_device == "mps":
                    device = "mps" if torch.backends.mps.is_available() else "cpu"
                elif configured_device == "cpu":
                    device = "cpu"
                else:
                    if torch.cuda.is_available():
                        device = "cuda"
                    elif torch.backends.mps.is_available():
                        device = "mps"
                    else:
                        device = "cpu"
                
                logger.info(f"Using device: {device}")

                # Load SenseVoiceSmall
                # Use absolute local path to prevent re-downloading
                model_path = "/Users/pan/.cache/modelscope/hub/models/iic/SenseVoiceSmall"
                if not os.path.exists(model_path):
                     # Fallback to ID if local path doesn't exist (though we just verified it does)
                    model_path = "iic/SenseVoiceSmall"

                cls._model = AutoModel(
                    model=model_path,
                    trust_remote_code=True,
                    device=device,
                    disable_update=True
                )
                logger.info(f"SenseVoiceSmall model loaded from {model_path}")
            except Exception as e:
                logger.error(f"Failed to load SenseVoiceSmall model: {e}")
                return None
        return cls._model

    @staticmethod
    def _ffmpeg_split_to_wav(input_path: str, segment_seconds: int) -> list[str]:
        if segment_seconds <= 0:
            return []
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("ffmpeg not found; ASR_SPLIT_SECONDS ignored.")
            return []

        tmpdir = tempfile.mkdtemp(prefix="asr_segments_")
        pattern = os.path.join(tmpdir, "seg_%05d.wav")

        # Decode + resample to mono 16k to reduce downstream compute/memory.
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            input_path,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "segment",
            "-segment_time",
            str(int(segment_seconds)),
            "-reset_timestamps",
            "1",
            pattern,
        ]
        try:
            subprocess.run(cmd, check=True)
        except Exception as e:
            logger.warning(f"ffmpeg split failed; falling back to single-file ASR: {e}")
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass
            return []

        segments = []
        for name in sorted(os.listdir(tmpdir)):
            if name.endswith(".wav"):
                segments.append(os.path.join(tmpdir, name))
        if not segments:
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass
        return segments

    @staticmethod
    def _ffmpeg_convert_to_wav(input_path: str) -> tuple[str, str]:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.warning("ffmpeg not found; using original file for ASR.")
            return "", ""

        tmpdir = tempfile.mkdtemp(prefix="asr_wav_")
        out = os.path.join(tmpdir, "audio.wav")
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            input_path,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            out,
        ]
        try:
            subprocess.run(cmd, check=True)
            if os.path.exists(out):
                return out, tmpdir
        except Exception as e:
            logger.warning(f"ffmpeg convert failed; using original file for ASR: {e}")
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
        return "", ""

    @staticmethod
    def transcribe_video(video_path: str) -> str:
        """
        Transcribe a video file using SenseVoiceSmall.
        """
        if not os.path.exists(video_path):
            logger.error(f"Video file not found: {video_path}")
            return ""

        model = VideoTranscriber.get_model()
        if not model:
            return ""

        tmpdirs_to_cleanup: list[str] = []
        try:
            try:
                import config  # MediaCrawler config (optional)
            except Exception:
                config = None  # type: ignore[assignment]

            batch_size_s = int(getattr(config, "ASR_BATCH_SIZE_S", 20) if config else 20)
            if batch_size_s <= 0:
                batch_size_s = 20
            split_seconds = int(getattr(config, "ASR_SPLIT_SECONDS", 0) if config else 0)
            if split_seconds < 0:
                split_seconds = 0

            logger.info(f"Starting transcription for: {video_path}")
            segments = VideoTranscriber._ffmpeg_split_to_wav(video_path, split_seconds)
            if segments:
                tmpdirs_to_cleanup.append(os.path.dirname(segments[0]))
                inputs = segments
            else:
                wav_path, tmpdir = VideoTranscriber._ffmpeg_convert_to_wav(video_path)
                if tmpdir:
                    tmpdirs_to_cleanup.append(tmpdir)
                inputs = [wav_path] if wav_path else [video_path]

            texts: list[str] = []
            for inp in inputs:
                # SenseVoice uses 'generate' but arguments might slightly differ.
                res = model.generate(
                    input=inp,
                    cache={},
                    language="auto",  # auto detect language
                    use_itn=True,
                    batch_size_s=batch_size_s,
                    merge_vad=True,
                    merge_length_s=15,
                )

                if res and isinstance(res, list):
                    t = (res[0].get("text", "") or "").strip()
                    if t:
                        texts.append(t)

            text = " ".join(texts).strip()
            
            # Clean up text (SenseVoice sometimes includes tags like <|zh|>)
            import re
            text = re.sub(r'<\|.*?\|>', '', text).strip()
            
            logger.info(f"Transcription complete. Length: {len(text)} chars")
            return text
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""
        finally:
            for d in tmpdirs_to_cleanup:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass
