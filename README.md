# tee-time-agent

**Tell it in plain English what tee time you want. It watches the tee sheet and pings your phone
the moment a matching time opens up, with a one-tap button to book it.**

```bash
$ teewatch add "Saturdays before 10 for a foursome at Twin Lakes, under \$75"
- name: sat-foursome-twin-lakes
  provider: fairfax
  courses: ['7756', '7743']
  days: [sat]
  latest: '10:00'
  players: 4
  max_price: 75
Next dates watched: Sat Nov 28
Save this watch? [y/N] y
```

…and fifteen minutes later, on your phone:

> **⛳ 2 new tee times · sat-foursome-twin-lakes**
> Sat Nov 28 8:10 AM · Twin Lakes Golf Course - Oaks Course · 4 spots · $49 · 41°F, 10% rain
> Sat Nov 28 8:20 AM · Twin Lakes Golf Course - Lakes Course · 4 spots · $49 · 41°F, 10% rain
> **[Book 8:10 AM Twin Lakes - Oaks]** **[Book 8:20 AM Twin Lakes - Lakes]**

[![ci](https://github.com/jinwoojang21/tee-time-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/jinwoojang21/tee-time-agent/actions/workflows/ci.yml)

```mermaid
flowchart LR
    U["plain-English request"] --> A[[Claude: add]]
    A -- calendar / list_courses --> A
    A -->|propose_watch → validated in code| C{you confirm?}
    C -->|yes| W[(teewatch.yaml)]
    subgraph every 15 min: GitHub Actions, no LLM
      W --> R[watcher] -->|1 request per course set per date| T[(TeeItUp API)]
      R --> M{matches rule?<br/>time · players · holes · price · rain}
      M -->|new slot| S[(alert history)]
      M --> N[ntfy push + Book buttons]
    end
    N -->|tap Book = your approval| B[course booking page]
    Q["“best foursome Saturday?”"] --> K[[Claude: ask]] --> T
```

## Design

| Decision | Reason |
|---|---|
| **The LLM only runs when you're there.** Claude turns your request into a rule once. The scheduled check is plain Python. | Monitoring every 15 minutes costs nothing and gives the same result every time. The model never decides on its own whether to notify you. |
| **Claude proposes a rule, code checks it, you confirm it.** | `propose_watch` goes through the same validation as a hand-written rule. If it fails, the error goes back to Claude to fix. Nothing is saved until you say yes. |
| **A calendar tool instead of date math** | "This weekend", "Black Friday" and "Tuesday the 1st" get resolved against a real calendar instead of the model's arithmetic. |
| **"Book" opens the course's booking page; it doesn't book for you** | Booking needs your login and payment details. The alert takes you to that date and course, and you finish in two taps. That tap is your approval. |
| **One alert per slot** | Alert history lives in the Actions cache, so the same 8:10 AM slot doesn't ping you every 15 minutes. |
| **Polite polling** | One request per date and course set per run, an identifying User-Agent, and checks only from 6 AM to 9 PM. |

## Courses

Ready to use: the **Fairfax County Park Authority** public courses, which book through TeeItUp:
Twin Lakes (Oaks and Lakes, Clifton), Laurel Hill, Burke Lake, Pinecrest, Greendale, Jefferson District and Oakmont.
Run `teewatch courses` for their ids.

Any other course on TeeItUp needs just two lines of config. Find its alias in the booking URL
(`https://<alias>.book.teeitup.golf`):

```yaml
providers:
  mycourse: {kind: teeitup, alias: <alias>}
```

**Private clubs (e.g. Piedmont Club on the Invited member portal).** The `json` provider handles
any tee sheet that a logged-in page loads as JSON, using config instead of code. See
[`providers/piedmont.example.yaml`](providers/piedmont.example.yaml): sign in, copy the tee-sheet
request URL from DevTools, map the fields, and store the session cookie as the
`PIEDMONT_SESSION_COOKIE` secret. A test shows the same provider reproducing the TeeItUp results
from config alone. Check your club's rules on automated access before turning it on.

## Set up (10 minutes)

1. **Phone:** install the [ntfy](https://ntfy.sh) app and subscribe to a long, random topic name,
   e.g. `justin-tee-7f3k9q2x`. Anyone who knows the name can read it, so make it hard to guess.
2. **Repo secrets** (Settings → Secrets and variables → Actions): `NTFY_TOPIC`, and
   `ANTHROPIC_API_KEY` for `add`, `ask` and the Claude evals.
3. **Watches:** edit [`teewatch.yaml`](teewatch.yaml) or run `teewatch add "…"` locally and commit the result.
4. Actions → **watch** → *Run workflow* once to test it. After that it runs every 15 minutes.

```bash
pip install -e ".[dev]"
teewatch courses                       # course ids
teewatch check --dry-run               # see current matches without alerting
teewatch test-alert                    # send a sample notification to your phone
teewatch add "every Sunday afternoon, 2 players, walking, under \$50"
teewatch ask "what's the cheapest foursome Saturday morning in Clifton?"
```

## Evals

[`evals/requests.yaml`](evals/requests.yaml) contains 12 plain-English requests, each with the fields a correct rule
must have. Dates are graded as the concrete dates the rule would watch over the next 14 days, so
"this weekend" and `days: [sat, sun]` are both correct when they cover the same dates. The house
conventions ("early morning" means before 09:00, "twilight" means from 15:00) are in the agent's prompt.
Everything else, like counting players or resolving holidays, Claude has to work out.

```bash
teewatch evals --agent baseline   # CI, every push
teewatch evals --agent claude     # manual CI job
```

| parser | requests fully correct | fields correct |
|---|---|---|
| Regex baseline | 7/12 | 52/58 |
| Claude | _pending first run_ | |

The regex parser misses "me and 3 buddies", "with my son", Thanksgiving, "early morning" and "over the next two weeks".

## Tests

17 offline tests run against a **real captured TeeItUp response** (`tests/fixtures/`). They cover
time-zone conversion, cart vs. walking prices, 9 vs. 18 holes, the rain filter, alerting once per
slot, provider failures reported instead of crashing, ntfy payloads, the JSON provider rebuilt
from config, and a scripted Claude client that sends an invalid rule and fixes it after the
validation error. A separate CI job checks the live API.

## License

MIT. Not affiliated with TeeItUp, GolfNow, Fairfax County or Invited.
