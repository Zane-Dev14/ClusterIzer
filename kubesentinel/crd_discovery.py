"""
CRD (Custom Resource Definition) discovery for KubeSentinel.

Discovers and extracts custom Kubernetes resources like ArgoCD Applications,
Istio resources, Prometheus CRDs, KEDA ScaledObjects, and CertManager resources.
"""

import logging
from typing import Dict, Any, List, Tuple
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

# Known CRD resources to discover
KNOWN_CRDS = {
    "argoproj.io": {
        "v1alpha1": ["Application", "AppProject"]
    },
    "networking.istio.io": {
        "v1beta1": ["VirtualService", "DestinationRule", "Gateway", "ServiceEntry"]
    },
    "security.istio.io": {
        "v1beta1": ["AuthorizationPolicy", "PeerAuthentication"]
    },
    "monitoring.coreos.com": {
        "v1": ["PrometheusRule", "ServiceMonitor", "AlertmanagerConfig"]
    },
    "keda.sh": {
        "v1alpha1": ["ScaledObject", "ScaledJob", "TriggerAuthentication"]
    },
    "cert-manager.io": {
        "v1": ["Certificate", "ClusterIssuer", "Issuer"]
    }
}

MAX_CRDS = 500  # Hard cap for CRD resources


def discover_crds(target_namespace: str = None) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """
    Discover custom resources in the cluster.
    
    Args:
        target_namespace: Optional namespace to filter CRDs. If None, discovers all namespaces.
    
    Returns:
        Tuple of (crds_dict, errors)
        - crds_dict: {crd_group/version/kind: [resources]}
        - errors: List of error messages during discovery
    """
    logger.info("Starting CRD discovery...")
    crds = {}
    errors = []
    
    try:
        api = client.CustomObjectsApi()
        count = 0
        
        # Iterate through known CRDs
        for group, versions in KNOWN_CRDS.items():
            for version, kinds in versions.items():
                for kind in kinds:
                    try:
                        resources = _fetch_custom_resources(
                            api, group, version, kind, target_namespace
                        )
                        if resources:
                            crd_key = f"{group}/{version}/{kind}"
                            crds[crd_key] = resources
                            count += len(resources)
                            
                            # Enforce cap
                            if count >= MAX_CRDS:
                                logger.warning(f"CRD limit reached ({MAX_CRDS}); stopping discovery")
                                return crds, errors
                                
                    except ApiException as e:
                        # Expected for clusters without specific CRD installed
                        if e.status != 404:  # Not a "not found" error
                            error_msg = f"Failed to fetch {group}/{version}/{kind}: {e.reason}"
                            logger.debug(error_msg)
                            errors.append(error_msg)
                    except Exception as e:
                        error_msg = f"Error discovering {group}/{version}/{kind}: {str(e)}"
                        logger.debug(error_msg)
                        errors.append(error_msg)
        
        logger.info(f"CRD discovery complete: {count} resources found in {len(crds)} CRD groups")
        
    except Exception as e:
        error_msg = f"CRD discovery failed: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
    
    return crds, errors


def _fetch_custom_resources(
    api: client.CustomObjectsApi,
    group: str,
    version: str,
    kind: str,
    namespace: str = None
) -> List[Dict[str, Any]]:
    """
    Fetch custom resources of a specific kind.
    
    Args:
        api: CustomObjectsApi instance
        group: API group (e.g., "argoproj.io")
        version: API version (e.g., "v1alpha1")
        kind: Resource kind (e.g., "Application")
        namespace: Optional namespace filter
    
    Returns:
        List of extracted custom resources
    """
    resources = []
    
    try:
        # Determine plural form (rough heuristic)
        plural = _get_plural_form(kind)
        
        if namespace:
            items = api.list_namespaced_custom_object(
                group=group,
                version=version,
                namespace=namespace,
                plural=plural,
                limit=MAX_CRDS
            ).get("items", [])
        else:
            items = api.list_cluster_custom_object(
                group=group,
                version=version,
                plural=plural,
                limit=MAX_CRDS
            ).get("items", [])
        
        for item in items[:MAX_CRDS]:
            resources.append(_extract_crd_resource(item, kind))
        
    except ApiException as e:
        # Re-raise to let caller handle
        raise
    
    return resources


def _get_plural_form(kind: str) -> str:
    """
    Get the plural form of a Kubernetes resource kind.
    
    This is a heuristic - for known resources, returns the correct plural.
    For unknown resources, applies simple pluralization rules.
    """
    plurals = {
        "Application": "applications",
        "AppProject": "appprojects",
        "VirtualService": "virtualservices",
        "DestinationRule": "destinationrules",
        "Gateway": "gateways",
        "ServiceEntry": "serviceentries",
        "AuthorizationPolicy": "authorizationpolicies",
        "PeerAuthentication": "peerauthentications",
        "PrometheusRule": "prometheusrules",
        "ServiceMonitor": "servicemonitors",
        "AlertmanagerConfig": "alertmanagerconfigs",
        "ScaledObject": "scaledobjects",
        "ScaledJob": "scaledjobs",
        "TriggerAuthentication": "triggerauthentications",
        "Certificate": "certificates",
        "ClusterIssuer": "clusterissuers",
        "Issuer": "issuers",
    }
    
    return plurals.get(kind, _simple_pluralize(kind))


def _simple_pluralize(word: str) -> str:
    """Simple pluralization heuristic."""
    if word.endswith("y"):
        return word[:-1] + "ies"
    elif word.endswith("s"):
        return word + "es"
    else:
        return word + "s"


def _extract_crd_resource(item: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """
    Extract relevant information from a CRD resource.
    
    Args:
        item: Raw CRD resource from Kubernetes API
        kind: Resource kind
    
    Returns:
        Extracted resource information
    """
    metadata = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    
    # Extract owner references for graph building
    owner_refs = []
    if metadata.get("ownerReferences"):
        for owner in metadata["ownerReferences"]:
            owner_refs.append({
                "kind": owner.get("kind"),
                "name": owner.get("name"),
                "uid": owner.get("uid"),
                "controller": owner.get("controller", False)
            })
    
    # Kind-specific extraction
    additional_fields = _extract_kind_specific_fields(kind, spec, status)
    
    resource = {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace", "cluster-scoped"),
        "uid": metadata.get("uid"),
        "kind": kind,
        "labels": dict(metadata.get("labels", {})),
        "owner_references": owner_refs,
        "creation_timestamp": metadata.get("creationTimestamp"),
        "deletion_timestamp": metadata.get("deletionTimestamp"),
    }
    
    # Add kind-specific fields
    resource.update(additional_fields)
    
    return resource


def _extract_kind_specific_fields(kind: str, spec: Dict[str, Any], status: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract kind-specific fields for CRD resources.
    
    Args:
        kind: Resource kind
        spec: Resource spec section
        status: Resource status section
    
    Returns:
        Additional fields to include in extracted resource
    """
    fields = {}
    
    # ArgoCD Application
    if kind == "Application":
        fields["repo"] = spec.get("source", {}).get("repoURL")
        fields["target_revision"] = spec.get("source", {}).get("targetRevision")
        fields["destination"] = spec.get("destination", {}).get("server")
        fields["sync_status"] = status.get("sync", {}).get("status", "Unknown")
        fields["health_status"] = status.get("health", {}).get("status", "Unknown")
    
    # Istio VirtualService
    elif kind == "VirtualService":
        fields["hosts"] = spec.get("hosts", [])
        fields["gateways"] = spec.get("gateways", [])
        routes = spec.get("http", [])
        fields["route_count"] = len(routes)
    
    # Istio DestinationRule
    elif kind == "DestinationRule":
        fields["host"] = spec.get("host")
        fields["traffic_policy"] = spec.get("trafficPolicy", {}).get("connectionPool")
    
    # Istio Gateway
    elif kind == "Gateway":
        fields["servers"] = spec.get("servers", [])
        fields["selector"] = spec.get("selector", {})
    
    # Prometheus PrometheusRule
    elif kind == "PrometheusRule":
        groups = spec.get("groups", [])
        fields["rule_group_count"] = len(groups)
        total_rules = sum(len(g.get("rules", [])) for g in groups)
        fields["rule_count"] = total_rules
    
    # Prometheus ServiceMonitor
    elif kind == "ServiceMonitor":
        fields["selector"] = spec.get("selector", {})
        fields["endpoints"] = spec.get("endpoints", [])
    
    # KEDA ScaledObject
    elif kind == "ScaledObject":
        fields["scale_target_ref"] = spec.get("scaleTargetRef", {})
        fields["min_replica_count"] = spec.get("minReplicaCount")
        fields["max_replica_count"] = spec.get("maxReplicaCount")
        triggers = spec.get("triggers", [])
        fields["trigger_count"] = len(triggers)
        fields["trigger_types"] = [t.get("type") for t in triggers]
    
    # CertManager Certificate
    elif kind == "Certificate":
        fields["dns_names"] = spec.get("dnsNames", [])
        fields["issuer_ref"] = spec.get("issuerRef", {})
        fields["renewal_time"] = status.get("renewalTime")
        fields["not_after"] = status.get("notAfter")
    
    # CertManager Issuer
    elif kind in ["Issuer", "ClusterIssuer"]:
        fields["issuer_type"] = next(
            (key for key in spec.keys() if key not in ["email", "server"]),
            "unknown"
        )
    
    return fields


def validate_crd_schema(crd_resource: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validate that a CRD resource has required fields.
    
    Args:
        crd_resource: Extracted CRD resource
    
    Returns:
        Tuple of (is_valid, error_messages)
    """
    required_fields = ["name", "namespace", "uid", "kind", "labels", "owner_references"]
    errors = []
    
    for field in required_fields:
        if field not in crd_resource or crd_resource[field] is None:
            errors.append(f"Missing required field: {field}")
    
    # Validate types
    if not isinstance(crd_resource.get("labels"), dict):
        errors.append("labels must be a dictionary")
    if not isinstance(crd_resource.get("owner_references"), list):
        errors.append("owner_references must be a list")
    
    return len(errors) == 0, errors
