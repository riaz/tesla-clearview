#!/usr/bin/env bash
# Publish the static Pages site (callback page, home.json, your Tesla public key) to your
# <user>.github.io repo: the current branch, plus gh-pages if the repo has one.
#
#   ./scripts/publish_site.sh ~/code/<user>.github.io
#
# home.json holds the fixed home-server address the callback page falls back to. It is
# CLEARVIEW_PUBLIC_URL from .env, or http://localhost:8787 when that's empty.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
dest="$(cd "${1:?usage: publish_site.sh <path to your <user>.github.io clone>}" && pwd)"
pub="$repo_root/config/fleet-public-key.pem"

[[ -d "$dest/.git" ]] || { echo "$dest is not a git clone" >&2; exit 1; }
[[ -f "$pub" ]] || { echo "missing $pub; run ./scripts/gen_keys.sh first" >&2; exit 1; }
grep -q "PRIVATE" "$pub" && { echo "refusing: $pub contains a private key" >&2; exit 1; }

home="$(grep -E '^CLEARVIEW_PUBLIC_URL=' "$repo_root/.env" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//; s/[[:space:]]*$//' || true)"
home="${home:-http://localhost:8787}"

# Copy the site into a working tree and commit it there.
publish_into() {
  local dir="$1"
  mkdir -p "$dir/.well-known/appspecific" "$dir/callback"
  cp "$pub" "$dir/.well-known/appspecific/com.tesla.3p.public-key.pem"
  cp "$repo_root/site/callback/index.html" "$dir/callback/index.html"
  printf '{"home": "%s"}\n' "$home" > "$dir/callback/home.json"
  touch "$dir/.nojekyll"   # without this, Pages won't serve .well-known
  [[ -f "$dir/index.html" ]] || cp "$repo_root/site/index.html" "$dir/index.html"
  git -C "$dir" add .nojekyll .well-known callback index.html
  if git -C "$dir" diff --cached --quiet; then
    echo "$(git -C "$dir" branch --show-current): nothing changed"
  else
    git -C "$dir" commit -q -m "clearview: Tesla public key + login callback"
    git -C "$dir" push -q origin HEAD
    echo "$(git -C "$dir" branch --show-current): pushed"
  fi
}

publish_into "$dest"

current="$(git -C "$dest" branch --show-current)"
if [[ "$current" != "gh-pages" ]] && git -C "$dest" show-ref --quiet refs/remotes/origin/gh-pages; then
  wt="$(mktemp -d)"
  git -C "$dest" fetch -q origin gh-pages
  git -C "$dest" worktree add -q "$wt" -B gh-pages origin/gh-pages
  publish_into "$wt"
  git -C "$dest" worktree remove --force "$wt"
fi

user="$(basename "$dest")"
echo
echo "Home server fallback: $home"
echo "Check in a minute or two:"
echo "  https://$user/.well-known/appspecific/com.tesla.3p.public-key.pem"
echo "  https://$user/callback/"
