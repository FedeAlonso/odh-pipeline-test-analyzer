"""
Jira Client - Search for related Jira issues for test failures
"""
import re
import os
from typing import List, Dict, Any, Optional
import httpx

from .config import Config


class JiraClient:
    """Client for searching Red Hat Jira (Atlassian Cloud, Basic Auth)"""

    def __init__(self, base_url: str = "", api_token: str = ""):
        self.base_url = (base_url or Config.JIRA_URL).rstrip('/')
        self.api_token = api_token or Config.JIRA_TOKEN
        self.jira_user = Config.JIRA_USER
        self.ssl_verify = Config.SSL_VERIFY

    def _auth(self):
        if self.jira_user:
            return httpx.BasicAuth(self.jira_user, self.api_token)
        return None

    def _headers(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if not self.jira_user:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    async def _search_jql(self, jql: str, max_results: int = 5,
                          fields: List[str] = None) -> List[Dict[str, Any]]:
        if fields is None:
            fields = ["summary", "status", "priority", "assignee", "created", "updated"]
        try:
            async with httpx.AsyncClient(verify=self.ssl_verify, timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/rest/api/3/search/jql",
                    headers=self._headers(),
                    auth=self._auth(),
                    json={"jql": jql, "maxResults": max_results, "fields": fields},
                )
                if response.status_code == 200:
                    return response.json().get('issues', [])
                else:
                    print(f"Jira search failed: {response.status_code}")
                    return []
        except Exception as e:
            print(f"Error searching Jira: {e}")
            return []

    async def search_issues(
        self,
        test_name: str,
        project: str = "RHOAIENG",
        max_results: int = 5
    ) -> List[Dict[str, Any]]:
        search_terms = self._extract_search_terms(test_name)
        if not search_terms:
            return []
        jql = self._build_jql_query(search_terms, project)
        issues = await self._search_jql(jql, max_results,
                                        ["summary", "status", "priority", "assignee", "created", "updated", "description"])
        return self._format_issues(issues)

    def _extract_search_terms(self, test_name: str) -> List[str]:
        """Extract meaningful search terms from test name"""
        cleaned = test_name.replace('.cy.ts', '').replace('should ', '').replace('test ', '')
        words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)', cleaned)
        additional_words = re.split(r'[-_\s/]+', cleaned)
        all_words = words + additional_words

        stopwords = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'it', 'is', 'be'}
        meaningful_terms = [
            word.lower() for word in all_words
            if len(word) > 2 and word.lower() not in stopwords
        ]

        seen = set()
        unique_terms = []
        for term in meaningful_terms:
            if term not in seen:
                seen.add(term)
                unique_terms.append(term)

        return unique_terms[:5]

    def _build_jql_query(self, search_terms: List[str], project: str) -> str:
        if not search_terms:
            return f"project = {project} AND status != Closed AND summary !~ CVE ORDER BY updated DESC"

        text_conditions = [f'text ~ "{term}"' for term in search_terms]
        text_query = " OR ".join(text_conditions)

        return (f"project = {project} AND ({text_query}) "
                f"AND summary !~ CVE "
                f"ORDER BY updated DESC")

    def _format_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        formatted = []
        for issue in issues:
            fields = issue.get('fields', {})
            status = fields.get('status', {})
            priority = fields.get('priority', {})
            assignee = fields.get('assignee', {})

            formatted.append({
                'key': issue.get('key'),
                'summary': fields.get('summary', 'No summary'),
                'status': status.get('name', 'Unknown'),
                'priority': priority.get('name', 'Unknown'),
                'assignee': assignee.get('displayName', 'Unassigned') if assignee else 'Unassigned',
                'created': fields.get('created'),
                'updated': fields.get('updated'),
                'url': f"{self.base_url}/browse/{issue.get('key')}"
            })
        return formatted

    async def get_issue(self, issue_key: str) -> Optional[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(verify=self.ssl_verify, timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/rest/api/3/issue/{issue_key}",
                    headers=self._headers(),
                    auth=self._auth(),
                )
                if response.status_code == 200:
                    return self._format_issues([response.json()])[0]
                else:
                    return None
        except Exception as e:
            print(f"Error fetching Jira issue {issue_key}: {e}")
            return None

    async def search_by_error_message(
        self,
        error_message: str,
        project: str = "RHOAIENG",
        max_results: int = 3
    ) -> List[Dict[str, Any]]:
        error_parts = self._extract_error_signature(error_message)
        if not error_parts:
            return []

        text_conditions = [f'text ~ "{part.replace(chr(34), "")}"' for part in error_parts]
        text_query = " OR ".join(text_conditions)
        jql = f"project = {project} AND ({text_query}) ORDER BY updated DESC"

        issues = await self._search_jql(jql, max_results, ["summary", "status", "priority", "created", "updated"])
        return self._format_issues(issues)

    def _extract_error_signature(self, error_message: str) -> List[str]:
        error_message = error_message[:500]
        patterns = [
            r'Error:\s*([^\n]+)',
            r'Exception:\s*([^\n]+)',
            r'expected\s+(.+?)\s+(?:to|but)',
            r'Timed out\s+(.+)',
            r'failed\s+(.+)',
        ]

        signatures = []
        for pattern in patterns:
            match = re.search(pattern, error_message, re.IGNORECASE)
            if match:
                sig = match.group(1).strip()
                sig = re.sub(r'\s+', ' ', sig)
                sig = re.sub(r'[\'"`]', '', sig)
                if len(sig) > 10:
                    signatures.append(sig)

        return signatures[:3]
