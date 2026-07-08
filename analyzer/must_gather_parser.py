"""
Must-Gather Archive Parser

Extracts diagnostic data from OpenShift must-gather archives for test failure analysis.

Must-gather structure:
    namespaces/<namespace>/pods/<pod>/logs/<container>.log
    namespaces/<namespace>/events
    cluster-scoped-resources/
    namespaces/<namespace>/<resource-type>/<resource-name>.yaml
"""
import tarfile
import json
import yaml
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MustGatherData:
    """Structured data extracted from must-gather archive"""

    # Pod logs by namespace
    pod_logs: Dict[str, Dict[str, str]] = field(default_factory=dict)

    # Events by namespace
    events: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    # Resource specs by namespace and type
    resources: Dict[str, Dict[str, List[Dict[str, Any]]]] = field(default_factory=dict)

    # Archive metadata
    archive_path: Optional[str] = None
    collection_time: Optional[str] = None
    cluster_version: Optional[str] = None

    # Warnings/errors during parsing
    warnings: List[str] = field(default_factory=list)


class MustGatherParser:
    """Parser for OpenShift must-gather archives"""

    def __init__(self, archive_path: str):
        """
        Initialize parser with path to must-gather archive

        Args:
            archive_path: Path to .tar.gz must-gather archive
        """
        self.archive_path = Path(archive_path)
        if not self.archive_path.exists():
            raise FileNotFoundError(f"Must-gather archive not found: {archive_path}")

    def parse(self, target_namespaces: Optional[List[str]] = None) -> MustGatherData:
        """
        Extract data from must-gather archive

        Args:
            target_namespaces: Optional list of namespaces to filter (None = all)

        Returns:
            MustGatherData with extracted information
        """
        data = MustGatherData(archive_path=str(self.archive_path))

        try:
            with tarfile.open(self.archive_path, 'r:*') as tar:
                # Get all members for processing
                members = tar.getmembers()

                # Find base directory (usually timestamped)
                base_dirs = [m.name.split('/')[0] for m in members if '/' in m.name]
                base_dir = base_dirs[0] if base_dirs else ''

                # Extract pod logs
                self._extract_pod_logs(tar, data, base_dir, target_namespaces)

                # Extract events
                self._extract_events(tar, data, base_dir, target_namespaces)

                # Extract resource specs
                self._extract_resources(tar, data, base_dir, target_namespaces)

                # Extract cluster info
                self._extract_cluster_info(tar, data, base_dir)

        except Exception as e:
            data.warnings.append(f"Error parsing archive: {e}")

        return data

    def _extract_pod_logs(
        self,
        tar: tarfile.TarFile,
        data: MustGatherData,
        base_dir: str,
        target_namespaces: Optional[List[str]]
    ):
        """Extract pod logs from namespaces/*/pods/*/logs/*.log"""
        pattern = re.compile(rf'{re.escape(base_dir)}/namespaces/([^/]+)/pods/([^/]+)/([^/]+)/logs/([^/]+)\.log')

        for member in tar.getmembers():
            if not member.isfile():
                continue

            match = pattern.match(member.name)
            if not match:
                continue

            namespace, pod_name, pod_id, container = match.groups()

            # Filter by target namespaces if specified
            if target_namespaces and namespace not in target_namespaces:
                continue

            try:
                content = tar.extractfile(member).read().decode('utf-8', errors='replace')

                # Store log content
                if namespace not in data.pod_logs:
                    data.pod_logs[namespace] = {}

                log_key = f"{pod_name}/{container}"
                data.pod_logs[namespace][log_key] = content

            except Exception as e:
                data.warnings.append(f"Failed to extract log {member.name}: {e}")

    def _extract_events(
        self,
        tar: tarfile.TarFile,
        data: MustGatherData,
        base_dir: str,
        target_namespaces: Optional[List[str]]
    ):
        """Extract events from namespaces/*/core/events.yaml"""
        pattern = re.compile(rf'{re.escape(base_dir)}/namespaces/([^/]+)/core/events\.yaml')

        for member in tar.getmembers():
            if not member.isfile():
                continue

            match = pattern.match(member.name)
            if not match:
                continue

            namespace = match.group(1)

            # Filter by target namespaces
            if target_namespaces and namespace not in target_namespaces:
                continue

            try:
                content = tar.extractfile(member).read().decode('utf-8')
                events_data = yaml.safe_load(content)

                if events_data and 'items' in events_data:
                    events = events_data['items']

                    # Filter for warnings and errors
                    important_events = [
                        e for e in events
                        if e.get('type') in ['Warning', 'Error'] or
                           e.get('reason') in ['Failed', 'BackOff', 'Unhealthy', 'FailedScheduling']
                    ]

                    if important_events:
                        data.events[namespace] = important_events

            except Exception as e:
                data.warnings.append(f"Failed to parse events for {namespace}: {e}")

    def _extract_resources(
        self,
        tar: tarfile.TarFile,
        data: MustGatherData,
        base_dir: str,
        target_namespaces: Optional[List[str]]
    ):
        """Extract resource specs (pods, deployments, etc.)"""
        # Common resource types to extract
        resource_types = ['pods', 'deployments', 'replicasets', 'statefulsets', 'daemonsets']

        for resource_type in resource_types:
            pattern = re.compile(rf'{re.escape(base_dir)}/namespaces/([^/]+)/[^/]+/{resource_type}/([^/]+)\.yaml')

            for member in tar.getmembers():
                if not member.isfile():
                    continue

                match = pattern.match(member.name)
                if not match:
                    continue

                namespace, resource_name = match.groups()

                # Filter by target namespaces
                if target_namespaces and namespace not in target_namespaces:
                    continue

                try:
                    content = tar.extractfile(member).read().decode('utf-8')
                    resource_spec = yaml.safe_load(content)

                    if namespace not in data.resources:
                        data.resources[namespace] = {}

                    if resource_type not in data.resources[namespace]:
                        data.resources[namespace][resource_type] = []

                    data.resources[namespace][resource_type].append(resource_spec)

                except Exception as e:
                    data.warnings.append(f"Failed to parse {resource_type}/{resource_name} in {namespace}: {e}")

    def _extract_cluster_info(
        self,
        tar: tarfile.TarFile,
        data: MustGatherData,
        base_dir: str
    ):
        """Extract cluster version and metadata"""
        # Try to find cluster version
        version_pattern = re.compile(rf'{re.escape(base_dir)}/cluster-scoped-resources/config\.openshift\.io/clusterversions/.*\.yaml')

        for member in tar.getmembers():
            if not member.isfile():
                continue

            if version_pattern.match(member.name):
                try:
                    content = tar.extractfile(member).read().decode('utf-8')
                    version_data = yaml.safe_load(content)

                    if version_data and 'status' in version_data:
                        desired = version_data['status'].get('desired', {})
                        data.cluster_version = desired.get('version', 'unknown')

                except Exception as e:
                    data.warnings.append(f"Failed to extract cluster version: {e}")
                break

    def get_pod_failures(self, data: MustGatherData) -> List[Dict[str, Any]]:
        """
        Analyze pod logs for failures

        Returns:
            List of failure events with context
        """
        failures = []

        for namespace, pods in data.pod_logs.items():
            for pod_container, log_content in pods.items():
                # Search for common error patterns
                error_patterns = [
                    (r'(ERROR|FATAL|CRITICAL)[:\s]+(.+)', 'error'),
                    (r'Exception[:\s]+(.+)', 'exception'),
                    (r'panic[:\s]+(.+)', 'panic'),
                    (r'failed[:\s]+(.+)', 'failure'),
                ]

                for pattern, failure_type in error_patterns:
                    matches = re.finditer(pattern, log_content, re.IGNORECASE | re.MULTILINE)

                    for match in matches:
                        # Get surrounding context
                        lines = log_content[:match.start()].split('\n')
                        line_num = len(lines)

                        context_start = max(0, line_num - 3)
                        context_end = min(len(log_content.split('\n')), line_num + 3)
                        context_lines = log_content.split('\n')[context_start:context_end]

                        failures.append({
                            'namespace': namespace,
                            'pod_container': pod_container,
                            'type': failure_type,
                            'message': match.group(0),
                            'line_number': line_num,
                            'context': '\n'.join(context_lines)
                        })

        return failures

    def get_failing_pods(self, data: MustGatherData) -> List[Dict[str, Any]]:
        """
        Get pods that are in failed/error state

        Returns:
            List of pods with failure information
        """
        failing_pods = []

        for namespace, resource_types in data.resources.items():
            pods = resource_types.get('pods', [])

            for pod in pods:
                status = pod.get('status', {})
                phase = status.get('phase', '')

                # Check for failed pod
                if phase in ['Failed', 'Unknown', 'CrashLoopBackOff']:
                    failing_pods.append({
                        'namespace': namespace,
                        'name': pod.get('metadata', {}).get('name', 'unknown'),
                        'phase': phase,
                        'reason': status.get('reason', ''),
                        'message': status.get('message', ''),
                        'container_statuses': status.get('containerStatuses', [])
                    })
                    continue

                # Check container statuses
                for container_status in status.get('containerStatuses', []):
                    state = container_status.get('state', {})

                    if 'waiting' in state:
                        waiting = state['waiting']
                        if waiting.get('reason') in ['CrashLoopBackOff', 'ImagePullBackOff', 'ErrImagePull']:
                            failing_pods.append({
                                'namespace': namespace,
                                'name': pod.get('metadata', {}).get('name', 'unknown'),
                                'container': container_status.get('name', 'unknown'),
                                'phase': phase,
                                'reason': waiting.get('reason', ''),
                                'message': waiting.get('message', '')
                            })

                    if 'terminated' in state:
                        terminated = state['terminated']
                        if terminated.get('exitCode', 0) != 0:
                            failing_pods.append({
                                'namespace': namespace,
                                'name': pod.get('metadata', {}).get('name', 'unknown'),
                                'container': container_status.get('name', 'unknown'),
                                'phase': phase,
                                'exit_code': terminated.get('exitCode'),
                                'reason': terminated.get('reason', ''),
                                'message': terminated.get('message', '')
                            })

        return failing_pods


def extract_must_gather(archive_path: str, namespaces: Optional[List[str]] = None) -> MustGatherData:
    """
    Convenience function to extract must-gather data

    Args:
        archive_path: Path to must-gather .tar.gz
        namespaces: Optional list of namespaces to filter

    Returns:
        MustGatherData with extracted information
    """
    parser = MustGatherParser(archive_path)
    return parser.parse(target_namespaces=namespaces)
