#!/usr/bin/env bash
# Refuse to commit secrets: .env files and API-key-shaped strings (OpenAI, Google, private keys).
#   scripts/check_secrets.sh --staged   what is about to be committed (the pre-commit hook)
#   scripts/check_secrets.sh --all      every tracked file (CI)
# Works in any git checkout it is run from (the code repo and the asset library repo).
set -euo pipefail

mode="${1:---staged}"
key_pattern='sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY'
env_pattern='(^|/)\.env(\.[^/]*)?$|(^|/)[^/]+\.env$'

case "$mode" in
  --staged) files=$(git diff --cached --name-only --diff-filter=ACMR); where="--cached" ;;
  --all) files=$(git ls-files); where="" ;;
  *) echo "usage: $0 --staged|--all" >&2; exit 2 ;;
esac

status=0
env_files=$(printf '%s\n' "$files" | grep -E "$env_pattern" | grep -vE '(^|/)\.env\.example$' || true)
if [ -n "$env_files" ]; then
  echo "error: .env files must never be committed (API keys live there):" >&2
  printf '  %s\n' $env_files >&2
  echo "  unstage with: git rm --cached <file>" >&2
  status=1
fi

# --text: also look inside binaries (the library's catalog.sqlite3). Only file names are printed, never the key.
# PCRE reads past NUL bytes; POSIX regexes stop at the first one, so they are only the fallback.
rc=0
hits=$(git grep --text -l -P $where -e "$key_pattern" 2>/dev/null) || rc=$?
if [ "$rc" -gt 1 ]; then
  echo "warning: git has no PCRE support; binary files are only partly scanned" >&2
  hits=$(git grep --text -l -E $where -e "$key_pattern" || true)
fi
if [ -n "$hits" ]; then
  echo "error: these files contain something that looks like an API key or private key:" >&2
  printf '  %s\n' $hits >&2
  status=1
fi

exit $status
