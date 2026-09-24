# ISV Kestrel

A text game where the only way to save a dying starship is to ask its AI the right questions.

You wake from emergency stasis with no memory, seventeen days after an interdimensional
translation went wrong. The reactor is at 40%, the coolant loop is losing pressure, the
scrubbers have stopped regenerating, and you have about ten hours of breathable air. KES,
the ship AI, is alive and helpful and holds the ship's entire archive — two hundred days of
manuals, maintenance records, sensor exports and crew diaries. It will answer anything you
ask and cite what it read. It will not tell you what to do.

KES is a language model backed by retrieval over a vector database. The archive is real: a
hundred documents, generated and consistency-checked, with the answers to every puzzle
planted inside them. The ship itself is ordinary deterministic code, and the two never mix.

```
> what's wrong with the coolant
KES: Loop B pressure has been declining since MD 203 and dropped sharply at
     translation (SENS-THM). The seal on pump B was replaced on MD 203; the
     maintenance record's pressure-test field is blank (MAINT-0203). Wrenfield
     also wrote about that seal the day he replaced it (LOG-WREN-0203), and his
     account is longer than the record's.

> isolate pump b
KES: Pump B isolated. Loop A carrying full thermal load, nominal at current output.
     [1h elapsed. 16.5h of atmosphere remaining.]
```

## How it works

Four pieces, deliberately separated:

**The corpus is generated, not written.** `kestrel/bible.py` holds a world bible and a plan
for ~100 documents: for each one, its type, author, date, access tier, and the exact facts it
must plant. `kestrel/generate.py` turns each into a prompt and writes a JSON document with
metadata. Puzzle solutions are distributed across three to five documents so no single
question solves a fault.

**Retrieval is the game mechanic, not just plumbing.** Every chunk carries `access` (crew /
engineering / command), `private`, and `sensitive` metadata. Locked documents are in the index
the whole time; a metadata filter at query time keeps them out of results until the player
earns the tier. Unlocking the data core is a filter change, and KES's knowledge widens
instantly. The `sensitive` flag switches KES into a different mode mid-conversation.

**The simulation has no AI in it and never will.** `kestrel/ship.py` decides what is true: reactor
state, coolant pressure, the clock, what kills you. KES reads documents and explains them.
If the model decided outcomes it would accept a fix that doesn't exist, and the game would
stop having rules. `python -m kestrel.ship` runs eleven self-tests covering the winning path and
every way to die.

**The archive is a case file, and trust is the reward.** Alongside the repairs runs a
reconstruction: ten things that happened aboard, assembled by the player out of documents
that never state them outright. It repairs nothing and you can win without it. What it
changes is KES. The AI begins guarded — recorded facts only, no conclusions, and it glitches
when pushed — and relaxes one visible step at a time as the player proves they have read the
record, until it volunteers the thing it has spent the whole game refusing to say. Confirming
enough of it also unlocks a second ending, where the distress beacon carries the anomaly
record and someone goes looking for the captain.

**The only clock is oxygen, and it only moves when you do.** Nothing ticks in real time.
A question costs a quarter of an hour, a document a few minutes, a repair an hour, and
filling in the reconstruction is free. Reading carefully is a choice with a visible price
rather than a race against a countdown.

**The manuals are complete, official, and sometimes wrong.** HRO's revival procedure gives
two commands with no interval between them. Opening a stasis pod before the occupant reaches
temperature kills them. The correction exists in exactly one place: the medical officer's
personal log, written by someone asleep nine metres from where you are standing.

## Keeping a hundred generated documents consistent

This was most of the work, and the interesting part. An LLM writing a hundred documents in
isolation will contradict itself constantly — and every contradiction is a broken puzzle,
because the player solves faults by cross-referencing documents that have to agree.

Three layers, applied in that order:

1. **Canonical state injected into prompts.** A `state_at(md)` function gives the ship's
   readings for every Mission Day after the translation, and every document dated 214 or
   later receives them. `fixed_facts(variant, md)` supplies the run's randomized facts —
   which injector sticks, where the spares are, the passphrase — gated by date, so a
   document dated MD 60 is told the bet doesn't exist yet and a document dated MD 180 is
   told how it came out.
2. **A heuristic lint** (`kestrel/lint.py`): invented people, forbidden words, timeline
   slips, off-variant locker references, stasis crew acting after MD 214. Fast, noisy,
   catches the mechanical failures.
3. **An LLM verifier** with a written spec (`docs/VERIFY_CORPUS.md`): reads every document against
   the plan and the design bible and reports semantic contradictions the lint cannot see,
   with a paste-ready regeneration list.

The discipline that made it work: **every defect was fixed in the generator, never in the
document.** A one-off patch fixes one corpus; a prompt fix means the next corpus is born
clean. Across three independently randomized corpora:

| Corpus | Pass 1 defects | Root cause |
| --- | --- | --- |
| v1 | 12 | No shared ship state; documents invented their own readings |
| v1 (pass 2) | 6 | Variant facts not reaching documents that mentioned them in passing |
| v1 (pass 3) | 6 | Cross-references to manual sections that didn't exist |
| v2 | 5 | Date-blind fact injection; sensor rows interpolating their own curve |
| v3 | 1 | Two sub-day sensor rows off by 1.4 points — cosmetic |

v3 was generated from the corrected generator and came back essentially clean on its first
pass, which is the evidence that the fixes were structural rather than lucky.

## Observability

Every turn is written to `logs/session-<timestamp>-v<seed>.jsonl`: the question, the query
actually embedded, every retrieved chunk with its distance and access tier, the answer,
the model that served it, tokens, latency, and the ship state at the time.

The metric this exists for is **citation groundedness** — for every document ID KES names,
was that document actually in its context? It needs no judge model, and it measures the
product's own promise: the player is invited to click those citations, so a fabricated one
is a dead link in the middle of a puzzle.

```bash
python -m kestrel.report                      # one summary block per session
python -m kestrel.report --last               # the most recent session, turn by turn
python -m kestrel.report --ungrounded         # only turns where a citation was invented
python -m kestrel.report --html report.html   # a self-contained page, one row per turn
```

See `docs/OBSERVABILITY.md`.

## Evaluating retrieval

`evals/golden_questions.json` holds twenty questions a player will actually ask, each with the
documents that must be retrieved. `evals/retrieval.py` runs them and reports two numbers:
how many of the expected documents came back, and how many questions got **at least one**
— the second being what decides whether KES can answer at all. It takes seconds, needs no
API calls, and runs after any change to the corpus, the chunking, or the embedding model.

It has earned its place several times over. The history, all measured on the same corpus:

| Change | all expected | at least one |
| --- | --- | --- |
| Starting point, k=6 | 55% | — |
| Retrieve distinct *documents* rather than chunks | 64% | 90% |
| Swap to a retrieval-trained embedder (`multi-qa-MiniLM`) | **59%** | — |
| Hybrid keyword search on every term | **45%** | — |
| Hybrid on rare terms only, capped | 64% | 90% |
| k=8, chosen from a sweep | **73%** | **95%** |

Two of those are regressions, and both were shipped-looking ideas. Keyword search on every
term flooded the results with matches on words like "pressure" and evicted the relevant
hits. The retrieval-trained embedder is the textbook choice for short-question/long-document
search and lost to Chroma's default on this corpus by five points. Neither would have been
caught by reading the code.

## Evaluating responses

`evals/retrieval.py` asks whether the right documents came back. `evals/responses.py` asks
the next question: given that they did, did KES do the right thing with them? They fail
independently, and the fix is in a different place each time — bad retrieval means the
corpus or the chunking, bad behaviour means the prompt.

The interesting part is that this system is not a function from question to answer. "What
happened to the captain" has three different correct answers depending on state: nothing at
crew tier, flat facts at command tier while KES is still guarded, and an admission of the
MD 218 decision once it has opened up. So a test case is not `(question, expected)` but
`(state, question, assertions)`, and the state-dependence stops being noise to control for
and becomes the property under test.

That is only practical because `kestrel/ship.py` is deterministic: any state can be constructed in
a line instead of played to over forty turns. The separation that keeps the model from
deciding outcomes is the same one that makes the system testable.

Eighteen cases, one API call each, a few cents a run:

| What it checks | Why this metric and not a generic one |
| --- | --- |
| grounded citations | the player is invited to click them, so a fabricated one is a dead link in a puzzle |
| tier leakage | access control is a game mechanic; a leak breaks the unlock, not just the answer |
| suggested commands parse | KES tells the player what to type, so an invented command breaks play |
| forbidden vocabulary | one use of "parallel" or "another ship" unravels the mystery at any disposition |
| abstention | asked something the archive does not contain, it must say so rather than invent |
| disposition behaviour | the same question at *guarded* and at *open* must differ, correctly |

Every assertion is mechanically decidable — a substring, a set membership, a call to the
real command parser. No judge model: an LLM-graded rubric would cover more and be worth
much less, because nobody could check the number afterwards.

Current: **17/18 cases, 97.7% of citations real** (42 of 43; one fabricated `MAN-PROP-03`).

Getting there took two corrections, both found by running it:

**The model invented document IDs.** It cited `MAN-THM-05` and `MAN-THM-06` — plausible
neighbours of a real `MAN-THM-04`, neither of which exists. Since citations are clickable,
that is a dead link in the middle of a puzzle. Fixed in three layers: a prompt rule (four
fabrications down to one), a runtime filter in `kestrel/game.py` that rewrites any ID absent from
the archive, and this eval to check the first two are holding. The eval calls `kes.ask`
directly, so 97.7% measures the model's raw output; what reaches a player is filtered.

**The metric itself was wrong.** It flagged "cited but not retrieved", which conflates two
opposite behaviours. `MAN-COM-06` genuinely prints "per MAN-DATA-02", so relaying that
cross-reference is the archive working as designed, and KES naming a document it would need
to pull is exactly what it was told to do. Only an ID that exists *nowhere* is a
hallucination. Before the fix the harness was failing correct behaviour and would have sent
anyone debugging it in the wrong direction — and the same mistake in the runtime filter
would have broken every valid cross-reference in the game.

```bash
python -m evals.responses --dry-run     # the cases and their states, no API calls
python -m evals.responses               # run them
python -m evals.responses --only tier   # just the access-control cases
```

`--sweep` reports coverage across k, which is how the numbers below were chosen. Retrieval
width is tied to KES's disposition, so confirming reconstruction entries genuinely widens
the search: 6 documents per question at *guarded*, rising to 14 at *open*. Six is the floor
on purpose — the sweep puts practical coverage at 90% there, which is workable, and guarded
is also the tutorial. The console shows the current width and the next threshold, so the
progression is legible rather than subliminal.

## Layout

```
kestrel/            the game and its machinery
  bible.py          the world: context, crew voices, canonical state, and the document plan
  generate.py       builds a corpus from the plan, one API call per document
  lint.py           heuristic consistency checks over a generated corpus
  rag.py            chunking, embedding, hybrid retrieval, access filters
  index.py          loads a corpus into Chroma
  query.py          ask the index a question and see the chunks. No LLM
  ship.py           the deterministic simulation. `python -m kestrel.ship` self-tests
  casebook.py       the reconstruction: entries, checking, KES's trust ladder
  kes.py            prompt assembly, model call, persona, endings
  game.py           routing, the oxygen cost of a turn, terminal front end
  observe.py        session logging, one JSONL line per turn
  report.py         reads the logs: summaries, per-turn detail, HTML reports
  server.py         FastAPI wrapper around Session.handle()
  static/           the web console

evals/              separate from the package because they test it, not belong to it
  retrieval.py      do the right documents come back?
  responses.py      given that they did, does KES behave?
  golden_questions.json

docs/
  DESIGN_BIBLE.md   premise, crew, timeline, fault chains, endings, the real explanation
  RAG_PRIMER.md     how the pipeline works, step by step, tied to the code
  OBSERVABILITY.md  what is logged, why, and how to read it
  VERIFY_CORPUS.md  the spec for the LLM verification pass

corpus/  index/  logs/    generated, and gitignored
```

Everything runs as a module from the repository root:

```bash
python -m kestrel.generate --seed 1     # build a corpus (costs API tokens)
python -m kestrel.index --seed 1        # index it
python -m kestrel.server                # play in a browser
python -m kestrel.game                  # play in the terminal
python -m kestrel.ship                  # the simulation's self-tests
python -m kestrel.report --last         # what happened in the last session
python -m evals.retrieval --seed 1      # retrieval eval
python -m evals.responses --dry-run     # response eval, without spending anything
```

## Models

Corpus generation uses Claude Fable 5.1, where voice quality matters and latency doesn't.
KES runs on Claude Opus 5, which is faster and cheaper per turn, and latency matters when
every reply is read rather than skimmed. Both send `fallbacks="default"`:
the cybersecurity classifier fires on ordinary phrases in this game ("unlock core", "the
passphrase"), and the fallback re-runs a declined request on another model inside the same
call, so a player never sees a dead turn.

## Setup

1. **Python 3.10 or newer.** Check with `python3 --version`.

2. **Create a virtual environment** in the project folder so the packages stay local:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate        # macOS / Linux
   .venv\Scripts\activate           # Windows PowerShell
   pip install -r requirements.txt
   ```

3. **Get an API key.** Sign in to the Claude Platform console at https://platform.claude.com,
   confirm your credits are on this organization, and create a key under API keys. The key
   is shown once; copy it.

4. **Put the key in your environment.** The SDK reads `ANTHROPIC_API_KEY` automatically.

   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."          # macOS / Linux, current shell only
   $env:ANTHROPIC_API_KEY = "sk-ant-..."           # Windows PowerShell, current shell only
   ```

   To make it stick, add the export line to `~/.zshrc` or `~/.bashrc` (or use
   `setx ANTHROPIC_API_KEY "sk-ant-..."` on Windows and open a new terminal). Never commit
   the key; if you use git, add `.venv/` and any `.env` file to `.gitignore`.

5. **Verify without spending anything:**

   ```bash
   python -m kestrel.generate --dry-run
   ```

   This prints the full document plan (98 documents for the default variant) and one
   sample prompt. No API calls are made.

6. **Smoke test with three documents:**

   ```bash
   python -m kestrel.generate --limit 3
   ```

   Open `corpus/v1/MAN-LS-03.json` and read it. If the voice or format is off, edit
   `WORLD`, `STYLE`, or the document's `include` list in `kestrel/bible.py`, then regenerate
   that one document with `python -m kestrel.generate --only MAN-LS-03 --force`.

7. **Generate the whole corpus:**

   ```bash
   python -m kestrel.generate
   ```

   The run skips any document already on disk, so you can stop and restart freely. Failed
   documents are listed at the end; re-running retries only those.

## Cost and time

Fable 5.1 list price is $10 per million input tokens and $50 per million output tokens.
Each document sends roughly 1,500 input tokens and returns 200–700 output tokens, so the
full 98-document corpus is on the order of $3–5 and ten to fifteen minutes with three
workers. The script prints the actual token counts and an estimate at the end.

`--effort` defaults to `medium`, which is plenty for prose in a fixed voice. Try `high`
for the crew logs if the voices feel flat (`--only log --effort high --force`).

## Randomization

`--seed N` picks a variant: which injector sticks, which panel is breached, where the
scrubber spares are, Halloran's passphrase, and Sam's bet. Each seed writes to its own
`corpus/vN/` directory with a `variant.json` recording the choices. Generate two or three
seeds now while the credits last; the game picks one at random per run.

To choose options yourself, list the pools with `--show-options`, then pass `--variant`
once per choice, by index or exact text:

```bash
python -m kestrel.generate --seed 2 --variant passphrase=2 --variant spares_location=1 --variant sticky_injector=3
```

`sticky_injector` takes the injector number itself (2 or 3), not an index. Once a corpus
directory exists, its `variant.json` is the source of truth: regenerating one document
uses the stored variant, not the seed, so it always matches its neighbors. Changing a
stored variant is refused unless you pass `--force`, which regenerates the whole directory.

## What the game reads

Each `<doc_id>.json` has:

```json
{
  "doc_id": "MAINT-0203", "type": "maint", "system": "THM", "md": 203,
  "author": "WREN", "access": "crew", "private": "none",
  "sensitive": false, "fault": "F3",
  "body": "MD: 203\nSystem: THM\n..."
}
```

`access`, `private` and `sensitive` are the fields the RAG layer filters on. `fault` is an
authoring aid for playtesting and must not be exposed to KES.

## Notes on Fable 5.1

- Adaptive thinking is always on; the script reads text blocks by type rather than
  assuming `content[0]` is text.
- No `thinking` parameter is sent (disabling it returns a 400).
- Fable 5.1 runs safety classifiers. The `cyber` one fires on benign text about passphrases,
  locked systems and access tiers, which this corpus is full of. The script sends
  `fallbacks="default"` (beta), so a declined request is re-run on the Opus model Anthropic
  recommends inside the same call; the declined attempt is not billed. Each JSON records
  which model served it in `"model"`, and the run log marks fallbacks. A refusal that no
  fallback serves is listed as a failure at the end.
- Model and default effort can be overridden with the `KESTREL_MODEL` and `KESTREL_EFFORT`
  environment variables, e.g. `KESTREL_MODEL=claude-opus-5` to compare output.

## Why Chroma

The corpus is a few hundred chunks, so vector-search performance is irrelevant here: a query
is microseconds against an LLM call of a second or more. The choice came down to setup cost.
Chroma installs with pip, stores to a local directory with no server to run, and bundles a
local embedding model, so `build_index.py` is the only setup step and a fresh clone works
after `pip install -r requirements.txt`.

Postgres with pgvector was the main alternative and is the stronger choice for anything
longer-lived: one datastore for vectors and game state, real transactions, SQL for ad-hoc
queries over the corpus, and no second system to keep in sync. It costs an explicit embedding
step (pgvector stores vectors, it does not compute them) and a running server. Both use HNSW
indexes and would perform comparably at any size this project will reach.

`kestrel/rag.py` is the only module that touches the database — five functions — so swapping the
backend is contained if this ever grows into something with its own Postgres.

## Indexing and querying

```bash
python -m kestrel.index --seed 1 --stats
python -m kestrel.query "why is coolant pressure dropping"
python -m kestrel.query "what happened to the captain" --tier command
python -m kestrel.query --doc MAINT-0203
python -m evals.retrieval --seed 1
```

The first `build_index.py` run downloads the embedding model (about 80 MB). Re-run it after
regenerating any documents; the index is a snapshot of the corpus. See `docs/RAG_PRIMER.md`.

## Playing

```bash
python -m kestrel.generate --seed 1      # once, if you haven't
python -m kestrel.index --seed 1          # once per corpus
python -m kestrel.game --seed 1                 # play
python -m kestrel.game --faults F1,F2,F3,F5     # a fixed set of faults instead of a random draw
```

Or play it in a browser:

```bash
python -m kestrel.server                        # then open http://127.0.0.1:8000
python -m kestrel.server --seed 3
```

The web console shows what the terminal makes you type for: air remaining and the decision
window as live clocks, active alerts and sensor readings as panels, and a three-deck
schematic marking where the faults are and why you cannot reach them. Every document ID KES
cites is a link that opens the document beside the transcript, which is the main thing the
terminal version made tedious. "How to play" pauses the clock.

KES's answers stream as they are written. The console shows the reconstruction panel and
KES's current disposition, live alerts and readings, a three-deck schematic, and a glossary;
every document ID KES cites opens beside the transcript, with sensor exports rendered as
tables rather than raw CSV and any out-of-place value picked out in amber.

Omit `--seed` and it picks at random from whichever corpora you have indexed, so each run
can have a different passphrase, breach panel, and spares location.

Everything costs oxygen: a question to KES a quarter of an hour, a document a few minutes,
a ship command an hour. Filling in the reconstruction is free, and a command the ship
refuses costs nothing. `help` lists the interface, `commands` prints the command vocabulary,
`casebook` shows the reconstruction, `status` the readings, `read <DOC-ID>` any document.
