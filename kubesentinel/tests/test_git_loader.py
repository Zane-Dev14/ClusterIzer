from pathlib import Path

from kubesentinel.git_loader import (
    DESIRED_STATE_KEYS,
    classify_resources,
    discover_manifests,
    load_git_desired_state,
    parse_manifests,
)


def test_discover_manifests_excludes_helm_and_git_dirs(tmp_path: Path):
    (tmp_path / "apps").mkdir()
    (tmp_path / "charts").mkdir()
    (tmp_path / "templates").mkdir()
    (tmp_path / ".git").mkdir()

    keep = tmp_path / "apps" / "deployment.yaml"
    skip_chart = tmp_path / "charts" / "svc.yaml"
    skip_template = tmp_path / "templates" / "ing.yaml"
    skip_git = tmp_path / ".git" / "ignored.yaml"

    keep.write_text("kind: Deployment\nmetadata:\n  name: api\n")
    skip_chart.write_text("kind: Service\nmetadata:\n  name: should-skip\n")
    skip_template.write_text("kind: ConfigMap\nmetadata:\n  name: should-skip\n")
    skip_git.write_text("kind: Secret\nmetadata:\n  name: should-skip\n")

    manifests = discover_manifests(tmp_path)
    assert manifests == [keep]


def test_parse_manifests_namespace_default_and_cluster_scope(tmp_path: Path):
    file_path = tmp_path / "bundle.yaml"
    file_path.write_text(
        """
---
kind: Service
metadata:
  name: api
spec:
  selector:
    app: api
---
kind: ClusterRole
metadata:
  name: audit-reader
rules: []
---
# ignored empty doc
---
[]
---
kind: ConfigMap
metadata:
  name:
""".strip()
    )

    resources = parse_manifests([file_path])

    service = next(r for r in resources if r["kind"] == "service")
    cluster_role = next(r for r in resources if r["kind"] == "clusterrole")

    assert service["namespace"] == "default"
    assert cluster_role["namespace"] == "_cluster"


def test_classification_and_duplicate_manifest_last_wins(tmp_path: Path):
    first = tmp_path / "a-service.yaml"
    second = tmp_path / "z-service.yaml"
    unknown = tmp_path / "custom.yaml"

    first.write_text(
        """
kind: Service
metadata:
  name: api
  namespace: default
spec:
  selector:
    app: old
""".strip()
    )
    second.write_text(
        """
kind: Service
metadata:
  name: api
  namespace: default
spec:
  selector:
    app: new
""".strip()
    )
    unknown.write_text(
        """
kind: Widget
metadata:
  name: custom-1
spec:
  value: 1
""".strip()
    )

    manifests = discover_manifests(tmp_path)
    resources = parse_manifests(manifests)
    classified = classify_resources(resources)

    assert list(classified.keys()) == DESIRED_STATE_KEYS
    assert len(classified["services"]) == 1
    assert classified["services"][0]["spec"]["selector"]["app"] == "new"
    assert len(classified["crds"]) == 1
    assert classified["crds"][0]["kind"] == "widget"


def test_load_git_desired_state_local_path(tmp_path: Path):
    (tmp_path / "deploy.yaml").write_text(
        """
kind: Deployment
metadata:
  name: api
spec:
  replicas: 2
""".strip()
    )

    desired = load_git_desired_state(repo_url=None, local_path=str(tmp_path))
    assert list(desired.keys()) == DESIRED_STATE_KEYS
    assert len(desired["deployments"]) == 1
    assert desired["deployments"][0]["name"] == "api"
