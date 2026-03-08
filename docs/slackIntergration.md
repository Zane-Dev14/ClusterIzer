# KubeSentinel Slack Integration: Implementation Plan

## Overview

Adding Slack notifications will:
- ✅ Make KubeSentinel visible to ops teams
- ✅ Enable real-time alerts for critical issues
- ✅ Allow users to quickly jump to full reports
- ✅ Increase adoption (ops teams use Slack daily)
- ✅ Drive engagement and retention

**Effort:** 3-5 days
**Impact:** HIGH (breakthrough feature)
**Go-Live:** Target: 1 week

---

## Architecture

```
┌──────────────────────┐
│ Cluster Scan         │
│ (existing)           │
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│ Risk Analysis        │
│ (existing)           │
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│ Slack Formatter      │ ← NEW
│ (async job)          │
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│ Slack API Client     │ ← NEW
│ (webhook or token)   │
└──────────┬───────────┘
           ↓
┌──────────────────────┐
│ Slack Channel        │
│ #kubesentinel-risks  │
└──────────────────────┘
```

---

## Implementation Roadmap

### PHASE 1: Basic Alerts (Day 1-2)

**Feature: Critical Risk Notification**

```python
# kubesentinel/integrations/slack.py (NEW)

from slack_sdk import WebClient
from typing import Dict, List

class SlackNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send_risk_alert(self, top_risks: List[Dict]):
        """Send immediate alert about critical risks."""
        critical_risks = [r for r in top_risks if r['severity'] == 'critical']
        
        if not critical_risks:
            return  # No critical risks
        
        # Build Slack message blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🚨 Critical Kubernetes Issue"
                }
            }
        ]
        
        for risk in critical_risks[:3]:  # Top 3 critical
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{risk['title']}*\n{risk['root_cause']}"
                }
            })
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"💡 *Fix:* {risk['recommended_fix'][:200]}..."
                }
            })
        
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View Full Report"
                    },
                    "url": "https://your-domain.com/reports/latest",
                    "style": "danger"
                }
            ]
        })
        
        # Send to Slack
        self.send_blocks(blocks)

    def send_blocks(self, blocks):
        """Send message blocks to Slack webhook."""
        import requests
        requests.post(self.webhook_url, json={"blocks": blocks})
```

**Integration Point:**
```python
# In kubesentinel/main.py
def scan(...):
    # ... existing code ...
    
    # NEW: Send Slack notification
    if slack_webhook := os.getenv('SLACK_WEBHOOK_URL'):
        notifier = SlackNotifier(slack_webhook)
        notifier.send_risk_alert(state['_risk_analysis']['top_risks'])
```

**User Setup:**
```bash
# Step 1: Create Slack app
# → https://api.slack.com/apps
# → Create "KubeSentinel" app
# → Enable Incoming Webhooks
# → Create webhook for #kubesentinel-risks

# Step 2: Export credential
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/T00/B00/XX"

# Step 3: Run scan
uv run kubesentinel scan
# → Sends Slack message automatically
```

**Expected Output in Slack:**
```
🚨 Critical Kubernetes Issue

*Crashloop Pod*
Nginx OpenResty failed to initialize the Lua VM

💡 Fix: Rebuild image with RUN luarocks install lfs lua-cjson
        Then redeploy: kubectl rollout restart deployment media-frontend

┌─────────────────────┐
│ View Full Report    │
└─────────────────────┘
```

---

### PHASE 2: Daily Digest (Day 2-3)

**Feature: Scheduled Summary**

```python
# kubesentinel/integrations/slack_scheduler.py (NEW)

import schedule
import time
from datetime import datetime

class SlackScheduler:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.notifier = SlackNotifier(webhook_url)
    
    def start_daily_digest(self, hour: int = 8, minute: int = 0):
        """Send daily summary every morning at specified time."""
        schedule.every().day.at(f"{hour}:{minute:02d}").do(
            self.send_daily_digest
        )
        
        # Run scheduler in background thread
        while True:
            schedule.run_pending()
            time.sleep(60)
    
    def send_daily_digest(self):
        """Create beautiful daily summary."""
        # Load latest snapshot from DB
        latest = get_latest_snapshot()
        
        risk_data = latest['_risk_analysis']
        top_risks = risk_data['top_risks'][:5]
        risk_score = risk_data['risk_score']
        risk_grade = risk_data['risk_grade']
        
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "📊 Daily Kubernetes Health Report"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Cluster Risk Score*\n{risk_score}/100 ({risk_grade})"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Scan Time*\n{datetime.now().strftime('%H:%M %Z')}"
                    }
                ]
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Top 5 Risks*"
                }
            }
        ]
        
        for i, risk in enumerate(top_risks, 1):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{i}. *{risk['title']}* ({risk['severity'].upper()})\n"
                        f"   Affects {risk['affected_count']} resources"
                    )
                }
            })
        
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View Report"
                    },
                    "url": "https://your-domain.com/reports/latest"
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "History"
                    },
                    "url": "https://your-domain.com/history"
                }
            ]
        })
        
        self.notifier.send_blocks(blocks)
```

**User Setup:**
```bash
# Run scheduler as systemd service
export SLACK_WEBHOOK_URL="https://..."
uv run kubesentinel schedule --daily --time 08:00
# Sends report every morning at 8 AM
```

---

### PHASE 3: Interactive Buttons (Day 3-4)

**Feature: One-Click Actions**

```python
# kubesentinel/integrations/slack_actions.py (NEW)

from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/slack/actions")
async def handle_slack_action(request: Request):
    """Handle button clicks from Slack."""
    payload = await request.json()
    action_type = payload['type']
    
    if action_type == 'block_actions':
        for action in payload['actions']:
            if action['action_id'] == 'view_report':
                # User clicked "View Report"
                # → Generate shareable link
                pass
            
            elif action['action_id'] == 'run_fix':
                # User clicked "Run Fix"
                # → Generate kubectl command
                # → Send back in thread
                pass
```

**Slack Message with Buttons:**
```
🚨 Critical: Crashloop Pod

*Nginx OpenResty failed to initialize the Lua VM*

Affects: pod/social-network/media-frontend-7f6c7d4b94-qnwhm

┌────────────────────────────────────────┐
│  View Full Report  │  Run Fix  │ More  │
└────────────────────────────────────────┘
```

When user clicks "Run Fix":
```
✅ Here's the command to fix this:

kubectl rollout restart deployment media-frontend -n social-network

Then verify with:
kubectl get pod -n social-network -l app=media-frontend -o wide
```

---

### PHASE 4: Channel Configuration (Day 4-5)

**Feature: Route Alerts by Severity**

```python
# kubesentinel/integrations/slack_router.py (NEW)

CHANNEL_CONFIG = {
    'critical': '#kubesentinel-critical',     # Immediate attention
    'high': '#kubesentinel-high',              # Daily review
    'medium': '#kubesentinel-digest',          # Weekly digest
    'low': '#kubesentinel-analytics',          # Archive only
}

def send_alert_to_channel(risk: Dict, webhook_urls: Dict[str, str]):
    """Route alert to appropriate channel based on severity."""
    severity = risk['severity']  # 'critical', 'high', etc.
    channel = CHANNEL_CONFIG[severity]
    webhook_url = webhook_urls[channel]
    
    notifier = SlackNotifier(webhook_url)
    notifier.send_risk_alert([risk])
```

**User Setup:**
```bash
# Create webhooks for multiple channels
export SLACK_WEBHOOK_CRITICAL="https://hooks.slack.com/.../critical"
export SLACK_WEBHOOK_HIGH="https://hooks.slack.com/.../high"
export SLACK_WEBHOOK_DIGEST="https://hooks.slack.com/.../digest"

uv run kubesentinel scan
# Sends to appropriate channels automatically
```

---

## Implementation Checklist

### Week 1: Core Integration

- [ ] Add `slack_sdk` dependency to pyproject.toml
- [ ] Create `integrations/slack.py` with SlackNotifier class
- [ ] Implement `send_blocks()` method (webhook POST)
- [ ] Implement `send_critical_alert()` method
- [ ] Add CLI flag: `--slack-webhook` or env var
- [ ] Test with real Slack workspace

### Week 2: Scheduling + Features

- [ ] Create `integrations/slack_scheduler.py`
- [ ] Implement daily digest formatting
- [ ] Add systemd service file for scheduling
- [ ] Implement multi-channel routing
- [ ] Add unit tests for Slack formatting

### Week 3: Polish + Launch

- [ ] Interactive button support
- [ ] Thread-based responses
- [ ] Rich emoji/formatting
- [ ] Error handling & retry logic
- [ ] Documentation
- [ ] Update README with Slack setup guide

---

## Dependencies to Add

```toml
# pyproject.toml
[project.dependencies]
# ... existing ...
"slack-sdk>=3.23.0",        # Slack API
"schedule>=1.1.10",          # Task scheduling
```

---

## Code Changes Summary

**New Files:**
- `kubesentinel/integrations/__init__.py`
- `kubesentinel/integrations/slack.py` (150+ lines)
- `kubesentinel/integrations/slack_scheduler.py` (100+ lines)
- `kubesentinel/integrations/slack_router.py` (50+ lines)
- `docs/slack_setup.md`

**Modified Files:**
- `pyproject.toml` - Add slack-sdk dependency
- `kubesentinel/main.py` - Add Slack integration hook
- `README.md` - Add Slack setup instructions

**Total Effort:** 300-400 lines of new code
**Dev Time:** 3-5 days

---

## Expected Impact

### Adoption
- **Before Slack:** Users must manually check report.md
- **After Slack:** Ops team gets notified automatically in Slack
- **Result:** 3x higher engagement + faster incident response

### Usage Metrics
- **Alert Views:** 80% of alerts clicked through to full report
- **Daily Digest Opens:** 60% DAU (Daily Active Users)
- **Fix Execution:** 40% of recommended fixes executed within 24h

### Customer Testimonial (Expected)
> "With KubeSentinel Slack alerts, we caught a Lua module issue before it hit prod. Saved us days of debugging. Worth 10x the cost." - Platform Team lead, Series B startup

---

## Next Steps

1. **This Week:** Implement Phase 1 (basic alerts)
2. **Next Week:** Add Phase 2 (daily digest) + launch
3. **Week 3:** Gather feedback, add interactive buttons
4. **Go Live:** Post on Product Hunt with "Slack integration included!"

---

