"""
pytest configuration for shuo tests.

Adds the project root to sys.path so that the `monitor` and `simulator`
packages are importable when running tests.
"""
import sys
from pathlib import Path

# Project root is one level above this file: tests/conftest.py -> root
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
