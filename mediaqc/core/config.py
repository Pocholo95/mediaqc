"""Configuración de la app: JSON en el directorio de configuración del usuario.

Nada de ``.env`` ni rutas hardcodeadas: todo pasa por ``platformdirs`` (spec
sección 3). El diálogo de preferencias de la UI es el único lugar donde se
edita; este módulo solo carga, guarda y valida.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import platformdirs

APP_NAME = "MediaQC"
CONFIG_FILENAME = "config.json"


class ConfigError(Exception):
    """Configuración inválida (p.ej. output_dir dentro de media_paths)."""


@dataclass
class Config:
    media_paths: list[str] = field(default_factory=list)
    candidates_paths: list[str] = field(default_factory=list)
    output_dir: str = ""
    tmdb_api_key: str = ""
    tmdb_language: str = "es-MX"
    expected_languages: list[str] = field(default_factory=lambda: ["spa"])
    analysis_windows: int = 5
    window_seconds: int = 60
    sample_rate: int = 8000
    max_concurrent_jobs: int = 2
    audio_cache_limit_gb: float = 5
    ffmpeg_path: str | None = None
    mkvmerge_path: str | None = None
    dark_mode: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def get_config_dir() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME))


def get_config_path() -> Path:
    return get_config_dir() / CONFIG_FILENAME


def get_data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME))


def get_db_path() -> Path:
    return get_data_dir() / "mediaqc.db"


def get_audio_cache_dir() -> Path:
    return get_data_dir() / "audio_cache"


def get_tmdb_cache_dir() -> Path:
    return get_data_dir() / "tmdb_cache"


def get_logs_dir() -> Path:
    return get_data_dir() / "logs"


def ensure_data_dirs() -> None:
    for d in (get_data_dir(), get_audio_cache_dir(), get_tmdb_cache_dir(), get_logs_dir()):
        d.mkdir(parents=True, exist_ok=True)


def config_exists() -> bool:
    return get_config_path().is_file()


def load_config() -> Config | None:
    """``None`` significa que no hay configuración todavía (primer arranque)."""
    path = get_config_path()
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return Config.from_dict(data)


def save_config(config: Config) -> None:
    validate_output_dir(config.output_dir, config.media_paths)
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    ensure_data_dirs()
    with get_config_path().open("w", encoding="utf-8") as fh:
        json.dump(config.to_dict(), fh, indent=2, ensure_ascii=False)


def validate_output_dir(output_dir: str, media_paths: list[str]) -> None:
    """``output_dir`` nunca puede caer dentro de ninguna ``media_paths``.

    Es la salvaguarda contra sobrescribir originales (spec sección 3). Vacío
    es válido (todavía no configurado); se valida solo cuando hay algo que
    validar.
    """
    if not output_dir:
        return
    out = Path(output_dir).resolve()
    for mp in media_paths:
        if not mp:
            continue
        media = Path(mp).resolve()
        if out == media or out.is_relative_to(media):
            raise ConfigError(
                f"El directorio de salida ({output_dir}) no puede estar dentro de "
                f"una ruta de la librería ({mp}). Elegí una carpeta fuera de la librería."
            )
