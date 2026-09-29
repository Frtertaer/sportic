"""
Team name parser — handles user input in English and Russian.

Design:
- Separator detection via regex (vs, v, -, против, —)
- TEAM_ALIASES dict for common abbreviations in both languages
- difflib.SequenceMatcher (stdlib, no extra deps) for fuzzy alias matching
- Returns (team1, team2) tuple or (None, None) on parse failure
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# ── Aliases ────────────────────────────────────────────────────────────────────
# Keys: lowercase input variants → canonical English name for scrapers

TEAM_ALIASES: dict[str, str] = {
    # ── English abbreviations ────────────────────────────────────────────────
    "man city": "Manchester City",
    "man utd": "Manchester United",
    "man united": "Manchester United",
    "manchester utd": "Manchester United",
    "spurs": "Tottenham Hotspur",
    "tottenham": "Tottenham Hotspur",
    "wolves": "Wolverhampton Wanderers",
    "leicester": "Leicester City",
    "newcastle": "Newcastle United",
    "west ham": "West Ham United",
    "brighton": "Brighton & Hove Albion",
    "norwich": "Norwich City",
    "nottm forest": "Nottingham Forest",
    "nott forest": "Nottingham Forest",
    "sheff utd": "Sheffield United",
    "sheffield united": "Sheffield United",
    "luton": "Luton Town",
    "burnley": "Burnley FC",
    "barca": "Barcelona",
    "real": "Real Madrid",
    "atletico": "Atletico Madrid",
    "atletico de madrid": "Atletico Madrid",
    "atl. madrid": "Atletico Madrid",
    "atm": "Atletico Madrid",
    "sevilla": "Sevilla FC",
    "villarreal": "Villarreal CF",
    "betis": "Real Betis",
    "real betis balompie": "Real Betis",
    "sociedad": "Real Sociedad",
    "real sociedad de futbol": "Real Sociedad",
    "bvb": "Borussia Dortmund",
    "bvb 09": "Borussia Dortmund",
    "dortmund": "Borussia Dortmund",
    "gladbach": "Borussia Mönchengladbach",
    "borussia monchengladbach": "Borussia Mönchengladbach",
    "mainz": "Mainz 05",
    "leverkusen": "Bayer Leverkusen",
    "hoffenheim": "TSG Hoffenheim",
    "1899 hoffenheim": "TSG Hoffenheim",
    "frankfurt": "Eintracht Frankfurt",
    "bayern munich": "Bayern Munich",
    "bayern muenchen": "Bayern Munich",
    "fc bayern": "Bayern Munich",
    "fc bayern muenchen": "Bayern Munich",
    "fc bayern munchen": "Bayern Munich",
    "inter": "Inter Milan",
    "inter milan": "Inter Milan",
    "internazionale": "Inter Milan",
    "fc internazionale": "Inter Milan",
    "inter milano": "Inter Milan",
    "fc internazionale milano": "Inter Milan",
    "ac milan": "AC Milan",
    "milan": "AC Milan",
    "juve": "Juventus",
    "juventus": "Juventus",
    "napoli": "SSC Napoli",
    "roma": "AS Roma",
    "lazio": "SS Lazio",
    "atalanta": "Atalanta BC",
    "fiorentina": "ACF Fiorentina",
    "torino": "Torino FC",
    "bologna": "Bologna FC",
    "psg": "Paris Saint-Germain",
    "paris": "Paris Saint-Germain",
    "paris sg": "Paris Saint-Germain",
    "paris saint germain": "Paris Saint-Germain",
    "marseille": "Olympique Marseille",
    "om": "Olympique Marseille",
    "lyon": "Olympique Lyonnais",
    "ol": "Olympique Lyonnais",
    "monaco": "AS Monaco",
    "lille": "LOSC Lille",
    "ajax": "AFC Ajax",
    "ajax amsterdam": "AFC Ajax",
    "psv": "PSV Eindhoven",
    "feyenoord": "Feyenoord",
    "benfica": "SL Benfica",
    "sport lisboa e benfica": "SL Benfica",
    "porto": "FC Porto",
    "sporting": "Sporting CP",
    "sporting lisboa": "Sporting CP",
    "sporting lisbon": "Sporting CP",
    "galatasaray": "Galatasaray SK",
    "fenerbahce": "Fenerbahçe SK",
    "besiktas": "Beşiktaş JK",
    "shakhtar": "Shakhtar Donetsk",
    "fc shakhtar donetsk": "Shakhtar Donetsk",
    "dynamo kyiv": "Dynamo Kyiv",
    "dynamo kiev": "Dynamo Kyiv",
    "fc dynamo kyiv": "Dynamo Kyiv",

    # ── Russian names ─────────────────────────────────────────────────────────
    "реал мадрид": "Real Madrid",
    "реал": "Real Madrid",
    "барселона": "Barcelona",
    "атлетико мадрид": "Atletico Madrid",
    "атлетико": "Atletico Madrid",
    "севилья": "Sevilla FC",
    "манчестер сити": "Manchester City",
    "манчестер юнайтед": "Manchester United",
    "ман сити": "Manchester City",
    "ман юнайтед": "Manchester United",
    "арсенал": "Arsenal",
    "chelsea": "Chelsea",
    "челси": "Chelsea",
    "ливерпуль": "Liverpool",
    "тоттенхэм": "Tottenham Hotspur",
    "бавария": "Bayern Munich",
    "боруссия дортмунд": "Borussia Dortmund",
    "боруссия": "Borussia Dortmund",
    "ювентус": "Juventus",
    "интер": "Inter Milan",
    "интернационале": "Inter Milan",
    "милан": "AC Milan",
    "наполи": "SSC Napoli",
    "рома": "AS Roma",
    "пари сен-жермен": "Paris Saint-Germain",
    "пари сен жермен": "Paris Saint-Germain",
    "псж": "Paris Saint-Germain",
    "аякс": "AFC Ajax",
    "порту": "FC Porto",
    "бенфика": "SL Benfica",
    "шахтар": "Shakhtar Donetsk",
    "шахтер": "Shakhtar Donetsk",
    "динамо киев": "Dynamo Kyiv",
    "динамо": "Dynamo Kyiv",
    "зенит": "Zenit St. Petersburg",
    "fc zenit": "Zenit St. Petersburg",
    "zenit petersburg": "Zenit St. Petersburg",
    "спартак": "Spartak Moscow",
    "fc spartak moscow": "Spartak Moscow",
    "цска": "CSKA Moscow",
    "pfc cska moscow": "CSKA Moscow",
    "локомотив": "Lokomotiv Moscow",

    # ── English — Premier League additions ───────────────────────────────────
    "arsenal fc": "Arsenal",
    "chelsea fc": "Chelsea",
    "liverpool fc": "Liverpool",
    "afc bournemouth": "AFC Bournemouth",
    "bournemouth": "AFC Bournemouth",
    "brentford": "Brentford FC",
    "crystal palace": "Crystal Palace",
    "everton": "Everton FC",
    "fulham": "Fulham FC",
    "ipswich": "Ipswich Town",
    "ipswich town": "Ipswich Town",
    "nottingham": "Nottingham Forest",
    "nottingham forest": "Nottingham Forest",
    "southampton": "Southampton FC",
    "west brom": "West Bromwich Albion",
    "wba": "West Bromwich Albion",
    "sunderland": "Sunderland AFC",
    "stoke": "Stoke City",
    "middlesbrough": "Middlesbrough FC",
    "boro": "Middlesbrough FC",
    "leeds": "Leeds United",
    "derby": "Derby County",
    "cardiff": "Cardiff City",
    "hull": "Hull City",
    "swansea": "Swansea City",
    "qpr": "Queens Park Rangers",
    "millwall": "Millwall FC",
    "coventry": "Coventry City",
    "preston": "Preston North End",
    "blackburn": "Blackburn Rovers",
    "birmingham": "Birmingham City",
    "bristol city": "Bristol City",
    "charlton": "Charlton Athletic",
    "oxford utd": "Oxford United",
    "oxford united": "Oxford United",
    "portsmouth": "Portsmouth FC",
    "pompey": "Portsmouth FC",
    "watford": "Watford FC",
    "wrexham": "Wrexham AFC",
    "sheffield wed": "Sheffield Wednesday",
    "sheffield wednesday": "Sheffield Wednesday",

    # ── English — Champions League / Bundesliga clubs ─────────────────────────
    "man city fc": "Manchester City",
    "dortmund fc": "Borussia Dortmund",
    "rb leipzig": "RB Leipzig",
    "rasenball leipzig": "RB Leipzig",
    "red bull leipzig": "RB Leipzig",
    "leipzig": "RB Leipzig",
    "wolfsburg": "VfL Wolfsburg",
    "b. monchengladbach": "Borussia Mönchengladbach",
    "st. pauli": "FC St. Pauli",
    "st pauli": "FC St. Pauli",
    "fc st. pauli": "FC St. Pauli",
    "hertha": "Hertha BSC",
    "hertha berlin": "Hertha BSC",
    "schalke": "FC Schalke 04",
    "werder": "Werder Bremen",
    "werder bremen": "Werder Bremen",
    "cologne": "FC Cologne",
    "koln": "1. FC Köln",
    "1. fc koln": "1. FC Köln",
    "freiburg": "SC Freiburg",
    "augsburg": "FC Augsburg",
    "stuttgart": "VfB Stuttgart",
    "hamburg": "Hamburger SV",
    "hsv": "Hamburger SV",
    "heidenheim": "1. FC Heidenheim",
    "1. fc heidenheim": "1. FC Heidenheim",
    "bochum": "VfL Bochum",
    "vfl bochum": "VfL Bochum",
    "darmstadt": "SV Darmstadt 98",
    "dusseldorf": "Fortuna Düsseldorf",
    "fortuna dusseldorf": "Fortuna Düsseldorf",
    "holstein kiel": "Holstein Kiel",
    "kiel": "Holstein Kiel",
    "kaiserslautern": "1. FC Kaiserslautern",
    "1. fc kaiserslautern": "1. FC Kaiserslautern",
    "magdeburg": "1. FC Magdeburg",
    "arminia bielefeld": "Arminia Bielefeld",
    "bielefeld": "Arminia Bielefeld",
    "preussen munster": "Preußen Münster",
    "sg dynamo dresden": "Dynamo Dresden",
    "dynamo dresden": "Dynamo Dresden",
    "dresden": "Dynamo Dresden",
    "union berlin": "Union Berlin",
    "1. fc union berlin": "1. FC Union Berlin",
    "berlin": "Union Berlin",

    # ── Italian Serie A additions ─────────────────────────────────────────────
    "inter milan fc": "Inter Milan",
    "ac milan fc": "AC Milan",
    "juventus fc": "Juventus",
    "sassuolo": "US Sassuolo",
    "udinese": "Udinese Calcio",
    "empoli": "Empoli FC",
    "lecce": "US Lecce",
    "monza": "AC Monza",
    "salernitana": "US Salernitana",
    "genoa": "Genoa CFC",
    "cagliari": "Cagliari Calcio",
    "hellas verona": "Hellas Verona",
    "verona": "Hellas Verona",
    "frosinone": "Frosinone Calcio",
    "parma": "Parma Calcio",
    "venezia": "Venezia FC",
    "como": "Como 1907",
    "pisa": "Pisa Calcio",
    "sampdoria": "UC Sampdoria",
    "bari": "SSC Bari",
    "reggiana": "AC Reggiana",
    "sudtirol": "FC Südtirol",
    "pescara": "Pescara Calcio",

    # ── Spanish LaLiga additions ──────────────────────────────────────────────
    "elche": "Elche CF",
    "levante": "Levante UD",
    "celta": "Celta Vigo",
    "celta vigo": "Celta Vigo",
    "alaves": "Deportivo Alavés",
    "mallorca": "RCD Mallorca",
    "getafe": "Getafe CF",
    "rayo": "Rayo Vallecano",
    "rayo vallecano": "Rayo Vallecano",
    "osasuna": "CA Osasuna",
    "athletic": "Athletic Club",
    "athletic bilbao": "Athletic Club",
    "athletic de bilbao": "Athletic Club",
    "ath bilbao": "Athletic Club",
    "ath. bilbao": "Athletic Club",
    "bilbao": "Athletic Club",
    "espanyol": "RCD Espanyol",
    "granada": "Granada CF",
    "cadiz": "Cádiz CF",
    "girona": "Girona FC",
    "las palmas": "UD Las Palmas",
    "leganes": "CD Leganés",
    "valladolid": "Real Valladolid",

    # ── French Ligue 1 additions ──────────────────────────────────────────────
    "rennes": "Stade Rennais",
    "stade rennais": "Stade Rennais",
    "nantes": "FC Nantes",
    "nice": "OGC Nice",
    "ogc nice": "OGC Nice",
    "strasbourg": "RC Strasbourg",
    "lens": "RC Lens",
    "rc lens": "RC Lens",
    "stade brestois": "Stade Brestois",
    "brest": "Stade Brestois",
    "toulouse": "Toulouse FC",
    "stade de reims": "Stade de Reims",
    "reims": "Stade de Reims",
    "montpellier": "Montpellier HSC",
    "lorient": "FC Lorient",
    "metz": "FC Metz",
    "clermont": "Clermont Foot",
    "angers": "Angers SCO",
    "auxerre": "AJ Auxerre",
    "havre": "Le Havre AC",
    "le havre": "Le Havre AC",
    "saint-etienne": "AS Saint-Étienne",
    "st etienne": "AS Saint-Étienne",
    "paris fc": "Paris FC",
    "troyes": "ES Troyes AC",
    "guingamp": "EA Guingamp",
    "amiens": "Amiens SC",
    "laval": "Stade Lavallois",
    "nancy": "AS Nancy",
    "bastia": "SC Bastia",
    "grenoble": "Grenoble Foot",

    # ── Portuguese additions ──────────────────────────────────────────────────
    "braga": "SC Braga",
    "sporting braga": "SC Braga",
    "vitoria": "Vitória SC",
    "guimaraes": "Vitória SC",
    "famalicao": "FC Famalicão",
    "vitoria guimaraes": "Vitória SC",
    "arouca": "FC Arouca",
    "estoril": "Estoril Praia",
    "estrela": "Estrela Amadora",
    "gil vicente": "Gil Vicente FC",
    "moreirense": "Moreirense FC",
    "rio ave": "Rio Ave FC",
    "santa clara": "Santa Clara",
    "tondela": "CD Tondela",
    "casa pia": "Casa Pia AC",
    "alverca": "FC Alverca",

    # ── Dutch Eredivisie ──────────────────────────────────────────────────────
    "az alkmaar": "AZ Alkmaar",
    "az": "AZ Alkmaar",
    "vitesse": "Vitesse Arnhem",
    "twente": "FC Twente",
    "utrecht": "FC Utrecht",
    "groningen": "FC Groningen",
    "heerenveen": "SC Heerenveen",
    "sparta rotterdam": "Sparta Rotterdam",
    "nec nijmegen": "NEC Nijmegen",
    "nec": "NEC Nijmegen",
    "nijmegen": "NEC Nijmegen",
    "excelsior": "Excelsior Rotterdam",
    "telstar": "SC Telstar",
    "g.a. eagles": "Go Ahead Eagles",
    "go ahead eagles": "Go Ahead Eagles",
    "sittard": "Fortuna Sittard",
    "fortuna sittard": "Fortuna Sittard",

    # ── Scottish / Belgian / Nordic ───────────────────────────────────────────
    "aberdeen": "Aberdeen FC",
    "hearts": "Heart of Midlothian",
    "hibs": "Hibernian FC",
    "hibernian": "Hibernian FC",
    "celtic": "Celtic FC",
    "rangers": "Rangers FC",
    "dundee utd": "Dundee United",
    "dundee united": "Dundee United",
    "falkirk": "Falkirk FC",
    "kilmarnock": "Kilmarnock FC",
    "motherwell": "Motherwell FC",
    "st. mirren": "St Mirren FC",
    "st mirren": "St Mirren FC",
    "club brugge": "Club Brugge",
    "club brugge kv": "Club Brugge",
    "brugge": "Club Brugge",
    "anderlecht": "RSC Anderlecht",
    "gent": "KAA Gent",
    "genk": "KRC Genk",
    "racing genk": "KRC Genk",
    "charleroi": "Sporting Charleroi",
    "cercle brugge": "Cercle Brugge",
    "cercle brugge ksv": "Cercle Brugge",
    "royale union sg": "Royale Union SG",
    "union sg": "Royale Union SG",
    "st. liege": "Standard Liège",
    "standard liege": "Standard Liège",
    "st. truiden": "Sint-Truiden",
    "sint-truiden": "Sint-Truiden",
    "kv mechelen": "KV Mechelen",
    "mechelen": "KV Mechelen",
    "leuven": "OH Leuven",
    "westerlo": "KVC Westerlo",
    "dender": "FCV Dender",
    "raal la louviere": "RAAL La Louvière",
    "waregem": "SV Zulte Waregem",
    "zulte waregem": "SV Zulte Waregem",
    "antwerp": "Royal Antwerp FC",
    "royal antwerp": "Royal Antwerp FC",
    "royal antwerp fc": "Royal Antwerp FC",
    "malmo": "Malmö FF",
    "malmo ff": "Malmö FF",
    "copenhagen": "FC Copenhagen",
    "midtjylland": "FC Midtjylland",
    "fc midtjylland": "FC Midtjylland",
    "bodo glimt": "FK Bodø/Glimt",
    "bodo": "FK Bodø/Glimt",
    "rosenborg": "Rosenborg BK",
    "red star": "Red Star Belgrade",
    "crvena zvezda": "Red Star Belgrade",
    "fk crvena zvezda": "Red Star Belgrade",
    "partizan": "FK Partizan",
    "dinamo zagreb": "Dinamo Zagreb",
    "gnk dinamo zagreb": "Dinamo Zagreb",
    "hajduk split": "HNK Hajduk Split",

    # ── Turkish additions ─────────────────────────────────────────────────────
    "galatasaray sk": "Galatasaray SK",
    "trabzonspor": "Trabzonspor",
    "basaksehir": "Istanbul Basaksehir",
    "istanbul": "Istanbul Basaksehir",
    "fenerbahce sk": "Fenerbahçe SK",
    "besiktas jk": "Beşiktaş JK",
    "alanyaspor": "Alanyaspor",
    "gaziantep": "Gaziantep FK",
    "genclerbirligi": "Gençlerbirliği",
    "goztepe": "Göztepe",
    "kasimpasa": "Kasımpaşa",
    "kayserispor": "Kayserispor",

    # ── Russian / Eastern European ────────────────────────────────────────────
    "zenit": "Zenit St. Petersburg",
    "зенит санкт-петербург": "Zenit St. Petersburg",
    "спартак москва": "Spartak Moscow",
    "цска москва": "CSKA Moscow",
    "локомотив москва": "Lokomotiv Moscow",
    "краснодар": "FC Krasnodar",
    "krasnodar": "FC Krasnodar",
    "ростов": "FC Rostov",
    "rostov": "FC Rostov",
    "шахтер донецк": "Shakhtar Donetsk",
    "dynamo moscow": "Dynamo Moscow",
    "динамо москва": "Dynamo Moscow",
    "rubin kazan": "Rubin Kazan",
    "рубин казань": "Rubin Kazan",
    "рубин": "Rubin Kazan",
    "akhmat grozny": "Akhmat Grozny",
    "ахмат": "Akhmat Grozny",
    "ахмат грозный": "Akhmat Grozny",
    "krylya sovetov": "Krylya Sovetov",
    "крылья советов": "Krylya Sovetov",
    "orenburg": "FK Orenburg",
    "оренбург": "FK Orenburg",
    "dyn. kyiv": "Dynamo Kyiv",

    # ── Latin American ────────────────────────────────────────────────────────
    "flamengo": "Flamengo",
    "palmeiras": "Palmeiras",
    "fluminense": "Fluminense",
    "corinthians": "Corinthians",
    "santos": "Santos FC",
    "gremio": "Grêmio",
    "river plate": "River Plate",
    "boca juniors": "Boca Juniors",
    "boca": "Boca Juniors",
    "river": "River Plate",
    "independiente": "Independiente",
    "nacional": "Nacional",
    "penarol": "Peñarol",
    "america": "Club América",
    "chivas": "Guadalajara",
    "guadalajara": "Guadalajara",
    "tigres": "Tigres UANL",
    "monterrey": "CF Monterrey",

    # ── MLS ─────────────────────────────────────────────────────────────────────
    "inter miami": "Inter Miami CF",
    "atlanta utd": "Atlanta United",
    "atlanta united": "Atlanta United",
    "la galaxy": "Los Angeles Galaxy",
    "los angeles galaxy": "Los Angeles Galaxy",
    "los angeles fc": "Los Angeles FC",
    "lafc": "Los Angeles FC",
    "new york city": "New York City FC",
    "nycfc": "New York City FC",
    "new york red bulls": "New York Red Bulls",
    "chicago fire": "Chicago Fire FC",
    "dc united": "DC United",
    "seattle sounders": "Seattle Sounders FC",
    "portland timbers": "Portland Timbers",
    "toronto fc": "Toronto FC",
    "cf montreal": "CF Montréal",
    "columbus crew": "Columbus Crew",
    "orlando city": "Orlando City SC",
    "nashville sc": "Nashville SC",
    "austin fc": "Austin FC",
    "st. louis city": "St. Louis City SC",
    "philadelphia union": "Philadelphia Union",
    "colorado rapids": "Colorado Rapids",
    "houston dynamo": "Houston Dynamo FC",
    "minnesota united": "Minnesota United FC",
    "real salt lake": "Real Salt Lake",
    "san jose earthquakes": "San Jose Earthquakes",
    "sporting kansas city": "Sporting Kansas City",
    "vancouver whitecaps": "Vancouver Whitecaps FC",
    "new england revolution": "New England Revolution",
    "charlotte": "Charlotte FC",
    "san diego fc": "San Diego FC",
    "cruzeiro": "Cruzeiro EC",
    "atletico mineiro": "Atlético Mineiro",
    "atl mineiro": "Atlético Mineiro",

    # ── English common shortcuts / nicknames ──────────────────────────────────
    "spurs fc": "Tottenham Hotspur",
    "the blues": "Chelsea",
    "the gunners": "Arsenal",
    "the reds": "Liverpool",
    "lfc": "Liverpool",
    "mufc": "Manchester United",
    "mcfc": "Manchester City",
    "cfc": "Chelsea",
    "afc": "Arsenal",
    "nufc": "Newcastle United",
    "thfc": "Tottenham Hotspur",
    "avfc": "Aston Villa",
    "aston villa": "Aston Villa",
    "villa": "Aston Villa",
}

# ── Separator regex ────────────────────────────────────────────────────────────
_SEP_RE = re.compile(
    r"\s+(?:vs\.?|v\.?|against|против|—|-)\s+",
    re.IGNORECASE | re.UNICODE,
)

_CLUB_PREFIX_RE = re.compile(
    r"^(?:FC|CF|SC|AFC|RFC|JK|SK|SL|SSC|AS|ACF|TSG|SS|US|AC|RCD|CD|UD|SD|AD|RC|CA)\s+",
    re.IGNORECASE,
)
_CLUB_SUFFIX_RE = re.compile(
    r"\s+(?:FC|CF|SC|AFC|RFC|JK|SK|SL|SSC|AS|ACF|TSG)$",
    re.IGNORECASE,
)


def normalize_team_name(raw: str) -> str:
    """
    Clean and normalize a team name:
    1. Strip whitespace and common suffixes (FC, SC, etc.)
    2. Check exact alias match (case-insensitive)
    3. Fuzzy alias match via SequenceMatcher (threshold 0.82)
    4. Title-case the result
    """
    raw = raw.strip()
    lower = raw.lower()
    cleaned = _strip_common_club_affixes(raw)
    cleaned_lower = cleaned.lower()

    # Exact alias
    if lower in TEAM_ALIASES:
        return TEAM_ALIASES[lower]
    if cleaned_lower in TEAM_ALIASES:
        return TEAM_ALIASES[cleaned_lower]

    # Fuzzy alias match
    best_ratio = 0.0
    best_canonical = ""
    for alias, canonical in TEAM_ALIASES.items():
        ratio_raw = SequenceMatcher(None, lower, alias).ratio()
        ratio_cleaned = SequenceMatcher(None, cleaned_lower, alias).ratio()
        ratio = max(ratio_raw, ratio_cleaned)
        if ratio > best_ratio:
            best_ratio = ratio
            best_canonical = canonical
    if best_ratio >= 0.82:
        return best_canonical

    return cleaned or raw.title()


def _strip_common_club_affixes(raw: str) -> str:
    cleaned = raw.strip()
    previous = None
    while cleaned and cleaned != previous:
        previous = cleaned
        cleaned = _CLUB_PREFIX_RE.sub("", cleaned).strip()
        cleaned = _CLUB_SUFFIX_RE.sub("", cleaned).strip()
    return cleaned


def parse_teams_from_input(raw: str) -> tuple[str | None, str | None]:
    """
    Parse team names from user command argument.
    Returns (team1, team2) or (None, None).

    Supports: "Arsenal vs Chelsea", "Реал Мадрид - Барселона",
              "Man City v Liverpool", "Bayern gegen Dortmund"
    """
    raw = raw.strip()

    # Primary: regex separator split
    parts = _SEP_RE.split(raw, maxsplit=1)
    if len(parts) == 2:
        t1 = normalize_team_name(parts[0].strip())
        t2 = normalize_team_name(parts[1].strip())
        if t1 and t2 and t1.lower() != t2.lower():
            return t1, t2

    # Fallback: simple keyword scan
    lower = raw.lower()
    for sep in (" vs ", " v ", " - ", " против ", " — ", " gegen ", " vs. "):
        idx = lower.find(sep)
        if idx != -1:
            t1 = normalize_team_name(raw[:idx].strip())
            t2 = normalize_team_name(raw[idx + len(sep):].strip())
            if t1 and t2 and t1.lower() != t2.lower():
                return t1, t2

    return None, None


def suggest_teams(raw: str, max_suggestions: int = 3) -> list[str]:
    """
    When parse_teams_from_input fails, suggest closest team names from TEAM_ALIASES.

    Tries to split the input on known separators first; if that fails, treats
    the whole string as a single team query.  Returns up to max_suggestions
    canonical team names sorted by similarity (descending).
    """
    # Collect unique canonical names for matching
    canonical_names: dict[str, list[str]] = {}  # canonical -> [aliases]
    for alias, canonical in TEAM_ALIASES.items():
        canonical_names.setdefault(canonical, []).append(alias)

    fragments: list[str] = []
    # Try to split on separators
    parts = _SEP_RE.split(raw.strip(), maxsplit=1)
    if len(parts) == 2:
        fragments = [p.strip() for p in parts if p.strip()]
    else:
        lower = raw.lower()
        for sep in (" vs ", " v ", " - ", " против ", " — ", " gegen ", " vs. "):
            idx = lower.find(sep)
            if idx != -1:
                fragments = [raw[:idx].strip(), raw[idx + len(sep):].strip()]
                break
    if not fragments:
        fragments = [raw.strip()]

    suggestions: list[str] = []
    for fragment in fragments:
        frag_lower = fragment.lower()
        cleaned = _strip_common_club_affixes(fragment).lower()

        scored: list[tuple[float, str]] = []
        for canonical, aliases in canonical_names.items():
            # Check canonical name itself
            best = max(
                SequenceMatcher(None, frag_lower, canonical.lower()).ratio(),
                SequenceMatcher(None, cleaned, canonical.lower()).ratio(),
            )
            # Check aliases
            for alias in aliases:
                r = max(
                    SequenceMatcher(None, frag_lower, alias).ratio(),
                    SequenceMatcher(None, cleaned, alias).ratio(),
                )
                if r > best:
                    best = r
            if best >= 0.45:  # lower threshold for suggestions
                scored.append((best, canonical))

        scored.sort(key=lambda x: x[0], reverse=True)
        for _, name in scored[:max_suggestions]:
            if name not in suggestions:
                suggestions.append(name)

    return suggestions[:max_suggestions]
