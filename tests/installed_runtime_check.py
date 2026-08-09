"""Runtime checks executed by an isolated interpreter for a built artifact."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import xml.etree.ElementTree as ET


expected_site = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory() as config_dir:
    os.environ["MPLCONFIGDIR"] = config_dir

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import mpl_tectonic
    from pypdf import PdfReader

    imported = Path(mpl_tectonic.__file__).resolve()
    if not imported.is_relative_to(expected_site):
        raise AssertionError(
            f"imported {imported}, expected installation in {expected_site}"
        )

    mpl_tectonic.enable()
    mpl_tectonic.enable()

    def make_figure():
        fig, ax = plt.subplots()
        ax.set_xlabel(r"energy $E / \Delta$")
        ax.set_title(r"$E_n = \hbar\omega(n + \frac{1}{2})$")
        return fig

    output_dir = Path(config_dir) / "output"
    output_dir.mkdir()
    with plt.rc_context({"text.usetex": True}):
        for suffix in ("png", "svg", "pdf"):
            figure = make_figure()
            figure.savefig(output_dir / f"render.{suffix}")
            plt.close(figure)

        figure, axes = plt.subplots()
        axes.set_title("Ångström")
        try:
            figure.savefig(output_dir / "unsupported.pdf")
        except RuntimeError as exc:
            if "glyph ID exceeds 255" not in str(exc):
                raise
        else:
            raise AssertionError("non-ASCII native PDF unexpectedly succeeded")
        finally:
            plt.close(figure)

    assert (output_dir / "render.png").read_bytes().startswith(b"\x89PNG")
    svg_root = ET.parse(output_dir / "render.svg").getroot()
    ns = "{http://www.w3.org/2000/svg}"
    assert len(svg_root.findall(f".//{ns}path")) > 10
    assert not svg_root.findall(f".//{ns}text")
    pdf = PdfReader(output_dir / "render.pdf", strict=True)
    assert len(pdf.pages) == 1
    fonts = pdf.pages[0]["/Resources"]["/Font"]
    assert any(
        "LMSans" in str(font.get_object().get("/BaseFont", ""))
        for font in fonts.values()
    )
