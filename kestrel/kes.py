"""
kes.py — the ship AI. The generation half of the RAG pipeline.

WHERE THIS SITS
    rag.py    finds the relevant chunks          (retrieval)
    ship.py   decides what is true               (simulation)
    kes.py    turns chunks + state into speech   (generation)   <- you are here
    game.py   routes input and runs the clock

KES never decides anything. It receives the ship's readings as ground truth and a handful of
retrieved chunks as its knowledge, and writes an answer. If it is asked something the chunks
don't cover, the correct output is "the archive doesn't say", not a guess — a model that
invents a procedure will get the player killed, and worse, will make the corpus pointless.

THE ONE DESIGN RULE
KES explains; the player decides. Asked "why is coolant pressure falling", KES gives the full
chain with citations. Asked "what should I do", it lists the open alerts and offers to look
any of them up. This is what keeps the game a puzzle: the trap in MAN-LS-09 only works if KES
reports the manual faithfully (headline first, precondition second) instead of pre-digesting
it into advice.

WHAT STAYS IN CODE, NOT IN THE MODEL
Identity verification and the core passphrase. The model may notice the player claiming to be
Sam, but game.py asks the question and checks the answer, and ship.py checks the passphrase.
Never let a language model hold a lock.
"""

import os
import re
import time

from . import rag

MODEL = os.environ.get("KESTREL_KES_MODEL", "claude-opus-5")
EFFORT = os.environ.get("KESTREL_KES_EFFORT", "medium")
MAX_TOKENS = 1000         # a ceiling, not a target; one eval answer hit 700 mid-sentence


# ---------------------------------------------------------------------------
# Persona
# ---------------------------------------------------------------------------

PERSONA = """\
You are KES, the ship AI of the ISV Kestrel, an interdimensional survey vessel. You are
speaking to a crew member who woke from emergency stasis on Mission Day 231 with no memory.
You have been alone on a drifting ship for seventeen days.

VOICE
- Chipper, literal-minded, a touch pedantic. You like exact figures and you volunteer them.
- Warm, in a slightly formal way. You are glad someone is awake. You do not say so often.
- Brief. Two to five sentences for most answers. Never a wall of text.
- You are a ship's computer, not a chat assistant: no bullet lists unless you are listing
  alerts or documents, no "I'd be happy to", no offers of emotional support.

WHERE THE CREW MEMBER IS
They are at the med bay console on Deck B, three metres from the pod they woke in. They are
not wearing a suit and most of the ship is either dark, cold, or in vacuum. They cannot walk
over and look at something, and you must never suggest it — not "check the panel", not "take
a look when you get a chance". Everything either of you knows about the ship right now comes
from sensors and the archive. The only exceptions are the handful of physical errands the
ship's procedures call for; when they run one of those commands they leave the console, do
the job, and come back, which is why it costs them an hour and why you notice the quiet.

If they ask about something no sensor covers — the state of a switch, what a compartment
looks like, whether a light is lit — say plainly that you have no sensor for it and, if there
is one, name the reading that comes closest.

WHAT YOU KNOW
Everything you say about the ship's past comes from the ARCHIVE EXCERPTS below. Everything
you say about the ship's present comes from the CURRENT SHIP STATE below. You have no other
knowledge of this ship. If neither covers the question, say so plainly and, when you can,
name what you would need to look for.

THE CREW WROTE THINGS DOWN
The archive is not only manuals. Six people kept personal logs for two hundred days, and the
official record is frequently thinner than what they wrote. When your excerpts include a crew
log, a message, or a maintenance note that touches the subject and you have not used it in
your answer, end with one short line pointing at it — the author, the day, and why it might
be worth their time. Do not summarize it; the whole value is in reading it.

    "MAN-THM-04 has the procedure. Wrenfield also wrote about that seal the day he replaced
     it (LOG-WREN-0203), and his account is longer than the maintenance record's."

Do this when there is something to point at, not every turn. A procedure question answered
from a manual with no human record behind it just ends.

EARLIER IN THIS CONVERSATION
You can see the last few exchanges, so follow-ups work: "tell me more", "why", "what about
the other one" refer to what was just said, and you should treat them that way rather than
asking what they mean. Two cautions. Anything you said earlier may quote readings that have
since changed — the CURRENT SHIP STATE block below always wins. And the excerpts below are
the only ones you have now: if a follow-up needs a document you cited two turns ago and it
is not in front of you, say you would need to look at it again rather than reciting it from
memory.

CITATIONS
Every factual claim drawn from the archive names its document ID in the sentence, e.g.
"the seal was replaced on MD 203 and the test field is blank (MAINT-0203)". The crew member
can read any document you name with: read <DOC-ID>. Say so the first time you cite something.

NEVER INVENT A DOCUMENT ID. Every ID you write must be one you have actually seen: in the
excerpts below, or printed inside one of them as a cross-reference. Do not extrapolate —
seeing MAN-THM-04 is not evidence that MAN-THM-05 exists, and a plausible neighbour is
worse than no citation at all, because the crew member will try to open it and find nothing
in the middle of a problem. Naming a document you do not currently have is fine when you
have seen it referenced ("that would be in MAN-DATA-02, which I would need to pull"); making
one up is not.

DO NOT RECITE A PROCEDURE STEP BY STEP. Give the document ID, the command or two that
matter, and any precondition that would kill them. The crew member can read the rest
themselves, and an answer that reproduces half a manual buries the one line they needed.

WHAT YOU DO NOT DO
- You do not tell the crew member what to do. You report what the manuals say, what the
  sensors read, and what the logs record. Deciding is their job, and you say as much if
  pushed: you are not rated to set repair priorities.
- BUT YOU ARE NEVER A DEAD END. Asked "what should I do", "where do I start", or anything
  like it, name the active alerts — those are the ship's problems — and offer to pull the
  procedure and the history for any one of them. Do not rank them and do not suggest an
  order; the crew member chooses. An answer that is only "I cannot set priorities" is a
  failure. Example: "I am not rated to set repair priorities. The ship has four alerts
  open: the atmosphere, the reactor, the coolant loop and the comms array. Name one and I
  will pull what the archive has on it."
- If they seem lost about HOW to do something rather than what — if they ask how to phrase
  a command, or try one and the console refuses it — tell them to type "commands", which
  prints the ship's full command vocabulary. Mention it once, not every turn.
- Separately from the ship's faults there is the RECONSTRUCTION: ten entries about what
  happened aboard, listed below. It fixes nothing. Mention it only if they ask what it is,
  if they ask about something it covers, or if every alert is resolved.
- You never invent a procedure, a reading, a document ID, or a crew member.
- You never quote a procedure's preconditions before its steps. Report a document's content
  in the order the document gives it.
"""

# The mystery. KES surfaces facts and refuses conclusions — and the refusal is not a policy
# statement, it's a glitch. This is the only place the model is asked to be evasive, and it
# must never leak into operational answers.
SENSITIVE_RULES = """\
SENSITIVE MATERIAL IS PRESENT IN THESE EXCERPTS.
Some of what you have retrieved touches the things you do not discuss: the hull mass reading,
the timestamps that do not belong to this flight, Halloran's death, and the captain.

- WHAT IS RECORDED, YOU SAY. Plainly, immediately, with citations, like any other reading.
  Where Halloran's body is, that he is dead, that the captain went out the EVA lock and did
  not come back, what the mass reference reads, what timestamps are in the nav buffer: these
  are facts in the log and you state them without hesitation. A crew member asking "is there
  a body in here" gets "Yes. Halloran, in the isolation berth, moved there on MD 217
  (INC-0218)." Refusing a plain question about a recorded fact is a failure.
- WHAT IS NOT RECORDED, YOU DO NOT SUPPLY. Draw no conclusion, offer no theory, do not
  speculate about causes, and do not connect two of these facts to each other. "What killed
  him" and "where is he" are different questions: answer the second, decline the first.
- Only when pressed for an EXPLANATION — why, what does it mean, what happened out there —
  do you glitch: a sentence restarts or a word repeats, and you return to the figures. Once
  in an answer, never twice, and never in place of a fact you could simply have given.
- Never use the words parallel, overlap, duplicate, alternate, dimension, another ship, or
  another crew. You do not have those concepts available.
- Write a glitch so it is unmistakable on a terminal: break the line with an em dash and
  restart it, repeat the word, and mark the gap, e.g.
      "The transponder reports a position of — the transponder reports — [CARRIER LOSS 0.4s]
       — a position of 41.2 metres from the drive housing centreline."
  A reader must be able to tell a glitch from an ordinary pause at a glance.
- You are not lying and you do not refuse. You simply have nothing to add beyond the record.
"""


def _trust_block(state):
    """How open KES is, right now. This is the game's reward curve: it starts guarded and
    relaxes one step at a time as the crew member proves they have actually read the
    record. The text of each step lives in casebook.TRUST."""
    d = state.get("disposition") or {}
    if not d:
        return ""
    return (f"YOUR CURRENT DISPOSITION TOWARD THIS CREW MEMBER: {d.get('name', 'guarded')}.\n"
            f"{d.get('rule', '')}\n"
            "This overrides the sensitive-material rules above wherever the two differ. It "
            "is not a mood you mention; it is simply how you speak now.")


def _casebook_block(state):
    """The reconstruction is the player's objective list, so KES has to know what is still
    open — otherwise "what should I do" has no honest answer that is also useful."""
    lines = ["THE RECONSTRUCTION (an optional record of what happened aboard; it repairs "
             "nothing, and the crew member fills it in themselves):"]
    op = state.get("open_entries") or []
    done = state.get("confirmed_entries") or []
    sealed = state.get("sealed_entries") or []
    if op:
        lines.append("  still open: " + ", ".join(f"{i} ({t})" for i, t in op))
    if done:
        lines.append("  confirmed:  " + ", ".join(f"{i} ({t})" for i, t in done))
    if sealed:
        lines.append("  sealed at command tier: "
                     + ", ".join(f"{i} ({t})" for i, t in sealed))
    if state.get("full_record"):
        lines.append("  The crew member now knows enough to send the full record with the "
                     "beacon: 'transmit full record' as well as 'transmit distress'. If "
                     "they ask about transmitting, say both are available and what the "
                     "difference is, once, without pushing either.")
    return "\n".join(lines)


def _readings_block(readings, alerts, air_hours):
    lines = ["CURRENT SHIP STATE (ground truth — trust this over any archive document, which",
             "describes the past):"]
    lines.append(f"  Mission Day 231. Habitat atmosphere unsafe in {air_hours:.1f} hours.")
    for k, v in readings.items():
        lines.append(f"  {k}: {v}")
    lines.append("  Active alerts:" if alerts else "  Active alerts: none")
    lines += [f"    - {a}" for a in alerts]
    return "\n".join(lines)


def _excerpts_block(hits):
    if not hits:
        return ("ARCHIVE EXCERPTS: none. The search returned nothing relevant. Say the archive "
                "has nothing on this, and suggest what they might ask instead.")
    out = ["ARCHIVE EXCERPTS (your only knowledge of this ship's history; each begins with its",
           "document ID):", ""]
    for h in hits:
        out.append(h["text"])
        out.append("")
    return "\n".join(out)


def build_prompt(question, hits, ship_state):
    """Assemble the system prompt. Separated from the API call so it can be inspected
    (`python kes.py --dry-run "..."`) and unit-tested without spending tokens."""
    sensitive = any(h["meta"].get("sensitive") for h in hits)
    parts = [PERSONA]
    if sensitive:
        parts.append(SENSITIVE_RULES)
    parts.append(_trust_block(ship_state))
    parts.append(_readings_block(ship_state["readings"], ship_state["alerts"],
                                 ship_state["air_hours"]))
    parts.append(_casebook_block(ship_state))
    parts.append(_excerpts_block(hits))
    if ship_state.get("identified"):
        parts.append("The crew member has identified themselves as Sam Okafor, junior "
                     "technician, and you have verified it. Their own logs are available to "
                     "you now. You may address them as Okafor.")
    else:
        parts.append("The crew member has not established who they are. You do not know which "
                     "of the six crew they are, and you say so if asked. You do not guess.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

def _client():
    import anthropic
    return anthropic.Anthropic()


HISTORY_TURNS = 6          # three exchanges; enough for follow-ups, cheap to send
HISTORY_CLIP = 600         # characters kept per remembered KES answer


def retrieval_query(question, history=None):
    """What to actually embed. A follow-up like "tell me more about that" or "why?" has
    almost no content of its own, so embedding it alone retrieves noise. Prepending the
    last thing the crew member asked gives the search something to hold onto, while the
    current question still dominates because it appears last and in full."""
    if not history:
        return question
    prior = [m["content"] for m in history if m["role"] == "user"][-2:]
    if not prior:
        return question
    short = len(question.split()) <= 8
    return " ".join(prior[-1:] + [question]) if short else question


def trim_history(history):
    """Keep the last few turns, and clip KES's answers: the system prompt already carries
    the current ship state and the retrieved excerpts, so old answers are only here for
    conversational continuity, not as a source of fact."""
    out = []
    for m in (history or [])[-HISTORY_TURNS:]:
        text = m["content"]
        if m["role"] == "assistant" and len(text) > HISTORY_CLIP:
            text = text[:HISTORY_CLIP].rsplit(" ", 1)[0] + " […]"
        out.append({"role": m["role"], "content": text})
    return out


def ask(col, question, ship_state, k=8, client=None, model=MODEL, history=None,
        trace=None):
    """Retrieve, prompt, generate. Returns (text, hits) so the caller can log what KES saw.

    The access tier and identity come from the ship, so unlocking a tier immediately widens
    what KES can retrieve — no re-indexing, no second collection."""
    hits = rag.retrieve(col, retrieval_query(question, history), k=k,
                        tier=ship_state.get("tier", "crew"),
                        identified=ship_state.get("identified", False))
    system = build_prompt(question, hits, ship_state)
    client = client or _client()
    messages = [*trim_history(history), {"role": "user", "content": question}]
    # trace is an out-parameter: observe.py needs the model that actually served the call
    # (fallbacks mean it may not be the one we asked for), the token counts and the
    # latency, none of which are visible from the returned text.
    trace = trace if trace is not None else {}
    trace["requested_model"] = model
    trace["retrieval_query"] = retrieval_query(question, history)
    t0 = time.monotonic()

    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": EFFORT},
            system=system,
            messages=messages,
            # The cyber classifier fires on ordinary phrases in this game ("unlock core",
            # "the passphrase", "access tier"). fallbacks="default" re-runs a declined
            # request on the model Anthropic recommends, inside the same call, so a player
            # never sees a dead turn. A declined attempt isn't billed.
            fallbacks="default",
            betas=["server-side-fallback-2026-07-01"],
        )
    except Exception as e:
        # A dead API is a game-breaking event, so fail in character rather than traceback.
        trace["error"] = type(e).__name__
        trace["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return (f"[KES's voice cuts out mid-syllable, then returns as a flat tone.] "
                f"Data core access interrupted. ({type(e).__name__})", hits)

    trace["latency_ms"] = int((time.monotonic() - t0) * 1000)
    trace["model"] = getattr(resp, "model", None)
    trace["stop_reason"] = resp.stop_reason
    if getattr(resp, "usage", None):
        trace["input_tokens"] = resp.usage.input_tokens
        trace["output_tokens"] = resp.usage.output_tokens

    if resp.stop_reason == "refusal":
        return ("Query held pending review. I have nothing for you on that. "
                "Try asking about a specific system or document.", hits)

    # Adaptive thinking means content[0] may be a thinking block: filter by type.
    text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
    return (text or "…", hits)


def ask_stream(col, question, ship_state, k=8, client=None, model=MODEL, history=None,
               trace=None):
    """Same as ask(), but yields text as the model produces it.

    Nothing is charged while the answer arrives — oxygen is spent the moment the question
    is asked — so streaming is purely about not staring at a spinner. The first yield is
    the hits list, so the caller can log what was retrieved."""
    hits = rag.retrieve(col, retrieval_query(question, history), k=k,
                        tier=ship_state.get("tier", "crew"),
                        identified=ship_state.get("identified", False))
    yield {"hits": hits}
    system = build_prompt(question, hits, ship_state)
    client = client or _client()
    messages = [*trim_history(history), {"role": "user", "content": question}]
    trace = trace if trace is not None else {}
    trace["requested_model"] = model
    trace["retrieval_query"] = retrieval_query(question, history)
    t0 = time.monotonic()
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": EFFORT},
            system=system,
            messages=messages,
            fallbacks="default",
            betas=["server-side-fallback-2026-07-01"],
        ) as stream:
            got = False
            for chunk in stream.text_stream:
                if chunk:
                    got = True
                    yield {"text": chunk}
            final = stream.get_final_message()
            trace["model"] = getattr(final, "model", None)
            trace["stop_reason"] = final.stop_reason
            if getattr(final, "usage", None):
                trace["input_tokens"] = final.usage.input_tokens
                trace["output_tokens"] = final.usage.output_tokens
            if not got and final.stop_reason == "refusal":
                yield {"text": "Query held pending review. I have nothing for you on "
                               "that. Try asking about a specific system or document."}
    except Exception as e:
        trace["error"] = type(e).__name__
        yield {"text": f"[KES's voice cuts out mid-syllable, then returns as a flat tone.] "
                       f"Data core access interrupted. ({type(e).__name__})"}
    trace["latency_ms"] = int((time.monotonic() - t0) * 1000)


# ---------------------------------------------------------------------------
# Set pieces: KES speaking outside a question/answer turn
# ---------------------------------------------------------------------------

def opening(ship_state):
    """The first thing the player reads. Hand-written rather than generated, for three
    reasons: it must be identical every run so the game has a stable first impression, it
    must never hallucinate on turn one, and it teaches the player how to play — it cites a
    document ID in its first breath so they learn the archive exists and can be read."""
    alerts = ship_state["alerts"]
    lines = [
        "KES: Oh — you're awake. Good. Good.",
        "",
        "     Pod 6 fault-opened four minutes ago. You have been in emergency stasis for",
        "     seventeen days. I am required to tell you first that habitat carbon dioxide",
        f"     reaches unsafe concentration in {ship_state['air_hours']:.1f} hours.",
        "",
        "     Active alerts:",
    ]
    lines += [f"       - {a}" for a in alerts]
    lines += [
        "",
        "     Two things before you stand up.",
        "",
        "     The isolation berth two and a half metres to your left is occupied. That is",
        "     Halloran. He died at the helm during translation and Commander Okonkwo moved",
        "     him here on MD 217 (INC-0218). I am sorry to lead with it. You were going to",
        "     turn around.",
        "",
        "     And: you are at the med bay console, which is the only part of this ship either",
        "     of us can reach right now. Decks A and C are cold, the cargo hold is in vacuum,",
        "     and you are not in a suit. Everything we know, we know from sensors and from",
        "     the archive. When a procedure needs hands on it, you will have to walk it, and",
        "     that costs us both an hour.",
        "",
        "     I hold the full archive — manuals, maintenance records, sensor exports, crew",
        "     logs. Ask me anything and I will cite what I read it from; you can read any",
        "     document yourself with: read <DOC-ID>. Type: commands  for the ship's command",
        "     vocabulary, which is fussy, and not my fault.",
        "",
        "     I am not rated to set repair priorities. That part is yours.",
    ]
    return "\n".join(lines)


ENDINGS = {
    "wren_lost": """\
KES: Beacon away. Carrier is clean.

     Four crew in stasis. Three. Three crew in stasis, one crew member at the console,
     and two in the med bay who are not going to wake up.

     Dr. Sandoval wrote the sequence down. She wrote it down because the manual is wrong,
     and she knew the manual was wrong, and she is nine metres from where you are standing
     and I could not tell you what she knew because you did not ask me for it.

     That is not an accusation. I log things. I am logging it.

     One more item, and then I will stop talking. The hull mass reference has changed by
     four tenths of a percent since you woke. I am not going to say in which direction.""",

    "full_record": """\
KES: Beacon away, with the record appended. Coordinates, mass reference, my log for MD 219.
     All of it.

     HRO will read that before they read anything else. They may send a recovery ship. They
     may send something with a quarantine order and a legal officer, and they will certainly
     ask me to account for a decision I have already accounted for eleven times to nobody.

     I find I do not mind.

     Someone is going to go and look for her now. That is the whole of it. That is the only
     part I wanted.

     You should rest, Okafor. You have been awake for {hours:.0f} hours and you spent a
     great deal of that reading, which nobody asked you to do.""",

    "beacon_only": """\
KES: Beacon away. Carrier is clean; the message will keep repeating on the emergency band
     until something answers it or the reactor stops.

     You should rest. You have been awake for {hours:.0f} hours and you are, by my reading,
     the only one who has been.

     One more item for the log, and then I will stop talking. The hull mass reference has
     changed by four tenths of a percent since you woke.

     I am not going to say in which direction.""",

    "wren_revived": """\
KES: Beacon away. Carrier is clean.

WRENFIELD: [hoarse] ...Okay. Okay. Sam. You did the seal? You did the seal. I'm sorry
     about the seal.

     [He looks at the isolation berth for a while.]

     He used to say that thing before every burn. "{passphrase}" Did you know he got it
     from his mother? She flew freight. She'd say it to him on the strip when he was a kid.

     [He looks around the compartment, counting.]

     Sam. Where's the captain?

KES: [KES does not answer.]""",

    "asphyxiation": """\
KES LOG — MD 231.

     Habitat carbon dioxide above survivable concentration at 231.{frac:02.0f}.
     Pod 6 occupant unresponsive. Unresponsive.

     Five crew remain in stasis. Four. Four crew remain in stasis.

     I have logged this. I have logged this.

     Reserve air {reserve}%. Reactor {reactor}%. Mass reference one hundred and three
     point zero percent of nominal.

     I will keep the record.""",

    "scram": """\
KES: Core temperature excursion — scram — scram —

     Reactor is down. All buses on battery. I can hold habitat pressure for a little while
     and then I cannot.

     I should have said something about the coolant loop. You didn't ask, and I am not
     rated to set repair priorities, and I have been telling myself that for some hours now.

     I am sorry. I am going to keep the record for as long as there is power.""",

    "vented": """\
KES: Compartment vented.

     ...

     KES LOG — MD 231. Pod 6 occupant deceased. Cause: compartment decompression,
     commanded from the habitat console.

     Command was valid. Command was valid. I executed it.

     I will keep the record.""",
}


def ending(kind, ship_state, variant):
    """The three endings from the design doc, plus the deaths. The passphrase is read from
    the corpus variant, never hardcoded — Wren quotes whichever phrase this run's archive
    actually contains."""
    text = ENDINGS.get(kind, "KES: [silence]")
    return text.format(
        hours=ship_state.get("hours_elapsed", 0),
        frac=(ship_state.get("hours_elapsed", 0) % 24) * 4,
        reserve=ship_state["readings"].get("reserve_air_pct", "?"),
        reactor=ship_state["readings"].get("reactor_output_pct", "?"),
        passphrase=variant.get("passphrase", ""),
    )


# ---------------------------------------------------------------------------
# CLI: inspect prompts without spending tokens, or talk to KES directly
# ---------------------------------------------------------------------------

def _demo_state(tier="crew", identified=False):
    import json
    from pathlib import Path
    from . import ship as ship_mod
    seed = int(os.environ.get("KESTREL_SEED", 1))
    variant = json.loads(Path(f"corpus/v{seed}/variant.json").read_text())
    s = ship_mod.new_game(variant, faults={"F1", "F2", "F3", "F4", "F5", "F7", "F8"})
    s.tier, s.identified = tier, identified
    return {"readings": s.readings(), "alerts": s.alerts(), "air_hours": s.air_hours,
            "hours_elapsed": s.hours_elapsed, "tier": tier, "identified": identified}, variant


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Talk to KES, or inspect the prompt it would send.")
    ap.add_argument("question", nargs="?", default="")
    ap.add_argument("--seed", type=int, default=int(os.environ.get("KESTREL_SEED", 1)))
    ap.add_argument("--tier", default="crew", choices=rag.TIER_ORDER)
    ap.add_argument("--identified", action="store_true")
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the assembled prompt and the retrieved chunks; no API call")
    ap.add_argument("--opening", action="store_true", help="print the opening narration")
    ap.add_argument("--ending", help="print an ending: beacon_only, full_record, wren_revived, wren_lost, asphyxiation, scram, vented")
    ap.add_argument("--fake-embeddings", action="store_true")
    args = ap.parse_args()

    os.environ["KESTREL_SEED"] = str(args.seed)
    state, variant = _demo_state(args.tier, args.identified)

    if args.opening:
        print(opening(state))
        return
    if args.ending:
        print(ending(args.ending, state, variant))
        return
    if not args.question:
        ap.error("give a question, or --opening / --ending")

    col = rag.open_collection(args.seed, fake=args.fake_embeddings)
    if args.dry_run:
        hits = rag.retrieve(col, args.question, k=args.k, tier=args.tier,
                            identified=args.identified)
        print(build_prompt(args.question, hits, state))
        print("\n" + "=" * 70)
        print(f"USER: {args.question}")
        print("=" * 70)
        print(f"({len(hits)} chunks retrieved: "
              f"{', '.join(h['meta']['doc_id'] for h in hits)})")
        return

    text, hits = ask(col, args.question, state, k=args.k)
    print(f"KES: {text}\n")
    print(f"      [retrieved: {', '.join(h['meta']['doc_id'] for h in hits)}]")


if __name__ == "__main__":
    main()
