#!/usr/bin/env bash
# One-shot: create the GitHub repo and push AstraSynth.
# Prereq (once): install GitHub CLI and run `gh auth login`.
# Then, from inside the AstraSynth/ folder:  ./PUSH_TO_GITHUB.sh
set -euo pipefail

REPO_NAME="astrasynth"

# Fail early rather than pushing a broken repo.
echo "Running the backend test suite first..."
(cd backend && python3 -m pytest -q)

git init -b main
git add .
git commit -m "AstraSynth: terrain hazard analysis, energy-aware rover path planning, battery feasibility assessment"

# --public (change to --private to keep it private). Creates the repo under your
# account, adds it as origin, and pushes main.
gh repo create "$REPO_NAME" --public --source=. --remote=origin --push \
  --description "AI-assisted planetary mission intelligence: terrain hazard analysis, energy-aware rover path planning, and battery feasibility assessment (FastAPI, OpenCV, PostgreSQL, React, Docker)"

echo "Done -> https://github.com/$(gh api user -q .login)/$REPO_NAME"

# --- No gh CLI? Create an empty repo named astrasynth on github.com, then:
#   git init -b main && git add . && git commit -m "AstraSynth"
#   git remote add origin https://github.com/<you>/astrasynth.git
#   git push -u origin main
