#!/usr/bin/env bash
# Boot the machine in a browser, in one command.
#
# The browser tier has more moving parts than the rest of the build -- an SDK
# that must be sourced per shell, a Verilator version the suite is actually
# green on, a payload that has to exist, and a port that has to be free -- and
# every one of them is a way for a reader's first attempt to fail on something
# that has nothing to do with the machine. This resolves all of them, verifies
# the machine headlessly, then serves the page.
#
#   tools/web.sh                                   # defaults
#   tools/web.sh --config configs/sim-bram.json \
#                --payload sw/baremetal/build/hello.hex
#   tools/web.sh --compare                         # machines side by side
#   tools/web.sh --port 9000 --no-check --no-open
#
# Everything it does is also available as ordinary targets; see sim/web/README.md.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

config="configs/sim-role-loopback.json"
payload="sw/kernel/build/axos_boot.hex"
port=""
run_check=1
open_browser=1
# Side-by-side mode: several selections at once rather than one. The set
# deliberately differs in exactly one component, so the spread between the
# machines is attributable to the core rather than merely observed alongside
# it, and the payload is the one program that reports its own cycle counts and
# a checksum that must match across all of them.
compare=0
machines="sim-minimal sim-bram sim-ax2"
compare_payload="sw/baremetal/build/cpu_perf.hex"
page=""

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
say() { printf '\033[36m==>\033[0m %s\n' "$*"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --config)  config=${2:?--config needs a profile}; shift 2;;
    --payload) payload=${2:?--payload needs a .hex}; shift 2;;
    --port)    port=${2:?--port needs a number}; shift 2;;
    --compare) compare=1; shift;;
    --machines) machines=${2:?--machines needs a space-separated list}; shift 2;;
    --no-check) run_check=0; shift;;
    --check-only) run_check=1; open_browser=0; port="none"; shift;;
    --no-open) open_browser=0; shift;;
    -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    *) die "unknown argument: $1 (try --help)";;
  esac
done

# Accept either form: a reader types a repo-relative path, `make` passes an
# absolute one, and the sub-makes need absolute either way.
absolute() { case "$1" in /*) printf '%s\n' "$1";; *) printf '%s\n' "$root/$1";; esac; }
default_payload="$root/sw/kernel/build/axos_boot.hex"
config=$(absolute "$config")
payload=$(absolute "$payload")

[ -f "$config" ] || die "profile not found: $config"

# --- Emscripten -----------------------------------------------------------
# emsdk deliberately does not touch the shell profile, so an ordinary shell
# has no emcc. Sourcing it here is the whole reason a reader should not have
# to know that.
if ! command -v emcc >/dev/null 2>&1; then
  if [ -f "$HOME/emsdk/emsdk_env.sh" ]; then
    say "sourcing ~/emsdk/emsdk_env.sh"
    # shellcheck disable=SC1091
    . "$HOME/emsdk/emsdk_env.sh" >/dev/null 2>&1
  fi
fi
command -v emcc >/dev/null 2>&1 || die \
  "emcc not found. Install Emscripten (docs/toolchain.md) -- this tier is optional;
       nothing else in the build, test, formal, or FPGA flow needs it."

# --- Verilator ------------------------------------------------------------
# The suite is green on 4.038 and Verilator 5 currently fails to elaborate the
# role component, so prefer a 4.x if one is installed rather than whatever
# happens to be first on PATH. An explicit VERILATOR= always wins.
pick_verilator() {
  if [ -n "${VERILATOR:-}" ]; then echo "$VERILATOR"; return; fi
  local candidate major
  for candidate in "$(command -v verilator 2>/dev/null || true)" /usr/bin/verilator; do
    [ -x "$candidate" ] || continue
    major=$("$candidate" --version 2>/dev/null | awk '{print $2}' | cut -d. -f1)
    if [ "$major" = "4" ]; then echo "$candidate"; return; fi
  done
  command -v verilator 2>/dev/null || true
}
verilator=$(pick_verilator)
[ -n "$verilator" ] || die "verilator not found (docs/dependencies.md)"
verilator_version=$("$verilator" --version 2>/dev/null | awk '{print $2}')
case "$verilator_version" in
  4.*) ;;
  *) printf '\033[33mwarning:\033[0m using Verilator %s; the suite is green on 4.x and\n' \
       "$verilator_version"
     printf '         5.x currently fails to elaborate role.loopback. Override with VERILATOR=.\n';;
esac
say "verilator $verilator_version ($verilator)"

# --- Side by side ---------------------------------------------------------
# A different question from "does the machine boot", and the one the component
# system exists to answer: what a selection is worth. One bundle is staged per
# selection and the headless check runs the same binary on every one of them,
# which is also what proves the bundles are not secretly the same machine.
if [ "$compare" = 1 ]; then
  compare_payload=$(absolute "$compare_payload")
  if [ ! -f "$compare_payload" ]; then
    say "building the baremetal images (first run)"
    make -s -C sw/baremetal images
  fi
  [ -f "$compare_payload" ] || die "payload not found: $compare_payload"
  say "machines $machines, payload ${compare_payload#"$root/"}"
  if [ "$run_check" = 1 ]; then
    say "verifying headlessly (one binary on every machine; checksums must match)"
    make -s -C sim/web compare VERILATOR="$verilator" \
      COMPARE_MACHINES="$machines" COMPARE_PAYLOAD="$compare_payload"
  else
    make -s -C sim/web machines VERILATOR="$verilator" \
      COMPARE_MACHINES="$machines" COMPARE_PAYLOAD="$compare_payload"
  fi
  if [ "$port" = "none" ]; then exit 0; fi
  # The two pages link to each other out of one served directory, so the
  # single-machine bundle is built as well rather than leaving a reader one
  # click away from a page that cannot start. Its headless check has already
  # been superseded by the one above.
  run_check=0
  page="compare.html"
fi

# --- Payload --------------------------------------------------------------
# A missing default payload means the kernel simply has not been built yet,
# which is a build step rather than a mistake worth stopping for.
if [ ! -f "$payload" ]; then
  if [ "$payload" = "$default_payload" ]; then
    say "building the aXos payload (first run)"
    make -s -C sw/kernel images VERILATOR="$verilator"
  else
    die "payload not found: $payload"
  fi
fi
say "profile $(basename "$config" .json), payload ${payload#"$root/"}"

web() { make -s -C sim/web "$@" VERILATOR="$verilator" \
          COMPONENT_CONFIG="$config" PAYLOAD="$payload"; }

# --- Verify ---------------------------------------------------------------
# Headless first, always before the page: if this fails the problem is the
# machine, and hunting it in a browser tab costs far more than reading it here.
# boot.mjs's pass condition is specifically "reached the aXos shell prompt, and
# `role` twice reported irq=1 then irq=2". That is the right check for the aXos
# payload and a meaningless one for a bare-metal program that never prints a
# prompt, so skip rather than fail on something the payload was never going to
# do.
if [ "$run_check" = 1 ] && [ "$payload" != "$default_payload" ]; then
  say "skipping the headless check: it asserts an aXos shell prompt, and this"
  printf '    payload is not the aXos kernel. The page still boots it.\n'
  run_check=0
fi
if [ "$run_check" = 1 ]; then
  say "verifying headlessly (boot, then role twice for irq=1, irq=2)"
  web check
fi
[ "$port" = "none" ] && exit 0

# --- Port -----------------------------------------------------------------
# Scan for a free one rather than failing on a busy 8000. A reader who has
# anything else serving locally should not have to diagnose a traceback.
if [ -z "$port" ]; then
  port=$(python3 - <<'PY'
import socket
for candidate in range(8000, 8100):
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", candidate))
        except OSError:
            continue
        print(candidate)
        break
else:
    raise SystemExit("no free port in 8000-8099")
PY
) || die "could not find a free port"
fi

url="http://localhost:$port/$page"
say "serving $url  (Ctrl-C to stop)"
if [ "$compare" = 1 ]; then
  printf '    the single machine is at \033[1m%s\033[0m\n' "http://localhost:$port/"
elif [ "$payload" = "$default_payload" ]; then
  printf '    try: \033[1mhelp\033[0m, then \033[1mrole\033[0m twice -- irq=1 then irq=2 is one continuous machine\n'
fi

# --- Open -----------------------------------------------------------------
# Best effort, and never fatal: a headless or restricted environment simply
# gets the URL printed above, which is all it needed anyway.
if [ "$open_browser" = 1 ]; then
  for opener in wslview xdg-open open; do
    if command -v "$opener" >/dev/null 2>&1; then
      ( sleep 1; "$opener" "$url" >/dev/null 2>&1 || true ) &
      break
    fi
  done
fi

exec make -s -C sim/web serve VERILATOR="$verilator" PORT="$port" ANNOUNCE=0 \
  COMPONENT_CONFIG="$config" PAYLOAD="$payload"
