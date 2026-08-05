"""MediaQC — detección y registro de problemas de sincronización de doblaje."""

__version__ = "0.1.0"

from mediaqc.core.tools import ensure_mpv_loadable as _ensure_mpv_loadable

# Tiene que correr antes de que cualquier módulo haga `import mpv` (ui/player.py).
_ensure_mpv_loadable()
