"""Hash-addressed files shared by snapshot publication and source resolution."""
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


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
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError(f"Cannot overwrite immutable artifact {relative}") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
