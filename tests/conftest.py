import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "skill" / "scripts"))


@pytest.fixture(scope="session")
def ollama_running():
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, timeout=5)
        if result.returncode != 0:
            pytest.skip("Ollama not running — start with: ollama serve")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("Ollama not available")
