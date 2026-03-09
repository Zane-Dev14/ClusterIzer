# KubeSentinel Operations & Usage Guide

**Status**: Production-Ready  
**Last Updated**: March 2026

---

## 1. Installation & Setup

### Prerequisites

- **Python 3.11+**
- **uv** package manager ([install uv](https://github.com/astral-sh/uv))
- **Ollama** with `llama3.1:8b-instruct-q8_0` model ([install Ollama](https://ollama.ai))
- Access to a Kubernetes cluster (kubeconfig or in-cluster authentication)

### Installation

```bash
# Clone repository
git clone <repo>
cd week-4

# Install dependencies
make install

# Or using uv directly
uv sync
```

### Environment Configuration

Create a `.env` file in the project root:

```bash
# Kubernetes
KUBECONFIG=${HOME}/.kube/config

# Slack Integration (optional)
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T00/B00/XX

# Safety & Access Control (optional)
KUBESENTINEL_OPS=U123:U456:U789  # Comma-separated Slack user IDs for approvals
FORCE_EXEC_ALLOWLIST=0            # Set to 1 to bypass approval requirements (emergency only)

# Ollama Configuration (optional)
OLLAMA_MODEL=llama3.1:8b-instruct-q8_0
OLLAMA_BASE_URL=http://localhost:11434
```

---

## 2. Basic Usage via CLI

### Quick Scan

```bash
# Run full cluster analysis
make run

# Or using uv directly
uv run kubesentinel scan

# Output: report.md with risk assessment and findings
```

### Custom Query Analysis

```bash
# Cost optimization focus
uv run kubesentinel scan --query "reduce costs"

# Security audit focus
uv run kubesentinel scan --query "security audit"

# Reliability analysis focus
uv run kubesentinel scan --query "identify reliability issues"

# Specific namespace
uv run kubesentinel scan --namespace production

# Full cluster review (all agents)
uv run kubesentinel scan --query "full cluster health check"
```

### Advanced Options

```bash
# JSON output (for automation)
uv run kubesentinel scan --json > analysis.json

# CI mode (exit 0 for A/B/C, exit 1 for D/F)
uv run kubesentinel scan --ci
echo $?  # 0 = pass, 1 = fail

# Combined options
uv run kubesentinel scan --ci --json --query "security"

# Force specific agents
uv run kubesentinel scan --agent cost_agent --agent security_agent

# Verbose logging
KUBESENTINEL_VERBOSE_AGENTS=1 uv run kubesentinel scan

# Test node failure impact
uv run kubesentinel simulate node-failure --node NODE_NAME
```

### Output Formats

**Default: Markdown Report**
```
report.md           # Full analysis with findings and recommendations
```

**JSON Output**
```json
{
  "risk_score": {
    "score": 45,
    "grade": "B"
  },
  "signals_count": 12,
  "findings": [...],
  "exit_code": 0
}
```

---

## 3. Slack Integration

### Bot Setup

#### Step 1: Create Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App**
3. Choose **From scratch**
4. Name: `KubeSentinel`
5. Select your workspace

#### Step 2: Enable Socket Mode

1. Navigate to **Socket Mode** (left menu)
2. Toggle **Enable Socket Mode**
3. Copy the app-level token (starts with `xapp-`)
4. Save as `SLACK_APP_TOKEN` in `.env`

#### Step 3: Create Bot User

1. Go to **OAuth & Permissions** (left menu)
2. Under **Scopes > Bot Token Scopes**, add:
   - `app_mentions:read` - Read app mentions
   - `chat:write` - Send messages
   - `commands:write` - Support slash commands
   - `channels:read` - List channels
   - `conversations:read` - List conversations
   - `users:read` - Read user info

3. **Install to Workspace** button appears
4. Click and authorize
5. Copy the Bot User OAuth Token (starts with `xoxb-`)
6. Save as `SLACK_BOT_TOKEN` in `.env`

#### Step 4: Add Permissions (Incoming Webhooks)

1. Go to **Incoming Webhooks** (left menu)
2. Toggle **Activate Incoming Webhooks**
3. Click **Add New Webhook to Workspace**
4. Choose channel (e.g., `#kubesentinel-alerts`)
5. Copy the webhook URL
6. Save as `SLACK_WEBHOOK_URL` in `.env`

### Running the Bot

```bash
# Start the bot (runs in foreground)
python -m kubesentinel.integrations.slack_bot

# Or with logging
KUBESENTINEL_VERBOSE_AGENTS=1 python -m kubesentinel.integrations.slack_bot

# In production, run as systemd service or container
# See: docs/OPERATIONS_AND_USAGE.md#slack-bot-systemd-setup
```

### Bot Interaction Examples

#### Basic Query

```
User (in Slack):
@kubesentinel why are my pods pending?

Bot Response:
🟡 KubeSentinel Analysis
Risk Score: 62/100 (C) Medium Risk

Key Findings:
• 3 pods in Pending state
• Insufficient node resources
• Consider scaling cluster or adjusting resource requests

[View Full Report] [Run Fixes]
```

#### Follow-up Questions (Uses Cache)

```
User:
@kubesentinel show me the full report

Bot Response:
[Full report from previous analysis - instant response]

User:
@kubesentinel tell me more about security

Bot Response:
[Uses cached analysis, shows security findings]
```

#### Run Fixes

```
User:
Clicks [Run Fixes] button

Bot Response (if write verb):
Approve & Execute kubectl scale deployment myapp --replicas=3?

[✅ Approve & Execute] [⏭ Skip]

User:
Clicks [✅ Approve & Execute]

Bot Response:
✅ kubectl scale deployment myapp --replicas=3
Executed in 1.23s
deployment.apps/myapp scaled
```

### Webhook Notifications

Enable automatic alerts for high-risk clusters:

```python
# In scheduled job (cron/scheduler)
import os
from kubesentinel.runtime import run_engine
from kubesentinel.reporting import build_report
from slack_sdk import WebClient

# Run analysis
state = run_engine("full cluster health check")

# Check risk level
if state.risk_score["grade"] in ["D", "F"]:
    # Send Slack alert
    client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
    client.chat_postMessage(
        channel="kubesentinel-alerts",
        blocks=[
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🚨 Critical Cluster Issue"}
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"Risk Score: {state.risk_score['score']}/100 ({state.risk_score['grade']})"
                }
            }
        ]
    )
```

---

## 4. Kubernetes Safety & kubectl Execution

### Safe kubectl Commands

KubeSentinel supports safe kubectl execution for remediation. Commands are validated for safety:

**Allowed Read-Only Commands**:
```
kubectl get
kubectl describe
kubectl logs
kubectl top
```

**Allowed Write Commands**:
```
kubectl patch
kubectl scale
kubectl set
kubectl rollout restart
kubectl apply
kubectl delete
```

**Blocked Commands**:
```
❌ kubectl exec          (no direct container access)
❌ kubectl debug         (no debugging)
❌ kubectl attach        (no container interaction)
❌ kubectl drain         (protected operation)
❌ kubectl delete node   (protected operation)
```

### Approval Gates

All write verbs (`patch`, `scale`, `rollout`, `apply`, `delete`) require explicit user approval:

```
Bot shows: kubectl scale deployment app --replicas=5

User clicks: [✅ Approve & Execute]

System:
1. Re-validates command syntax
2. Checks shell metacharacters (rejects: ; | & $ ( ) < > ` \)
3. Checks approver list (if KUBESENTINEL_OPS is set)
4. Executes with 60-second timeout
5. Logs execution with Slack user ID
```

### Command Extraction

Recommendations in findings automatically extract kubectl commands:

```json
{
  "resource": "deployment/nginx",
  "analysis": "Deployment has only 1 replica",
  "remediation": {
    "commands": [
      "kubectl scale deployment nginx --replicas=3"
    ],
    "automated": true
  }
}
```

Only commands in `remediation.commands` with `automated: true` are executed.

### Execution Audit Trail

All kubectl executions are logged to:
```
runtime_traces/kubectl_execution_YYYYMMDD_HHMMSS.log
```

Format (JSONL):
```json
{
  "timestamp": "2026-03-09T12:34:56.000Z",
  "user": "U123456",
  "approver_user_id": "U654321",
  "command": "scale deployment app --replicas=3",
  "argv": ["scale", "deployment", "app", "--replicas=3"],
  "executed": true,
  "ok": true,
  "stdout": "deployment.apps/app scaled",
  "stderr": "",
  "elapsed_seconds": 1.23
}
```

---

## 5. Configuration & Customization

### Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `KUBECONFIG` | Kubernetes config path | `~/.kube/config` |
| `SLACK_BOT_TOKEN` | Slack bot auth | `xoxb-...` |
| `SLACK_APP_TOKEN` | Slack Socket Mode | `xapp-...` |
| `SLACK_WEBHOOK_URL` | Slack webhooks | `https://hooks.slack.com/...` |
| `KUBESENTINEL_OPS` | Approvers list | `U123:U456:U789` |
| `FORCE_EXEC_ALLOWLIST` | Bypass approvals | `0` (never use in prod) |
| `OLLAMA_MODEL` | LLM model | `llama3.1:8b-instruct-q8_0` |
| `OLLAMA_BASE_URL` | Ollama endpoint | `http://localhost:11434` |
| `KUBESENTINEL_VERBOSE_AGENTS` | Debug logging | `1` |

### Agent Configuration

Override default agent selection:

```bash
# Run only cost analysis
uv run kubesentinel scan --agent cost_agent

# Run multiple agents
uv run kubesentinel scan --agent failure_agent --agent security_agent

# Or via query (automatic routing)
uv run kubesentinel scan --query "reduce costs"
```

### Signal Configuration

Modify signal detection in `kubesentinel/signals.py`:

```python
# Adjust severity weights
SIGNAL_WEIGHTS = {
    "critical": 15,
    "high": 8,
    "medium": 3,
    "low": 1
}

# Adjust signal cap
MAX_SIGNALS = 200  # Increase for more detailed analysis
```

### Risk Scoring Tuning

Modify scoring algorithm in `kubesentinel/risk.py`:

```python
# Adjust category multipliers
CATEGORY_MULTIPLIERS = {
    "security": 2.0,      # Security incidents weighted heavily
    "reliability": 1.8,
    "cost": 0.5,          # Cost issues lower priority
    "architecture": 1.0
}

# Adjust grade boundaries
GRADE_THRESHOLDS = {
    "A": (0, 34),
    "B": (35, 54),
    "C": (55, 74),
    "D": (75, 89),
    "F": (90, 100)
}
```

---

## 6. Scheduled Scanning & CI/CD Integration

### Kubernetes CronJob

Deploy KubeSentinel as a scheduled job:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: kubesentinel-scan
  namespace: default
spec:
  schedule: "0 * * * *"  # Every hour
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: kubesentinel
          containers:
          - name: kubesentinel
            image: kubesentinel:latest
            command:
            - sh
            - -c
            - |
              python -m kubesentinel.integrations.slack_bot
            env:
            - name: SLACK_BOT_TOKEN
              valueFrom:
                secretKeyRef:
                  name: kubesentinel-secrets
                  key: slack-bot-token
            - name: SLACK_APP_TOKEN
              valueFrom:
                secretKeyRef:
                  name: kubesentinel-secrets
                  key: slack-app-token
          restartPolicy: OnFailure
```

### GitHub Actions CI

```yaml
name: KubeSentinel Cluster Check

on:
  schedule:
    - cron: '0 * * * *'  # Hourly
  workflow_dispatch:

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install uv
          uv sync
      
      - name: Run KubeSentinel scan
        env:
          KUBECONFIG: ${{ secrets.KUBECONFIG }}
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
        run: |
          uv run kubesentinel scan --json > analysis.json
      
      - name: Comment on issue if failing
        if: failure()
        uses: actions/github-script@v6
        with:
          script: |
            const fs = require('fs');
            const analysis = JSON.parse(fs.readFileSync('analysis.json'));
            if (analysis.risk_score.grade in ['D', 'F']) {
              github.rest.issues.createComment({
                issue_number: context.issue.number,
                owner: context.repo.owner,
                repo: context.repo.repo,
                body: `⚠️ Cluster has ${analysis.risk_score.grade} grade issues`
              });
            }
```

### GitLab CI

```yaml
kubesentinel-scan:
  stage: test
  image: python:3.11
  script:
    - pip install uv
    - uv sync
    - uv run kubesentinel scan --ci --json
  artifacts:
    reports:
      dotenv: analysis.json
  only:
    - schedules
```

---

## 7. Troubleshooting

### Common Issues

**Issue: Bot not responding in Slack**

```
Check:
1. SLACK_BOT_TOKEN and SLACK_APP_TOKEN set correctly
2. Bot is mentioned with @kubesentinel (case-sensitive)
3. Bot has app_mentions:read permission
4. Socket Mode is enabled
5. Check logs for errors
```

**Issue: kubectl commands failing**

```
Check:
1. kubeconfig is valid: kubectl get pods works
2. Approver is in KUBESENTINEL_OPS list
3. Command is in allowed list (see: Safe kubectl Commands)
4. No shell metacharacters in command
5. Timeout not exceeded (60 seconds)
```

**Issue: High memory usage**

```
Solutions:
1. Reduce MAX_SIGNALS (default: 200)
2. Reduce MAX_PODS (default: 1000)
3. Disable CRD discovery: crd_discovery.py disabled
4. Run on smaller cluster first for testing
```

**Issue: Slow analysis**

```
Profile:
1. Cluster scan: 2-5s (depends on pod count)
2. Graph building: 1s
3. Signal generation: 1s
4. Agent execution: 15-30s (bottleneck with LLM)

Optimization:
1. Use cheaper LLM model
2. Run agents in parallel (already enabled)
3. Reduce query complexity
4. Skip agents: --agent failure_agent only
```

### Debug Logging

Enable verbose logging:

```bash
KUBESENTINEL_VERBOSE_AGENTS=1 uv run kubesentinel scan

# Outputs:
# - Agent invocations and responses
# - LLM token counts
# - JSON parsing failures
# - Tool usage
```

### Accessing Logs

**Agent output logs**:
```
runtime_traces/agent_outputs_YYYYMMDD_HHMMSS.log  # Parse failures
```

**Kubectl execution logs**:
```
runtime_traces/kubectl_execution_YYYYMMDD_HHMMSS.log  # Command executions
```

**Runtime traces**:
```
runtime_traces/runtime_graph_YYYYMMDD_HHMMSS.mmd    # Pipeline visualization
runtime_traces/runtime_trace_YYYYMMDD_HHMMSS.json   # Full execution trace
```

---

## 8. Production Deployment

### Docker Container

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy project
COPY . .

# Install dependencies
RUN uv sync --frozen

# Run bot
CMD ["python", "-m", "kubesentinel.integrations.slack_bot"]
```

### systemd Service

```ini
[Unit]
Description=KubeSentinel Slack Bot
After=network.target

[Service]
Type=simple
User=kubesentinel
WorkingDirectory=/opt/kubesentinel
EnvironmentFile=/etc/kubesentinel/.env
ExecStart=/usr/local/bin/python -m kubesentinel.integrations.slack_bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Kubernetes Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubesentinel-bot
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kubesentinel
  template:
    metadata:
      labels:
        app: kubesentinel
    spec:
      serviceAccountName: kubesentinel
      containers:
      - name: bot
        image: kubesentinel:latest
        env:
        - name: SLACK_BOT_TOKEN
          valueFrom:
            secretKeyRef:
              name: kubesentinel-secrets
              key: slack-bot-token
        - name: SLACK_APP_TOKEN
          valueFrom:
            secretKeyRef:
              name: kubesentinel-secrets
              key: slack-app-token
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
```

---

## 9. Advanced Topics

### Custom Agent Implementation

Extend agent functionality in `kubesentinel/agents.py`:

```python
async def custom_agent_node(state: InfraState) -> Dict[str, Any]:
    """Custom agent for specialized analysis."""
    query = state.get("query", "")
    
    # Custom logic here
    findings = []
    
    if "my-keyword" in query:
        findings.append({
            "resource": "custom-resource",
            "severity": "high",
            "analysis": "Custom finding",
            "remediation": {...},
            "verification": {...}
        })
    
    return {"custom_findings": findings}
```

### Custom Signal Rules

Add new signals in `kubesentinel/signals.py`:

```python
def _generate_custom_signals(cluster_snapshot, graph_summary):
    """Generate custom signals for your environment."""
    signals = []
    
    for deployment in cluster_snapshot.get("deployments", []):
        if deployment.get("name").startswith("legacy-"):
            signals.append({
                "category": "architecture",
                "severity": "medium",
                "resource": deployment["name"],
                "analysis": "Legacy application detected",
                "remediation": "Consider modernizing this application"
            })
    
    return signals
```

### Extending Report Output

Customize report format in `kubesentinel/reporting.py`:

```python
def _build_custom_section(state: InfraState) -> str:
    """Add custom section to report."""
    return """
## Custom Analysis

Your custom findings here...
"""
```

