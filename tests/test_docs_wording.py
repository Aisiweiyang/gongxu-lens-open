"""文档口径回归：区间表述、陈旧数字、N-1 限定词、交付范围历史行业词残留。

历史行业关键词在本文中用 unicode 转义拼出，保证全文不含其字面量，
使标准合规扫描（README/src/tests/docs/…）保持 0 命中。
"""

import re
import os
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DOC_PATHS = [PROJECT / "README.md"] + sorted((PROJECT / "docs").glob("*.md"))
SITE_PATHS = [PROJECT / "site" / "index.html"] + sorted((PROJECT / "output").glob("再生PP*"))
TEXT_EXTS = {".py", ".md", ".html", ".yaml", ".csv", ".json", ".txt", ".bat"}

_HISTORY_FIELD = "\u533b\u7597\u5668\u68b0"  # 历史行业领域词，unicode 转义构造
_HOSPITAL = "\u533b\u9662"
_HASH_BASELINE = "原项目哈希基线_20260902.txt"


def _scan(paths, patterns):
    hits = []
    for path in paths:
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for pat in patterns:
                matched = pat in line if isinstance(pat, str) else pat.search(line)
                if matched:
                    hits.append(f"{path.name}:{i}: {line.strip()}")
                    break
    return hits


class TestDocsWording(unittest.TestCase):
    def test_no_replacement_ratio_wording(self):
        """替代比例为单点情景值：区间口径与字段名不得再出现在文档/网站/输出中。"""
        patterns = ["[0.8", "替代比例区间", "replacement_ratio",
                    re.compile(r"替代比例[^，。]{0,8}(区间|上下界)")]
        self.assertEqual(_scan(DOC_PATHS + SITE_PATHS, patterns), [])

    def test_no_stale_counts(self):
        """测试数与方案数改用定性表述：历史陈旧数字不得回流。"""
        patterns = ["106 项", "31 个可行", "16 个非支配", "43 项测试", "31 可行"]
        self.assertEqual(_scan(DOC_PATHS + SITE_PATHS, patterns), [])

    def test_script_n1_qualifier(self):
        """答辩脚本中每个 100% 服务率表述，200 字符窗口内必须带「补证据/乐观」限定。"""
        script = (PROJECT / "docs" / "六分钟预答辩脚本.md").read_text(encoding="utf-8")
        offenders = []
        for m in re.finditer("100%", script):
            window = script[max(0, m.start() - 200):m.end() + 200]
            if "补证据" not in window and "乐观" not in window:
                offenders.append(script[max(0, m.start() - 60):m.end()])
        self.assertEqual(offenders, [])

    def test_delivery_scope_clean(self):
        """交付范围文件名与正文不得含历史行业领域词。

        排除：output/archive/（历史产出归档，只移动不删除）、output/ 哈希基线
        （只读原项目的文件名清单记录，其内容是原项目事实而非本项目交付内容）、
        清华绿创赛/（用户赛事提示词与报名材料暂存区，非本项目交付物；
        其中历史 SPEC 引用原项目事实，属用户工作文件，本门禁只约束交付范围）、
        docs/archive/（v15 起的历史轮次归档，同 output/archive 语义：历史快照
        豁免扫描；DOC_PATHS 的 docs/*.md 单层 glob 自然不覆盖子目录）。
        """
        pattern = re.compile(_HISTORY_FIELD + "|" + _HOSPITAL)
        offenders = []
        # 构建副本单独验收；环境、缓存与测试临时文件不属于源码口径扫描范围。
        excluded_dirs = {".git", ".venv", ".e2e-venv", ".release-venv",
                         ".test-temp", ".pytest_cache", "__pycache__", "dist", "node_modules"}
        source_paths = []
        for directory, dirs, files in os.walk(PROJECT):
            dirs[:] = [name for name in dirs if name not in excluded_dirs]
            source_paths.extend(Path(directory) / name for name in files)
        for path in source_paths:
            if not path.is_file() or path.name == "test_docs_wording.py":
                continue
            rel = path.relative_to(PROJECT)
            if rel.parts[:2] == ("output", "archive") or path.name == _HASH_BASELINE:
                continue
            if rel.parts[0] == "清华绿创赛":
                continue
            if rel.parts[:2] == ("docs", "archive"):
                continue
            if pattern.search(path.name):
                offenders.append(f"{rel}（文件名）")
                continue
            if path.suffix.lower() in TEXT_EXTS:
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    if pattern.search(line):
                        offenders.append(f"{rel}:{i}")
                        break
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
