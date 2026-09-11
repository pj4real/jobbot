#!/usr/bin/env bash
# Everything that can be installed without a human. Safe to rerun.
#
#     ./scripts/install.sh
#
# What it cannot do is the Google part: that needs someone signed into a
# browser with their own account. It tells you when it gets there.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
say() { printf "\n\033[1m%s\033[0m\n" "$1"; }
ok()  { printf "  ok   %s\n" "$1"; }
bad() { printf "  XX   %s\n" "$1"; }

say "1. python"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
    maj=${v%%.*}; min=${v##*.}
    if [ "$maj" -eq 3 ] && [ "$min" -ge 10 ]; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  bad "no python 3.10 or newer found"
  echo "       macOS:  brew install python@3.12"
  echo "       ubuntu: sudo apt install python3.12 python3.12-venv"
  exit 1
fi
ok "$PY ($($PY -c 'import sys;print(sys.version.split()[0])'))"

say "2. virtual environment"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  ok ".venv created"
else
  ok ".venv already there"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

say "3. dependencies"
pip install --quiet -r requirements.txt
ok "$(pip list --format=freeze | wc -l | tr -d ' ') packages"

say "4. browser"
BROWSER_OK=1
have_browser() {
  python - <<'PYCHK' 2>/dev/null
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    try:
        sys.exit(0 if Path(p.chromium.executable_path).exists() else 1)
    except Exception:
        sys.exit(1)
PYCHK
}
if have_browser; then
  ok "chromium already installed"
else
  echo "  downloading chromium, about 150 MB"
  if playwright install chromium >/tmp/jobbot-browser.log 2>&1 && have_browser; then
    ok "chromium installed"
  else
    BROWSER_OK=0
    bad "chromium did not download (log: /tmp/jobbot-browser.log)"
    echo "       Everything except the form filler still works."
    echo "       Retry any time with:  playwright install chromium"
  fi
fi

say "5. database and config"
python run.py init

say "6. git hook"
if [ -d .git ]; then
  ./scripts/install-hook.sh >/dev/null && ok "pre-commit leak check installed"
else
  ok "not a git repo, skipping the hook"
fi

say "7. tests"
if python tests/test_all.py >/tmp/jobbot-tests.log 2>&1; then
  ok "$(tail -2 /tmp/jobbot-tests.log | head -1 | xargs)"
else
  bad "tests failed, see /tmp/jobbot-tests.log"
fi

if [ "$BROWSER_OK" -eq 0 ]; then
  say "Installed, except the browser."
  echo "  Finding openings, drafting mail and the dashboard all work."
  echo "  Filling forms needs:  playwright install chromium"
else
  say "Installed. What is left needs you, not a script:"
fi
cat <<'TODO'

  1. a resume PDF
  2. your details and evidence lines
  3. a Google OAuth client, if you want it to read your mail

  Start the dashboard and it walks you through all three:

      source .venv/bin/activate
      python run.py web

  then open http://127.0.0.1:8000 and work down the Setup page.

TODO
