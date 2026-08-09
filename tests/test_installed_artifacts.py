"""Inspect and exercise both distribution artifacts in isolated environments."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def _single(pattern: str) -> Path:
    matches = list(DIST.glob(pattern))
    if len(matches) != 1:
        raise AssertionError(f"expected one {pattern} artifact, found {matches}")
    return matches[0]


def _check_contents(wheel: Path, sdist: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "mpl_tectonic/__init__.py" in names
    assert "mpl_tectonic/_patch.py" in names
    assert any(name.endswith(".dist-info/METADATA") for name in names)
    assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
    assert not any(name.startswith("tests/") for name in names)

    with tarfile.open(sdist, "r:gz") as archive:
        names = {Path(name).parts[1:] for name in archive.getnames() if "/" in name}
    required = {
        ("pyproject.toml",),
        ("README.md",),
        ("LICENSE",),
        ("pixi.toml",),
        ("pixi.lock",),
        (".pre-commit-config.yaml",),
        ("src", "mpl_tectonic", "__init__.py"),
        ("src", "mpl_tectonic", "_patch.py"),
        ("tests", "test_mpl_tectonic.py"),
        ("tests", "installed_runtime_check.py"),
        ("tests", "test_conda_artifact.py"),
    }
    assert required <= names


def _exercise(artifact: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="mpl-tectonic-artifact-") as tmpdir:
        environment = Path(tmpdir) / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--system-site-packages", environment],
            check=True,
        )
        interpreter = environment / "bin" / "python"
        subprocess.run(
            [
                interpreter,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-build-isolation",
                artifact,
            ],
            check=True,
        )
        purelib = subprocess.run(
            [
                interpreter,
                "-c",
                "import sysconfig; print(sysconfig.get_paths()['purelib'])",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            [interpreter, ROOT / "tests" / "installed_runtime_check.py", purelib],
            check=True,
            cwd=tmpdir,
        )


def main() -> None:
    wheel = _single("*.whl")
    sdist = _single("*.tar.gz")
    _check_contents(wheel, sdist)
    _exercise(wheel)
    _exercise(sdist)


if __name__ == "__main__":
    main()
