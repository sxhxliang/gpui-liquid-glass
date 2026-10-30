#!/usr/bin/env python3
"""Install the GPUI Liquid Glass skill from a checkout or a public GitHub archive."""

import argparse
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

SKILL_NAME = "gpui-liquid-glass"
DEFAULT_REPOSITORY = "sxhxliang/gpui-liquid-glass"
REQUIRED_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/macos-bridge.md",
    "references/verification.md",
)


def default_skills_directory(agent="codex"):
    if agent == "claude":
        return Path.home() / ".claude" / "skills"
    codex_root = os.environ.get("CODEX_HOME")
    if codex_root:
        return Path(codex_root).expanduser() / "skills"
    return Path.home() / ".agents" / "skills"


def resolve_source(path):
    path = Path(path).expanduser().resolve()
    if (path / "SKILL.md").is_file():
        source = path
    else:
        source = path / "skills" / SKILL_NAME
    for relative in REQUIRED_FILES:
        if not (source / relative).is_file():
            raise ValueError(f"Skill source is missing {relative}: {source}")
    if any(entry.is_symlink() for entry in source.rglob("*")):
        raise ValueError("Skill source contains symbolic links; use a regular skill directory.")
    manifest = (source / "SKILL.md").read_text(encoding="utf-8")
    if not re.search(rf"^name: {re.escape(SKILL_NAME)}\s*$", manifest, re.MULTILINE):
        raise ValueError(f"SKILL.md must declare name: {SKILL_NAME}")
    return source


def extract_skill(archive_path, directory):
    directory = Path(directory).resolve()
    with zipfile.ZipFile(archive_path) as archive:
        entries = []
        roots = set()
        for info in archive.infolist():
            parts = PurePosixPath(info.filename).parts
            if len(parts) < 4 or parts[1:3] != ("skills", SKILL_NAME):
                continue
            relative = PurePosixPath(*parts[3:])
            target = (directory / relative).resolve()
            if (
                "\\" in info.filename
                or ".." in relative.parts
                or not target.is_relative_to(directory)
                or stat.S_ISLNK(info.external_attr >> 16)
            ):
                raise ValueError(f"Unsupported archive entry: {info.filename}")
            roots.add(parts[0])
            entries.append((info, target))
        if len(roots) != 1:
            raise ValueError("Archive must contain one skills/gpui-liquid-glass directory.")
        for info, target in entries:
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as reader, target.open("wb") as writer:
                    shutil.copyfileobj(reader, writer)
    return resolve_source(directory)


def download_skill(repository, ref, directory):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("--repo must use OWNER/REPOSITORY format.")
    if any(part in (".", "..") for part in repository.split("/")):
        raise ValueError("Invalid GitHub repository name.")
    archive_path = Path(directory) / "source.zip"
    url = f"https://codeload.github.com/{repository}/zip/{quote(ref, safe='')}"
    request = Request(url, headers={"User-Agent": "gpui-liquid-glass-installer"})
    with urlopen(request, timeout=30) as response, archive_path.open("wb") as writer:
        shutil.copyfileobj(response, writer)
    return extract_skill(archive_path, Path(directory) / "skill")


@contextmanager
def installation_lock(skills_directory):
    lock = skills_directory / f".{SKILL_NAME}.install-lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise ValueError(f"Another installation may be running; lock exists: {lock}") from error
    try:
        yield
    finally:
        lock.rmdir()


def validate_destination(source, skills_directory, force=False):
    skills_directory = Path(skills_directory).expanduser().resolve()
    destination = skills_directory / SKILL_NAME
    if source == destination or destination.is_relative_to(source):
        raise ValueError("Installation destination must be outside the source skill directory.")
    if destination.is_symlink():
        raise ValueError(f"Destination is a symbolic link: {destination}")
    if destination.exists() and (not force or not destination.is_dir()):
        raise ValueError(f"Destination already exists: {destination}; use --force to update a directory.")
    return skills_directory, destination


def install_skill(source, skills_directory, force=False):
    source = resolve_source(source)
    skills_directory, destination = validate_destination(source, skills_directory, force)
    skills_directory.mkdir(parents=True, exist_ok=True)
    with installation_lock(skills_directory):
        validate_destination(source, skills_directory, force)
        backup = None
        with tempfile.TemporaryDirectory(prefix=f".{SKILL_NAME}-stage-", dir=skills_directory) as staging:
            payload = Path(staging) / "payload"
            shutil.copytree(source, payload)
            if destination.exists():
                backup_root = skills_directory.parent / "skill-backups"
                backup_root.mkdir(parents=True, exist_ok=True)
                backup_parent = Path(tempfile.mkdtemp(prefix=f"{SKILL_NAME}-", dir=backup_root))
                backup = backup_parent / SKILL_NAME
                try:
                    destination.rename(backup)
                except OSError:
                    backup_parent.rmdir()
                    raise
            try:
                payload.rename(destination)
            except OSError:
                if backup is not None:
                    backup.rename(destination)
                raise
    return destination, backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="local repository or skill directory")
    parser.add_argument("--repo", help=f"public GitHub OWNER/REPOSITORY (default: {DEFAULT_REPOSITORY})")
    parser.add_argument("--ref", help="GitHub branch, tag, or commit (default: main)")
    parser.add_argument("--agent", choices=("codex", "claude", "both"), default="codex", help="installation target (default: codex)")
    destination_group = parser.add_mutually_exclusive_group()
    destination_group.add_argument("--skills-dir", type=Path, help="custom skills directory for one agent")
    destination_group.add_argument("--project", type=Path, help="install in a project's .agents/skills or .claude/skills")
    parser.add_argument("--force", action="store_true", help="update an existing installation and keep a backup")
    args = parser.parse_args(argv)
    if args.source is not None and (args.repo is not None or args.ref is not None):
        parser.error("--source cannot be combined with --repo or --ref")
    if args.agent == "both" and args.skills_dir is not None:
        parser.error("--skills-dir selects one directory; use separate invocations or --project for both agents")

    source = args.source
    if source is None and args.repo is None and args.ref is None:
        script_path = Path(__file__)
        if script_path.is_file():
            candidate = script_path.resolve().parent.parent / "skills" / SKILL_NAME
            if candidate.is_dir():
                source = candidate
    agents = ("codex", "claude") if args.agent == "both" else (args.agent,)
    targets = []
    for agent in agents:
        if args.skills_dir is not None:
            skills_directory = args.skills_dir
        elif args.project is not None:
            folder = ".agents" if agent == "codex" else ".claude"
            skills_directory = args.project.expanduser().resolve() / folder / "skills"
        else:
            skills_directory = default_skills_directory(agent)
        targets.append((agent, skills_directory))
    try:
        with tempfile.TemporaryDirectory(prefix=f"{SKILL_NAME}-download-") as scratch:
            if source is None:
                source = download_skill(args.repo or DEFAULT_REPOSITORY, args.ref or "main", scratch)
            source = resolve_source(source)
            for _, skills_directory in targets:
                validate_destination(source, skills_directory, args.force)
            for agent, skills_directory in targets:
                destination, backup = install_skill(source, skills_directory, args.force)
                print(f"Installed ({agent}): {destination}")
                if backup is not None:
                    print(f"Previous version saved: {backup}")
                if agent == "claude":
                    print("Invoke /gpui-liquid-glass in Claude Code. If it is missing, start a new session.")
                else:
                    print("Invoke $gpui-liquid-glass in your next Codex prompt. If it is missing, restart Codex.")
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"Install failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        raise SystemExit("Python 3.9 or newer is required.")
    raise SystemExit(main())
