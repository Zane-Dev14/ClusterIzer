"""Heuristic Rule Engine — deterministic audit checks.

Each rule is a pure function:  ``(snapshot, graph=None) -> list[Finding]``

Wave 1 (v1) ships four high-signal rules that prove the full pipeline:
  1. missing_requests   — reliability / high
  2. single_replica     — reliability / high
  3. image_latest       — security   / medium
  4. wildcard_rbac      — security   / critical

Wave 2 adds the remaining eight rules (see ``# --- Wave 2 ---`` section).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.models import (
    Category,
    Evidence,
    Finding,
    RemediationDetail,
    Severity,
)

logger = logging.getLogger("clustergpt.rules")

# Type alias for the graph (optional NetworkX DiGraph)
Graph = Any


# ===================================================================
# Helpers
# ===================================================================

def _containers(deployment: dict) -> list[dict]:
    """Return the list of container specs from a deployment dict."""
    return (
        deployment
        .get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )


def _deploy_key(dep: dict) -> tuple[str, str]:
    """Return (namespace, name) for a deployment."""
    meta = dep.get("metadata", {})
    return meta.get("namespace", "default"), meta.get("name", "unknown")


def _evidence_for_deploy(dep: dict, extra_pointer: str = "") -> Evidence:
    ns, name = _deploy_key(dep)
    uid = dep.get("metadata", {}).get("uid", "")
    pointer = extra_pointer or f"kubectl get deployment {name} -n {ns} -o yaml"
    return Evidence(
        kind="Deployment",
        namespace=ns,
        name=name,
        timestamp=dep.get("metadata", {}).get("creationTimestamp", ""),
        pointer=pointer,
    )


# ===================================================================
# Rule 1 — missing_requests  (reliability / high)
# ===================================================================

def check_missing_requests(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag deployments where ≥1 container has no ``resources.requests``."""
    findings: list[Finding] = []
    for dep in snapshot.get("deployments", []):
        ns, name = _deploy_key(dep)
        for ctr in _containers(dep):
            requests = ctr.get("resources", {}).get("requests")
            if not requests:
                ctr_name = ctr.get("name", "?")
                findings.append(Finding(
                    id=f"missing_requests:{ns}/{name}/{ctr_name}",
                    category=Category.reliability,
                    severity=Severity.high,
                    summary=(
                        f"Container '{ctr_name}' in Deployment {ns}/{name} "
                        f"has no resource requests. The scheduler cannot make "
                        f"informed placement decisions."
                    ),
                    evidence=[_evidence_for_deploy(dep)],
                    remediation=RemediationDetail(
                        description=f"Add CPU and memory requests to container '{ctr_name}'.",
                        kubectl=[
                            f"kubectl set resources deployment/{name} -n {ns} "
                            f"-c {ctr_name} --requests=cpu=100m,memory=128Mi",
                        ],
                        patch_yaml=(
                            f"# Add to deployment {ns}/{name}, container {ctr_name}\n"
                            f"resources:\n"
                            f"  requests:\n"
                            f"    cpu: \"100m\"\n"
                            f"    memory: \"128Mi\""
                        ),
                    ),
                ))
    return findings


# ===================================================================
# Rule 2 — single_replica  (reliability / high)
# ===================================================================

def check_single_replica(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag deployments with ``spec.replicas == 1`` (no HA)."""
    findings: list[Finding] = []
    for dep in snapshot.get("deployments", []):
        replicas = dep.get("spec", {}).get("replicas", 1)
        if replicas == 1:
            ns, name = _deploy_key(dep)
            findings.append(Finding(
                id=f"single_replica:{ns}/{name}",
                category=Category.reliability,
                severity=Severity.high,
                summary=(
                    f"Deployment {ns}/{name} has only 1 replica. "
                    f"A single pod failure causes full downtime."
                ),
                evidence=[_evidence_for_deploy(dep)],
                remediation=RemediationDetail(
                    description=f"Scale {name} to at least 2 replicas for basic HA.",
                    kubectl=[
                        f"kubectl scale deployment/{name} -n {ns} --replicas=2",
                    ],
                    patch_yaml=(
                        f"# Patch deployment {ns}/{name}\n"
                        f"spec:\n"
                        f"  replicas: 2"
                    ),
                ),
            ))
    return findings


# ===================================================================
# Rule 3 — image_latest  (security / medium)
# ===================================================================

def _is_latest_tag(image: str) -> bool:
    """Return True if the image uses :latest or has no explicit tag."""
    if ":" not in image.split("/")[-1]:
        return True  # no tag at all → defaults to :latest
    return image.endswith(":latest")


def check_image_latest(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag containers using ``:latest`` or untagged images."""
    findings: list[Finding] = []
    for dep in snapshot.get("deployments", []):
        ns, name = _deploy_key(dep)
        for ctr in _containers(dep):
            image = ctr.get("image", "")
            if _is_latest_tag(image):
                ctr_name = ctr.get("name", "?")
                findings.append(Finding(
                    id=f"image_latest:{ns}/{name}/{ctr_name}",
                    category=Category.security,
                    severity=Severity.medium,
                    summary=(
                        f"Container '{ctr_name}' in {ns}/{name} uses image "
                        f"'{image}' (latest / untagged). This is "
                        f"non-reproducible and a security risk."
                    ),
                    evidence=[_evidence_for_deploy(
                        dep,
                        extra_pointer=f"Image: {image}",
                    )],
                    remediation=RemediationDetail(
                        description=(
                            f"Pin '{ctr_name}' to an immutable digest or "
                            f"semantic version tag."
                        ),
                        kubectl=[
                            f"kubectl set image deployment/{name} -n {ns} "
                            f"{ctr_name}={image.split(':')[0]}:<specific-tag>",
                        ],
                        patch_yaml="",
                    ),
                ))
    return findings


# ===================================================================
# Rule 4 — wildcard_rbac  (security / critical)
# ===================================================================

def check_wildcard_rbac(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag ClusterRoles with wildcard ``*`` in verbs or resources."""
    findings: list[Finding] = []
    for role in snapshot.get("rbac_roles", []):
        role_name = role.get("metadata", {}).get("name", "unknown")
        for rule_item in role.get("rules", []):
            verbs = rule_item.get("verbs", [])
            resources = rule_item.get("resources", [])
            api_groups = rule_item.get("apiGroups", [])
            has_wildcard = "*" in verbs or "*" in resources
            if has_wildcard:
                detail_parts: list[str] = []
                if "*" in verbs:
                    detail_parts.append("verbs=[*]")
                if "*" in resources:
                    detail_parts.append("resources=[*]")
                findings.append(Finding(
                    id=f"wildcard_rbac:cluster/{role_name}",
                    category=Category.security,
                    severity=Severity.critical,
                    summary=(
                        f"ClusterRole '{role_name}' grants wildcard "
                        f"permissions ({', '.join(detail_parts)}). "
                        f"This violates the principle of least privilege."
                    ),
                    evidence=[Evidence(
                        kind="RBAC",
                        namespace="",
                        name=role_name,
                        timestamp=role.get("metadata", {}).get("creationTimestamp", ""),
                        pointer=f"kubectl get clusterrole {role_name} -o yaml",
                    )],
                    remediation=RemediationDetail(
                        description=(
                            f"Replace wildcard grants in ClusterRole '{role_name}' "
                            f"with specific verbs and resources."
                        ),
                        kubectl=[
                            f"kubectl get clusterrole {role_name} -o yaml > "
                            f"clusterrole-{role_name}-backup.yaml",
                        ],
                        patch_yaml="",
                    ),
                ))
                break  # one finding per role is enough
    return findings


# ===================================================================
# Rule 5 — missing_limits  (reliability / high)
# ===================================================================

def check_missing_limits(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag deployments where ≥1 container has no ``resources.limits``."""
    findings: list[Finding] = []
    for dep in snapshot.get("deployments", []):
        ns, name = _deploy_key(dep)
        for ctr in _containers(dep):
            limits = ctr.get("resources", {}).get("limits")
            if not limits:
                ctr_name = ctr.get("name", "?")
                findings.append(Finding(
                    id=f"missing_limits:{ns}/{name}/{ctr_name}",
                    category=Category.reliability,
                    severity=Severity.high,
                    summary=(
                        f"Container '{ctr_name}' in Deployment {ns}/{name} "
                        f"has no resource limits. It can consume unbounded "
                        f"CPU/memory and starve neighbours."
                    ),
                    evidence=[_evidence_for_deploy(dep)],
                    remediation=RemediationDetail(
                        description=f"Add CPU and memory limits to container '{ctr_name}'.",
                        kubectl=[
                            f"kubectl set resources deployment/{name} -n {ns} "
                            f"-c {ctr_name} --limits=cpu=500m,memory=512Mi",
                        ],
                        patch_yaml=(
                            f"# Add to deployment {ns}/{name}, container {ctr_name}\n"
                            f"resources:\n"
                            f"  limits:\n"
                            f"    cpu: \"500m\"\n"
                            f"    memory: \"512Mi\""
                        ),
                    ),
                ))
    return findings


# ===================================================================
# Rule 6 — missing_readiness_probe  (reliability / medium)
# ===================================================================

def check_missing_readiness_probe(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag containers with no ``readinessProbe``."""
    findings: list[Finding] = []
    for dep in snapshot.get("deployments", []):
        ns, name = _deploy_key(dep)
        for ctr in _containers(dep):
            if not ctr.get("readinessProbe"):
                ctr_name = ctr.get("name", "?")
                findings.append(Finding(
                    id=f"missing_readiness_probe:{ns}/{name}/{ctr_name}",
                    category=Category.reliability,
                    severity=Severity.medium,
                    summary=(
                        f"Container '{ctr_name}' in {ns}/{name} has no "
                        f"readinessProbe. Kubernetes cannot know when the "
                        f"pod is ready to serve traffic."
                    ),
                    evidence=[_evidence_for_deploy(dep)],
                    remediation=RemediationDetail(
                        description=f"Add a readinessProbe to container '{ctr_name}'.",
                        kubectl=[
                            f"kubectl edit deployment/{name} -n {ns}",
                        ],
                        patch_yaml=(
                            f"# Add to container {ctr_name} in {ns}/{name}\n"
                            f"readinessProbe:\n"
                            f"  httpGet:\n"
                            f"    path: /healthz\n"
                            f"    port: 8080\n"
                            f"  initialDelaySeconds: 5\n"
                            f"  periodSeconds: 10"
                        ),
                    ),
                ))
    return findings


# ===================================================================
# Rule 7 — missing_liveness_probe  (reliability / medium)
# ===================================================================

def check_missing_liveness_probe(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag containers with no ``livenessProbe``."""
    findings: list[Finding] = []
    for dep in snapshot.get("deployments", []):
        ns, name = _deploy_key(dep)
        for ctr in _containers(dep):
            if not ctr.get("livenessProbe"):
                ctr_name = ctr.get("name", "?")
                findings.append(Finding(
                    id=f"missing_liveness_probe:{ns}/{name}/{ctr_name}",
                    category=Category.reliability,
                    severity=Severity.medium,
                    summary=(
                        f"Container '{ctr_name}' in {ns}/{name} has no "
                        f"livenessProbe. Kubernetes cannot auto-restart the "
                        f"container if it becomes unresponsive."
                    ),
                    evidence=[_evidence_for_deploy(dep)],
                    remediation=RemediationDetail(
                        description=f"Add a livenessProbe to container '{ctr_name}'.",
                        kubectl=[
                            f"kubectl edit deployment/{name} -n {ns}",
                        ],
                        patch_yaml=(
                            f"# Add to container {ctr_name} in {ns}/{name}\n"
                            f"livenessProbe:\n"
                            f"  httpGet:\n"
                            f"    path: /healthz\n"
                            f"    port: 8080\n"
                            f"  initialDelaySeconds: 15\n"
                            f"  periodSeconds: 20"
                        ),
                    ),
                ))
    return findings


# ===================================================================
# Rule 8 — privileged_container  (security / critical)
# ===================================================================

def check_privileged_container(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag containers running in privileged mode."""
    findings: list[Finding] = []
    for dep in snapshot.get("deployments", []):
        ns, name = _deploy_key(dep)
        for ctr in _containers(dep):
            sc = ctr.get("securityContext", {}) or {}
            if sc.get("privileged") is True:
                ctr_name = ctr.get("name", "?")
                findings.append(Finding(
                    id=f"privileged_container:{ns}/{name}/{ctr_name}",
                    category=Category.security,
                    severity=Severity.critical,
                    summary=(
                        f"Container '{ctr_name}' in {ns}/{name} runs in "
                        f"privileged mode. This grants full host access "
                        f"and is a severe security risk."
                    ),
                    evidence=[_evidence_for_deploy(dep)],
                    remediation=RemediationDetail(
                        description=(
                            f"Remove privileged mode from container '{ctr_name}'. "
                            f"Use specific capabilities instead."
                        ),
                        kubectl=[
                            f"kubectl patch deployment {name} -n {ns} "
                            f"--type=json -p='[{{\"op\":\"replace\","
                            f"\"path\":\"/spec/template/spec/containers/0/"
                            f"securityContext/privileged\",\"value\":false}}]'",
                        ],
                        patch_yaml=(
                            f"# Patch container {ctr_name} in {ns}/{name}\n"
                            f"securityContext:\n"
                            f"  privileged: false\n"
                            f"  runAsNonRoot: true"
                        ),
                    ),
                ))
    return findings


# ===================================================================
# Rule 9 — no_network_policy  (security / medium)
# ===================================================================

def check_no_network_policy(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag namespaces that contain pods but have no NetworkPolicy."""
    findings: list[Finding] = []

    # Build set of namespaces that have NetworkPolicies.
    ns_with_policy: set[str] = set()
    for np_obj in snapshot.get("networkpolicies", []):
        ns_with_policy.add(np_obj.get("metadata", {}).get("namespace", ""))

    # Build set of namespaces that have pods.
    ns_with_pods: set[str] = set()
    for pod in snapshot.get("pods", []):
        ns_with_pods.add(pod.get("metadata", {}).get("namespace", "default"))

    # Also count deploy namespaces (pods might not be in snapshot yet).
    for dep in snapshot.get("deployments", []):
        ns_with_pods.add(dep.get("metadata", {}).get("namespace", "default"))

    # Skip system namespaces.
    skip_ns = {"kube-system", "kube-public", "kube-node-lease"}

    for ns in ns_with_pods - ns_with_policy - skip_ns:
        findings.append(Finding(
            id=f"no_network_policy:{ns}",
            category=Category.security,
            severity=Severity.medium,
            summary=(
                f"Namespace '{ns}' has workloads but no NetworkPolicy. "
                f"All pod-to-pod traffic is unrestricted."
            ),
            evidence=[Evidence(
                kind="Namespace",
                namespace=ns,
                name=ns,
                pointer=f"kubectl get networkpolicy -n {ns}",
            )],
            remediation=RemediationDetail(
                description=(
                    f"Create a default-deny NetworkPolicy in namespace '{ns}' "
                    f"and then allow required traffic explicitly."
                ),
                kubectl=[
                    f"kubectl apply -f - <<EOF\n"
                    f"apiVersion: networking.k8s.io/v1\n"
                    f"kind: NetworkPolicy\n"
                    f"metadata:\n"
                    f"  name: default-deny\n"
                    f"  namespace: {ns}\n"
                    f"spec:\n"
                    f"  podSelector: {{}}\n"
                    f"  policyTypes: [Ingress, Egress]\n"
                    f"EOF",
                ],
                patch_yaml="",
            ),
        ))
    return findings


# ===================================================================
# Rule 10 — overprovision  (cost / medium)
# ===================================================================

def _parse_cpu(value: str | int | float | None) -> float:
    """Convert K8s CPU quantity to cores."""
    if value is None:
        return 0.0
    s = str(value).strip()
    if s.endswith("m"):
        return float(s[:-1]) / 1000.0
    return float(s)


def _parse_memory_gb(value: str | int | float | None) -> float:
    """Convert K8s memory quantity to GB."""
    if value is None:
        return 0.0
    s = str(value).strip()
    multipliers = {"Ki": 1 / 1024**2, "Mi": 1 / 1024, "Gi": 1.0, "Ti": 1024.0}
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mult
    try:
        return float(s) / (1024**3)
    except ValueError:
        return 0.0


def check_overprovision(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag deployments requesting > 50% of any single node's allocatable CPU."""
    findings: list[Finding] = []

    # Find largest node allocatable CPU.
    max_node_cpu = 0.0
    for node in snapshot.get("nodes", []):
        alloc = node.get("status", {}).get("allocatable", {})
        max_node_cpu = max(max_node_cpu, _parse_cpu(alloc.get("cpu")))

    if max_node_cpu == 0:
        return findings  # can't determine without node data

    for dep in snapshot.get("deployments", []):
        ns, name = _deploy_key(dep)
        total_cpu = sum(
            _parse_cpu(ctr.get("resources", {}).get("requests", {}).get("cpu"))
            for ctr in _containers(dep)
        )
        if total_cpu > max_node_cpu * 0.5:
            findings.append(Finding(
                id=f"overprovision:{ns}/{name}",
                category=Category.cost,
                severity=Severity.medium,
                summary=(
                    f"Deployment {ns}/{name} requests {total_cpu:.2f} CPU "
                    f"per pod — over 50% of the largest node "
                    f"({max_node_cpu:.2f} allocatable). "
                    f"This limits scheduling flexibility."
                ),
                evidence=[_evidence_for_deploy(dep)],
                remediation=RemediationDetail(
                    description="Reduce CPU requests or consider vertical pod autoscaling.",
                    kubectl=[
                        f"kubectl set resources deployment/{name} -n {ns} "
                        f"--requests=cpu={int(max_node_cpu * 250)}m",
                    ],
                    patch_yaml="",
                ),
            ))
    return findings


# ===================================================================
# Rule 11 — hpa_missing_high_cpu  (cost / medium)
# ===================================================================

def check_hpa_missing_high_cpu(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag deployments with ≥500m CPU request and no HPA targeting them."""
    findings: list[Finding] = []

    # Build set of deployment names targeted by HPAs.
    hpa_targets: set[str] = set()
    for hpa in snapshot.get("hpa", []):
        ref = hpa.get("spec", {}).get("scaleTargetRef", {})
        if ref.get("kind") == "Deployment":
            ns = hpa.get("metadata", {}).get("namespace", "default")
            hpa_targets.add(f"{ns}/{ref.get('name', '')}")

    for dep in snapshot.get("deployments", []):
        ns, name = _deploy_key(dep)
        total_cpu = sum(
            _parse_cpu(ctr.get("resources", {}).get("requests", {}).get("cpu"))
            for ctr in _containers(dep)
        )
        if total_cpu >= 0.5 and f"{ns}/{name}" not in hpa_targets:
            findings.append(Finding(
                id=f"hpa_missing_high_cpu:{ns}/{name}",
                category=Category.cost,
                severity=Severity.medium,
                summary=(
                    f"Deployment {ns}/{name} requests {total_cpu:.2f} CPU "
                    f"but has no HPA. It cannot scale with demand, "
                    f"leading to waste or under-capacity."
                ),
                evidence=[_evidence_for_deploy(dep)],
                remediation=RemediationDetail(
                    description=f"Create an HPA for {name} targeting 70% CPU utilisation.",
                    kubectl=[
                        f"kubectl autoscale deployment/{name} -n {ns} "
                        f"--min=2 --max=10 --cpu-percent=70",
                    ],
                    patch_yaml="",
                ),
            ))
    return findings


# ===================================================================
# Rule 12 — pvc_not_bound  (reliability / high)
# ===================================================================

def check_pvc_not_bound(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Flag PersistentVolumeClaims stuck in Pending phase."""
    findings: list[Finding] = []

    # PVCs may appear inside pods or as a top-level snapshot key.
    # Check pods for volume references and also check a dedicated 'pvcs' key.
    pvcs: list[dict] = snapshot.get("pvcs", [])

    # Also scan pod volumes for PVC references (if pvcs not separately collected).
    if not pvcs:
        seen: set[str] = set()
        for pod in snapshot.get("pods", []):
            pod_ns = pod.get("metadata", {}).get("namespace", "default")
            for vol in pod.get("spec", {}).get("volumes", []):
                pvc_ref = vol.get("persistentVolumeClaim", {})
                claim_name = pvc_ref.get("claimName", "")
                if claim_name and f"{pod_ns}/{claim_name}" not in seen:
                    seen.add(f"{pod_ns}/{claim_name}")
                    # We don't have PVC status from pod volumes alone,
                    # so skip — this rule is best served by a dedicated PVC list.

    for pvc in pvcs:
        phase = pvc.get("status", {}).get("phase", "")
        if phase in ("Pending", "Lost"):
            meta = pvc.get("metadata", {})
            ns = meta.get("namespace", "default")
            name = meta.get("name", "unknown")
            findings.append(Finding(
                id=f"pvc_not_bound:{ns}/{name}",
                category=Category.reliability,
                severity=Severity.high,
                summary=(
                    f"PVC {ns}/{name} is in '{phase}' phase. "
                    f"Pods depending on it may be stuck."
                ),
                evidence=[Evidence(
                    kind="PVC",
                    namespace=ns,
                    name=name,
                    timestamp=meta.get("creationTimestamp", ""),
                    pointer=f"kubectl describe pvc {name} -n {ns}",
                )],
                remediation=RemediationDetail(
                    description=(
                        f"Check StorageClass availability and PV provisioning "
                        f"for PVC '{name}'."
                    ),
                    kubectl=[
                        f"kubectl describe pvc {name} -n {ns}",
                        f"kubectl get sc",
                    ],
                    patch_yaml="",
                ),
            ))
    return findings


# ===================================================================
# Orchestrator
# ===================================================================

# Registry: add rules here as they are implemented.
_ALL_RULES = [
    # Wave 1
    check_missing_requests,
    check_single_replica,
    check_image_latest,
    check_wildcard_rbac,
    # Wave 2 (stubs — will be filled in)
    check_missing_limits,
    check_missing_readiness_probe,
    check_missing_liveness_probe,
    check_privileged_container,
    check_no_network_policy,
    check_overprovision,
    check_hpa_missing_high_cpu,
    check_pvc_not_bound,
]

_SEVERITY_ORDER = {
    Severity.critical: 0,
    Severity.high: 1,
    Severity.medium: 2,
    Severity.low: 3,
}


def run_all_rules(
    snapshot: dict[str, Any],
    graph: Graph = None,
) -> list[Finding]:
    """Execute every registered rule and return findings sorted by severity."""
    findings: list[Finding] = []
    for rule_fn in _ALL_RULES:
        try:
            findings.extend(rule_fn(snapshot, graph))
        except Exception as exc:
            logger.warning("Rule %s raised: %s", rule_fn.__name__, exc)
    findings.sort(key=lambda f: _SEVERITY_ORDER.get(f.severity, 99))
    return findings
