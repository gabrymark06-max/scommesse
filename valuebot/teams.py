"""Riconoscimento delle squadre nel testo (nomi The Odds API + soprannomi italiani)."""
import re
import unicodedata
from dataclasses import dataclass


@dataclass
class EventInfo:
    id: str
    sport_key: str
    home: str
    away: str
    commence: int  # millisecondi epoch


def normalize(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9.\- ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


STOP = {"fc", "ac", "as", "ss", "ssc", "us", "bc", "cf", "afc", "sc", "sv", "vfb", "vfl", "tsg", "fsv", "calcio", "club",
        "the", "and", "de", "of", "1907", "1909", "1913", "1899", "04", "05"}

# Parole troppo comuni per identificare una squadra da sole
GENERIC = {
    "manchester", "united", "city", "real", "athletic", "atletico", "sporting", "borussia", "olympique", "racing",
    "deportivo", "union", "west", "town", "county", "rovers", "albion", "wanderers", "hotspur", "saint", "paris",
    "inter", "milan", "hove", "brighton", "nottingham", "forest", "crystal", "palace", "aston", "villa", "celta",
    "rayo", "stade", "stadium", "eintracht", "bayer", "hertha", "werder", "hellas", "sheffield", "leeds", "queens",
}

# Soprannomi e abbreviazioni. Chiave = nome The Odds API normalizzato.
ALIASES = {
    "inter milan": ["inter", "internazionale"],
    "ac milan": ["milan"],
    "juventus": ["juventus", "juve"],
    "as roma": ["roma"],
    "ss lazio": ["lazio"],
    "lazio": ["lazio"],
    "atalanta bc": ["atalanta", "dea"],
    "hellas verona fc": ["verona", "hellas verona"],
    "hellas verona": ["verona", "hellas verona"],
    "manchester city": ["manchester city", "man city", "city"],
    "manchester united": ["manchester united", "man united", "man utd", "united"],
    "tottenham hotspur": ["tottenham", "spurs"],
    "wolverhampton wanderers": ["wolverhampton", "wolves"],
    "brighton and hove albion": ["brighton"],
    "nottingham forest": ["nottingham forest", "forest", "nottingham"],
    "newcastle united": ["newcastle"],
    "west ham united": ["west ham"],
    "crystal palace": ["crystal palace", "palace"],
    "aston villa": ["aston villa", "villa"],
    "paris saint germain": ["paris saint germain", "psg", "paris sg", "paris"],
    "bayern munich": ["bayern", "bayern monaco", "bayern munich"],
    "borussia dortmund": ["dortmund", "borussia dortmund", "bvb"],
    "borussia monchengladbach": ["gladbach", "monchengladbach"],
    "bayer leverkusen": ["leverkusen", "bayer leverkusen"],
    "rb leipzig": ["lipsia", "leipzig"],
    "eintracht frankfurt": ["eintracht", "francoforte", "frankfurt"],
    "atletico madrid": ["atletico madrid", "atletico"],
    "real madrid": ["real madrid", "real"],
    "barcelona": ["barcellona", "barcelona", "barca"],
    "sevilla": ["siviglia", "sevilla"],
    "athletic bilbao": ["athletic bilbao", "bilbao", "athletic"],
    "real sociedad": ["real sociedad", "sociedad"],
    "real betis": ["betis", "real betis"],
    "olympique marseille": ["marsiglia", "marseille", "om"],
    "olympique lyonnais": ["lione", "lyon", "ol"],
    "as monaco": ["monaco"],
    "sporting lisbon": ["sporting", "sporting lisbona"],
    "fc porto": ["porto"],
    "benfica": ["benfica"],
}


def aliases_for(team):
    n = normalize(team)
    if n in ALIASES:
        return ALIASES[n]
    words = [w for w in n.split(" ") if w not in STOP]
    out = []
    if words:
        out.append(" ".join(words))
    for w in words:
        if len(w) >= 4 and w not in GENERIC and w not in out:
            out.append(w)
    return out


@dataclass
class Mention:
    start: int
    end: int
    event_id: str
    side: str  # home | away


def find_mentions(text, events):
    """Squadre citate in un testo normalizzato. Gli alias più lunghi hanno la precedenza."""
    by_alias = {}
    for ev in events:
        for side, name in (("home", ev.home), ("away", ev.away)):
            for al in aliases_for(name):
                by_alias.setdefault(al, []).append((ev.id, side, ev.commence))
    hits = []
    for al in by_alias:
        for m in re.finditer(r"(?<![a-z0-9])" + re.escape(al) + r"(?![a-z0-9])", text):
            hits.append((m.start(), m.end(), al))
    hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    taken = []
    for h in hits:
        if not any(h[0] < t[1] and t[0] < h[1] for t in taken):
            taken.append(h)
    taken.sort(key=lambda h: h[0])
    return [(s, e, by_alias[al]) for s, e, al in taken]


def resolve_mentions(mentions):
    """Se due squadre citate si affrontano è quella partita, altrimenti la prossima in calendario."""
    count = {}
    for _, _, cands in mentions:
        for eid in {c[0] for c in cands}:
            count[eid] = count.get(eid, 0) + 1
    out = []
    for s, e, cands in mentions:
        paired = [c for c in cands if count.get(c[0], 0) >= 2]
        pool = paired or cands
        best = min(pool, key=lambda c: c[2])
        out.append(Mention(s, e, best[0], best[1]))
    return out
