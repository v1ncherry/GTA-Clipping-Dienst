"""Transkription mit Wort-Zeitstempeln. Backends:

- faster-whisper : Windows/Linux, CPU (int8) oder NVIDIA-GPU
- mlx            : Mac mit Apple Silicon (mlx-whisper), sehr schnell
- whisper.cpp    : externes Binary, z.B. mit Vulkan-Build für AMD-Grafikkarten (RX 9070 XT) oder Metal (Mac)
- none           : keine Untertitel
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .util import IS_MAC_ARM, NO_WINDOW, ffmpeg_bin

log = logging.getLogger("clipper.transcribe")


@dataclass
class Word:
    start: float
    end: float
    text: str

    def to_list(self) -> list:
        return [round(self.start, 3), round(self.end, 3), self.text]


class Transcriber:
    name = "none"

    def transcribe(self, wav: str, language: str | None) -> list[Word]:
        return []


class FasterWhisper(Transcriber):
    name = "faster-whisper"

    def __init__(self, cfg: dict):
        from faster_whisper import WhisperModel

        t = cfg["transcription"]
        device = t.get("device", "auto")
        compute = t.get("compute_type", "int8")
        if device == "auto":
            device = "cpu"
            try:
                import ctranslate2

                if ctranslate2.get_cuda_device_count() > 0:
                    device, compute = "cuda", "float16"
            except Exception:  # noqa: BLE001
                pass
        log.info("Lade faster-whisper '%s' (%s, %s) – beim ersten Mal wird das Modell heruntergeladen",
                 t["model"], device, compute)
        self.model = WhisperModel(t["model"], device=device, compute_type=compute,
                                  download_root=str(Path(cfg["data_dir"]) / "models" / "whisper"))
        self.ffmpeg = ffmpeg_bin(cfg)

    def _load_audio(self, path: str):
        """Audio per ffmpeg als 16-kHz-Mono laden. faster-whisper würde sonst PyAV nehmen, dessen API sich
        zwischen Versionen ändert (faster-whisper 1.2.1 + av 19 → 'unexpected keyword argument metadata_errors')."""
        import numpy as np

        proc = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path,
                               "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                              capture_output=True, check=True, creationflags=NO_WINDOW, timeout=300)
        return np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0

    def transcribe(self, wav: str, language: str | None) -> list[Word]:
        segments, _info = self.model.transcribe(self._load_audio(wav), language=language or None,
                                                word_timestamps=True, vad_filter=True, beam_size=5,
                                                condition_on_previous_text=False)
        words = []
        for seg in segments:
            for w in seg.words or []:
                if w.word.strip():
                    words.append(Word(float(w.start), float(w.end), w.word.strip()))
        return words


class MLXWhisper(Transcriber):
    name = "mlx"

    def __init__(self, cfg: dict):
        import mlx_whisper  # noqa: F401

        self.repo = cfg["transcription"].get("mlx_model", "mlx-community/whisper-large-v3-turbo")

    def transcribe(self, wav: str, language: str | None) -> list[Word]:
        import mlx_whisper

        res = mlx_whisper.transcribe(wav, path_or_hf_repo=self.repo, word_timestamps=True, language=language or None,
                                     condition_on_previous_text=False)
        words = []
        for seg in res.get("segments", []):
            for w in seg.get("words", []):
                if w["word"].strip():
                    words.append(Word(float(w["start"]), float(w["end"]), w["word"].strip()))
        return words


class WhisperCpp(Transcriber):
    name = "whisper.cpp"

    def __init__(self, cfg: dict):
        t = cfg["transcription"]
        self.bin = t.get("whispercpp_bin") or shutil.which("whisper-cli") or shutil.which("whisper-cpp")
        self.model = t.get("whispercpp_model")
        if not self.bin or not Path(self.bin).exists():
            raise RuntimeError("whisper.cpp: 'whispercpp_bin' nicht gefunden")
        if not self.model or not Path(self.model).exists():
            raise RuntimeError("whisper.cpp: 'whispercpp_model' (ggml-*.bin) nicht gefunden")

    def transcribe(self, wav: str, language: str | None) -> list[Word]:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            cmd = [self.bin, "-m", self.model, "-f", wav, "-l", language or "auto", "-oj", "-of", str(out),
                   "-ml", "1", "-sow", "-np"]
            subprocess.run(cmd, check=True, capture_output=True, creationflags=NO_WINDOW, timeout=900)
            data = json.loads(Path(str(out) + ".json").read_text(encoding="utf-8", errors="replace"))
        words = []
        for seg in data.get("transcription", []):
            text = seg.get("text", "").strip()
            off = seg.get("offsets", {})
            if text and "from" in off:
                words.append(Word(off["from"] / 1000.0, off["to"] / 1000.0, text))
        return words


_LOCK = threading.Lock()
_INSTANCE: Transcriber | None = None


def get_transcriber(cfg: dict) -> Transcriber:
    """Bestes verfügbares Backend wählen (einmal pro Prozess, Modell bleibt im Speicher)."""
    global _INSTANCE
    with _LOCK:
        if _INSTANCE is not None:
            return _INSTANCE
        backend = cfg["transcription"].get("backend", "auto")
        if backend == "auto":
            order = ["mlx", "whisper.cpp", "faster-whisper"] if IS_MAC_ARM else ["whisper.cpp", "faster-whisper"]
        else:
            order = [backend]
        classes = {"faster-whisper": FasterWhisper, "mlx": MLXWhisper, "whisper.cpp": WhisperCpp,
                   "none": Transcriber}
        reasons = []
        for name in order:
            try:
                _INSTANCE = classes[name](cfg) if name != "none" else Transcriber()
                log.info("Transkription: %s", _INSTANCE.name)
                return _INSTANCE
            except Exception as exc:  # noqa: BLE001
                level = logging.WARNING if backend != "auto" else logging.DEBUG
                log.log(level, "Transkriptions-Backend %s nicht verfügbar: %s", name, exc)
                reasons.append(f"{name}: {exc}")
        log.warning("Keine Transkription verfügbar – Clips bekommen keine Untertitel (%s)", "; ".join(reasons))
        _INSTANCE = Transcriber()
        return _INSTANCE


def transcribe_locked(cfg: dict, wav: str, language: str | None) -> list[Word]:
    tr = get_transcriber(cfg)
    if str(language or "").lower() in ("", "auto"):  # „Automatisch“: Whisper erkennt die Sprache selbst
        language = None
    with _LOCK:  # ein Modell, eine Transkription gleichzeitig
        return tr.transcribe(wav, language)


def smart_trim(words: list[Word], start: float, end: float, min_len: float, max_len: float) -> tuple[float, float]:
    """Clip-Grenzen an Satzanfänge/-enden schieben, damit nicht mitten im Wort geschnitten wird."""
    if not words:
        return start, end
    words = sorted(words, key=lambda w: w.start)
    starts = [words[0].start]
    ends = []
    for prev, cur in zip(words, words[1:]):
        if cur.start - prev.end > 0.6 or prev.text.rstrip().endswith((".", "!", "?")):
            starts.append(cur.start)
            ends.append(prev.end)
    ends.append(words[-1].end)

    new_start = start
    cands = [s for s in starts if start - 5.0 <= s <= start + 1.0]
    if cands:
        new_start = max(cands) - 0.15
    else:
        for w in words:
            if w.start < start < w.end:
                new_start = w.start - 0.15
                break
    new_end = end
    cands = [e for e in ends if end - 1.0 <= e <= end + 5.0]
    if cands:
        new_end = min(cands) + 0.35
    else:
        for w in words:
            if w.start < end < w.end:
                new_end = w.end + 0.25
                break
    new_start = max(0.0, new_start)
    if new_end - new_start > max_len:
        new_end = new_start + max_len
    if new_end - new_start < min_len:
        return start, end
    return round(new_start, 3), round(new_end, 3)
