"""
Scrum team mapping for RHOAI Dashboard nightly test failure routing.

Resolves Cypress E2E test file paths to the owning scrum team.
Primary source: Confluence page (fetched at runtime).
Fallback: built-in DEFAULT_TEAMS mapping.
"""
import logging
import os
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List, Optional

import httpx

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


DEFAULT_TEAMS: List[ScrumTeam] = [
    ScrumTeam(
        name="Green",
        emoji=":large_green_circle:",
        slack_handle="@openshift-ai-dashboard-green-scrum",
        slack_group_id="S07BJDHQR2R",
        ci_tags=["ModelRegistryCI"],
        path_patterns=["modelRegistry/", "modelCatalog/"],
    ),
    ScrumTeam(
        name="Razzmatazz",
        emoji=":razzmatazz:",
        slack_handle="@openshift-ai-dashboard-razzmatazz-scrum",
        slack_group_id="S06FFRUSFH8",
        ci_tags=[
            "AgentOpsCI",
            "ConnectionTypes",
            "HardwareProfiles",
            "MlflowEmbedded",
            "MLflowCI",
        ],
        path_patterns=[
            "pipelines/",
            "agentOps/",
            "connectionTypes/",
            "hardwareProfiles/",
            "mlflow/",
        ],
        is_default=True,
    ),
    ScrumTeam(
        name="Zaffre",
        emoji=":zaffre:",
        slack_handle="@openshift-ai-dashboard-zaffre-scrum",
        slack_group_id="S07CFUVMXBM",
        ci_tags=["KServeCI", "NIMServingCI", "ModelServingCI", "LLMDServingCI"],
        path_patterns=[
            "modelServing/",
            "nim/",
            "dataScienceProjects/models/",
            "dataScienceProjects/connections/",
            "settings/servingRuntimes/",
        ],
    ),
    ScrumTeam(
        name="Monarch",
        emoji=":large_yellow_circle:",
        slack_handle="@openshift-ai-dashboard-monarch-scrum",
        slack_group_id="S07EBN8NY1L",
        ci_tags=["Observability"],
        path_patterns=["distributedWorkloadMetrics/"],
    ),
    ScrumTeam(
        name="Crimson",
        emoji=":crimson:",
        slack_handle="@openshift-ai-dashboard-crimson-scrum",
        slack_group_id="S09A6NJHBU1",
        ci_tags=["GenAICI"],
        path_patterns=["gen-ai/"],
    ),
    ScrumTeam(
        name="Teal",
        emoji=":teal:",
        slack_handle="@group-openshift-ai-notebooks-server-extensions",
        slack_group_id="S05UB882TDF",
        ci_tags=["Notebooks Extensions"],
        path_patterns=[
            "dataScienceProjects/workbenches/",
            "applications/enabled/",
        ],
    ),
    ScrumTeam(
        name="Indigo",
        emoji=":indigo:",
        slack_handle="@group-openshift-ai-notebooks-server-extensions",
        slack_group_id="S05UB882TDF",
        ci_tags=["Notebooks Server"],
        path_patterns=["notebooks/"],
    ),
    ScrumTeam(
        name="Onyx",
        emoji=":onyxl:",
        slack_handle="@openshift-ai-dashboard-onyx-scrum",
        slack_group_id="S0AU18N3AFP",
        ci_tags=["MaaSCI"],
        path_patterns=["maas/"],
    ),
    ScrumTeam(
        name="Purple",
        emoji=":large_purple_circle:",
        slack_handle="@openshift-ai-dashboards-purple-scrum",
        slack_group_id="S0ABKEG0C14",
        ci_tags=["AutoMLCI", "AutoRAGCI", "EvalHubCI"],
        path_patterns=["automl/", "autorag/", "evalHub/"],
    ),
    ScrumTeam(
        name="Tangerine",
        emoji=":tangerine:",
        slack_handle="@openshift-ai-dashboard-tangerine-scrum",
        slack_group_id="S0AG2A9KP5W",
        ci_tags=["FeatureStoreCI", "ModelTrainingCI", "GpuaasCI"],
        path_patterns=["featureStore/", "modelTraining/", "rayjob/"],
    ),
    ScrumTeam(
        name="Pewter",
        emoji=":star:",
        slack_handle="@openshift-ai-dashboard-pewter-scrum",
        slack_group_id="S0B5BJW6T8S",
        ci_tags=[],
        path_patterns=[],
    ),
]


_cached_mapping: Optional[List[ScrumTeam]] = None


class _ConfluenceTableParser(HTMLParser):
    """Extracts rows from the first HTML table in Confluence storage format."""

    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self._current_row: List[str] = []
        self._current_cell: List[str] = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = []
        elif tag == "tr":
            self._current_row = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_cell = False
            self._current_row.append("".join(self._current_cell).strip())
        elif tag == "tr" and self._current_row:
            self.rows.append(self._current_row)

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell.append(data)


def fetch_mapping_from_confluence(page_id: str) -> Optional[List[ScrumTeam]]:
    """Fetch scrum team mapping from a Confluence page table.

    Expects a table with columns:
      Team Name | Slack Handle | Slack Group ID | CI Tags | Path Patterns
    CI Tags and Path Patterns are comma-separated.
    """
    jira_url = Config.JIRA_URL.rstrip("/")
    jira_user = Config.JIRA_USER
    jira_token = Config.JIRA_TOKEN

    if not all([jira_url, jira_user, jira_token, page_id]):
        return None

    wiki_base = jira_url.replace("/rest/api", "").rstrip("/")
    if "/wiki" not in wiki_base:
        wiki_base = wiki_base + "/wiki"

    url = f"{wiki_base}/rest/api/content/{page_id}?expand=body.storage"

    try:
        resp = httpx.get(
            url,
            auth=(jira_user, jira_token),
            timeout=15,
            verify=Config.SSL_VERIFY,
        )
        resp.raise_for_status()
        data = resp.json()
        html_body = data.get("body", {}).get("storage", {}).get("value", "")
        if not html_body:
            log.warning("Confluence page %s has no body content", page_id)
            return None

        parser = _ConfluenceTableParser()
        parser.feed(html_body)

        if len(parser.rows) < 2:
            log.warning("Confluence page %s has no table rows", page_id)
            return None

        header = [h.lower().strip() for h in parser.rows[0]]
        required = {"team name", "slack handle", "slack group id"}
        if not required.issubset(set(header)):
            log.warning(
                "Confluence table missing required columns: %s (found: %s)",
                required - set(header),
                header,
            )
            return None

        col = {h: i for i, h in enumerate(header)}
        teams = []
        default_name = None

        for row in parser.rows[1:]:
            if len(row) < len(header):
                continue
            name = row[col["team name"]].strip()
            handle = row[col["slack handle"]].strip()
            group_id = row[col["slack group id"]].strip()

            ci_tags_raw = row[col.get("ci tags", -1)] if "ci tags" in col else ""
            ci_tags = [t.strip() for t in ci_tags_raw.split(",") if t.strip()]

            patterns_raw = row[col.get("path patterns", -1)] if "path patterns" in col else ""
            patterns = [p.strip() for p in patterns_raw.split(",") if p.strip()]

            is_default = "(default" in patterns_raw.lower() or "(default" in name.lower()
            if is_default:
                default_name = name
                patterns = [p for p in patterns if not p.startswith("(")]

            teams.append(
                ScrumTeam(
                    name=name,
                    emoji="",
                    slack_handle=handle,
                    slack_group_id=group_id,
                    ci_tags=ci_tags,
                    path_patterns=patterns,
                    is_default=is_default,
                )
            )

        if not teams:
            log.warning("No teams parsed from Confluence page %s", page_id)
            return None

        log.info(
            "Loaded %d scrum teams from Confluence page %s (default: %s)",
            len(teams),
            page_id,
            default_name or "none",
        )
        return teams

    except Exception as e:
        log.warning("Failed to fetch Confluence page %s: %s", page_id, e)
        return None


def get_team_mapping() -> List[ScrumTeam]:
    """Return the scrum team mapping, using Confluence if available."""
    global _cached_mapping
    if _cached_mapping is not None:
        return _cached_mapping

    page_id = os.getenv("CONFLUENCE_PAGE_ID", "")
    if page_id:
        teams = fetch_mapping_from_confluence(page_id)
        if teams:
            _cached_mapping = teams
            return teams

    log.info("Using built-in default scrum team mapping")
    _cached_mapping = DEFAULT_TEAMS
    return _cached_mapping


def resolve_team(file_path: str) -> ScrumTeam:
    """Map a Cypress test file path to the owning scrum team.

    Returns the matching team, or the default team (Razzmatazz) if no match.
    """
    teams = get_team_mapping()

    normalized = file_path.replace("\\", "/")
    # Strip the common prefix to get the relative path under e2e/
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

    return next(t for t in DEFAULT_TEAMS if t.name == "Razzmatazz")


def format_slack_mention(team: ScrumTeam) -> str:
    """Format a Slack @-mention for the team.

    Uses <!subteam^ID|@handle> for real pings when a group ID is set.
    Falls back to plain text when the group ID is a placeholder.
    """
    if team.slack_group_id and team.slack_group_id != PLACEHOLDER_GROUP_ID:
        return f"<!subteam^{team.slack_group_id}|{team.slack_handle}>"
    return team.slack_handle


def clear_cache():
    """Clear the cached team mapping (for testing)."""
    global _cached_mapping
    _cached_mapping = None
