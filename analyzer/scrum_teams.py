"""
Scrum team mapping for RHOAI Dashboard nightly test failure routing.

Resolves Cypress E2E test file paths to the owning scrum team.
Source of truth: team-ownership.json in the odh-dashboard repo.
"""
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from analyzer.config import Config

log = logging.getLogger(__name__)

PLACEHOLDER_GROUP_ID = "SXXXXXXXXXX"


@dataclass
class ScrumTeam:
    name: str
    emoji: str
    slack_handle: str
    slack_group_id: str
    ci_tags: List[str] = field(default_factory=list)
    path_patterns: List[str] = field(default_factory=list)
    is_default: bool = False


SLACK_DIRECTORY = {
    "Green": ("@openshift-ai-dashboard-green-scrum", "S07BJDHQR2R", ":large_green_circle:"),
    "Razzmatazz": ("@openshift-ai-dashboard-razzmatazz-scrum", "S06FFRUSFH8", ":razzmatazz:"),
    "Zaffre": ("@openshift-ai-dashboard-zaffre-scrum", "S07CFUVMXBM", ":zaffre:"),
    "Monarch": ("@openshift-ai-dashboard-monarch-scrum", "S07EBN8NY1L", ":large_yellow_circle:"),
    "Crimson": ("@openshift-ai-dashboard-crimson-scrum", "S09A6NJHBU1", ":crimson:"),
    "Teal": ("@group-openshift-ai-notebooks-server-extensions", "S05UB882TDF", ":teal:"),
    "Indigo": ("@group-openshift-ai-notebooks-server-extensions", "S05UB882TDF", ":indigo:"),
    "Onyx": ("@openshift-ai-dashboard-onyx-scrum", "S0AU18N3AFP", ":onyxl:"),
    "Purple": ("@openshift-ai-dashboards-purple-scrum", "S0ABKEG0C14", ":large_purple_circle:"),
    "Tangerine": ("@openshift-ai-dashboard-tangerine-scrum", "S0AG2A9KP5W", ":tangerine:"),
    "Pewter": ("@openshift-ai-dashboard-pewter-scrum", "S0B5BJW6T8S", ":star:"),
    "Unassigned": ("@openshift-ai-dashboard-qe", "S08AZ980ER0", ":question:"),
}


_cached_mapping: Optional[List[ScrumTeam]] = None


def _build_team(name: str, ci_tags: List[str], path_patterns: List[str],
                is_default: bool = False) -> ScrumTeam:
    """Build a ScrumTeam by merging ownership data with Slack directory info."""
    slack_handle, slack_group_id, emoji = SLACK_DIRECTORY.get(
        name, ("@openshift-ai-dashboard-razzmatazz-scrum", PLACEHOLDER_GROUP_ID, "")
    )
    return ScrumTeam(
        name=name,
        emoji=emoji,
        slack_handle=slack_handle,
        slack_group_id=slack_group_id,
        ci_tags=ci_tags,
        path_patterns=path_patterns,
        is_default=is_default,
    )


def _resolve_ownership_path() -> Path:
    """Resolve the absolute path to team-ownership.json."""
    configured = Config.TEAM_OWNERSHIP_PATH
    p = Path(configured)
    if p.is_absolute():
        return p

    repo_path = os.getenv("FRONTEND_REPO_PATH", "")
    if repo_path:
        return Path(repo_path) / configured

    return p


def _load_ownership_file(filepath: Path) -> List[ScrumTeam]:
    """Load and parse team-ownership.json."""
    data = json.loads(filepath.read_text())
    default_team_name = data.get("default_team", "Razzmatazz")
    teams = []
    for entry in data.get("teams", []):
        name = entry.get("name", "")
        if not name:
            continue
        teams.append(_build_team(
            name=name,
            ci_tags=entry.get("ci_tags", []),
            path_patterns=entry.get("path_patterns", []),
            is_default=(name == default_team_name),
        ))
    return teams


def get_team_mapping() -> List[ScrumTeam]:
    """Return the scrum team mapping from team-ownership.json."""
    global _cached_mapping
    if _cached_mapping is not None:
        return _cached_mapping

    filepath = _resolve_ownership_path()
    if not filepath.exists():
        raise FileNotFoundError(
            f"team-ownership.json not found at {filepath}. "
            f"Set FRONTEND_REPO_PATH or TEAM_OWNERSHIP_PATH to the correct location."
        )

    teams = _load_ownership_file(filepath)
    if not teams:
        raise ValueError(f"team-ownership.json at {filepath} contains no teams")

    log.info("Loaded %d scrum teams from %s", len(teams), filepath)
    _cached_mapping = teams
    return _cached_mapping


def resolve_team(file_path: str) -> ScrumTeam:
    """Map a Cypress test file path to the owning scrum team.

    Returns the matching team, or the default team if no match.
    """
    teams = get_team_mapping()

    normalized = file_path.replace("\\", "/")
    for prefix in ("cypress/tests/e2e/", "tests/e2e/", "e2e/"):
        if prefix in normalized:
            normalized = normalized[normalized.index(prefix) + len(prefix):]
            break

    for team in teams:
        for pattern in team.path_patterns:
            if pattern in normalized:
                return team

    default = next((t for t in teams if t.is_default), None)
    if default:
        return default

    return teams[0]


def format_slack_mention(team: ScrumTeam) -> str:
    """Format a Slack @-mention for the team.

    Uses <!subteam^ID|@handle> for real pings when a group ID is set.
    Falls back to plain text when the group ID is a placeholder.
    """
    if team.slack_group_id and team.slack_group_id != PLACEHOLDER_GROUP_ID:
        return f"<!subteam^{team.slack_group_id}|{team.slack_handle}>"
    return team.slack_handle


def clear_cache():
    """Clear the cached team mapping."""
    global _cached_mapping
    _cached_mapping = None
