"""Qwen3.5-9B CAD visual-action policy fine-tuning package.

Everything here runs in the shared ML virtual environment
(``/home/armand0e/Documents/training/.venv``). The pure-Python data conversions are imported from
this repository's ``src/cad1000`` package so the trainer, evaluator and converter share a single
prompt/schema definition.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

__version__ = "0.1.0"
