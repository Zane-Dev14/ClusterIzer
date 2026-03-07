from pathlib import Path

from typer.testing import CliRunner

from kubesentinel.main import app


runner = CliRunner()


def test_scan_accepts_git_repo_flag_and_forwards_to_engine(monkeypatch, tmp_path: Path):
    fixture_repo = tmp_path / "desired"
    fixture_repo.mkdir()
    (fixture_repo / "deployment.yaml").write_text(
        """
kind: Deployment
metadata:
  name: api
""".strip()
    )

    captured = {}

    def fake_run_engine(user_query, namespace=None, agents=None, git_repo=None):
        captured["git_repo"] = git_repo
        return {
            "user_query": user_query,
            "cluster_snapshot": {
                "nodes": [],
                "deployments": [],
                "pods": [],
                "services": [],
            },
            "graph_summary": {},
            "signals": [],
            "risk_score": {"score": 0, "grade": "A", "signal_count": 0},
            "planner_decision": [],
            "failure_findings": [],
            "cost_findings": [],
            "security_findings": [],
            "strategic_summary": "",
            "final_report": "",
            "_drift_analysis": {},
        }

    monkeypatch.setattr("kubesentinel.main.run_engine", fake_run_engine)
    monkeypatch.setattr("kubesentinel.main.build_report", lambda state: "ok")

    result = runner.invoke(app, ["scan", "--git-repo", str(fixture_repo), "--json"])

    assert result.exit_code == 0
    assert captured["git_repo"] == str(fixture_repo)
    assert '"risk"' in result.stdout
