"""本地与 CI 共用的验收入口：完整依赖、零跳过测试、独立穷举核验。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile

BASE = Path(__file__).resolve().parents[1]


class NoSkippedTests:
    def __init__(self):
        self.skipped = []

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)

    def pytest_collectreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)


def main():
    os.chdir(BASE)
    if sys.platform == "win32":
        runtime_temp = BASE / ".test-temp" / "runtime"
        runtime_temp.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(runtime_temp)
        os.environ["TMP"] = os.environ["TEMP"] = os.environ["TMPDIR"] = str(runtime_temp)
    try:
        import pytest
        import yaml
        from ortools.sat.python import cp_model
    except ImportError as exc:
        print(f"Missing test dependency: {exc}; install requirements-test.txt", file=sys.stderr)
        return 1
    plugin = NoSkippedTests()
    code = pytest.main(["tests", "-q", "--capture=sys", "--basetemp",
                        str(BASE / ".test-temp" / "pytest")],
                       plugins=[plugin])
    if code or plugin.skipped:
        if plugin.skipped:
            print("Skipped tests are not accepted:\n" + "\n".join(plugin.skipped),
                  file=sys.stderr)
        return int(code) or 1
    return subprocess.run([sys.executable, "-B", "docs/独立核验脚本-v10.py"],
                          cwd=BASE, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
