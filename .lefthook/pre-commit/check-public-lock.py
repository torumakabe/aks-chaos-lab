# /// script
# requires-python = ">=3.14"
# ///
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.public_lock import main  # noqa: E402

raise SystemExit(main(["staged"]))
