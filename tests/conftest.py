import sys
from pathlib import Path

# `src/` düzenini (kurulum gerektirmeden) testlere tanıt
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
