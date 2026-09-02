#!/usr/bin/env bash
# Build a release wheel: the frontend, bundled into the package, then the wheel.
#
# `python -m build` on its own produces a wheel that works but serves no UI,
# because pip cannot run npm -- the bundle only exists if something puts it
# there first. That something is this script, and the release workflow.
#
#   ./packaging/build-wheel.sh          # build and verify
#
# The result lands in dist/ and is what you copy to a server.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)
BUNDLE=backend/infinimap/api/web

echo "==> frontend"
cd frontend
npm ci --no-audit --no-fund
npm run build
cd "$ROOT"

echo "==> bundling the UI into the package"
rm -rf "$BUNDLE"
cp -r frontend/dist "$BUNDLE"

echo "==> wheel"
rm -rf build backend/infinimap.egg-info
mkdir -p dist && rm -f dist/*.whl
python -m pip install --quiet --upgrade build
python -m build --wheel

echo "==> verifying the artefact"
python - <<'PY'
import glob, sys, zipfile

wheel = sorted(glob.glob("dist/*.whl"))[-1]
names = zipfile.ZipFile(wheel).namelist()

checks = [
    ("web UI",            any(n.endswith("api/web/index.html") for n in names)),
    ("5 migrations",      sum(1 for n in names if n.endswith(".sql")) == 5),
    ("no stray top-level", not any(n.startswith(("api/", "collector/")) for n in names)),
]
for label, ok in checks:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
print(f"\n{wheel}")
sys.exit(0 if all(ok for _, ok in checks) else 1)
PY

# Leave the tree clean.
rm -rf "$BUNDLE" build backend/infinimap.egg-info
