"""Matplotlib 3.11 integration internals.

This module is imported lazily by :func:`mpl_tectonic.enable`, so importing the
public package does not import or modify Matplotlib.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import version
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import threading

import matplotlib.backends.backend_pdf as backend_pdf
import matplotlib.dviread as dviread
from matplotlib.backends.backend_pdf import PdfFile, RendererPdf
from matplotlib.dviread import DviFont
from matplotlib.font_manager import FontPath
from matplotlib.texmanager import TexManager


_SUPPORTED_MATPLOTLIB = ">=3.11,<3.12"
_enabled = False
_enable_lock = threading.Lock()
_tectonic_executable: str | None = None

_ORIGINAL_FONT_FROM_XETEX = DviFont.__dict__["from_xetex"]
_ORIGINAL_EMBED_TEX_FONT = PdfFile._embedTeXFont
_ORIGINAL_DVI_FONT_NAME = PdfFile.dviFontName
_ORIGINAL_DRAW_TEX = RendererPdf.draw_tex


def _validate_matplotlib() -> None:
    installed = version("matplotlib")
    match = re.match(r"^(\d+)\.(\d+)", installed)
    if match is None or tuple(map(int, match.groups())) != (3, 11):
        raise RuntimeError(
            f"mpl-tectonic supports Matplotlib {_SUPPORTED_MATPLOTLIB}; "
            f"found {installed}. Install a compatible Matplotlib release."
        )


def _validate_tectonic() -> str:
    executable = shutil.which("tectonic")
    if executable is None:
        raise RuntimeError(
            "mpl-tectonic requires the 'tectonic' executable on PATH. "
            "Install Tectonic and call mpl_tectonic.enable() again."
        )
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"mpl-tectonic could not execute {executable!r} successfully with "
            "'--version'. Check the Tectonic installation."
        ) from exc
    reported = f"{result.stdout}\n{result.stderr}".strip().lower()
    if "tectonic" not in reported:
        raise RuntimeError(
            f"The executable at {executable!r} did not identify itself as "
            "Tectonic. Check PATH and call mpl_tectonic.enable() again."
        )
    return executable


def _tectonic() -> str:
    if _tectonic_executable is None:  # pragma: no cover - installation invariant
        raise RuntimeError("mpl-tectonic's Tectonic executable was not initialized")
    return _tectonic_executable


def _tectonic_tex_source(tex: str, fontsize: float) -> str:
    """Return Matplotlib's source without its pdfLaTeX-only UTF-8 setup."""
    lines = TexManager._get_tex_source(tex, fontsize).splitlines(keepends=True)
    for index, line in enumerate(lines):
        if re.fullmatch(r"\\usepackage(?:\[[^]]*\])?\{inputenc\}", line.strip()):
            del lines[index]
            while index < len(lines) and lines[index].lstrip().startswith(
                r"\DeclareUnicodeCharacter"
            ):
                del lines[index]
            return "".join(lines)
    raise RuntimeError(
        "Matplotlib generated TeX without the expected inputenc setup; "
        "mpl-tectonic's Matplotlib compatibility assumptions no longer hold."
    )


def _make_dvi_with_tectonic(cls: type[TexManager], tex: str, fontsize: float) -> str:
    """Compile Matplotlib's generated TeX to XDV using Tectonic."""
    dvipath = cls._get_base_path(tex, fontsize).with_suffix(".dvi")
    if not dvipath.exists():
        with TemporaryDirectory(dir=dvipath.parent) as tmpdir:
            texfile = Path(tmpdir, "file.tex")
            texfile.write_text(_tectonic_tex_source(tex, fontsize), encoding="utf-8")
            cls._run_checked_subprocess(
                [
                    _tectonic(),
                    "--outfmt=xdv",
                    "--outdir",
                    tmpdir,
                    texfile.name,
                ],
                tex,
                cwd=tmpdir,
            )
            xdvfile = Path(tmpdir, "file.xdv")
            if not xdvfile.is_file():
                raise RuntimeError(
                    f"Tectonic did not produce the expected {xdvfile.name}"
                )
            xdvfile.replace(dvipath)
            texfile.replace(dvipath.with_suffix(".tex"))
    return str(dvipath)


def _font_from_tectonic_bundle(
    cls: type[DviFont], scale: float, texname: bytes, subfont: int, effects: dict
) -> DviFont:
    """Resolve Tectonic's basename-only native-font records."""
    path = Path(os.fsdecode(texname))
    if not path.exists() and path.name == str(path):
        font_dir = TexManager._cache_dir / "tectonic-fonts"
        font_dir.mkdir(exist_ok=True)
        path = font_dir / path.name
        if not path.exists():
            path.write_bytes(
                subprocess.check_output([_tectonic(), "-X", "bundle", "cat", path.name])
            )
    return _ORIGINAL_FONT_FROM_XETEX.__func__(
        cls, scale, os.fsencode(path), subfont, effects
    )


def _find_in_tectonic_bundle(filename: str | bytes) -> str:
    """Materialize a TeX resource from the bundle used by Tectonic."""
    name = os.fsdecode(filename)
    if Path(name).name != name:
        raise FileNotFoundError(
            f"Tectonic bundle resources must be requested by basename; got {name!r}"
        )
    resource_dir = TexManager._cache_dir / "tectonic-resources"
    resource_dir.mkdir(exist_ok=True)
    path = resource_dir / name
    if not path.exists():
        try:
            path.write_bytes(
                subprocess.check_output(
                    [_tectonic(), "-X", "bundle", "cat", name],
                    stderr=subprocess.DEVNULL,
                )
            )
        except subprocess.CalledProcessError:
            path.unlink(missing_ok=True)
            raise FileNotFoundError(
                f"Could not materialize {name!r} from Tectonic's bundle. Check "
                "that the resource exists and that Tectonic can access its "
                "bundle cache or network."
            ) from None
    return str(path)


def _embed_tex_font_with_opentype(self: PdfFile, dvifont: DviFont):
    """Embed XDV native fonts through Matplotlib's existing TTF PDF path."""
    if not dvifont.texname.startswith(b"["):
        return _ORIGINAL_EMBED_TEX_FONT(self, dvifont)

    source = FontPath(dvifont.fname, dvifont.face_index)
    target = FontPath(str(dvifont.resolve_path()), dvifont.subfont)
    tracker = self._character_tracker
    tracker.used[target] = tracker.used[source]
    tracker.glyph_maps[target] = tracker.glyph_maps[source]
    return self.embedTTF(target, 0, tracker.used[target][0])


def _dvi_font_name_with_safe_xdv_key(self: PdfFile, dvifont: DviFont):
    """Avoid bracketed XDV font paths as raw PDF dictionary names."""
    if not dvifont.texname.startswith(b"["):
        return _ORIGINAL_DVI_FONT_NAME(self, dvifont)
    digest = hashlib.sha256(dvifont.texname).hexdigest()[:16]
    pdfname = backend_pdf.Name(f"F-XDV-{digest}")
    self._dviFontInfo[pdfname] = dvifont
    return backend_pdf.Name(pdfname)


def _draw_tex_with_xdv_error(self: RendererPdf, *args, **kwargs):
    """Explain Matplotlib's one-byte limitation for XDV native glyphs."""
    try:
        return _ORIGINAL_DRAW_TEX(self, *args, **kwargs)
    except ValueError as exc:
        if str(exc) != "bytes must be in range(0, 256)":
            raise
        raise RuntimeError(
            "Matplotlib's PDF backend cannot encode this Tectonic XDV native-font "
            "glyph because its glyph ID exceeds 255. Save this figure as SVG "
            "instead, or replace non-ASCII prose with supported TeX math commands."
        ) from exc


def enable() -> None:
    """Validate dependencies and install all process-global integration hooks."""
    global _enabled, _tectonic_executable
    with _enable_lock:
        if _enabled:
            return

        _validate_matplotlib()
        _tectonic_executable = _validate_tectonic()

        TexManager.make_dvi = classmethod(_make_dvi_with_tectonic)
        DviFont.from_xetex = classmethod(_font_from_tectonic_bundle)
        dviread.find_tex_file = _find_in_tectonic_bundle
        PdfFile._embedTeXFont = _embed_tex_font_with_opentype
        PdfFile.dviFontName = _dvi_font_name_with_safe_xdv_key
        RendererPdf.draw_tex = _draw_tex_with_xdv_error
        _enabled = True
