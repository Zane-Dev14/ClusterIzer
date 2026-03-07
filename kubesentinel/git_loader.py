import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

logger = logging.getLogger(__name__)

EXCLUDED_DIRS = {".git", "charts", "templates", ".helm"}
CLUSTER_SCOPED_KINDS = {
    "namespace",
    "clusterrole",
    "clusterrolebinding",
    "customresourcedefinition",
    "node",
    "persistentvolume",
    "storageclass",
    "mutatingwebhookconfiguration",
    "validatingwebhookconfiguration",
    "priorityclass",
    "runtimeclass",
    "certificatesigningrequest",
}

KIND_MAP: Dict[str, str] = {
    "deployment": "deployments",
    "statefulset": "statefulsets",
    "daemonset": "daemonsets",
    "service": "services",
    "pod": "pods",
    "configmap": "configmaps",
    "secret": "secrets",
    "ingress": "ingresses",
    "customresourcedefinition": "crds",
}

DESIRED_STATE_KEYS = [
    "deployments",
    "statefulsets",
    "daemonsets",
    "services",
    "pods",
    "configmaps",
    "secrets",
    "ingresses",
    "crds",
]


def load_git_repository(repo_url: str, branch: str, path: str) -> Path:
    """Resolve a repository path from local path or remote Git URL.

    For local paths, returns the resolved directory directly.
    For remote URLs, performs a shallow clone into `path` and returns the clone root.
    """
    candidate = Path(repo_url).expanduser()
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()

    if shutil.which("git") is None:
        raise RuntimeError("git executable not found in PATH")

    clone_root = Path(path).expanduser().resolve() / "repo"
    clone_root.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        branch,
        repo_url,
        str(clone_root),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"Failed to clone repository '{repo_url}': {stderr}") from exc

    return clone_root


def discover_manifests(root: Path) -> List[Path]:
    """Recursively discover YAML manifest files with deterministic ordering."""
    manifests: List[Path] = []

    for current_root, dirs, files in os.walk(root, topdown=True):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for filename in files:
            if filename.endswith((".yaml", ".yml")):
                manifests.append(Path(current_root) / filename)

    return sorted(manifests, key=lambda p: str(p.relative_to(root)))


def parse_manifests(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    """Parse YAML manifests and return normalized resources.

    Duplicate resources are resolved deterministically with last-manifest-wins behavior.
    """
    normalized_by_key: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for manifest_path in paths:
        text = manifest_path.read_text(encoding="utf-8")
        for doc in yaml.safe_load_all(text):
            if not isinstance(doc, dict):
                continue

            kind = doc.get("kind")
            metadata = doc.get("metadata")
            if not kind or not isinstance(metadata, dict):
                continue
            if not metadata.get("name"):
                continue

            normalized = normalize_resource(doc)
            key = _resource_identity(normalized)
            normalized_by_key[key] = normalized

    resources = list(normalized_by_key.values())
    resources.sort(key=_resource_identity)
    return resources


def normalize_resource(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Kubernetes manifest object into a deterministic desired-state shape."""
    kind = str(obj.get("kind", "")).strip().lower()
    metadata_raw = obj.get("metadata")
    metadata: Dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
    name = str(metadata.get("name", "")).strip()

    labels = metadata.get("labels") if isinstance(metadata.get("labels"), dict) else {}
    annotations = (
        metadata.get("annotations")
        if isinstance(metadata.get("annotations"), dict)
        else {}
    )
    spec = obj.get("spec") if isinstance(obj.get("spec"), dict) else {}

    namespace = metadata.get("namespace")
    if not namespace:
        namespace = "_cluster" if kind in CLUSTER_SCOPED_KINDS else "default"

    return {
        "kind": kind,
        "name": name,
        "namespace": str(namespace),
        "labels": labels,
        "annotations": annotations,
        "spec": spec,
    }


def classify_resources(resources: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Classify normalized resources into the fixed 9-key desired-state schema."""
    classified: Dict[str, List[Dict[str, Any]]] = {key: [] for key in DESIRED_STATE_KEYS}

    for resource in resources:
        bucket = KIND_MAP.get(resource.get("kind", ""), "crds")
        classified[bucket].append(resource)

    for key in DESIRED_STATE_KEYS:
        classified[key].sort(key=_resource_identity)

    return classified


def load_git_desired_state(
    repo_url: str | None,
    local_path: str | None,
    branch: str = "main",
) -> Dict[str, List[Dict[str, Any]]]:
    """Load desired state from Git URL or local path into fixed schema.

    Exactly one of `repo_url` or `local_path` must be provided.
    """
    if bool(repo_url) == bool(local_path):
        raise ValueError("Provide exactly one of repo_url or local_path")

    if local_path:
        root = Path(local_path).expanduser().resolve()
        manifests = discover_manifests(root)
        resources = parse_manifests(manifests)
        return classify_resources(resources)

    assert repo_url is not None
    local_candidate = Path(repo_url).expanduser()
    if local_candidate.exists() and local_candidate.is_dir():
        manifests = discover_manifests(local_candidate.resolve())
        resources = parse_manifests(manifests)
        return classify_resources(resources)

    with tempfile.TemporaryDirectory(prefix="kubesentinel-git-") as temp_dir:
        root = load_git_repository(repo_url=repo_url, branch=branch, path=temp_dir)
        manifests = discover_manifests(root)
        resources = parse_manifests(manifests)
        return classify_resources(resources)


def _resource_identity(resource: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(resource.get("kind", "")).lower(),
        str(resource.get("namespace", "default")),
        str(resource.get("name", "")),
    )
