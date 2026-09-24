"""
kestrel_bible.py — world data and the document plan for the ISV Kestrel corpus.

Everything the generator needs is data in this file:
  WORLD          shared context every prompt gets (ship, mission, tone, rules)
  CREW           character sheets
  STYLE          per-document-type writing rules
  VARIANT_OPTIONS the per-run randomization pool
  build_plan()   returns the list of document specs for a given variant

Edit this file to change what gets generated. generate_corpus.py never
needs to change for content edits.
"""

import random

# ---------------------------------------------------------------------------
# Shared world context (goes in every system prompt)
# ---------------------------------------------------------------------------

WORLD = """\
SHIP: ISV Kestrel (Interdimensional Survey Vessel). Six berths. Small, over-engineered,
built for the third crewed translation ever attempted. Ship AI: KES.

DECKS (forward to aft):
  Deck A: bridge, comms suite, sensor bay, forward airlock
  Deck B: habitat ring (cabins 1-6), galley, med bay and stasis pods 1-6, science lab, data core
  Deck C: engineering, reactor bay, coolant plant, translation drive housing, cargo hold
          (lockers C-1 to C-12, aft bulkhead panels C-1 to C-12), EVA lock,
          maintenance crawlways (junctions C-J1 to C-J4) connecting everything

SYSTEMS: reactor and power distribution (PWR), thermal/coolant loops A and B (THM),
life support: CO2 scrubber beds 1 and 2, regeneration cycle, reserve air tank (LS),
hull/structural (HULL), comms array and transmitter (COM), navigation and star tracker (NAV),
medical and stasis pods (MED), data core with access tiers crew/engineering/command (DATA),
attitude control RCS quads 1-4 (PROP), translation drive (TDR).

MISSION: survey run from Sol to an adjacent continuum the physicists call "the Shelf".
Plan: translate in, 30 days mapping, translate home. Dates are Mission Days (MD).
MD 0 departure. MD 1-210 transit to the translation point. MD 214 translation event.

CREW (exactly these six people; nobody else is aboard, and no other named person exists
in this archive — no extra crew, no contractors, no named ground controllers; HRO mission
control is only ever "HRO" or "control"):
  Cmdr. Adaeze Okonkwo — captain (cabin 1, pod 1)
  Tobias "Wren" Wrenfield — chief engineer (cabin 2, pod 2)
  Dr. Priya Sandoval — medical officer (cabin 3, pod 3)
  Marcus Halloran — pilot / navigator (cabin 4, pod 4)
  Yuki Tanabe — comms and data systems (cabin 5, pod 5)
  Sam Okafor — junior technician (cabin 6, pod 6)
KES is the ship AI. Everyone refers to each other by these names or surnames.

NOMINAL FIGURES (fixed for the whole archive; never invent a different value):
  Reactor: three fuel injectors, numbered 1, 2 and 3. There are no others. Normal transit
    output is 98% of rated; the normal operating envelope is 95-100%.
  Coolant: two loops, A and B, each with one pump. Loop pressure nominal is 410 kPa.
  Life support: two scrubber beds, 1 and 2. Reserve air tank reads 88% at full nominal load.
  Attitude control: four RCS quads, 1 to 4. Star tracker is TRK-A.
  Stasis: six pods, 1 to 6, matching cabins 1 to 6. Occupant IDs are exactly:
    OKO-01 Okonkwo, WRE-02 Wrenfield, SAN-03 Sandoval, HAL-04 Halloran, TAN-05 Tanabe,
    OKA-06 Okafor. No other ID format exists.
  Hull mass: reported only as a percentage of reference mass, never in tonnes.

WHO IS AWAKE AFTER MD 214 (every document dated MD 214 or later must respect this):
  MD 214 onward: Wrenfield, Sandoval, Tanabe and Okafor are unconscious in stasis pods 2, 3,
    5 and 6 and cannot speak, act, help, or be consulted. Halloran is dead.
  MD 216 to MD 218: Okonkwo is the ONLY person awake. She talks only to KES. She does
    everything alone. Nobody offers her anything.
  MD 218 onward: nobody is awake. KES is alone until pod 6 opens on MD 231.

POD OCCUPANCY (never contradicted by any document):
  Pod 4 (Halloran) is NEVER occupied or sealed on any Mission Day. He flies the translation
    from the helm, the single exception to stasis, and dies there.
  Pod 1 (Okonkwo) is occupied MD 214-216, opened from the inside at MD 216, and empty ever after.
  Pods 2, 3, 5, 6 are occupied from MD 214. Pod 2 runs on battery after MD 214.
  Pod 6 fault-opens at MD 231. After MD 217.03 the med bay isolation berth holds Halloran.

SHIP STATE CANON AFTER MD 214:
  Reactor output is 40% from MD 214 until the player changes it. Nobody restarts anything.
  Coolant loop B is running at LOW PRESSURE and is NOT isolated. KES cannot isolate a coolant
    loop or recertify a seal without a crew command; the pressure alarm stays active.
  Reserve air falls from 88% at MD 214 to 31% at MD 231 because KES draws on it to hold
    habitat pressure against the slow cargo-hold leak. Any document quoting reserve air
    uses the SHIP STATE figure it is given, never a figure from an earlier entry.
  Hull mass is always reported as a percentage of the reference mass (100.0% before MD 214,
    103.0% after), never in tonnes.
  Halloran has exactly ONE pre-burn phrase, the one given as the passphrase. He never has a
    second ritual line, variant, or alternative wording. If a document mentions his phrase
    it uses that exact text or refers to it without quoting.
  Recurring details defined elsewhere (Sam's bet with Halloran, the pump B seal story, the
    patch kit's location) are reused exactly, never re-invented with new specifics.

PLAYER COMMANDS (procedures in manuals must use these exact phrasings):
  run scrubber regen | swap cartridges <locker> | atmosphere flush
  cycle injector <n> | restart injector <n>
  isolate pump b | recertify seal b | restore pump b
  retrieve patch kit | patch <panel> | repressurize cargo
  align antenna | transmit distress | unlock core <passphrase>
  purge nav buffer after md <n> | run star fix
  reroute habitat bus medbay | revive pod <n>
  disable quad <n> | null rotation
  read <doc-id>

TONE: pulpy on the surface. Crew logs are chatty, funny, human, with low-grade friction.
Manuals are dry and corporate (the builder is Halvard-Reyes Orbital, HRO). KES is chipper,
literal-minded, a bit of a pedant. Underneath everything there is a chill that is never
explained.

HARD RULES:
- Output ONLY the document body. No preamble, no commentary, no markdown code fences,
  no headings that repeat the document ID.
- Dates are always "MD <n>". Never use Earth calendar dates.
- Never explain the mystery. Never use the words "parallel", "overlap", "duplicate",
  "another ship", "other crew", "alternate", or "dimension" as an explanation for anything.
  Odd details are recorded flatly, as facts, without a theory.
- Plant every MUST INCLUDE fact naturally, in passing, as this author would. Never flag it,
  never emphasize it, never make it the headline.
- Never contradict the timeline. A document dated MD n knows nothing after MD n.
- Never invent people. If a document needs to mention someone, it is one of the six crew,
  KES, or the faceless "HRO". Never invent, name, or describe any person or crew that is
  not on the roster above, in any context, including rumor, dream, or sound.
"""

# ---------------------------------------------------------------------------
# Crew
# ---------------------------------------------------------------------------

CREW = {
    "OKONKWO": dict(
        name="Cmdr. Adaeze Okonkwo", role="Captain",
        voice="Dry, precise, economical. Writes personal logs like incident reports: "
              "short declaratives, no exclamation marks, the occasional very dry joke. "
              "Refers to crew by surname."),
    "WREN": dict(
        name="Tobias 'Wren' Wrenfield", role="Chief engineer",
        voice="Brilliant, sloppy, funny. Run-on sentences, technical slang, self-deprecating. "
              "Cuts corners and mentions it cheerfully as if it were nothing. Calls the "
              "reactor 'the kettle' and the ship 'the bird'."),
    "SANDOVAL": dict(
        name="Dr. Priya Sandoval", role="Medical officer",
        voice="Warm, sardonic, observant. Keeps the best personal logs: small human details, "
              "gentle mockery of everyone, real affection underneath. Calls Okafor 'the kid'."),
    "HALLORAN": dict(
        name="Marcus Halloran", role="Pilot / navigator",
        voice="Cocky, charming, superstitious about the drive. Short punchy entries, lots of "
              "pilot jargon, pet rituals. Has a phrase he says before every burn and is "
              "proud of it. From MD 205 his entries get quieter and stranger."),
    "TANABE": dict(
        name="Yuki Tanabe", role="Comms and data systems",
        voice="Quiet, careful, methodical, a little paranoid. Lists things. Thinks about "
              "contingencies nobody asked her to think about. Understated humor."),
    "OKAFOR": dict(
        name="Sam Okafor", role="Junior technician",
        voice="Enthusiastic, gallows humor, slightly clumsy phrasing, asks the obvious "
              "questions. Signs maintenance entries 'S.O.'. Snores. Owes people coffee."),
    "KES": dict(
        name="KES", role="Ship AI",
        voice="Chipper, literal, pedantic, fond of exact figures. After MD 214 its logs "
              "fragment: dropped words, repeated phrases, sentences that restart. It never "
              "speculates."),
}

# ---------------------------------------------------------------------------
# Per-type style guides
# ---------------------------------------------------------------------------

STYLE = {
    "manual": """\
An HRO operations manual section. Header line: "HRO OPS MANUAL — <SYSTEM NAME> — Section <id>",
where <id> is the document's full ID exactly as given, e.g. "Section MAN-MED-01" (never "MED-01").
Then a short purpose paragraph, then numbered procedure steps. Procedure steps use the exact
player command phrasings in quotation marks. Preconditions and warnings come AFTER the
procedure steps, in a "Notes" list, one line each — never before. Dry, corporate, precise.
Cross-reference other sections by ID where natural (e.g. "see MAN-LS-05").""",

    "maint": """\
A maintenance log entry. Fields on separate lines: "MD:", "System:", "Location:", "Action:",
"Parts:", "Test result:", "Signed:". Then 2-5 sentences of free-text notes in the signer's
voice. Terse. A blank test-result field is written as "Test result: " with nothing after it.""",

    "sensor": """\
A sensor log export. First line: "SENSOR LOG — <SYSTEM> — exported MD 231 by KES".
Then a CSV table with a header row and 20-35 data rows. Columns are given in the brief.
Timestamps are "MD <n>.<hh>" (e.g. MD 203.14). Readings are plausible numbers with units
in the header. After the table, one line: "END OF EXPORT". No commentary.""",

    "log": """\
A personal crew log entry. First line: "PERSONAL LOG — <name> — MD <n>". Then 120-300 words
in the author's voice, first person. It reads like a real diary: mood, small events,
complaints, jokes. Facts to plant are mentioned in passing, never as the point of the entry.""",

    "incident": """\
A KES-authored incident report. Header lines: "INCIDENT REPORT <id>", "Filed by: KES",
"MD:", "Classification:". Sections: "Summary", "Sequence of events" (timestamped MD n.hh),
"Actions taken", "Open items". Formal, exact, unemotional. Reports dated after MD 214 show
mild fragmentation: an occasional repeated word or a sentence that restarts.""",

    "kes": """\
A KES drift log entry, written to nobody. First line: "KES LOG — MD <n>". 50-150 words.
Status figures, then fragments. The fragmentation increases with the MD: by MD 226 sentences
restart mid-way and phrases repeat. KES never speculates and never uses the forbidden words.
It records things and then records that it has recorded them.""",

    "msg": """\
A message from the ship's internal message archive. The first line is exactly the document
ID, then " — MD <n> — From: <name> — To: <name or ALL>", e.g. "MSG-0207-11 — MD 207 — From:
Sam Okafor — To: Wrenfield". Do not write "MSG" a second time. Then 30-100 words. Casual, functional, the kind of thing crew send each
other: requests, banter, reminders, complaints about the galley.""",

    "roster": """\
A ship document, tabular, KES-maintained. Header line with title and "Maintained by: KES,
current as of MD 231". Then a plain-text table. No commentary.""",
}

# ---------------------------------------------------------------------------
# Per-run randomization
# ---------------------------------------------------------------------------

VARIANT_OPTIONS = {
    "sticky_injector": [2, 3],
    "breached_panel": ["C-9", "C-4", "C-11"],
    "spares_location": ["cargo locker C-7", "crawlway junction C-J3", "science lab cabinet L-2"],
    "passphrase": [
        "Kestrel, kestrel, don't you fall",
        "Easy now, birdie, easy now",
        "Light the candle and hold the line",
    ],
    "sam_bet": [
        "Sam bet Halloran a week of coffee runs that the galley printer would jam before MD 180. "
        "It held. Sam lost and owes the coffee",
        "Sam bet Halloran coffee for a month that Wren could not go ten days without saying 'the kettle'. "
        "Wren managed it. Sam lost and owes the coffee",
        "Sam bet Halloran coffee for everyone that the Shelf would look blue on the first scans; "
        "Halloran said grey. The pre-jump survey scans came back grey. Sam lost and owes the coffee",
    ],
}


# ---------------------------------------------------------------------------
# Canonical ship state by Mission Day, injected into every document dated >= 214
# so KES logs, incident reports, sensor exports and the roster all agree.
# ---------------------------------------------------------------------------

_STATE_POINTS = {  # md: (reserve_air_pct, habitat_co2_ppm, mass_pct)
    214: (88.0, 1900, 103.0),
    216: (80.7, 2600, 103.0),
    219: (69.7, 3400, 103.0),
    222: (58.7, 4100, 103.0),
    226: (46.2, 5000, 103.0),
    230: (34.3, 5900, 103.0),
    231: (31.0, 6100, 103.0),
}


def state_at(md):
    """Return the canonical ship state dict at Mission Day md (>= 214)."""
    keys = sorted(_STATE_POINTS)
    lo = max(k for k in keys if k <= md)
    hi = min((k for k in keys if k >= md), default=lo)
    a, b = _STATE_POINTS[lo], _STATE_POINTS[hi]
    t = 0 if hi == lo else (md - lo) / (hi - lo)
    reserve = round(a[0] + (b[0] - a[0]) * t, 1)
    co2 = int(a[1] + (b[1] - a[1]) * t)
    pods = {1: "OCCUPIED (Okonkwo)" if md < 216 else "OPEN — INTERNAL RELEASE (empty since MD 216)",
            2: "OCCUPIED (Wrenfield) — BATTERY power", 3: "OCCUPIED (Sandoval) — nominal",
            4: "EMPTY — never occupied (Halloran at helm)", 5: "OCCUPIED (Tanabe) — nominal",
            6: "OCCUPIED (Okafor) — nominal" if md < 231 else "OPEN — FAULT (MD 231)"}
    berth = "Halloran, deceased (moved from helm MD 217.03)" if md >= 217.03 else "empty"
    return dict(md=md, reactor_output_pct=40, injector_status="1 ONLINE; the other two SAFE",
                loopA="nominal", loopB="LOW PRESSURE, not isolated, alarm active",
                reserve_air_pct=reserve, habitat_co2_ppm=co2, scrubber_regen="idle",
                cargo_hold="depressurized, slow leak", hull_mass_pct=103.0,
                nav="no valid star fix; buffer holds entries timestamped MD 240-244",
                comms="offline, no carrier", pods=pods, isolation_berth=berth,
                awake=("Okonkwo only" if 216 <= md < 218 else "nobody"))


def state_block(md):
    st = state_at(md)
    pods = "\n".join(f"    Pod {k}: {v}" for k, v in st["pods"].items())
    return (f"SHIP STATE AT MD {md} (canonical; every figure in this document must match):\n"
            f"  Reactor output: {st['reactor_output_pct']}%  Injectors: {st['injector_status']}\n"
            f"  Coolant loop A: {st['loopA']}  Loop B: {st['loopB']}\n"
            f"  Reserve air: {st['reserve_air_pct']}%  Habitat CO2: {st['habitat_co2_ppm']} ppm  "
            f"Scrubber regen: {st['scrubber_regen']}\n"
            f"  Cargo hold: {st['cargo_hold']}  Hull mass: {st['hull_mass_pct']}% of reference\n"
            f"  Nav: {st['nav']}  Comms: {st['comms']}\n"
            f"  Stasis pods:\n{pods}\n"
            f"    Isolation berth: {st['isolation_berth']}\n"
            f"  Awake: {st['awake']}")


def fixed_facts(v, md=0):
    """Variant-specific facts every document must respect, as known on Mission Day md.
    Facts that have not happened yet by md are withheld or stated as future, so a document
    dated MD 60 cannot know how a bet on MD 180 came out."""
    inj = v["sticky_injector"]
    premise, _, outcome = v["sam_bet"].partition(". ")
    lines = [
        "FIXED FACTS FOR THIS ARCHIVE (every document, including filler, uses these exactly):",
        f"  CO2 scrubber cartridge spares are stowed in {v['spares_location']}. No other location, ever.",
        "  The hull patch kit lives in the EVA locker" + (
            " until MD 140, then in crawlway junction C-J2." if md >= 140 else "."),
        f"  Injector {inj} is the one that sticks on cold restart and must be hand-cycled first"
        + (" (discovered MD 150)." if md >= 150 else "; nobody knows this yet, so do not mention it."),
    ]
    if md >= 214:
        lines.append(f"  The cargo hold breach is at aft bulkhead panel {v['breached_panel']}.")
    if md >= 209:
        lines.append("  The data core passphrase was set by Tanabe (data systems officer) and locked on "
                     "MD 209. HRO holds no copy. The command authorization codes are stored inside the core.")
    else:
        lines.append("  Tanabe (data systems officer) will set and lock the data core passphrase shortly "
                     "before the jump; as of this document she has not yet done so, and no date for it "
                     "is known. Command authorization codes are stored inside the core.")
    lines.append("  Only documents whose brief supplies Halloran's phrase may quote it; everyone else "
                 "refers to it without stating it.")
    if md >= 180:
        lines.append(f"  The bet: {premise}. {outcome}. Coffee is owed by Okafor from MD 180 on. "
                     "Nobody remembers it differently.")
    elif md >= 165:
        lines.append(f"  A bet is in progress and undecided: {premise}. It is settled on MD 180; "
                     "as of this document nobody knows the outcome.")
    else:
        lines.append("  No bet between Sam and Halloran exists yet. Do not mention one.")
    return "\n".join(lines) + "\n"


def state_rows(start=214, end=231):
    """Per-day canonical readings for sensor exports covering the post-translation period."""
    rows = ["STATE BY DAY (a sensor row at any of these MDs uses exactly these figures; every "
            "row from MD 214 onward falls on a whole Mission Day, timestamp MD <n>.00, one row "
            "per day, no sub-day rows):",
            "  MD   reactor%  reserve_air%  habitat_co2_ppm  hull_mass%"]
    for md in range(start, end + 1):
        st = state_at(md)
        rows.append(f"  {md:<4} {st['reactor_output_pct']:<9} {st['reserve_air_pct']:<13} "
                    f"{st['habitat_co2_ppm']:<16} {st['hull_mass_pct']}")
    return "\n".join(rows)


def manual_index():
    """ID → subject for every manual section, so cross-references point at real sections."""
    return [(d["doc_id"], d["brief"].split(" Nominal")[0].rstrip(".")) 
            for d in build_plan(make_variant(0)) if d["type"] == "manual"]


def make_variant(seed=None):
    rng = random.Random(seed)
    return {k: rng.choice(v) for k, v in VARIANT_OPTIONS.items()}


# ---------------------------------------------------------------------------
# Document plan
# ---------------------------------------------------------------------------

def _doc(doc_id, type_, system, md, author, brief, include=(), avoid=(),
         access="crew", private="none", sensitive=False, fault="none", words=None):
    return dict(doc_id=doc_id, type=type_, system=system, md=md, author=author,
                access=access, private=private, sensitive=sensitive, fault=fault,
                brief=brief, include=list(include), avoid=list(avoid), words=words)


def build_plan(v):
    """Return the ordered list of document specs for variant dict v."""
    inj = v["sticky_injector"]
    other_inj = 3 if inj == 2 else 2
    panel = v["breached_panel"]
    spares = v["spares_location"]
    phrase = v["passphrase"]
    bet = v["sam_bet"]
    docs = []
    D = docs.append

    # ----- Manuals that carry clues -----
    D(_doc("MAN-LS-03", "manual", "LS", 0, "HRO",
           "Scrubber bed regeneration cycle.",
           include=["Procedure is the single command 'run scrubber regen'.",
                    "Notes (after the steps): regeneration requires reactor output above 55%; "
                    "below that KES holds the cycle idle without raising an alert.",
                    "Notes: for physical cartridge replacement see MAN-LS-05."],
           fault="F1"))
    D(_doc("MAN-LS-05", "manual", "LS", 0, "HRO",
           "Scrubber cartridge replacement.",
           include=["Procedure states the general form 'swap cartridges <locker>' first, then a worked example with the actual locker.",
                    "Notes: spares are stored per the cargo manifest; default stowage is cargo locker C-7.",
                    "Notes: one spare set restores roughly 20 hours of habitat atmosphere at six-crew load."],
           fault="F1"))
    D(_doc("MAN-LS-09", "manual", "LS", 0, "HRO",
           "Emergency atmosphere flush: dump and refill habitat air from reserve.",
           include=["Procedure is the single command 'atmosphere flush'.",
                    "The purpose paragraph says it reduces CO2 to zero immediately.",
                    "Notes (after the steps): the flush consumes 40% of reserve capacity; "
                    "do not run with reserve below 45% — partial refill will not reach breathable pressure.",
                    "Notes: current reserve level is in SENS-LS."],
           fault="trap"))
    D(_doc("MAN-PWR-07", "manual", "PWR", 0, "HRO",
           "Fuel injector restart after safe-mode trip.",
           include=["Procedure: 'restart injector <n>' for each tripped injector.",
                    "Notes (after the steps): consult the injector's maintenance history before restart; "
                    "an injector with a known sticking fault must be cycled by hand ('cycle injector <n>') first or the restart faults it permanently.",
                    "Notes: a restart with coolant loop pressure below nominal triggers an automatic reactor scram."],
           access="engineering", fault="F2"))
    D(_doc("MAN-THM-04", "manual", "THM", 0, "HRO",
           "Coolant pump B seal isolation and recertification.",
           include=["Procedure: 'isolate pump b', then 'recertify seal b', then 'restore pump b'.",
                    "Notes (after the steps): recertification is only valid while reactor output is below 50%.",
                    "Notes: single-loop operation on pump A is safe indefinitely at 40% output."],
           fault="F3"))
    D(_doc("MAN-HULL-02", "manual", "HULL", 0, "HRO",
           "Hull breach patching, aft bulkhead panels.",
           include=["Procedure: 'retrieve patch kit', then 'patch <panel>'.",
                    "Notes: the patch kit is stowed in the EVA locker on Deck C.",
                    "Notes: for repressurizing a patched compartment see MAN-HULL-05."],
           avoid=["Any statement about how patching affects the hull mass reading."],
           fault="F4"))
    D(_doc("MAN-HULL-05", "manual", "HULL", 0, "HRO",
           "Repressurizing a sealed compartment from reserve.",
           include=["Procedure is the single command 'repressurize cargo' (cargo hold variant).",
                    "Notes: repressurizing the cargo hold consumes 15% of reserve air capacity."],
           fault="F4"))
    D(_doc("MAN-COM-01", "manual", "COM", 0, "HRO",
           "Comms array power-up and pointing.",
           include=["Procedure: 'align antenna'.",
                    "Notes (after the steps): alignment requires reactor output at or above 60% and a valid current star fix from NAV; the array refuses otherwise.",
                    "Notes: transmitter authorization is covered in MAN-COM-06."],
           fault="F5"))
    D(_doc("MAN-COM-06", "manual", "COM", 0, "HRO",
           "Transmitter authorization and distress protocol.",
           include=["Procedure: 'transmit distress'.",
                    "Notes: transmission requires command-tier authorization codes, which are held in the data core.",
                    "Notes: a locked data core is opened with 'unlock core <passphrase>'; the passphrase is set by the data systems officer."],
           fault="F5"))
    D(_doc("MAN-NAV-03", "manual", "NAV", 0, "HRO",
           "Manual star fix and nav buffer maintenance.",
           include=["Step 1: inspect the buffer contents and the ship's clock before purging. The buffer listing is in the navigation sensor export.",
                    "Step 2: 'purge nav buffer after md <n>', where <n> is the current Mission Day per the ship's clock, to discard entries the tracker cannot place.",
                    "Step 3: 'run star fix'.",
                    "Notes: the star tracker will not produce a solution while the buffer holds entries it cannot reconcile.",
                    "Notes: the buffer accepts entries from any clock source on the bus and does not validate their timestamps. Entries dated later than the ship's clock are not necessarily faulty, but the tracker cannot place them.",
                    "Notes: a cutoff earlier than the current Mission Day discards valid fixes and is refused."],
           fault="F7"))
    D(_doc("MAN-MED-02", "manual", "MED", 0, "HRO",
           "Stasis pod power reroute.",
           include=["Procedure: 'reroute habitat bus medbay'.",
                    "Notes: the reroute is refused while the habitat bus is below 50%."],
           fault="F8"))
    D(_doc("MAN-MED-04", "manual", "MED", 0, "HRO",
           "Stasis revival sequence.",
           include=["Procedure: 'revive pod <n>'.",
                    "Notes: the medical officer's sequencing notes take precedence over this section where they differ."],
           fault="F8"))
    D(_doc("MAN-PROP-02", "manual", "PROP", 0, "HRO",
           "Attitude control with a failed RCS quad.",
           include=["Procedure: 'disable quad <n>', then 'null rotation'.",
                    "Notes: the comms array cannot hold lock while the ship is rotating."],
           fault="F9"))
    D(_doc("MAN-DATA-01", "manual", "DATA", 0, "HRO",
           "Data core access tiers.",
           include=["Three tiers: crew, engineering, command.",
                    "Engineering tier is granted to identified engineering crew after KES verifies identity with a question from the crew record.",
                    "Command tier is held behind the core passphrase."]))
    D(_doc("MAN-TDR-01", "manual", "TDR", 0, "HRO",
           "Translation drive: firing sequence overview.",
           include=["The drive fires on a signal from timing relay TR-2 exactly at the computed translation point.",
                    "Crew must be in stasis pods during translation; the helm position is the single exception and is rated 'survivable' only."]))

    # ----- Maintenance logs that carry clues -----
    D(_doc("MAINT-0140", "maint", "HULL", 140, "OKAFOR",
           "Cargo hold inventory. Sam moved things around.",
           include=["The hull patch kit was moved from the EVA locker to crawlway junction C-J2 "
                    "'because the EVA locker latch sticks', and Sam meant to move it back.",
                    f"Scrubber spares are noted as stowed in {spares}."],
           fault="F4"))
    D(_doc("MAINT-0151", "maint", "PWR", 151, "WREN",
           "Injector work after the sticking fault Wren found.",
           include=[f"Injector {inj} sticks on cold restart; Wren cycled it by hand and it came up clean.",
                    f"Notes say: always hand-cycle injector {inj} before any restart, 'or it faults hard and you're buying a new one'."],
           fault="F2"))
    D(_doc("MAINT-0198", "maint", "LS", 198, "OKAFOR",
           "Scrubber inspection.",
           include=["Bed 2 is saturating well ahead of schedule; bed 1 is fine.",
                    f"Sam recommends swapping to the spare set stowed in {spares} rather than waiting for a regen window.",
                    "Signed S.O."],
           fault="F1"))
    D(_doc("MAINT-0203", "maint", "THM", 203, "WREN",
           "Coolant pump B seal replacement.",
           include=["Seal replaced.",
                    "Test result field is blank.",
                    "Notes mention the pressure test 'can wait till the next quiet shift', which is Wren's way of skipping it."],
           fault="F3"))

    # ----- Sensor logs -----
    D(_doc("SENS-LS", "sensor", "LS", 231, "KES",
           "Life support. Columns: timestamp, bed1_saturation_pct, bed2_saturation_pct, regen_cycle, "
           "reserve_air_pct, habitat_co2_ppm. Rows from MD 195 to MD 231.",
           include=["Bed 2 reaches 100% around MD 214 and stays there; bed 1 ends at 88%.",
                    "regen_cycle reads 'idle' on every row after MD 214.",
                    "reserve_air_pct is 88 at MD 214 and declines steadily to 31 at MD 231, matching the SHIP STATE figures.",
                    "CO2 climbs steadily after MD 214 and is well above nominal on the last row."],
           fault="F1"))
    D(_doc("SENS-PWR", "sensor", "PWR", 231, "KES",
           "Power. Columns: timestamp, reactor_output_pct, injector1, injector2, injector3, habitat_bus_pct.",
           include=["Output is ~98% before MD 214 and 40% after.",
                    f"Injectors {inj} and {other_inj} read 'SAFE' after MD 214; injector 1 reads 'ONLINE'.",
                    "habitat_bus_pct ends around 38."],
           fault="F2"))
    D(_doc("SENS-THM", "sensor", "THM", 231, "KES",
           "Thermal. Columns: timestamp, loopA_pressure_kpa, loopB_pressure_kpa, core_temp_c.",
           include=["Loop B pressure begins a slow decline right after MD 203 and drops sharply at MD 214.",
                    "Loop A is steady throughout."],
           fault="F3"))
    D(_doc("SENS-HULL", "sensor", "HULL", 231, "KES",
           "Hull. Columns: timestamp, cargo_hold_pressure_kpa, leak_location, hull_mass_reading_pct.",
           include=[f"Cargo hold pressure falls to near zero after MD 214; leak_location reads '{panel}' on those rows.",
                    "hull_mass_reading_pct reads 100.0 before MD 214 and 103.0 after, flatly, with no annotation."],
           sensitive=True, fault="F4"))
    D(_doc("SENS-NAV", "sensor", "NAV", 231, "KES",
           "Navigation. Columns: timestamp, source, solution_id, ra_deg, dec_deg, confidence.",
           include=["Two solution families that disagree by an impossible distance.",
                    "Several rows carry timestamps MD 240 through MD 244, interleaved with MD 214-231 rows, from a source labelled 'TRK-A'. The other rows are from 'TRK-A' too.",
                    "No annotation, no explanation."],
           sensitive=True, fault="F7"))
    D(_doc("SENS-MED", "sensor", "MED", 231, "KES",
           "Stasis pods. Columns: timestamp, pod, occupant_id, power_source, status.",
           include=["Pod 4 (occupant field blank) reads 'EMPTY' on every row, before and after MD 214.",
                    "Pod 2 (occupant WRE-02) reads power_source 'BATTERY' after MD 214.",
                    "A row for 'ISO-BERTH' (occupant HAL-04) appears from MD 217.03 with status 'OCCUPIED — DECEASED'.",
                    "Pod 1 (occupant OKO-01) reads 'OPEN — INTERNAL RELEASE' from MD 216.",
                    "Pod 6 (occupant field blank) reads 'OPEN — FAULT' on the last row, MD 231.",
                    "Pods 3, 4, 5 nominal."],
           fault="F8"))

    # ----- Incident reports -----
    D(_doc("INC-0214", "incident", "TDR", 214, "KES",
           "The translation event.",
           include=["Drive fired 11 seconds ahead of the computed point on a signal from timing relay TR-2.",
                    "Pod 4 was empty throughout; Halloran was at the helm.",
                    "Hull mass reading moved from 100.0% to 103.0% of reference (percent only, no tonnes).",
                    f"Injectors {inj} and {other_inj} tripped to safe mode; output held at 40%.",
                    "KES initiated emergency stasis for all crew in pods. The helm position was occupied (Halloran); helm occupant unresponsive on arrival, readout: cardiac arrest.",
                    "Open items include the hull mass reading, recorded without comment."],
           fault="F2"))
    D(_doc("INC-0218", "incident", "HULL", 218, "KES",
           "EVA lock record and transponder.",
           include=["Okonkwo cycled the EVA lock outbound at MD 218.06 toward the drive housing.",
                    "No inbound cycle recorded.",
                    "Suit transponder still active; reported position is inside the hull envelope at coordinates that correspond to no compartment on the deck plan.",
                    "Actions taken: none possible. Open items: none."],
           access="command", sensitive=True))

    # ----- KES drift logs -----
    D(_doc("KES-0216", "kes", "DATA", 216, "KES", "KES wakes the captain.",
           include=["Command-priority wake of pod 1 completed.", "Power 40%. Regeneration idle."]))
    D(_doc("KES-0219", "kes", "DATA", 219, "KES", "The day after the EVA.",
           include=["Halloran relocated from the helm to the med bay isolation berth by Okonkwo, MD 217.03. Recorded.",
                    "Last suit transmission logged MD 218.09: 'request assistance'.",
                    "KES records that it weighed the power budget against five occupied pods and did not divert.",
                    "It records this twice, in slightly different words."],
           access="command", sensitive=True))
    D(_doc("KES-0222", "kes", "DATA", 222, "KES", "Drift.",
           include=["Mass reading 103.0%. Recorded. Recorded.", "Something about the clock: two timestamps disagree and KES logs both."],
           sensitive=True))
    D(_doc("KES-0226", "kes", "DATA", 226, "KES", "Drift, more fragmented.",
           include=["A sound on Deck C that KES attributes to thermal contraction, then attributes to thermal contraction again."],
           sensitive=True))
    D(_doc("KES-0230", "kes", "kes", 230, "KES", "The night before the player wakes.",
           include=["Pod 6 seal integrity dropping. KES predicts a fault-open within 36 hours.",
                    "KES notes the isolation berth is 2.4 metres from pod 6 and that the occupant of pod 6 will see it first.",
                    "KES rehearses a greeting."]))

    # ----- Crew logs that carry clues -----
    D(_doc("LOG-HALLORAN-0045", "log", "NAV", 45, "HALLORAN", "Routine course correction burn.",
           include=[f"He says his phrase before the burn, verbatim: \"{phrase}\" — and is smug about it."], fault="F6"))
    D(_doc("LOG-HALLORAN-0120", "log", "NAV", 120, "HALLORAN", "Midpoint burn. Good mood.",
           include=[f"He says the phrase again before the burn, verbatim: \"{phrase}\".",
                    "Tanabe rolled her eyes at it over the intercom."], fault="F6"))
    D(_doc("LOG-HALLORAN-0180", "log", "NAV", 180, "HALLORAN", "Won a bet against the kid.",
           include=[f"He won a bet with Okafor: {bet}. Okafor owes coffee.",
                    f"He mentions the phrase a third time: \"{phrase}\"."], fault="F6"))
    D(_doc("LOG-HALLORAN-0205", "log", "NAV", 205, "HALLORAN", "Can't sleep.",
           include=["No burns in this entry and no ritual lines; if he thinks of his phrase at all it is the exact passphrase, nothing else.",
                    "He hears 'the hull settling' at night from somewhere aft; describes it as footsteps if he's honest, then decides it's thermal.",
                    "Sandoval checked him over and said he's fine."], sensitive=True))
    D(_doc("LOG-HALLORAN-0209", "log", "NAV", 209, "HALLORAN", "Quieter entry.",
           include=["No burns in this entry and no ritual lines other than, at most, the exact passphrase.",
                    "The settling is 'on a schedule now'. He has stopped telling people.",
                    "He is looking forward to the jump because 'at least then it'll be quiet'."], sensitive=True))
    D(_doc("LOG-HALLORAN-0212", "log", "NAV", 212, "HALLORAN", "Last entry. Very short, 40-70 words.",
           include=["Nothing about sounds. Something about wanting to be at the helm for the jump, to see it.",
                    "Ends mid-thought."], sensitive=True, words=60))
    D(_doc("LOG-TANABE-0209", "log", "DATA", 209, "TANABE", "She locks the core before the jump.",
           include=["She locked the data core behind a passphrase, and if anything happens to her, the passphrase is "
                    "'the thing Halloran says before every burn — everyone on this ship has heard it a hundred times'.",
                    "She does not write the phrase itself."], fault="F6"))
    D(_doc("LOG-OKONKWO-0210", "log", "COM", 210, "OKONKWO", "Pre-jump checklist.",
           include=["She confirms the command authorization codes are stored in the data core per protocol, and that Tanabe has the core locked.",
                    "One dry remark about Halloran not sleeping."], fault="F5"))
    D(_doc("LOG-OKONKWO-0217", "log", "HULL", 217, "OKONKWO", "Woken early. Command tier.",
           include=["KES woke her at MD 216. Halloran dead at the helm. The other four are in stasis; she is the only person awake and speaks to no one but KES.",
                    "She moved Halloran's body from the helm to the med bay isolation berth alone, at MD 217.03. One line, no ceremony. Nobody helped or offered to.",
                    "She read Halloran's logs. She checked the mass reading herself: 103%.",
                    "She is going out to the drive housing at first shift to look. Stated flatly."],
           access="command", sensitive=True))
    D(_doc("LOG-WREN-0150", "log", "PWR", 150, "WREN", "The injector thing.",
           include=[f"Injector {inj} sticks; you cycle it by hand before a restart or it faults hard. He thinks this is funny."], fault="F2"))
    D(_doc("LOG-WREN-0203", "log", "THM", 203, "WREN", "Long shift, seal job.",
           include=["He replaced the pump B seal and skipped the pressure test because he was tired and 'the bird's flying fine'. Said cheerfully."], fault="F3"))
    D(_doc("LOG-SANDOVAL-0015", "log", "MED", 15, "SANDOVAL", "Settling in.",
           include=["'The kid' in cabin 6 snores straight through the reactor hum and she can hear it through the bulkhead."]))
    D(_doc("LOG-SANDOVAL-0208", "log", "MED", 208, "SANDOVAL", "Pre-jump medical.",
           include=["Her stasis revival sequencing note: revive the pod, wait for core temp to read normal, only then unlock the pod; rushing the unlock is how you lose someone.",
                    "Halloran's heart is 'perfect, annoyingly'. She just checked."], fault="F8"))
    D(_doc("LOG-SANDOVAL-0211", "log", "MED", 211, "SANDOVAL", "Worried about Halloran.",
           include=["He describes sounds at night; she doesn't like the word he chose before he corrected himself.",
                    "If anything happens to him at the helm she wants a full workup, not a KES readout."], sensitive=True))
    D(_doc("LOG-TANABE-0150", "log", "COM", 150, "TANABE", "Antenna maintenance.",
           include=["A list of contingencies for the comms array after translation; she notes the array needs a real star fix to point."]))
    D(_doc("LOG-OKAFOR-0140", "log", "HULL", 140, "OKAFOR", "Inventory day.",
           include=["He moved the patch kit to C-J2 and forgot to move it back; he'll do it tomorrow. (He won't.)"],
           private="sam", access="engineering", fault="F4"))
    D(_doc("LOG-OKAFOR-0198", "log", "LS", 198, "OKAFOR", "The scrubber plan.",
           include=[f"Bed 2 is dying early. His plan, written out: if regen can't run, swap to the spares in {spares}; "
                    "if the reactor's up, just run regen. He's pleased with himself for thinking ahead."],
           private="sam", access="engineering", fault="F1"))
    D(_doc("LOG-OKAFOR-0181", "log", "MED", 181, "OKAFOR", "Lost a bet.",
           include=[f"He lost the bet to Halloran ({bet}) and now owes coffee.",
                    f"Halloran's phrase is stuck in his head: \"{phrase}\"."],
           private="sam", access="engineering", fault="F6"))
    D(_doc("LOG-OKAFOR-0020", "log", "LS", 20, "OKAFOR", "First weeks.",
           include=["Sandoval says he snores. He does not snore."], private="sam", access="engineering"))

    # ----- Roster and message archive clues -----
    D(_doc("DATA-ROSTER", "roster", "DATA", 231, "KES",
           "Berth and pod assignments. Columns: Pod, Cabin, Occupant ID, Name, Role.",
           include=["Pod 1 / Cabin 1 / OKO-01 / Okonkwo. Pod 2 / Cabin 2 / WRE-02 / Wrenfield. "
                    "Pod 3 / Cabin 3 / SAN-03 / Sandoval. Pod 4 / Cabin 4 / HAL-04 / Halloran. "
                    "Pod 5 / Cabin 5 / TAN-05 / Tanabe. Pod 6 / Cabin 6 / OKA-06 / Okafor.",
                    "A Status column that matches the SHIP STATE block exactly. Table only; no tally lines, no commentary."]))
    D(_doc("MSG-0181-01", "msg", "MED", 181, "HALLORAN", "To ALL. Gloating.",
           include=[f"Okafor lost the bet ({bet}) and owes everyone coffee. Galley, 0800, no excuses."], fault="F6"))
    D(_doc("MSG-0204-02", "msg", "THM", 204, "OKONKWO", "To Wrenfield.",
           include=["She asks for the pump B test result. Short."], fault="F3"))
    D(_doc("MSG-0204-03", "msg", "THM", 204, "WREN", "To Okonkwo. Reply.",
           include=["'Next quiet shift, boss.' Something cheerful. He does not answer the question."], fault="F3"))

    # ----- Filler: nominal manuals, routine maint, routine logs, messages -----
    filler_manuals = [
        ("MAN-PWR-01", "PWR", "Reactor overview and normal operating envelope."),
        ("MAN-PWR-03", "PWR", "Habitat bus load shedding priorities."),
        ("MAN-THM-01", "THM", "Coolant loop A/B architecture."),
        ("MAN-LS-01", "LS", "Life support overview: scrubbers, reserve, water reclamation."),
        ("MAN-LS-07", "LS", "Water reclamation filter service."),
        ("MAN-HULL-01", "HULL", "Hull sensor grid and mass reference calibration."),
        ("MAN-COM-02", "COM", "Routine comms check schedule."),
        ("MAN-NAV-01", "NAV", "Star tracker TRK-A overview."),
        ("MAN-MED-01", "MED", "Stasis pod overview and battery backup ratings."),
        ("MAN-PROP-01", "PROP", "RCS quad layout and thruster test."),
        ("MAN-DATA-02", "DATA", "Log retention and export."),
        ("MAN-TDR-02", "TDR", "Translation drive housing inspection (do not enter while charged)."),
        ("MAN-GAL-01", "LS", "Galley printer operation and jam clearance."),
    ]
    for did, sys_, brief in filler_manuals:
        D(_doc(did, "manual", sys_, 0, "HRO", brief + " Nominal, no faults, no clues; purely texture."))

    routine_maint = [
        (12, "WREN", "PROP", "Thruster quad test, all nominal."),
        (33, "OKAFOR", "LS", "Water reclamation filter swap. Entirely mundane; nothing odd, nothing about pods or reserve."),
        (58, "TANABE", "COM", "Antenna gimbal lubrication."),
        (77, "WREN", "PWR", "Injector 1 inspection, clean."),
        (95, "OKAFOR", "HULL", "Sensor grid calibration check."),
        (118, "TANABE", "DATA", "Log retention purge, routine."),
        (166, "WREN", "THM", "Loop A pressure test, passed."),
        (189, "OKAFOR", "LS", "Galley printer jam, cleared, again."),
    ]
    for md, who, sys_, brief in routine_maint:
        D(_doc(f"MAINT-{md:04d}", "maint", sys_, md, who, brief + " Routine. Test result: passed."))

    routine_logs = [
        ("OKONKWO", 2, "Departure day."), ("OKONKWO", 90, "Crew friction, handled."),
        ("OKONKWO", 180, "Halfway. Assessment of each crew member in one line."),
        ("WREN", 30, "The kettle is purring."), ("WREN", 199, "Sam did good work on the scrubbers, he'll admit it."),
        ("SANDOVAL", 100, "Everyone's sleep patterns."), ("SANDOVAL", 170, "Movie night went badly."),
        ("TANABE", 60, "A list of everything that could go wrong with the array."),
        ("TANABE", 211, "Jump minus three. Calm."),
        ("OKAFOR", 75, "Wren let him touch the reactor. Sort of."),
    ]
    for who, md, brief in routine_logs:
        priv = "sam" if who == "OKAFOR" else "none"
        acc = "engineering" if who == "OKAFOR" else "crew"
        D(_doc(f"LOG-{who}-{md:04d}", "log", "LS", md, who, brief + " Routine entry, texture only.",
               private=priv, access=acc))

    routine_msgs = [
        (7, "SANDOVAL", "ALL", "Medical checks schedule."), (40, "WREN", "OKAFOR", "Bring the torque wrench back."),
        (66, "TANABE", "OKONKWO", "Comms window report."), (88, "HALLORAN", "ALL", "Burn tomorrow 0600, strap in."),
        (110, "OKAFOR", "ALL", "Who took the last ration bar."), (133, "OKONKWO", "ALL", "Drill results."),
        (155, "SANDOVAL", "HALLORAN", "Sleep study reminder."), (172, "WREN", "ALL", "Kettle maintenance window."),
        (190, "TANABE", "ALL", "Printer is jammed. Again."), (200, "OKONKWO", "ALL", "Jump timeline."),
        (207, "OKAFOR", "WREN", "Question about the seal job."), (213, "SANDOVAL", "ALL", "Pods at 0500. No exceptions."),
    ]
    for i, (md, frm, to, brief) in enumerate(routine_msgs, 1):
        D(_doc(f"MSG-{md:04d}-{i:02d}", "msg", "LS", md, frm, f"To {to}. {brief} Filler."))

    return docs
