import sys
from pathlib import Path

# Make `import skills` work when pytest is started from this folder or from elsewhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
