"""
rag.py — the retrieval layer for KES.

This is the only file that knows about the vector database. Everything else (the indexer,
the query tool, the game) calls these five functions:

    chunk_document(rec)           split one corpus document into chunks + metadata
    open_collection(seed, ...)    open (or create) the Chroma collection for a corpus
    index_corpus(seed, ...)       read corpus/v<seed>/*.json, chunk, embed, store
    retrieve(col, question, ...)  embed a question, find the closest chunks, apply filters
    get_document(col, doc_id)     stitch a document back together from its chunks

HOW RAG WORKS, IN ONE PARAGRAPH
An embedding model turns a piece of text into a list of numbers (a vector) such that texts
with similar meaning get vectors that point in similar directions. We embed every chunk of
the corpus once and store the vectors. At query time we embed the player's question with the
same model, ask the database for the stored vectors closest to it, and hand those chunks to
the LLM as context. The LLM never sees the whole corpus, only the handful of chunks that
are nearest to the question. That's retrieval-augmented generation: retrieve first, then
generate.

WHY CHUNK AT ALL
Embeddings summarize a text into one vector. A 600-word manual has five different procedures
in it; one vector for all of them is a blur, and a question about step 3 would match it
weakly. Splitting into 150–250-word chunks gives each procedure its own vector, so the
match is sharp. Too small and a chunk loses the context needed to understand it (a step
without its heading). The rules below are the compromise for each document type.
"""

import hashlib
import json
import os
import math
import re
from pathlib import Path

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

CORPUS_ROOT = Path("corpus")
INDEX_ROOT = Path("index")

# Access tiers are ordered: a player at `engineering` can see crew + engineering documents.
TIER_ORDER = ["crew", "engineering", "command"]


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _header(rec):
    """The citation tag prepended to every chunk so KES can always name its source,
    even when it only saw the middle of a document."""
    return f"[{rec['doc_id']} | {rec['type']} | MD {rec['md']} | {rec['author']}]"


def _split_words(text, target=200, hard_max=320):
    """Split prose into chunks of about `target` words on paragraph boundaries.
    Paragraphs are kept whole unless one alone exceeds hard_max."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur, cur_len = [], [], 0
    for p in paras:
        n = len(p.split())
        if cur and cur_len + n > target:
            chunks.append("\n\n".join(cur))
            cur, cur_len = [], 0
        if n > hard_max:  # a wall of text: split it on sentences
            sentences = re.split(r"(?<=[.!?])\s+", p)
            buf = []
            for s_ in sentences:
                buf.append(s_)
                if len(" ".join(buf).split()) >= target:
                    chunks.append("\n\n".join(cur + [" ".join(buf)]))
                    cur, cur_len, buf = [], 0, []
            if buf:
                cur.append(" ".join(buf))
                cur_len += len(" ".join(buf).split())
        else:
            cur.append(p)
            cur_len += n
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def _split_sensor(text, window=10):
    """Sensor exports are CSV. Embedding a 30-row table as one blob buries the interesting
    rows; a 10-row window with the column header repeated keeps each window readable on
    its own and lets a question about MD 214 land on the rows around MD 214."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # Everything before the first CSV row (the title line) plus the column header line.
    head_end = next((i for i, ln in enumerate(lines) if ln.count(",") >= 2), 1)
    preamble = lines[:head_end]
    columns = lines[head_end] if head_end < len(lines) else ""
    rows = [ln for ln in lines[head_end + 1:] if ln.count(",") >= 2]
    if not rows:
        return [text]
    chunks = []
    for i in range(0, len(rows), window):
        block = rows[i:i + window]
        chunks.append("\n".join(preamble + [columns] + block))
    return chunks


def chunk_document(rec):
    """Return a list of (chunk_id, text, metadata) for one corpus record.

    Every chunk carries the full document metadata, so retrieval can filter on access
    tier, privacy, and sensitivity without looking at the text. `fault` is deliberately
    NOT stored: it's an authoring aid and KES must never see it."""
    body = rec["body"]
    t = rec["type"]
    if t == "sensor":
        pieces = _split_sensor(body)
    elif t in ("manual", "incident"):
        pieces = _split_words(body, target=200)
    elif t in ("log", "msg", "maint", "kes"):
        # A crew log is 200 words of diary in which the fact that matters appears once.
        # Embedded whole, that one detail is averaged away to nothing: "who snores" could
        # not find the log that says the kid snores, because the vector was mostly about
        # packing foam and baselines. Paragraph-sized chunks give each beat its own vector.
        pieces = [body] if len(body.split()) <= 90 else _split_words(body, target=80,
                                                                    hard_max=160)
    else:
        pieces = [body] if len(body.split()) <= 350 else _split_words(body, target=250)

    header = _header(rec)
    out = []
    for i, piece in enumerate(pieces):
        meta = {
            "doc_id": rec["doc_id"], "type": rec["type"], "system": rec["system"],
            "md": int(rec["md"]), "author": rec["author"], "access": rec["access"],
            "private": rec["private"], "sensitive": bool(rec["sensitive"]),
            "chunk_index": i, "chunk_count": len(pieces),
        }
        out.append((f"{rec['doc_id']}#{i}", f"{header}\n{piece}", meta))
    return out


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

class FakeEmbedding(EmbeddingFunction):
    """TEST ONLY. A bag-of-words hash into 256 dimensions. It has no idea what words mean,
    so it only matches documents that share literal words with the question. It exists so
    the indexing code can be exercised with no model download. Never ship a game on it."""

    @staticmethod
    def name():
        return "fake_hash"

    def __call__(self, input: Documents) -> Embeddings:
        vecs = []
        for text in input:
            v = [0.0] * 256
            for w in re.findall(r"[a-z0-9]+", text.lower()):
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                v[h % 256] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vecs.append([x / norm for x in v])
        return vecs

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return FakeEmbedding()


# Chroma's default is all-MiniLM-L6-v2, trained for sentence-to-sentence similarity:
# "the cat sat on the mat" vs "a cat is on a mat". That is not this task. Here a six-word
# question is matched against a three-hundred-word document, which is asymmetric search,
# and models trained on question-passage pairs are markedly better at it. multi-qa-MiniLM
# is the same size and speed, trained on 215M question-answer pairs.
# Measured on this corpus, not assumed. multi-qa-MiniLM is trained on question-answer
# pairs and is the textbook choice for asymmetric search, so it should have won — it
# scored 59% against the default's 64% on the same index and the same questions. General
# benchmarks are not your corpus. Set KESTREL_EMBED_MODEL to try another; re-index after.
EMBED_MODEL = os.environ.get("KESTREL_EMBED_MODEL", "default")


def embedding_function(fake=False, model=None):
    """The embedder. Changing it means re-indexing: vectors from different models are not
    comparable, and a mixed index returns confident nonsense.

    Falls back to Chroma's default if sentence-transformers isn't installed, so a fresh
    clone still works — but prints why, because the difference is measurable."""
    if fake:
        return FakeEmbedding()
    name = model or EMBED_MODEL
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    if not name or name == "default":
        return DefaultEmbeddingFunction()
    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
        return SentenceTransformerEmbeddingFunction(model_name=name)
    except Exception as e:
        print(f"  [embeddings] could not load {name} ({type(e).__name__}); falling back to "
              f"Chroma's default. `pip install sentence-transformers` for better retrieval.")
        return DefaultEmbeddingFunction()


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

def open_collection(seed, fake=False, reset=False):
    """One Chroma collection per corpus variant, stored on disk under index/v<seed>.
    Cosine distance: 0 = identical direction, 2 = opposite. Distances below ~0.6 are
    usually relevant with MiniLM; treat the number as a ranking, not a truth value."""
    path = INDEX_ROOT / f"v{seed}"
    client = chromadb.PersistentClient(path=str(path))
    name = f"kestrel_v{seed}"
    if reset:
        try:
            client.delete_collection(name)
        except Exception:
            pass
    try:
        return client.get_or_create_collection(
            name, embedding_function=embedding_function(fake),
            metadata={"hnsw:space": "cosine"})
    except ValueError as e:
        if "embedding function" not in str(e).lower():
            raise
        # The index was built with a different embedder. Vectors from two models are not
        # comparable, so the only correct answers are "use that model" or "re-index" —
        # never "carry on", which would return confident nonsense.
        import re as _re
        was = _re.search(r"persisted: (\S+)", str(e))
        raise SystemExit(
            f"\nThis index was built with a different embedding model"
            f"{' (' + was.group(1) + ')' if was else ''}, and vectors from two models "
            f"cannot be compared.\n"
            f"Either set the same model for this command:\n"
            f"    KESTREL_EMBED_MODEL={was.group(1) if was else '<model>'} python ...\n"
            f"or rebuild the index with the current one:\n"
            f"    python build_index.py --seed {seed}\n")


def index_corpus(seed, fake=False, reset=True, log=print):
    """Read every document for a corpus, chunk it, and store the chunks.
    Chroma computes the embeddings when we call add(); we only pass text."""
    src = CORPUS_ROOT / f"v{seed}"
    files = sorted(p for p in src.glob("*.json") if p.name not in ("manifest.json", "variant.json"))
    if not files:
        raise SystemExit(f"no documents in {src}")
    col = open_collection(seed, fake=fake, reset=reset)

    ids, docs, metas = [], [], []
    for p in files:
        rec = json.loads(p.read_text())
        for cid, text, meta in chunk_document(rec):
            ids.append(cid)
            docs.append(text)
            metas.append(meta)
    # add() in batches: embedding a few hundred texts at once is fine, thousands is slow.
    for i in range(0, len(ids), 100):
        col.add(ids=ids[i:i + 100], documents=docs[i:i + 100], metadatas=metas[i:i + 100])
        log(f"  indexed {min(i + 100, len(ids))}/{len(ids)} chunks")
    log(f"{len(files)} documents → {len(ids)} chunks in {INDEX_ROOT / f'v{seed}'}")
    return col


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def access_filter(tier="crew", identified=False):
    """Build the Chroma `where` clause that enforces the game's access rules.

    This is the whole trick behind locked documents: they're in the index the entire
    time, but a filter keeps them out of the results until the player earns the tier.
    Filtering at query time (rather than keeping separate indexes) means unlocking is
    instant and the game can hand out tiers in any order."""
    allowed = TIER_ORDER[: TIER_ORDER.index(tier) + 1]
    clauses = [{"access": {"$in": allowed}}]
    if not identified:
        clauses.append({"private": "none"})   # Sam's private logs hide until the unlock
    return {"$and": clauses} if len(clauses) > 1 else clauses[0]


def retrieve(col, question, k=8, tier="crew", identified=False, where_extra=None,
             per_document=True, expand_best=True, keyword=True):
    """Embed the question and return the k best permitted results, best first.

    k=8 is measured, not guessed. A sweep over the golden questions gave, for "all the
    expected documents" / "at least one of them": k=4 50%/85%, k=6 64%/90%, k=8 73%/95%,
    k=10 80%/95%, k=16 89%/100%. The second number is the one that decides whether KES can
    answer at all, and it saturates at eight; past that the strict number keeps climbing
    only because it is a greedy metric, while every extra chunk costs tokens and dilutes
    the model's attention across the context.

    `per_document` is the important default. Chroma returns the k nearest *chunks*, and a
    six-hundred-word manual is three of them, so a query that lands on one document can
    spend half the budget on it and crowd out the other documents the answer needs. Since
    KES's job is to cross-reference — the procedure in one document, the cause in another,
    the reading in a third — breadth beats depth here. So we over-fetch chunks, keep the
    best chunk of each document, and return k distinct documents.

    `expand_best` then adds the chunk immediately following the top hit, if there is one.
    Procedures run across a chunk boundary, and the step you need is often the next one
    down. This is cheap (no extra search) and costs one slot.

    `keyword` makes this hybrid search. Embeddings are averages, so a single rare word in
    a long document barely moves its vector: "who snores" could not find the log that says
    the kid snores, because the word occurs once in two hundred words about something else.
    Exact matching is the opposite — it is useless at paraphrase and perfect at rare terms.
    Running both and merging covers each other's blind spot, which is why nearly every
    production RAG system is hybrid rather than pure vector.
    """
    where = access_filter(tier, identified)
    if where_extra:
        where = {"$and": [where, where_extra]}

    want = k * 4 if per_document else k
    res = col.query(query_texts=[question], n_results=want, where=where,
                    include=["documents", "metadatas", "distances"])
    raw = [{"id": cid, "text": text, "distance": dist, "meta": meta}
           for cid, text, dist, meta in zip(res["ids"][0], res["documents"][0],
                                            res["distances"][0], res["metadatas"][0])]
    if keyword:
        raw = _merge_keyword(col, question, raw, where, want)
    if not per_document:
        return raw[:k]

    best, seen = [], set()
    for h in raw:                      # raw is already sorted by distance
        doc = h["meta"]["doc_id"]
        if doc not in seen:
            seen.add(doc)
            best.append(h)
        if len(best) == k:
            break

    if expand_best and best:
        nxt = _adjacent(col, best[0], tier, identified)
        if nxt:
            best.insert(1, nxt)
    return best


# Words too common to be worth matching literally. Short, because the corpus is small and
# a rare-ish word is exactly what we want to catch.
_STOP = set("""a an the and or but if of to in on at by for from with without is are was
were be been being do does did done have has had can could should would will shall may
might must i me my we us our you your it its this that these those there here what which
who whom whose when where why how not no yes so than then too very just about into over
under out up down off again more most some any all each every other another""".split())


def _terms(question, max_terms=3):
    """The parts of a question worth an exact-match lookup.

    Three kinds, in priority order. Identifiers like C-7, TR-2 or MAINT-0203 are the most
    literal things a player can type and embed the worst. Then phrases like "pod 6" or
    "injector 2" — meaningless as separate tokens, precise together, and all over this
    corpus. Then ordinary long words, longest first as a rough stand-in for rarest.
    """
    q = question.lower()
    codes = re.findall(r"\b[a-z]{1,5}-\d{1,4}\b|\b[a-z]-j?\d+\b", q)
    phrases = [f"{a} {b}" for a, b in
               re.findall(r"\b([a-z]{3,})\s+(\d{1,3})\b", q) if a not in _STOP]
    words = [w for w in re.findall(r"[a-z]{4,}", q) if w not in _STOP]
    words.sort(key=len, reverse=True)
    out = []
    for t in [*codes, *phrases, *words]:
        if t not in out:
            out.append(t)
    return out[:max_terms]


# A term is only worth an exact-match lookup if it is rare. "snores" appears in one chunk
# of the whole archive and embeddings cannot see it; "pressure" appears in dozens and the
# embedding handles it perfectly well. Matching on a common word does not rescue anything,
# it floods the results and evicts the hits that were actually relevant — which is exactly
# what an earlier version of this function did, dropping hit@6 from 64% to 45%.
KEYWORD_MAX_DF = 6          # a term in more than this many chunks is not discriminative
KEYWORD_MAX_HITS = 2        # never spend more than this many of k's slots on rescues


def _doc_frequency(col, term, where, ceiling=KEYWORD_MAX_DF):
    """How many chunks literally contain this term, counted only up to the ceiling —
    we do not care whether it is 7 or 700, only that it is too many."""
    n, variants = 0, {term, term.title(), term.upper()}
    ids = set()
    for v in variants:
        try:
            res = col.get(where=where, where_document={"$contains": v},
                          include=[], limit=ceiling + 1)
        except Exception:
            return None                   # an older Chroma without where_document
        ids.update(res["ids"])
        if len(ids) > ceiling:
            return ceiling + 1
    return len(ids)


def _merge_keyword(col, question, vector_hits, where, want):
    """Add chunks that literally contain a RARE word from the question.

    Rescues go at the back of the list, never the front: the vector ranking is usually
    right, and the keyword path exists to catch the specific case it cannot see — one
    unusual word buried in a long document. At most two slots are given up for it.
    """
    rescues = []
    seen = {h["id"] for h in vector_hits}
    for term in _terms(question, max_terms=4):
        if len(rescues) >= KEYWORD_MAX_HITS:
            break
        df = _doc_frequency(col, term, where)
        if df is None:
            return vector_hits            # keyword search unavailable
        if df == 0 or df > KEYWORD_MAX_DF:
            continue                      # absent, or too common to discriminate
        for v in {term, term.title(), term.upper()}:
            try:
                res = col.get(where=where, where_document={"$contains": v},
                              include=["documents", "metadatas"], limit=KEYWORD_MAX_DF)
            except Exception:
                continue
            for cid, text, meta in zip(res["ids"], res["documents"], res["metadatas"]):
                if cid in seen or len(rescues) >= KEYWORD_MAX_HITS:
                    continue
                seen.add(cid)
                # distance is nominal: no vector comparison lies behind it. The `keyword`
                # field is what a log should key on.
                rescues.append({"id": cid, "text": text, "distance": 0.5,
                                "meta": meta, "keyword": term})
    if not rescues:
        return vector_hits
    # keep the vector ranking intact and give up only the tail
    keep = max(0, want - len(rescues))
    return vector_hits[:keep] + rescues + vector_hits[keep:]


def _adjacent(col, hit, tier, identified):
    """The chunk right after a hit, if the document has one. A manual's preconditions sit
    one chunk below its procedure, which is exactly the pair a player needs."""
    meta = hit["meta"]
    nxt = meta["chunk_index"] + 1
    if nxt >= meta["chunk_count"]:
        return None
    res = col.get(ids=[f"{meta['doc_id']}#{nxt}"],
                  include=["documents", "metadatas"])
    if not res["ids"]:
        return None
    return {"id": res["ids"][0], "text": res["documents"][0],
            "distance": hit["distance"], "meta": res["metadatas"][0]}


_ALL_IDS_CACHE = {}


def all_doc_ids(col):
    """Every document ID in the archive. Needed to tell apart two very different things
    that look identical from a single turn's point of view: a citation KES invented, and a
    citation it read inside a document it was shown. Manuals cross-reference each other —
    MAN-COM-06 genuinely says "per MAN-DATA-02" — so relaying that is correct behaviour,
    and the player can open it. Only an ID that exists nowhere is a hallucination."""
    key = id(col)
    if key not in _ALL_IDS_CACHE:
        res = col.get(include=["metadatas"])
        _ALL_IDS_CACHE[key] = {m["doc_id"] for m in res["metadatas"]}
    return _ALL_IDS_CACHE[key]


def document_exists(col, doc_id):
    """Is there such a document at all, ignoring access? Used so the game can tell the
    player 'that exists but you aren't cleared for it' instead of 'no such document',
    which was indistinguishable from a typo and made cited-but-locked documents baffling."""
    res = col.get(where={"doc_id": doc_id}, include=[], limit=1)
    return bool(res["ids"])


def get_document(col, doc_id, tier="crew", identified=False):
    """Reassemble a full document from its chunks for the `read <doc-id>` command.
    Returns None if it doesn't exist or the player isn't allowed to see it — the game
    should answer 'no such document' in both cases, so the refusal itself isn't a clue."""
    where = {"$and": [{"doc_id": doc_id}, access_filter(tier, identified)]}
    res = col.get(where=where, include=["documents", "metadatas"])
    if not res["ids"]:
        return None
    order = sorted(range(len(res["ids"])), key=lambda i: res["metadatas"][i]["chunk_index"])
    header_len = len(_header({**res["metadatas"][order[0]], "doc_id": doc_id})) + 1
    pieces = [res["documents"][i][header_len:] for i in order]
    # Sensor windows repeat the title and column header; keep them once.
    if res["metadatas"][order[0]]["type"] == "sensor" and len(pieces) > 1:
        first = pieces[0].splitlines()
        n_pre = next((i for i, ln in enumerate(first) if ln.count(",") >= 2), 1) + 1
        pieces = [pieces[0]] + ["\n".join(p.splitlines()[n_pre:]) for p in pieces[1:]]
    return {"doc_id": doc_id, "meta": res["metadatas"][order[0]], "body": "\n".join(pieces)}
