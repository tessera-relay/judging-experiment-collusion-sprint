import os
import sys
import tempfile
from pathlib import Path

# The package lives in code/collusion_study; `code` itself clashes with a stdlib module name.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Tests that run real Colosseum episodes (with fake agents) need the Colosseum checkout.
COLOSSEUM_DIR = os.environ.get("COLOSSEUM_DIR")
if COLOSSEUM_DIR:
    # Made absolute before the chdir below, so a relative path keeps working.
    COLOSSEUM_DIR = os.environ["COLOSSEUM_DIR"] = str(Path(COLOSSEUM_DIR).resolve())
    sys.path.insert(0, COLOSSEUM_DIR)


# Colosseum's prompt logger writes logs/ under the working directory it sees at import,
# so the tests run from a scratch directory to keep that output out of the project.
os.chdir(tempfile.mkdtemp(prefix="collusion-study-tests-"))
