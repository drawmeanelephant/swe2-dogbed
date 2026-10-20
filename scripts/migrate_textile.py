#!/usr/bin/env python3
"""One-shot migration: data/*.json bodies from Textile to the new
knap -> Markdown -> oliver pipeline (post k4o fidelity rewrite).

Mapping:
  p(cls). x      -> <p class="cls">x</p>      (raw HTML keeps CSS hooks)
  p. x           -> x
  hN(cls). x     -> <hN class="cls">x</hN>;   hN. x -> ##.. x
  bq. para       -> >-prefixed lines (until blank)
  table(cls).    -> <table class="cls"> raw <tr><th>/<td> (headerless ok)
  bc(cls). / bc. -> <pre class="cls"><code>escaped</code></pre>
  %(cls)text%    -> <span class="cls">text</span>
  "text":url     -> <a href="url">text</a>
  @code@         -> <code>code</code>
  *strong*       -> <strong>strong</strong>
  _em_           -> <em>em</em>
  # item         -> 1. / 2. / ... (renumbered per run)
  * item         -> kept (markdown bullet)

Raw HTML now passes through oliver, so literal &<> in prose is escaped.
"""
import json, re, sys, glob

def esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

URL_RE = r'(?:https?://[^\s|)<]+|[\w./-]+\.html)'

def _typography(s):
    """Match the old Textile render's smart quotes on plain text
    (generated tags are placeholder-shielded by the caller)."""
    s = re.sub(r"(\w)'", "\\1\u2019", s)               # don't / closers
    s = re.sub(r"'([^'\u2019\n]*)[\u2019']", lambda m: f"\u2018{m.group(1)}\u2019", s)  # 'quoted'
    s = re.sub(r'"([^"\n]+)"', lambda m: f"\u201c{m.group(1)}\u201d", s)  # "quoted"
    s = s.replace(" - ", " \u2013 ").replace("...", "\u2026")  # - -> –, ... -> …
    return s

def inline(s, escaped=False, md=True):
    """md=True when the fragment lands in a Markdown-parsed context
    (plain paragraphs/headings/quotes/lists); False for raw HTML
    blocks (<p class>, table cells) where * and _ are already literal."""
    if not escaped:
        s = esc_text(s)
    stash = []
    def keep(html):
        stash.append(html)
        return f"\x00{len(stash)-1}\x00"
    s = re.sub(r'"([^"\n]+)":(' + URL_RE + r')',
               lambda m: keep(f'<a href="{m.group(2)}">{m.group(1)}</a>'), s)
    s = re.sub(r'%\(([A-Za-z][\w-]*)\)([^%]*)%',
               lambda m: keep(f'<span class="{m.group(1)}">{inline(m.group(2), escaped=True, md=md)}</span>'), s)
    s = re.sub(r'@([^@\n]+)@', lambda m: keep(f'<code>{m.group(1)}</code>'), s)
    s = re.sub(r'(?<![\w*])\*(\w[^*\n]*?)\*(?![\w*])',
               lambda m: keep(f'<strong>{m.group(1)}</strong>'), s)
    s = re.sub(r'(?<!\w)_(\w[^_\n]*?)_(?!\w)',
               lambda m: keep(f'<em>{m.group(1)}</em>'), s)
    s = _typography(s)
    if md:
        s = s.replace("*", "\\*").replace("_", "\\_")  # leftovers stay literal
    return re.sub(r'\x00(\d+)\x00', lambda m: stash[int(m.group(1))], s)

def convert(body):
    out, i, lines = [], 0, body.split("\n")
    mode = None          # None | ('pre', cls) | ('table', cls)
    ord_n = 0
    def close():
        nonlocal mode
        if mode:
            kind, _ = mode
            out.append("</code></pre>" if kind == "pre" else "</table>")
            mode = None
    while i < len(lines):
        line = lines[i]
        if mode and mode[0] == "pre":
            if line.strip() == "":
                close(); out.append("")
            else:
                out.append(esc_text(line))
            i += 1; continue
        if mode and mode[0] == "table":
            if line.startswith("|"):
                cells = line.split("|")
                if cells and cells[0] == "": cells = cells[1:]
                if cells and cells[-1] == "": cells = cells[:-1]
                tds = "".join(
                    f"<th>{inline(c[2:].lstrip(), md=False)}</th>" if c.startswith("_.")
                    else f"<td>{inline(c, md=False)}</td>"
                    for c in cells)
                out.append(f"<tr>{tds}</tr>")
            else:
                close(); out.append(""); continue  # reprocess; blank ends HTML block
            i += 1; continue
        if line.strip() == "":
            close(); ord_n = 0; out.append(""); i += 1; continue
        m = re.match(r'(?:bc|pre)(?:\(([\w-]+)\))?\. ?(.*)$', line)
        if m:
            mode = ("pre", m.group(1))
            cls = f' class="{m.group(1)}"' if m.group(1) else ""
            out.append(f"<pre{cls}><code>" + esc_text(m.group(2).lstrip()))
            i += 1; continue
        m = re.match(r'table(?:\(([\w-]+)\))?\. *$', line)
        if m:
            mode = ("table", m.group(1))
            cls = f' class="{m.group(1)}"' if m.group(1) else ""
            out.append(f"<table{cls}>")
            i += 1; continue
        m = re.match(r'p(?:\(([\w-]+)\))?\. ?(.*)$', line)
        if m:
            if m.group(1):
                out.append(f'<p class="{m.group(1)}">{inline(m.group(2), md=False)}</p>')
            else:
                out.append(inline(m.group(2)))
            i += 1; continue
        m = re.match(r'h([1-6])(?:\(([\w-]+)\))?\. ?(.*)$', line)
        if m:
            n, cls, txt = m.group(1), m.group(2), m.group(3)
            if cls:
                out.append(f'<h{n} class="{cls}">{inline(txt, md=False)}</h{n}>')
            else:
                out.append("#" * int(n) + " " + inline(txt))
            i += 1; continue
        if line.startswith("bq. "):
            j = i
            while j < len(lines) and lines[j].strip() != "":
                t = lines[j]
                if j == i: t = t[4:]
                out.append("> " + inline(t))
                j += 1
            i = j; continue
        m = re.match(r'# +(.*)$', line)
        if m:
            ord_n += 1
            out.append(f"{ord_n}. " + inline(m.group(1)))
            i += 1; continue
        m = re.match(r'\* +(.*)$', line)
        if m:
            out.append("* " + inline(m.group(1)))
            i += 1; continue
        out.append(inline(line))
        i += 1
    close()
    return "\n".join(out)

def main():
    files = sys.argv[1:] or sorted(glob.glob("data/*.json"))
    for f in files:
        d = json.load(open(f))
        d["body"] = convert(d["body"])
        json.dump(d, open(f, "w"), indent=2, ensure_ascii=False)
        print(f"migrated {f}")

if __name__ == "__main__":
    main()
