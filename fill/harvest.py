"""Turn any page into a list of field descriptors.

Purely mechanical. No intelligence, no site knowledge, no per-portal code.
That is the point: this same function runs on Greenhouse, on a Google Form,
and on the careers portal of a company nobody has ever written a scraper for.
"""
from __future__ import annotations
import hashlib
import json

# Runs in the page. Walks controls and reads whatever label text a human would
# see next to each one, trying the same things in the same order a person does.
JS = r"""
() => {
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };

  const clean = (s) => (s || '').replace(/\s+/g, ' ').replace(/\*/g, '').trim().slice(0, 160);

  // how a person finds the label for a control, in order of reliability
  const labelFor = (el) => {
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && clean(l.innerText)) return clean(l.innerText);
    }
    const wrap = el.closest('label');
    if (wrap && clean(wrap.innerText)) return clean(wrap.innerText);

    const aria = el.getAttribute('aria-labelledby');
    if (aria) {
      const t = aria.split(/\s+/).map(id => document.getElementById(id))
                    .filter(Boolean).map(n => n.innerText).join(' ');
      if (clean(t)) return clean(t);
    }
    if (clean(el.getAttribute('aria-label'))) return clean(el.getAttribute('aria-label'));

    // walk up looking for the nearest block that has text but few controls
    let node = el.parentElement, hops = 0;
    while (node && hops < 5) {
      const controls = node.querySelectorAll('input,select,textarea').length;
      const txt = clean(node.innerText);
      if (controls <= 2 && txt && txt.length < 160) return txt;
      node = node.parentElement; hops++;
    }
    return '';
  };

  const sel = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    if (el.name) return `${el.tagName.toLowerCase()}[name="${CSS.escape(el.name)}"]`;
    const same = [...document.querySelectorAll(el.tagName)];
    return `${el.tagName.toLowerCase()}:nth-of-type(${same.indexOf(el) + 1})`;
  };

  const SKIP = new Set(['hidden', 'submit', 'button', 'reset', 'image']);
  const out = [];

  document.querySelectorAll('input,select,textarea').forEach((el) => {
    const type = (el.type || el.tagName).toLowerCase();
    if (SKIP.has(type)) return;
    if (!vis(el) && type !== 'file') return;   // file inputs are often hidden by design

    const d = {
      tag: el.tagName.toLowerCase(),
      type,
      name: el.name || '',
      id: el.id || '',
      placeholder: el.placeholder || '',
      aria: el.getAttribute('aria-label') || '',
      label: labelFor(el),
      required: el.required || el.getAttribute('aria-required') === 'true',
      selector: sel(el),
      value: (type === 'checkbox' || type === 'radio') ? el.value : '',
      options: [],
    };
    if (el.tagName === 'SELECT') {
      d.options = [...el.options].map(o => ({ value: o.value, text: clean(o.text) }))
                                 .filter(o => o.text);
    }
    out.push(d);
  });

  // some portals build comboboxes out of divs; record them so we can flag them
  document.querySelectorAll('[role="combobox"],[role="listbox"]').forEach((el) => {
    if (!vis(el)) return;
    out.push({
      tag: 'widget', type: 'combobox', name: '', id: el.id || '',
      placeholder: '', aria: el.getAttribute('aria-label') || '',
      label: clean(el.innerText).slice(0, 80), required: false,
      selector: el.id ? `#${CSS.escape(el.id)}` : '', value: '', options: [],
    });
  });

  return out;
}
"""


def harvest(page) -> list[dict]:
    """Descriptors for the main frame and every same-origin iframe.

    Greenhouse and Lever embed their form in an iframe on company career
    pages, so a harvester that only looks at the main frame finds nothing on
    exactly the sites you most want to work.
    """
    found: list[dict] = []
    for i, frame in enumerate(page.frames):
        try:
            items = frame.evaluate(JS)
        except Exception:
            continue                       # cross-origin frame, nothing to do
        for it in items:
            it["frame"] = i
            it["frame_url"] = frame.url
        found += items

    seen, unique = set(), []
    for f in found:
        k = (f["frame"], f["selector"], f["name"], f["label"][:40])
        if k not in seen:
            seen.add(k)
            unique.append(f)
    return unique


def fingerprint(fields: list[dict]) -> str:
    """Identity of a form's shape, so a cached mapping can be looked up and
    invalidated when the portal changes its DOM."""
    parts = sorted(f"{f['type']}:{f['name'] or f['id'] or f['label'][:30]}" for f in fields)
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]
