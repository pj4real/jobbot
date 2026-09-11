"""The look of the dashboard: tokens, shell, and the handful of components
every page is built from.

Design notes, so the next person does not have to guess:

  Subject     one student's placement season. Anxious, checked several times a
              day, scanned rather than read. The page's job is to say what needs
              doing right now and let you do it.
  Ground      neutrals biased green rather than pure grey, so they read as
              chosen and sit under the accent without fighting it.
  Accent      one deep pine green, used for progress and for the primary action.
  Signal      amber, spent only on "this needs you". If everything is amber,
              nothing is.
  Type        Instrument Sans for the interface, Instrument Serif for the
              wordmark alone, IBM Plex Mono for ids, counts and logs.
  Density     a vertical list, not a horizontal kanban. Kanban columns look
              organised and are miserable to scan on a laptop.
"""
from __future__ import annotations

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Instrument+Sans:wght@400;500;600;700&'
         'family=Instrument+Serif:ital@0;1&'
         'family=IBM+Plex+Mono:wght@400;500;600&display=swap">')

CSS = """
:root{
  --ground:#F3F5F4; --surface:#FFFFFF; --surface-2:#E9EDEB; --raise:#FFFFFF;
  --ink:#101816; --ink-2:#4A5754; --ink-3:#7B8A86;
  --line:#DCE3E0; --line-2:#C2CDC9;
  --accent:#0F5C4A; --accent-ink:#0F5C4A; --accent-soft:#DDEDE6;
  --signal:#A85E11; --signal-soft:#F8EBD9;
  --danger:#8E2F26; --danger-soft:#F7E4E1;
  --good:#1B6B4A; --good-soft:#DDEFE3;
  --term:#0E1513; --term-ink:#C6D6D0;
  --shadow:0 1px 2px rgba(16,24,22,.05), 0 10px 26px -18px rgba(16,24,22,.35);
  --r:8px;
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --ground:#0C110F; --surface:#141A18; --surface-2:#1C2422; --raise:#1A2220;
  --ink:#E4EAE7; --ink-2:#9FAFAA; --ink-3:#71827D;
  --line:#26302D; --line-2:#38443F;
  --accent:#63C9A6; --accent-ink:#63C9A6; --accent-soft:#10302A;
  --signal:#E0A85F; --signal-soft:#2C2213;
  --danger:#E2887C; --danger-soft:#32191A;
  --good:#5FC896; --good-soft:#102C20;
  --term:#080D0C; --term-ink:#B5C7C1;
  --shadow:0 1px 2px rgba(0,0,0,.5), 0 10px 26px -18px rgba(0,0,0,.9);
}}
:root[data-theme="dark"]{
  --ground:#0C110F; --surface:#141A18; --surface-2:#1C2422; --raise:#1A2220;
  --ink:#E4EAE7; --ink-2:#9FAFAA; --ink-3:#71827D;
  --line:#26302D; --line-2:#38443F;
  --accent:#63C9A6; --accent-ink:#63C9A6; --accent-soft:#10302A;
  --signal:#E0A85F; --signal-soft:#2C2213;
  --danger:#E2887C; --danger-soft:#32191A;
  --good:#5FC896; --good-soft:#102C20;
  --term:#080D0C; --term-ink:#B5C7C1;
  --shadow:0 1px 2px rgba(0,0,0,.5), 0 10px 26px -18px rgba(0,0,0,.9);
}

*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font-family:"Instrument Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  font-size:14.5px; line-height:1.5; padding:0 20px 96px;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1060px;margin:0 auto}
.mono{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;
  font-variant-numeric:tabular-nums}

/* ---------- top bar ---------- */
.top{display:flex;align-items:center;gap:18px;flex-wrap:wrap;
  padding:20px 0 14px;border-bottom:1px solid var(--line)}
.mark{font-family:"Instrument Serif",Georgia,serif;font-size:25px;line-height:1;
  letter-spacing:-.01em;text-decoration:none;color:var(--ink)}
.mark i{font-style:italic;color:var(--accent-ink)}
.tabs{display:flex;gap:2px}
.tabs a{color:var(--ink-2);text-decoration:none;font-size:13.5px;font-weight:500;
  padding:6px 11px;border-radius:6px}
.tabs a:hover{background:var(--surface-2);color:var(--ink)}
.tabs a.on{background:var(--accent-soft);color:var(--accent-ink);font-weight:600}
.grow{flex:1}

/* ---------- headline ---------- */
.headline{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  padding:22px 0 4px}
.headline h1{margin:0;font-size:clamp(21px,3.4vw,27px);font-weight:600;
  letter-spacing:-.021em;text-wrap:balance}
.headline .sub{color:var(--ink-3);font-size:13px}
.tone-good h1{color:var(--good)} .tone-act h1{color:var(--ink)}
.tone-wait h1{color:var(--signal)} .tone-idle h1{color:var(--ink-3)}

/* ---------- stage rail ---------- */
.rail{display:flex;gap:6px;flex-wrap:wrap;margin:18px 0 4px}
.rail a{display:flex;align-items:baseline;gap:7px;text-decoration:none;
  padding:7px 12px;border-radius:7px;border:1px solid var(--line);
  background:var(--surface);color:var(--ink-2);font-size:13px;font-weight:500}
.rail a:hover{border-color:var(--line-2);color:var(--ink)}
.rail a.on{background:var(--ink);border-color:var(--ink);color:var(--ground)}
.rail a.on b{color:var(--ground)}
.rail b{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;
  font-weight:600;font-variant-numeric:tabular-nums;color:var(--ink-3)}
.rail a.hot{border-color:var(--signal);color:var(--signal)}
.rail a.hot b{color:var(--signal)}
.rail-note{color:var(--ink-3);font-size:12.5px;margin:10px 0 0}

/* ---------- job rows ---------- */
.rows{display:flex;flex-direction:column;gap:0;margin:14px 0 0;
  border:1px solid var(--line);border-radius:var(--r);background:var(--surface);
  overflow:hidden}
.row{display:grid;grid-template-columns:44px 1fr auto;gap:0 14px;
  padding:14px 16px;border-top:1px solid var(--line);align-items:center}
.row:first-child{border-top:0}
.row:hover{background:var(--surface-2)}
.fit{display:flex;flex-direction:column;align-items:flex-start;gap:3px}
.fit b{font-family:"IBM Plex Mono",monospace;font-size:12px;font-weight:600;
  color:var(--ink-2);font-variant-numeric:tabular-nums}
.meter{width:38px;height:4px;border-radius:2px;background:var(--line);overflow:hidden}
.meter i{display:block;height:100%;background:var(--accent)}
.who{min-width:0}
.who .co{font-weight:600;font-size:14.5px;letter-spacing:-.005em}
.who .co a{color:inherit;text-decoration:none}
.who .co a:hover{text-decoration:underline}
.who .role{color:var(--ink-2);font-size:13.5px}
.who .meta{color:var(--ink-3);font-size:12px;margin-top:3px;
  display:flex;gap:7px;flex-wrap:wrap;align-items:center}
.who .meta span:not(:last-child):after{content:"·";margin-left:7px;color:var(--line-2)}
.act{display:flex;align-items:center;gap:7px;flex-shrink:0}

/* ---------- controls ---------- */
button,.btn{font:inherit;font-size:13px;font-weight:500;padding:6px 12px;
  border-radius:6px;border:1px solid var(--line-2);background:var(--surface);
  color:var(--ink);cursor:pointer;text-decoration:none;display:inline-block;
  line-height:1.35;white-space:nowrap}
button:hover,.btn:hover{border-color:var(--ink-3);background:var(--surface-2)}
button:focus-visible,.btn:focus-visible,a:focus-visible,input:focus-visible,
textarea:focus-visible,select:focus-visible{outline:2px solid var(--accent);
  outline-offset:2px}
.btn-go,button.go{background:var(--accent);border-color:var(--accent);color:var(--ground)}
.btn-go:hover,button.go:hover{background:var(--accent);opacity:.88;color:var(--ground)}
.btn-quiet{border-color:transparent;background:transparent;color:var(--ink-3)}
.btn-quiet:hover{background:var(--surface-2);color:var(--ink)}
.btn-warn{border-color:var(--signal);color:var(--signal)}
.btn-bad{border-color:var(--danger);color:var(--danger)}
button:disabled{opacity:.4;cursor:not-allowed}
button.sm,.btn.sm{font-size:12px;padding:4px 9px}

/* ---------- chips ---------- */
.chip{display:inline-block;font-family:"IBM Plex Mono",monospace;font-size:10.5px;
  font-weight:500;letter-spacing:.04em;text-transform:uppercase;padding:3px 7px;
  border-radius:4px;background:var(--surface-2);color:var(--ink-3);white-space:nowrap}
.chip.act{background:var(--signal-soft);color:var(--signal)}
.chip.go{background:var(--accent-soft);color:var(--accent-ink)}
.chip.good{background:var(--good-soft);color:var(--good)}
.chip.bad{background:var(--danger-soft);color:var(--danger)}

/* ---------- panels ---------- */
h2{font-size:15.5px;font-weight:600;letter-spacing:-.01em;margin:30px 0 8px}
h3{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;
  color:var(--ink-3);margin:24px 0 8px}
p{margin:0 0 12px;max-width:68ch}
a{color:var(--accent-ink)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);
  padding:16px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(238px,1fr));gap:10px}
.card h4{margin:0 0 4px;font-size:13.5px;font-weight:600}
.card p{margin:0 0 11px;font-size:12.5px;color:var(--ink-3);line-height:1.45}
.note{border:1px solid var(--line);border-left:3px solid var(--ink-3);
  background:var(--surface);border-radius:var(--r);padding:13px 15px;margin:12px 0;
  font-size:13.5px}
.note.act{border-left-color:var(--signal)}
.note.bad{border-left-color:var(--danger)}
.note.good{border-left-color:var(--good)}
.note.info{border-left-color:var(--accent)}
.empty{border:1px dashed var(--line-2);border-radius:var(--r);padding:34px 20px;
  text-align:center;color:var(--ink-3);background:var(--surface)}
.empty b{display:block;color:var(--ink-2);font-weight:600;margin-bottom:5px;
  font-size:14.5px}

/* ---------- stats ---------- */
.stats{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 0}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);
  padding:10px 14px;min-width:92px}
.stat b{display:block;font-family:"IBM Plex Mono",monospace;font-size:20px;
  font-weight:600;font-variant-numeric:tabular-nums;line-height:1.2}
.stat span{font-size:10.5px;color:var(--ink-3);text-transform:uppercase;
  letter-spacing:.07em}

/* ---------- timeline ---------- */
.tl{list-style:none;padding:0;margin:8px 0 0;position:relative}
.tl:before{content:"";position:absolute;left:5px;top:6px;bottom:6px;width:1px;
  background:var(--line)}
.tl li{position:relative;padding:0 0 14px 22px;font-size:13.5px}
.tl li:before{content:"";position:absolute;left:2px;top:6px;width:7px;height:7px;
  border-radius:50%;background:var(--line-2);border:2px solid var(--surface)}
.tl li.now:before{background:var(--accent)}
.tl .when{color:var(--ink-3);font-size:11.5px;font-family:"IBM Plex Mono",monospace}

/* ---------- forms and logs ---------- */
textarea,input[type=text],input[type=url],input[type=password],select{
  width:100%;padding:9px 11px;border:1px solid var(--line-2);border-radius:6px;
  background:var(--surface);color:var(--ink);font:inherit}
textarea{min-height:280px;font-family:"IBM Plex Mono",ui-monospace,monospace;
  font-size:12.5px;line-height:1.6;tab-size:2}
textarea.tall{min-height:58vh}
label.f{display:block;font-size:12px;color:var(--ink-3);margin:13px 0 4px;
  font-weight:500}
.row-f{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin:12px 0}
#log{background:var(--term);color:var(--term-ink);border:1px solid var(--line);
  border-radius:var(--r);padding:14px 16px;
  font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12px;line-height:1.65;
  white-space:pre-wrap;min-height:190px;max-height:56vh;overflow-y:auto;margin:10px 0}
#log .e{color:#E2887C} #log .g{color:#5FC896} #log .w{color:#E0A85F}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--ink-3);padding:0 10px 7px 0;border-bottom:1px solid var(--line-2);
  font-weight:600}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--line);vertical-align:top}
.scroll{overflow-x:auto}
.dim{color:var(--ink-3)} .sm{font-size:12.5px}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
.dot.run{background:var(--signal)} .dot.ok{background:var(--good)}
.dot.err{background:var(--danger)}

kbd{font-family:"IBM Plex Mono",monospace;font-size:11px;padding:1px 5px;
  border:1px solid var(--line-2);border-bottom-width:2px;border-radius:4px;
  background:var(--surface-2);color:var(--ink-2)}

@media (max-width:640px){
  body{font-size:14px;padding:0 14px 80px}
  .row{grid-template-columns:1fr;gap:9px}
  .fit{flex-direction:row;align-items:center;gap:8px}
  .act{justify-content:flex-start;flex-wrap:wrap}
  .headline h1{font-size:19px}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

TABS = [("/", "Board"), ("/activity", "Activity"), ("/setup", "Setup")]


def shell(body: str, tab: str = "", scripts: str = "") -> str:
    tabs = "".join(
        f'<a href="{href}"{" class=on" if name == tab else ""}>{name}</a>'
        for href, name in TABS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>jobbot</title>{FONTS}<style>{CSS}</style></head><body><div class="wrap">
<div class="top">
  <a class="mark" href="/">job<i>bot</i></a>
  <nav class="tabs">{tabs}</nav>
  <div class="grow"></div>
  <a class="btn sm" href="/setup?start=find#run">Scan</a>
  <a class="btn sm" href="/setup#run">Run</a>
</div>
{body}
</div>{scripts}</body></html>"""


def stat(label: str, value, tone: str = "") -> str:
    color = {"act": "var(--signal)", "good": "var(--good)",
             "bad": "var(--danger)"}.get(tone, "")
    style = f' style="color:{color}"' if color else ""
    return (f'<div class="stat"><b{style}>{value}</b><span>{label}</span></div>')


def empty(title: str, hint: str, action: str = "") -> str:
    return f'<div class="empty"><b>{title}</b>{hint}{"<div style=margin-top:14px>" + action + "</div>" if action else ""}</div>'


def note(text: str, tone: str = "") -> str:
    return f'<div class="note {tone}">{text}</div>'
