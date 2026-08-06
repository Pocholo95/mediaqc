"""Comandos sugeridos (mkvmerge/ffmpeg) y exportación de scripts (spec 5.7).

Nunca ejecuta nada: solo arma listas de argumentos o texto para mostrar y
copiar. Ejecutar los comandos queda siempre del lado del usuario — regla
número uno de la spec, la app nunca modifica archivos de la librería.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path


def compute_final_delay_ms(measured_offset_ms: float) -> int:
    """El delta a pasarle a ``mkvmerge --sync <track>:<delta>`` (o a
    ``player.audio_delay``) para alinear el candidato con la referencia.

    La spec (sección 5.2) documenta una fórmula que resta el delay de
    contenedor del candidato y suma el de la referencia, asumiendo que la
    ventana de audio se extrae de forma "cruda" (sin timestamps de
    contenedor). Nuestra extracción (``core/analyzer/correlate.py``, vía
    ``ffmpeg -ss <pos> -i <archivo> -map 0:<stream>``) **sí** respeta el
    ``start_time``/delay de cada pista al recortar la ventana — lo verifiqué
    empíricamente muxeando un archivo con un delay conocido e inyectado
    (+200ms) y remidiendo: aplicar la fórmula de la spec tal cual empeoraba
    el desfasaje en vez de corregirlo, porque contaba el delay de contenedor
    dos veces. Con esta extracción, el offset medido YA es exactamente el
    delta que hay que sumarle al delay que la pista ya trae (mkvmerge suma
    ``--sync`` al delay existente, no lo reemplaza) — sin ningún término de
    delay de contenedor adicional. Es, literalmente, la trampa #2 de la
    spec ("produce el doble del valor correcto") pero en la dirección
    contraria: el bug estaba en aplicar la fórmula de más, no de menos.
    """
    return round(measured_offset_ms)


def format_command(cmd: list[str], platform: str = "windows") -> str:
    """``platform``: ``"windows"`` o ``"linux"`` — el destino del comando, no
    necesariamente el SO donde corre la app (spec 5.7: exportar `.bat` en
    Windows, `.sh` en Linux, y poder elegir el otro)."""
    if platform == "windows":
        return " ".join(f'"{part}"' if (" " in part or part == "") else part for part in cmd)
    return shlex.join(cmd)


@dataclass
class Suggestion:
    verdict: str
    summary: str
    commands: list[list[str]] = field(default_factory=list)
    warning: str | None = None


# --- comandos individuales --------------------------------------------------


def build_internal_sync_command(
    mkvmerge_bin: str,
    episode_path: str,
    output_path: str,
    mkv_track_id: int,
    delay_ms: int,
) -> list[str]:
    """``sync_constante`` en modo interno (spec 5.7): remux del archivo
    consigo mismo, corrigiendo el delay de una sola pista. Conserva el
    resto de las pistas, capítulos y adjuntos intactos, sin recodificar."""
    return [mkvmerge_bin, "-o", output_path, "--sync", f"{mkv_track_id}:{delay_ms}", episode_path]


def build_external_sync_command(
    mkvmerge_bin: str,
    bdrip_path: str,
    candidate_path: str,
    output_path: str,
    delay_ms: int,
    language: str,
    track_name: str,
) -> list[str]:
    """``sync_constante`` en modo externo (spec 5.7): muxea el candidato
    sobre el BD rip agregando la pista de idioma."""
    return [
        mkvmerge_bin,
        "-o", output_path,
        bdrip_path,
        "--language", f"0:{language}",
        "--track-name", f"0:{track_name}",
        "--sync", f"0:{delay_ms}",
        candidate_path,
    ]


def build_drift_ffmpeg_commands(
    ffmpeg_bin: str,
    source_path: str,
    output_path_speed: str,
    output_path_tempo: str,
    ratio: float,
    sample_rate: int = 48000,
) -> dict[str, list[str]]:
    """``sync_drift`` (spec 5.7): dos variantes, la UI explica la diferencia.
    - ``speed``: corrección de velocidad (correcta para PAL/NTSC, cambia el
      pitch como el propio speedup PAL).
    - ``tempo``: preserva el pitch (rubberband).
    """
    target_rate = round(sample_rate * ratio)
    return {
        "speed": [
            ffmpeg_bin,
            "-i", source_path,
            "-filter:a", f"asetrate={target_rate},aresample={sample_rate}",
            output_path_speed,
        ],
        "tempo": [
            ffmpeg_bin,
            "-i", source_path,
            "-filter:a", f"rubberband=tempo={ratio}",
            output_path_tempo,
        ],
    }


# --- orquestación: elegir qué sugerir según el veredicto --------------------


def suggest_for_analysis(
    *,
    verdict: str,
    pair_source: str,
    suggested_delay_ms: int | None,
    suggested_resample_ratio: float | None,
    mkv_track_id: int | None,
    episode_path: str,
    output_filename: str,
    output_dir: str,
    mkvmerge_bin: str | None,
    ffmpeg_bin: str | None,
    candidate_path: str | None = None,
    candidate_language: str | None = None,
    candidate_track_name: str | None = None,
) -> Suggestion:
    if verdict == "ok":
        return Suggestion(verdict=verdict, summary="Sincronizado — no hace falta ningún comando.")

    if verdict == "sync_constante":
        if suggested_delay_ms is None:
            return Suggestion(verdict=verdict, summary="Sin delay medido todavía.", warning="Corré el análisis primero.")

        final_delay = compute_final_delay_ms(suggested_delay_ms)
        summary = f"Delay sugerido: {final_delay} ms"
        output_path = str(Path(output_dir) / output_filename)

        if not mkvmerge_bin:
            return Suggestion(verdict=verdict, summary=summary, warning="No se encontró mkvmerge — instalá MKVToolNix.")

        if pair_source == "internal":
            if mkv_track_id is None:
                return Suggestion(
                    verdict=verdict,
                    summary=summary,
                    warning="Falta el ID de pista de mkvmerge para este episodio — reinstalá "
                    "MKVToolNix (si no estaba al escanear) y volvé a escanear la biblioteca.",
                )
            cmd = build_internal_sync_command(mkvmerge_bin, episode_path, output_path, mkv_track_id, final_delay)
            return Suggestion(verdict=verdict, summary=summary, commands=[cmd])

        if not candidate_path:
            return Suggestion(verdict=verdict, summary=summary, warning="Falta la ruta del candidato externo.")
        cmd = build_external_sync_command(
            mkvmerge_bin,
            episode_path,
            candidate_path,
            output_path,
            final_delay,
            candidate_language or "und",
            candidate_track_name or "Audio",
        )
        return Suggestion(verdict=verdict, summary=summary, commands=[cmd])

    if verdict == "sync_drift":
        if suggested_resample_ratio is None:
            return Suggestion(verdict=verdict, summary="Sin ratio de resample medido todavía.")
        if not ffmpeg_bin:
            return Suggestion(verdict=verdict, summary="No se encontró ffmpeg.", warning="Instalá ffmpeg.")

        source = candidate_path if (pair_source == "external" and candidate_path) else episode_path
        stem = Path(output_filename).stem
        output_speed = str(Path(output_dir) / f"{stem}_speed.mka")
        output_tempo = str(Path(output_dir) / f"{stem}_tempo.mka")
        variants = build_drift_ffmpeg_commands(
            ffmpeg_bin, source, output_speed, output_tempo, suggested_resample_ratio
        )
        return Suggestion(
            verdict=verdict,
            summary=f"Ratio de resample sugerido: {suggested_resample_ratio:.4f} (problema de framerate)",
            commands=[variants["speed"], variants["tempo"]],
            warning=(
                "Dos variantes, elegí una: 'speed' corrige velocidad y pitch juntos (como el propio "
                "speedup PAL); 'tempo' preserva el pitch (rubberband). El resultado hay que muxearlo "
                "aparte con mkvmerge, esto solo corrige el audio."
            ),
        )

    if verdict == "sync_segmentado":
        return Suggestion(
            verdict=verdict,
            summary="Saltos escalonados entre cortes — no hay un delay único que lo arregle.",
            warning="Revisión manual, o probá la herramienta Sushi para resincronizar por segmentos.",
        )

    return Suggestion(verdict=verdict, summary=f"Sin comando sugerido para el veredicto '{verdict}'.")


# --- exportación de script por temporada ------------------------------------


def build_season_export_script(episode_suggestions: list[tuple[str, Suggestion]], platform: str) -> str:
    """``episode_suggestions``: ``[(nombre_episodio, Suggestion), ...]``.
    ``platform``: ``"windows"`` -> `.bat`, ``"linux"`` -> `.sh`. Con
    comprobación de error por comando, comentario por episodio, y todas las
    rutas ya entrecomilladas por ``format_command`` (spec 5.7)."""
    lines: list[str] = []

    if platform == "windows":
        lines.append("@echo off")
        lines.append("setlocal enabledelayedexpansion")
        lines.append("")
        for name, suggestion in episode_suggestions:
            if not suggestion.commands:
                continue
            lines.append(f"rem --- {name}: {suggestion.summary} ---")
            for cmd in suggestion.commands:
                lines.append(format_command(cmd, platform="windows"))
                lines.append("if errorlevel 1 (")
                lines.append(f"    echo ERROR en {name}")
                lines.append("    exit /b 1")
                lines.append(")")
            lines.append("")
        lines.append("echo Listo.")
    else:
        lines.append("#!/bin/sh")
        lines.append("set -e")
        lines.append("")
        for name, suggestion in episode_suggestions:
            if not suggestion.commands:
                continue
            lines.append(f"# --- {name}: {suggestion.summary} ---")
            for cmd in suggestion.commands:
                lines.append(format_command(cmd, platform="linux"))
            lines.append("")
        lines.append('echo "Listo."')

    return "\n".join(lines) + "\n"
