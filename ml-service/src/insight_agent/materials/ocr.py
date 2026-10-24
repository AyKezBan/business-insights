"""Local OCR only: subprocess arguments never contain shell commands."""
from pathlib import Path
from functools import cached_property
import subprocess
from typing import Protocol


class OCRBackend(Protocol):
    def recognize(self, image_path: Path, languages: str = "rus+eng") -> str: ...


class TesseractOCR:
    def __init__(self, executable: str = "tesseract", timeout_seconds: float = 120):
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._languages: set[str] | None = None

    @cached_property
    def tool_versions(self) -> dict[str, str]:
        versions = {}
        for key, args in (("tesseract", [self.executable, "--version"]), ("poppler", ["pdftoppm", "-v"])):
            result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=10)
            versions[key] = (result.stdout or result.stderr).splitlines()[0]
        return versions

    @property
    def version(self) -> str:
        return self.tool_versions["tesseract"]

    def recognize(self, image_path: Path, languages: str = "rus+eng") -> str:
        if self._languages is None:
            result = subprocess.run([self.executable, "--list-langs"], check=True,
                capture_output=True, text=True, timeout=self.timeout_seconds)
            self._languages = set(result.stdout.splitlines()[1:])
        missing = set(languages.split("+")) - self._languages
        if missing:
            raise RuntimeError("Missing Tesseract languages: " + ", ".join(sorted(missing)))
        result = subprocess.run([self.executable, str(image_path), "stdout", "-l", languages, "--psm", "3"],
            capture_output=True, text=True, check=True, timeout=self.timeout_seconds)
        return result.stdout.strip()
