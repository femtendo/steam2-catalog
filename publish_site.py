"""Push the generated site (including data JSONs) to the public repo.

The data files under site/dist/data/ are build artifacts (gitignored locally)
but must ship for the static site to work. This commits ONLY site/dist/ to the
repo on the current branch — after the leak check passes.

Run after build_site.py + build_games.py + build_tf2hub.py + build_verify.py.
"""
from __future__ import annotations

import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# 1. leak check
r = subprocess.run([sys.executable, "leak_check.py"])
if r.returncode != 0:
    sys.exit("leak check failed — not publishing")

# 2. temporarily add site/dist to the index (force-add past gitignore)
dist = "site/dist"
if not os.path.isdir(dist):
    sys.exit("site/dist missing — run the build scripts first")

subprocess.run(["git", "add", "-f", dist], check=True)

# 3. nothing to commit?
status = subprocess.run(["git", "status", "--porcelain", dist],
                        capture_output=True, text=True)
if not status.stdout.strip():
    print("site/dist unchanged — nothing to publish")
    sys.exit(0)

# 4. commit + push
subprocess.run(["git", "commit", "-q", "-m", "Publish site data"], check=True)
subprocess.run(["git", "push", "-q", "origin", "main"], check=True)
print("published: site data committed and pushed")
