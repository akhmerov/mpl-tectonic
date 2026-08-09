"""Install and exercise the exact conda artifact built by Pixi."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    artifacts = list((ROOT / "build" / "conda").glob("*.conda"))
    if len(artifacts) != 1:
        raise AssertionError(f"expected one conda artifact, found {artifacts}")

    environment = os.environ.copy()
    environment["PIXI_CACHE_DIR"] = str(ROOT / ".pixi" / "cache")
    subprocess.run(
        [
            "pixi",
            "exec",
            "--force-reinstall",
            "--spec",
            str(artifacts[0]),
            "--spec",
            "pypdf",
            "--spec",
            "python=3.13",
            "python",
            str(ROOT / "tests" / "installed_runtime_check.py"),
        ],
        check=True,
        cwd=ROOT,
        env=environment,
    )


if __name__ == "__main__":
    main()
