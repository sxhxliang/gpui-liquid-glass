#!/usr/bin/env bash
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
    printf 'Python 3.9 or newer is required.\n' >&2
    exit 1
fi

skill_script_source="${BASH_SOURCE[0]:-}"
if [[ -n "$skill_script_source" && -f "$skill_script_source" ]]; then
    skill_repository_dir="$(cd "$(dirname "$skill_script_source")" && pwd -P)"
    if [[ -f "$skill_repository_dir/scripts/install.py" ]]; then
        exec python3 "$skill_repository_dir/scripts/install.py" "$@"
    fi
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    printf '%s\n' \
        'Usage: bash install.sh [--agent codex|claude|both] [--project DIR | --skills-dir DIR] [--ref REF] [--force]' \
        'From a checkout, --source PATH installs a local repository or skill directory.' \
        'Codex default: ~/.agents/skills, or $CODEX_HOME/skills when CODEX_HOME is set.' \
        'Claude Code default: ~/.claude/skills. --project uses the selected project directory.' \
        'Use --force to update an existing installation and keep a backup.'
    exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
    printf 'curl is required when running the installer without a checkout.\n' >&2
    exit 1
fi

skill_installer_temp="$(mktemp "${TMPDIR:-/tmp}/gpui-liquid-glass-installer.XXXXXX")"
trap 'rm -f "$skill_installer_temp"' EXIT
curl -fsSL --connect-timeout 10 --max-time 60 \
    'https://raw.githubusercontent.com/sxhxliang/gpui-liquid-glass/main/scripts/install.py' \
    -o "$skill_installer_temp"
python3 "$skill_installer_temp" "$@"
