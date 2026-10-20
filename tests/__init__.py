"""Tests import the repo's top-level modules and each other by bare name; put both dirs on the path."""
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
for p in (_here.parent, _here):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
