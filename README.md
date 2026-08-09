# mpl-tectonic

`mpl-tectonic` makes Matplotlib's `text.usetex` pipeline use
[Tectonic](https://tectonic-typesetting.github.io/) instead of a traditional
`latex` executable. It is a small, opt-in compatibility layer for Matplotlib
3.11.

## Installation

Install the Python package together with its tightly constrained Matplotlib
dependency:

```console
python -m pip install mpl-tectonic
```

The `tectonic` executable is also required on `PATH`. It is an external runtime
dependency, not a Python package. This repository's Pixi environment installs
the validated Tectonic 0.15 release.

## Usage

Importing `mpl_tectonic` does not change Matplotlib. Call `enable()` once before
rendering any text with `usetex`:

```python
import matplotlib.pyplot as plt
import mpl_tectonic

mpl_tectonic.enable()

with plt.rc_context({"text.usetex": True}):
    fig, ax = plt.subplots()
    ax.set_xlabel(r"energy $E / \Delta$")
    ax.set_title(r"$E_n = \hbar\omega(n + \frac{1}{2})$")
    fig.savefig("figure.pdf")  # PNG and SVG work too.
```

`enable()` is idempotent. It validates Matplotlib and Tectonic before changing
anything, then installs the integration process-wide. The intentionally small
public runtime API contains only `mpl_tectonic.enable()`; there is no disable,
status, or context-manager API.

## How it works

Matplotlib 3.11 can read XeTeX's XDV format, but its `usetex` manager invokes
`latex` and expects a `.dvi` cache file. `mpl-tectonic` asks Tectonic for XDV,
places it at the cache path Matplotlib expects, and adapts the remaining private
integration points:

- pdfLaTeX-only UTF-8 declarations are removed from Matplotlib's generated TeX;
- native fonts and classic TeX resources are materialized from Tectonic's
  bundle, keeping resource resolution consistent with the compiler;
- XDV native OpenType fonts use Matplotlib's TrueType PDF embedding path and
  safe PDF resource names;
- Matplotlib's opaque one-byte native-glyph failure is replaced with an
  actionable error.

SVG text is emitted as vector glyph paths. Native PDF output contains embedded,
subsetted fonts rather than converting an SVG or raster image.

## Compatibility and limitations

- Only Matplotlib `>=3.11,<3.12` is supported. The narrow range is deliberate:
  the package patches six private Matplotlib integration points.
- Activation is process-global and permanent. Call `enable()` during
  single-threaded application setup, before concurrent rendering begins.
- PNG, path-based SVG, and native PDF are supported.
- Every label must be valid TeX once `text.usetex` is enabled. For example,
  literal `e^2/h` must be written as `$e^2/h$`; the package intentionally does
  not rewrite project text.
- The ordinary research-figure PDF regime—ASCII prose plus TeX math
  commands—is supported. Literal or TeX-accented non-ASCII prose may select an
  XDV native-font glyph ID above 255, which Matplotlib 3.11's PDF renderer
  cannot encode. In that case `mpl-tectonic` raises an explicit error advising
  SVG output or supported TeX math commands. SVG remains the path-based
  fallback for such labels.
- Tectonic may need network access on its first run to obtain its default
  bundle, depending on how Tectonic was installed and cached.

## Development

The locked Pixi workflow builds both distributions, tests the installed
development package, inspects wheel and sdist contents, and installs and runs
the full output checks from each artifact:

```console
pixi run test
```

The same manifest also builds a noarch conda package with explicit Python,
Matplotlib, and Tectonic runtime dependencies:

```console
pixi run build-conda
```

The test task includes the local pre-commit suite: Ruff linting, a Ruff format
check, and codespell. To apply formatting or install the Git hook explicitly,
use:

```console
pixi run format
pixi run install-hooks
```

GitHub Actions runs the same workflow. Version tags matching `v*` trigger a
separate Trusted Publishing workflow: it builds once, tests those exact wheel
and sdist files, transfers them as one immutable Actions artifact, and publishes
without an API-token secret. The `pypi` environment must be configured for this
repository in PyPI before a release can succeed.

## License

BSD-3-Clause. See [LICENSE](LICENSE).
