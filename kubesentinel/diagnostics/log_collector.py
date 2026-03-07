"""Kubernetes pod log collection for crashloop diagnostics."""

import logging
from typing import Optional
from kubernetes.client import CoreV1Api
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


def fetch_pod_logs(
    api_client: CoreV1Api,
    pod_name: str,
    namespace: str,
    container: Optional[str] = None,
    tail_lines: int = 100,
) -> Optional[str]:
    """
    Fetch logs from a crashed container in a pod.

    Uses previous=True to retrieve logs from the last terminated container instance,
    which is what we need for crashloop diagnostics.

    Args:
        api_client: Kubernetes CoreV1Api client instance
        pod_name: Name of the pod
        namespace: Namespace of the pod
        container: Container name (required for multi-container pods)
        tail_lines: Number of log lines to retrieve from the end

    Returns:
        Log text as string, or None if logs cannot be retrieved
    """
    try:
        # Fetch logs from the previous (crashed) container instance
        log_text = api_client.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            previous=True,  # Get logs from terminated container
            tail_lines=tail_lines,
        )
        logger.debug(
            f"Successfully fetched {len(log_text)} bytes of logs from "
            f"{namespace}/{pod_name}/{container or 'default'}"
        )
        return log_text
    except ApiException as e:
        # Common cases:
        # - 400: container not terminated yet (no previous logs)
        # - 404: pod/container not found
        # - 403: RBAC permissions issue
        if e.status == 400:
            logger.debug(
                f"No previous logs available for {namespace}/{pod_name}/{container or 'default'} "
                f"(container may not have crashed yet)"
            )
        elif e.status == 404:
            logger.warning(
                f"Pod or container not found: {namespace}/{pod_name}/{container or 'default'}"
            )
        elif e.status == 403:
            logger.warning(
                f"Permission denied fetching logs for {namespace}/{pod_name}/{container or 'default'} "
                f"(check RBAC permissions)"
            )
        else:
            logger.warning(
                f"Failed to fetch logs for {namespace}/{pod_name}/{container or 'default'}: "
                f"{e.status} {e.reason}"
            )
        return None
    except Exception as e:
        logger.error(
            f"Unexpected error fetching logs for {namespace}/{pod_name}/{container or 'default'}: {e}"
        )
        return None
