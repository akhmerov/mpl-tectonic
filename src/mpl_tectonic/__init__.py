"""Opt-in Tectonic support for Matplotlib's ``usetex`` mode."""

from __future__ import annotations

__all__ = ["enable"]


def enable() -> None:
    """Enable Tectonic for Matplotlib's ``usetex`` rendering process-wide.

    The operation validates the supported Matplotlib version and the Tectonic
    executable before installing the integration. Repeated calls are safe and
    have no additional effect.
    """
    from ._patch import enable as _enable

    _enable()
