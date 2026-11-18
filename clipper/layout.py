"""Hochformat-Layout: Facecam erkennen und Gameplay-Ausschnitt (Smart-Crop) berechnen.

Layouts (Ausgabe 1080x1920):
- stack : Facecam oben, Gameplay unten (wenn eine Facecam erkannt/eingestellt ist)
- crop  : 9:16-Ausschnitt aus dem Gameplay, folgt der Bewegung (Kamera-Schwenk ist geglättet)
- blur  : ganzes 16:9-Bild mittig, unscharfer Hintergrund
"""

from __future__ import annotations

import logging
import math
import urllib.request
from pathlib import Path

import numpy as np

from .util import ffmpeg_bin, run

log = logging.getLogger("clipper.layout")

OUT_W, OUT_H = 1080, 1920
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")


def _even(v: float) -> int:
    return max(2, int(round(v / 2.0)) * 2)


# --------------------------------------------------------------------------- Frames

def grab_frame(cfg: dict, path: str, t: float, width: int, height: int) -> np.ndarray | None:
    """Ein Frame als BGR-Array (über ffmpeg, damit keine OpenCV-Codecs nötig sind)."""
    cmd = [ffmpeg_bin(cfg), "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{max(t, 0):.3f}", "-i", path,
           "-frames:v", "1", "-vf", f"scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    out = run(cmd, timeout=60, check=False).stdout
    if len(out) < width * height * 3:
        return None
    return np.frombuffer(out[:width * height * 3], dtype=np.uint8).reshape(height, width, 3)


def sample_frames(cfg: dict, path: str, start: float, dur: float, src_w: int, src_h: int, n: int = 10,
                  width: int = 640) -> list[np.ndarray]:
    height = _even(width * src_h / max(src_w, 1))
    frames = []
    for i in range(n):
        f = grab_frame(cfg, path, start + dur * (i + 0.5) / n, width, height)
        if f is not None:
            frames.append(f)
    return frames


# --------------------------------------------------------------------------- Gesichter

class FaceDetector:
    def __init__(self, models_dir: Path):
        import cv2

        self.cv2 = cv2
        self.yunet = None
        model = models_dir / "face_detection_yunet_2023mar.onnx"
        if not model.exists():
            try:
                models_dir.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(YUNET_URL, model)  # noqa: S310
                log.info("Gesichtserkennungs-Modell (YuNet) geladen")
            except Exception as exc:  # noqa: BLE001
                log.debug("YuNet-Download fehlgeschlagen (%s) – nutze Haar-Cascade", exc)
                model.unlink(missing_ok=True)
        if model.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.yunet = cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.75, 0.3, 5000)
            except Exception as exc:  # noqa: BLE001
                log.debug("YuNet nicht nutzbar: %s", exc)
        self.haar = None
        if hasattr(cv2, "CascadeClassifier"):
            self.haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if self.yunet is None and self.haar is None:
            raise RuntimeError("Keine Gesichtserkennung verfügbar (YuNet-Modell fehlt, OpenCV ohne Haar-Cascades)")

    def detect(self, img: np.ndarray) -> list[tuple[float, float, float, float]]:
        h, w = img.shape[:2]
        if self.yunet is not None:
            self.yunet.setInputSize((w, h))
            _ok, faces = self.yunet.detect(img)
            if faces is None:
                return []
            return [tuple(map(float, f[:4])) for f in faces]
        if self.haar is None:
            return []
        gray = self.cv2.cvtColor(img, self.cv2.COLOR_BGR2GRAY)
        faces = self.haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(18, 18))
        return [tuple(map(float, f)) for f in faces]


_DETECTOR: FaceDetector | None = None


def get_face_detector(cfg: dict) -> FaceDetector:
    global _DETECTOR
    if _DETECTOR is None:
        _DETECTOR = FaceDetector(Path(cfg["data_dir"]) / "models")
    return _DETECTOR


def _stable_face(dets: list[tuple[int, tuple]], n_frames: int, frame_w: int) -> tuple | None:
    """Gesicht finden, das in den meisten Frames an derselben Stelle ist (= Facecam, nicht Spielfigur)."""
    best, best_count = None, 0
    for _i, (_fi, d) in enumerate(dets):
        cx, cy = d[0] + d[2] / 2, d[1] + d[3] / 2
        group = [g for g in dets
                 if abs(g[1][0] + g[1][2] / 2 - cx) < 0.6 * d[2] and abs(g[1][1] + g[1][3] / 2 - cy) < 0.6 * d[3]]
        count = len({g[0] for g in group})
        if count > best_count:
            best, best_count = group, count
    if not best or best_count < max(3, math.ceil(0.5 * n_frames)):
        return None
    arr = np.array([g[1] for g in best])
    centers = arr[:, 0] + arr[:, 2] / 2
    if centers.std() > 0.05 * frame_w:
        return None
    return tuple(np.median(arr, axis=0))


def _persistent_edges(frames: list[np.ndarray]) -> np.ndarray:
    import cv2

    acc = None
    for f in frames:
        gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        edges = (cv2.Canny(gray, 60, 160) > 0).astype(np.float32)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        acc = edges if acc is None else acc + edges
    return acc / max(len(frames), 1)


def _snap(profile: np.ndarray, lo: float, hi: float, fallback: float, outer: str, thresh: float = 0.5) -> float:
    """Äußerste dauerhafte Kante im Suchbereich (= Rahmen der Facecam; Gameplay dahinter bewegt sich)."""
    lo_i, hi_i = max(0, int(lo)), min(len(profile) - 1, int(hi))
    if hi_i <= lo_i:
        return fallback
    hits = np.nonzero(profile[lo_i:hi_i + 1] >= thresh)[0]
    if len(hits) == 0:
        return fallback
    return float(lo_i + (hits.min() if outer == "low" else hits.max()))


def detect_facecam(cfg: dict, frames: list[np.ndarray], src_w: int, src_h: int) -> dict | None:
    """Liefert {"kind": "overlay"|"fullcam", "rect": [x, y, w, h]} in Quell-Pixeln oder None."""
    if len(frames) < 3:
        return None
    fh_, fw_ = frames[0].shape[:2]
    det = get_face_detector(cfg)
    dets = []
    for i, f in enumerate(frames):
        for d in det.detect(f):
            if d[2] * d[3] > 0.0004 * fw_ * fh_:
                dets.append((i, d))
    face = _stable_face(dets, len(frames), fw_)
    if face is None:
        return None
    fx, fy, fw, fh = face
    sx, sy = src_w / fw_, src_h / fh_
    if fw * fh > 0.05 * fw_ * fh_ and 0.25 * fw_ < fx + fw / 2 < 0.75 * fw_:
        return {"kind": "fullcam", "rect": [fx * sx, fy * sy, fw * sx, fh * sy]}

    # Facecam-Rahmen schätzen, dann an dauerhafte Kanten (Overlay-Rahmen) bzw. Bildränder einrasten
    cam_w = fw * 3.4
    cam_h = max(cam_w * 9 / 16, fh * 2.4)
    cx, cy = fx + fw / 2, fy + fh / 2 + 0.08 * cam_h
    x0, x1 = cx - cam_w / 2, cx + cam_w / 2
    y0, y1 = cy - cam_h / 2, cy + cam_h / 2
    edges = _persistent_edges(frames)
    by0, by1 = int(max(0, fy - fh * 0.3)), int(min(fh_, fy + fh * 1.6))
    col = edges[by0:by1].mean(axis=0) if by1 > by0 else np.zeros(fw_)
    est = (x0, x1, y0, y1)
    x0 = _snap(col, fx - fw * 3.0, fx - fw * 0.4, x0, "low")
    x1 = _snap(col, fx + fw * 1.4, fx + fw * 4.0, x1, "high")
    rx0, rx1 = int(max(0, x0 + 3)), int(min(fw_, x1 - 3))
    row = edges[:, rx0:rx1].mean(axis=1) if rx1 > rx0 else np.zeros(fh_)
    y0 = _snap(row, fy - fh * 2.5, fy - fh * 0.25, y0, "low")
    y1 = _snap(row, fy + fh * 1.3, fy + fh * 4.0, y1, "high")
    # Nur geschätzte (nicht per Kante gefundene) Seiten nahe am Bildrand bündig an den Rand setzen
    margin_x, margin_y = 0.06 * fw_, 0.06 * fh_
    if x0 == est[0] and x0 < margin_x:
        x0 = 0
    if x1 == est[1] and x1 > fw_ - margin_x:
        x1 = fw_
    if y0 == est[2] and y0 < margin_y:
        y0 = 0
    if y1 == est[3] and y1 > fh_ - margin_y:
        y1 = fh_
    x0, y0 = max(0.0, x0), max(0.0, y0)
    x1, y1 = min(float(fw_), x1), min(float(fh_), y1)
    if (x1 - x0) > 0.55 * fw_ or (y1 - y0) > 0.7 * fh_ or x1 - x0 < fw * 1.2 or y1 - y0 < fh * 1.2:
        return None
    return {"kind": "overlay", "rect": [x0 * sx, y0 * sy, (x1 - x0) * sx, (y1 - y0) * sy]}


# --------------------------------------------------------------------------- Smart-Crop

def compute_crop_path(cfg: dict, path: str, start: float, dur: float, src_w: int, src_h: int, crop_w: int,
                      exclude: list[list[float]] | None = None, focus_x: float | None = None) -> list[list[float]]:
    """Horizontale Crop-Position über die Zeit: [[t, x], ...] (Quell-Pixel).

    Bereiche in `exclude` (Facecam) werden bei der Bewegungsanalyse ignoriert und – wenn es die Breite
    zulässt – ganz aus dem Ausschnitt herausgehalten, damit die Facecam nicht doppelt zu sehen ist.
    """
    max_x = src_w - crop_w
    if max_x <= 0:
        return [[0.0, 0]]
    lo_x, hi_x = 0.0, float(max_x)
    for r in exclude or []:
        if r[0] + r[2] / 2 > src_w / 2:          # Facecam rechts -> Ausschnitt muss links davon enden
            if r[0] - crop_w >= 0:
                hi_x = min(hi_x, r[0] - crop_w)
        elif r[0] + r[2] + crop_w <= src_w:      # Facecam links -> Ausschnitt rechts davon
            lo_x = max(lo_x, r[0] + r[2])
    if lo_x > hi_x:
        lo_x, hi_x = 0.0, float(max_x)
    center_x = float(np.clip((src_w - crop_w) / 2, lo_x, hi_x))
    if focus_x is not None:
        return [[0.0, int(np.clip(focus_x - crop_w / 2, lo_x, hi_x))]]
    import cv2

    lw = 320
    lh = _even(lw * src_h / src_w)
    s = lw / src_w
    cmd = [ffmpeg_bin(cfg), "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{start:.3f}", "-i", path,
           "-t", f"{dur:.3f}", "-vf", f"fps=4,scale={lw}:{lh},format=gray", "-f", "rawvideo", "pipe:1"]
    raw = run(cmd, timeout=300, check=False).stdout
    n = len(raw) // (lw * lh)
    if n < 3:
        return [[0.0, int(center_x)]]
    frames = np.frombuffer(raw[:n * lw * lh], dtype=np.uint8).reshape(n, lh, lw).astype(np.float32)
    mask = np.ones((lh, lw), np.float32)
    mask[int(0.70 * lh):, :int(0.22 * lw)] = 0     # GTA-Minimap unten links
    for r in exclude or []:
        x, y, w, h = (int(v * s) for v in r)
        mask[max(0, y):y + h, max(0, x):x + w] = 0
    cw = crop_w * s
    positions = np.arange(0, lw - cw + 1)
    prior = np.exp(-(((positions + cw / 2) - lw / 2) / (0.35 * lw)) ** 2)
    targets = []
    for k in range(1, n):
        diff = cv2.GaussianBlur(np.abs(frames[k] - frames[k - 1]), (5, 5), 0) * mask
        cs = np.concatenate([[0], np.cumsum(diff.sum(axis=0))])
        energy = cs[(positions + cw).astype(int)] - cs[positions.astype(int)]
        if energy.max() < 1e-3 * lh * cw * 255:   # statische Szene
            targets.append(center_x)
            continue
        score = 0.65 * energy / energy.max() + 0.35 * prior
        targets.append(float(positions[int(np.argmax(score))]) / s)
    targets = np.array(targets)
    # Glätten: Median gegen Ausreißer, dann träge Kamera mit Totzone und Tempolimit
    pad = np.pad(targets, 3, mode="edge")
    med = np.array([np.median(pad[i:i + 7]) for i in range(len(targets))])
    cur = float(med[0])
    max_step = 220 / 4          # Quell-Pixel pro Sample (4 fps)
    dead = 0.05 * src_w
    smooth = []
    for tgt in med:
        if abs(tgt - cur) > dead:
            cur += float(np.clip((tgt - cur) * 0.2, -max_step, max_step))
        smooth.append(cur)
    keys = []
    for i in range(0, len(smooth), 4):
        keys.append([round(i / 4.0 + 0.25, 2), int(np.clip(smooth[i], lo_x, hi_x))])
    if max(k[1] for k in keys) - min(k[1] for k in keys) <= 4:
        return [[0.0, keys[0][1]]]
    keys[0][0] = 0.0
    return keys


def x_expression(keys: list[list[float]]) -> str:
    """Stückweise lineare Crop-Position als flache ffmpeg-Expression (ohne tiefe Verschachtelung)."""
    if len(keys) == 1:
        return str(int(keys[0][1]))
    terms = []
    for i in range(len(keys) - 1):
        t0, x0 = keys[i]
        t1, x1 = keys[i + 1]
        if t1 <= t0:
            continue
        cond = f"lt(t,{t1:.2f})" if i == 0 else f"gte(t,{t0:.2f})*lt(t,{t1:.2f})"
        if x0 == x1:
            terms.append(f"{cond}*{int(x0)}")
        else:
            terms.append(f"{cond}*({int(x0)}+{int(x1) - int(x0)}*(t-{t0:.2f})/{t1 - t0:.2f})")
    terms.append(f"gte(t,{keys[-1][0]:.2f})*{int(keys[-1][1])}")
    return "+".join(terms)


# --------------------------------------------------------------------------- Plan

def plan_layout(cfg: dict, path: str, start: float, dur: float, info: dict, facecam_setting=None,
                override: str | None = None) -> dict:
    src_w, src_h = info["width"], info["height"]
    mode = override or cfg["render"].get("layout", "auto")
    sub_pos = cfg["render"]["subtitles"].get("position", "auto")
    if src_w <= src_h:   # schon Hochformat
        mode = "blur"

    cam = None
    focus_x = None
    if isinstance(facecam_setting, dict) and facecam_setting.get("w"):
        r = facecam_setting
        cam = [r["x"] * src_w, r["y"] * src_h, r["w"] * src_w, r["h"] * src_h]
    elif facecam_setting != "none" and mode in ("auto", "stack", "crop"):
        try:
            frames = sample_frames(cfg, path, start, dur, src_w, src_h)
            found = detect_facecam(cfg, frames, src_w, src_h)
        except Exception as exc:  # noqa: BLE001
            log.warning("Facecam-Erkennung fehlgeschlagen: %s", exc)
            found = None
        if found and found["kind"] == "overlay":
            cam = found["rect"]
        elif found:
            focus_x = found["rect"][0] + found["rect"][2] / 2

    if mode == "blur":
        fg_h = _even(OUT_W * src_h / src_w) if src_w > src_h else OUT_H
        top = (OUT_H - fg_h) // 2
        return {"type": "blur", "fg_h": fg_h,
                "sub_y": _sub_y(sub_pos, min(OUT_H - 380, top + fg_h + 140)),
                "hook_y": max(160, top - 150), "credit": [40, top + fg_h + 50, 1]}

    if cam and mode in ("auto", "stack"):
        cx, cy, cw, ch = (int(round(v)) for v in cam)
        cw, ch = _even(min(cw, src_w - cx)), _even(min(ch, src_h - cy))
        top_h = _even(min(900, max(540, OUT_W * ch / max(cw, 1))))
        game_h = OUT_H - top_h
        gw, gh = _even(src_h * OUT_W / game_h), src_h
        if gw > src_w:
            gw, gh = src_w, _even(src_w * game_h / OUT_W)
        gy = (src_h - gh) // 2
        keys = compute_crop_path(cfg, path, start, dur, src_w, src_h, gw, exclude=[cam])
        return {"type": "stack", "cam": [cx, cy, cw, ch], "top_h": top_h, "game": {"w": gw, "h": gh, "y": gy},
                "x_keys": keys, "sub_y": _sub_y(sub_pos, top_h + int(0.27 * game_h)), "hook_y": top_h,
                "credit": [30, top_h - 24, 1]}

    gw, gh = _even(src_h * 9 / 16), src_h
    if gw > src_w:
        gw, gh = src_w, _even(src_w * 16 / 9)
    keys = compute_crop_path(cfg, path, start, dur, src_w, src_h, gw, exclude=[cam] if cam else None,
                             focus_x=focus_x)
    return {"type": "crop", "game": {"w": gw, "h": gh, "y": (src_h - gh) // 2}, "x_keys": keys,
            "sub_y": _sub_y(sub_pos, int(0.66 * OUT_H)), "hook_y": int(0.17 * OUT_H), "credit": [30, 130, 7]}


def _sub_y(setting, auto_value: int) -> int:
    if isinstance(setting, (int, float)) and 0 < float(setting) < 1:
        return int(float(setting) * OUT_H)
    return int(auto_value)
