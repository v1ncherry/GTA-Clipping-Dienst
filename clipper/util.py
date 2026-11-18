"""Hilfsfunktionen: Logging, ffmpeg/ffprobe, Encoder-Erkennung."""

from __future__ import annotations

import json
import logging
import logging.handlers
import platform
import re
import shutil
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path

log = logging.getLogger("clipper")

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_MAC_ARM = IS_MAC and platform.machine() in ("arm64", "aarch64")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
NO_CONSOLE = False  # True beim Start ohne Konsole (pythonw.exe) – dann nur ins Logfile schreiben


def setup_logging(data_dir: str, level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_clipper_configured", False):
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    if not NO_CONSOLE:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)
    logfile = Path(data_dir) / "logs" / "clipper.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    fileh = logging.handlers.RotatingFileHandler(logfile, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fileh.setFormatter(fmt)
    root.addHandler(fileh)
    for noisy in ("httpx", "httpx2", "huggingface_hub", "urllib3", "googleapiclient", "streamlink", "websockets",
                  "chat_downloader", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._clipper_configured = True  # type: ignore[attr-defined]


def ffmpeg_bin(cfg: dict) -> str:
    return shutil.which(cfg.get("ffmpeg", "ffmpeg")) or cfg.get("ffmpeg", "ffmpeg")


def ffprobe_bin(cfg: dict) -> str:
    return shutil.which(cfg.get("ffprobe", "ffprobe")) or cfg.get("ffprobe", "ffprobe")


_JOB_LOCK = threading.Lock()
_JOB: int | None = None  # Windows-Job „Kinder sterben mit“ – Handle bleibt bis zum Prozessende offen
_WIN = None


def _win_api():
    global _WIN
    if _WIN is None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        _WIN = (ctypes, k32, ntdll)
    return _WIN


def _kill_on_close_job() -> int:
    """Job mit JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Endet der Clipper – auch per Absturz oder Task-Manager –,
    schließt Windows das (nicht vererbte) Job-Handle und beendet alle Prozesse im Job."""
    global _JOB
    with _JOB_LOCK:
        if _JOB is None:
            ctypes, k32, _ntdll = _win_api()
            from ctypes import wintypes

            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_uint64 * 6),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            job = k32.CreateJobObjectW(None, None)
            if not job:
                raise OSError(ctypes.get_last_error(), "CreateJobObject fehlgeschlagen")
            info = Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # Extended = 9
                err = ctypes.get_last_error()
                k32.CloseHandle(job)
                raise OSError(err, "SetInformationJobObject fehlgeschlagen")
            _JOB = job
        return _JOB


def popen_guarded(cmd: list[str], **kw) -> subprocess.Popen:
    """Wie subprocess.Popen – unter Windows aber an einen Job gebunden, damit der Prozess samt seinen Kindern
    (z.B. python -m streamlink → Python-Prozess) mit dem Clipper stirbt. Sonst laufen streamlink/ffmpeg nach einem
    Absturz weiter und füllen den Puffer-Ordner endlos (~4 GB/h pro Streamer).

    Nur für Prozesse, die nicht länger leben dürfen als der Clipper – nie für Browser o.ä.
    Der Prozess startet angehalten und läuft erst los, wenn er im Job ist (sonst könnten Enkel entwischen).
    """
    if not IS_WINDOWS:
        return subprocess.Popen(cmd, **kw)
    kw["creationflags"] = kw.get("creationflags", 0) | 0x00000004  # CREATE_SUSPENDED
    proc = subprocess.Popen(cmd, **kw)
    handle = int(proc._handle)  # type: ignore[attr-defined]
    try:
        ctypes, k32, ntdll = _win_api()
        if not k32.AssignProcessToJobObject(_kill_on_close_job(), handle):
            log.warning("Prozess %s nicht an den Job gebunden (Fehler %d) – läuft nach Absturz evtl. weiter",
                        cmd[0], ctypes.get_last_error())
    except Exception as exc:  # noqa: BLE001
        log.warning("Prozess %s nicht an den Job gebunden: %s", cmd[0], exc)
    finally:
        status = _win_api()[2].NtResumeProcess(handle)
        if status != 0:
            proc.kill()
            raise OSError(f"Prozess {cmd[0]} ließ sich nicht starten (NtResumeProcess 0x{status & 0xFFFFFFFF:08X})")
    return proc


def run(cmd: list[str], timeout: float | None = None, check: bool = True, **kw) -> subprocess.CompletedProcess:
    log.debug("run: %s", " ".join(map(str, cmd)))
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW, **kw)
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[-2000:]
        raise RuntimeError(f"Befehl fehlgeschlagen ({proc.returncode}): {cmd[0]} ... \n{err}")
    return proc


def probe(cfg: dict, path: str | Path) -> dict:
    out = run([ffprobe_bin(cfg), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
              timeout=60).stdout
    data = json.loads(out or b"{}")
    info = {"width": 0, "height": 0, "fps": 30.0, "duration": 0.0, "has_audio": False, "has_video": False}
    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and not info["has_video"]:
            info["has_video"] = True
            info["width"] = int(st.get("width") or 0)
            info["height"] = int(st.get("height") or 0)
            rate = st.get("avg_frame_rate") or st.get("r_frame_rate") or "30/1"
            try:
                num, den = rate.split("/")
                fps = float(num) / float(den) if float(den) else 30.0
            except (ValueError, ZeroDivisionError):
                fps = 30.0
            info["fps"] = fps if 1 < fps < 241 else 30.0
        elif st.get("codec_type") == "audio":
            info["has_audio"] = True
    try:
        info["duration"] = float(data.get("format", {}).get("duration") or 0.0)
    except ValueError:
        info["duration"] = 0.0
    return info


def ff_filter_path(path: str | Path) -> str:
    """Pfad für ffmpeg-Filteroptionen (ass=, fontsdir=) escapen, auch für Windows (C:\\...)."""
    p = str(Path(path).resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"'{p}'"


# --------------------------------------------------------------------------- Encoder

_ENCODER_LOCK = threading.Lock()
_ENCODER_CACHE: dict[str, list[str]] = {}


def _encoder_args(name: str, bitrate: str) -> list[str]:
    maxrate = _scale_bitrate(bitrate, 1.5)
    if name == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "quality", "-rc", "vbr_peak", "-b:v", bitrate, "-maxrate", maxrate,
                "-profile:v", "high"]
    if name == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", bitrate, "-maxrate", maxrate, "-profile:v", "high",
                "-allow_sw", "1"]
    if name == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", bitrate,
                "-maxrate", maxrate, "-profile:v", "high"]
    if name == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-b:v", bitrate, "-maxrate", maxrate, "-profile:v", "high"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-maxrate", maxrate,
            "-bufsize", _scale_bitrate(bitrate, 2), "-profile:v", "high"]


def _scale_bitrate(bitrate: str, factor: float) -> str:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", str(bitrate).strip())
    if not m:
        return bitrate
    return f"{float(m.group(1)) * factor:g}{m.group(2)}"


def pick_encoder(cfg: dict) -> list[str]:
    """Funktionierenden H.264-Encoder finden (Hardware bevorzugt) und die ffmpeg-Argumente liefern."""
    pref = cfg["render"].get("encoder", "auto")
    bitrate = cfg["render"].get("video_bitrate", "12M")
    key = f"{pref}:{bitrate}"
    with _ENCODER_LOCK:
        if key in _ENCODER_CACHE:
            return _ENCODER_CACHE[key]
        if pref != "auto":
            order = [pref]
        elif IS_MAC:
            order = ["h264_videotoolbox", "libx264"]
        elif IS_WINDOWS:
            order = ["h264_amf", "h264_nvenc", "h264_qsv", "libx264"]
        else:
            order = ["h264_nvenc", "h264_amf", "libx264"]
        chosen = None
        for name in order:
            if name == "libx264" or _encoder_works(cfg, name, bitrate):
                chosen = name
                break
        chosen = chosen or "libx264"
        log.info("Video-Encoder: %s", chosen)
        _ENCODER_CACHE[key] = _encoder_args(chosen, bitrate)
        return _ENCODER_CACHE[key]


def _encoder_works(cfg: dict, name: str, bitrate: str) -> bool:
    cmd = [ffmpeg_bin(cfg), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
           "color=c=black:s=320x240:r=30:d=0.5", "-pix_fmt", "yuv420p", *_encoder_args(name, bitrate), "-f", "null", "-"]
    try:
        return run(cmd, timeout=30, check=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- Text

def slugify(text: str, maxlen: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:maxlen] or "x"


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U00002B00-\U00002BFF\uFE0F\u200d]+"
)


def strip_emoji(text: str) -> str:
    return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub("", text)).strip()
