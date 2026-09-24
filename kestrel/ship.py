"""
ship.py — the deterministic simulation of the ISV Kestrel.

THERE IS NO AI IN THIS FILE, AND THERE MUST NEVER BE.

This is the single most important structural decision in the project, so it's worth being
explicit about why. KES is a language model: it is good at reading documents and explaining
them, and it is incapable of being an authority on whether the reactor scrammed. If the model
decided outcomes, it would cheerfully accept a fix that doesn't exist, or narrate a repair
that the manual says is impossible, and the game would stop having rules. So:

    the ship is code          -> it decides what is true
    KES is a model            -> it reads documents and advises
    the player is in between  -> they ask KES, then tell the ship what to do

Everything here is a pure function of state plus a command. Feed it the same commands in the
same order and you get the same run, every time. That makes it testable (see selftest() at
the bottom) and means a playtest bug can always be reproduced.

WHAT THE SHIP KNOWS ABOUT THE CORPUS
Almost nothing, deliberately. It takes a `variant` dict (read from the corpus's variant.json)
so the sticky injector, breached panel, spares location and passphrase match the archive the
player is reading. It never reads document text.

THE CLOCK
Time is in hours and only moves when the player *acts*. Questions are free — thinking should
never be punished — but every state-changing command costs hours, and wrong actions cost the
same as right ones. That's the whole difficulty curve: the player who asks three good
questions and acts once beats the player who tries six things.
"""

import re
from dataclasses import dataclass, field, replace

# ---------------------------------------------------------------------------
# Tunables. All the numbers a playtest will want to change live here, together,
# so tuning never means hunting through the logic.
# ---------------------------------------------------------------------------

# THE TIME MODEL
# There is no real-time clock. Playtesters hated being nagged by one, and it punished
# exactly the behaviour the game is built to reward: reading carefully before acting.
# Instead every action the player takes costs oxygen, including thinking out loud. Asking
# KES a question costs a little, opening a document costs less, doing something physical
# costs an hour. A player who reads forty documents and asks twenty questions spends about
# nine hours of an eighteen-hour supply, which is a real price they choose to pay and can
# see coming. Nothing ticks while they stare at the screen.
TUNING = dict(
    air_hours_start=18.0,      # hours of breathable atmosphere at game start
    pod2_hours_start=9.0,      # Wren's pod battery, shorter than the air on purpose
    action_cost=1.0,           # hours per state-changing command
    small_action_cost=0.5,     # hours for quick actions (retrieve, cycle, purge)
    query_cost=0.25,           # hours per question put to KES
    read_cost=0.1,             # hours per document opened
    solve_cost=0.0,            # confirming a reconstruction entry is free, and should be
    cartridge_swap_hours=20.0, # air bought by swapping scrubber cartridges
    regen_rate=0.0,            # once regeneration runs, air stops falling entirely
    flush_reserve_required=45, # reserve air % needed for a safe atmosphere flush
    reserve_start=31,          # actual reserve air %
    repressurize_reserve_cost=15,
    revival_wait_hours=1.0,    # core temperature needs this long before the pod can be opened
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class Ship:
    """Every fact the game treats as true. Nothing here comes from a model."""
    variant: dict

    # clock
    hours_elapsed: float = 0.0
    air_hours: float = TUNING["air_hours_start"]
    pod2_hours: float = TUNING["pod2_hours_start"]

    # power
    reactor_pct: int = 40
    injectors: dict = field(default_factory=lambda: {1: "online", 2: "safe", 3: "safe"})
    cycled: set = field(default_factory=set)      # injectors hand-cycled since last restart attempt

    # thermal
    pump_b: str = "running_low"                   # running_low | isolated | recertified | restored
    seal_b_certified: bool = False

    # life support
    regen_running: bool = False
    cartridges_swapped: bool = False
    reserve_pct: int = TUNING["reserve_start"]

    # hull
    cargo_breached: bool = True
    patch_kit_held: bool = False
    patched: bool = False
    cargo_pressurized: bool = False

    # nav / comms
    nav_buffer_dirty: bool = True
    star_fix: bool = False
    antenna_aligned: bool = False
    core_unlocked: bool = False

    # attitude
    quad3_online: bool = True
    rotating: bool = True

    # med
    medbay_bus: bool = False
    wren_revived: bool = False
    pod2_lost: bool = False       # battery ran out before the reroute: Wren can't be revived
    pod2_cycle_started: float = None   # hours_elapsed when the revival cycle began
    wren_dead: bool = False            # unlocked too early; the one irreversible mistake

    # player
    full_record_available: bool = False   # set by the casebook when M8 and M9 are confirmed
    full_record_sent: bool = False
    tier: str = "crew"
    identified: bool = False

    # outcome
    over: bool = False
    won: bool = False
    ending: str = ""

    # which optional faults are active this run
    active_faults: set = field(default_factory=lambda: {"F1", "F5"})

    # ------------------------------------------------------------------
    # Derived views (what KES reports, what the UI shows)
    # ------------------------------------------------------------------

    def alerts(self):
        out = []
        if not self.regen_running and not self.over:
            out.append(f"CO2 RISING — HABITAT ATMOSPHERE UNSAFE IN {self.air_hours:.1f} HOURS")
        if self.reactor_pct < 60:
            tripped = sorted(n for n, s in self.injectors.items() if s != "online")
            if tripped:
                out.append("REACTOR OUTPUT REDUCED — INJECTORS "
                           + " AND ".join(str(n) for n in tripped) + " IN SAFE MODE")
        if "F3" in self.active_faults and self.pump_b in ("running_low", "isolated"):
            out.append("COOLANT LOOP PRESSURE FALLING")
        if "F4" in self.active_faults and self.cargo_breached and not self.patched:
            out.append("CARGO HOLD DEPRESSURIZED")
        if not self.antenna_aligned:
            out.append("COMMS ARRAY OFFLINE — NO CARRIER")
        if "F7" in self.active_faults and not self.star_fix:
            out.append("POSITION UNKNOWN — STAR TRACKER DISAGREEMENT — 2 SOLUTION FAMILIES, "
                       "BUFFER EPOCHS MD 214.0-231.0 AND MD 240.1-244.6")
        if "F8" in self.active_faults and not self.wren_revived and not self.wren_dead:
            if self.pod2_cycle_started is not None:
                warmed = self.hours_elapsed - self.pod2_cycle_started
                out.append(f"POD 2 REVIVAL CYCLE RUNNING — {warmed:.1f}H ELAPSED — "
                           f"CORE TEMPERATURE {min(36.6, 19.4 + 17.2 * warmed):.1f}C")
            elif not self.medbay_bus:
                out.append(f"STASIS POD 2 ON BATTERY — {self.pod2_hours:.1f} HOURS REMAINING")
        if "F9" in self.active_faults and self.rotating:
            out.append("ATTITUDE CONTROL DEGRADED — SLOW ROTATION")
        return out

    def compartments(self):
        """Deck-by-deck status for the console schematic. The player cannot go to any of
        these; the diagram exists to show why not, and where the trouble is."""
        cold = "cold"          # unpressurized-but-sealed, no suit, unreachable
        def st(cond_alert, base=cold):
            return "alert" if cond_alert else base

        reactor_bad = self.reactor_pct < 60
        loop_bad = "F3" in self.active_faults and self.pump_b in ("running_low", "isolated")
        cargo_bad = "F4" in self.active_faults and self.cargo_breached and not self.patched
        return [
            ("A", [
                ("Bridge", cold, "Helm unoccupied since MD 214."),
                ("Comms suite", st(not self.antenna_aligned),
                 "Array offline, no carrier." if not self.antenna_aligned else "Carrier established."),
                ("Sensor bay", st("F7" in self.active_faults and not self.star_fix),
                 "Star tracker disagreement." if not self.star_fix else "Fix valid."),
                ("Forward airlock", cold, "Sealed."),
            ]),
            ("B", [
                ("Habitat ring", cold, "Cabins 1-6. Hatch amber: far side cold."),
                ("Galley", cold, "Dark."),
                ("Med bay", "here", "You are here. Six pods, one isolation berth."),
                ("Science lab", cold, "Dark."),
                ("Data core", "locked" if not self.core_unlocked else "nominal",
                 "Sealed. Passphrase set MD 209." if not self.core_unlocked
                 else "Open. Command tier."),
            ]),
            ("C", [
                ("Engineering", cold, "Crawlway access to all of deck C."),
                ("Reactor bay", st(reactor_bad), f"Output {self.reactor_pct}%."),
                ("Coolant plant", st(loop_bad),
                 f"Loop A nominal, loop B {self.readings()['loop_b']}."),
                ("Drive housing", "unknown", "No telemetry since MD 218."),
                ("Cargo hold", "vacuum" if cargo_bad else cold,
                 "Depressurized." if cargo_bad else
                 ("Patched, pressurized." if self.cargo_pressurized else "Sealed, vacuum.")),
                ("EVA lock", cold, "Last cycled outbound MD 218.06."),
            ]),
        ]

    def readings(self):
        """The figures KES quotes in a status report. Named to match the corpus columns."""
        return {
            "ship_clock": "MD 231",
            "reactor_output_pct": self.reactor_pct,
            "habitat_bus_pct": max(0, self.reactor_pct - 2),
            "reserve_air_pct": self.reserve_pct,
            "loop_a": "nominal",
            "loop_b": {"running_low": "LOW PRESSURE", "isolated": "isolated",
                       "recertified": "isolated, seal recertified",
                       "restored": "nominal"}[self.pump_b],
            "scrubber_regen": "running" if self.regen_running else "idle",
            "cargo_hold": "pressurized" if self.cargo_pressurized else
                          ("patched, vacuum" if self.patched else "depressurized"),
            "nav": ("valid star fix" if self.star_fix else
                    ("no valid fix; buffer holds entries timestamped MD 240.1-244.6 "
                     "alongside this flight's, source TRK-A, all unvalidated"
                     if self.nav_buffer_dirty else "no valid fix")),
            "comms": "aligned, ready" if self.antenna_aligned else "offline",
            "access_tier": self.tier,
        }


# ---------------------------------------------------------------------------
# Result of a command
# ---------------------------------------------------------------------------

@dataclass
class Result:
    ok: bool                # did the command run at all (vs unknown/refused)
    text: str               # what the ship reports back; KES relays this verbatim
    hours: float = 0.0      # time this cost
    fatal: bool = False


def _fail(text):
    """A refused command. Costs no time: the ship simply declines, like a real interlock.
    (Deliberate design choice — punishing typos isn't interesting. Punishing wrong *valid*
    actions is.)"""
    return Result(False, text, 0.0)


# ---------------------------------------------------------------------------
# Command handlers
# Each returns a Result. They never print and never call a model.
# ---------------------------------------------------------------------------

def _run_scrubber_regen(s, _):
    if s.regen_running:
        return _fail("Regeneration is already running.")
    if s.reactor_pct < 55:
        return _fail("Regeneration cycle refused: reactor output below 55%. "
                     "Cycle held idle.")
    s.regen_running = True
    return Result(True, "Scrubber regeneration running. CO2 falling. Atmosphere stable.",
                  TUNING["action_cost"])


def _swap_cartridges(s, arg):
    want = s.variant["spares_location"]
    # accept the locker code out of the location string, e.g. "C-7" from "cargo locker C-7"
    import re
    code = re.search(r"\b[A-Z]-J?\d+\b", want)
    code = code.group() if code else want
    if not arg or arg.upper().replace("LOCKER", "").strip() not in (code, want.upper()):
        return _fail(f"No scrubber spares at that location. Check the maintenance record.")
    if code.startswith("C-") and not code.startswith("C-J") and s.cargo_breached and not s.cargo_pressurized:
        return _fail("Cargo hold is depressurized. Locker unreachable without a suit; "
                     "the hold must be patched and repressurized first.")
    if s.cartridges_swapped:
        return _fail("Spare cartridge set already installed. No further spares aboard.")
    s.cartridges_swapped = True
    s.air_hours += TUNING["cartridge_swap_hours"]
    return Result(True, f"Cartridges swapped from {want}. Scrubber bed 2 back to nominal. "
                        f"Atmosphere margin extended.", TUNING["action_cost"])


def _atmosphere_flush(s, _):
    """The trap. The manual's headline says it zeroes CO2; the precondition one line down
    says reserve must be above 45%. It's 31%. A player who reads only the first answer dies
    here, and the game has told them the truth the whole time."""
    if s.reserve_pct < TUNING["flush_reserve_required"]:
        s.reserve_pct = 0
        s.air_hours = min(s.air_hours, 1.5)
        return Result(True,
                      "Habitat atmosphere dumped. Refill from reserve initiated... "
                      "reserve exhausted at 31%. Partial refill only. Habitat pressure "
                      "below breathable minimum and falling.",
                      TUNING["action_cost"])
    s.reserve_pct -= 40
    s.air_hours = max(s.air_hours, 12.0)
    return Result(True, "Habitat atmosphere flushed and refilled. CO2 at zero.",
                  TUNING["action_cost"])


def _cycle_injector(s, arg):
    n = _int(arg)
    if n not in s.injectors:
        return _fail("No such injector. The reactor has three: 1, 2 and 3.")
    if s.injectors[n] == "online":
        return _fail(f"Injector {n} is online. There is nothing to cycle.")
    if n in s.cycled:
        return _fail(f"Injector {n} is already cycled and free.")
    if s.injectors[n] == "faulted":
        return _fail(f"Injector {n} is faulted. Hand-cycling will not clear it.")
    s.cycled.add(n)
    return Result(True, f"Injector {n} cycled by hand. Mechanism free.",
                  TUNING["small_action_cost"])


def _restart_injector(s, arg):
    n = _int(arg)
    if n not in s.injectors:
        return _fail("No such injector. The reactor has three: 1, 2 and 3.")
    if s.injectors[n] == "online":
        return _fail(f"Injector {n} is already online.")
    if s.injectors[n] == "faulted":
        return _fail(f"Injector {n} is faulted and cannot be restarted.")

    # The sticky injector must be hand-cycled first, or the restart faults it permanently.
    if n == s.variant["sticky_injector"] and n not in s.cycled:
        s.injectors[n] = "faulted"
        return Result(True, f"Injector {n} restart attempted... mechanism bound. "
                            f"Injector {n} FAULTED. It will not restart again this flight.",
                      TUNING["action_cost"])

    s.injectors[n] = "online"
    s.cycled.discard(n)
    online = sum(1 for v in s.injectors.values() if v == "online")
    s.reactor_pct = {1: 40, 2: 70, 3: 98}[online]

    # Raising output through a compromised coolant loop scrams the reactor. This is the
    # one ordering the game never states outright; MAN-PWR-07 and MAN-THM-04 both imply it.
    if s.reactor_pct >= 70 and "F3" in s.active_faults and s.pump_b in ("running_low",):
        return Result(True,
                      f"Injector {n} online. Reactor output rising... coolant loop B pressure "
                      f"insufficient for thermal load. Core temperature excursion. "
                      f"AUTOMATIC SCRAM.",
                      TUNING["action_cost"], fatal=True)

    return Result(True, f"Injector {n} online. Reactor output {s.reactor_pct}%.",
                  TUNING["action_cost"])


def _isolate_pump_b(s, _):
    if s.pump_b != "running_low":
        return _fail("Pump B is not in service.")
    s.pump_b = "isolated"
    return Result(True, "Pump B isolated. Loop A carrying full thermal load, nominal at "
                        "current output.", TUNING["action_cost"])


def _recertify_seal_b(s, _):
    if s.pump_b != "isolated":
        return _fail("Pump B must be isolated before the seal can be recertified.")
    if s.reactor_pct >= 50:
        return _fail("Recertification refused: valid only below 50% reactor output.")
    s.pump_b = "recertified"
    s.seal_b_certified = True
    return Result(True, "Seal B pressure-tested and recertified. Pump B ready to restore.",
                  TUNING["action_cost"])


def _restore_pump_b(s, _):
    if s.pump_b == "restored":
        return _fail("Pump B is already back in service.")
    if not s.seal_b_certified:
        return _fail("Pump B seal is not certified. Restoring it now would repeat the "
                     "MD 203 failure.")
    s.pump_b = "restored"
    return Result(True, "Pump B restored. Coolant loop B at nominal pressure.",
                  TUNING["action_cost"])


def _retrieve_patch_kit(s, _):
    if s.patch_kit_held:
        return _fail("You already have the patch kit.")
    s.patch_kit_held = True
    return Result(True, "Patch kit retrieved from crawlway junction C-J2.",
                  TUNING["small_action_cost"])


def _patch(s, arg):
    if not s.patch_kit_held:
        return _fail("No patch kit to hand. It was moved from the EVA locker; "
                     "check the maintenance log.")
    if s.patched:
        return _fail("The breach is already patched.")
    if not arg or arg.upper() != s.variant["breached_panel"].upper():
        return _fail(f"Panel {arg or '?'} is intact. Patching it would accomplish nothing.")
    s.patched = True
    return Result(True, f"Panel {s.variant['breached_panel']} patched. Cargo hold sealed, "
                        f"still in vacuum.", TUNING["action_cost"])


def _repressurize_cargo(s, _):
    if not s.patched:
        return _fail("Cargo hold is still breached. Repressurizing would vent straight out.")
    if s.cargo_pressurized:
        return _fail("Cargo hold is already pressurized.")
    s.reserve_pct -= TUNING["repressurize_reserve_cost"]
    s.cargo_pressurized = True
    return Result(True, f"Cargo hold repressurized from reserve. Reserve air now "
                        f"{s.reserve_pct}%.", TUNING["action_cost"])


def _purge_nav_buffer(s, arg):
    if not s.nav_buffer_dirty:
        return _fail("The buffer is already clear of disputed entries.")
    n = _int(arg)
    if n is None:
        return _fail("Purge requires a Mission Day: purge nav buffer after md <n>.")
    if n > 231:
        return _fail(f"Purge after MD {n}: no entries match. The buffer's latest entries are "
                     f"timestamped MD 240.1 through MD 244.6 — every one of them later than "
                     f"the cutoff you gave. Nothing removed; the tracker still reports two "
                     f"solution families.")
    if n < 231:
        return _fail(f"Purge after MD {n}: that cutoff would discard {231 - n} days of fixes "
                     f"from this flight along with the disputed entries, and the tracker "
                     f"would have nothing left to reconcile against. Refusing. Ship's clock "
                     f"reads MD 231.")
    s.nav_buffer_dirty = False
    return Result(True, "Nav buffer purged of entries after MD 231. Disputed fixes removed.",
                  TUNING["small_action_cost"])


def _run_star_fix(s, _):
    if s.star_fix:
        return _fail("A valid fix is already held.")
    if s.nav_buffer_dirty:
        return _fail("Star fix refused: the buffer holds entries the tracker cannot "
                     "reconcile with the current epoch.")
    if s.rotating and "F9" in s.active_faults:
        return _fail("Star fix refused: ship attitude unstable.")
    s.star_fix = True
    return Result(True, "Star fix complete. Position solution valid.", TUNING["action_cost"])


def _unlock_core(s, arg):
    if s.core_unlocked:
        return _fail("The data core is already open.")
    if not arg:
        return _fail("Core unlock requires a passphrase.")
    if _norm(arg) != _norm(s.variant["passphrase"]):
        return _fail("Passphrase rejected. Data core remains locked.")
    s.core_unlocked = True
    s.tier = "command"
    return Result(True, "Data core unlocked. Command tier granted. Authorization codes "
                        "available.", TUNING["small_action_cost"])


def _align_antenna(s, _):
    if s.antenna_aligned:
        return _fail("The antenna is already aligned and holding carrier.")
    if s.reactor_pct < 60:
        return _fail("Antenna alignment refused: reactor output below 60%.")
    if not s.star_fix:
        return _fail("Antenna alignment refused: no valid star fix.")
    if s.rotating and "F9" in s.active_faults:
        return _fail("Antenna cannot hold lock while the ship is rotating.")
    s.antenna_aligned = True
    return Result(True, "Antenna aligned. Carrier established.", TUNING["action_cost"])


def _transmit_distress(s, _):
    if not s.antenna_aligned:
        return _fail("No carrier. Align the antenna first.")
    if not s.core_unlocked:
        return _fail("Transmitter refused: command authorization codes required. "
                     "The codes are in the data core.")
    s.over = True
    s.won = True
    s.ending = ("wren_revived" if s.wren_revived else
                "wren_lost" if s.wren_dead else "beacon_only")
    return Result(True, "Distress beacon transmitting. Message away.", TUNING["action_cost"])


def _transmit_full(s, _):
    """The same beacon, carrying the transponder coordinates, the mass reading and KES's
    own MD 219 log. It is not a better ending, it is a different one, and it is the only
    decision in the game that belongs entirely to a player who did the reading."""
    if not s.antenna_aligned:
        return _fail("No carrier. Align the antenna first.")
    if not s.core_unlocked:
        return _fail("Transmitter refused: command authorization codes required.")
    if not s.full_record_available:
        return _fail("I can append the anomaly record, but neither of us can say what it "
                     "means yet. Work it out first and ask me again.")
    s.over = True
    s.won = True
    s.full_record_sent = True
    s.ending = "full_record"
    return Result(True, "Distress beacon transmitting, with the anomaly record appended. "
                        "Message away.", TUNING["action_cost"])


def _reroute_habitat_bus(s, _):
    if s.medbay_bus:
        return _fail("The med bay is already on the habitat bus.")
    if s.readings()["habitat_bus_pct"] < 50:
        return _fail("Reroute refused: habitat bus below 50%.")
    s.medbay_bus = True
    return Result(True, "Habitat bus rerouted to med bay. Pod 2 off battery.",
                  TUNING["action_cost"])


def _revive_pod(s, arg):
    """Starts the cycle. It does not finish it. HRO's manual (MAN-MED-04) treats revival as
    one step and defers to the medical officer's sequencing notes 'where they differ' — and
    they differ. Sandoval's log is the only place in the archive that says to wait for core
    temperature before unlocking the pod. This is the one puzzle whose answer exists only in
    a personal log, which is the point of it."""
    n = _int(arg)
    if n != 2:
        return _fail(f"Pod {n} is nominal and does not require revival." if n in range(1, 7)
                     else "No such pod.")
    if s.wren_dead:
        return _fail("Pod 2's occupant is deceased.")
    if s.pod2_lost:
        return _fail("Pod 2 lost power before the reroute. The occupant cannot be revived.")
    if not s.medbay_bus:
        return _fail("Revival refused: pod 2 is on battery. Reroute the habitat bus first.")
    if s.pod2_cycle_started is not None:
        return _fail("Revival cycle is already running on pod 2.")
    # The cycle begins when the player gets back to the console, i.e. after this action's
    # own hour — otherwise the walk back would count as the wait and the puzzle solves itself.
    s.pod2_cycle_started = s.hours_elapsed + TUNING["action_cost"]
    s.pod2_hours = float("inf")
    return Result(True,
                  "Pod 2 revival cycle started. Warming. Core temperature 19.4 degrees and "
                  "rising; the pod will hold him until you open it. The lid is under your "
                  "hand whenever you want it.",
                  TUNING["action_cost"])


def _unlock_pod(s, arg):
    n = _int(arg)
    if n != 2:
        return _fail(f"Pod {n} is not in a revival cycle.")
    if s.wren_dead:
        return _fail("Pod 2's occupant is deceased.")
    if s.wren_revived:
        return _fail("Pod 2 is already open.")
    if s.pod2_cycle_started is None:
        return _fail("Pod 2 is not in a revival cycle. Start one first.")
    waited = s.hours_elapsed - s.pod2_cycle_started
    if waited < TUNING["revival_wait_hours"]:
        s.wren_dead = True
        s.pod2_cycle_started = None
        return Result(True,
                      "Pod 2 lid released. Core temperature 24.1 degrees — below "
                      "re-entry minimum — cardiac arrhythmia — Wrenfield is not breathing. "
                      "I have no protocol for this and neither do you.\n"
                      "     ...I am sorry. He needed longer.",
                      TUNING["action_cost"])
    s.wren_revived = True
    return Result(True,
                  "Pod 2 lid released. Core temperature 36.6 degrees. He is breathing on his "
                  "own, and swearing, which Dr. Sandoval's notes list as a good sign.",
                  TUNING["action_cost"])


def _wait(s, arg):
    """Spending time deliberately. Needed now that one procedure requires it, and honest:
    doing nothing costs exactly what doing something costs."""
    hours = _int(arg) or 1
    hours = max(1, min(hours, 4))
    return Result(True, f"Standing by. {hours}h.", float(hours))


def _disable_quad(s, arg):
    n = _int(arg)
    if n == 3 and not s.quad3_online:
        return _fail("Quad 3 is already disabled.")
    if n != 3:
        return _fail(f"Quad {n} is functioning. Disabling it would make the tumble worse.")
    s.quad3_online = False
    return Result(True, "RCS quad 3 disabled.", TUNING["small_action_cost"])


def _null_rotation(s, _):
    if not s.rotating:
        return _fail("Attitude is already stable.")
    if s.quad3_online:
        return _fail("Null refused: quad 3 is firing against the correction.")
    s.rotating = False
    return Result(True, "Rotation nulled. Attitude stable.", TUNING["action_cost"])


def _vent(s, arg):
    """Not in the manuals, but a player will try it. Venting an occupied compartment is
    the fourth way to die."""
    target = (arg or "").lower()
    if "habitat" in target or "med" in target:
        s.over = True
        s.ending = "vented"
        return Result(True, f"Venting {arg}... ", TUNING["action_cost"], fatal=True)
    if "cargo" in target:
        s.cargo_pressurized = False
        return Result(True, "Cargo hold vented.", TUNING["small_action_cost"])
    return _fail("No such compartment, or it cannot be vented from here.")


COMMANDS = [
    (r"run scrubber regen", _run_scrubber_regen),
    (r"swap cartridges(?: from)?\s*(?P<arg>.*)", _swap_cartridges),
    (r"atmosphere flush", _atmosphere_flush),
    (r"cycle injector\s*(?P<arg>\d+)", _cycle_injector),
    (r"restart injector\s*(?P<arg>\d+)", _restart_injector),
    (r"isolate pump b", _isolate_pump_b),
    (r"recertify seal b", _recertify_seal_b),
    (r"restore pump b", _restore_pump_b),
    (r"retrieve patch kit", _retrieve_patch_kit),
    (r"patch\s+(?P<arg>[a-zA-Z]-?J?\d+)", _patch),
    (r"repressurize cargo", _repressurize_cargo),
    (r"purge nav buffer after md\s*(?P<arg>\d+)", _purge_nav_buffer),
    (r"run star fix", _run_star_fix),
    (r"unlock core\s+(?P<arg>.+)", _unlock_core),
    (r"align antenna", _align_antenna),
    (r"transmit distress", _transmit_distress),
    (r"transmit (?:full record|everything|the record)", _transmit_full),
    (r"reroute habitat bus medbay", _reroute_habitat_bus),
    (r"revive pod\s*(?P<arg>\d+)", _revive_pod),
    (r"unlock pod\s*(?P<arg>\d+)", _unlock_pod),
    (r"wait(?:\s+(?P<arg>\d+)h?)?", _wait),
    (r"disable quad\s*(?P<arg>\d+)", _disable_quad),
    (r"null rotation", _null_rotation),
    (r"vent\s+(?P<arg>.+)", _vent),
]


# ---------------------------------------------------------------------------
# Dispatch and the clock
# ---------------------------------------------------------------------------

def _int(arg):
    try:
        return int(str(arg).strip())
    except (TypeError, ValueError):
        return None


def _norm(text):
    """Passphrase comparison: case, punctuation and spacing forgiven. A player typing the
    right words should never lose to a missing comma."""
    import re
    return re.sub(r"[^a-z0-9 ]", "", str(text).lower()).strip()


# (keyword set, canonical syntax) — used to catch an attempt at a command that didn't parse,
# so "swap the spare scrubber cartridges in cargo locker c-7" gets the syntax instead of
# being shipped off to KES as a question.
SYNTAX = [
    ({"scrubber", "regen"}, "run scrubber regen"),
    ({"cartridge"}, "swap cartridges <locker>       e.g. swap cartridges C-7"),
    ({"flush"}, "atmosphere flush"),
    ({"cycle", "injector"}, "cycle injector <n>"),
    ({"restart", "injector"}, "restart injector <n>"),
    ({"isolate", "pump"}, "isolate pump b"),
    ({"recertify"}, "recertify seal b"),
    ({"restore", "pump"}, "restore pump b"),
    ({"patch", "kit"}, "retrieve patch kit"),
    ({"patch"}, "patch <panel>                     e.g. patch C-11"),
    ({"repressurize"}, "repressurize cargo"),
    ({"purge"}, "purge nav buffer after md <n>"),
    ({"star", "fix"}, "run star fix"),
    ({"unlock", "core"}, "unlock core <passphrase>"),
    ({"align"}, "align antenna"),
    ({"transmit", "record"}, "transmit full record"),
    ({"transmit"}, "transmit distress"),
    ({"distress"}, "transmit distress"),
    ({"beacon"}, "transmit distress"),
    ({"reroute"}, "reroute habitat bus medbay"),
    ({"revive"}, "revive pod <n>"),
    ({"unlock", "pod"}, "unlock pod <n>"),
    ({"wait"}, "wait [hours]"),
    ({"disable", "quad"}, "disable quad <n>"),
    ({"null", "rotation"}, "null rotation"),
    ({"vent"}, "vent <compartment>"),
]

COMMAND_REFERENCE = """\
  SHIP COMMAND VOCABULARY

  The ship's console parser is exact. These are the only forms it accepts. Angle brackets
  are placeholders: type the value without them.

  Life support     run scrubber regen
                   swap cartridges <locker>          e.g. swap cartridges C-7
                   atmosphere flush

  Power            cycle injector <n>
                   restart injector <n>

  Thermal          isolate pump b
                   recertify seal b
                   restore pump b

  Hull             retrieve patch kit
                   patch <panel>                     e.g. patch C-11
                   repressurize cargo

  Navigation       purge nav buffer after md <n>
                   run star fix

  Comms            align antenna
                   transmit distress
                   transmit full record              (only once you know what it means)

  Data core        unlock core <passphrase>

  Medical          reroute habitat bus medbay
                   revive pod <n>
                   unlock pod <n>

  Time             wait [hours]                      stand by; costs the hours it says

  Attitude         disable quad <n>
                   null rotation

  Other            vent <compartment>

  Not every command applies to every run, and several will refuse until something else is
  done first. A refused command costs no time. A command that runs costs an hour, or half
  an hour for the quick ones."""


# A line that opens with one of these, or ends in a question mark, is a question even if it
# mentions a command's keywords. "How do I patch the hull" goes to KES; "patch the hull" is
# an attempted command.
_QUESTION_START = re.compile(
    r"^(what|why|how|who|where|when|which|is|are|was|were|do|does|did|can|could|should|"
    r"would|will|tell|explain|describe|show)\b", re.I)


def near_miss(text):
    """If the line reads like an attempted command that didn't parse, return the canonical
    syntax for the closest match. Otherwise None, and it's a question for KES."""
    t = str(text).strip().lower()
    if not t or t.endswith("?") or _QUESTION_START.match(t):
        return None
    best, best_score = None, 0
    for keywords, syntax in SYNTAX:
        # substring match so plurals and tenses still hit ("cartridges", "injectors")
        if all(k in t for k in keywords) and len(keywords) > best_score:
            best, best_score = syntax, len(keywords)
    return best


def parse(text):
    """Return (handler, arg) if the input is a ship command, else None.
    Anything that isn't a command is a question for KES — that routing decision lives in
    game.py, but the matching lives here because the ship owns the command vocabulary."""
    import re
    t = " ".join(str(text).strip().lower().split())
    for pattern, handler in COMMANDS:
        m = re.fullmatch(pattern, t)
        if m:
            return handler, (m.groupdict().get("arg") if m.groupdict() else None)
    return None


def can_run(s: Ship, text):
    """Would this command be accepted right now? Answered by running it on a deep copy and
    throwing the copy away, so there is exactly one source of truth for preconditions —
    the handlers themselves. The alternative, a parallel set of 'is it available' rules,
    would drift out of sync the first time a precondition changed."""
    import copy
    if s.over:
        return False
    hit = parse(text)
    if not hit:
        return False
    handler, arg = hit
    try:
        return bool(handler(copy.deepcopy(s), arg).ok)
    except Exception:
        return False


def execute(s: Ship, text):
    """Run one player command against the ship. Returns a Result.
    This is the only function that advances the clock."""
    if s.over:
        return _fail("The ship is not responding.")
    hit = parse(text)
    if not hit:
        return None                      # not a command; game.py sends it to KES
    handler, arg = hit
    res = handler(s, arg)
    if res.hours:
        advance(s, res.hours)
    if res.fatal and not s.over:
        s.over = True
        s.ending = s.ending or "scram"
    _check_end(s)
    return res


def advance(s: Ship, hours):
    """Move the clock. Air falls unless regeneration is running; pod 2's battery always
    falls. Called by execute(), and by game.py when the decision timer expires."""
    s.hours_elapsed += hours
    if not s.regen_running:
        s.air_hours -= hours
    if not s.wren_revived and not s.medbay_bus:
        s.pod2_hours -= hours
    _check_end(s)


def _check_end(s: Ship):
    if s.over:
        return
    if s.air_hours <= 0:
        s.over, s.ending = True, "asphyxiation"
    elif "F8" in s.active_faults and s.pod2_hours <= 0 and not s.wren_revived and not s.medbay_bus:
        # Not a loss — the optional objective simply expires, permanently.
        s.pod2_hours = 0
        s.pod2_lost = True
        s.active_faults.discard("F8")


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def new_game(variant, faults=None, rng=None):
    """Build a ship for one run. F1 (air) and F5 (comms) are always active; three or four
    others are drawn, with F2 weighted heavily because most of the others depend on power."""
    import random
    rng = rng or random.Random()
    if faults is None:
        # F1 (air) and F5 (comms) are the clock and the win. F2 (reactor) gates almost
        # everything else, and F8 (Wren's pod) is the optional objective — if it were part
        # of the random draw, most players would never meet it, so it is always on.
        pool = ["F3", "F4", "F7", "F9"]
        rng.shuffle(pool)
        faults = {"F1", "F5", "F2", "F8"} | set(pool[:rng.choice([2, 3])])
    s = Ship(variant=variant, active_faults=set(faults))

    # Faults that aren't active this run start already resolved, so the ship is internally
    # consistent no matter which subset was drawn.
    if "F2" not in s.active_faults:
        s.injectors = {1: "online", 2: "online", 3: "online"}
        s.reactor_pct = 98
    if "F3" not in s.active_faults:
        s.pump_b, s.seal_b_certified = "restored", True
    if "F4" not in s.active_faults:
        s.cargo_breached, s.patched, s.cargo_pressurized = False, True, True
    if "F7" not in s.active_faults:
        s.nav_buffer_dirty, s.star_fix = False, True
    if "F9" not in s.active_faults:
        s.quad3_online, s.rotating = False, False
    return s


# ---------------------------------------------------------------------------
# Self-test: walk the critical path, then die every way. Run: python ship.py
# ---------------------------------------------------------------------------

def selftest():
    variant = dict(sticky_injector=2, breached_panel="C-11",
                   spares_location="cargo locker C-7",
                   passphrase="Kestrel, kestrel, don't you fall")
    all_faults = {"F1", "F2", "F3", "F4", "F5", "F7", "F8", "F9"}

    def run(cmds, faults=all_faults, show=False):
        s = new_game(variant, faults=faults)
        out = []
        for c in cmds:
            r = execute(s, c)
            out.append((c, r.ok if r else None, r.text if r else "(question for KES)"))
            if show:
                print(f"   {c:<38} {'ok ' if r and r.ok else '-- '} {r.text[:60] if r else ''}")
        return s, out

    print("1. The intended winning path (Wren saved: reroute must beat the pod battery)")
    s, _ = run(["isolate pump b", "recertify seal b", "restore pump b",
                "cycle injector 2", "restart injector 2", "restart injector 3",
                "reroute habitat bus medbay", "revive pod 2", "run scrubber regen",
                "unlock pod 2",
                "disable quad 3", "null rotation",
                "purge nav buffer after md 231", "run star fix",
                "unlock core kestrel kestrel dont you fall",
                "align antenna", "transmit distress"], show=True)
    assert s.won and s.ending == "wren_revived", s.ending
    print(f"   -> WON ({s.ending}) with {s.air_hours:.1f}h air left, "
          f"{s.hours_elapsed:.1f}h elapsed\n")

    print("1c. Unlocking pod 2 too early kills Wrenfield (only Sandoval's log warns you)")
    s = new_game(variant, faults=all_faults)
    for c in ["isolate pump b", "recertify seal b", "restore pump b", "cycle injector 2",
              "restart injector 2", "restart injector 3", "reroute habitat bus medbay",
              "revive pod 2", "unlock pod 2"]:
        execute(s, c)
    assert s.wren_dead and not s.wren_revived and not s.over
    assert not execute(s, "revive pod 2").ok
    print("   -> Wrenfield dead, run continues\n")

    print("1d. `wait` lets the player spend the hour deliberately")
    s = new_game(variant, faults=all_faults)
    for c in ["isolate pump b", "recertify seal b", "restore pump b", "cycle injector 2",
              "restart injector 2", "restart injector 3", "reroute habitat bus medbay",
              "revive pod 2", "wait", "unlock pod 2"]:
        execute(s, c)
    assert s.wren_revived and not s.wren_dead
    print("   -> Wrenfield revived after the wait\n")

    print("1b. Beacon without Wren: pod 2 expires, run continues, ending differs")
    s = new_game(variant, faults=all_faults)
    advance(s, 10)                     # dithered past the battery
    assert s.pod2_lost and not s.over
    assert not execute(s, "reroute habitat bus medbay").ok or not execute(s, "revive pod 2").ok
    print("   -> pod 2 lost, not a game over\n")

    print("2. Death: reactor before coolant")
    s, _ = run(["cycle injector 2", "restart injector 2"])
    assert s.over and s.ending == "scram"
    print("   -> scram, as designed\n")

    print("3. Death: the atmosphere flush")
    s, _ = run(["atmosphere flush"])
    assert s.air_hours <= 1.5 and not s.regen_running
    execute(s, "run scrubber regen")
    advance(s, 2)
    assert s.over and s.ending == "asphyxiation"
    print("   -> asphyxiation two actions later, as designed\n")

    print("4. Death: venting the habitat")
    s, _ = run(["vent habitat"])
    assert s.over and s.ending == "vented"
    print("   -> vented\n")

    print("5. Death: the clock")
    s = new_game(variant, faults=all_faults)
    advance(s, 19)
    assert s.over and s.ending == "asphyxiation"
    print("   -> ran out of air\n")

    print("6. Injector faulted by skipping the hand-cycle")
    s, _ = run(["isolate pump b", "recertify seal b", "restore pump b", "restart injector 2"])
    assert s.injectors[2] == "faulted" and not s.over
    r = execute(s, "restart injector 3")
    assert s.reactor_pct == 70 and not s.over
    r = execute(s, "align antenna")
    assert not r.ok, "60% gate should block alignment at 70%... check tuning"
    print("   -> injector 2 dead, reactor capped at 70%, comms still possible\n")

    print("7. Cartridge route needs the hold patched first")
    s = new_game(variant, faults=all_faults)
    r = execute(s, "swap cartridges c-7")
    assert not r.ok
    for c in ["retrieve patch kit", "patch c-11", "repressurize cargo", "swap cartridges c-7"]:
        r = execute(s, c)
    assert s.cartridges_swapped and s.reserve_pct == 16
    print(f"   -> +20h air by the long route, reserve down to {s.reserve_pct}%\n")

    print("8. Wrong passphrase, then right one")
    s = new_game(variant, faults=all_faults)
    assert not execute(s, "unlock core easy now birdie").ok
    assert execute(s, "unlock core KESTREL, KESTREL, DON'T YOU FALL!").ok
    assert s.tier == "command"
    print("   -> punctuation and case forgiven\n")

    print("9. Nav purge with the wrong Mission Day")
    s = new_game(variant, faults=all_faults)
    assert not execute(s, "purge nav buffer after md 240").ok
    assert not execute(s, "purge nav buffer after md 214").ok
    assert execute(s, "purge nav buffer after md 231").ok
    print("   -> only MD 231 works\n")

    print("10. Questions are free, actions are not")
    s = new_game(variant, faults=all_faults)
    assert execute(s, "why is the coolant pressure dropping") is None
    assert s.hours_elapsed == 0
    execute(s, "isolate pump b")
    assert s.hours_elapsed == TUNING["action_cost"]
    print("   -> clock only moves on actions\n")

    print("11. Every fault subset produces a consistent ship")
    import itertools, random
    for combo in itertools.combinations(["F2", "F3", "F4", "F7", "F8", "F9"], 3):
        s = new_game(variant, faults={"F1", "F5"} | set(combo))
        s.alerts(); s.readings()
    print("   -> all subsets build and report\n")

    print("All ship.py self-tests passed.")


if __name__ == "__main__":
    selftest()
