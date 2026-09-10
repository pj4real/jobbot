Paste everything below into the chat that has your resume details.
It will hand back two blocks you drop straight into this project.

---

I'm setting up a job application automation system. It needs my details in two
specific formats. Please read back through our conversation and output exactly
two YAML blocks, nothing else.

RULES, these matter:
- Use only facts I have actually told you. Do not infer, round, or embellish.
- If you do not know a value, write FILL: <what you need from me> instead of
  guessing. A wrong number here goes into real applications.
- Do not invent metrics. If a bullet had a number I gave you, keep it exactly.
  If it did not, leave the bullet without one.
- Keep bullets to the wording we already agreed on where we refined them.

BLOCK 1, the missing profile fields:

```yaml
phone: "+91..."
tenth_percentage: ""
twelfth_percentage: ""
portfolio: ""
```

BLOCK 2, my resume as a tagged bullet bank. Every bullet needs a unique id, a
tag list, a weight from 1 to 5 for how much I want it seen, and a `lines`
estimate of rendered height. Valid tags: python, java, cpp, typescript, sql,
ml, data, ai, speech, backend, api, web, fullstack, mobile, security, network,
crypto, algorithms, cp, compilers, devops, infra, product, leadership, tools.

```yaml
header:
  name: "Your Name"
  tagline_pool:
    - id: tl_swe
      tags: [sde, backend, fullstack]
      text: "one line, backend flavoured"
    - id: tl_ml
      tags: [ml, data, ai]
      text: "one line, ML flavoured"
    - id: tl_sec
      tags: [security, network, crypto]
      text: "one line, security flavoured"

experience:
  - org: ""
    role: ""
    dates: ""
    location: ""
    bullets:
      - id: short_unique_id
        tags: [leadership, product]
        weight: 4
        lines: 2
        text: "what I did and what came of it"

projects:
  - id: p_shortname
    name: ""
    subtitle: ""
    stack: []
    link: ""
    tags: []
    weight: 4
    bullets:
      - id: short_unique_id
        tags: []
        weight: 4
        lines: 2
        text: ""

skills:
  - group: Languages
    items:
      - {id: sk_py, text: Python, tags: [python, ml, data]}

achievements:
  - id: ach_x
    tags: []
    weight: 3
    lines: 1
    text: ""
```

Include every project and role we discussed, even weak ones. The system ranks
and drops them per application, so a bullet sitting unused in the bank costs
nothing, while a missing one can never be selected.

After the two blocks, list anything you had to mark FILL and what you need
from me to complete it.
