#!/usr/bin/env python3
"""
Security Audit Script for self_fixing_engineer Module

Performs comprehensive security checks including:
- SQL injection vulnerabilities
- Hardcoded secrets and credentials
- Insecure configurations
- Authentication and authorization issues
- Input validation gaps
- Dependency vulnerabilities
"""

import re
from pathlib import Path
from typing import Dict, List


class SecurityAuditor:
    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.findings = {
            "critical": [],
            "high": [],
            "medium": [],
            "low": [],
            "info": [],
        }

    def audit_file(self, filepath: Path) -> List[Dict]:
        """Audit a single Python file for security issues."""
        findings = []

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # Check for hardcoded secrets
            secret_patterns = [
                (r'password\s*=\s*["\'][^"\']+["\']', "Hardcoded password"),
                (r'api_key\s*=\s*["\'][^"\']+["\']', "Hardcoded API key"),
                (r'secret\s*=\s*["\'][^"\']+["\']', "Hardcoded secret"),
                (r'token\s*=\s*["\'][^"\']+["\']', "Hardcoded token"),
                (
                    r'AWS_SECRET_ACCESS_KEY\s*=\s*["\'][^"\']+["\']',
                    "Hardcoded AWS secret",
                ),
            ]

            for pattern, issue in secret_patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    line_num = content[: match.start()].count("\n") + 1
                    findings.append(
                        {
                            "severity": "critical",
                            "issue": issue,
                            "file": str(filepath.relative_to(self.base_path)),
                            "line": line_num,
                            "context": match.group(0)[:50],
                        }
                    )

            # Check for SQL injection vulnerabilities
            sql_patterns = [
                (
                    r"execute\([^)]*%[sd][^)]*\)",
                    "Potential SQL injection (string formatting)",
                ),
                (
                    r"execute\([^)]*\.format\([^)]*\)",
                    "Potential SQL injection (format)",
                ),
                (r"execute\([^)]*\+[^)]*\)", "Potential SQL injection (concatenation)"),
            ]

            for pattern, issue in sql_patterns:
                if "execute" in content.lower():
                    matches = re.finditer(pattern, content)
                    for match in matches:
                        line_num = content[: match.start()].count("\n") + 1
                        findings.append(
                            {
                                "severity": "high",
                                "issue": issue,
                                "file": str(filepath.relative_to(self.base_path)),
                                "line": line_num,
                                "context": match.group(0)[:50],
                            }
                        )

            # Check for insecure configurations
            if "DEBUG = True" in content or "debug=True" in content:
                findings.append(
                    {
                        "severity": "medium",
                        "issue": "Debug mode enabled",
                        "file": str(filepath.relative_to(self.base_path)),
                        "line": content.index("debug") // len(content.split("\n")[0])
                        + 1,
                        "context": "DEBUG=True",
                    }
                )

            # Check for weak crypto
            weak_crypto_patterns = [
                (r"hashlib\.md5", "Weak hash algorithm MD5"),
                (r"hashlib\.sha1", "Weak hash algorithm SHA1"),
                (r"DES\(", "Weak encryption algorithm DES"),
            ]

            for pattern, issue in weak_crypto_patterns:
                matches = re.finditer(pattern, content)
                for match in matches:
                    line_num = content[: match.start()].count("\n") + 1
                    findings.append(
                        {
                            "severity": "medium",
                            "issue": issue,
                            "file": str(filepath.relative_to(self.base_path)),
                            "line": line_num,
                            "context": match.group(0),
                        }
                    )

            # Check for eval/exec usage
            dangerous_functions = ["eval(", "exec(", "__import__("]
            for func in dangerous_functions:
                if func in content:
                    line_num = content.index(func) // len(content.split("\n")[0]) + 1
                    findings.append(
                        {
                            "severity": "high",
                            "issue": f"Dangerous function {func[:-1]} usage",
                            "file": str(filepath.relative_to(self.base_path)),
                            "line": line_num,
                            "context": func,
                        }
                    )

            # Check for missing input validation in FastAPI/Flask routes
            if "@app." in content or "@router." in content:
                if "Depends(" not in content and "validate" not in content.lower():
                    findings.append(
                        {
                            "severity": "medium",
                            "issue": "API routes may lack input validation",
                            "file": str(filepath.relative_to(self.base_path)),
                            "line": 0,
                            "context": "Review route handlers",
                        }
                    )

            # Check for CORS configuration
            if "CORS" in content and 'allow_origins=["*"]' in content:
                findings.append(
                    {
                        "severity": "high",
                        "issue": "Permissive CORS configuration",
                        "file": str(filepath.relative_to(self.base_path)),
                        "line": content.index("allow_origins")
                        // len(content.split("\n")[0])
                        + 1,
                        "context": 'allow_origins=["*"]',
                    }
                )

        except Exception as e:
            findings.append(
                {
                    "severity": "info",
                    "issue": f"Error auditing file: {e}",
                    "file": str(filepath.relative_to(self.base_path)),
                    "line": 0,
                    "context": str(e),
                }
            )

        return findings

    def audit_dependencies(self) -> List[Dict]:
        """Audit dependencies for known vulnerabilities."""
        findings = []
        req_file = self.base_path / "requirements.txt"

        if not req_file.exists():
            return findings

        # Known vulnerable packages (example list)
        vulnerable_packages = {
            "django": {"<3.2.20": "CVE-2023-XXXX"},
            "flask": {"<2.3.0": "Multiple vulnerabilities"},
            "pillow": {"<10.0.0": "Image processing vulnerabilities"},
        }

        try:
            with open(req_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "==" in line:
                        pkg, version = line.split("==")
                        pkg = pkg.strip().lower()
                        version = version.strip()

                        if pkg in vulnerable_packages:
                            findings.append(
                                {
                                    "severity": "high",
                                    "issue": f"Potentially vulnerable package: {pkg}=={version}",
                                    "file": "requirements.txt",
                                    "line": 0,
                                    "context": vulnerable_packages[pkg],
                                }
                            )
        except Exception as e:
            findings.append(
                {
                    "severity": "info",
                    "issue": f"Error auditing dependencies: {e}",
                    "file": "requirements.txt",
                    "line": 0,
                    "context": str(e),
                }
            )

        return findings

    def audit_authentication(self) -> List[Dict]:
        """Audit authentication and authorization implementations."""
        findings = []

        # Check for JWT secret management
        for py_file in self.base_path.rglob("*.py"):
            if "__pycache__" in str(py_file) or "test" in py_file.name.lower():
                continue

            try:
                with open(py_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Check JWT implementation
                if "jwt.encode" in content or "jwt.decode" in content:
                    if "JWT_SECRET" not in content and "SECRET_KEY" not in content:
                        findings.append(
                            {
                                "severity": "high",
                                "issue": "JWT implementation may lack proper secret management",
                                "file": str(py_file.relative_to(self.base_path)),
                                "line": 0,
                                "context": "Review JWT secret handling",
                            }
                        )

                # Check for missing authentication decorators
                if "@app.post" in content or "@app.get" in content:
                    if "Depends(" not in content and "require_auth" not in content:
                        findings.append(
                            {
                                "severity": "medium",
                                "issue": "API endpoint may lack authentication",
                                "file": str(py_file.relative_to(self.base_path)),
                                "line": 0,
                                "context": "Review endpoint authentication",
                            }
                        )
            except (OSError, IOError, UnicodeDecodeError):
                # Skip files that cannot be read
                pass

        return findings

    def run_audit(self) -> Dict:
        """Run complete security audit."""
        print("Starting security audit...")

        # Audit all Python files
        for py_file in self.base_path.rglob("*.py"):
            if "__pycache__" in str(py_file) or "/tests/" in str(py_file):
                continue

            file_findings = self.audit_file(py_file)
            for finding in file_findings:
                severity = finding["severity"]
                self.findings[severity].append(finding)

        # Audit dependencies
        dep_findings = self.audit_dependencies()
        for finding in dep_findings:
            severity = finding["severity"]
            self.findings[severity].append(finding)

        # Audit authentication
        auth_findings = self.audit_authentication()
        for finding in auth_findings:
            severity = finding["severity"]
            self.findings[severity].append(finding)

        return self.findings

    def generate_report(self) -> str:
        """Generate security audit report."""
        report = []
        report.append("=" * 80)
        report.append("SECURITY AUDIT REPORT")
        report.append("=" * 80)
        report.append("")

        total_findings = sum(len(findings) for findings in self.findings.values())
        report.append(f"Total Findings: {total_findings}")
        report.append(f"  Critical: {len(self.findings['critical'])}")
        report.append(f"  High: {len(self.findings['high'])}")
        report.append(f"  Medium: {len(self.findings['medium'])}")
        report.append(f"  Low: {len(self.findings['low'])}")
        report.append(f"  Info: {len(self.findings['info'])}")
        report.append("")

        for severity in ["critical", "high", "medium", "low"]:
            if self.findings[severity]:
                report.append(f"\n{'='*80}")
                report.append(f"{severity.upper()} SEVERITY FINDINGS")
                report.append(f"{'='*80}\n")

                for i, finding in enumerate(self.findings[severity][:10], 1):
                    report.append(f"{i}. {finding['issue']}")
                    report.append(f"   File: {finding['file']}")
                    if finding["line"]:
                        report.append(f"   Line: {finding['line']}")
                    report.append(f"   Context: {finding['context']}")
                    report.append("")

                if len(self.findings[severity]) > 10:
                    report.append(
                        f"   ... and {len(self.findings[severity]) - 10} more findings"
                    )
                    report.append("")

        report.append("\n" + "=" * 80)
        report.append("RECOMMENDATIONS")
        report.append("=" * 80)
        report.append("""
1. Remove all hardcoded secrets and use environment variables or secret managers
2. Implement parameterized queries to prevent SQL injection
3. Add input validation to all API endpoints
4. Configure proper CORS policies
5. Review and strengthen authentication mechanisms
6. Update vulnerable dependencies
7. Disable debug mode in production
8. Use strong cryptographic algorithms (SHA-256+, AES-256)
9. Implement rate limiting on all public APIs
10. Add security headers (CSP, X-Frame-Options, etc.)
        """)

        return "\n".join(report)


if __name__ == "__main__":
    auditor = SecurityAuditor(
        "/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/self_fixing_engineer"
    )
    findings = auditor.run_audit()
    report = auditor.generate_report()

    print(report)

    # Save report
    output_file = Path(
        "/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2/self_fixing_engineer/SECURITY_AUDIT_REPORT.md"
    )
    with open(output_file, "w") as f:
        f.write(report)

    print(f"\n\nReport saved to: {output_file}")
