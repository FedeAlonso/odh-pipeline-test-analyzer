#!/usr/bin/env python3
"""
Inject deep analysis findings into an existing HTML report.

Reads a markdown snippet (the deep analysis section) and injects it as styled HTML
into the existing report before the <footer> tag. Preserves all embedded screenshots,
videos, and JavaScript from the original HTML.

Usage:
  python scripts/inject_deep_analysis.py <html_report> <deep_analysis_md>

  # Or pipe markdown via stdin:
  echo "## Deep Analysis\n..." | python scripts/inject_deep_analysis.py <html_report> -

  # Also updates the MD report (appends if not already present):
  python scripts/inject_deep_analysis.py <html_report> <deep_analysis_md> --update-md <md_report>

Example:
  python scripts/inject_deep_analysis.py \
      reports/current/RHOAI/latest-build-1590.html \
      /tmp/deep_analysis.md \
      --update-md reports/current/RHOAI/latest-build-1590.md
  # Upload updated reports to Jira ticket (deletes old attachments first):
  python scripts/inject_deep_analysis.py \\
      reports/current/RHOAI/latest-build-1590.html \\
      /tmp/deep_analysis.md \\
      --update-md reports/current/RHOAI/latest-build-1590.md \\
      --jira-ticket RHOAIENG-72291
"""
import os
import re
import sys
from pathlib import Path


def md_to_html(md_text: str) -> str:
    """Convert markdown to styled HTML matching the report's dark theme.

    Groups content under ### headings into card divs for visual consistency
    with the rest of the report.
    """
    lines = md_text.strip().split("\n")
    html_lines = []
    list_tag = None  # "ul" or "ol" when inside a list, None otherwise
    in_card = False

    def close_list():
        nonlocal list_tag
        if list_tag:
            html_lines.append(f"</{list_tag}>")
            list_tag = None

    for line in lines:
        stripped = line.strip()

        if stripped in ("---", "***", "___"):
            continue

        if not stripped:
            close_list()
            continue

        # ## heading — section title (close any open card)
        if stripped.startswith("## "):
            close_list()
            if in_card:
                html_lines.append("</div>")
                in_card = False
            html_lines.append(f'<h2 style="font-size:1.3rem;border-bottom:1px solid var(--border);padding-bottom:.4rem;">{_inline(stripped[3:])}</h2>')
            continue

        # ### heading — starts a new card
        if stripped.startswith("### "):
            close_list()
            if in_card:
                html_lines.append("</div>")
            html_lines.append(
                '<div style="background:var(--card);border:1px solid var(--border);'
                'border-left:4px solid var(--orange);border-radius:10px;padding:1rem 1.2rem;margin:.8rem 0;">'
            )
            html_lines.append(f'<h3 style="margin:0 0 .6rem;font-size:1.1rem;color:var(--orange);">{_inline(stripped[4:])}</h3>')
            in_card = True
            continue

        # #### heading — sub-heading inside card
        if stripped.startswith("#### "):
            close_list()
            html_lines.append(f'<h4 style="margin:.8rem 0 .3rem;font-size:0.95rem;color:var(--text2);">{_inline(stripped[5:])}</h4>')
            continue

        # Numbered items (1. 2. 3.)
        num_match = re.match(r"^(\d+)\.\s+(.*)", stripped)
        if num_match:
            if list_tag and list_tag != "ol":
                close_list()
            if not list_tag:
                html_lines.append('<ol style="padding-left:1.5rem;margin:.4rem 0;font-size:0.9rem;">')
                list_tag = "ol"
            html_lines.append(f"<li>{_inline(num_match.group(2))}</li>")
            continue

        # Unordered list items
        if stripped.startswith("- ") or stripped.startswith("* "):
            if list_tag and list_tag != "ul":
                close_list()
            if not list_tag:
                html_lines.append('<ul style="padding-left:1.5rem;margin:.4rem 0;font-size:0.9rem;">')
                list_tag = "ul"
            html_lines.append(f"<li>{_inline(stripped[2:])}</li>")
            continue

        # Regular paragraph
        close_list()
        html_lines.append(f'<p style="margin:.3rem 0;font-size:0.9rem;">{_inline(stripped)}</p>')

    close_list()
    if in_card:
        html_lines.append("</div>")

    return "\n".join(html_lines)


def _inline(text: str) -> str:
    """Convert inline markdown: **bold**, `code`, [links](url)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(
        r"`(.+?)`",
        r'<code style="background:var(--bg2);padding:1px 5px;border-radius:3px;font-size:0.85em;">\1</code>',
        text,
    )
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2" style="color:var(--blue);">\1</a>', text)
    return text


def inject(html_content: str, deep_analysis_md: str) -> str:
    """Inject deep analysis HTML into the report before the <footer> tag."""
    deep_html = md_to_html(deep_analysis_md)

    section = f"""
    <section class="section" id="deep-analysis" style="border-top:3px solid var(--orange);margin-top:2rem;padding-top:1.5rem;">
      {deep_html}
    </section>
  """

    # Remove any previously injected deep analysis
    html_content = re.sub(
        r'\s*<section class="section" id="deep-analysis".*?</section>\s*',
        "\n",
        html_content,
        flags=re.DOTALL,
    )

    # Inject before <footer>
    footer_pattern = r"(\s*<footer\s)"
    match = re.search(footer_pattern, html_content)
    if match:
        pos = match.start()
        return html_content[:pos] + section + html_content[pos:]

    # Fallback: inject before </body>
    body_pattern = r"(\s*</body>)"
    match = re.search(body_pattern, html_content)
    if match:
        pos = match.start()
        return html_content[:pos] + section + html_content[pos:]

    # Last resort: append
    return html_content + section


def _get_arg(flag: str) -> str | None:
    """Get the value of a CLI flag like --flag value."""
    if flag in sys.argv:
        idx = sys.argv.index(flag)
        if idx + 1 < len(sys.argv):
            return sys.argv[idx + 1]
    return None


def _load_env():
    """Load .env file if present (for local runs where vars aren't exported)."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def upload_to_jira(ticket_key: str, files: list[Path]):
    """Delete old attachments with same filenames, then upload new ones."""
    try:
        import httpx
    except ImportError:
        print("⚠️  httpx not installed, skipping Jira upload", file=sys.stderr)
        return

    jira_url = os.getenv("JIRA_URL", "").rstrip("/")
    jira_user = os.getenv("JIRA_USER", "")
    jira_token = os.getenv("JIRA_TOKEN", "")

    if not all([jira_url, jira_user, jira_token]):
        print("⚠️  JIRA_URL/JIRA_USER/JIRA_TOKEN not set, skipping Jira upload")
        return

    auth = (jira_user, jira_token)
    filenames = {f.name for f in files}

    ssl_verify = os.getenv("SSL_VERIFY", "true").lower() == "true"
    with httpx.Client(verify=ssl_verify, timeout=120.0) as client:
        # Get existing attachments
        resp = client.get(
            f"{jira_url}/rest/api/3/issue/{ticket_key}?fields=attachment",
            auth=auth,
        )
        if resp.status_code != 200:
            print(f"⚠️  Failed to fetch ticket {ticket_key}: {resp.status_code}")
            return

        existing = resp.json().get("fields", {}).get("attachment", [])

        # Delete old attachments with same filenames
        for att in existing:
            if att["filename"] in filenames:
                del_resp = client.delete(
                    f"{jira_url}/rest/api/3/attachment/{att['id']}",
                    auth=auth,
                )
                if del_resp.status_code in (200, 204):
                    print(f"   🗑️  Deleted old {att['filename']} ({att['id']})")
                else:
                    print(f"   ⚠️  Failed to delete {att['filename']}: {del_resp.status_code}")

        # Upload new files
        for filepath in files:
            if not filepath.exists():
                continue
            with open(filepath, "rb") as f:
                resp = client.post(
                    f"{jira_url}/rest/api/3/issue/{ticket_key}/attachments",
                    auth=auth,
                    headers={"X-Atlassian-Token": "no-check", "Accept": "application/json"},
                    files={"file": (filepath.name, f)},
                )
            if resp.status_code in (200, 201):
                print(f"   ✅ Uploaded {filepath.name} to {ticket_key}")
            else:
                print(f"   ⚠️  Upload {filepath.name} failed: {resp.status_code}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    html_path = Path(sys.argv[1])
    md_input = sys.argv[2]
    update_md_path = _get_arg("--update-md")
    jira_ticket = _get_arg("--jira-ticket")

    update_md = Path(update_md_path) if update_md_path else None

    # Read deep analysis markdown
    if md_input == "-":
        deep_md = sys.stdin.read()
    else:
        deep_md = Path(md_input).read_text()

    if not deep_md.strip():
        print("Error: deep analysis content is empty", file=sys.stderr)
        sys.exit(1)

    # Inject into HTML
    html_content = html_path.read_text()
    updated_html = inject(html_content, deep_md)
    html_path.write_text(updated_html)
    print(f"✅ Injected deep analysis into {html_path}")

    # Update MD report if requested
    if update_md and update_md.exists():
        md_content = update_md.read_text()
        if "## 🔬 Deep Analysis" not in md_content and "## Deep Analysis" not in md_content:
            md_content = md_content.rstrip() + "\n\n" + deep_md + "\n"
            update_md.write_text(md_content)
            print(f"✅ Appended deep analysis to {update_md}")
        else:
            print(f"⏭ MD report already contains deep analysis, skipping")

    # Upload to Jira if requested
    if jira_ticket:
        _load_env()
        upload_files = [html_path]
        if update_md:
            upload_files.append(update_md)
        print(f"\n📤 Uploading to Jira {jira_ticket}...")
        upload_to_jira(jira_ticket, upload_files)


if __name__ == "__main__":
    main()
