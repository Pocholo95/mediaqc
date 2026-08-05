"""Extracción de metadatos técnicos de un archivo de medios.

Dos llamadas por archivo (spec sección 5.2):
- ``ffprobe`` para streams/formato.
- ``mkvmerge -J`` (solo MKV) porque sus IDs de pista **no coinciden** con los
  índices de stream de ffmpeg (trampa #1), y los comandos de mkvmerge que
  arma ``core/suggestions.py`` necesitan el ID de mkvmerge.

Se decodifica la salida de los binarios como UTF-8 explícitamente: no hay que
confiar en la locale del sistema, sobre todo en Windows (trampa #9).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Container delay y la fórmula del delay sugerido (spec sección 5.2):
#
#   delay_sugerido = -(offset_medido) - delay_contenedor_candidato + delay_contenedor_referencia
#
# El offset que mide la correlación cruzada (core/analyzer/correlate.py) ya
# incluye el delay declarado en el contenedor de cada pista. Sumarlo de nuevo
# al armar el comando de mkvmerge da el doble del valor correcto: es la
# fuente de bugs número uno de este tipo de herramienta. La aplicación de
# esta fórmula vive en core/suggestions.py (fase 5); acá solo se extrae y se
# guarda `container_delay_ms` por pista.


class ProbeError(Exception):
    pass


@dataclass
class ProbedTrack:
    stream_index: int
    mkv_track_id: int | None
    type: str  # 'video' | 'audio' | 'subtitle'
    codec: str | None
    language: str | None
    title: str | None
    channels: int | None
    is_default: bool
    is_forced: bool
    container_delay_ms: int


@dataclass
class ProbeResult:
    duration_s: float | None
    video_fps: float | None
    container: str | None
    tracks: list[ProbedTrack] = field(default_factory=list)
    error: str | None = None


def _run_json(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, capture_output=True, check=True)
    return json.loads(proc.stdout.decode("utf-8"))


def probe_ffprobe(path: Path, ffprobe_bin: Path) -> dict:
    cmd = [
        str(ffprobe_bin),
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    return _run_json(cmd)


def probe_mkvmerge(path: Path, mkvmerge_bin: Path) -> dict:
    return _run_json([str(mkvmerge_bin), "-J", str(path)])


def extract_container_delay_ms(stream: dict) -> int:
    """Delay de contenedor de una pista de audio: tag ``delay`` explícito si
    existe, si no ``start_time`` del stream (spec sección 5.2)."""
    tags = stream.get("tags", {}) or {}
    for key in ("DELAY", "delay", "DELAY_RELATIVE_TO_VIDEO", "delay_relative_to_video"):
        if key in tags:
            try:
                return round(float(tags[key]))
            except (TypeError, ValueError):
                pass

    start_time = stream.get("start_time")
    if start_time is not None:
        try:
            return round(float(start_time) * 1000)
        except (TypeError, ValueError):
            pass

    return 0


def _fps_from_stream(stream: dict) -> float | None:
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    if not rate or rate == "0/0":
        return None
    try:
        num_s, den_s = rate.split("/")
        num, den = float(num_s), float(den_s)
        if den == 0:
            return None
        return num / den
    except (ValueError, ZeroDivisionError):
        return None


def probe_file(path: Path, ffprobe_bin: Path, mkvmerge_bin: Path | None) -> ProbeResult:
    """Combina ffprobe + mkvmerge (si aplica) en un único ``ProbeResult``.

    Nunca lanza: los errores de subproceso o de parseo quedan en
    ``ProbeResult.error`` para que el caller los registre en ``jobs`` sin
    tumbar la cola de escaneo (spec sección 9).
    """
    try:
        ff = probe_ffprobe(path, ffprobe_bin)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        return ProbeResult(duration_s=None, video_fps=None, container=None, error=str(exc))

    fmt = ff.get("format", {}) or {}
    try:
        duration_s = float(fmt["duration"]) if fmt.get("duration") else None
    except (TypeError, ValueError):
        duration_s = None
    container = fmt.get("format_name")

    streams = ff.get("streams", [])

    video_fps = None
    for s in streams:
        if s.get("codec_type") == "video":
            video_fps = _fps_from_stream(s)
            break

    mkv_tracks_by_type: dict[str, list[int]] = {}
    if mkvmerge_bin is not None and path.suffix.lower() == ".mkv":
        try:
            mkv_data = probe_mkvmerge(path, mkvmerge_bin)
        except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
            mkv_data = None
        if mkv_data:
            type_map = {"video": "video", "audio": "audio", "subtitles": "subtitle"}
            for t in mkv_data.get("tracks", []):
                ttype = type_map.get(t.get("type"))
                if ttype:
                    mkv_tracks_by_type.setdefault(ttype, []).append(t["id"])

    # mkvmerge no expone el índice de ffmpeg ni viceversa: se emparejan por
    # orden dentro de cada tipo (n-ésima pista de audio de ffprobe <->
    # n-ésima pista de audio de mkvmerge), asumiendo que ambos preservan el
    # orden físico de las pistas en el contenedor.
    type_counters: dict[str, int] = {}
    tracks: list[ProbedTrack] = []
    for s in streams:
        codec_type = s.get("codec_type")
        if codec_type not in ("video", "audio", "subtitle"):
            continue

        idx_within_type = type_counters.get(codec_type, 0)
        type_counters[codec_type] = idx_within_type + 1

        mkv_id = None
        ids = mkv_tracks_by_type.get(codec_type)
        if ids and idx_within_type < len(ids):
            mkv_id = ids[idx_within_type]

        tags = s.get("tags", {}) or {}
        disposition = s.get("disposition", {}) or {}

        tracks.append(
            ProbedTrack(
                stream_index=s["index"],
                mkv_track_id=mkv_id,
                type=codec_type,
                codec=s.get("codec_name"),
                language=tags.get("language"),
                title=tags.get("title"),
                channels=s.get("channels"),
                is_default=bool(disposition.get("default")),
                is_forced=bool(disposition.get("forced")),
                container_delay_ms=extract_container_delay_ms(s) if codec_type == "audio" else 0,
            )
        )

    return ProbeResult(duration_s=duration_s, video_fps=video_fps, container=container, tracks=tracks)
