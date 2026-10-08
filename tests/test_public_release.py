"""公开源码范围不能带入运行数据、凭据和第三方原件。"""

from pathlib import Path
import tempfile
import unittest

from scripts.prepare_public_release import is_public_path, public_files


class TestPublicRelease(unittest.TestCase):
    def test_required_public_assets_are_kept(self):
        for path in ("LICENSE", "requirements-test.txt", ".github/workflows/ci.yml",
                     "src/material.py", "tests/test_static_server.py",
                     "docs/独立核验脚本-v10.py", "config/industries.yaml"):
            with self.subTest(path=path):
                self.assertTrue(is_public_path(path))

    def test_private_and_generated_assets_are_excluded(self):
        for path in ("清华绿创赛/报名.md", "output/archive/report.html",
                     "pilot/data/customers.csv", "pilot/backups/backup.json",
                     "docs/archive/process/private.md", "dist/old/src/main.py",
                     "config/material_emission_factors.yaml.bak-v10",
                     "benchmarks/影子因子-EcoProfiles/original.pdf", "e2e_out.txt",
                     "docs/manual.docx", "src/.env", "src/credential.pem",
                     "tools/docx/node_modules/pkg/index.js", "../src/main.py"):
            with self.subTest(path=path):
                self.assertFalse(is_public_path(path))

    def test_selection_keeps_public_content_only(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for rel in ("src/main.py", "pilot/data/customer.csv", "README.md"):
                path = base / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture", encoding="utf-8")
            self.assertEqual([rel for rel, _ in public_files(base)],
                             ["README.md", "src/main.py"])

    def test_symlink_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "src").mkdir()
            (base / "private.txt").write_text("PRIVATE_SENTINEL", encoding="utf-8")
            try:
                (base / "src/main.py").symlink_to(base / "private.txt")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with self.assertRaises(ValueError):
                public_files(base)
