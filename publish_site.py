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

# 4. commit + push to main (source of truth / dist snapshots)
subprocess.run(["git", "commit", "-q", "-m", "Publish site data"], check=True)
subprocess.run(["git", "push", "-q", "origin", "main"], check=True)

# 5. publish site/dist to gh-pages (the branch GitHub Pages serves).
# HTTP pushes of the ~1.9 GB dist tree time out (408) — SSH works.
wt = "/tmp/valveleak-ghpages"
subprocess.run(["rm", "-rf", wt], check=True)
subprocess.run(["git", "worktree", "add", wt, "origin/gh-pages"], check=True)
subprocess.run(["bash", "-c",
                f"cd {wt} && git rm -rq --ignore-unmatch . && "
                f"cp -R {BASE}/site/dist/. . && git add -A && "
                f"git commit -qm 'Publish site' && "
                f"git push git@github.com:femtendo/steam2-catalog.git HEAD:gh-pages"],
               check=True)
subprocess.run(["git", "worktree", "remove", "--force", wt], check=True)
print("published: site data committed and pushed (main + gh-pages)")
