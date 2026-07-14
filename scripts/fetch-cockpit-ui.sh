#!/usr/bin/env bash
#
# Fetch the prebuilt cockpit admin UI bundle and unpack it into the package so
# it ships inside the wheel (served at /admin). The bundle is a GitHub Release
# asset published by refindery-cockpit's release-dist workflow; the version is
# pinned in COCKPIT_UI_VERSION. Requires the `gh` CLI (authenticated) and tar.
#
# Usage:
#   scripts/fetch-cockpit-ui.sh [version]
#
# The optional positional argument overrides COCKPIT_UI_VERSION (e.g. to test an
# unreleased tag). COCKPIT_UI_REPO overrides the source repository.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly PROJECT_ROOT
readonly VERSION_FILE="${PROJECT_ROOT}/COCKPIT_UI_VERSION"
readonly TARGET_DIR="${PROJECT_ROOT}/src/refindery/api/static/admin"
readonly REPO="${COCKPIT_UI_REPO:-hbmartin/refindery-cockpit}"

main() {
    local version
    if [[ $# -ge 1 ]]; then
        version="$1"
    elif [[ -f "${VERSION_FILE}" ]]; then
        version="$(tr -d '[:space:]' <"${VERSION_FILE}")"
    else
        echo "error: no version given and ${VERSION_FILE} is missing" >&2
        exit 1
    fi

    if ! command -v gh >/dev/null 2>&1; then
        echo "error: the 'gh' CLI is required to download the cockpit bundle" >&2
        exit 1
    fi

    echo "Fetching cockpit UI ${version} from ${REPO}..."
    local tmp
    tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp}"' EXIT

    gh release download "${version}" \
        --repo "${REPO}" \
        --pattern 'cockpit-dist-*.tgz' \
        --dir "${tmp}"

    rm -rf "${TARGET_DIR}"
    mkdir -p "${TARGET_DIR}"
    tar -xzf "${tmp}"/cockpit-dist-*.tgz -C "${TARGET_DIR}"

    if [[ ! -f "${TARGET_DIR}/index.html" ]]; then
        echo "error: unpacked bundle has no index.html at ${TARGET_DIR}" >&2
        exit 1
    fi
    echo "Cockpit UI ${version} unpacked into ${TARGET_DIR}"
}

main "$@"
