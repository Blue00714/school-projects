"""Application package; load this checkout's optional local dependencies."""
import sys
from pathlib import Path
APP_ROOT = Path(__file__).resolve().parents[2]
PYDEPS = APP_ROOT / 'vendor' / 'python-packages'
if PYDEPS.is_dir() and str(PYDEPS) not in sys.path:
    sys.path.insert(0, str(PYDEPS))
