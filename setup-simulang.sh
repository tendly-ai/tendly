#!/usr/bin/env bash
set -euo pipefail

MIN_NODE_MAJOR=22
MIN_NODE_MINOR=18
NODE_FORMULA="node@22"
SIMULANG_PACKAGE="@simular-ai/simulang"

info() {
    printf "\033[1;34m==>\033[0m %s\n" "$*"
}

warn() {
    printf "\033[1;33mwarning:\033[0m %s\n" "$*" >&2
}

fail() {
    printf "\033[1;31merror:\033[0m %s\n" "$*" >&2
    exit 1
}

version_at_least() {
    local version="${1#v}"
    local major minor
    IFS=. read -r major minor _ <<< "$version"
    [[ "${major:-0}" =~ ^[0-9]+$ ]] || return 1
    [[ "${minor:-0}" =~ ^[0-9]+$ ]] || minor=0

    if (( major < MIN_NODE_MAJOR )); then
        return 1
    fi
    if (( major == MIN_NODE_MAJOR && minor < MIN_NODE_MINOR )); then
        return 1
    fi
    return 0
}

ensure_homebrew() {
  if command -v brew >/dev/null 2>&1; then
    return
 fi

  fail "Homebrew is required. Install it from https://brew.sh, then re-run this script."
}

ensure_node() {
    if command -v node >/dev/null 2>&1 && version_at_least "$(node --version)"; then
info "Node $(node --version) is already compatible."
return
fi

ensure_homebrew
info "Installing/upgrading ${NODE_FORMULA} with Homebrew for Node ${MIN_NODE_MAJOR}.${MIN_NODE_MINOR}+..."
brew install "${NODE_FORMULA}" || brew upgrade "${NODE_FORMULA}"

local brew_prefix
brew_prefix="$(brew --prefix "${NODE_FORMULA}")"
export PATH="${brew_prefix}/bin:$(brew --prefix)/bin:${PATH}"

if ! command -v node >/dev/null 2>&1 || ! version_at_least "$(node --version)"; then
fail "Node is still not ${MIN_NODE_MAJOR}.${MIN_NODE_MINOR}+. Add ${brew_prefix}/bin to PATH and re-run."
fi

info "Using Node $(node --version) from $(command -v node)."
}

ensure_simulang() {
info "Installing/updating ${SIMULANG_PACKAGE} globally..."
npm install -g "${SIMULANG_PACKAGE}"

if ! command -v simulang >/dev/null 2>&1; then
fail "simulang is not on PATH after install. Check npm global bin path with: npm bin -g"
fi

info "Installed $(simulang --version | tr '\n' ' ')"
}

run_setup() {
if [[ "$(uname -s)" != "Darwin" ]]; then
warn "simulang setup is only needed on macOS. Skipping permission prompts."
return
fi

info "Running simulang setup. Approve Screen Recording, Accessibility, and Input Monitoring if macOS prompts."
if ! simulang setup; then
    warn "Some permissions are still pending."
    warn "Open System Settings > Privacy & Security and enable pending entries for your terminal/IDE, then run:"
    warn "  simulang setup"
    exit 2
  fi
}

verify() {
  info "Verifying CLI..."
  node --version
  npm --version
  simulang --version
  simulang --help >/dev/null
  info "Simulang setup is ready."
}

main() {
  cd "$(dirname "$0")"
  ensure_node
  ensure_simulang
  run_setup
  verify
}

main "$@"