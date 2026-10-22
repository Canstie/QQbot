"""Temporary images owned by one send operation."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CachedImages:
    directory: Path | None
    paths: tuple[Path, ...]

    def cleanup(self) -> None:
        if self.directory is not None:
            shutil.rmtree(self.directory, ignore_errors=True)
