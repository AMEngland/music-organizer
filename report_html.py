#!/usr/bin/env python3
"""Self-contained HTML report for a run.

Replaces the scatter of text reports with one file you can actually navigate:
collapsible duplicate groups, a proposed-deletion list, the low-confidence
queue, and the artist names that were folded together. No external assets, so
it works from a file:// URL with no network.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = ["render_report"]

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1a1a1a; --muted: #666; --line: #e0e0e0;
  --card: #f7f7f8; --accent: #2563eb; --warn: #b45309; --bad: #b91c1c;
  --good: #15803d;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16181c; --fg: #e6e6e6; --muted: #9aa0a6; --line: #2c2f36;
    --card: #1e2127; --accent: #7aa2f7; --warn: #e0a458; --bad: #f87171;
    --good: #6ee7a8;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg); color: var(--fg);
  font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 .25rem; }
h2 { font-size: 1.15rem; margin: 2.5rem 0 .75rem; padding-bottom: .35rem;
     border-bottom: 1px solid var(--line); }
.sub { color: var(--muted); margin: 0 0 2rem; font-size: .9rem; }
.grid { display: grid; gap: .75rem;
        grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); }
.stat { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
        padding: .75rem .9rem; }
.stat .n { font-size: 1.5rem; font-weight: 600; }
.stat .l { color: var(--muted); font-size: .8rem; text-transform: uppercase;
           letter-spacing: .03em; }
table { border-collapse: collapse; width: 100%; font-size: .88rem; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: .78rem;
     text-transform: uppercase; letter-spacing: .03em; }
.scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
code, .path { font-family: ui-monospace, Consolas, "Courier New", monospace;
              font-size: .82rem; word-break: break-all; }
details { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
          padding: .6rem .9rem; margin-bottom: .5rem; }
summary { cursor: pointer; font-weight: 600; }
summary::marker { color: var(--muted); }
.kept { color: var(--good); } .dropped { color: var(--bad); }
.muted { color: var(--muted); }
.empty { color: var(--muted); font-style: italic; }
.bar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden;
       min-width: 60px; }
.bar > span { display: block; height: 100%; background: var(--accent); }
.pill { display: inline-block; padding: .05rem .45rem; border-radius: 10px;
        font-size: .75rem; border: 1px solid var(--line); }
"""


def _escape(value) -> str:
    return html.escape("" if value is None else str(value))


def _stat_cards(stats: Dict[str, object]) -> str:
    interesting = [
        ("total_files", "Files scanned"),
        ("organized", "Organized"),
        ("duplicates_skipped", "Duplicates skipped"),
        ("already_in_library", "Already present"),
        ("files_renamed", "Renamed"),
        ("manual_sort", "Manual sort"),
        ("needs_review", "Low confidence"),
        ("artists_merged", "Artist variants merged"),
        ("album_art_extracted", "Album art"),
        ("identified_by_fingerprint", "Fingerprint IDs"),
        ("tags_written", "Tags written"),
        ("sidecars_copied", "Sidecars"),
    ]
    cards = []
    for key, label in interesting:
        if key not in stats:
            continue
        cards.append(
            f'<div class="stat"><div class="n">{_escape(stats[key])}</div>'
            f'<div class="l">{_escape(label)}</div></div>'
        )
    return f'<div class="grid">{"".join(cards)}</div>'


def _additions_section(groups: Sequence[Dict]) -> str:
    if not groups:
        return '<p class="empty">Nothing was added to the library.</p>'

    blocks = []
    for group in sorted(groups, key=lambda item: (item["artist"], item["album"])):
        tracks = "".join(
            f"<li><code>{_escape(name)}</code></li>" for name in group["tracks"]
        )
        blocks.append(
            f'<details><summary>{_escape(group["artist"])} &mdash; '
            f'{_escape(group["album"])} '
            f'<span class="pill">{len(group["tracks"])}</span></summary>'
            f"<ul>{tracks}</ul></details>"
        )
    return "".join(blocks)


def _duplicates_section(pairs: Sequence[Tuple]) -> str:
    if not pairs:
        return '<p class="empty">No duplicate audio found.</p>'

    blocks = []
    for loser, winner in pairs:
        blocks.append(
            "<details><summary>{title}</summary>"
            '<p class="kept">KEPT &nbsp;<span class="path">{kept}</span><br>'
            '<span class="muted">{kept_q}</span></p>'
            '<p class="dropped">SKIPPED &nbsp;<span class="path">{dropped}</span><br>'
            '<span class="muted">{dropped_q}</span></p></details>'.format(
                title=_escape(winner.get("label") or winner.get("path")),
                kept=_escape(winner.get("path")),
                kept_q=_escape(winner.get("quality")),
                dropped=_escape(loser.get("path")),
                dropped_q=_escape(loser.get("quality")),
            )
        )
    return "".join(blocks)


def _deletion_list(pairs: Sequence[Tuple]) -> str:
    if not pairs:
        return ""
    lines = "\n".join(_escape(loser.get("path")) for loser, _ in pairs)
    return (
        "<p class=\"muted\">These source files are redundant. The organizer never "
        "deletes anything, so this is a list for you to act on if you choose.</p>"
        f'<div class="scroll"><pre><code>{lines}</code></pre></div>'
    )


def _near_duplicates_section(groups: Sequence[Dict]) -> str:
    if not groups:
        return '<p class="empty">No near-duplicates found.</p>'

    blocks = []
    for group in groups:
        rows = "".join(
            f'<tr><td class="path">{_escape(item["path"])}</td>'
            f"<td>{_escape(item['quality'])}</td></tr>"
            for item in group["items"]
        )
        blocks.append(
            f'<details><summary>{_escape(group["label"])} '
            f'<span class="pill">{len(group["items"])} copies</span></summary>'
            f'<div class="scroll"><table><tbody>{rows}</tbody></table></div></details>'
        )
    return "".join(blocks)


def _review_section(rows: Sequence[Dict]) -> str:
    if not rows:
        return '<p class="empty">Everything was classified with high confidence.</p>'

    body = []
    for row in rows:
        confidence = int(row.get("confidence") or 0)
        body.append(
            "<tr>"
            f'<td><div class="bar"><span style="width:{confidence}%"></span></div>'
            f'<span class="muted">{confidence}</span></td>'
            f"<td>{_escape(row.get('artist')) or '<span class=muted>?</span>'}</td>"
            f"<td>{_escape(row.get('album'))}</td>"
            f"<td>{_escape(row.get('title'))}</td>"
            f"<td>{_escape(row.get('artist_source'))}</td>"
            f'<td class="path">{_escape(row.get("path"))}</td>'
            "</tr>"
        )
    return (
        '<div class="scroll"><table><thead><tr>'
        "<th>Confidence</th><th>Artist</th><th>Album</th><th>Title</th>"
        "<th>Source</th><th>File</th>"
        f'</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
    )


def _merges_section(merges: Dict[str, List[Tuple[str, int]]]) -> str:
    if not merges:
        return '<p class="empty">No artist name variants needed merging.</p>'

    blocks = []
    for canonical, variants in sorted(merges.items()):
        items = "".join(
            f"<li><code>{_escape(name)}</code> "
            f'<span class="muted">&times;{count}</span></li>'
            for name, count in variants
        )
        blocks.append(
            f"<details><summary>{_escape(canonical)}</summary><ul>{items}</ul></details>"
        )
    return "".join(blocks)


def _quality_section(quality: Dict[str, int]) -> str:
    if not quality:
        return '<p class="empty">Quality analysis disabled.</p>'
    rows = "".join(
        f"<tr><td>{_escape(key)}</td><td>{_escape(value)}</td></tr>"
        for key, value in sorted(quality.items())
    )
    return (f'<div class="scroll"><table><thead><tr><th>Metric</th><th>Count</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></div>')


def _list_section(paths: Iterable[str], empty_message: str) -> str:
    paths = list(paths)
    if not paths:
        return f'<p class="empty">{_escape(empty_message)}</p>'
    lines = "\n".join(_escape(path) for path in paths)
    return f'<div class="scroll"><pre><code>{lines}</code></pre></div>'


def render_report(context: Dict) -> str:
    """Render the whole report as one HTML document."""
    generated = context.get("generated_at") or datetime.now()
    if isinstance(generated, datetime):
        generated = generated.strftime("%Y-%m-%d %H:%M:%S")

    duplicates = context.get("duplicates") or []

    sections = [
        ("Summary", _stat_cards(context.get("stats") or {})),
        ("Added to the library", _additions_section(context.get("additions") or [])),
        ("Duplicates removed from the plan", _duplicates_section(duplicates)),
        ("Redundant source files", _deletion_list(duplicates)),
        ("Near-duplicates (same song, different audio)",
         _near_duplicates_section(context.get("near_duplicates") or [])),
        ("Low-confidence classifications", _review_section(context.get("review") or [])),
        ("Needs manual sorting",
         _list_section(context.get("unclassified") or [], "Nothing needed manual sorting.")),
        ("Artist names merged", _merges_section(context.get("artist_merges") or {})),
        ("Audio quality", _quality_section(context.get("quality") or {})),
        ("Errors",
         _list_section(context.get("errors") or [], "No errors.")),
    ]

    body = "".join(
        f"<h2>{_escape(title)}</h2>{content}"
        for title, content in sections if content
    )

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Music Organizer report - run {_escape(context.get('run_id'))}</title>"
        f"<style>{_STYLE}</style></head><body><div class=\"wrap\">"
        "<h1>Music Organizer report</h1>"
        f'<p class="sub">Run {_escape(context.get("run_id"))} &middot; '
        f'{_escape(generated)} &middot; version {_escape(context.get("version"))}'
        f'{" &middot; DRY RUN" if context.get("dry_run") else ""}<br>'
        f'Collection: <span class="path">{_escape(context.get("collection"))}</span></p>'
        f"{body}</div></body></html>\n"
    )
