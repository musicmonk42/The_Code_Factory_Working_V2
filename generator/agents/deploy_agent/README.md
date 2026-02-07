<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

{
  "languages": ["python", "javascript", "typescript", "go", "rust", "java"],
  "target_language": "auto",
  "pipeline_steps": ["lint", "test", "security_scan", "semantic", "fix"],
  "enable_e2e_tests": false,
  "enable_stress_tests": false,
  "enable_containerization": true,
  "vulnerability_scan_tools": {
    "python": ["bandit", "semgrep"],
    "javascript": ["npm_audit", "semgrep"],
    "typescript": ["npm_audit", "semgrep"],
    "go": ["gosec", "semgrep"],
    "rust": ["cargo-audit", "semgrep"],
    "java": ["checkstyle", "semgrep"]
  },
  "tool_timeout_seconds": 300,
  "explainability": true,
  "multi_modal": true,
  "chain_of_thought": true,
  "llm_providers": ["grok", "openai", "claude", "gemini", "local"],
  "rag_enabled": true
}