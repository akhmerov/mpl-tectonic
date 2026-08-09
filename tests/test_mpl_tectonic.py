from __future__ import annotations

import json
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
    with pytest.raises(RuntimeError, match="requires the 'tectonic' executable on PATH"):
        _patch._validate_tectonic()


def test_unsupported_matplotlib_error_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mpl_tectonic import _patch

    monkeypatch.setattr(_patch, "version", lambda distribution: "3.12.0")
    with pytest.raises(RuntimeError, match=r"supports Matplotlib >=3\.11,<3\.12"):
        _patch._validate_matplotlib()


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
    base_fonts = [str(font.get_object().get("/BaseFont", "")) for font in fonts.values()]
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
