"""Resolución de binarios externos (ffmpeg, ffprobe, mkvmerge).

Orden de búsqueda para cada binario, según spec sección 7-8:
1. Ruta explícita en la configuración (``ffmpeg_path`` / ``mkvmerge_path``).
2. Directorio ``bin/`` junto al ejecutable (empaquetado con PyInstaller) o junto
   a la raíz del proyecto en modo desarrollo.
3. ``PATH`` del sistema (``shutil.which``).

Nunca se invoca nada con ``shell=True``.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

_EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""

_BINARY_NAMES = {
    "ffmpeg": f"ffmpeg{_EXE_SUFFIX}",
    "ffprobe": f"ffprobe{_EXE_SUFFIX}",
    "mkvmerge": f"mkvmerge{_EXE_SUFFIX}",
}


def _app_root() -> Path:
    """Directorio junto al ejecutable (frozen) o raíz del proyecto (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # mediaqc/core/tools.py -> mediaqc/core -> mediaqc -> raíz del proyecto
    return Path(__file__).resolve().parents[2]


def _bundled_bin_dir() -> Path:
    return _app_root() / "bin"


def resolve_tool(name: str, override: str | None = None) -> Path | None:
    """Busca un binario externo por nombre lógico (``ffmpeg``, ``ffprobe``, ``mkvmerge``)."""
    if name not in _BINARY_NAMES:
        raise ValueError(f"binario desconocido: {name}")

    if override:
        p = Path(override)
        if p.is_file():
            return p

    bundled = _bundled_bin_dir() / _BINARY_NAMES[name]
    if bundled.is_file():
        return bundled

    found = shutil.which(_BINARY_NAMES[name]) or shutil.which(name)
    if found:
        return Path(found)

    return None


@dataclass
class ToolPaths:
    ffmpeg: Path | None
    ffprobe: Path | None
    mkvmerge: Path | None

    def missing(self) -> list[str]:
        missing = []
        if self.ffmpeg is None:
            missing.append("ffmpeg")
        if self.ffprobe is None:
            missing.append("ffprobe")
        if self.mkvmerge is None:
            missing.append("mkvmerge")
        return missing


_mpv_dll_setup_done = False


def ensure_mpv_loadable() -> None:
    """Deja ``bin/`` disponible para que ``import mpv`` (ctypes) encuentre
    ``libmpv-2.dll``/``mpv-2.dll`` en Windows.

    ``libmpv-2.dll`` no se versiona en el repo (pesa ~110MB, supera el
    límite de GitHub) — hay que colocarlo en ``bin/`` a mano en desarrollo,
    o empaquetarlo ahí para distribución (spec sección 7). Tiene que
    llamarse ANTES de cualquier ``import mpv`` en el proceso: ctypes
    resuelve la DLL en el momento del import, no lazily.
    """
    global _mpv_dll_setup_done
    if _mpv_dll_setup_done:
        return
    _mpv_dll_setup_done = True

    bundled = _bundled_bin_dir()
    if not bundled.is_dir():
        return
    if hasattr(os, "add_dll_directory"):  # Windows únicamente
        os.add_dll_directory(str(bundled))
    os.environ["PATH"] = str(bundled) + os.pathsep + os.environ.get("PATH", "")


def resolve_all(ffmpeg_override: str | None = None, mkvmerge_override: str | None = None) -> ToolPaths:
    """``ffprobe`` no tiene override propio en la config: vive junto a ``ffmpeg``."""
    ffmpeg = resolve_tool("ffmpeg", ffmpeg_override)
    ffprobe_override = None
    if ffmpeg_override:
        candidate = Path(ffmpeg_override).parent / _BINARY_NAMES["ffprobe"]
        if candidate.is_file():
            ffprobe_override = str(candidate)
    ffprobe = resolve_tool("ffprobe", ffprobe_override)
    mkvmerge = resolve_tool("mkvmerge", mkvmerge_override)
    return ToolPaths(ffmpeg=ffmpeg, ffprobe=ffprobe, mkvmerge=mkvmerge)
