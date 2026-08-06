"""Parseo de subtítulos para el visor de verificación (no editor: la app
nunca reescribe archivos de la librería, regla número uno de la spec).

Soporta ``.srt`` y ``.ass``/``.ssa``, los formatos de texto más comunes.
Formatos basados en imagen (PGS, VobSub) no se pueden mostrar como texto —
``codec_supports_text_preview`` lo señala para que la UI avise en vez de
fallar en silencio.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Códecs de subtítulo basados en texto, extraíbles como .srt/.ass sin perder
# nada; el resto (PGS, VobSub/DVD) son imágenes por frame, no hay texto que
# mostrar en una tabla.
_TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text"}


def codec_supports_text_preview(codec: str | None) -> bool:
    return (codec or "").lower() in _TEXT_CODECS


@dataclass
class SubtitleCue:
    index: int
    start_s: float
    end_s: float
    text: str


_RE_ASS_TAG = re.compile(r"\{[^}]*\}")
_RE_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")


def _strip_ass_tags(text: str) -> str:
    text = _RE_ASS_TAG.sub("", text)
    return text.replace("\\N", "\n").replace("\\n", "\n").strip()


def _srt_timestamp_to_seconds(ts: str) -> float:
    m = _RE_SRT_TIME.match(ts.strip())
    if not m:
        return 0.0
    h, mi, s, ms = (int(g) for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def parse_srt(content: str) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    blocks = re.split(r"\r?\n\r?\n+", content.strip())
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip() != ""]
        if len(lines) < 2:
            continue

        idx_line = 0
        try:
            index = int(lines[0].strip())
            idx_line = 1
        except ValueError:
            index = len(cues) + 1

        time_line = lines[idx_line] if idx_line < len(lines) else ""
        if "-->" not in time_line:
            continue
        start_raw, end_raw = time_line.split("-->")
        start_s = _srt_timestamp_to_seconds(start_raw)
        end_s = _srt_timestamp_to_seconds(end_raw)

        text = "\n".join(lines[idx_line + 1 :]).strip()
        text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)  # tags <i>, <b>, etc.
        cues.append(SubtitleCue(index=index, start_s=start_s, end_s=end_s, text=text))

    return cues


def _ass_timestamp_to_seconds(ts: str) -> float:
    # formato H:MM:SS.cc (centésimas, no milésimas)
    m = re.match(r"(\d+):(\d{2}):(\d{2})[.,](\d{1,3})", ts.strip())
    if not m:
        return 0.0
    h, mi, s, frac = m.groups()
    frac_s = int(frac) / (10 ** len(frac))
    return int(h) * 3600 + int(mi) * 60 + int(s) + frac_s


def parse_ass(content: str) -> list[SubtitleCue]:
    cues: list[SubtitleCue] = []
    in_events = False
    format_fields: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("["):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue

        if line.lower().startswith("format:"):
            format_fields = [f.strip().lower() for f in line[len("format:") :].split(",")]
            continue

        if not line.lower().startswith("dialogue:"):
            continue
        if not format_fields:
            continue

        raw_fields = line[len("Dialogue:") :].strip().split(",", len(format_fields) - 1)
        if len(raw_fields) < len(format_fields):
            continue
        row = dict(zip(format_fields, raw_fields))

        start_s = _ass_timestamp_to_seconds(row.get("start", "0"))
        end_s = _ass_timestamp_to_seconds(row.get("end", "0"))
        text = _strip_ass_tags(row.get("text", ""))
        cues.append(SubtitleCue(index=len(cues) + 1, start_s=start_s, end_s=end_s, text=text))

    return cues


def parse_subtitle_file(path: Path) -> list[SubtitleCue]:
    content = path.read_text(encoding="utf-8-sig", errors="replace")
    suffix = path.suffix.lower()
    if suffix in (".ass", ".ssa"):
        return parse_ass(content)
    return parse_srt(content)


def extract_subtitle_track(
    ffmpeg_bin: Path, file_path: Path, stream_index: int, codec: str | None, out_dir: Path
) -> Path:
    """Extrae una pista de subtítulo de texto a un archivo aparte para
    poder parsearla — no toca el archivo original (regla número uno)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = ".ass" if (codec or "").lower() in ("ass", "ssa") else ".srt"
    out_path = out_dir / f"preview_{stream_index}{ext}"
    cmd = [
        str(ffmpeg_bin),
        "-y",
        "-i", str(file_path),
        "-map", f"0:{stream_index}",
        str(out_path),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return out_path
