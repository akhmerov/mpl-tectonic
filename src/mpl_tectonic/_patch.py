"""Matplotlib 3.11 integration internals.

This module is imported lazily by :func:`mpl_tectonic.enable`, so importing the
public package does not import or modify Matplotlib.
"""

from __future__ import annotations

import functools
import hashlib
from importlib.metadata import version
import logging
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


_log = logging.getLogger(__name__)

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


def _xetex_unicode_character(line: str) -> str:
    """Translate an inputenc ``\\DeclareUnicodeCharacter`` to XeTeX."""
    match = re.fullmatch(
        r"\\DeclareUnicodeCharacter\{([0-9A-Fa-f]{4,6})\}\{(.*)\}", line.strip()
    )
    if match is None:
        raise RuntimeError(
            f"Matplotlib generated an unexpected Unicode declaration {line!r}; "
            "mpl-tectonic's Matplotlib compatibility assumptions no longer hold."
        )
    character = chr(int(match[1], 16))
    return f"\\catcode`\\{character}=\\active\\def{character}{{{match[2]}}}\n"


def _tectonic_tex_source(tex: str, fontsize: float) -> str:
    """Return Matplotlib's source with its UTF-8 setup adapted to XeTeX.

    XeTeX reads UTF-8 natively, so ``inputenc`` is dropped. Matplotlib's
    character declarations, such as the one rendering U+2212 as a math minus,
    become active-character definitions, and a character missing from its font
    is an error rather than silently dropped.
    """
    lines = TexManager._get_tex_source(tex, fontsize).splitlines(keepends=True)
    for index, line in enumerate(lines):
        if re.fullmatch(r"\\usepackage(?:\[[^]]*\])?\{inputenc\}", line.strip()):
            lines[index] = "\\tracinglostchars=3\n"
            index += 1
            while index < len(lines) and lines[index].lstrip().startswith(
                r"\DeclareUnicodeCharacter"
            ):
                lines[index] = _xetex_unicode_character(lines[index])
                index += 1
            return "".join(lines)
    raise RuntimeError(
        "Matplotlib generated TeX without the expected inputenc setup; "
        "mpl-tectonic's Matplotlib compatibility assumptions no longer hold."
    )


def _run_tectonic(*args: str) -> bytes:
    """Run Tectonic, logging its diagnostics instead of passing them through."""
    command = [_tectonic(), *args]
    result = subprocess.run(command, capture_output=True)
    diagnostics = result.stderr.decode("utf-8", "backslashreplace").strip()
    if diagnostics:
        _log.debug("%s:\n%s", " ".join(command), diagnostics)
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(command)!r} failed with exit status {result.returncode}. "
            "Check that Tectonic can access its bundle cache or network. "
            f"Tectonic reported:\n{diagnostics}"
        )
    return result.stdout


def _make_dvi_with_tectonic(cls: type[TexManager], tex: str, fontsize: float) -> str:
    """Compile Matplotlib's generated TeX to XDV using Tectonic.

    Like Matplotlib's own cache path, the XDV path hashes the compiled source,
    which here is the XeTeX adaptation. It therefore never reuses a DVI from
    ``latex`` or from a release of this package that compiled other source.
    """
    source = _tectonic_tex_source(tex, fontsize)
    filehash = hashlib.sha256(source.encode("utf-8"), usedforsecurity=False).hexdigest()
    cache_dir = cls._cache_dir / filehash[:2] / filehash[2:4]
    cache_dir.mkdir(parents=True, exist_ok=True)
    dvipath = (cache_dir / filehash).with_suffix(".dvi")
    if not dvipath.exists():
        with TemporaryDirectory(dir=dvipath.parent) as tmpdir:
            texfile = Path(tmpdir, "file.tex")
            texfile.write_text(source, encoding="utf-8")
            try:
                cls._run_checked_subprocess(
                    [
                        _tectonic(),
                        "--keep-logs",
                        "--outfmt=xdv",
                        "--outdir",
                        tmpdir,
                        texfile.name,
                    ],
                    tex,
                    cwd=tmpdir,
                )
            except RuntimeError as exc:
                # Tectonic's output omits some TeX errors, such as a character
                # missing from its font, so report the log's error lines too.
                logfile = Path(tmpdir, "file.log")
                if logfile.is_file():
                    errors = [
                        line
                        for line in logfile.read_text(errors="replace").splitlines()
                        if line.startswith("! ")
                    ]
                    if errors:
                        exc.add_note("TeX reported:\n" + "\n".join(errors))
                raise
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
        path = (font_dir / path.name).with_suffix(".otf")
        if not path.exists():
            path.write_bytes(_run_tectonic("-X", "bundle", "cat", path.name))
    return _ORIGINAL_FONT_FROM_XETEX.__func__(
        cls, scale, os.fsencode(path), subfont, effects
    )


@functools.cache
def _bundle_resource(name: str) -> str | None:
    """Return the materialized bundle resource, or ``None`` if it is absent.

    Results, including absences, are cached for the lifetime of the process,
    so an absent resource, such as the virtual font of a TFM font, costs one
    Tectonic call per process instead of one per label. Tectonic's ``bundle
    search`` cannot decide absence: Tectonic 0.17 lists only the part of the
    bundle index that it has loaded, which may be nothing.
    """
    path = TexManager._cache_dir / "tectonic-resources" / name
    if path.exists():
        return str(path)
    try:
        contents = _run_tectonic("-X", "bundle", "cat", name)
    except RuntimeError:
        return None
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(contents)
    return str(path)


def _find_in_tectonic_bundle(filename: str | bytes) -> str:
    """Materialize a TeX resource from the bundle used by Tectonic."""
    name = os.fsdecode(filename)
    if Path(name).name != name:
        raise FileNotFoundError(
            f"Tectonic bundle resources must be requested by basename; got {name!r}"
        )
    path = _bundle_resource(name)
    if path is None:
        raise FileNotFoundError(
            f"Tectonic's bundle does not provide {name!r}; the logger "
            "'mpl_tectonic' reports Tectonic's diagnostics at debug level"
        )
    return path


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
