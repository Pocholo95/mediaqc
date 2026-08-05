# MediaQC

Detecta y registra problemas de sincronización de doblaje en una librería de BD rips. Ver [MEDIAQC-SPEC.md](MEDIAQC-SPEC.md) para la spec completa. La app nunca modifica archivos de la librería.

## Setup

```bash
conda env create -f environment.yml
conda activate mediaqc
pip install -e .
```

### Binarios externos

- **ffmpeg / ffprobe**: tienen que estar en el `PATH`, o configurar su ruta en Preferencias.
- **mkvmerge** (parte de [MKVToolNix](https://mkvtoolnix.download/)): igual, `PATH` o Preferencias. Sin él, el escaneo funciona pero no se guarda el ID de pista de mkvmerge (lo necesitan los comandos sugeridos de las fases 5+).
- **libmpv** (reproductor embebido, fase 3+): **no se versiona en git** (`libmpv-2.dll` pesa ~110MB, supera el límite de GitHub). Hay que colocarlo a mano:
  1. Descargar el paquete `mpv-dev-x86_64-*.7z` de la [última release de mpv-winbuild-cmake](https://github.com/shinchiro/mpv-winbuild-cmake/releases/latest).
  2. Extraer `libmpv-2.dll` (con 7-Zip; `py7zr` no soporta el filtro BCJ2 que usan estos archivos).
  3. Colocarlo en `bin/libmpv-2.dll`, en la raíz del proyecto.

  Sin este archivo, la app funciona igual (catálogo, TMDB) pero la vista de Revisión muestra un aviso en vez del reproductor.

## Correr en desarrollo

```bash
python -m mediaqc
```

## Tests

```bash
pytest tests/ -q
```
