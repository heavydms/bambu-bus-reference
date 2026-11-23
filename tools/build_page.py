# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 heavydms. See LICENSE.
"""Build a standalone HTML page from README.md (pandoc fragment + designed shell). Usage: build_page.py README.md out.html"""
import re, subprocess, sys, html

SRC, OUT = sys.argv[1], sys.argv[2]
md = open(SRC).read()
# drop the markdown H1 (the page header replaces it)
md = re.sub(r"\A# [^\n]*\n", "", md)
md = re.sub(r"\n(!\[)", r"\n\n\1", md)  # figures always start their own paragraph
frag = subprocess.run(["pandoc", "-f", "gfm-strikeout", "-t", "html5", "--wrap=none"], input=md,
                      capture_output=True, text=True, check=True).stdout

# figures: <p><img .../> <em>caption</em></p> -> <figure>
frag = re.sub(r"<p><img ([^>]*?)\s*/?>\s*<em>(.*?)</em></p>",
              r'<figure class="plate"><img \1 loading="lazy"><figcaption>\2</figcaption></figure>', frag, flags=re.S)
# provenance tags -> chips
def chip(m):
    kind, body = m.group(1), m.group(2).strip()
    return f'<span class="tag tag-{kind.lower()}"><b>{kind}</b> {body}</span>'
frag = re.sub(r"\[(Published|Measured):\s*(.*?)\]", chip, frag)
frag = re.sub(r"\[Measured\]", '<span class="tag tag-measured"><b>Measured</b></span>', frag)
# New badges
frag = frag.replace("<strong>New</strong>", '<span class="new">New</span>')
# links to repo-only files -> plain names
frag = re.sub(r'<a href="(?!https?:|#|charts/|img/|buffer_load_unload)([^"]+)">(.*?)</a>', r'<span class="repo">\2</span>', frag)
# wide tables scroll in their own box
frag = re.sub(r"<table>", '<div class="tablewrap"><table>', frag).replace("</table>", "</table></div>")

# table of contents from h2
toc = []
for hid, title in re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', frag):
    toc.append(f'<li><a href="#{hid}">{title}</a></li>')
toc_html = "\n".join(toc)

page = f"""<title>Bambu Bus Reference</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:ital,wght@0,400;0,500;0,600;1,400&family=Space+Grotesk:wght@500;600&display=swap">
<style>
/* Layout: sticky contents rail + one reading column; figures as light plates, tables scroll in place. */
:root {{
  --bg: #f5f7f6; --surface: #ffffff; --fg: #15201e; --muted: #56635f; --rule: #d6dedb;
  --accent: #0d7468; --accent-soft: #e1f1ed; --new: #a8520a; --new-soft: #fbecd9;
  --tag: #edf1ef; --code: #eef2f1;
  --f-display: "Space Grotesk", "IBM Plex Sans", system-ui, sans-serif;
  --f-body: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --f-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #0e1413; --surface: #151d1b; --fg: #e4ebe9; --muted: #9aa8a4; --rule: #28332f;
  --accent: #46c4b1; --accent-soft: #12302b; --new: #f2a65a; --new-soft: #3a2611;
  --tag: #1b2523; --code: #1b2523; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #0e1413; --surface: #151d1b; --fg: #e4ebe9; --muted: #9aa8a4; --rule: #28332f;
  --accent: #46c4b1; --accent-soft: #12302b; --new: #f2a65a; --new-soft: #3a2611;
  --tag: #1b2523; --code: #1b2523; color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ background: var(--bg); color: var(--fg); font: 15.5px/1.65 var(--f-body);
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }}
a {{ color: var(--accent); text-underline-offset: 2px; }}
a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }}
header.top {{ max-width: 1180px; margin: 0 auto; padding-block: 2.4rem 1.4rem; border-bottom: 1px solid var(--rule); }}
.eyebrow {{ font: 500 12px/1 var(--f-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--accent); }}
h1 {{ font: 600 clamp(2rem, 5vw, 3rem)/1.05 var(--f-display); margin: .5rem 0 .6rem; text-wrap: balance; letter-spacing: -.01em; }}
.lede {{ max-width: 68ch; color: var(--muted); margin: 0 0 1.1rem; }}
.wire {{ display: flex; flex-wrap: wrap; gap: 6px 18px; font: 13px/1.4 var(--f-mono); color: var(--fg); }}
.wire span {{ white-space: nowrap; }} .wire span::before {{ content: "▍"; color: var(--accent); margin-right: 4px; }}
.layout {{ max-width: 1180px; margin: 0 auto; display: grid; grid-template-columns: 230px minmax(0, 1fr); gap: 48px; padding-block: 1.6rem 4rem; }}
nav.toc {{ position: sticky; top: calc(env(safe-area-inset-top, 0px) + 16px); align-self: start; max-height: calc(100vh - 32px); overflow: auto; font-size: 13.5px; }}
nav.toc h2 {{ font: 500 12px/1 var(--f-mono); letter-spacing: .08em; text-transform: uppercase; color: var(--muted); margin: 0 0 .8rem; }}
nav.toc ol {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }}
nav.toc a {{ display: block; padding: 4px 8px; border-left: 2px solid var(--rule); color: var(--fg); text-decoration: none; }}
nav.toc a:hover {{ border-left-color: var(--accent); background: var(--accent-soft); }}
main {{ min-width: 0; max-width: 820px; }}
main h2 {{ font: 600 1.55rem/1.2 var(--f-display); margin: 2.8rem 0 .8rem; padding-top: .6rem; border-top: 1px solid var(--rule); scroll-margin-top: 16px; text-wrap: balance; }}
main h3 {{ font: 600 1.12rem/1.3 var(--f-display); margin: 2rem 0 .5rem; scroll-margin-top: 16px; }}
main p, main li {{ max-width: 72ch; }}
main ul, main ol {{ padding-left: 1.3rem; }} main li {{ margin: .25rem 0; }}
code {{ font: .88em var(--f-mono); background: var(--code); padding: .08em .35em; border-radius: 4px; overflow-wrap: anywhere; }}
pre {{ background: var(--code); padding: 12px 14px; border-radius: 6px; overflow-x: auto; }} pre code {{ background: none; padding: 0; }}
.tablewrap {{ overflow-x: auto; margin: 1rem 0; border: 1px solid var(--rule); border-radius: 6px; background: var(--surface); }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; font-variant-numeric: tabular-nums; }}
th, td {{ text-align: left; vertical-align: top; padding: 7px 10px; border-bottom: 1px solid var(--rule); }}
th {{ font: 600 12px/1.3 var(--f-body); color: var(--muted); background: var(--tag); white-space: nowrap; }}
tr:last-child td {{ border-bottom: 0; }}
.tag {{ display: inline; font: 11.5px/1.5 var(--f-mono); color: var(--muted); background: var(--tag); border-radius: 4px; padding: .05em .4em; white-space: normal; }}
.tag b {{ font-weight: 500; color: var(--fg); }} .tag-measured b {{ color: var(--accent); }}
.new {{ display: inline-block; font: 600 11px/1.5 var(--f-mono); letter-spacing: .06em; text-transform: uppercase; color: var(--new); background: var(--new-soft); border: 1px solid currentColor; border-radius: 3px; padding: 0 .4em; vertical-align: .08em; }}
.repo {{ font-family: var(--f-mono); font-size: .9em; }}
figure.plate {{ margin: 1.4rem 0 1.6rem; }}
figure.plate img {{ display: block; width: 100%; height: auto; max-width: 100%; background: #ffffff; padding: 10px; border: 1px solid var(--rule); border-radius: 6px; }}
figcaption {{ font-size: 13px; color: var(--muted); margin-top: .5rem; max-width: 72ch; }}
blockquote {{ margin: 1rem 0; padding: .4rem 1rem; border-left: 3px solid var(--accent); background: var(--accent-soft); }}
hr {{ border: 0; border-top: 1px solid var(--rule); margin: 2rem 0; }}
footer {{ max-width: 1180px; margin: 0 auto; padding-block: 1.5rem 3rem; border-top: 1px solid var(--rule); color: var(--muted); font-size: 13px; }}
@media (max-width: 900px) {{
  .layout {{ grid-template-columns: minmax(0, 1fr); gap: 0; }}
  nav.toc {{ position: static; max-height: none; border: 1px solid var(--rule); border-radius: 6px; padding: 12px; background: var(--surface); }}
}}
@media (prefers-reduced-motion: no-preference) {{ html {{ scroll-behavior: smooth; }} }}
</style>

<header class="top">
  <div class="eyebrow">Unofficial · reverse-engineered · X2D + AMS 2 Pro</div>
  <h1>Bambu Bus Reference</h1>
  <p class="lede">Everything we could find or measure about how a Bambu Lab printer talks to its AMS: the wire, the frames, every message type we saw, the sequences behind load, unload, auth and firmware updates, and the buffer signal on pins 5/6. Each claim says where it came from.</p>
  <div class="wire"><span>RS-485 · 1,228,800 baud · 8E1</span><span>frame start 0x3D</span><span>CRC8 0x39 / 0x66</span><span>CRC16 0x1021 / 0x913D</span><span>buffer: 10 kHz PWM, pins 5/6</span></div>
</header>
<div class="layout">
  <nav class="toc" aria-label="Contents"><h2>Contents</h2><ol>
{toc_html}
  </ol></nav>
  <main>
{frag}
  </main>
</div>
<footer>Not affiliated with or endorsed by Bambu Lab. Figures are rendered from the author's own bench captures (X2D 01.01.00.00 + AMS 2 Pro, November 2026). Raw captures are withheld because they contain device identifiers.</footer>
"""
open(OUT, "w").write(page)
print("bytes", len(page), "| toc", len(toc), "| figures", page.count('class="plate"'), "| chips", page.count('class="tag '), "| new", page.count('class="new"'), "| repo refs", page.count('class="repo"'))
