import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import URLError
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills" / "gpui-liquid-glass"
SCRIPT = ROOT / "scripts" / "install.py"
spec = importlib.util.spec_from_file_location("skill_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="gpui-liquid-glass-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.skills = self.directory / "configuration with spaces" / "skills"

    def cli(self, *arguments, env=None):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, arguments)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def assert_payload(self, destination):
        files = {p.relative_to(destination) for p in destination.rglob("*") if p.is_file()}
        expected = {p.relative_to(SOURCE) for p in SOURCE.rglob("*") if p.is_file()}
        self.assertEqual(files, expected)
        for relative in expected:
            self.assertEqual((destination / relative).read_bytes(), (SOURCE / relative).read_bytes())

    def archive_bytes(self, extra=None):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for path in SOURCE.rglob("*"):
                if path.is_file():
                    archive.write(
                        path,
                        "release/skills/gpui-liquid-glass/" + path.relative_to(SOURCE).as_posix(),
                    )
            archive.writestr("release/README.md", "Repository documentation")
            if extra is not None:
                archive.writestr(*extra)
        return data.getvalue()

    def test_checkout_wrapper_installs_only_the_skill_into_a_path_with_spaces(self):
        result = subprocess.run(
            ["bash", str(ROOT / "install.sh"), "--skills-dir", str(self.skills)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_payload(self.skills / installer.SKILL_NAME)

    def test_explicit_codex_home_is_used_by_the_cli(self):
        environment = dict(os.environ, CODEX_HOME=str(self.directory / "custom codex home"))
        result = self.cli("--source", SOURCE, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_payload(Path(environment["CODEX_HOME"]) / "skills" / installer.SKILL_NAME)

    def test_current_personal_directory_is_used_when_codex_home_is_unset(self):
        environment = {key: value for key, value in os.environ.items() if key != "CODEX_HOME"}
        with mock.patch.dict(os.environ, environment, clear=True):
            with mock.patch.object(installer.Path, "home", return_value=self.directory):
                with contextlib.redirect_stdout(io.StringIO()):
                    status = installer.main(["--source", str(SOURCE)])
        self.assertEqual(status, 0)
        self.assert_payload(self.directory / ".agents" / "skills" / installer.SKILL_NAME)

    def test_existing_installation_is_preserved_until_force_is_requested(self):
        destination, _ = installer.install_skill(SOURCE, self.skills)
        local_file = destination / "local-customization.md"
        local_file.write_text("Keep this edit", encoding="utf-8")
        result = self.cli("--source", SOURCE, "--skills-dir", self.skills)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(local_file.read_text(), "Keep this edit")

        updated, backup = installer.install_skill(SOURCE, self.skills, force=True)
        self.assert_payload(updated)
        self.assertEqual((backup / local_file.name).read_text(), "Keep this edit")
        self.assertTrue(backup.is_relative_to((self.skills.parent / "skill-backups").resolve()))
        self.assertFalse(backup.is_relative_to(self.skills.resolve()))

    def test_claude_personal_installation_ignores_codex_home(self):
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.directory / "codex-home")}):
            with mock.patch.object(installer.Path, "home", return_value=self.directory):
                with contextlib.redirect_stdout(io.StringIO()):
                    status = installer.main(["--agent", "claude", "--source", str(SOURCE)])
        self.assertEqual(status, 0)
        self.assert_payload(self.directory / ".claude" / "skills" / installer.SKILL_NAME)
        self.assertFalse((self.directory / "codex-home").exists())

    def test_project_installation_can_target_both_agents(self):
        project = self.directory / "GPUI project"
        result = self.cli("--source", ROOT, "--agent", "both", "--project", project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_payload(project / ".agents" / "skills" / installer.SKILL_NAME)
        self.assert_payload(project / ".claude" / "skills" / installer.SKILL_NAME)

    def test_project_installation_can_target_claude_only(self):
        project = self.directory / "GPUI project"
        result = self.cli("--source", SOURCE, "--agent", "claude", "--project", project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_payload(project / ".claude" / "skills" / installer.SKILL_NAME)
        self.assertFalse((project / ".agents").exists())

    def test_both_agent_installation_preflights_existing_destinations(self):
        project = self.directory / "GPUI project"
        existing = project / ".claude" / "skills" / installer.SKILL_NAME
        existing.mkdir(parents=True)
        marker = existing / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        result = self.cli("--source", SOURCE, "--agent", "both", "--project", project)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(), "keep")
        self.assertFalse((project / ".agents").exists())

    def test_both_agents_reject_one_ambiguous_custom_directory(self):
        result = self.cli("--agent", "both", "--source", SOURCE, "--skills-dir", self.skills)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.skills.exists())

    def test_stdin_shell_entry_point_forwards_claude_options(self):
        commands = self.directory / "commands"
        commands.mkdir()
        curl = commands / "curl"
        curl.write_text(
            "#!/usr/bin/env python3\nimport shutil, sys\n"
            f"shutil.copyfile({str(SCRIPT)!r}, sys.argv[sys.argv.index('-o') + 1])\n",
            encoding="utf-8",
        )
        curl.chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = str(commands) + os.pathsep + environment.get("PATH", "")
        project = self.directory / "Claude project"
        result = subprocess.run(
            ["bash", "-s", "--", "--source", str(ROOT), "--agent", "claude", "--project", str(project)],
            input=(ROOT / "install.sh").read_text(),
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_payload(project / ".claude" / "skills" / installer.SKILL_NAME)

    def test_force_does_not_follow_a_destination_symlink(self):
        real = self.directory / "existing directory"
        real.mkdir()
        marker = real / "keep.txt"
        marker.write_text("untouched", encoding="utf-8")
        self.skills.mkdir(parents=True)
        destination = self.skills / installer.SKILL_NAME
        destination.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install_skill(SOURCE, self.skills, force=True)
        self.assertTrue(destination.is_symlink())
        self.assertEqual(marker.read_text(), "untouched")

    def test_failed_final_rename_restores_the_previous_installation(self):
        destination, _ = installer.install_skill(SOURCE, self.skills)
        marker = destination / "local-customization.md"
        marker.write_text("previous version", encoding="utf-8")
        original_rename = Path.rename

        def fail_payload_rename(path, target):
            if path.name == "payload":
                raise OSError("Simulated install rename failure")
            return original_rename(path, target)

        with mock.patch.object(Path, "rename", fail_payload_rename):
            with self.assertRaises(OSError):
                installer.install_skill(SOURCE, self.skills, force=True)
        self.assertEqual(marker.read_text(), "previous version")
        self.assertFalse((self.skills / f".{installer.SKILL_NAME}.install-lock").exists())
        self.assertEqual(list(self.skills.glob(f".{installer.SKILL_NAME}-stage-*")), [])

    def test_github_download_extracts_only_the_skill_and_accepts_a_commit_ref(self):
        requests = []

        def archive_response(request, timeout):
            requests.append(request.full_url)
            return io.BytesIO(self.archive_bytes())

        with mock.patch.object(installer, "urlopen", side_effect=archive_response):
            with contextlib.redirect_stdout(io.StringIO()):
                status = installer.main([
                    "--repo", installer.DEFAULT_REPOSITORY,
                    "--ref", "a" * 40,
                    "--skills-dir", str(self.skills),
                ])
        self.assertEqual(status, 0)
        self.assert_payload(self.skills / installer.SKILL_NAME)
        self.assertEqual(requests, [f"https://codeload.github.com/{installer.DEFAULT_REPOSITORY}/zip/" + "a" * 40])

    def test_failed_download_leaves_an_existing_installation_intact(self):
        destination, _ = installer.install_skill(SOURCE, self.skills)
        marker = destination / "local-customization.md"
        marker.write_text("keep", encoding="utf-8")
        with mock.patch.object(installer, "urlopen", side_effect=URLError("Unavailable ref")):
            with contextlib.redirect_stderr(io.StringIO()):
                status = installer.main([
                    "--ref", "missing-ref", "--force", "--skills-dir", str(self.skills),
                ])
        self.assertNotEqual(status, 0)
        self.assertEqual(marker.read_text(), "keep")
        self.assertFalse((self.skills.parent / "skill-backups").exists())

    def test_archive_entries_cannot_escape_the_extraction_directory(self):
        archive = self.directory / "invalid.zip"
        archive.write_bytes(self.archive_bytes((
            "release/skills/gpui-liquid-glass/../outside.txt", "bad",
        )))
        with self.assertRaises(ValueError):
            installer.extract_skill(archive, self.directory / "extracted")
        self.assertFalse((self.directory / "outside.txt").exists())

    def test_archive_symlinks_are_rejected(self):
        link = zipfile.ZipInfo("release/skills/gpui-liquid-glass/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive = self.directory / "symlink.zip"
        archive.write_bytes(self.archive_bytes((link, "../../outside.txt")))
        with self.assertRaises(ValueError):
            installer.extract_skill(archive, self.directory / "extracted")

    def test_invalid_source_does_not_create_an_installation_directory(self):
        result = self.cli("--source", self.directory, "--skills-dir", self.skills)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.skills.exists())

    def test_an_existing_lock_is_not_removed_by_another_installer(self):
        self.skills.mkdir(parents=True)
        lock = self.skills / f".{installer.SKILL_NAME}.install-lock"
        lock.mkdir()
        with self.assertRaises(ValueError):
            installer.install_skill(SOURCE, self.skills)
        self.assertTrue(lock.is_dir())
        self.assertFalse((self.skills / installer.SKILL_NAME).exists())


if __name__ == "__main__":
    unittest.main()
