#!/usr/bin/env python3
"""Generate an honest, repeatable source/documentation footprint for public owned repos."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

OWNER = os.environ.get("GITHUB_OWNER", "VireonNoctis")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
README_PATH = Path(os.environ.get("README_PATH", "README.md"))
STATS_JSON_PATH = Path(os.environ.get("STATS_JSON_PATH", "code-stats.json"))
START = "<!-- AUTO-CODE-STATS:START -->"
END = "<!-- AUTO-CODE-STATS:END -->"
API = "https://api.github.com"
MAX_FILE_BYTES = 1_000_000
MAX_REPOSITORIES = 100
EXCLUDED_DIRS = {
    ".git", ".next", ".nuxt", ".turbo", ".cache", ".venv", "venv",
    "node_modules", "vendor", "dist", "build", "out", "target", "coverage",
    "__pycache__", "Pods", "DerivedData", "storybook-static", "playwright-report",
    "test-results", "generated", "third_party", "third-party", "site-packages",
}
EXCLUDED_NAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb",
    "cargo.lock", "gemfile.lock", "pipfile.lock", "poetry.lock", "uv.lock",
    "composer.lock", "go.sum",
}
TEXT_EXTENSIONS = {
    ".py", ".pyi", ".rs", ".go", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".swift", ".rb", ".php", ".cs", ".cpp", ".cc",
    ".cxx", ".c", ".h", ".hpp", ".hh", ".sh", ".bash", ".zsh", ".ps1", ".bat",
    ".cmd", ".html", ".htm", ".css", ".scss", ".sass", ".less", ".svelte",
    ".vue", ".astro", ".md", ".mdx", ".rst", ".txt", ".json", ".jsonc",
    ".json5", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".sql",
    ".graphql", ".gql", ".proto", ".xml", ".svg", ".properties", ".gradle",
    ".r", ".lua", ".pl", ".ex", ".exs", ".erl", ".hrl", ".clj", ".cljs",
    ".cljc", ".scala", ".sc", ".dart", ".m", ".mm", ".vb", ".fs", ".fsx",
    ".xaml", ".tf", ".hcl", ".nix", ".ipynb", ".mod", ".make",
}
TEXT_FILENAMES = {
    "readme", "license", "copying", "dockerfile", "makefile", "justfile",
    "procfile", ".gitignore", ".gitattributes", ".dockerignore", ".editorconfig",
    ".env.example", ".npmrc", ".gitmodules", ".prettierrc", ".eslintrc",
}
WORD_PATTERN = re.compile(r"\S+")


def request_json(url: str) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vireon-code-footprint",
    }
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def owned_public_repositories() -> list[dict]:
    items: list[dict] = []
    for page in range(1, 11):
        query = urllib.parse.urlencode({"per_page": 100, "page": page, "type": "owner", "sort": "updated"})
        batch = request_json(f"{API}/users/{urllib.parse.quote(OWNER)}/repos?{query}")
        if not isinstance(batch, list) or not batch:
            break
        for repo in batch:
            owner = (repo.get("owner") or {}).get("login", "")
            if owner.lower() != OWNER.lower():
                continue
            if repo.get("private") or repo.get("fork") or repo.get("archived") or repo.get("disabled"):
                continue
            if not repo.get("default_branch") or not repo.get("clone_url"):
                continue
            items.append(repo)
        if len(batch) < 100:
            break
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return items[:MAX_REPOSITORIES]


def include_path(raw_path: str) -> bool:
    path = PurePosixPath(raw_path)
    if any(part in EXCLUDED_DIRS for part in path.parts[:-1]):
        return False
    name = path.name.lower()
    if name in EXCLUDED_NAMES or name.endswith((".min.js", ".min.css", ".map", ".snap", ".lock", ".sum")):
        return False
    if name in TEXT_FILENAMES:
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS


def count_worktree(repo_dir: Path) -> dict[str, int]:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-files", "-z"],
        check=True, capture_output=True, timeout=30,
    )
    counts = {"files": 0, "lines": 0, "nonblankLines": 0, "characters": 0, "words": 0}
    for encoded_path in result.stdout.split(b"\0"):
        if not encoded_path:
            continue
        relative = os.fsdecode(encoded_path)
        if not include_path(relative):
            continue
        file_path = repo_dir.joinpath(*PurePosixPath(relative).parts)
        try:
            if file_path.is_symlink() or not file_path.is_file():
                continue
            if file_path.stat().st_size > MAX_FILE_BYTES:
                continue
            raw = file_path.read_bytes()
            if b"\0" in raw:
                continue
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeError):
            continue
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines()
        counts["files"] += 1
        counts["lines"] += len(lines)
        counts["nonblankLines"] += sum(1 for line in lines if line.strip())
        # Character totals include spaces and punctuation but exclude line-break characters.
        counts["characters"] += sum(len(line) for line in lines)
        counts["words"] += len(WORD_PATTERN.findall(normalized))
    return counts


def scan_repository(repo: dict, root: Path) -> tuple[dict | None, str | None]:
    name = str(repo.get("name", "repository"))
    branch = str(repo.get("default_branch", ""))
    destination = root / re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    clone_url = str(repo.get("clone_url", ""))
    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--depth=1", "--single-branch", "--branch", branch, clone_url, str(destination)],
            check=True, capture_output=True, timeout=120,
        )
        counts = count_worktree(destination)
        return ({
            "name": name,
            "url": str(repo.get("html_url", "")),
            "branch": branch,
            **counts,
            "stars": int(repo.get("stargazers_count") or 0),
            "forks": int(repo.get("forks_count") or 0),
        }, None)
    except (OSError, subprocess.SubprocessError) as error:
        return (None, f"{name}: {type(error).__name__}")


def number(value: int) -> str:
    return f"{value:,}"


def markdown_cell(value: object) -> str:
    return " ".join(str(value or "").replace("|", r"\|").split())


def build_readme_block(stats: dict) -> str:
    totals = stats["totals"]
    generated_date = (stats.get("generatedAt") or "")[:10] or "date unavailable"
    rows = [
        f"**Last scan:** {generated_date} UTC · {number(totals['repositories'])} public repositories scanned.",
        "",
        "| Measure | Current total |",
        "| --- | ---: |",
        f"| Public repositories scanned | {number(totals['repositories'])} |",
        f"| Tracked text files | {number(totals['files'])} |",
        f"| Lines (including blank lines) | {number(totals['lines'])} |",
        f"| Non-blank lines | {number(totals['nonblankLines'])} |",
        f"| Characters (excluding line breaks) | {number(totals['characters'])} |",
        f"| Words | {number(totals['words'])} |",
        f"| Stars across scanned projects | {number(totals['stars'])} |",
        f"| Forks across scanned projects | {number(totals['forks'])} |",
    ]
    projects = sorted(stats.get("repositories", []), key=lambda item: item.get("characters", 0), reverse=True)[:8]
    if projects:
        rows.extend([
            "",
            "**Largest current text footprints**",
            "",
            "| Project | Files | Lines | Characters |",
            "| --- | ---: | ---: | ---: |",
        ])
        for project in projects:
            name = markdown_cell(project.get("name", "Repository"))
            url = project.get("url", "")
            rows.append(
                f"| [{name}]({url}) | {number(project['files'])} | {number(project['lines'])} | {number(project['characters'])} |"
            )
    rows.extend([
        "",
        f"_Scope: {markdown_cell(stats['scope'])}_",
    ])
    return "\n".join(rows)


def main() -> None:
    if not TOKEN:
        raise SystemExit("GH_TOKEN or GITHUB_TOKEN is required")
    repos = owned_public_repositories()
    if not repos:
        raise SystemExit("No eligible public repositories were returned; refusing to publish zeroed metrics")
    errors: list[str] = []
    scanned: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="vireon-code-stats-") as temporary:
        root = Path(temporary)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(scan_repository, repo, root) for repo in repos]
            for future in concurrent.futures.as_completed(futures):
                result, error = future.result()
                if result:
                    scanned.append(result)
                    print(f"Counted {result['name']}: {result['files']} files, {result['lines']} lines")
                elif error:
                    errors.append(error)
                    print(f"Skipped {error}")
    scanned.sort(key=lambda item: item.get("characters", 0), reverse=True)
    keys = ("files", "lines", "nonblankLines", "characters", "words", "stars", "forks")
    totals = {key: sum(int(item.get(key, 0)) for item in scanned) for key in keys}
    totals["repositories"] = len(scanned)
    totals["failedRepositories"] = len(errors)
    stats = {
        "schemaVersion": 1,
        "owner": OWNER,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "scope": (
            "Default-branch snapshots of public, non-fork, non-archived repositories owned by "
            f"{OWNER}. Counts cover tracked text-based source, configuration and documentation; "
            "dependency/build/generated directories, lockfiles, binary files and files over 1 MB are excluded. "
            "These are current codebase-footprint totals, not a line-by-line authorship attribution."
        ),
        "totals": totals,
        "repositories": scanned,
        "scanErrors": len(errors),
    }
    STATS_JSON_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not README_PATH.exists():
        raise SystemExit(f"README not found: {README_PATH}")
    readme = README_PATH.read_text(encoding="utf-8")
    if START not in readme or END not in readme:
        raise SystemExit("README code-stat markers are missing; refusing to overwrite unrelated content")
    before, remainder = readme.split(START, 1)
    _, after = remainder.split(END, 1)
    updated = before + START + "\n" + build_readme_block(stats) + "\n" + END + after
    README_PATH.write_text(updated, encoding="utf-8", newline="\n")
    print(f"Published footprint for {totals['repositories']} repositories; {totals['failedRepositories']} scan failures.")


if __name__ == "__main__":
    main()
