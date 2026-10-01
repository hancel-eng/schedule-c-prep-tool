"""Commits a newly learned bank profile (see core/bank_profiles.py) back to
this project's own GitHub repository, so it survives the next deploy.

Streamlit Community Cloud's filesystem is ephemeral -- it re-clones the repo
from scratch on every redeploy/restart, which would otherwise silently
discard every profile learned since the last deploy (the running instance
that learned it has to re-pay the AI extraction cost for that bank all over
again, forever, every time the app updates). Writing the learned profile
straight back to the repo's own bank_profiles/ directory is what actually
makes "a bank costs a few cents once, then nothing, forever" true, instead
of "once per deploy."

Requires a GITHUB_TOKEN with "Contents: Read and write" access scoped to
this one repository (a fine-grained GitHub Personal Access Token -- see
README.md for exact setup steps). Without it, learning still works for the
lifetime of the current running instance (core/bank_profiles.py's own local
save still happens first, unconditionally) -- it just won't survive the
next deploy. Same graceful-degradation pattern as a missing
OPENAI_API_KEY: never required, never a crash, just a smaller guarantee.
"""
import base64
import json
import os
from typing import Optional

import requests

from core.bank_profiles import BankProfile

GITHUB_REPO = os.environ.get("GITHUB_REPO", "hancel-eng/schedule-c-prep-tool")
GITHUB_API_BASE = "https://api.github.com"
GITHUB_BRANCH = "main"
_REQUEST_TIMEOUT_SECONDS = 15


def get_github_token() -> Optional[str]:
    return os.environ.get("GITHUB_TOKEN") or None


def commit_profile_to_github(profile: BankProfile) -> bool:
    """Creates or updates bank_profiles/<slug>.json directly on the main
    branch via the GitHub Contents API. Returns True on success, False if
    no token is configured or the request failed for any reason -- never
    raises. A failure here must never take down an extraction that already
    succeeded; the profile is already saved locally (core/bank_profiles.py)
    either way, so this is purely about durability past the next deploy,
    not about this session's own correctness.
    """
    token = get_github_token()
    if not token:
        return False

    path = f"bank_profiles/{profile.bank_slug}.json"
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_REPO}/contents/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        # An update requires the current file's blob sha; a brand-new
        # profile has none, and the Contents API treats that as a create.
        existing = requests.get(
            url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=_REQUEST_TIMEOUT_SECONDS
        )
        sha = existing.json().get("sha") if existing.status_code == 200 else None

        content = json.dumps(profile.model_dump(), indent=2) + "\n"
        payload = {
            "message": (
                f"Auto-learn bank profile: {profile.bank_slug}\n\n"
                "Learned automatically after a statement from this bank wasn't "
                "recognized by the existing rules engine. The rules engine, fed "
                "this profile, reproduced the AI-extracted and self-verified "
                "transaction totals exactly before this was committed -- see "
                "ARCHITECTURE.md's self-teaching fallback section."
            ),
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        response = requests.put(url, headers=headers, json=payload, timeout=_REQUEST_TIMEOUT_SECONDS)
        return response.status_code in (200, 201)
    except requests.RequestException:
        return False
