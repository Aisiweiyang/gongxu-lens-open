"""导出可验证的公开源码包，不更改本地 Git 历史或上传文件。"""

import hashlib
import json
from pathlib import Path
import shutil
import zipfile

BASE = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    ".gitignore", ".gitattributes", "README.md", "README.en.md", "LICENSE",
    "THIRD_PARTY_NOTICES.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md",
    "requirements.txt", "requirements-test.txt", "requirements-dev.txt",
    "run.py", "serve.py", "安装环境.bat", "启动演示网站.bat", "启动试点工作台.bat",
    "打开最新报告.bat",
}
ROOTS = {".github", "src", "pilot", "config", "schemas", "templates", "tests",
         "scripts", "benchmarks", "docs", "site", "tools"}
EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".csv", ".json", ".html", ".css",
              ".js", ".png", ".svg"}
PRIVATE_PREFIXES = (
    "docs/archive/process/", "docs/archive/legacy-scripts/", "pilot/data/",
    "pilot/backups/", "tools/docx/node_modules/",
)
LOCAL_DOCS = {
    "docs/使用手册-所有者版.md", "docs/项目现状与使用调试指南.md",
    "docs/archive/Codex交接-v12.md", "docs/e2e-shots/probe390.py",
    "docs/e2e-shots/probe_confirm.py",
}


def is_public_path(relative):
    path = Path(relative)
    rel = path.as_posix()
    if path.is_absolute() or ".." in path.parts or "__pycache__" in path.parts:
        return False
    if rel in ROOT_FILES:
        return True
    return (bool(path.parts) and path.parts[0] in ROOTS and
            path.suffix.lower() in EXTENSIONS and rel not in LOCAL_DOCS and
            not any(rel.startswith(prefix) for prefix in PRIVATE_PREFIXES))


def public_files(base):
    base = Path(base).resolve()
    files = []
    for path in base.rglob("*"):
        rel = path.relative_to(base).as_posix()
        if not is_public_path(rel) or not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            raise ValueError(f"Refusing symlink or external source: {rel}")
        files.append((rel, path))
    return sorted(files)


def main():
    target = BASE / "dist" / "public-source"
    intended_dist = BASE.resolve() / "dist"
    if target.is_symlink() or not target.resolve().is_relative_to(intended_dist):
        raise ValueError("Public export must stay in this repository's dist directory")
    files = public_files(BASE)
    present = {rel for rel, _ in files}
    missing = ROOT_FILES - present
    if missing:
        raise ValueError(f"Missing public root files: {sorted(missing)}")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    manifest = {}
    for rel, source in files:
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest[rel] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (target / "PUBLIC-MANIFEST.json").write_text(
        json.dumps({"files": manifest, "file_count": len(manifest)},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = BASE / "dist" / "gongxu-lens-public-source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                package.write(path, "gongxu-lens/" + path.relative_to(target).as_posix())
    print(f"Public source: {len(manifest)} files\n{target}\n{archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
