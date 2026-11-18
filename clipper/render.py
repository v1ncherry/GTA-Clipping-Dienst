"""Rendern des fertigen Shorts (1080x1920) mit ffmpeg: Layout, Untertitel, Lautheit, Hardware-Encoder."""

from __future__ import annotations

import logging
from pathlib import Path

from .layout import OUT_H, OUT_W, x_expression
from .util import ff_filter_path, ffmpeg_bin, pick_encoder, run

log = logging.getLogger("clipper.render")


def output_fps(cfg: dict, info: dict) -> int:
    fps = cfg["render"].get("fps", "auto")
    if fps == "auto":
        return 60 if info.get("fps", 30) >= 50 else 30
    return int(fps)


def build_filtergraph(plan: dict, fps: int, ass_path: str | None, cfg: dict, has_audio: bool) -> str:
    subs = ""
    if ass_path:
        subs = f",ass={ff_filter_path(ass_path)}"
        fonts_dir = cfg["render"]["subtitles"].get("fonts_dir")
        if fonts_dir and Path(fonts_dir).exists():
            subs += f":fontsdir={ff_filter_path(fonts_dir)}"
    tail = f"setsar=1,fps={fps},format=yuv420p{subs}[v]"
    t = plan["type"]
    if t == "stack":
        cx, cy, cw, ch = plan["cam"]
        top_h = plan["top_h"]
        g = plan["game"]
        x = x_expression(plan["x_keys"])
        video = (
            "[0:v]split=2[cin][gin];"
            f"[cin]crop={cw}:{ch}:{cx}:{cy},scale={OUT_W}:{top_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={OUT_W}:{top_h},setsar=1[cam];"
            f"[gin]crop={g['w']}:{g['h']}:x='{x}':y={g['y']},scale={OUT_W}:{OUT_H - top_h}:flags=lanczos,setsar=1[game];"
            f"[cam][game]vstack=inputs=2,{tail}"
        )
    elif t == "crop":
        g = plan["game"]
        x = x_expression(plan["x_keys"])
        video = f"[0:v]crop={g['w']}:{g['h']}:x='{x}':y={g['y']},scale={OUT_W}:{OUT_H}:flags=lanczos,{tail}"
    else:
        video = (
            "[0:v]split=2[bgin][fgin];"
            f"[bgin]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
            "boxblur=luma_radius=30:luma_power=2,eq=brightness=-0.1[bg];"
            f"[fgin]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{tail}"
        )
    if has_audio:
        audio = "[0:a]aresample=48000"
        if cfg["render"].get("loudnorm", True):
            audio += ",loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
        video += f";{audio}[a]"
    return video


def render_clip(cfg: dict, src: str, start: float, dur: float, plan: dict, info: dict, ass_path: str | None,
                out_path: str) -> None:
    fps = output_fps(cfg, info)
    has_audio = bool(info.get("has_audio"))
    fg = build_filtergraph(plan, fps, ass_path, cfg, has_audio)
    cmd = [ffmpeg_bin(cfg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-ss", f"{start:.3f}", "-i", src]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-t", f"{dur:.3f}", "-filter_complex", fg, "-map", "[v]", "-map", "[a]" if has_audio else "1:a",
            *pick_encoder(cfg), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart"]
    if not has_audio:
        cmd += ["-shortest"]
    cmd.append(out_path)
    run(cmd, timeout=1800)


def make_thumbnail(cfg: dict, video: str, t: float, out: str) -> None:
    run([ffmpeg_bin(cfg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{max(t, 0):.2f}",
         "-i", video, "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4", out], timeout=60, check=False)


def extract_wav(cfg: dict, src: str, start: float, dur: float, out: str) -> None:
    run([ffmpeg_bin(cfg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{max(start, 0):.3f}",
         "-i", src, "-t", f"{dur:.3f}", "-vn", "-ac", "1", "-ar", "16000", out], timeout=300)
