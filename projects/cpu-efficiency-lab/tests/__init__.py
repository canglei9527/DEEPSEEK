"""Test package.

Importing this package adds the project source directories to ``sys.path`` so
that ``python -m unittest discover -s tests -t .`` needs no PYTHONPATH. The
explicit ``-t .`` is required: without it ``unittest`` uses ``tests`` as the
top level directory and never imports this package.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _relative in ("src", "scripts"):
    _candidate = str(ROOT / _relative)
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)