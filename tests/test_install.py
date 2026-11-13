import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_CURL = '#!/bin/sh\necho "$@" >> "$CURL_LOG"\nexit 22\n'  # records the call, then fails like a dead network
FAKE_PYTHON = "#!/bin/sh\nexit 0\n"  # every import/check passes


def executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.dest = self.home / ".local/share/plane-mcp"
        self.curl_log = self.home / "curl.log"
        executable(self.home / "fakebin/curl", FAKE_CURL)

    def tearDown(self):
        self.temp.cleanup()

    def install(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", str(ROOT / "install.sh")], capture_output=True, text=True, stdin=subprocess.DEVNULL,
            env={"HOME": str(self.home), "PATH": f"{self.home / 'fakebin'}:/usr/bin:/bin", "CURL_LOG": str(self.curl_log)},
        )

    def curl_calls(self) -> str:
        return self.curl_log.read_text() if self.curl_log.exists() else ""

    def make_complete_install(self) -> None:
        self.dest.mkdir(parents=True)
        (self.dest / "manage.py").write_text("")
        (self.dest / ".plane-mcp-managed").write_text("")
        executable(self.dest / ".venv/bin/python", FAKE_PYTHON)
        executable(self.home / ".local/bin/plane-mcp", "#!/bin/sh\nexit 0\n")

    def test_failed_install_puts_the_unmanaged_copy_back(self):
        self.dest.mkdir(parents=True)
        (self.dest / "plane_mcp.py").write_text("old install")

        result = self.install()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing is deleted", result.stderr)
        self.assertEqual((self.dest / "plane_mcp.py").read_text(), "old install")
        self.assertEqual([p.name for p in self.dest.parent.iterdir()], ["plane-mcp"])  # no stray backup left

    def test_failed_first_install_keeps_a_marked_folder_so_the_next_run_resumes(self):
        result = self.install()

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.dest / ".plane-mcp-managed").exists())
        self.assertEqual([p.name for p in self.dest.parent.iterdir()], ["plane-mcp"])  # not mistaken for an old install later

    def test_resume_downloads_only_what_is_missing(self):
        self.dest.mkdir(parents=True)
        (self.dest / "manage.py").write_text("")  # source is already there, uv and the venv are not
        (self.dest / ".plane-mcp-managed").write_text("")

        result = self.install()

        self.assertNotEqual(result.returncode, 0)  # the fake network is down
        self.assertIn("astral.sh", self.curl_calls())  # it went on to install uv
        self.assertNotIn("archive/refs/tags", self.curl_calls())  # and did not download the source again
        self.assertTrue((self.dest / "manage.py").exists())

    def test_complete_install_downloads_nothing(self):
        self.make_complete_install()

        result = self.install()

        self.assertIn("already installed", result.stdout)
        self.assertEqual(self.curl_calls(), "")


if __name__ == "__main__":
    unittest.main()
