from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import matplotlib
import pytest
from pypdf import PdfReader

matplotlib.use("Agg")
import matplotlib.dviread as dviread
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfFile, RendererPdf
from matplotlib.dviread import DviFont
from matplotlib.texmanager import TexManager

import mpl_tectonic


def test_import_has_no_matplotlib_side_effects() -> None:
    code = """
import json
import sys
before = set(sys.modules)
import mpl_tectonic
after = set(sys.modules)
print(json.dumps({
    "matplotlib_loaded": any(
        name == "matplotlib" or name.startswith("matplotlib.")
        for name in after - before
    ),
    "public": mpl_tectonic.__all__,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    observed = json.loads(result.stdout)
    assert observed == {"matplotlib_loaded": False, "public": ["enable"]}


def test_public_api_is_intentionally_minimal() -> None:
    assert mpl_tectonic.__all__ == ["enable"]
    assert callable(mpl_tectonic.enable)
    assert not hasattr(mpl_tectonic, "disable")
    assert not hasattr(mpl_tectonic, "is_active")
    assert not hasattr(mpl_tectonic, "tectonic_usetex")


def test_missing_tectonic_error_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    from mpl_tectonic import _patch

    monkeypatch.setattr(_patch.shutil, "which", lambda executable: None)
    with pytest.raises(
        RuntimeError, match="requires the 'tectonic' executable on PATH"
    ):
        _patch._validate_tectonic()


def test_unsupported_matplotlib_error_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mpl_tectonic import _patch

    monkeypatch.setattr(_patch, "version", lambda distribution: "3.12.0")
    with pytest.raises(RuntimeError, match=r"supports Matplotlib >=3\.11,<3\.12"):
        _patch._validate_matplotlib()


def test_tex_source_adapts_matplotlibs_pdftex_unicode_setup() -> None:
    from mpl_tectonic import _patch

    custom_declaration = r"\DeclareUnicodeCharacter{1234}{custom}"
    with matplotlib.rc_context({"text.latex.preamble": custom_declaration}):
        source = _patch._tectonic_tex_source(r"$x$", 10)

    assert r"\usepackage[utf8]{inputenc}" not in source
    assert r"\DeclareUnicodeCharacter{2212}" not in source
    assert "\\catcode`\\\N{MINUS SIGN}=\\active" in source
    assert "\\tracinglostchars=3" in source
    assert custom_declaration in source


@pytest.fixture
def fresh_bundle_cache() -> Iterator[None]:
    from mpl_tectonic import _patch

    def clear() -> None:
        for value in vars(_patch).values():
            if hasattr(value, "cache_clear"):
                value.cache_clear()

    clear()
    yield
    clear()


def _fake_tectonic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    files: dict[str, bytes],
    stderr: bytes = b"",
) -> list[list[str]]:
    from mpl_tectonic import _patch

    calls = []

    def run(command: list[str], *, check: bool = False, **kwargs: object):
        calls.append(command)
        match command[1:]:
            case ["-X", "bundle", "cat", name] if name in files:
                stdout, returncode = files[name], 0
            case _:
                stdout, returncode = b"", 1
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, command, stdout, stderr)
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(TexManager, "_cache_dir", tmp_path)
    monkeypatch.setattr(_patch, "_tectonic", lambda: "/usr/bin/tectonic")
    monkeypatch.setattr(_patch.subprocess, "run", run)
    return calls


def test_tex_resources_come_only_from_tectonic_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_bundle_cache: None
) -> None:
    from mpl_tectonic import _patch

    calls = _fake_tectonic(monkeypatch, tmp_path, {"pdftex.map": b"bundle resource"})

    resolved = Path(_patch._find_in_tectonic_bundle("pdftex.map"))

    assert resolved.read_bytes() == b"bundle resource"
    assert calls == [["/usr/bin/tectonic", "-X", "bundle", "cat", "pdftex.map"]]


def test_tex_resource_hits_and_misses_are_looked_up_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fresh_bundle_cache: None
) -> None:
    from mpl_tectonic import _patch

    calls = _fake_tectonic(monkeypatch, tmp_path, {"cmr10.tfm": b"tfm"})

    for _ in range(3):
        assert Path(_patch._find_in_tectonic_bundle(b"cmr10.tfm")).is_file()
        for name in ("cmr10.vf", b"cmr10.vf", "lmr10.vf"):
            with pytest.raises(FileNotFoundError):
                _patch._find_in_tectonic_bundle(name)

    assert calls == [
        ["/usr/bin/tectonic", "-X", "bundle", "cat", "cmr10.tfm"],
        ["/usr/bin/tectonic", "-X", "bundle", "cat", "cmr10.vf"],
        ["/usr/bin/tectonic", "-X", "bundle", "cat", "lmr10.vf"],
    ]


def test_tectonic_diagnostics_are_logged_and_reported_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fresh_bundle_cache: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mpl_tectonic import _patch

    _fake_tectonic(
        monkeypatch, tmp_path, {"cmr10.tfm": b"tfm"}, stderr=b"note: downloading"
    )
    with caplog.at_level(logging.DEBUG, logger="mpl_tectonic"):
        _patch._find_in_tectonic_bundle("cmr10.tfm")
    assert "note: downloading" in caplog.text

    with pytest.raises(RuntimeError, match="exit status 1(.|\n)*note: downloading"):
        _patch._run_tectonic("-X", "bundle", "cat", "absent.tfm")


def test_enable_installs_every_hook_and_is_idempotent() -> None:
    before = {
        "make_dvi": TexManager.__dict__["make_dvi"],
        "from_xetex": DviFont.__dict__["from_xetex"],
        "find_tex_file": dviread.find_tex_file,
        "embed": PdfFile._embedTeXFont,
        "font_name": PdfFile.dviFontName,
        "draw_tex": RendererPdf.draw_tex,
    }
    mpl_tectonic.enable()
    enabled = {
        "make_dvi": TexManager.__dict__["make_dvi"],
        "from_xetex": DviFont.__dict__["from_xetex"],
        "find_tex_file": dviread.find_tex_file,
        "embed": PdfFile._embedTeXFont,
        "font_name": PdfFile.dviFontName,
        "draw_tex": RendererPdf.draw_tex,
    }
    assert all(enabled[name] is not original for name, original in before.items())

    mpl_tectonic.enable()
    repeated = {
        "make_dvi": TexManager.__dict__["make_dvi"],
        "from_xetex": DviFont.__dict__["from_xetex"],
        "find_tex_file": dviread.find_tex_file,
        "embed": PdfFile._embedTeXFont,
        "font_name": PdfFile.dviFontName,
        "draw_tex": RendererPdf.draw_tex,
    }
    assert all(repeated[name] is value for name, value in enabled.items())


def _figure() -> plt.Figure:
    fig, ax = plt.subplots()
    ax.set_xlabel(r"energy $E / \Delta$")
    ax.set_title(r"$E_n = \hbar\omega(n + \frac{1}{2})$")
    return fig


def test_tectonic_renders_png(tmp_path: Path) -> None:
    mpl_tectonic.enable()
    output = tmp_path / "render.png"
    with plt.rc_context({"text.usetex": True}):
        fig = _figure()
        fig.savefig(output)
        plt.close(fig)

    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert output.stat().st_size > 1_000


def test_tectonic_renders_path_based_svg(tmp_path: Path) -> None:
    mpl_tectonic.enable()
    output = tmp_path / "render.svg"
    with plt.rc_context({"text.usetex": True}):
        fig = _figure()
        fig.savefig(output)
        plt.close(fig)

    root = ET.parse(output).getroot()
    namespace = "{http://www.w3.org/2000/svg}"
    assert root.tag == f"{namespace}svg"
    assert not root.findall(f".//{namespace}text")
    assert len(root.findall(f".//{namespace}path")) > 10
    assert output.stat().st_size > 10_000


def _has_embedded_font_file(font: object) -> bool:
    obj = font.get_object()
    descriptor = obj.get("/FontDescriptor")
    if descriptor is not None:
        descriptor = descriptor.get_object()
        if any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3")):
            return True
    descendants = obj.get("/DescendantFonts", [])
    return any(_has_embedded_font_file(descendant) for descendant in descendants)


def test_tectonic_renders_native_pdf_with_embedded_fonts(tmp_path: Path) -> None:
    mpl_tectonic.enable()
    output = tmp_path / "render.pdf"
    with plt.rc_context({"text.usetex": True}):
        fig = _figure()
        fig.savefig(output)
        plt.close(fig)

    data = output.read_bytes()
    assert data.startswith(b"%PDF-")
    assert data.rstrip().endswith(b"%%EOF")
    assert output.stat().st_size > 10_000

    reader = PdfReader(output, strict=True)
    assert len(reader.pages) == 1
    fonts = reader.pages[0]["/Resources"]["/Font"]
    base_fonts = [
        str(font.get_object().get("/BaseFont", "")) for font in fonts.values()
    ]
    assert any("LMSans" in name for name in base_fonts)
    assert any(_has_embedded_font_file(font) for font in fonts.values())


def test_pdf_rejects_unsupported_native_glyph_with_clear_error(tmp_path: Path) -> None:
    mpl_tectonic.enable()
    output = tmp_path / "unsupported.pdf"
    with plt.rc_context({"text.usetex": True}):
        fig, ax = plt.subplots()
        ax.set_title("Ångström")
        with pytest.raises(
            RuntimeError,
            match="glyph ID exceeds 255.*Save this figure as SVG instead",
        ):
            fig.savefig(output)
        plt.close(fig)


def _glyphs(tex: str) -> list[tuple[bytes, int]]:
    with dviread.Dvi(TexManager.make_dvi(tex, 10), None) as dvi:
        page = next(iter(dvi))
    return [(text.font.texname, text.glyph) for text in page.text]


@pytest.mark.parametrize(
    "preamble", ["", r"\usepackage[T1]{fontenc}\usepackage{lmodern}"]
)
def test_unicode_minus_renders_as_math_minus(preamble: str) -> None:
    mpl_tectonic.enable()
    with plt.rc_context({"text.latex.preamble": preamble}):
        unicode_minus = _glyphs("$\\mathdefault{\N{MINUS SIGN}1.2}$")
        ascii_minus = _glyphs(r"$\mathdefault{-1.2}$")

    assert len(ascii_minus) == 4
    assert unicode_minus == ascii_minus


def test_dvi_from_matplotlibs_source_is_not_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mpl_tectonic.enable()
    monkeypatch.setattr(TexManager, "_cache_dir", tmp_path)
    tex = "$\\mathdefault{\N{MINUS SIGN}2.5}$"
    # mpl-tectonic 0.1.1 cached XDV at Matplotlib's path for its source.
    stale = TexManager._get_base_path(tex, 10).with_suffix(".dvi")
    stale.write_bytes(b"stale")

    assert Path(TexManager.make_dvi(tex, 10)).read_bytes() != b"stale"


def test_glyph_missing_from_font_is_an_error() -> None:
    mpl_tectonic.enable()
    with pytest.raises(RuntimeError, match="Missing character"):
        TexManager.make_dvi("\N{CJK UNIFIED IDEOGRAPH-4E2D}", 10)


def test_cold_tectonic_cache_writes_nothing_to_stderr(tmp_path: Path) -> None:
    code = """
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mpl_tectonic

mpl_tectonic.enable()
with plt.rc_context({"text.usetex": True}):
    fig, ax = plt.subplots()
    ax.set_xlabel(r"energy $E / \\Delta$")
    fig.savefig(sys.argv[1])
"""
    output = tmp_path / "render.pdf"
    result = subprocess.run(
        [sys.executable, "-c", code, output],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "MPLCONFIGDIR": str(tmp_path / "mplconfig"),
            "TECTONIC_CACHE_DIR": str(tmp_path / "tectonic"),
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert (tmp_path / "tectonic").is_dir()
    assert output.read_bytes().startswith(b"%PDF-")
