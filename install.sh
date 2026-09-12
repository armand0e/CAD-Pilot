#!/usr/bin/env bash
# Downloaded by the account's connection command, then clones/updates this repository.
set -euo pipefail
if [ "$#" -ne 3 ]; then
  echo 'Open your CADPilot account and generate a connection command in onboarding.' >&2
  exit 2
fi
command -v git >/dev/null || { echo 'Install Git first.' >&2; exit 1; }
command -v docker >/dev/null || { echo 'Install Docker with Compose first.' >&2; exit 1; }
docker compose version >/dev/null
docker info >/dev/null
git_url=https://github.com/armand0e/CAD-Pilot.git
repo_dir="${CADPILOT_INSTALL_DIR:-$HOME/CAD-Pilot}"
if [ -e "$repo_dir" ]; then
  [ "$(git -C "$repo_dir" remote get-url origin)" = "$git_url" ] || {
    echo 'Choose an empty CADPILOT_INSTALL_DIR; this directory belongs to another repository.' >&2; exit 1;
  }
  [ "$(git -C "$repo_dir" branch --show-current)" = main ] && git -C "$repo_dir" diff --quiet && git -C "$repo_dir" diff --cached --quiet || {
    echo 'Save your source changes before updating CADPilot.' >&2; exit 1;
  }
  git -C "$repo_dir" pull --ff-only origin main
else
  git clone --depth 1 --branch main "$git_url" "$repo_dir"
fi
exec bash "$repo_dir/harness/connect.sh" "$@"
