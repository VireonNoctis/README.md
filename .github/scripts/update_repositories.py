#!/usr/bin/env python3
"""Refresh the generated repository section in the profile README."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

OWNER = os.environ.get('GITHUB_OWNER', 'VireonNoctis')
README_PATH = os.environ.get('README_PATH', 'README.md')
START = '<!-- AUTO-REPOSITORIES:START -->'
END = '<!-- AUTO-REPOSITORIES:END -->'
API = 'https://api.github.com'

def request_json(url, token):
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'Authorization': 'Bearer ' + token,
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'vireon-repository-readme-updater',
    })
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)

def safe_cell(value):
    return ' '.join(str(value or '').replace('|', '\\|').split())

def main():
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not token:
        raise SystemExit('GH_TOKEN or GITHUB_TOKEN is required')
    repos = []
    page = 1
    while page <= 10:
        query = urllib.parse.urlencode({
            'visibility': 'all', 'affiliation': 'owner',
            'per_page': 100, 'sort': 'updated', 'page': page,
        })
        batch = request_json(API + '/user/repos?' + query, token)
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    candidates = []
    for repo in repos:
        if repo.get('owner', {}).get('login', '').lower() != OWNER.lower():
            continue
        if repo.get('name', '').lower() == OWNER.lower():
            continue
        if repo.get('fork') or repo.get('archived') or not repo.get('description'):
            continue
        candidates.append(repo)
    candidates.sort(key=lambda item: item.get('updated_at', ''), reverse=True)
    candidates = candidates[:10]

    rows = [
        '| Repository | Description | GitHub-detected languages | Updated |',
        '| --- | --- | --- | --- |',
    ]
    for repo in candidates:
        name = repo.get('name', 'Repository')
        url = repo.get('html_url', '')
        description = safe_cell(repo.get('description'))
        try:
            languages = request_json(repo['languages_url'], token)
            stack = ', '.join(sorted(languages, key=languages.get, reverse=True)) or 'Not detected'
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, TimeoutError):
            stack = 'Unavailable'
        updated_at = (repo.get('updated_at') or '')[:10] or 'Unknown'
        rows.append('| [' + safe_cell(name) + '](' + url + ') | ' + description + ' | ' + safe_cell(stack) + ' | ' + updated_at + ' |')

    if not candidates:
        rows.append('| No described repositories found | Add a description in GitHub repository settings to include it here. | — | — |')
    generated = '\n'.join(rows)
    with open(README_PATH, 'r', encoding='utf-8') as handle:
        readme = handle.read()
    if START not in readme or END not in readme:
        raise SystemExit('README markers are missing; refusing to overwrite unrelated content')
    before, rest = readme.split(START, 1)
    _, after = rest.split(END, 1)
    result = before + START + '\n' + generated + '\n' + END + after
    with open(README_PATH, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(result)

if __name__ == '__main__':
    main()
