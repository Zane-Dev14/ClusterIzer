from kubesentinel.persistence import compare_live_vs_desired, drift_to_signals
from kubesentinel.signals import (
    DRIFT_CONFIG_CHANGE,
    DRIFT_EXTRA_RESOURCE,
    DRIFT_MISSING_RESOURCE,
)


def test_compare_live_vs_desired_detects_all_drift_types_deterministically():
    live = {
        "deployments": [
            {
                "name": "api",
                "namespace": "default",
                "replicas": 2,
                "labels": {"app": "api-live"},
                "selector": {"app": "api-live"},
                "pod_labels": {"app": "api-live"},
                "containers": [],
            }
        ],
        "services": [
            {
                "name": "legacy",
                "namespace": "default",
                "type": "ClusterIP",
                "selector": {"app": "legacy"},
            }
        ],
        "statefulsets": [],
        "daemonsets": [],
        "pods": [],
    }

    desired = {
        "deployments": [
            {
                "kind": "deployment",
                "name": "api",
                "namespace": "default",
                "labels": {"app": "api-desired"},
                "annotations": {},
                "spec": {
                    "replicas": 3,
                    "selector": {"app": "api-desired"},
                    "template": {
                        "metadata": {"labels": {"app": "api-desired"}},
                        "spec": {"containers": []},
                    },
                },
            }
        ],
        "services": [
            {
                "kind": "service",
                "name": "api",
                "namespace": "default",
                "labels": {},
                "annotations": {},
                "spec": {"selector": {"app": "api-desired"}, "type": "ClusterIP"},
            }
        ],
        "statefulsets": [],
        "daemonsets": [],
        "pods": [],
        "configmaps": [],
        "secrets": [],
        "ingresses": [],
        "crds": [
            {
                "kind": "clusterrole",
                "name": "read-all",
                "namespace": "_cluster",
                "labels": {},
                "annotations": {},
                "spec": {},
            }
        ],
    }

    report = compare_live_vs_desired(live, desired)

    assert [m["resource_key"] for m in report["missing"]] == [
        "clusterrole/_cluster/read-all",
        "service/default/api",
    ]
    assert [m["resource_key"] for m in report["extra"]] == ["service/default/legacy"]

    changed_types = [c["drift_type"] for c in report["changed"]]
    assert changed_types == ["label_drift", "replica_drift", "spec_drift"]
    assert all(c["resource_key"] == "deployment/default/api" for c in report["changed"])


def test_drift_to_signals_adds_new_ids_and_deduplicates():
    drift_analysis = {
        "summary": {
            "categorized_drifts": {
                "critical_lost": [],
                "critical_risky": [],
            }
        },
        "desired_drift": {
            "missing": [
                {
                    "resource_key": "deployment/default/api",
                    "description": "Desired resource missing from cluster: deployment/default/api",
                }
            ],
            "extra": [
                {
                    "resource_key": "service/default/legacy",
                    "description": "Live resource not found in desired state: service/default/legacy",
                }
            ],
            "changed": [
                {
                    "resource_key": "deployment/default/api",
                    "description": "Spec drift detected for deployment/default/api",
                }
            ],
        },
    }

    existing = [
        {
            "category": "reliability",
            "severity": "high",
            "resource": "drift/deployment/default/api",
            "message": "Desired resource missing from cluster: deployment/default/api",
            "signal_id": DRIFT_MISSING_RESOURCE,
        }
    ]

    out = drift_to_signals(drift_analysis, existing)

    ids = {(s["signal_id"], s["resource"]) for s in out}
    assert (DRIFT_MISSING_RESOURCE, "drift/deployment/default/api") in ids
    assert (DRIFT_EXTRA_RESOURCE, "drift/service/default/legacy") in ids
    assert (DRIFT_CONFIG_CHANGE, "drift/deployment/default/api") in ids

    # Missing resource signal should not be duplicated
    assert len([s for s in out if s["signal_id"] == DRIFT_MISSING_RESOURCE]) == 1
