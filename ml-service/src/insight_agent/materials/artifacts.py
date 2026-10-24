"""Hash-addressed files shared by snapshot publication and source resolution."""
import hashlib
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Artifact path escapes corpus root")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Artifact symlink escapes corpus root")
    return path


def write_immutable(root: Path, relative: str, data: bytes) -> Path:
    path = safe_path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f"Cannot overwrite immutable artifact {relative}") from None
    return path
