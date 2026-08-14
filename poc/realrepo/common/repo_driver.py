"""Repo driver + apply a real repo change to a copy, run pytest as a real
subprocess, restore the pristine tree after. All agent commands run here.

Paths: common/repo_driver.py -> parents[1] = realrepo/, repos live beside it.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1] / "repos"


class RepoDriver:
    def __init__(self, repo_name: str, workdir: Path | None = None, keep: bool = False):
        src = REPO_DIR / repo_name
        assert src.is_dir(), f"repo not found: {src}"
        self.repo_name = repo_name
        self.src = src
        if workdir is None:
            workdir = Path(tempfile.mkdtemp(prefix=f"rr-{repo_name}-"))
        workdir.mkdir(parents=True, exist_ok=True)
        self.workdir = workdir
        self._keep = keep
        self._copy(src, workdir / repo_name)
        self.root = workdir / repo_name

    @staticmethod
    def _copy(src: Path, dst: Path):
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name in (".git", "__pycache__", ".pytest_cache",
                             ".mypy_cache", ".hypothesis"):
                continue
            d = dst / item.name
            if item.is_dir():
                shutil.copytree(item, d)
            else:
                shutil.copy2(item, d)

    # ---- file access -------------------------------------------------------
    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def write(self, rel: str, text: str):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def rm(self, rel: str):
        (self.root / rel).unlink(missing_ok=True)

    def diff(self, rel: str, n_ctx: int = 8) -> str:
        """Small line-diff of `rel` vs pristine source (deterministic)."""
        before = (self.src / rel).read_text(encoding="utf-8").splitlines()
        after = self.read(rel).splitlines()
        out_b, out_a = [], []
        for i, j in zip(before, after):
            if i != j:
                out_b.append(i)
                out_a.append(j)
        n = min(len(out_b), n_ctx)
        lines = [f"--- {rel} (before)", f"+++ {rel} (after)"]
        lines += [f"- {l}" for l in out_b[:n]]
        lines += [f"+ {l}" for l in out_a[:n]]
        return "\n".join(lines)

    def artifact_hash(self, rel: str) -> str:
        import hashlib
        return hashlib.sha256(self.read(rel).encode()).hexdigest()[:16]

    # ---- exec --------------------------------------------------------------
    def run_pytest(self, targets=(), extra=(), cwd=None, timeout=180) -> dict:
        cwd = cwd or self.root
        cmd = ["python3", "-m", "pytest", "-q", "--no-header",
               "-p", "no:cacheprovider", "--tb=short"]
        cmd += [str(t) for t in targets]
        cmd += list(extra)
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        t0 = time.time()
        try:
            cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True,
                                text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return {"result": "FAIL", "returncode": -1, "command": " ".join(cmd),
                    "duration_s": float(timeout), "tests_failed": 999,
                    "log_tail": "TIMEOUT"}
        out = cp.stdout
        m = re.search(r"(\d+) failed", out)
        n_failed = int(m.group(1)) if m else (0 if "passed" in out else -1)
        return {"result": "PASS" if cp.returncode == 0 else "FAIL",
                "returncode": cp.returncode,
                "command": " ".join(cmd),
                "duration_s": round(time.time() - t0, 3),
                "tests_failed": n_failed,
                "log_tail": (out + cp.stderr)[-500:]}

    def run_python(self, code: str, cwd=None, timeout=60) -> dict:
        cwd = cwd or self.root
        t0 = time.time()
        cp = subprocess.run(["python3", "-c", code], cwd=str(cwd),
                            capture_output=True, text=True, timeout=timeout)
        return {"result": "PASS" if cp.returncode == 0 else "FAIL",
                "returncode": cp.returncode, "command": code[:120],
                "duration_s": round(time.time() - t0, 3),
                "log_tail": (cp.stdout + cp.stderr)[-400:]}

    def cleanup(self):
        if self._keep is False and self.workdir.exists():
            shutil.rmtree(self.workdir, ignore_errors=True)