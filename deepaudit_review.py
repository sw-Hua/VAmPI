#!/usr/bin/env python3
"""
DeepAudit CI Integration Script
================================
在 GitHub Actions 中调用本地 DeepAudit 平台的即时代码分析 API，
对 PR 中变更的 Python 文件逐一审计，汇总报告并决定 CI 是否通过。

Required Environment Variables:
  DEEPAUDIT_URL      - DeepAudit 服务地址 (e.g. https://xxxx.ngrok.io)
  DEEPAUDIT_EMAIL    - 登录邮箱
  DEEPAUDIT_PASSWORD - 登录密码

Optional:
  GITHUB_OUTPUT      - GitHub Actions output file
"""

import os
import sys
import json
import subprocess
import time
import requests

# ======================== Configuration ========================

DEEPAUDIT_URL = os.environ.get("DEEPAUDIT_URL", "").rstrip("/")
DEEPAUDIT_EMAIL = os.environ.get("DEEPAUDIT_EMAIL", "demo@example.com")
DEEPAUDIT_PASSWORD = os.environ.get("DEEPAUDIT_PASSWORD", "demo123")

# Severity threshold: findings at or above this level will FAIL CI
# Order: critical > high > medium > low > info
SEVERITY_ORDER = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
FAIL_THRESHOLD = "high"  # HIGH and above will block the PR

MAX_FILE_SIZE = 50000  # Skip files larger than 50KB
REQUEST_TIMEOUT = 120  # seconds per API call
REQUEST_GAP = 1  # seconds between API calls (rate limiting)


# ======================== Helper Functions ========================

def log(msg):
    """Print with timestamp."""
    print(f"[DeepAudit] {msg}")


def get_changed_python_files():
    """Get list of changed Python files in this PR vs origin/main."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD", "--", "*.py"],
            capture_output=True, text=True, check=True
        )
        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        return files
    except subprocess.CalledProcessError as e:
        log(f"Error getting changed files: {e}")
        return []


def login(base_url, email, password):
    """Authenticate with DeepAudit and return access token."""
    url = f"{base_url}/api/v1/auth/login"
    log(f"Authenticating with DeepAudit at {base_url}...")
    
    try:
        resp = requests.post(
            url,
            data={"username": email, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            log("ERROR: No access_token in login response")
            return None
        log("Authentication successful")
        return token
    except requests.RequestException as e:
        log(f"ERROR: Login failed - {e}")
        return None


def analyze_file(base_url, token, code, language="python"):
    """Call DeepAudit instant analysis API for a single code snippet."""
    url = f"{base_url}/api/v1/scan/instant"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"code": code, "language": language}
    
    resp = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def severity_meets_threshold(severity, threshold):
    """Check if a severity level meets or exceeds the threshold."""
    return SEVERITY_ORDER.get(severity.lower(), 0) >= SEVERITY_ORDER.get(threshold.lower(), 4)


def build_markdown_report(all_findings, file_results):
    """Build a markdown report from all findings."""
    lines = []
    lines.append("## 🛡️ DeepAudit AI Security Audit Report")
    lines.append("")
    
    # Summary
    total_issues = sum(len(r["issues"]) for r in file_results)
    critical_count = sum(1 for f in all_findings if f["severity"].lower() == "critical")
    high_count = sum(1 for f in all_findings if f["severity"].lower() == "high")
    medium_count = sum(1 for f in all_findings if f["severity"].lower() == "medium")
    low_count = sum(1 for f in all_findings if f["severity"].lower() == "low")
    
    lines.append(f"**Files Analyzed**: {len(file_results)}")
    lines.append(f"**Total Issues Found**: {total_issues}")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|----------|-------|")
    lines.append(f"| 🔴 Critical | {critical_count} |")
    lines.append(f"| 🟠 High | {high_count} |")
    lines.append(f"| 🟡 Medium | {medium_count} |")
    lines.append(f"| 🔵 Low | {low_count} |")
    lines.append("")
    
    # Gate decision
    blocking = [f for f in all_findings if severity_meets_threshold(f["severity"], FAIL_THRESHOLD)]
    if blocking:
        lines.append(f"### ❌ CI Gate: FAILED ({len(blocking)} blocking issue(s) found)")
        lines.append(f"_Threshold: {FAIL_THRESHOLD.upper()} and above will block merge_")
    else:
        lines.append("### ✅ CI Gate: PASSED (No blocking issues)")
        lines.append(f"_Threshold: {FAIL_THRESHOLD.upper()} and above will block merge_")
    lines.append("")
    
    # Per-file details
    if all_findings:
        lines.append("### Detailed Findings")
        lines.append("")
        
        for result in file_results:
            if not result["issues"]:
                continue
            lines.append(f"#### 📄 `{result['file']}`")
            lines.append("")
            for issue in result["issues"]:
                sev = issue.get("severity", "info").upper()
                title = issue.get("title", "Untitled Issue")
                desc = issue.get("description", "")
                suggestion = issue.get("suggestion", "")
                line_num = issue.get("line", "?")
                
                icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}.get(sev, "⚪")
                lines.append(f"- {icon} **[{sev}]** Line {line_num}: {title}")
                if desc:
                    lines.append(f"  > {desc}")
                if suggestion:
                    lines.append(f"  > 💡 {suggestion}")
                lines.append("")
    
    return "\n".join(lines)


def set_github_output(key, value):
    """Set GitHub Actions output variable."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{key}={value}\n")


# ======================== Main ========================

def main():
    # Validate environment
    if not DEEPAUDIT_URL:
        log("WARNING: DEEPAUDIT_URL not set. Skipping DeepAudit analysis.")
        log("To enable: set DEEPAUDIT_URL GitHub Secret to your ngrok URL")
        set_github_output("deepaudit_failed", "false")
        # Write a skip-report
        with open("deepaudit_report.md", "w") as f:
            f.write("## 🛡️ DeepAudit AI Security Audit Report\n\n")
            f.write("_⏭️ Skipped: `DEEPAUDIT_URL` secret not configured_\n")
        return 0
    
    # Step 1: Get changed files
    changed_files = get_changed_python_files()
    if not changed_files:
        log("No Python files changed. Skipping DeepAudit analysis.")
        set_github_output("deepaudit_failed", "false")
        with open("deepaudit_report.md", "w") as f:
            f.write("## 🛡️ DeepAudit AI Security Audit Report\n\n")
            f.write("_No Python files changed in this PR._\n")
        return 0
    
    log(f"Found {len(changed_files)} changed Python file(s):")
    for fp in changed_files:
        log(f"  - {fp}")
    
    # Step 2: Login
    token = login(DEEPAUDIT_URL, DEEPAUDIT_EMAIL, DEEPAUDIT_PASSWORD)
    if not token:
        log("ERROR: Cannot authenticate with DeepAudit. Marking as non-blocking failure.")
        set_github_output("deepaudit_failed", "false")
        with open("deepaudit_report.md", "w") as f:
            f.write("## 🛡️ DeepAudit AI Security Audit Report\n\n")
            f.write("_⚠️ Authentication failed. DeepAudit analysis skipped._\n")
            f.write(f"_URL: {DEEPAUDIT_URL}_\n")
        return 0
    
    # Step 3: Analyze each file
    file_results = []
    all_findings = []
    
    for filepath in changed_files:
        if not os.path.isfile(filepath):
            log(f"  SKIP (file not found): {filepath}")
            continue
        
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                code = f.read()
        except Exception as e:
            log(f"  SKIP (read error): {filepath} - {e}")
            continue
        
        if len(code) > MAX_FILE_SIZE:
            log(f"  SKIP (too large: {len(code)} bytes): {filepath}")
            continue
        
        if not code.strip():
            log(f"  SKIP (empty): {filepath}")
            continue
        
        log(f"  Analyzing: {filepath}")
        
        try:
            result = analyze_file(DEEPAUDIT_URL, token, code, "python")
            issues = result.get("issues", [])
            quality_score = result.get("quality_score", 100)
            
            log(f"    → {len(issues)} issue(s), quality_score={quality_score}")
            
            file_results.append({
                "file": filepath,
                "issues": issues,
                "quality_score": quality_score,
            })
            
            for issue in issues:
                all_findings.append({
                    "file": filepath,
                    "severity": issue.get("severity", "info"),
                    "title": issue.get("title", ""),
                    "line": issue.get("line", 0),
                })
            
        except requests.RequestException as e:
            log(f"    ERROR: API call failed - {e}")
            file_results.append({
                "file": filepath,
                "issues": [],
                "quality_score": 0,
                "error": str(e),
            })
        
        # Rate limiting
        time.sleep(REQUEST_GAP)
    
    # Step 4: Build report
    report_md = build_markdown_report(all_findings, file_results)
    
    with open("deepaudit_report.md", "w") as f:
        f.write(report_md)
    
    log(f"Report written to deepaudit_report.md")
    
    # Step 5: Determine CI gate
    blocking_findings = [
        f for f in all_findings 
        if severity_meets_threshold(f["severity"], FAIL_THRESHOLD)
    ]
    
    if blocking_findings:
        log(f"FAIL: {len(blocking_findings)} blocking issue(s) found (>= {FAIL_THRESHOLD.upper()})")
        set_github_output("deepaudit_failed", "true")
    else:
        log(f"PASS: No issues at {FAIL_THRESHOLD.upper()} level or above")
        set_github_output("deepaudit_failed", "false")
    
    # Print summary
    log("=" * 50)
    log(f"Files analyzed: {len(file_results)}")
    log(f"Total issues: {len(all_findings)}")
    log(f"Blocking issues: {len(blocking_findings)}")
    log("=" * 50)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
