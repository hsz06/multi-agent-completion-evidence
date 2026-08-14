"""DynamicDependencyExtractor: test -> code edges via real `coverage` runs.

Each named test set is executed once with coverage; the set of executed repo
source files becomes the dynamic dependency targets. Deterministic for a pinned
commit.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path


class DynamicDependencyExtractor:
    def __init__(self, repo_root: Path, pkg: str):
        self.root = Path(repo_root).resolve()
        self.pkg = pkg
        # run coverage in a temp copy so we never write into the source clone
        self._cov_file = self.root / ".rr_measure.coverage"

    def measure(self, targets=(), extra=()) -> dict:
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp(prefix="rr-cov-"))
        try:
            rcopy = tmp / self.pkg
            shutil.copytree(self.root, rcopy)
            cov_file = rcopy / ".rr_measure.coverage"
            rc = rcopy / ".rr_coveragerc"
            rc.write_text(
                "[run]\nsource = %s\ndata_file = %s\nparallel = False\n"
                "disable_warnings = no-data-collected\n"
                % (self.pkg, cov_file.name))
            env = {"PYTHONDONTWRITEBYTECODE": "1",
                   "COVERAGE_PROCESS_START": ""}
            cmd = ["python3", "-m", "coverage", "run", "--rcfile", rc.name,
                   "--source", self.pkg,
                   "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
                   "-p", "no:cov", "--tb=short", "-o", "addopts="]
            cmd += [str(t) for t in targets]
            cmd += [str(e) for e in extra]
            t0 = time.time()
            cp = subprocess.run(cmd, cwd=str(rcopy), capture_output=True,
                                text=True, timeout=180, env=env)
            dur = time.time() - t0
            m = re.search(r"(\d+) failed", cp.stdout)
            self._cov_file = cov_file
            self._cov_root = rcopy
            covered = self._covered_files()
            return {"returncode": cp.returncode,
                    "result": "PASS" if cp.returncode == 0 else "FAIL",
                    "tests_failed": int(m.group(1)) if m else 0,
                    "covered_files": covered,
                    "duration_s": round(dur, 3)}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _covered_files(self) -> list[str]:
        import coverage as coverage_mod
        if not self._cov_file.exists():
            return []
        data = coverage_mod.CoverageData(basename=str(self._cov_file))
        try:
            data.read()  # coverage >= 5.x
        except Exception:
            pass
        out = set()
        marker = f"/{self.pkg}/"
        for f in data.measured_files():
            if not f.endswith(".py") or self.pkg not in f:
                continue
            # take the repo-relative tail starting at the package dir
            idx = f.rfind(marker)
            if idx < 0:
                continue
            rel = f[idx + 1:]           # "tinydb/table.py"
            out.add(rel)
        return sorted(out)

    