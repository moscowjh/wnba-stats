#!/usr/bin/env python3
"""preview_copy.py — render `reference/guide-copy.md` in the site's own CSS.

An editing aid, not part of the build. It exists so copy can be reviewed
against what a reader actually sees — same tokens, same fonts, same fluid prose
measure, same cyan — rather than against a generic markdown preview whose line
length and typography are nothing like the page.

Two things make it more useful than just loading the live page:

1. **It is a single self-contained file that opens over `file://`.** The real
   site uses root-relative links, so it needs a server (see DEPLOY.md); this
   does not. Open it from Finder, or mail it to someone.
2. **It renders the copy alone** — no nav, no chrome — so an editor reads the
   words rather than the furniture, and the appendix in `guide-copy.md` is
   dropped automatically.

Deliberately zero-dependency, like everything else that ships here. The
markdown subset is exactly what `guide-copy.md` uses: headings, paragraphs,
bold/italic/code, tables, blockquotes, links and rules. Anything unrecognised
degrades to a paragraph rather than raising — this is a preview tool, and a
crash mid-edit would be worse than an imperfect render.

Usage:
    .venv/bin/python sites/wwc/preview_copy.py
    open sites/wwc/preview/guide-copy.html
"""

import re
import sys
from html import escape as esc
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SITE_DIR))

from build_wwc_pages import PAGE_CSS, TOURNAMENT_NAME  # noqa: E402

SOURCE = SITE_DIR / "reference" / "guide-copy.md"
OUT = SITE_DIR / "preview" / "guide-copy.html"

#: Everything from here down is machinery notes, not copy. The preview stops.
APPENDIX = "# Appendix"

#: Relative links resolve against the eventual live host so they are not dead
#: in a file:// preview.
BASE = "https://wwc.statsataglance.com"


def inline(text):
    """Bold, italic, code and links. Escaped first, so the source cannot
    inject markup — this file is hand-edited and may come back from a word
    processor that helpfully inserted some."""
    t = esc(text)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", t)

    def link(m):
        label, href = m.group(1), m.group(2)
        if href.startswith("/"):
            href = BASE + href
        return f'<a href="{esc(href, quote=True)}">{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, t)


def render_table(rows):
    """A markdown table. The first row is a header only when it has content —
    `guide-copy.md`'s "Not differences" table deliberately has none."""
    head, body = rows[0], rows[1:]
    out = ['<div class="table-scroll"><div class="table-wrap"><table>']
    if any(c.strip() for c in head):
        out.append("<tr>" + "".join(f"<th>{inline(c)}</th>" for c in head)
                   + "</tr>")
    for r in body:
        cells = "".join(f"<td>{inline(c)}</td>" for c in r)
        out.append(f"<tr>{cells}</tr>")
    out.append("</table></div></div>")
    return "".join(out)


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def render(md):
    lines = md.splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("# "):
            out.append(f'<h1 style="color:var(--accent);font-size:22px;'
                       f'font-weight:700;margin:4px 0 14px">'
                       f'{inline(stripped[2:])}</h1>')
            i += 1
        elif stripped.startswith("## "):
            out.append(f'<h2 class="sec">{inline(stripped[3:])}</h2>')
            i += 1
        elif stripped.startswith("### "):
            out.append(f'<div class="mu" style="font-size:12px;'
                       f'margin:-4px 0 12px">{inline(stripped[4:])}</div>')
            i += 1
        elif stripped.startswith("---"):
            i += 1  # horizontal rules are structure, not copy
        elif stripped.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = split_row(lines[i])
                # skip the |---|---| separator
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            if rows:
                out.append(render_table(rows))
        elif stripped.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append(f'<div class="pend">{inline(" ".join(buf))}</div>')
        elif stripped.startswith(("- ", "* ")):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(("- ", "* ")):
                buf.append(lines[i].strip()[2:])
                i += 1
            items = "".join(f"<li>{inline(b)}</li>" for b in buf)
            out.append(f'<ul class="prose" style="padding-left:20px">'
                       f'{items}</ul>')
        else:
            buf = []
            while i < len(lines) and lines[i].strip() and not \
                    lines[i].strip().startswith(("#", "|", ">", "---", "- ", "* ")):
                buf.append(lines[i].strip())
                i += 1
            if not buf:
                # An unrecognised marker line — a heading level with no branch,
                # say. Consume it as prose rather than leaving `i` where it was:
                # a paragraph fallback that refuses the line is an infinite
                # loop, and a preview tool hanging mid-edit is the one failure
                # this file's docstring promises not to have.
                buf.append(stripped)
                i += 1
            out.append(f'<p class="prose">{inline(" ".join(buf))}</p>')
    return "\n".join(out)


def main():
    if not SOURCE.exists():
        print(f"Missing {SOURCE}")
        return 1
    md = SOURCE.read_text(encoding="utf-8")
    if APPENDIX in md:
        md = md.split(APPENDIX)[0]
    # Drop the file's own front matter — the title and the "hand it back and I
    # translate it" instructions are addressed to the editor, not the reader,
    # and rendering them in the site's type makes them look like copy. The
    # page starts at the first section heading.
    first = md.find("\n## ")
    if first != -1:
        md = md[first:]

    body = render(md)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Guide copy — {esc(TOURNAMENT_NAME)} 2026</title>
<style>
{PAGE_CSS}
  .copybar{{border:1px solid var(--accent);color:var(--accent);
    padding:8px 11px;font-size:11.5px;margin-bottom:18px;line-height:1.6}}
</style>
</head>
<body>
<div class="copybar"><b>COPY PREVIEW</b> — the Guide page's words in the
site's own type and colour, rendered from
<code>reference/guide-copy.md</code>. Not the live page: no nav, no
computed values refreshed, links point at the eventual public host.</div>
{body}
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"  open {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
