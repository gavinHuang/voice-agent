"""
pytest configuration for shuo tests.

Adds the project root to sys.path so that the `ivr` package is importable
when running e2e tests that start the IVR mock server.
"""
import sys
from pathlib import Path

# Project root is two levels above this file: shuo/tests/conftest.py -> root
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
