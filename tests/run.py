#!/usr/bin/env python3
"""Run every test script in this folder, each in its own process.

    python tests/run.py          # all of them
    python tests/run.py exams    # only the ones whose name matches

Each script boots the app against a throw-away SQLite file, so nothing here
touches a real database.
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    files = [f for f in files if pattern in os.path.basename(f)]
    if not files:
        print(f"no test matches {pattern!r}")
        return 1

    failed = []
    t0 = time.time()
    for f in files:
        name = os.path.basename(f)
        print(f"\n{'=' * 62}\n  {name}\n{'=' * 62}")
        r = subprocess.run([sys.executable, f], cwd=os.path.dirname(HERE))
        if r.returncode != 0:
            failed.append(name)

    print(f"\n{'=' * 62}")
    print(f"  {len(files) - len(failed)}/{len(files)} passed in {time.time() - t0:.1f}s")
    if failed:
        print("  FAILED: " + ", ".join(failed))
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
