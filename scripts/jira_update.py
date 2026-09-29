#!/usr/bin/env python3
"""
CLI tool for updating Jira tickets (summary, description, comments, labels).

Reads credentials from .env (JIRA_URL, JIRA_USER, JIRA_TOKEN).

Usage:
    # Update summary
    python scripts/jira_update.py RHOAIENG-97002 --summary "New title"

    # Update description from a markdown file
    python scripts/jira_update.py RHOAIENG-97002 --description-file /tmp/desc.md

    # Update description from inline markdown
    python scripts/jira_update.py RHOAIENG-97002 --description "## Heading\nSome text"

    # Add a comment from a markdown file
    python scripts/jira_update.py RHOAIENG-97002 --comment-file /tmp/comment.md

    # Add a comment from inline markdown
    python scripts/jira_update.py RHOAIENG-97002 --comment "Analysis complete. See report."

    # Add/remove labels
    python scripts/jira_update.py RHOAIENG-97002 --add-labels ci-slack,interim --remove-labels old-label

    # Combine operations
    python scripts/jira_update.py RHOAIENG-97002 --summary "New title" --description-file /tmp/desc.md --add-labels ci-slack
"""
import argparse
import os
import re
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).parent.parent
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)

JIRA_URL = os.getenv("JIRA_URL", "https://issues.redhat.com").rstrip("/")
JIRA_USER = os.getenv("JIRA_USER", "")
JIRA_TOKEN = os.getenv("JIRA_TOKEN", "")
SSL_VERIFY = os.getenv("SSL_VERIFY", "true").lower() == "true"


def _auth():
    if JIRA_USER:
        return httpx.BasicAuth(JIRA_USER, JIRA_TOKEN)
    return None


def _headers():
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if not JIRA_USER:
        headers["Authorization"] = f"Bearer {JIRA_TOKEN}"
    return headers


# ---------------------------------------------------------------------------
# Markdown → ADF converter
# ---------------------------------------------------------------------------

def _parse_inline(text: str) -> list:
    """Parse inline markdown (bold, italic, code, links) into ADF content nodes."""
    nodes = []
    i = 0
    while i < len(text):
        # Link: [text](url)
        m = re.match(r"\[([^\]]+)\]\(([^)]+)\)", text[i:])
        if m:
            nodes.append({
                "type": "text",
                "text": m.group(1),
                "marks": [{"type": "link", "attrs": {"href": m.group(2)}}],
            })
            i += m.end()
            continue

        # Inline code: `code`
        m = re.match(r"`([^`]+)`", text[i:])
        if m:
            nodes.append({"type": "text", "text": m.group(1), "marks": [{"type": "code"}]})
            i += m.end()
            continue

        # Bold: **text** or __text__
        m = re.match(r"\*\*(.+?)\*\*|__(.+?)__", text[i:])
        if m:
            bold_text = m.group(1) or m.group(2)
            nodes.append({"type": "text", "text": bold_text, "marks": [{"type": "strong"}]})
            i += m.end()
            continue

        # Italic: *text* or _text_ (but not inside bold)
        m = re.match(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", text[i:])
        if m:
            italic_text = m.group(1) or m.group(2)
            nodes.append({"type": "text", "text": italic_text, "marks": [{"type": "em"}]})
            i += m.end()
            continue

        # Plain text — consume until next special char
        end = i + 1
        while end < len(text) and text[end] not in "[`*_":
            end += 1
        nodes.append({"type": "text", "text": text[i:end]})
        i = end

    return nodes or [{"type": "text", "text": text}]


def markdown_to_adf(markdown: str) -> dict:
    """Convert markdown text to Atlassian Document Format (ADF)."""
    content = []
    lines = markdown.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Code block: ```lang ... ```
        if line.startswith("```"):
            lang = line[3:].strip() or None
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            attrs = {}
            if lang:
                attrs["language"] = lang
            content.append({
                "type": "codeBlock",
                "attrs": attrs,
                "content": [{"type": "text", "text": "\n".join(code_lines)}],
            })
            i += 1
            continue

        # Headings
        hm = re.match(r"^(#{1,6})\s+(.+)$", line)
        if hm:
            level = len(hm.group(1))
            content.append({
                "type": "heading",
                "attrs": {"level": level},
                "content": _parse_inline(hm.group(2)),
            })
            i += 1
            continue

        # Horizontal rule
        if re.match(r"^-{3,}$|^\*{3,}$", line.strip()):
            content.append({"type": "rule"})
            i += 1
            continue

        # Bullet list — gather consecutive lines
        if re.match(r"^[-*]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i]):
                item_text = re.sub(r"^[-*]\s+", "", lines[i])
                items.append({
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _parse_inline(item_text)}],
                })
                i += 1
            content.append({"type": "bulletList", "content": items})
            continue

        # Ordered list — gather consecutive lines
        if re.match(r"^\d+\.\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i]):
                item_text = re.sub(r"^\d+\.\s+", "", lines[i])
                items.append({
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _parse_inline(item_text)}],
                })
                i += 1
            content.append({"type": "orderedList", "attrs": {"order": 1}, "content": items})
            continue

        # Table: | col | col |
        if line.strip().startswith("|") and "|" in line[1:]:
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row_text = lines[i].strip()
                # Skip separator rows (|---|---|)
                if re.match(r"^\|[\s\-:|]+\|$", row_text):
                    i += 1
                    continue
                cells = [c.strip() for c in row_text.split("|")[1:-1]]
                is_header = not rows
                cell_type = "tableHeader" if is_header else "tableCell"
                row_cells = []
                for cell in cells:
                    row_cells.append({
                        "type": cell_type,
                        "content": [{"type": "paragraph", "content": _parse_inline(cell)}],
                    })
                rows.append({"type": "tableRow", "content": row_cells})
                i += 1
            if rows:
                content.append({
                    "type": "table",
                    "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
                    "content": rows,
                })
            continue

        # Empty line — skip
        if not line.strip():
            i += 1
            continue

        # Regular paragraph
        content.append({"type": "paragraph", "content": _parse_inline(line)})
        i += 1

    return {
        "type": "doc",
        "version": 1,
        "content": content or [{"type": "paragraph", "content": [{"type": "text", "text": "(empty)"}]}],
    }


# ---------------------------------------------------------------------------
# Jira API operations
# ---------------------------------------------------------------------------

def update_issue(issue_key: str, fields: dict):
    """Update fields on a Jira issue."""
    resp = httpx.put(
        f"{JIRA_URL}/rest/api/3/issue/{issue_key}",
        headers=_headers(),
        auth=_auth(),
        json={"fields": fields},
        verify=SSL_VERIFY,
        timeout=30.0,
    )
    if resp.status_code == 204:
        print(f"Updated {issue_key}: {', '.join(fields.keys())}")
    else:
        print(f"Failed to update {issue_key}: {resp.status_code}", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)


def add_comment(issue_key: str, markdown: str):
    """Add a comment (markdown converted to ADF) to a Jira issue."""
    adf = markdown_to_adf(markdown)
    resp = httpx.post(
        f"{JIRA_URL}/rest/api/3/issue/{issue_key}/comment",
        headers=_headers(),
        auth=_auth(),
        json={"body": adf},
        verify=SSL_VERIFY,
        timeout=30.0,
    )
    if resp.status_code in (200, 201):
        print(f"Comment added to {issue_key}")
    else:
        print(f"Failed to add comment to {issue_key}: {resp.status_code}", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)


def modify_labels(issue_key: str, add: list[str] = None, remove: list[str] = None):
    """Add or remove labels on a Jira issue."""
    update = []
    for label in (add or []):
        update.append({"add": label})
    for label in (remove or []):
        update.append({"remove": label})
    if not update:
        return

    resp = httpx.put(
        f"{JIRA_URL}/rest/api/3/issue/{issue_key}",
        headers=_headers(),
        auth=_auth(),
        json={"update": {"labels": update}},
        verify=SSL_VERIFY,
        timeout=30.0,
    )
    if resp.status_code == 204:
        added = ", ".join(add or [])
        removed = ", ".join(remove or [])
        parts = []
        if added:
            parts.append(f"added [{added}]")
        if removed:
            parts.append(f"removed [{removed}]")
        print(f"Labels on {issue_key}: {', '.join(parts)}")
    else:
        print(f"Failed to update labels on {issue_key}: {resp.status_code}", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Update Jira tickets")
    parser.add_argument("issue_key", help="Jira issue key (e.g., RHOAIENG-97002)")
    parser.add_argument("--summary", help="New summary/title")
    parser.add_argument("--description", help="New description (inline markdown)")
    parser.add_argument("--description-file", help="New description from a markdown file")
    parser.add_argument("--comment", help="Add a comment (inline markdown)")
    parser.add_argument("--comment-file", help="Add a comment from a markdown file")
    parser.add_argument("--add-labels", help="Comma-separated labels to add")
    parser.add_argument("--remove-labels", help="Comma-separated labels to remove")
    args = parser.parse_args()

    if not JIRA_TOKEN:
        print("JIRA_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    # Build fields update
    fields = {}
    if args.summary:
        fields["summary"] = args.summary

    desc_md = None
    if args.description_file:
        desc_md = Path(args.description_file).read_text()
    elif args.description:
        desc_md = args.description
    if desc_md:
        fields["description"] = markdown_to_adf(desc_md)

    if fields:
        update_issue(args.issue_key, fields)

    # Labels
    add_labels = [l.strip() for l in args.add_labels.split(",") if l.strip()] if args.add_labels else []
    remove_labels = [l.strip() for l in args.remove_labels.split(",") if l.strip()] if args.remove_labels else []
    if add_labels or remove_labels:
        modify_labels(args.issue_key, add_labels, remove_labels)

    # Comment
    comment_md = None
    if args.comment_file:
        comment_md = Path(args.comment_file).read_text()
    elif args.comment:
        comment_md = args.comment
    if comment_md:
        add_comment(args.issue_key, comment_md)


if __name__ == "__main__":
    main()
