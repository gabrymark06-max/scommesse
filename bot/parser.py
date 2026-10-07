"""Interpretazione del testo di una super quota o di una richiesta di probabilità.

Esempi gestiti: "Inter vince e over 2.5 @3.50", "Inter, Napoli e Milan vincono a 4,00",
"Inter-Juventus: gol + over 2,5 @ 2.90", "Roma vince con almeno 2 gol di scarto @ 3.75".
"""
import re
from dataclasses import dataclass, field

from .teams import find_mentions, normalize, resolve_mentions

UNSUPPORTED_RE = re.compile(
    r"(angol|corner|cartellin|ammonit|ammonizion|espuls|\btiri\b|\btiro\b|assist|rigor|primo tempo|1 tempo|"
    r"secondo tempo|2 tempo|intervallo|\bpt\b|\bst\b|minut|marcator|tripletta|doppietta|falli|fuorigioco|parat|"
    r"cartell|sostituz|possesso)"
)
IGNORABLE = {"super", "quota", "superquota", "boost", "boosted", "bet365", "maggiorata", "giocata", "combo", "multipla",
             "la", "le", "il", "lo", "i", "gli", "e", "ed", "con", "della", "del", "partita", "match", "oggi", "stasera",
             "domani", "vs", "contro", "-", "speciale", "esclusiva", "ora", "era", "da", "a", "sq", "/sq"}


@dataclass
class ParsedBoost:
    odds: float = None
    legs: dict = field(default_factory=dict)  # event_id -> [selezioni]
    unsupported: list = field(default_factory=list)  # marcatori, angoli...
    unknown: list = field(default_factory=list)  # parti non capite
    events: list = field(default_factory=list)  # partite citate, anche senza giocata
    lone_teams: list = field(default_factory=list)  # partite con una squadra citata senza giocata


def _num(s):
    return float(s.replace(",", "."))


def extract_odds(raw):
    """Estrae la quota maggiorata e restituisce (quota, testo senza la quota)."""
    m = re.search(r"@\s*(\d+(?:[.,]\d+)?)", raw)
    if m:
        return _num(m.group(1)), raw.replace(m.group(0), " ", 1)
    m = re.search(r"\b(?:quota|ora|a)\s*:?\s*(\d+[.,]\d+)\s*$", raw, re.I)
    if m:
        return _num(m.group(1)), raw[: m.start()]
    # ultimo numero decimale non preceduto da over/under/più di/meno di
    cands = [
        m for m in re.finditer(r"(\d+[.,]\d+)", raw)
        if not re.search(r"(?:^|\s)(over|under|più di|piu di|meno di|oltre|o|u)\s*$", raw[max(0, m.start() - 12): m.start()].lower())
    ]
    if cands and _num(cands[-1].group(1)) > 1.01:
        last = cands[-1]
        return _num(last.group(1)), raw[: last.start()] + " " + raw[last.end():]
    return None, raw


def _team_sel(kind, side, n=0):
    if kind == "win":
        return {"t": "1x2", "o": "1" if side == "home" else "2"}
    if kind == "dc":
        return {"t": "dc", "o": "1X" if side == "home" else "X2"}
    if kind == "margin":
        return {"t": "margin", "team": side, "min": n}
    if kind == "scores":
        return {"t": "teamTotal", "team": side, "side": "over", "line": 0.5}
    if kind == "teamOver":
        return {"t": "teamTotal", "team": side, "side": "over", "line": n}
    if kind == "teamUnder":
        return {"t": "teamTotal", "team": side, "side": "under", "line": n}
    raise ValueError(kind)


def _n(x):
    v = float(x)
    return int(v) if v.is_integer() and "." not in x else v


def parse_boost(raw, events):
    odds, rest = extract_odds(raw)
    text = re.sub(r"(\d),(\d)", r"\1.\2", rest)
    clauses = [normalize(c) for c in re.split(r"[;+\n:,]|\s(?:e|ed|and|&)\s", text, flags=re.I)]
    clauses = [c for c in clauses if c]

    out = ParsedBoost(odds=odds)

    def add(eid, s):
        out.legs.setdefault(eid, []).append(s)

    full = " | ".join(clauses)
    resolved = resolve_mentions(find_mentions(full, events))
    per_clause, offset = [], 0
    for c in clauses:
        start = offset
        offset += len(c) + 3
        per_clause.append([
            type(m)(m.start - start, m.end - start, m.event_id, m.side)
            for m in resolved if m.start >= start and m.end <= start + len(c)
        ])

    last_event = None
    pending = []

    for cl, ms in zip(clauses, per_clause):
        if UNSUPPORTED_RE.search(cl):
            out.unsupported.append(cl)
            continue
        bare = cl
        for m in sorted(ms, key=lambda m: -m.start):
            bare = bare[: m.start] + " § " + bare[m.end:]
        bare = re.sub(r"\s+", " ", bare).strip()

        same_event = len(ms) >= 2 and all(m.event_id == ms[0].event_id for m in ms)
        ev = ms[-1].event_id if ms else last_event
        if ms:
            last_event = ms[-1].event_id

        def actor(verb_re):
            v = re.search(verb_re, cl)
            pos = v.start() if v else -1
            before = [m for m in ms if m.end <= pos]
            return before[-1] if before else (ms[0] if ms else None)

        sels = []
        team_kind = None

        if m := re.search(r"multigol\s*(\d+)\s*-\s*(\d+)", bare):
            if ev:
                sels.append((ev, {"t": "multigol", "lo": int(m.group(1)), "hi": int(m.group(2))}))
        elif (m := re.search(r"(?:risultato esatto|esatto|finisce|termina)\s*(\d+)\s*-\s*(\d+)", bare)) or (m := re.search(r"^§?\s*(\d)\s*-\s*(\d)\s*$", bare)):
            if ev:
                sels.append((ev, {"t": "exact", "h": int(m.group(1)), "a": int(m.group(2))}))
        elif re.search(r"(entrambe|ambedue|tutte e due).*(segn|gol)|^(§ )?(gol|goal|gg)$|\bgoal goal\b|\bgol gol\b", bare) and not re.search(r"no ?gol|\bng\b|non segn", bare):
            if ev:
                sels.append((ev, {"t": "btts", "yes": True}))
        elif re.search(r"no ?gol|no ?goal|\bng\b|non segnano entrambe|almeno una non segna", bare):
            if ev:
                sels.append((ev, {"t": "btts", "yes": False}))
        elif (m := re.search(r"(?:vince|vincono|vittoria).*?(\d+)\s*(?:\+|o piu)?\s*(?:gol|goal|reti) di (?:scarto|differenza)", bare)) or (m := re.search(r"handicap\s*-\s*(\d+)", bare)):
            n = int(m.group(1)) + 1 if "handicap" in bare else int(m.group(1))
            team_kind = ("margin", n)
        elif (m := re.search(r"(?:over|piu di|oltre)\s*(\d+(?:\.\d+)?)", bare)) or (m := re.search(r"almeno\s*(\d+)\s*(?:gol|goal|reti)", bare)):
            line = int(m.group(1)) - 0.5 if "almeno" in m.group(0) else _n(m.group(1))
            single = len(ms) == 1 and not re.search(r"partita|totale|match", bare)
            if single:
                team_kind = ("teamOver", line)
            elif ev:
                sels.append((ev, {"t": "total", "side": "over", "line": line}))
        elif m := re.search(r"(?:under|meno di)\s*(\d+(?:\.\d+)?)", bare):
            single = len(ms) == 1 and not re.search(r"partita|totale|match", bare)
            if single:
                team_kind = ("teamUnder", _n(m.group(1)))
            elif ev:
                sels.append((ev, {"t": "total", "side": "under", "line": _n(m.group(1))}))
        elif re.search(r"(o pareggio|o pari|doppia chance|non perde|non perdono|\b1x\b|\bx2\b)", bare):
            if re.search(r"\bx2\b", bare) and ev and not ms:
                sels.append((ev, {"t": "dc", "o": "X2"}))
            elif re.search(r"\b1x\b", bare) and ev and not ms:
                sels.append((ev, {"t": "dc", "o": "1X"}))
            else:
                team_kind = ("dc", 0)
        elif re.search(r"\b(vince|vincono|vittoria|batte|battono|vincente)\b", bare):
            team_kind = ("win", 0)
        elif re.search(r"\b(segna|segnano)\b", bare):
            if ms:
                team_kind = ("scores", 0)
            else:
                out.unsupported.append(cl)  # probabilmente un giocatore
                continue
        elif re.search(r"\b(pareggio|pareggiano|pari)\b", bare) or bare in ("§ x", "x"):
            if ev:
                sels.append((ev, {"t": "1x2", "o": "X"}))
        elif (m := re.search(r"(?:^|\s)(1x|x2|12|1|x|2)$", bare)) and ev:
            sign = m.group(1).upper()
            sels.append((ev, {"t": "dc", "o": sign} if sign in ("1X", "X2", "12") else {"t": "1x2", "o": sign}))

        if team_kind:
            who = actor(r"vinc|vitt|batt|segn|over|under|piu|meno|almeno|pareggio|pari|perd|handicap|scarto")
            if not who:
                out.unknown.append(cl)
                continue
            sels.append((who.event_id, _team_sel(team_kind[0], who.side, team_kind[1])))
            # "Inter, Napoli e Milan vincono": la stessa giocata vale per le squadre citate prima da sole
            for p in pending:
                sels.append((p.event_id, _team_sel(team_kind[0], p.side, team_kind[1])))
            pending = []

        if sels:
            for eid, s in sels:
                add(eid, s)
            continue
        if len(ms) == 1:
            pending.append(ms[0])
            continue
        if same_event:
            continue  # es. "Inter - Juventus": indica solo la partita
        words = [w for w in bare.replace("§", " ").split(" ") if w and w not in IGNORABLE]
        if words:
            out.unknown.append(cl)

    out.lone_teams = [p.event_id for p in pending if p.event_id not in out.legs]
    seen = []
    for m in resolved:
        if m.event_id not in seen:
            seen.append(m.event_id)
    out.events = seen
    return out
