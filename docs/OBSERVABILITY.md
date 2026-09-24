# Observability — what it is, and what we just built

Written to be read start to finish if the word "observability" has been floating past you
without landing. If you already know the idea, skip to *What a turn looks like*.

## The thing it solves

A playtester says: "KES gave me a weird answer about the coolant loop."

Right now you can ask them to describe it, and that's all. The answer is gone. So are the
six document chunks it was written from, the ship state that framed it, the access tier
that decided what was searchable, and which of KES's five dispositions it was in. You can't
reproduce it, because the corpus is fixed but the retrieval depends on the exact wording
they used and the state they were in.

Observability is just: **write down enough, while the program runs, to answer "why did it
do that?" afterwards.** Not logging for crashes — the program didn't crash. Logging for
behaviour.

It matters more for this project than for ordinary software because of where failures
live. In a normal app, a bug is in code you can read and step through. Here, five things
happen per question:

1. The question is turned into a search query (which may not be the question — short
   follow-ups get the previous question prepended).
2. That query is embedded into a vector.
3. Chroma returns the nearest chunks, *after* filtering by access tier and privacy.
4. Those chunks plus the ship state plus the persona go into a prompt.
5. A language model writes an answer.

A bad answer could come from any of them, and **four of the five are invisible in the
output.** If KES says something wrong about the coolant loop, the most likely cause isn't
the model — it's that step 3 returned six documents about the hull, because the player's
phrasing happened to sit closer to those. You cannot see that from the answer. You can see
it instantly from a log of what was retrieved.

## Why not a tracing platform

Langfuse, LangSmith, Arize Phoenix and friends are real tools, and if this were a
ten-service pipeline built out of a framework I'd use one. They're designed for tracing
chains *you didn't write* — showing you the inside of an agent framework's control flow.

This pipeline is five functions across two files, all ours. Adding a platform would mean a
dependency, an account, a network call on every turn, and learning their data model, in
exchange for a dashboard over data we can already reach directly. So instead:

**One line of JSON per turn, one file per playthrough.** No dependencies. Works offline.
Greppable. Survives a failed deploy. And it's already the shape the eval harness wants,
which is the next thing you're building.

That's a defensible engineering choice, not a shortcut, and it's worth saying so in the
presentation: *we chose the smallest thing that answers the questions we actually have.*

## The files

| File | What it does |
| --- | --- |
| `observe.py` | The recorder. A `SessionLog` object per playthrough; `game.py` calls it on every turn. |
| `report.py` | The reader. Terminal summaries, per-turn detail, and a self-contained HTML report. |
| `logs/session-<timestamp>-v<seed>.jsonl` | The data. One JSON object per line. |

JSONL means "JSON Lines": a file where every line is a complete JSON object. It's append-only,
so a session that crashes halfway still leaves a readable file, and you can process it a line
at a time without loading the whole thing.

Logging is on by default. `KESTREL_LOG=0` turns it off; `KESTREL_LOG_DIR` moves it.

## What a turn looks like

A question — the interesting case — records the whole path:

```json
{
  "event": "question", "turn": 4, "ts": "2026-09-23T14:02:11+00:00",
  "text": "tell me more about that",
  "retrieval_query": "why is the coolant pressure falling tell me more about that",
  "k": 6,
  "hits": [
    {"doc_id": "MAINT-0203", "chunk": 0, "distance": 0.31, "type": "maint",
     "access": "crew", "sensitive": false},
    {"doc_id": "LOG-WREN-0203", "chunk": 0, "distance": 0.38, "type": "log",
     "access": "crew", "sensitive": false}
  ],
  "answer": "Wrenfield replaced the seal on MD 203 (MAINT-0203) and...",
  "cited": ["MAINT-0203", "LOG-WREN-0203"],
  "ungrounded": [],
  "grounded": true,
  "model": "claude-opus-5", "input_tokens": 1840, "output_tokens": 96,
  "latency_ms": 1210, "stop_reason": "end_turn",
  "air_before": 15.25,
  "state": {"air_hours": 15.0, "tier": "crew", "identified": false,
            "alerts": 5, "confirmed": 2, "disposition": "steady"}
}
```

Read it as the answer to "why did KES say that?":

- **`text` vs `retrieval_query`** — the player typed four words; the search used eleven,
  because the previous question was prepended. If a follow-up retrieves nonsense, this line
  is where you see why.
- **`hits`** — exactly what was in KES's context, in order, with distances. Lower is closer.
  If the right document isn't in this list, nothing downstream could have saved the answer,
  and the fix is in the corpus or the chunking, not the prompt.
- **`access`** on each hit, plus **`tier`** in the state — these two together explain every
  "why doesn't KES know about the captain" mystery. It didn't know because the filter
  excluded it.
- **`disposition`** — KES's answer to a sensitive question is *supposed* to differ between
  *guarded* and *open*. Without this recorded, two different answers to the same question
  look like nondeterminism instead of the designed behaviour.
- **`model`** — with `fallbacks="default"`, a declined request is served by a different
  model. This tells you when that happened, which would otherwise be invisible.

Other events are smaller: `command` (accepted or refused, hours spent), `read` (found,
sealed, or nonexistent), `solve` (which entry, right or wrong, which attempt), `near_miss`
(a line that looked like a command and didn't parse), and `session_end`.

## The metric worth putting on a slide

**Citation groundedness.** For every document ID in KES's answer, was that document
actually among the chunks retrieved for that turn?

```
grounded citations: 100.0% (47/47)
```

It's computed with a regex and a set membership test. No judge model, no extra API calls,
no subjective scoring. And it's not a generic metric borrowed from a paper — it's the
failure this specific game cannot tolerate, because the player is *invited to click the
citations*. A fabricated `MAINT-9999` isn't a slightly-wrong answer; it's a dead link in
the middle of a puzzle.

This is worth understanding as a general principle: **the best eval metrics come from your
product's own promises.** The product promises "I will tell you what I read it from." The
metric measures exactly that promise. Generic RAG metrics — relevance scores, faithfulness
rubrics — would have been vaguer and needed a model to judge them.

If the number is 100%, it's a strong, specific, checkable claim. If it isn't, you've found
a real bug and you know which turn to look at.

## Using it

```bash
python report.py                      # every session: one summary block each
python report.py --last               # the most recent session, turn by turn
python report.py --ungrounded         # only the turns where a citation was invented
python report.py --corpus             # which documents get retrieved, cited, opened
python report.py --html report.html   # a self-contained page, one row per turn
```

The terminal detail view is the debugging one. It prints each question with its retrieved
documents, marks with `*` the ones KES went on to cite, and flags anything cited but never
retrieved:

```
[4] Q  tell me more about that
     embedded: why is the coolant pressure falling tell me more about that
     * 0.312  MAINT-0203 (chunk 0, crew)
     * 0.381  LOG-WREN-0203 (chunk 0, crew)
       0.442  SENS-THM (chunk 2, crew)
     A  Wrenfield replaced the seal on MD 203 and left the test field blank...
```

The HTML report is the one to screenshot for the presentation. It shows the metric cards
at the top and then every turn with its retrieval table underneath — the architecture
visible in one page, without anyone having to read prose.

## What to look for after a playtest

- **Any ungrounded citation.** Run `--ungrounded` first, every time. This should be zero.
- **Questions whose hits are all above ~0.6 distance.** The player asked something the
  corpus can't answer well. Either the corpus needs a document, or the golden-questions
  file needs their phrasing added.
- **Documents that are retrieved often but never opened.** They're winning searches they
  shouldn't — usually filler that's too generically written.
- **Documents never retrieved at all** (`--corpus`). Dead weight, or answering a question
  nobody asks.
- **A pile of `near_miss` events.** The parser or the manuals are failing people.
- **Oxygen per turn vs. entries confirmed.** Your pacing curve, measured instead of guessed.

## Where this goes next

The eval harness is the same data, run deliberately instead of incidentally: a fixed list
of questions, played through the same code path, with groundedness and retrieval hit@k
computed over the results. Because the logging already exists, an eval run is "play these
forty questions, then read the log" — which is why observability is worth building first.
