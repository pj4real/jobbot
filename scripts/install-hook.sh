#!/usr/bin/env bash
# Run the leak check before every commit. One line, and you stop having to
# remember it.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-commit"
[ -d "$ROOT/.git" ] || { echo "not a git repo yet: run git init first"; exit 1; }
cat > "$HOOK" <<'HOOK'
#!/usr/bin/env bash
exec python3 "$(git rev-parse --show-toplevel)/scripts/preflight.py" --staged
HOOK
chmod +x "$HOOK"
echo "installed $HOOK"
echo "it runs on every commit. --no-verify skips it, but do not."
