#!/usr/bin/env python3
"""Generate a repeatable footprint for public repositories owned or contributed to."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import re
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
REPO_CONFIG_PATH = Path(os.environ.get("REPO_CONFIG_PATH", ".github/code_stats_repositories.json"))
START = "<!-- AUTO-CODE-STATS:START -->"
END = "<!-- AUTO-CODE-STATS:END -->"
API = "https://api.github.com"
MAX_FILE_BYTES = 1_000_000
MAX_OWNED_REPOSITORIES = 100
MAX_AUTO_DISCOVERED_REPOSITORIES = 15
MAX_TOTAL_REPOSITORIES = 120
REPO_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
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
LANGUAGE_BY_EXTENSION = {
    ".rs": "Rust", ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".py": "Python", ".pyi": "Python", ".go": "Go", ".kt": "Kotlin", ".kts": "Kotlin",
    ".java": "Java", ".rb": "Ruby", ".r": "R", ".lua": "Lua", ".swift": "Swift", ".dart": "Dart",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++", ".h": "C/C++ header", ".c": "C",
    ".php": "PHP", ".cs": "C#", ".vb": "Visual Basic", ".fs": "F#", ".fsx": "F#",
    ".css": "CSS", ".scss": "SCSS", ".sass": "SCSS", ".less": "Less",
    ".html": "HTML", ".htm": "HTML", ".svelte": "Svelte", ".vue": "Vue", ".astro": "Astro",
    ".json": "JSON", ".jsonc": "JSONC", ".json5": "JSON5", ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML", ".ini": "INI / config", ".cfg": "INI / config", ".conf": "Config",
    ".sql": "SQL", ".graphql": "GraphQL", ".gql": "GraphQL", ".proto": "Protocol Buffers",
    ".xml": "XML", ".svg": "SVG", ".xaml": "XAML", ".properties": "Properties",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell", ".bat": "Batch", ".cmd": "Batch",
    ".md": "Markdown", ".mdx": "MDX", ".rst": "reStructuredText", ".txt": "Plain text",
    ".tf": "Terraform (HCL)", ".hcl": "HCL", ".nix": "Nix", ".ipynb": "Jupyter Notebook",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".hrl": "Erlang",
    ".clj": "Clojure", ".cljs": "ClojureScript", ".cljc": "Clojure", ".scala": "Scala", ".sc": "Scala",
    ".pl": "Perl", ".m": "Objective-C", ".mm": "Objective-C++", ".make": "Makefile", ".gradle": "Gradle",
    ".mod": "Go module",
}
LANGUAGE_BY_FILENAME = {
    "readme": "Markdown", "license": "Plain text", "copying": "Plain text",
    "dockerfile": "Dockerfile", "makefile": "Makefile", "justfile": "Justfile",
    "procfile": "Config", ".gitignore": "Git config", ".gitattributes": "Git config",
    ".dockerignore": "Docker config", ".editorconfig": "EditorConfig", ".env.example": "Environment config",
    ".npmrc": "NPM config", ".gitmodules": "Git config", ".prettierrc": "JSON config", ".eslintrc": "JSON config",
}
COUNT_KEYS = ("files", "lines", "nonblankLines", "characters", "words", "stars", "forks")


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


def mark_repository(repo: dict, relationship: str, note: str = "", role: str = "") -> dict:
    item = dict(repo)
    item["_stats_relationship"] = relationship
    if note:
        item["_contribution_note"] = note
    item["_contribution_role"] = role or ("Repository owner" if relationship == "owned" else "Contributor")
    return item


def is_scannable_public_repository(repo: dict) -> bool:
    return (
        not repo.get("private")
        and not repo.get("archived")
        and not repo.get("disabled")
        and bool(repo.get("default_branch"))
        and bool(repo.get("clone_url"))
        and bool(repo.get("full_name"))
    )


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
            if not is_scannable_public_repository(repo):
                continue
            items.append(mark_repository(repo, "owned", "Repository owned by the account."))
        if len(batch) < 100:
            break
    items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    return items[:MAX_OWNED_REPOSITORIES]


def configured_contributor_repositories() -> tuple[list[dict], list[str]]:
    if not REPO_CONFIG_PATH.exists():
        print(f"No contributor config found at {REPO_CONFIG_PATH}; using automatic discovery only.")
        return [], []
    try:
        config = json.loads(REPO_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [], [f"Contributor config: {type(error).__name__}"]

    configured = config.get("contributorRepositories", []) if isinstance(config, dict) else []
    if not isinstance(configured, list):
        return [], ["Contributor config: contributorRepositories must be a list"]

    items: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for entry in configured:
        full_name = entry.get("fullName", "") if isinstance(entry, dict) else str(entry)
        note = entry.get("note", "") if isinstance(entry, dict) else ""
        role = entry.get("role", "") if isinstance(entry, dict) else ""
        if not REPO_SLUG.fullmatch(full_name):
            errors.append(f"Invalid configured repository slug: {full_name!r}")
            continue
        key = full_name.lower()
        if key in seen or key.split("/", 1)[0] == OWNER.lower():
            continue
        seen.add(key)
        try:
            owner, name = full_name.split("/", 1)
            repo = request_json(f"{API}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}")
            if not isinstance(repo, dict) or not is_scannable_public_repository(repo):
                errors.append(f"{full_name}: repository is private, archived, disabled, or unavailable")
                continue
            items.append(mark_repository(
                repo,
                "contributor",
                str(note).strip() or "Explicitly listed contributor project.",
                str(role).strip() or "Major contributor",
            ))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as error:
            errors.append(f"{full_name}: {type(error).__name__}")
    return items, errors


def discover_authored_commit_repositories(excluded: set[str]) -> tuple[list[dict], str | None]:
    """Discover public external repositories with commits attributed to the configured GitHub login."""
    discovered: dict[str, dict] = {}
    try:
        for page in range(1, 4):
            query = urllib.parse.urlencode({
                "q": f"author:{OWNER}",
                "per_page": 100,
                "page": page,
                "sort": "committer-date",
                "order": "desc",
            })
            payload = request_json(f"{API}/search/commits?{query}")
            if not isinstance(payload, dict):
                break
            matches = payload.get("items", [])
            if not isinstance(matches, list) or not matches:
                break
            for match in matches:
                repo = match.get("repository") if isinstance(match, dict) else None
                if not isinstance(repo, dict):
                    continue
                full_name = str(repo.get("full_name", ""))
                key = full_name.lower()
                if not REPO_SLUG.fullmatch(full_name) or key in excluded:
                    continue
                if (repo.get("owner") or {}).get("login", "").lower() == OWNER.lower():
                    continue
                if repo.get("private") or repo.get("archived") or repo.get("disabled"):
                    continue
                # Search results normally include the repository's clone URL and default branch.
                if not repo.get("clone_url") or not repo.get("default_branch"):
                    owner, name = full_name.split("/", 1)
                    repo = request_json(f"{API}/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}")
                if not isinstance(repo, dict) or not is_scannable_public_repository(repo):
                    continue
                discovered[key] = mark_repository(
                    repo,
                    "contributor",
                    f"Public commits attributed to {OWNER} were found by GitHub search.",
                )
                if len(discovered) >= MAX_AUTO_DISCOVERED_REPOSITORIES:
                    break
            if len(discovered) >= MAX_AUTO_DISCOVERED_REPOSITORIES or len(matches) < 100:
                break
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
        # Manual contributor entries remain effective even if search is throttled or unavailable.
        return list(discovered.values()), f"{type(error).__name__}: automatic contributor discovery was incomplete"
    return list(discovered.values()), None


def collect_repositories() -> tuple[list[dict], list[str], str | None]:
    owned = owned_public_repositories()
    owned_keys = {str(repo["full_name"]).lower() for repo in owned}
    configured, config_errors = configured_contributor_repositories()
    excluded = owned_keys | {str(repo["full_name"]).lower() for repo in configured}
    discovered, discovery_warning = discover_authored_commit_repositories(excluded)
    candidates = owned + configured + discovered

    # Deduplicate by full repository name, preferring an owned or explicitly configured entry.
    unique: dict[str, dict] = {}
    for repo in candidates:
        key = str(repo.get("full_name", "")).lower()
        if not key:
            continue
        if key not in unique or (
            unique[key].get("_stats_relationship") != "owned"
            and repo.get("_contribution_note", "").startswith("Explicitly listed")
        ):
            unique[key] = repo
    result = list(unique.values())[:MAX_TOTAL_REPOSITORIES]
    return result, config_errors, discovery_warning


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


def language_for_path(raw_path: str) -> str:
    path = PurePosixPath(raw_path)
    filename = path.name.lower()
    if filename in LANGUAGE_BY_FILENAME:
        return LANGUAGE_BY_FILENAME[filename]
    return LANGUAGE_BY_EXTENSION.get(path.suffix.lower(), "Other text/config")


def count_worktree(repo_dir: Path) -> dict:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-files", "-z"],
        check=True, capture_output=True, timeout=30,
    )
    counts = {"files": 0, "lines": 0, "nonblankLines": 0, "characters": 0, "words": 0}
    language_counts: dict[str, dict[str, int | str]] = {}
    for encoded_path in result.stdout.split(b"\\0"):
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
            if b"\\0" in raw:
                continue
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeError):
            continue
        normalized = text.replace("\\r\\n", "\\n").replace("\\r", "\\n")
        lines = normalized.splitlines()
        file_counts = {
            "files": 1,
            "lines": len(lines),
            "nonblankLines": sum(1 for line in lines if line.strip()),
            "characters": sum(len(line) for line in lines),
            "words": len(WORD_PATTERN.findall(normalized)),
        }
        for key, value in file_counts.items():
            counts[key] += value
        language = language_for_path(relative)
        bucket = language_counts.setdefault(language, {
            "language": language, "files": 0, "lines": 0, "nonblankLines": 0, "characters": 0, "words": 0,
        })
        for key, value in file_counts.items():
            bucket[key] = int(bucket[key]) + value

    languages = sorted(
        language_counts.values(),
        key=lambda item: (int(item["lines"]), int(item["characters"])),
        reverse=True,
    )
    return {**counts, "languages": languages}


def scan_repository(repo: dict, root: Path) -> tuple[dict | None, str | None]:
    full_name = str(repo.get("full_name", repo.get("name", "repository")))
    branch = str(repo.get("default_branch", ""))
    destination_name = re.sub(r"[^A-Za-z0-9_.-]", "_", full_name.replace("/", "--"))
    destination = root / destination_name
    clone_url = str(repo.get("clone_url", ""))
    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--depth=1", "--single-branch", "--branch", branch, clone_url, str(destination)],
            check=True, capture_output=True, timeout=120,
        )
        counts = count_worktree(destination)
        return ({
            "name": str(repo.get("name", full_name.split("/")[-1])),
            "fullName": full_name,
            "url": str(repo.get("html_url", "")),
            "branch": branch,
            "relationship": repo.get("_stats_relationship", "owned"),
            "role": repo.get("_contribution_role", "Repository owner" if repo.get("_stats_relationship", "owned") == "owned" else "Contributor"),
            "contributionNote": repo.get("_contribution_note", ""),
            **counts,
            "stars": int(repo.get("stargazers_count") or 0),
            "forks": int(repo.get("forks_count") or 0),
        }, None)
    except (OSError, subprocess.SubprocessError) as error:
        return (None, f"{full_name}: {type(error).__name__}")


def sum_counts(repositories: list[dict]) -> dict[str, int]:
    return {key: sum(int(item.get(key, 0)) for item in repositories) for key in COUNT_KEYS}


def aggregate_languages(repositories: list[dict]) -> list[dict]:
    totals: dict[str, dict[str, int | str]] = {}
    for repo in repositories:
        for language in repo.get("languages", []):
            name = str(language.get("language", "Other text/config"))
            bucket = totals.setdefault(name, {
                "language": name, "files": 0, "lines": 0, "nonblankLines": 0, "characters": 0, "words": 0,
            })
            for key in ("files", "lines", "nonblankLines", "characters", "words"):
                bucket[key] = int(bucket[key]) + int(language.get(key, 0))
    return sorted(totals.values(), key=lambda item: (int(item["lines"]), int(item["characters"])), reverse=True)


def load_manual_project_estimates() -> list[dict]:
    try:
        config = json.loads(REPO_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    estimates = config.get("manualProjectEstimates", []) if isinstance(config, dict) else []
    if not isinstance(estimates, list):
        return []
    valid: list[dict] = []
    for estimate in estimates:
        if not isinstance(estimate, dict) or not isinstance(estimate.get("areas"), list):
            continue
        areas = []
        for area in estimate["areas"]:
            if not isinstance(area, dict):
                continue
            try:
                line_count = int(area.get("lines"))
            except (TypeError, ValueError):
                continue
            if line_count < 0:
                continue
            areas.append({
                "name": str(area.get("name", "Unnamed area")),
                "stack": str(area.get("stack", "Unspecified")),
                "lines": line_count,
            })
        if areas:
            valid.append({
                "id": str(estimate.get("id", "manual-estimate")),
                "name": str(estimate.get("name", "Unpublished project")),
                "url": str(estimate.get("url", "")),
                "classification": str(estimate.get("classification", "User-provided estimate; not scanned automatically.")),
                "areas": areas,
            })
    return valid


def number(value: int) -> str:
    return f"{value:,}"


def markdown_cell(value: object) -> str:
    return " ".join(str(value or "").replace("|", r"\|").split())


def build_readme_block(stats: dict) -> str:
    totals = stats["totals"]
    generated_date = (stats.get("generatedAt") or "")[:10] or "date unavailable"
    rows = [
        f"**Last scan:** {generated_date} UTC · {number(totals['repositories'])} public repositories scanned "
        f"({number(totals['ownedRepositories'])} owned, {number(totals['contributorRepositories'])} contributed).",
        "",
        "| Measure | Current total |",
        "| --- | ---: |",
        f"| Public repositories scanned | {number(totals['repositories'])} |",
        f"| Owned repositories scanned | {number(totals['ownedRepositories'])} |",
        f"| Contributor repositories scanned | {number(totals['contributorRepositories'])} |",
        f"| Repositories with scan failures | {number(totals['failedRepositories'])} |",
        f"| Tracked text files | {number(totals['files'])} |",
        f"| Lines (including blank lines) | {number(totals['lines'])} |",
        f"| Non-blank lines | {number(totals['nonblankLines'])} |",
        f"| Characters (excluding line breaks) | {number(totals['characters'])} |",
        f"| Words | {number(totals['words'])} |",
        f"| Stars across scanned projects | {number(totals['stars'])} |",
        f"| Forks across scanned projects | {number(totals['forks'])} |",
    ]

    languages = stats.get("languages", [])
    if languages:
        rows.extend([
            "",
            "**Language and file-format footprint**",
            "",
            "| Language / format | Files | Lines | Characters | Words |",
            "| --- | ---: | ---: | ---: | ---: |",
        ])
        for language in languages:
            rows.append(
                "| " + markdown_cell(language["language"]) + " | " + number(language["files"]) + " | "
                + number(language["lines"]) + " | " + number(language["characters"]) + " | " + number(language["words"]) + " |"
            )

    estimates = stats.get("manualProjectEstimates", [])
    for estimate in estimates:
        rows.extend([
            "",
            "**" + markdown_cell(estimate["name"]) + " — supplied project estimates**",
            "",
            "| Subsystem | Stack | Lines |",
            "| --- | --- | ---: |",
        ])
        for area in estimate.get("areas", []):
            rows.append("| " + markdown_cell(area["name"]) + " | " + markdown_cell(area["stack"]) + " | " + number(area["lines"]) + " |")
        reported_total = sum(int(area["lines"]) for area in estimate.get("areas", []))
        rows.append("| **Reported total** | | **" + number(reported_total) + "** |")
        rows.append("_" + markdown_cell(estimate.get("classification", "User-provided estimate; not scanned automatically.")) + "_")

    projects = sorted(stats.get("repositories", []), key=lambda item: item.get("characters", 0), reverse=True)[:8]
    if projects:
        rows.extend([
            "",
            "**Largest current text footprints**",
            "",
            "| Project | Relationship / role | Files | Lines | Characters |",
            "| --- | --- | ---: | ---: | ---: |",
        ])
        for project in projects:
            name = markdown_cell(project.get("name", "Repository"))
            url = project.get("url", "")
            relationship = markdown_cell(project.get("role") or ("Repository owner" if project.get("relationship") == "owned" else "Contributor"))
            rows.append(
                f"| [{name}]({url}) | {relationship} | {number(project['files'])} | "
                f"{number(project['lines'])} | {number(project['characters'])} |"
            )

    contributed = sorted(
        (item for item in stats.get("repositories", []) if item.get("relationship") == "contributor"),
        key=lambda item: item.get("characters", 0),
        reverse=True,
    )
    if contributed:
        rows.extend([
            "",
            "**Contributor projects included**",
            "",
            "| Project | Role | Tracked files | Lines | Characters |",
            "| --- | --- | ---: | ---: | ---: |",
        ])
        for project in contributed:
            name = markdown_cell(project.get("name", "Repository"))
            url = project.get("url", "")
            rows.append(
                f"| [{name}]({url}) | {markdown_cell(project.get('role') or 'Major contributor')} | {number(project['files'])} | "
                f"{number(project['lines'])} | {number(project['characters'])} |"
            )

    rows.extend([
        "",
        f"_Scope: {markdown_cell(stats['scope'])}_",
    ])
    if stats.get("discoveryWarning"):
        rows.append(f"_Contributor discovery note: {markdown_cell(stats['discoveryWarning'])}_")
    return "\n".join(rows)


def main() -> None:
    if not TOKEN:
        raise SystemExit("GH_TOKEN or GITHUB_TOKEN is required")
    repos, config_errors, discovery_warning = collect_repositories()
    if not repos:
        raise SystemExit("No eligible public repositories were returned; refusing to publish zeroed metrics")

    errors: list[str] = list(config_errors)
    scanned: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="vireon-code-stats-") as temporary:
        root = Path(temporary)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(scan_repository, repo, root) for repo in repos]
            for future in concurrent.futures.as_completed(futures):
                result, error = future.result()
                if result:
                    scanned.append(result)
                    print(
                        f"Counted {result['fullName']} ({result['relationship']}): "
                        f"{result['files']} files, {result['lines']} lines"
                    )
                elif error:
                    errors.append(error)
                    print(f"Skipped {error}")

    scanned.sort(key=lambda item: item.get("characters", 0), reverse=True)
    owned_scanned = [item for item in scanned if item.get("relationship") == "owned"]
    contributed_scanned = [item for item in scanned if item.get("relationship") == "contributor"]
    totals = sum_counts(scanned)
    totals["repositories"] = len(scanned)
    totals["ownedRepositories"] = len(owned_scanned)
    totals["contributorRepositories"] = len(contributed_scanned)
    totals["failedRepositories"] = len(errors)

    all_keys = ("files", "lines", "nonblankLines", "characters", "words", "stars", "forks")
    def category_totals(items: list[dict]) -> dict[str, int]:
        result = sum_counts(items)
        result["repositories"] = len(items)
        return result

    stats = {
        "schemaVersion": 3,
        "owner": OWNER,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "scope": (
            f"Current default-branch snapshots of eligible public repositories owned by {OWNER} plus public external "
            "repositories explicitly listed as contributor projects or discovered through commits attributed to the "
            "GitHub login. Counts cover tracked text-based source, configuration, and documentation; dependency/build/"
            "generated directories, lockfiles, binary files, and files over 1 MB are excluded. These repository-wide "
            "footprint totals are not line-by-line authorship attribution. Language counts follow tracked file "
            "extensions; manually supplied project estimates are kept separate from repository scans."
        ),
        "discoveryWarning": discovery_warning,
        "totals": totals,
        "ownedTotals": category_totals(owned_scanned),
        "contributedTotals": category_totals(contributed_scanned),
        "languages": aggregate_languages(scanned),
        "manualProjectEstimates": load_manual_project_estimates(),
        "repositories": scanned,
        "scanErrors": len(errors),
        "scanErrorDetails": errors,
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
    print(
        f"Published footprint for {totals['repositories']} repositories "
        f"({totals['ownedRepositories']} owned, {totals['contributorRepositories']} contributed); "
        f"{totals['failedRepositories']} scan failures."
    )


if __name__ == "__main__":
    main()
