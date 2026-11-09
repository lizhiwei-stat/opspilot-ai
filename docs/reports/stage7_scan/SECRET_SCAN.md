# Secret scan summary

```json
{
  "tool": "gitleaks 8.21.2",
  "date": "2026-11-09",
  "working_tree_findings": 1,
  "history_findings": 1,
  "findings": [
    {
      "rule": "generic-api-key",
      "file": "tests/conftest.py",
      "classification": "synthetic_test_jwt_secret",
      "live_cloud_key": false,
      "note": "TEST_SECRET used only by unit tests; not a DashScope/production key. Present in git history. No history rewrite performed."
    }
  ],
  "pattern_review": "Other sk-/api_key hits are FAKE_KEY / policy fixtures in tests/.env never committed.",
  "personal_data": "No personal gmail/phone PII found in tracked sources (demo emails *@opspilot.local only)."
}
```
