#!/usr/bin/env bash
# Report the local toolchain state. Changes nothing.
#
# Exists mainly because the macOS SUMO installer creates two traps: it does not
# export SUMO_HOME, and the `sumo` it puts on PATH launches the GUI. This script
# resolves the real installation and prints the exports for your shell.

set -uo pipefail

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
RED=$'\033[31m'; RESET=$'\033[0m'

ok()   { printf "  ${GREEN}✓${RESET} %s\n" "$1"; }
warn() { printf "  ${YELLOW}!${RESET} %s\n" "$1"; }
bad()  { printf "  ${RED}✗${RESET} %s\n" "$1"; }
note() { printf "    ${DIM}%s${RESET}\n" "$1"; }

printf "\n${BOLD}EMS Black Box - environment check${RESET}\n\n"

# --- Node -----------------------------------------------------------------
printf "${BOLD}Node${RESET}\n"
if command -v node >/dev/null 2>&1; then
  ok "node $(node --version)"
  command -v npm >/dev/null 2>&1 && ok "npm $(npm --version)" || bad "npm not found"
else
  bad "node not found - required for the frontend"
fi
echo

# --- Python ---------------------------------------------------------------
printf "${BOLD}Python${RESET}\n"
if command -v python3.11 >/dev/null 2>&1; then
  ok "python3.11 $(python3.11 --version 2>&1 | awk '{print $2}')  (target interpreter)"
else
  warn "python3.11 not found"
  note "The geospatial stack (GeoPandas/OSMnx/pyproj) has patchy wheel coverage"
  note "on newer interpreters. Install 3.11: brew install python@3.11"
fi
command -v python3 >/dev/null 2>&1 && \
  note "default python3 is $(python3 --version 2>&1 | awk '{print $2}')"
echo

# --- SUMO -----------------------------------------------------------------
printf "${BOLD}SUMO${RESET}\n"
SUMO_HOME_RESOLVED=""
for candidate in \
  "${SUMO_HOME:-}" \
  "/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/share/sumo" \
  "/opt/homebrew/share/sumo" \
  "/usr/local/share/sumo" \
  "/usr/share/sumo"
do
  [ -n "$candidate" ] && [ -x "$candidate/bin/sumo" ] && { SUMO_HOME_RESOLVED="$candidate"; break; }
done

if [ -n "$SUMO_HOME_RESOLVED" ]; then
  ok "SUMO $("$SUMO_HOME_RESOLVED/bin/sumo" --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
  note "SUMO_HOME: $SUMO_HOME_RESOLVED"

  missing=""
  for bin in sumo netconvert duarouter polyconvert; do
    [ -x "$SUMO_HOME_RESOLVED/bin/$bin" ] || missing="$missing $bin"
  done
  [ -z "$missing" ] && ok "required binaries present (sumo, netconvert, duarouter, polyconvert)" \
                    || bad "missing binaries:$missing"

  if PYTHONPATH="$SUMO_HOME_RESOLVED/tools" python3 -c "import traci, sumolib" >/dev/null 2>&1; then
    ok "traci + sumolib importable from SUMO_HOME/tools"
  else
    warn "traci/sumolib not importable from $SUMO_HOME_RESOLVED/tools"
  fi

  if [ -z "${SUMO_HOME:-}" ]; then
    warn "SUMO_HOME is not exported in this shell"
    note "The macOS installer does not set it, and the 'sumo' on PATH is the GUI"
    note "launcher - a headless run through it simulates nothing. Add to your shell:"
    echo
    printf "      export SUMO_HOME=\"%s\"\n" "$SUMO_HOME_RESOLVED"
    printf "      export PATH=\"\$SUMO_HOME/bin:\$PATH\"\n"
    printf "      export PYTHONPATH=\"\$SUMO_HOME/tools:\$PYTHONPATH\"\n"
  fi
else
  bad "no SUMO installation found - see docs/ENVIRONMENT.md"
fi
echo

# --- Docker ---------------------------------------------------------------
printf "${BOLD}Docker${RESET} ${DIM}(optional - nothing depends on it yet)${RESET}\n"
if command -v docker >/dev/null 2>&1; then
  ok "docker $(docker --version | awk '{print $3}' | tr -d ',')"
  if docker info >/dev/null 2>&1; then
    ok "daemon running"
  else
    warn "daemon not running"
  fi
  if docker compose version >/dev/null 2>&1; then
    ok "compose plugin present"
  else
    warn "compose plugin not installed - 'docker compose' will not work"
    note "Not needed yet. See docs/ENVIRONMENT.md for the workaround."
  fi
else
  warn "docker not found - not required at this stage"
fi
echo

# --- Project --------------------------------------------------------------
printf "${BOLD}Project${RESET}\n"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -d "$REPO_ROOT/.venv" ] && ok "python venv present (.venv)" \
                          || warn "no .venv - run scripts/bootstrap.sh"
[ -d "$REPO_ROOT/frontend/node_modules" ] && ok "frontend dependencies installed" \
                                          || warn "frontend/node_modules missing - run: cd frontend && npm install"

scenarios=$(find "$REPO_ROOT/simulation/sumo" -name '*.sumocfg' 2>/dev/null | wc -l | tr -d ' ')
if [ "$scenarios" = "0" ]; then
  warn "no SUMO scenario built yet - expected at this stage (see docs/ROADMAP.md)"
else
  ok "$scenarios SUMO scenario config(s) found"
fi
echo
