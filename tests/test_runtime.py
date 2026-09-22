"""Cross-platform startup checks, including a Windows-like missing OS TZ database."""
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfoNotFoundError

import pytest
import run

ROOT = Path(__file__).resolve().parent.parent


def test_timezone_package_is_an_explicit_runtime_dependency():
    requirements = (ROOT / 'requirements.txt').read_text().splitlines()
    assert any(line.startswith('tzdata') for line in requirements)


def test_nairobi_works_without_any_system_timezone_directory():
    # Empty PYTHONTZPATH forces zoneinfo to use the tzdata wheel, as on Windows.
    code = '''from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, TZPATH
assert not TZPATH
zone = ZoneInfo("Africa/Nairobi")
assert datetime(2026, 9, 15, 12, 0, tzinfo=zone).utcoffset() == timedelta(hours=3)
from backend.source import ZONE
assert ZONE.key == "Africa/Nairobi"
print("tzdata fallback and source adapter import: OK")
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                            env={**os.environ, 'PYTHONTZPATH': ''}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'OK' in result.stdout


def test_missing_timezone_preflight_gives_an_actionable_install_command(monkeypatch):
    def unavailable(name):
        raise ZoneInfoNotFoundError(name)
    monkeypatch.setattr(run, 'ZoneInfo', unavailable)
    with pytest.raises(SystemExit) as error:
        run.verify_timezone()
    assert 'Africa/Nairobi' in str(error.value)
    assert '-m pip install tzdata' in str(error.value)
    assert 'database has not been modified' in str(error.value)
