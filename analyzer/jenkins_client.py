"""
Jenkins Client - Direct HTTP API wrapper for fetching Jenkins job and build data.

Note: MCP client code has been removed. Use the MCP server (mcp/server.py) separately
if you need MCP protocol support.
"""
import os
import re
import json
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import httpx

from .config import Config


class JenkinsClient:
    """Client for interacting with Jenkins API via direct HTTP"""

    def __init__(self, jenkins_url: str, jenkins_token: str, jenkins_username: str = "", jenkins_password: str = ""):
        self.jenkins_url = jenkins_url.rstrip('/')
        self.jenkins_token = jenkins_token
        self.jenkins_username = jenkins_username
        self.jenkins_password = jenkins_password
        
        # SSL verification setting from config
        self.ssl_verify = Config.SSL_VERIFY
        
        # Format token for auth
        if jenkins_username and jenkins_password:
            self.jenkins_token = f"{jenkins_username}:{jenkins_password}"

        self._session = None
        
        # Always use direct HTTP API
        print(f"✓ Jenkins client initialized (SSL verify: {self.ssl_verify})")

    async def _request(self, url: str, client: httpx.AsyncClient) -> httpx.Response:
        """Send a GET request with auth, retrying without auth on 401 (anonymous fallback)."""
        if ':' in self.jenkins_token:
            username, token = self.jenkins_token.split(':', 1)
            response = await client.get(url, auth=(username, token))
        else:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {self.jenkins_token}"}
            )

        if response.status_code == 401:
            print("⚠️  Auth returned 401, falling back to anonymous access")
            response = await client.get(url)

        response.raise_for_status()
        return response

    async def _fetch_text_direct(self, endpoint: str) -> str:
        """Fetch text content directly from Jenkins (for logs and artifacts)"""
        async with httpx.AsyncClient(verify=self.ssl_verify, timeout=120.0) as client:
            url = f"{self.jenkins_url}/{endpoint.lstrip('/')}"
            response = await self._request(url, client)
            return response.text

    async def _fetch_bytes_direct(self, endpoint: str) -> bytes:
        """Fetch binary content directly from Jenkins (for images, videos)"""
        async with httpx.AsyncClient(verify=self.ssl_verify, timeout=120.0) as client:
            url = f"{self.jenkins_url}/{endpoint.lstrip('/')}"
            response = await self._request(url, client)
            return response.content

    async def get_artifact_bytes(self, job_path: str, build_number: int, artifact_path: str) -> bytes:
        """Fetch binary artifact content from a build"""
        path_parts = job_path.split("/")
        jenkins_path = "/".join([f"job/{part}" for part in path_parts])
        endpoint = f"{jenkins_path}/{build_number}/artifact/{artifact_path}"
        return await self._fetch_bytes_direct(endpoint)

    def _convert_job_path(self, job_path: str) -> str:
        """
        Convert job path to Jenkins API format
        Input: "odh/selfmanaged/cli/gcp/odh-tier2"
        Output: "job/odh/job/selfmanaged/job/cli/job/gcp/job/odh-tier2"
        """
        path_parts = job_path.split("/")
        return "job/" + "/job/".join(path_parts)

    async def get_job(self, job_path: str) -> Dict[str, Any]:
        """Get job details via direct HTTP API"""
        path_parts = job_path.split("/")
        jenkins_path = "/".join([f"job/{part}" for part in path_parts])
        endpoint = f"{jenkins_path}/api/json"

        async with httpx.AsyncClient(verify=self.ssl_verify, timeout=120.0) as client:
            url = f"{self.jenkins_url}/{endpoint}"
            response = await self._request(url, client)
            return response.json()

    async def get_build(self, job_path: str, build_number: Optional[int] = None) -> Dict[str, Any]:
        """
        Get build details via direct HTTP API

        job_path format: "components/dashboard/dashboard-e2e-tests/dash-e2e-odh"
        """
        path_parts = job_path.split("/")
        jenkins_path = "/".join([f"job/{part}" for part in path_parts])

        # If no build number specified, get latest
        if build_number is None:
            endpoint = f"{jenkins_path}/lastBuild/api/json"
        else:
            endpoint = f"{jenkins_path}/{build_number}/api/json"

        async with httpx.AsyncClient(verify=self.ssl_verify, timeout=120.0) as client:
            url = f"{self.jenkins_url}/{endpoint}"
            response = await self._request(url, client)
            return response.json()

    async def get_build_log(self, job_path: str, build_number: Optional[int] = None) -> str:
        """Get complete build log via direct HTTP API (consoleText endpoint)"""
        return await self.get_console_output(job_path, build_number)

    async def get_console_output(self, job_path: str, build_number: int) -> str:
        """Get console output for a build (direct access)"""
        path_parts = job_path.split("/")
        jenkins_path = "/".join([f"job/{part}" for part in path_parts])
        endpoint = f"{jenkins_path}/{build_number}/consoleText"
        return await self._fetch_text_direct(endpoint)

    async def get_artifact_content(self, job_path: str, build_number: int, artifact_path: str) -> str:
        """Fetch artifact content from a build (direct access)"""
        path_parts = job_path.split("/")
        jenkins_path = "/".join([f"job/{part}" for part in path_parts])
        endpoint = f"{jenkins_path}/{build_number}/artifact/{artifact_path}"
        return await self._fetch_text_direct(endpoint)

    async def list_artifacts(self, job_path: str, build_number: int) -> List[Dict[str, Any]]:
        """List all artifacts for a build"""
        build_data = await self.get_build(job_path, build_number)
        return build_data.get('artifacts', [])

    async def get_test_report(self, job_path: str, build_number: int) -> Optional[Dict[str, Any]]:
        """
        Get test report from Jenkins Test Report API

        This is a secondary evidence source when XML artifacts are not available.
        Returns aggregated test counts and failure details.

        Args:
            job_path: Jenkins job path (e.g., "rhoai/job/3.4/job/sanity")
            build_number: Build number

        Returns:
            Dict with test report data, or None if not available

        API format:
        {
            "failCount": 2,
            "skipCount": 1,
            "totalCount": 10,
            "suites": [
                {
                    "cases": [
                        {
                            "name": "test_name",
                            "className": "test_file",
                            "status": "FAILED|PASSED|SKIPPED",
                            "errorDetails": "...",
                            "errorStackTrace": "...",
                            "duration": 1.23
                        }
                    ]
                }
            ]
        }
        """
        job_api_path = self._convert_job_path(job_path)
        endpoint = f"{job_api_path}/{build_number}/testReport/api/json"

        try:
            async with httpx.AsyncClient(verify=self.ssl_verify, timeout=60.0) as client:
                url = f"{self.jenkins_url}/{endpoint.lstrip('/')}"
                print(f"      GET {url}")

                if ':' in self.jenkins_token:
                    username, token = self.jenkins_token.split(':', 1)
                    auth = (username, token)
                    response = await client.get(url, auth=auth)
                else:
                    response = await client.get(url, headers={'Authorization': f'Bearer {self.jenkins_token}'})

                print(f"      HTTP {response.status_code}")
                response.raise_for_status()
                data = response.json()
                print(f"      Found {data.get('totalCount', 0)} tests")
                return data

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # No test report available for this build
                print(f"   ⓘ No test report found at {endpoint}")
                return None
            print(f"   ⚠️  Test Report API error (HTTP {e.response.status_code}): {e}")
            return None
        except Exception as e:
            print(f"   ⚠️  Failed to fetch test report: {e}")
            return None

    async def find_nightly_builds(
        self,
        job_path: str,
        days_back: int = 1,
        job_pattern: str = None
    ) -> List[Dict[str, Any]]:
        """
        Find nightly builds from the last N days

        Args:
            job_path: The job path (e.g., "components/dashboard/dashboard-e2e-tests")
            days_back: Number of days to look back
            job_pattern: Optional pattern to filter job names (e.g., "dash-e2e")
        """
        job_data = await self.get_job(job_path)
        cutoff_time = datetime.now() - timedelta(days=days_back)

        nightly_builds = []

        for build in job_data.get('builds', []):
            build_number = build['number']
            build_details = await self.get_build(job_path, build_number)

            # Check timestamp
            build_time = datetime.fromtimestamp(build_details['timestamp'] / 1000)
            if build_time < cutoff_time:
                continue

            # Check if it's a nightly build (runs between 2-4 AM)
            if not (2 <= build_time.hour <= 4):
                continue

            # Check day of week (Mon-Fri = 0-4, Sunday = 6)
            if build_time.weekday() not in [0, 1, 2, 3, 4, 6]:
                continue

            # Apply job pattern filter if specified
            if job_pattern:
                job_name = build_details.get('fullDisplayName', '')
                if job_pattern not in job_name:
                    continue

            nightly_builds.append(build_details)

        return nightly_builds

    async def get_subjobs(self, job_path: str) -> List[Dict[str, Any]]:
        """Get all subjobs under a folder job"""
        job_data = await self.get_job(job_path)
        return job_data.get('jobs', [])

    async def find_latest_job_builds(
        self,
        parent_job_path: str,
        job_names: List[str],
        days_back: int = 1
    ) -> Dict[str, Dict[str, Any]]:
        """
        Find the latest builds for specific job names under a parent job

        Args:
            parent_job_path: Parent job path (e.g., "components/dashboard/dashboard-e2e-tests")
            job_names: List of job names to find (e.g., ["dash-e2e-rhoai", "dash-e2e-odh"])
            days_back: Number of days to look back

        Returns:
            Dict mapping job names to their latest build details
        """
        subjobs = await self.get_subjobs(parent_job_path)
        results = {}

        for subjob in subjobs:
            job_name = subjob['name']
            if job_name not in job_names:
                continue

            full_job_path = f"{parent_job_path}/{job_name}"

            try:
                # Get the latest build
                latest_build = await self.get_build(full_job_path)

                # Check if it's within the time window
                build_time = datetime.fromtimestamp(latest_build['timestamp'] / 1000)
                cutoff_time = datetime.now() - timedelta(days=days_back)

                if build_time >= cutoff_time:
                    # Check if it's a nightly build (2-4 AM)
                    if 2 <= build_time.hour <= 4:
                        results[job_name] = latest_build
            except Exception as e:
                print(f"Error fetching build for {full_job_path}: {e}")

        return results
