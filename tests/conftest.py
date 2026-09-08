"""Keep test fixtures inside this project, without touching system temp data."""
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path():
    results = Path(__file__).resolve().parents[1] / 'test-results'
    results.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='case-', dir=results))
