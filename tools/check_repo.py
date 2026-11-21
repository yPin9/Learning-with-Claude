#!/usr/bin/env python3
"""Repo 健檢：README 索引是否與課程資料夾一致、所有 Markdown 內部連結是否有效。

用法：python3 tools/check_repo.py   （在 repo 任何位置執行皆可）
有問題 → 印出清單並 exit 1；全部通過 → exit 0。
只用標準函式庫，不需要安裝任何東西。
"""
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
# 不是課程分類的頂層資料夾
NON_CATEGORY = {"tools"}

FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)  # ``` 程式碼區塊
INLINE_CODE = re.compile(r"`[^`\n]*`")  # `行內程式碼`
LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")  # [text](target "title")


def course_dirs():
    """課程 = 頂層分類資料夾底下的第二層資料夾，例如 systems/bpf。"""
    for cat in sorted(ROOT.iterdir()):
        if not cat.is_dir() or cat.name.startswith(".") or cat.name in NON_CATEGORY:
            continue
        for course in sorted(cat.iterdir()):
            if course.is_dir() and not course.name.startswith("."):
                yield course.relative_to(ROOT).as_posix()


def check_index():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    indexed = set(re.findall(r"\]\(\./([^/)]+/[^/)]+)/README\.md\)", readme))
    actual = set(course_dirs())
    errors = []
    for c in sorted(actual - indexed):
        errors.append(f"README 索引漏列：{c}")
    for c in sorted(indexed - actual):
        errors.append(f"README 索引指向不存在的課程：{c}")
    return errors


def git_ignored(paths):
    """回傳 paths 中被 .gitignore 規則排除的（repo 相對、posix）路徑集合。

    刻意忽略「指向 git-ignored 檔案」的連結：例如 c_sysprog/sources/ 的
    著作權快照，依設計 README 進版控、快照本身不進（見該目錄 .gitignore），
    這種連結在本機有效、在 repo 內「失效」是預期的，不算問題。
    git 不可用時退回空集合（維持原本行為）。
    """
    if not paths:
        return set()
    try:
        # 用 bytes 餵 stdin：text 模式在 Windows 會把 \n 轉成 \r\n，
        # 讓 git 看到結尾帶 \r 的路徑而比對不到 *.md。
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "--stdin"],
            input="\n".join(paths).encode("utf-8"), capture_output=True,
        )
    except (FileNotFoundError, OSError):
        return set()
    out = proc.stdout.decode("utf-8", "replace")
    return {line.strip() for line in out.splitlines() if line.strip()}


def check_links():
    candidates = []  # (rel, target, repo_relative_posix_path)
    for md in sorted(ROOT.rglob("*.md")):
        rel = md.relative_to(ROOT)
        if rel.parts[0].startswith("."):  # .git / .claude / .scratch
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        text = INLINE_CODE.sub("", FENCE.sub("", text))
        for target in LINK.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                continue  # http:, mailto:, 頁內錨點
            path = unquote(target.split("#", 1)[0])
            if not path:
                continue
            resolved = (ROOT / path.lstrip("/")) if path.startswith("/") else (md.parent / path)
            if resolved.exists():
                continue
            try:
                repo_rel = resolved.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                repo_rel = None
            candidates.append((rel, target, repo_rel))

    ignored = git_ignored([c[2] for c in candidates if c[2]])
    errors = []
    for rel, target, repo_rel in candidates:
        if repo_rel and repo_rel in ignored:
            continue  # 連到 git-ignored 的檔案是預期的，略過
        errors.append(f"失效連結：{rel} → {target}")
    return errors


def main():
    errors = check_index() + check_links()
    for e in errors:
        print(e)
    if errors:
        print(f"\n共 {len(errors)} 個問題", file=sys.stderr)
        return 1
    print("check_repo: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
