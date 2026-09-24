# RAG primer — how KES will know things

This explains the retrieval pipeline in the order the data flows through it, and points at
the code that does each step. Read it once before running `build_index.py`; come back to the
"Judging results" section when the query tool shows you something surprising.

## The problem RAG solves

A language model knows what it was trained on, and nothing about the ISV Kestrel. You could
paste the whole archive into every prompt — 98 documents, roughly 60,000 words — and for a
corpus this small that would technically work, but every KES reply would cost a full
archive's worth of tokens, take seconds longer, and the model would still have to find the
one relevant paragraph in a haystack every time.

RAG splits the job in two. A cheap, fast **retriever** finds the handful of passages most
relevant to the question. Then the expensive, clever **generator** (the LLM) writes an answer
using only those passages. The LLM's prompt says, in effect: "Here are six excerpts from the
ship's archive. Answer the crew member's question from these, cite the document IDs, and say
so if they don't cover it."

The whole game rests on the retriever being good. If the right chunk isn't in the six, KES
can't cite it, and a better prompt won't help.

## Step 1 — Chunking (`rag.chunk_document`)

Each JSON document becomes one or more **chunks**: pieces of text small enough that each is
about one thing.

Why not one chunk per document? Because of how embeddings work (next step): a vector is a
summary of a text's meaning, and the summary of a 600-word manual covering five procedures
is a blur that matches everything a little and nothing well. Cut it into five chunks and each
procedure gets a sharp vector of its own.

Why not tiny chunks? A single procedure step, out of context, is meaningless: "3. Enter
'restore pump b'" doesn't say what it's restoring or when it's safe. Chunks need enough
surrounding text to be understood on their own.

The rules used here, by document type:

| Type | Rule | Why |
| --- | --- | --- |
| manual, incident | ~200 words, split on paragraph boundaries | each procedure or section gets its own vector |
| log, msg, maint, kes, roster | whole document (they're short) | a diary entry is one thought; splitting it loses the voice |
| sensor | 10-row windows, column header repeated in each | a question about MD 214 lands on the rows near MD 214, and each window is still a readable table |

Every chunk gets a **citation header** on its first line — `[MAINT-0203 | maint | MD 203 | WREN]` —
so KES can name its source even when it only saw the middle of a document. And every chunk
carries the document's **metadata** (type, system, MD, author, access, private, sensitive) as
fields the database can filter on. `fault` is deliberately left out: it's an authoring aid
and KES must never see which documents are puzzle pieces.

Try it: `python query.py --doc MAN-THM-04` prints a document reassembled from its chunks.

## Step 2 — Embedding (`rag.embedding_function`)

An **embedding model** turns text into a vector: a list of a few hundred numbers. The model
is trained so that texts with similar meaning produce vectors pointing in similar directions.
"The coolant pressure is dropping" and "loop B is losing pressure" end up close together;
"the galley printer jammed" ends up far away. That is the entire trick. Meaning becomes
geometry, and "find relevant text" becomes "find nearby points".

The model here is Chroma's default, `all-MiniLM-L6-v2`: small, runs on your CPU, downloaded
once (~80 MB). It is not an LLM and does not generate anything. It only reads text and emits
numbers. Anthropic doesn't offer an embedding model, which is why this isn't Claude; if you
ever want a stronger one, the Claude docs point at Voyage AI, and swapping is a one-line
change in `rag.py` followed by a re-index (vectors from different models can't be mixed).

There's also `FakeEmbedding`, a word-hashing stub used only so the code can be tested
without downloading the model. It has no idea what words mean. Never use it for the game.

## Step 3 — Indexing (`build_index.py` → `rag.index_corpus`)

Indexing means: for every chunk, compute its vector once and store vector + text + metadata
in the database. Chroma is a **vector database**: a store whose one special ability is
"given a vector, find the stored vectors nearest to it", fast, with metadata filters. It
lives on disk under `index/v<seed>`, one collection per corpus variant.

Run it once per corpus and again after regenerating documents. `--stats` shows chunk counts
by type and access tier, which is a quick sanity check that the metadata came through.

## Step 4 — Retrieval (`rag.retrieve`)

At query time:

1. The player's question is embedded **with the same model** (this matters: question and
   chunks must live in the same vector space).
2. Chroma returns the k stored vectors closest to the question's vector. "Closest" is cosine
   distance: 0 means identical direction, 2 means opposite. Lower is better.
3. A **metadata filter** is applied *before* ranking, so forbidden chunks are never
   candidates at all.

The filter is how the game's access tiers work. Every document is in the index the whole
time. A player at `crew` tier gets `where access in [crew]`; at `engineering`, `[crew,
engineering]`; command adds the captain's logs and the EVA lock record. Sam's private logs
carry `private: sam` and are filtered out until the identity check passes. Unlocking a tier
is just changing the filter, so it's instant and works in any order.

Try it: `python query.py "what happened to the captain"` and then the same with
`--tier command`. INC-0218 appears only in the second.

## Step 5 — Generation (next file: `kes.py`)

Not built yet. The retrieved chunks go into the LLM prompt as context, with instructions:
answer from these, cite IDs, don't invent, evade on sensitive material. The rest of KES —
persona, glitches, the identity and passphrase checks — is prompt and game code, not RAG.

## Judging results (`query.py`, `eval_retrieval.py`)

The query tool prints hits with their distances. Rough guide for the default model:

- under 0.5: strong match, the chunk is about the question
- 0.5–0.7: plausible, often a document that mentions the topic in passing
- over 0.7: usually noise; the question just didn't have a good match

`golden_questions.json` is a list of questions a player will actually ask, each with the
documents that must come back. `eval_retrieval.py` runs them all and reports hit@k. Run it
after any change to the corpus, the chunking, or the model. If a question misses:

- **The document doesn't say it the way people ask.** "Why does the ship weigh more" won't
  find a sensor log whose only word for it is `hull_mass_reading_pct`. Fix by adding a
  plain-language line to the document (regenerate with an extra `include`), or by
  adding a second golden question phrased the way the corpus speaks.
- **The chunk is too big and the relevant sentence is buried.** Lower the target size for
  that type in `rag.chunk_document`.
- **The chunk is too small and lost its context.** Raise it, or improve the citation header.
- **k is too small.** Six is a good default for KES; ten is fine for exploring.

Aim for 85%+ with the real embedder before wiring up KES. Below that, tune here first.

## Things people get wrong the first time

- Embedding the question with a different model than the chunks. Everything returns nonsense
  with confident-looking distances.
- Forgetting to re-index after regenerating documents. The index is a snapshot.
- Filtering after retrieval instead of before. If you ask for 6 and then drop 4 for access
  reasons, you get 2. Pass the filter into the query.
- Trusting distances as truth. They rank; they don't judge. A 0.45 can still be the wrong
  document if the corpus has nothing better.
- Letting the LLM see metadata it shouldn't (like `fault`). If it's in the chunk text or
  metadata you hand over, assume the model will use it.
