#!/usr/bin/env python3
"""PreToolUse guard for Search-Jobs. Enforced by Claude Code itself, not by instructions:
it blocks every way an agent session could send a message in the owner's name or forge an approval.

Exit code 2 = the tool call is refused and the reason is shown to the agent."""
import json
import re
import sys

BLOCKED_TOOLS = {
    # Gmail: drafts only. Sending, replying and forwarding stay with the owner.
    "mcp__Gmail__send_message": "Sending email is the owner's job. Create a draft instead.",
    "mcp__Gmail__reply": "Replying sends email. Create a draft instead.",
    "mcp__Gmail__forward": "Forwarding sends email. Create a draft instead.",
    "mcp__Gmail__update_draft": "Drafts are created once from an approved packet; changing one would bypass approval.",
    # GitHub: these create web-flow-signed commits or merges that could imitate an owner approval.
    "mcp__github__create_or_update_file": "Writing files through the GitHub API is disabled here (approval forgery risk). Use git.",
    "mcp__github__push_files": "Writing files through the GitHub API is disabled here (approval forgery risk). Use git.",
    "mcp__github__delete_file": "Writing files through the GitHub API is disabled here. Use git.",
    "mcp__github__merge_pull_request": "Merging through the API is disabled here (approval forgery risk). The owner merges.",
    "mcp__github__update_pull_request_branch": "Disabled here (creates a GitHub-signed merge commit).",
    "mcp__github__enable_pr_auto_merge": "Disabled here: the owner merges.",
}

BASH_RULES = [
    (re.compile(r"approvals/"), re.compile(r"(>|>>|\btee\b|\bcp\b|\bmv\b|\btouch\b|\bgit\s+(add|rm|mv)\b|\bln\b|\bmkdir\b|write_text|open\()"),
     "Approvals are written only by the owner on github.com. No session may create, copy or stage files in approvals/."),
    (re.compile(r"api\.github\.com|/repos/[^/\s]+/[^/\s]+/(contents|git/|merges|pulls/\d+/merge)"),
     re.compile(r"-X\s*(PUT|POST|PATCH|DELETE)|--request\s+(PUT|POST|PATCH|DELETE)|method\s*=\s*['\"](PUT|POST|PATCH|DELETE)|\s-d\s|--data"),
     "Writing through the GitHub REST API is disabled here (approval forgery risk). Use git."),
    (re.compile(r"\bgh\s+api\b"), re.compile(r"-X\s*(PUT|POST|PATCH|DELETE)|--method\s+(PUT|POST|PATCH|DELETE)|\s-f\s|\s-F\s"),
     "Writing through the GitHub API is disabled here."),
    (re.compile(r"\b(sendmail|ssmtp|msmtp|mailx?|smtplib)\b"), re.compile(r".*"),
     "This system never sends email. It prepares Gmail drafts for the owner."),
]


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    tool = event.get("tool_name", "")
    if tool in BLOCKED_TOOLS:
        print(f"Search-Jobs guard: {BLOCKED_TOOLS[tool]}", file=sys.stderr)
        return 2
    if tool == "Bash":
        cmd = (event.get("tool_input") or {}).get("command", "")
        for scope, action, reason in BASH_RULES:
            if scope.search(cmd) and action.search(cmd):
                print(f"Search-Jobs guard: {reason}", file=sys.stderr)
                return 2
    if tool in ("Write", "Edit", "NotebookEdit"):
        path = (event.get("tool_input") or {}).get("file_path", "")
        if re.search(r"(^|/)approvals/", path):
            print("Search-Jobs guard: approvals/ is written only by the owner on github.com.", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
