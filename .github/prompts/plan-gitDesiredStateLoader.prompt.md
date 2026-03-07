## Plan: Git Desired State Loader + Drift Pipeline

Implement a deterministic desired-state ingestion path that reads Kubernetes YAML from a Git source, normalizes it into a dedicated 9-key snapshot schema, compares it against live cluster state, and emits drift signals that flow into existing risk and reporting.

**Steps**
1. Phase 1: Add `kubesentinel/git_loader.py` with repository resolution, manifest discovery, parsing, normalization, and classification.
2. Implement `load_git_repository(repo_url: str, branch: str, path: str) -> Path`:
   - Local repo/path: use directly.
   - Remote URL: shallow clone (`--depth 1`) and return manifest root.
   - Keep temp clone lifecycle safe so parsing happens while clone path is valid.
3. Implement `discover_manifests(root: Path) -> list[Path]`:
   - Recursively include `*.yaml` and `*.yml`.
   - Exclude `.git/`, `charts/`, `templates/`, `.helm/`.
   - Return deterministically sorted paths.
4. Implement YAML ingestion with `yaml.safe_load_all()`:
   - Skip empty/non-dict docs.
   - Skip docs missing `kind` or `metadata.name`.
5. Implement `normalize_resource(obj: dict) -> dict` with required fields:
   - `kind`, `name`, `namespace` (default `default`), `labels`, `annotations`, `spec`.
6. Implement `KIND_MAP` + `classify_resources(resources: list[dict]) -> dict[str, list]`:
   - Return exactly: `deployments`, `statefulsets`, `daemonsets`, `services`, `pods`, `configmaps`, `secrets`, `ingresses`, `crds`.
   - Route unknown/custom kinds to `crds`.
7. Expose `load_git_desired_state(repo_url: str | None, local_path: str | None, branch: str = "main") -> dict[str, list]`:
   - Validate input (`repo_url` xor `local_path`).
   - Pipeline: repo -> discover -> parse -> normalize -> classify.
8. Phase 2: Extend drift engine in `kubesentinel/persistence.py` with `compare_live_vs_desired(live, desired) -> DriftReport`:
   - Detect `missing_resource`, `extra_resource`, `spec_drift`, `label_drift`, `replica_drift`.
   - Identity key: `(kind, namespace, name)`.
   - Return shape: `{missing, extra, changed}` in deterministic order.
9. Integrate desired-state drift into existing drift analysis as additive behavior, without breaking current snapshot-vs-snapshot drift logic.
10. Phase 3: Signals integration:
    - Add `DRIFT_MISSING_RESOURCE` (`high`), `DRIFT_EXTRA_RESOURCE` (`low`), `DRIFT_CONFIG_CHANGE` (`medium`).
    - Ensure signal deduplication stays consistent with current behavior.
11. Phase 4: CLI/runtime integration:
    - Add `--git-repo` to `kubesentinel scan` (URL or local path).
    - Flow with flag: extract live -> load desired -> compare drift -> include drift signals -> continue risk/planner/report.
    - Preserve existing behavior when no git input is provided.
12. Phase 5: Tests:
    - Add `kubesentinel/tests/test_git_loader.py` for manifest parsing, namespace defaulting, classification.
    - Add desired-drift tests (new drift test module) for missing/extra/spec/label/replica cases and deterministic output.
    - Extend signal/risk tests as needed for new drift signal impact.
    - Add CLI smoke test for `--git-repo` local fixture path.
13. Add direct dependency for YAML parsing in `pyproject.toml` (`PyYAML`) if not already declared.


**Extra things to rememember**
1. Temporary Git Clone Lifecycle (Important)

Your plan mentions this briefly, but it needs a hard rule.

Problem

If load_git_repository() returns a path from TemporaryDirectory(), the directory will disappear when the context closes.

Example bug:

path = load_git_repository(...)
manifests = discover_manifests(path)   # path already deleted
Correct Pattern

Do not return a temp path.

Instead orchestrate everything inside one function.

Better design:

def load_git_desired_state(...):

    with tempfile.TemporaryDirectory() as tmp:
        repo_path = resolve_repo(...)
        manifests = discover_manifests(repo_path)
        resources = parse_manifests(manifests)
        normalized = normalize(resources)
        return classify_resources(normalized)

This ensures:

clone -> parse -> normalize -> classify

happens while the repo exists.

2. Resource Identity Must Be Canonical

Your drift logic proposes:

(kind, namespace, name)

This is correct, but two things must be enforced:

Normalize case

Kubernetes kinds vary:

Deployment
deployment
apps/v1 Deployment

Normalize with:

kind = obj["kind"].lower()

Example canonical key:

("deployment", "default", "api")
Namespace normalization

Some resources are cluster scoped:

Namespace
ClusterRole
CRD

Your schema forces namespace.

Solution:

namespace = metadata.get("namespace", "_cluster")

Using _cluster prevents collisions.

3. Replica Drift Logic Needs Special Handling

You already noticed this.

Live snapshot:

deployment["replicas"]

Desired state:

deployment["spec"]["replicas"]

If you diff raw specs you'll miss drift.

Create helper:

def extract_replicas(resource):
    if resource["kind"] == "deployment":
        return resource["spec"].get("replicas", 1)

Then compare.

Otherwise your replica drift detection will silently fail.

4. YAML Multi-Doc Edge Cases

GitOps repos often contain YAML like this:

---
apiVersion: v1
kind: Service
...
---
# comment
---

Your parser must ignore:

None
strings
lists

Add this filter:

if not isinstance(doc, dict):
    continue

Also validate:

metadata
metadata.name
kind

or you'll hit runtime crashes later.

5. Deterministic Ordering (Critical for Testing)

Drift results must be deterministic or your tests will randomly fail.

Sort:

resources
manifests
drift lists

Example:

sorted(resources, key=lambda r: (r["kind"], r["namespace"], r["name"]))

Same for drift output.

Otherwise:

pytest

will intermittently break.

6. CRD Handling Is Correct (Good Choice)

Your rule:

unknown kinds -> crds bucket

is exactly right.

Do not attempt CRD schema parsing.

Just store:

kind
name
namespace
spec

Your graph builder can ignore them.

7. CLI Interface Is Good

Your CLI design:

kubesentinel scan --git-repo

is the correct UX.

One improvement:

Allow both forms:

--git-repo https://repo
--git-repo ./local/path

Then you don't need local_path.

Just detect:

if path.exists()
8. Signals Integration Strategy Is Good

Your signals:

DRIFT_MISSING_RESOURCE   high
DRIFT_EXTRA_RESOURCE     low
DRIFT_CONFIG_CHANGE      medium

Good mapping.

But ensure you deduplicate by resource identity.

Example signal key:

signal_id + resource_key

Otherwise large clusters will produce thousands of duplicates.

9. Tests Need One More Case

Add this test because it will break otherwise:

Duplicate manifests

Example GitOps mistake:

service.yaml
service-copy.yaml

Same resource identity.

Decide deterministic behavior:

last one wins

or

raise error

I recommend:

last manifest wins
10. Performance Consideration (Future)

GitOps repos can contain thousands of manifests.

Avoid loading everything into memory first.

Current pipeline is fine, but later you may want:

stream parse -> normalize -> classify

Not required now.


**Relevant files**
- `/Users/eric/IBM/Projects/courses/Deliverables/week-4/kubesentinel/git_loader.py` — new module for loader/discovery/parse/normalize/classify.
- `/Users/eric/IBM/Projects/courses/Deliverables/week-4/kubesentinel/persistence.py` — desired-vs-live drift comparison and integration.
- `/Users/eric/IBM/Projects/courses/Deliverables/week-4/kubesentinel/signals.py` — new drift signal IDs and severity mapping.
- `/Users/eric/IBM/Projects/courses/Deliverables/week-4/kubesentinel/runtime.py` — desired-state pipeline node/wiring.
- `/Users/eric/IBM/Projects/courses/Deliverables/week-4/kubesentinel/main.py` — `--git-repo` CLI flag and pass-through.
- `/Users/eric/IBM/Projects/courses/Deliverables/week-4/kubesentinel/models.py` — optional state fields for desired snapshot metadata.
- `/Users/eric/IBM/Projects/courses/Deliverables/week-4/kubesentinel/tests/test_git_loader.py` — new loader tests.
- `/Users/eric/IBM/Projects/courses/Deliverables/week-4/pyproject.toml` — add `PyYAML` if missing.

**Verification**
1. `pytest kubesentinel/tests/test_git_loader.py -q`
2. `pytest kubesentinel/tests/test_signals.py kubesentinel/tests/test_risk.py -q`
3. `pytest -q`
4. `kubesentinel scan --git-repo <fixture_or_repo_path> --json` and validate drift + signal output.
5. `kubesentinel scan --json` to confirm non-git behavior remains unchanged.

**Decisions captured**
- CLI scope now: only `--git-repo`.
- Desired snapshot stays limited to the 9 required keys.
- Manifest scan scope: repo root only.
- No Helm runtime/template execution; only static YAML parsing.

**Further Considerations**
1. Temporary clone lifecycle should be codified so downstream processing does not outlive temp content (recommend: clone/parse/classify within a single orchestrator scope).
2. Live resource shape differs from desired normalized shape (for workloads `replicas` lives top-level in live snapshot, while desired replicas is in `spec`); replica comparison logic should normalize both before diffing.
3. CRD handling should remain lightweight by treating unknown kinds as `crds` bucket entries with generic `spec` comparison only.
