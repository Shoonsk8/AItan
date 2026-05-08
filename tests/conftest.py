"""Make the project root importable for tests without installing a package.
Also force Qt to run headless so the GUI tests don't need a display."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Run Qt offscreen by default; set in env BEFORE PyQt6 is imported by any test.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
