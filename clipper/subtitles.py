"""ASS-Untertitel im Shorts-Stil: 2-3 Wörter, großes Outline, aktuelles Wort farbig hervorgehoben."""

from __future__ import annotations

import re

from .transcribe import Word
from .util import strip_emoji

_PUNCT_STRIP = ".,;:„“\"'«»…-–—"


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def clean_word(text: str, upper: bool) -> str:
    text = text.strip().strip(_PUNCT_STRIP)
    return text.upper() if upper else text


def chunk_words(words: list[Word], max_words: int = 3, max_chars: int = 18, max_gap: float = 0.5) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur:
            chars = sum(len(x.text) + 1 for x in cur) + len(w.text)
            gap = w.start - cur[-1].end
            if (len(cur) >= max_words or chars > max_chars or gap > max_gap
                    or re.search(r"[.!?,]$", cur[-1].text.strip())):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[Word], plan: dict, cfg: dict, duration: float, hook: str | None = None,
              credit: str | None = None) -> str:
    r = cfg["render"]
    sc = r["subtitles"]
    font = sc.get("font", "Arial Black")
    size = int(sc.get("size", 82))
    outline = int(sc.get("outline", 7))
    primary = ass_color(sc.get("color", "#FFFFFF"))
    highlight = ass_color(sc.get("highlight", "#FFE600"))
    upper = bool(sc.get("uppercase", True))
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Sub,{font},{size},{primary},&H000000FF,&H00000000,&H78000000,-1,0,0,0,100,100,0,0,1,{outline},3,5,"
        "70,70,0,1",
        f"Style: Hook,{font},62,&H00000000,&H000000FF,&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,16,0,5,90,90,0,1",
        "Style: Credit,Arial,34,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,0,1,30,30,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    sub_y = int(plan.get("sub_y", 1260))
    if sc.get("enabled", True) and words:
        words = [Word(w.start, w.end, clean_word(w.text, upper)) for w in words]
        words = [w for w in words if w.text and w.end > 0 and w.start < duration]
        chunks = chunk_words(words, int(sc.get("max_words", 3)), int(sc.get("max_chars", 18)))
        for ci, chunk in enumerate(chunks):
            c_end = chunk[-1].end
            nxt = chunks[ci + 1][0].start if ci + 1 < len(chunks) else None
            c_end = nxt if nxt is not None and nxt - c_end < 0.4 else c_end + 0.3
            for k, w in enumerate(chunk):
                start = chunk[0].start if k == 0 else w.start
                end = chunk[k + 1].start if k + 1 < len(chunk) else c_end
                start, end = max(0.0, start), min(duration, end)
                if end - start < 0.04:
                    continue
                parts = []
                for j, x in enumerate(chunk):
                    txt = ass_escape(x.text)
                    parts.append(f"{{\\c{highlight}}}{txt}{{\\c{primary}}}" if j == k else txt)
                anim = "\\fscx82\\fscy82\\t(0,90,\\fscx100\\fscy100)" if k == 0 else ""
                text = f"{{\\an5\\pos(540,{sub_y}){anim}}}" + " ".join(parts)
                lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Sub,,0,0,0,,{text}")
    if hook and r.get("hook_text", True):
        hook_txt = ass_escape(strip_emoji(hook).upper())
        if hook_txt:
            end = min(duration, float(r.get("hook_seconds", 3.5)))
            lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Hook,,0,0,0,,"
                         f"{{\\an5\\pos(540,{int(plan.get('hook_y', 300))})\\fad(80,250)}}{hook_txt}")
    if credit and r.get("credit", True):
        cx, cy, align = plan.get("credit", [30, 130, 7])
        lines.append(f"Dialogue: 2,{ass_time(0)},{ass_time(duration)},Credit,,0,0,0,,"
                     f"{{\\an{align}\\pos({cx},{cy})}}{ass_escape(credit)}")
    return "\n".join(lines) + "\n"
