"""Root conftest — ensures the backend directory is on sys.path for tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
