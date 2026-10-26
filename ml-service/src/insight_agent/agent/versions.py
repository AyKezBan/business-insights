"""Версия конвейера: хеш промптов и правил проверки плюс git SHA сборки."""
import hashlib
import os
from pathlib import Path

_HERE = Path(__file__).parent
_FILES = ("prompts.py", "analyst.py", "planner.py", "personalizer.py", "qa.py", "critic.py", "evidence.py",
          "orchestrator.py", "semantic.py", "spans.py", "../schemas.py", "../llm.py",
          "../materials/policy.py", "../materials/identity.py", "../tools/news.py", "../tools/bank.py")


def compute_version(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


PROMPT_VERSION = compute_version([_HERE / name for name in _FILES])


def git_sha() -> str:
    return os.environ.get("GIT_SHA", "unknown")
