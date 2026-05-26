"""
seed_dummy_cases.py
══════════════════════════════════════════════════════════════════════════════
Seeds 100 realistic dummy cases for model testing.

Assumes:
  • seed_lookups.py has been run (lkp_* tables populated)
  • seed_users.py has been run (admin + wajdan.mustafa exist)
  • The 10 investigators from the previous seed_dummy_data.py run exist
    (rana.ali, sajid.iqbal, ayesha.malik, kamran.shah, fariha.tariq,
     hamza.qureshi, zara.hussain, yasir.bhatti, nida.javed, imran.afridi)

Intentional design choices:
  • One investigator (`nida.javed`) is deliberately left with ZERO cases,
    so the UI can show what an empty investigator dashboard looks like.
  • No Lead rows, no CaseLink rows (will be tested separately).
  • Cases distributed across all 7 provinces with 65 distinct
    (province, city, area, police_station) tuples — no clumping.
  • Per-case randomization on weapon, vehicle, status, severity, CCTV,
    progress, dates, victim/suspect demographics, witnesses, evidence,
    timeline events, and notes — so no two cases look the same.

All lookup codes verified against seed_lookups.py:
  CaseType    : MURDER, RAPE, ROBBERY, THEFT, ASSAULT, FRAUD,
                KIDNAPPING, DRUG, EXTORTION, MISSING, TERRORISM,
                SMUGGLING, ARSON, DOMESTIC, OTHER
  Weapon      : PISTOL, RIFLE, SHOTGUN, KNIFE, BLUNT, VEHICLE,
                CHEMICAL, EXPLOSIVE, PHYSICAL, UNKNOWN, NONE
  SuspectStatus: AT_LARGE, DETAINED, ARRESTED, CHARGED, ABSCONDING,
                 SURVEILLANCE, RELEASED, CLEARED
  VictimStatus : ALIVE, DECEASED, CRITICAL, HOSPITALIZED, MISSING
  CaseStatus   : OPEN, UNDER_INVESTIGATION, ACTIVE, PENDING, CLOSED,
                 COLD_CASE, ARCHIVED
══════════════════════════════════════════════════════════════════════════════
"""

import random
from datetime import UTC, datetime, date, timedelta

from db import session_scope
from models import (
    # Lookups
    Province, City, CaseType, CaseStatus, Severity, Weapon, EvidenceType,
    SuspectStatus, VictimStatus, WitnessType, WitnessCredibility,
    TimelineEventType, NoteCategory,
    # Users
    User,
    # Operational
    Person, Case, Location,
    MurderDetails, SexualAssaultDetails, TheftDetails,
    CaseSuspect, CaseVictim, CaseWitness,
    Evidence, ExtractedEntity, CompletenessReport,
    CompletenessMissingField, TimelineEvent, InvestigationNote,
)

# Reproducible randomness — same dataset every run
random.seed(20260520)


# ════════════════════════════════════════════════════════════════════════════
# Configuration: which investigators receive cases
# ────────────────────────────────────────────────────────────────────────────
# `nida.javed` is intentionally excluded so her dashboard renders the
# "no cases yet" state. Re-add her username here if you change your mind.
# ════════════════════════════════════════════════════════════════════════════

ACTIVE_INVESTIGATOR_USERNAMES = [
    "rana.ali",
    "sajid.iqbal",
    "ayesha.malik",
    "kamran.shah",
    "fariha.tariq",
    "hamza.qureshi",
    "zara.hussain",
    "yasir.bhatti",
    # "nida.javed",   ← intentionally left out (no cases)
    "imran.afridi",
]

EMPTY_INVESTIGATOR_USERNAME = "nida.javed"


# ════════════════════════════════════════════════════════════════════════════
# Lookup-resolver
# ════════════════════════════════════════════════════════════════════════════

class L:
    def __init__(self, db):
        self.db = db
        self._cache = {}

    def _id(self, model, code, code_field="code"):
        key = (model.__name__, code)
        if key in self._cache:
            return self._cache[key]
        row = self.db.query(model).filter(getattr(model, code_field) == code).first()
        if not row:
            raise RuntimeError(
                f"Lookup miss: {model.__name__}.{code_field}={code!r} — "
                "run seed_lookups.py first"
            )
        self._cache[key] = row.id
        return row.id

    def _id_pair(self, model, f1, v1, f2, v2):
        key = (model.__name__, v1, v2)
        if key in self._cache:
            return self._cache[key]
        row = self.db.query(model).filter(
            getattr(model, f1) == v1, getattr(model, f2) == v2,
        ).first()
        if not row:
            # Auto-create city if missing (matches case_register_service)
            if model is City:
                new_city = City(province_id=v2, name=v1, sort_order=999, active=True)
                self.db.add(new_city); self.db.flush()
                self._cache[key] = new_city.id
                return new_city.id
            raise RuntimeError(f"Lookup miss: {model.__name__} where {f1}={v1!r}, {f2}={v2}")
        self._cache[key] = row.id
        return row.id

    def province(self, c):       return self._id(Province, c)
    def city(self, name, prov):  return self._id_pair(City, "name", name, "province_id", self.province(prov))
    def case_type(self, c):      return self._id(CaseType, c)
    def case_status(self, c):    return self._id(CaseStatus, c)
    def severity(self, c):       return self._id(Severity, c)
    def weapon(self, c):         return self._id(Weapon, c)
    def evidence_type(self, c):  return self._id(EvidenceType, c)
    def suspect_status(self, c): return self._id(SuspectStatus, c)
    def victim_status(self, c):  return self._id(VictimStatus, c)
    def witness_type(self, c):   return self._id(WitnessType, c)
    def witness_cred(self, c):   return self._id(WitnessCredibility, c)
    def timeline_event_type(self, c): return self._id(TimelineEventType, c)
    def note_category(self, c):  return self._id(NoteCategory, c)


# ════════════════════════════════════════════════════════════════════════════
# Geographic master data — must match seeded province codes + city names
# ────────────────────────────────────────────────────────────────────────────
# Cities here that aren't in seed_lookups.py CITIES dict will be
# auto-created by L.city() at sort_order=999. The (province, city) pairs
# below are chosen so common ones (Karachi, Lahore, Islamabad, Peshawar,
# Quetta, etc.) already exist — keeping auto-created rows to a minimum.
# ════════════════════════════════════════════════════════════════════════════

GEO = [
    # ── PUNJAB ───────────────────────────────────────────────────────────
    ("PUNJAB", "Lahore",      "Gulberg",         "Gulberg Police Station",      31.5204, 74.3587),
    ("PUNJAB", "Lahore",      "Defence Phase 5", "DHA Police Post",             31.4762, 74.4126),
    ("PUNJAB", "Lahore",      "Model Town",      "Model Town Police Station",   31.4830, 74.3210),
    ("PUNJAB", "Lahore",      "Iqbal Town",      "Iqbal Town Police Station",   31.5012, 74.2871),
    ("PUNJAB", "Rawalpindi",  "Saddar",          "Saddar Police Station",       33.5973, 73.0479),
    ("PUNJAB", "Rawalpindi",  "Westridge",       "Westridge Police Station",    33.6024, 73.0354),
    ("PUNJAB", "Rawalpindi",  "Chaklala",        "Chaklala Police Station",     33.5345, 73.1010),
    ("PUNJAB", "Multan",      "Cantt",           "Multan Cantt Police Station", 30.1798, 71.4928),
    ("PUNJAB", "Multan",      "Shah Rukn-e-Alam","Shah Rukn-e-Alam PS",         30.1900, 71.4500),
    ("PUNJAB", "Faisalabad",  "D-Ground",        "D-Ground Police Station",     31.4504, 73.1350),
    ("PUNJAB", "Faisalabad",  "Jaranwala Road",  "Jaranwala Road PS",           31.4670, 73.2100),
    ("PUNJAB", "Gujranwala",  "Civil Lines",     "Civil Lines Police Station",  32.1877, 74.1945),
    ("PUNJAB", "Sialkot",     "Cantt",           "Sialkot Cantt PS",            32.5071, 74.5358),
    ("PUNJAB", "Bahawalpur",  "Model Town A",    "Model Town A PS",             29.3956, 71.6722),
    ("PUNJAB", "Sargodha",    "Satellite Town",  "Satellite Town PS",           32.0836, 72.6711),

    # ── SINDH ─────────────────────────────────────────────────────────────
    ("SINDH",  "Karachi",     "Clifton",         "Clifton Police Station",      24.8133, 67.0286),
    ("SINDH",  "Karachi",     "Defence (DHA)",   "Darakhshan Police Station",   24.8019, 67.0467),
    ("SINDH",  "Karachi",     "Saddar",          "Saddar Police Station",       24.8607, 67.0011),
    ("SINDH",  "Karachi",     "Gulshan-e-Iqbal", "Gulshan PS",                  24.9180, 67.0971),
    ("SINDH",  "Karachi",     "North Nazimabad", "North Nazimabad PS",          24.9320, 67.0379),
    ("SINDH",  "Karachi",     "Korangi",         "Korangi Industrial PS",       24.8470, 67.1410),
    ("SINDH",  "Karachi",     "Orangi Town",     "Orangi Town PS",              24.9420, 66.9963),
    ("SINDH",  "Hyderabad",   "Latifabad",       "Latifabad Police Station",    25.3792, 68.3683),
    ("SINDH",  "Hyderabad",   "Qasimabad",       "Qasimabad Police Station",    25.3870, 68.3245),
    ("SINDH",  "Sukkur",      "New Sukkur",      "New Sukkur Police Station",   27.7050, 68.8579),
    ("SINDH",  "Larkana",     "Civil Lines",     "Larkana Civil Lines PS",      27.5590, 68.2123),
    ("SINDH",  "Nawabshah",   "Cantt",           "Nawabshah Cantt PS",          26.2442, 68.4090),
    ("SINDH",  "Mirpur Khas", "City",            "Mirpur Khas City PS",         25.5276, 69.0125),

    # ── KHYBER PAKHTUNKHWA (KPK) ──────────────────────────────────────────
    ("KPK",    "Peshawar",    "Hayatabad",       "Hayatabad Police Station",    33.9836, 71.4380),
    ("KPK",    "Peshawar",    "University Town", "University Town PS",          34.0048, 71.4750),
    ("KPK",    "Peshawar",    "Cantt",           "Peshawar Cantt PS",           33.9990, 71.5380),
    ("KPK",    "Mardan",      "City",            "Mardan City PS",              34.1986, 72.0404),
    ("KPK",    "Abbottabad",  "Mandian",         "Mandian Police Station",      34.1463, 73.2118),
    ("KPK",    "Mingora",     "City",            "Mingora City PS",             34.7717, 72.3611),
    ("KPK",    "Kohat",       "City",            "Kohat City PS",               33.5870, 71.4432),
    ("KPK",    "Bannu",       "City",            "Bannu City PS",               32.9890, 70.6055),
    ("KPK",    "Dera Ismail Khan", "Cantt",      "DI Khan Cantt PS",            31.8313, 70.9028),

    # ── BALOCHISTAN ───────────────────────────────────────────────────────
    ("BALOCHISTAN", "Quetta",  "Cantt",          "Quetta Cantt Police Station", 30.1798, 66.9750),
    ("BALOCHISTAN", "Quetta",  "Satellite Town", "Satellite Town PS",           30.1944, 67.0011),
    ("BALOCHISTAN", "Quetta",  "Hazara Town",    "Hazara Town PS",              30.2080, 66.9542),
    ("BALOCHISTAN", "Gwadar",  "Padi Zir",       "Gwadar City PS",              25.1216, 62.3254),
    ("BALOCHISTAN", "Turbat",  "City",           "Turbat City PS",              26.0029, 63.0594),
    ("BALOCHISTAN", "Khuzdar", "City",           "Khuzdar City PS",             27.8090, 66.6131),
    ("BALOCHISTAN", "Hub",     "Industrial",     "Hub Industrial PS",           25.2480, 66.7800),
    ("BALOCHISTAN", "Chaman",  "Border Town",    "Chaman Border PS",            30.9203, 66.4499),

    # ── ISLAMABAD CAPITAL TERRITORY (ICT) ─────────────────────────────────
    ("ICT",    "Islamabad",   "F-6",             "Kohsar Police Station",       33.7330, 73.0782),
    ("ICT",    "Islamabad",   "F-7",             "Kohsar Police Station",       33.7280, 73.0530),
    ("ICT",    "Islamabad",   "F-8",             "Margalla Police Station",     33.7100, 73.0500),
    ("ICT",    "Islamabad",   "F-10",            "Margalla Police Station",     33.6883, 72.9970),
    ("ICT",    "Islamabad",   "G-9",             "Aabpara Police Station",      33.6938, 73.0470),
    ("ICT",    "Islamabad",   "G-11",            "Tarnol Police Station",       33.6680, 72.9590),
    ("ICT",    "Islamabad",   "I-8",             "Industrial Area PS",          33.6685, 73.0780),
    ("ICT",    "Islamabad",   "Bhara Kahu",      "Bhara Kahu Police Station",   33.7330, 73.2010),
    ("ICT",    "Islamabad",   "Bani Gala",       "Bani Gala PS",                33.6890, 73.1305),

    # ── GILGIT-BALTISTAN (GB) ─────────────────────────────────────────────
    ("GB",     "Gilgit",      "City Area",       "Gilgit City Police Station",  35.9208, 74.3144),
    ("GB",     "Skardu",      "Bazaar",          "Skardu City PS",              35.2971, 75.6336),
    ("GB",     "Hunza",       "Karimabad",       "Hunza Karimabad PS",          36.3157, 74.6498),
    ("GB",     "Diamer",      "Chilas",          "Chilas City PS",              35.4180, 74.1010),
    ("GB",     "Astore",      "Bazaar",          "Astore Bazaar PS",            35.3680, 74.8580),

    # ── AZAD JAMMU & KASHMIR (AJK) ────────────────────────────────────────
    ("AJK",    "Muzaffarabad","Lower Plate",     "Muzaffarabad City PS",        34.3700, 73.4710),
    ("AJK",    "Mirpur",      "Allama Iqbal Rd", "Mirpur City PS",              33.1480, 73.7510),
    ("AJK",    "Rawalakot",   "Bazaar",          "Rawalakot Bazaar PS",         33.8580, 73.7613),
    ("AJK",    "Kotli",       "City",            "Kotli City PS",               33.5180, 73.9020),
    ("AJK",    "Bagh",        "Main Bazaar",     "Bagh Main Bazaar PS",         33.9790, 73.7780),
    ("AJK",    "Poonch",      "City",            "Poonch City PS",              33.7700, 73.9300),
]


# ════════════════════════════════════════════════════════════════════════════
# Name pools — varied across regions/genders
# ════════════════════════════════════════════════════════════════════════════

MALE_FIRST = [
    "Ahmed", "Ali", "Bilal", "Faisal", "Hassan", "Hamza", "Hussain", "Imran",
    "Jamal", "Junaid", "Kashif", "Khalid", "Mansoor", "Naveed", "Omar", "Qasim",
    "Rashid", "Saad", "Salman", "Tahir", "Umar", "Wasim", "Yousaf", "Zain",
    "Abdullah", "Adeel", "Asad", "Babar", "Daniyal", "Ehsan", "Faraz", "Furqan",
    "Ghulam", "Habib", "Iftikhar", "Jahangir", "Kamran", "Liaqat", "Mehmood",
    "Noman", "Owais", "Pervaiz", "Qadeer", "Raheel", "Shahid", "Tariq", "Usman",
    "Waqas", "Yasir", "Zubair",
]
FEMALE_FIRST = [
    "Ayesha", "Aisha", "Bushra", "Fatima", "Hira", "Iqra", "Javeria", "Khadija",
    "Laiba", "Maria", "Nadia", "Noor", "Rabia", "Sana", "Sadia", "Tahira",
    "Uzma", "Wajiha", "Yasmin", "Zainab", "Amna", "Bisma", "Faiza", "Gulnaz",
    "Hina", "Iram", "Komal", "Lubna", "Mahnoor", "Nimra", "Rida", "Sumaira",
]
LAST_NAMES = [
    "Ahmed", "Ali", "Khan", "Malik", "Sheikh", "Hussain", "Raza", "Iqbal",
    "Mehmood", "Akhtar", "Shah", "Qureshi", "Awan", "Cheema", "Bhatti", "Butt",
    "Chaudhry", "Hashmi", "Jafri", "Kazmi", "Lodhi", "Nawaz", "Pasha", "Rana",
    "Siddiqui", "Tariq", "Usmani", "Warraich", "Zafar", "Abbasi", "Baig",
    "Farooqi", "Gillani", "Haider", "Jamali", "Khattak", "Marwat", "Niazi",
    "Pirzada", "Rizvi", "Sarwar", "Yousafzai",
]


def _cnic() -> str:
    return f"{random.randint(11101, 71402):05d}-{random.randint(1000000, 9999999):07d}-{random.randint(1, 9)}"


def _phone() -> str:
    return f"+92-{random.randint(300, 349)}-{random.randint(1000000, 9999999):07d}"


def _name(gender: str) -> str:
    first = random.choice(MALE_FIRST if gender == "Male" else FEMALE_FIRST)
    last  = random.choice(LAST_NAMES)
    return f"{first} {last}"


def upsert_person(db, **kwargs) -> Person:
    """Find by CNIC if provided, else create new."""
    cnic = kwargs.get("cnic")
    if cnic:
        existing = db.query(Person).filter_by(cnic=cnic).first()
        if existing:
            return existing
    p = Person(**kwargs)
    db.add(p); db.flush()
    return p


# ════════════════════════════════════════════════════════════════════════════
# Per-crime-type templates — every code below matches seed_lookups.py exactly
# ════════════════════════════════════════════════════════════════════════════

CRIME_PROFILES = {
    "MURDER": {
        "ppc":     "PPC 302, 460",
        "weapons": ["KNIFE", "PISTOL", "BLUNT", "RIFLE", "UNKNOWN"],
        "titles": [
            "Murder – {victim} in {area}",
            "Homicide investigation – {area}, {city}",
            "Stabbing death – {victim}",
            "Fatal shooting – {area}",
        ],
        "descriptions": [
            "Victim was found deceased at residence after neighbours reported a disturbance. Body bore multiple stab wounds. No signs of forced entry.",
            "Victim shot at close range in a targeted attack. Eyewitnesses report two assailants on a motorcycle.",
            "Body recovered from a vacant lot. Initial forensic indicates blunt force trauma. Motive unclear.",
            "Victim succumbed to injuries at hospital after a violent assault. Investigation ongoing.",
        ],
        "motives": ["Personal enmity", "Financial dispute", "Land dispute", "Honour-related", "Unknown"],
        "modus":   ["Surprise attack at residence", "Drive-by attack", "Ambush in isolated area", "Targeted hit"],
    },
    "ROBBERY": {
        "ppc":     "PPC 392, 397",
        "weapons": ["PISTOL", "KNIFE", "RIFLE", "UNKNOWN"],
        "titles": [
            "Armed Robbery – {area} branch",
            "Daylight Robbery – {area}",
            "Gunpoint Robbery – {area}",
        ],
        "descriptions": [
            "Three armed individuals robbed the premises in broad daylight. Cash and valuables taken. No injuries reported.",
            "Suspects entered the shop brandishing weapons. Held employees at gunpoint while emptying the till.",
            "Robbery at peak hour. Suspects fled on motorcycles. CCTV footage available.",
        ],
        "motives": ["Financial Gain"],
        "modus":   ["Coordinated multi-person robbery", "Smash-and-grab", "Inside-information assisted robbery"],
    },
    "THEFT": {
        "ppc":     "PPC 379, 380",
        "weapons": ["NONE", "UNKNOWN"],
        "titles": [
            "House Theft – {area}",
            "Vehicle Theft – {area}",
            "Burglary – {area}",
            "Snatching incident – {area}",
        ],
        "descriptions": [
            "Premises broken into overnight. Cash, jewellery, and electronics reported missing.",
            "Vehicle stolen from parking. CCTV captured partial registration of getaway vehicle.",
            "Burglary while occupants were away. Suspect known to scout the area.",
            "Mobile phone and wallet snatched at gunpoint while victim was at a traffic signal.",
        ],
        "motives": ["Financial Gain"],
        "modus":   ["Late-night break-in", "Lock-pick technique", "Opportunistic snatching"],
    },
    "KIDNAPPING": {
        "ppc":     "PPC 364-A, 365",
        "weapons": ["PISTOL", "NONE", "UNKNOWN"],
        "titles": [
            "Kidnapping – {victim}, {area}",
            "Abduction – {area} sector",
            "Forced Abduction – {area}",
        ],
        "descriptions": [
            "Victim abducted from a public location by unknown individuals. Family has not yet received ransom demand.",
            "Forced abduction in broad daylight. Suspect drove a sedan. Witnesses report multiple persons involved.",
            "Victim kidnapped from school pickup point. Investigation ongoing under PPC 364-A.",
        ],
        "motives": ["Ransom demand expected", "Personal dispute", "Unknown"],
        "modus":   ["Targeted abduction at predictable time", "Forced entry into vehicle", "Lure-and-grab"],
    },
    "FRAUD": {
        "ppc":     "PPC 420, PECA 2016 §3, §4",
        "weapons": ["NONE"],
        "titles": [
            "Cybercrime – Online Banking Fraud",
            "Investment Fraud – {area}",
            "Identity Theft Case",
            "Phishing Attack on {area} Business",
        ],
        "descriptions": [
            "Complainant defrauded of significant sum via online banking phishing scheme. Funds transferred to multiple accounts.",
            "Suspect ran a fraudulent investment scheme over several months. Multiple victims have come forward.",
            "Identity theft used to open credit lines in victim's name. Credit report flagged irregular activity.",
            "Phishing attack compromised business email account. Wire transfer redirected to attacker's account.",
        ],
        "motives": ["Financial Gain"],
        "modus":   ["Phishing emails", "Social engineering", "Spoofed website", "SIM-swap attack"],
    },
    "ASSAULT": {
        "ppc":     "PPC 324, 337",
        "weapons": ["KNIFE", "BLUNT", "PHYSICAL", "UNKNOWN"],
        "titles": [
            "Aggravated Assault – {area}",
            "Physical Assault – {area}",
            "Attempted Murder – {area}",
        ],
        "descriptions": [
            "Victim sustained injuries after being attacked by unknown individuals. Hospitalized in critical condition.",
            "Group assault at public location. Multiple injured. Surveillance footage available.",
            "Attempted murder via knife attack. Victim survived after emergency surgery.",
        ],
        "motives": ["Personal enmity", "Argument escalation", "Honour-related"],
        "modus":   ["Surprise attack", "Group attack", "Ambush"],
    },
    "DRUG": {
        "ppc":     "Control of Narcotics Substances Act §9-C",
        "weapons": ["NONE", "PISTOL"],
        "titles": [
            "Narcotics Recovery – {area}",
            "Drug Trafficking – {area}",
            "Heroin Smuggling Case",
            "Hashish Recovery – {area}",
        ],
        "descriptions": [
            "Anti-narcotics force recovered a significant quantity of contraband during a raid. Suspects arrested at the scene.",
            "Vehicle stopped at checkpoint. Concealed drugs recovered from modified compartments.",
            "Tip-off led to arrest of suspected dealer. Drugs and unaccounted cash recovered.",
        ],
        "motives": ["Financial Gain"],
        "modus":   ["Vehicle-based smuggling", "Concealed transport", "Local distribution network"],
    },
    "RAPE": {
        "ppc":     "PPC 376",
        "weapons": ["PHYSICAL", "NONE", "UNKNOWN"],
        "titles": [
            "Sexual Assault – {area}",
            "Assault Case – Investigation Active",
        ],
        "descriptions": [
            "Survivor reported the incident at the local police station. Medical examination and counselling provided. Investigation handled with strict confidentiality.",
            "Case registered following formal complaint. Survivor support services engaged. All proceedings confidential.",
        ],
        "motives": ["Personal", "Power-based"],
        "modus":   ["Confidential — limited disclosure"],
    },
    "EXTORTION": {
        "ppc":     "PPC 386, 387",
        "weapons": ["NONE", "UNKNOWN"],
        "titles": [
            "Extortion – {area} Business",
            "Threatening Calls – {area}",
            "Bhatta Demand – {area}",
        ],
        "descriptions": [
            "Business owner received repeated threatening calls demanding protection money. Refused to comply, then property vandalized.",
            "Anonymous calls demanding payment. Caller threatened harm to family members if demand not met.",
            "Extortion attempt via WhatsApp. Suspect demanded weekly payments under threat of violence.",
        ],
        "motives": ["Financial Gain", "Organised crime"],
        "modus":   ["Threatening phone calls", "WhatsApp threats", "In-person intimidation"],
    },
    "MISSING": {
        "ppc":     "PPC 365 (where applicable)",
        "weapons": ["NONE"],
        "titles": [
            "Missing Person Report – {victim}",
            "Disappearance – {area}",
        ],
        "descriptions": [
            "Family reported the person missing after they did not return home. Last seen leaving for work in the morning.",
            "Adult reported missing under suspicious circumstances. Last known location traced through mobile tower data.",
            "Search ongoing. Photo and description circulated to all area police stations.",
        ],
        "motives": ["Unknown"],
        "modus":   ["N/A — investigation ongoing"],
    },
    "TERRORISM": {
        "ppc":     "Anti-Terrorism Act 1997 §7",
        "weapons": ["EXPLOSIVE", "RIFLE", "PISTOL", "UNKNOWN"],
        "titles": [
            "Bomb Blast – {area}",
            "Targeted Killing – {area}",
            "IED Recovery – {area}",
        ],
        "descriptions": [
            "Explosion at a public location. Multiple casualties. CTD has taken over the investigation.",
            "Targeted killing of a public official. Drive-by attack by motorcycle-borne assailants.",
            "Suspicious package recovered. Bomb disposal squad neutralized the device. Investigation ongoing.",
        ],
        "motives": ["Ideological", "Sectarian", "Anti-state"],
        "modus":   ["Improvised explosive device", "Suicide attack", "Drive-by targeted hit"],
    },
    "SMUGGLING": {
        "ppc":     "Customs Act 1969 §156, §157",
        "weapons": ["NONE", "PISTOL"],
        "titles": [
            "Smuggling Operation – {area}",
            "Border Smuggling – {area}",
            "Contraband Seizure – {area}",
        ],
        "descriptions": [
            "Smuggled goods recovered from concealed compartments in vehicle. Driver detained for questioning.",
            "Tip-off led to interception of contraband shipment at checkpoint. Goods seized and inventoried.",
            "Cross-border smuggling network uncovered. Multiple suspects under investigation.",
        ],
        "motives": ["Financial Gain"],
        "modus":   ["Vehicle concealment", "Border crossing at night", "Bribery of checkpoints"],
    },
    "ARSON": {
        "ppc":     "PPC 435, 436",
        "weapons": ["CHEMICAL", "NONE", "UNKNOWN"],
        "titles": [
            "Arson – {area}",
            "Suspicious Fire – {area}",
            "Property Set Ablaze – {area}",
        ],
        "descriptions": [
            "Fire damage to commercial property. Accelerant residue detected. Investigation underway.",
            "Suspicious fire at residence. Origin point identified in storage room. Forensic team examining.",
            "Vehicle set on fire overnight. CCTV captured silhouette of suspect with jerry can.",
        ],
        "motives": ["Personal enmity", "Insurance fraud", "Intimidation"],
        "modus":   ["Petrol-based accelerant", "Delayed-ignition device", "Direct ignition"],
    },
    "DOMESTIC": {
        "ppc":     "PPC 337-A, Domestic Violence Act",
        "weapons": ["PHYSICAL", "BLUNT", "NONE"],
        "titles": [
            "Domestic Violence – {area}",
            "Family Dispute – {area}",
        ],
        "descriptions": [
            "Survivor reported pattern of domestic abuse. Medical examination conducted. Protection order requested.",
            "Family member injured during dispute. Both parties interviewed. Case under review.",
        ],
        "motives": ["Domestic"],
        "modus":   ["Repeated pattern", "Single incident escalation"],
    },
}

CASE_TYPE_CODES = list(CRIME_PROFILES.keys())

# Status distribution — weighted toward active investigations
STATUS_WEIGHTED = (
    ["UNDER_INVESTIGATION"] * 25 +
    ["ACTIVE"]              * 25 +
    ["OPEN"]                * 20 +
    ["PENDING"]             * 12 +
    ["CLOSED"]              * 10 +
    ["COLD_CASE"]           * 5  +
    ["ARCHIVED"]            * 3
)

SEVERITY_WEIGHTED = (
    ["CRITICAL"] * 25 + ["HIGH"] * 35 + ["MEDIUM"] * 30 + ["LOW"] * 10
)


# ════════════════════════════════════════════════════════════════════════════
# Case builder
# ════════════════════════════════════════════════════════════════════════════

def build_case(
    db, lk: L, seq_num: int,
    case_type_code: str,
    geo_tuple: tuple,
    investigator_user: User,
):
    """Build one fully-populated case. Returns the Case row."""

    prov, city_name, area, station, lat, lon = geo_tuple
    profile = CRIME_PROFILES[case_type_code]

    # ── External case IDs ──────────────────────────────────────────────────
    case_external_id = f"CASE-2026-{2100 + seq_num:04d}"
    fir_number       = f"FIR-2026-{1500 + seq_num:04d}"

    # ── Random core dates (over the past ~11 months) ──────────────────────
    today = date(2026, 5, 20)
    incident_offset = random.randint(7, 330)
    incident_dt = today - timedelta(days=incident_offset)
    reporting_dt = incident_dt + timedelta(days=random.randint(0, 3))
    incident_time = f"{random.randint(0, 23):02d}:{random.choice([0, 15, 30, 45]):02d}"
    reporting_time = f"{random.randint(7, 22):02d}:{random.choice([0, 15, 30]):02d}"

    # ── Victim demographics ────────────────────────────────────────────────
    if case_type_code == "FRAUD":
        victim_gender = random.choice(["Male", "Female"])
        victim_age    = random.randint(25, 65)
    elif case_type_code == "RAPE":
        victim_gender = "Female"
        victim_age    = random.randint(16, 40)
    elif case_type_code == "KIDNAPPING":
        victim_gender = random.choice(["Male", "Female"])
        victim_age    = random.randint(8, 50)
    elif case_type_code == "DOMESTIC":
        victim_gender = random.choice(["Female", "Female", "Male"])
        victim_age    = random.randint(20, 55)
    else:
        victim_gender = random.choice(["Male", "Male", "Female"])
        victim_age    = random.randint(18, 70)

    victim_name = _name(victim_gender)
    victim = upsert_person(
        db,
        cnic=_cnic(),
        full_name=victim_name,
        gender=victim_gender, age=victim_age,
        contact=_phone(),
        address=f"{area}, {city_name}",
        occupation=random.choice([
            "Shopkeeper", "Teacher", "Bank Employee", "Driver", "Engineer",
            "Doctor", "Student", "Businessman", "Government Employee", "Labourer",
            "Housewife", "Retired", "Lawyer", "IT Professional",
        ]),
    )

    # ── Primary suspect ────────────────────────────────────────────────────
    is_suspect_known = random.random() > 0.25
    if is_suspect_known:
        sus_gender = random.choice(["Male", "Male", "Male", "Female"])
        suspect = upsert_person(
            db,
            cnic=_cnic(),
            full_name=_name(sus_gender),
            gender=sus_gender, age=random.randint(19, 55),
            contact=_phone(),
            address=f"{random.choice(['Old', 'New', 'Block A', 'Block B'])} {area}, {city_name}",
            occupation=random.choice([
                "Unemployed", "Driver", "Labourer", "Mechanic", "Shopkeeper",
                "Unknown", "Construction Worker", "Daily Wager",
            ]),
            physical_description=f"{random.choice(['Medium', 'Tall', 'Short'])} build, "
                                 f"{random.choice(['fair', 'wheatish', 'dark'])} complexion.",
        )
    else:
        suspect = upsert_person(
            db,
            is_unknown=True,
            full_name=f"Unknown Suspect (Case {case_external_id})",
            gender=random.choice(["Male", "Female"]),
            physical_description=f"Approximately {random.randint(160, 185)}cm tall. Face concealed during incident.",
        )

    # ── Case-level fields ──────────────────────────────────────────────────
    title  = random.choice(profile["titles"]).format(
        victim=victim_name, area=area, city=city_name,
    )
    desc   = random.choice(profile["descriptions"])
    motive = random.choice(profile["motives"])
    modus  = random.choice(profile["modus"])
    weapon_code = random.choice(profile["weapons"])

    status_code   = random.choice(STATUS_WEIGHTED)
    severity_code = random.choice(SEVERITY_WEIGHTED)

    cctv     = random.random() > 0.4
    progress = random.choice([10, 15, 20, 25, 35, 45, 55, 65, 75, 85])
    vehicle  = random.choice([
        None, None, "White Toyota Corolla", "Black Honda Civic",
        "Silver Suzuki Mehran", "Red Suzuki Cultus", "Motorcycle (Honda CD-70)",
        "Blue Suzuki Alto", "White Suzuki Bolan", "Black Toyota Vigo",
    ])

    # Weapon FK — NONE is a real lookup row, so we resolve it too
    try:
        weapon_id = lk.weapon(weapon_code)
    except RuntimeError:
        weapon_id = None

    case = Case(
        case_id          = case_external_id,
        fir_number       = fir_number,
        fir_language     = random.choice(["English", "Urdu", "Bilingual"]),
        fir_text_clean   = f"{desc} Incident reported at {station}, {city_name}.",
        case_type_id     = lk.case_type(case_type_code),
        case_status_id   = lk.case_status(status_code),
        priority_id      = lk.severity(severity_code),
        ppc_sections     = profile["ppc"],
        case_title       = title[:255],
        description      = desc,
        incident_date    = incident_dt,
        incident_time    = incident_time,
        reporting_date   = reporting_dt,
        reporting_time   = reporting_time,
        reporting_officer= f"DSP {random.choice(MALE_FIRST)} {random.choice(LAST_NAMES)}",
        assigned_investigator_id = investigator_user.investigator.id,
        created_by_id    = investigator_user.id,
        complainant_name = victim_name if case_type_code != "MURDER"
                            else f"{random.choice(FEMALE_FIRST)} {random.choice(LAST_NAMES)}",
        complainant_contact = _phone(),
        complainant_cnic = _cnic(),
        weapon_id        = weapon_id,
        weapon_description = None if weapon_code == "NONE" else
            f"{weapon_code.replace('_', ' ').title()} recovered from / observed at scene.",
        vehicle_used     = vehicle,
        motive           = motive,
        modus_operandi   = modus,
        cctv_available   = cctv,
        crime_description= desc,
        progress_percent = progress,
        next_hearing_date= today + timedelta(days=random.randint(15, 90))
                            if status_code in ("UNDER_INVESTIGATION", "ACTIVE", "PENDING")
                            else None,
    )
    db.add(case); db.flush()

    # ── Crime-type subtype (Murder / SexualAssault / Theft details) ────────
    if case_type_code == "MURDER":
        db.add(MurderDetails(
            case_id_fk      = case.id,
            cause_of_death  = random.choice([
                "Multiple stab wounds", "Gunshot wound to chest",
                "Blunt force trauma to head", "Strangulation",
                "Multiple gunshot wounds", "Asphyxiation",
            ]),
            body_location   = random.choice([
                "At Scene of Crime", "Hospital Mortuary", "Recovered Off-site",
            ]),
            time_of_death   = f"{random.randint(18, 23)}:00 – {random.randint(0, 5):02d}:00",
            postmortem_done = random.choice([True, True, False]),
            forensic_done   = random.choice([True, False]),
        ))
    elif case_type_code == "RAPE":
        db.add(SexualAssaultDetails(
            case_id_fk         = case.id,
            medical_exam       = random.choice(["Conducted", "Pending", "Refused"]),
            victim_counseling  = True,
            protection_order   = random.choice([True, False]),
            confidential_notes = "All proceedings confidential. Survivor support engaged.",
        ))
    elif case_type_code in ("THEFT", "ROBBERY"):
        db.add(TheftDetails(
            case_id_fk      = case.id,
            stolen_items    = random.choice([
                "Cash, jewellery, mobile phones",
                "Cash and laptop",
                "Vehicle and personal belongings",
                "Cash from till and inventory",
                "Cash, gold ornaments, ATM cards",
            ]),
            stolen_value    = float(random.choice([
                50000, 150000, 300000, 750000, 1500000, 3000000, 5000000,
            ])),
            recovery_status = random.choice([
                "Not Recovered", "Partially Recovered", "Fully Recovered",
            ]),
            entry_point     = random.choice([
                "Main Door", "Back Door", "Window", "Roof Access",
                "Unknown", "Forced Entry — Main Gate",
            ]),
        ))

    # ── Location ───────────────────────────────────────────────────────────
    db.add(Location(
        case_id_fk       = case.id,
        province_id      = lk.province(prov),
        city_id          = lk.city(city_name, prov),
        area             = area,
        police_station   = station,
        full_address     = f"{area}, {city_name}, {prov}",
        display_address  = f"{area}, {city_name}, Pakistan",
        latitude         = round(lat + random.uniform(-0.012, 0.012), 6),
        longitude        = round(lon + random.uniform(-0.012, 0.012), 6),
        crime_scene_type = random.choice([
            "Residential – Indoor", "Commercial – Indoor", "Public Space",
            "Roadside", "Residential – Outdoor", "Vehicle",
        ]),
        scene_access     = random.choice([
            "Released", "Forensic Team Present", "Restricted Access",
            "Secured by Police",
        ]),
        landmarks        = f"Near major landmark in {area}, {city_name}",
    ))

    # ── Primary suspect ────────────────────────────────────────────────────
    db.add(CaseSuspect(
        case_id_fk        = case.id,
        person_id         = suspect.id,
        suspect_id        = f"SUS-{2100 + seq_num:04d}-01",
        status_id         = lk.suspect_status(random.choice([
            "AT_LARGE", "DETAINED", "ARRESTED", "ABSCONDING", "SURVEILLANCE",
        ])),
        relation_to_case  = "Unknown" if not is_suspect_known else random.choice([
            "Stranger", "Acquaintance", "Neighbour", "Former colleague",
            "Family acquaintance",
        ]),
        reason            = "Identification based on witness account, CCTV review, "
                            "and forensic linkage.",
        alibi             = random.choice([
            "Claims to have been at home during the incident.",
            "Provided alibi at a public location — under verification.",
            "No alibi provided.",
            None,
        ]),
        criminal_record   = random.choice([True, False, False]),
        arrested          = random.random() < 0.4,
        notes             = "Investigation ongoing. Further questioning scheduled.",
    ))

    # Occasional second suspect (~25% of cases)
    if random.random() < 0.25:
        second_suspect = upsert_person(
            db,
            is_unknown=True,
            full_name=f"Unknown Accomplice (Case {case_external_id})",
            gender=random.choice(["Male", "Female"]),
            physical_description="Identity unknown. Description circulated.",
        )
        db.add(CaseSuspect(
            case_id_fk       = case.id,
            person_id        = second_suspect.id,
            suspect_id       = f"SUS-{2100 + seq_num:04d}-02",
            status_id        = lk.suspect_status("AT_LARGE"),
            relation_to_case = "Stranger",
            reason           = "Second figure observed at scene. Identity pending.",
            notes            = "Search ongoing. Description shared with area stations.",
        ))

    # ── Victim record ──────────────────────────────────────────────────────
    if case_type_code == "MURDER":
        v_status, v_label, injury_type = "DECEASED", "Primary Victim — Murder", "Fatal (Death)"
    elif case_type_code == "KIDNAPPING":
        v_status, v_label, injury_type = "MISSING", "Abducted — Active Search", "Psychological Trauma"
    elif case_type_code == "MISSING":
        v_status, v_label, injury_type = "MISSING", "Missing Person — Active Search", "Unknown"
    elif case_type_code == "ASSAULT":
        choice = random.choice([
            ("CRITICAL",     "Primary Victim — Critical",     "Severe / Critical"),
            ("HOSPITALIZED", "Primary Victim — Hospitalized", "Moderate"),
            ("ALIVE",        "Primary Victim — Recovering",   "Minor"),
        ])
        v_status, v_label, injury_type = choice
    elif case_type_code == "TERRORISM":
        choice = random.choice([
            ("DECEASED",     "Casualty — Deceased",           "Fatal (Death)"),
            ("CRITICAL",     "Casualty — Critical",           "Severe / Critical"),
            ("HOSPITALIZED", "Casualty — Hospitalized",       "Moderate"),
        ])
        v_status, v_label, injury_type = choice
    else:
        v_status, v_label = "ALIVE", "Primary Victim"
        injury_type       = random.choice([
            "Psychological Trauma", "Minor", "No Physical Injury",
        ])

    db.add(CaseVictim(
        case_id_fk         = case.id,
        person_id          = victim.id,
        victim_id          = f"VIC-{2100 + seq_num:04d}-01",
        status_id          = lk.victim_status(v_status),
        primary_label      = v_label,
        injury_type        = injury_type,
        cooperative        = random.choice([True, True, True, False]),
        statement          = None if v_status in ("DECEASED", "MISSING") else
                              "Statement recorded and on file.",
        medical_report     = case_type_code in ("MURDER", "ASSAULT", "RAPE", "TERRORISM"),
        postmortem         = case_type_code == "MURDER",
        threat_level_id    = lk.severity(severity_code),
    ))

    # ── Witnesses (0–3 per case) ───────────────────────────────────────────
    n_witnesses = random.choices([0, 1, 2, 3], weights=[10, 35, 35, 20])[0]
    for w_seq in range(n_witnesses):
        w_gender = random.choice(["Male", "Female"])
        witness_person = upsert_person(
            db,
            cnic=_cnic(),
            full_name=_name(w_gender),
            gender=w_gender, age=random.randint(20, 65),
            contact=_phone(),
            address=f"{area}, {city_name}",
            occupation=random.choice(["Resident", "Shopkeeper", "Security Guard", "Driver"]),
        )
        db.add(CaseWitness(
            case_id_fk            = case.id,
            person_id             = witness_person.id,
            witness_id            = f"WIT-{2100 + seq_num:04d}-{w_seq + 1:02d}",
            witness_type_id       = lk.witness_type(random.choice([
                "EYEWITNESS", "EARWITNESS", "CHARACTER", "EXPERT",
            ])),
            relation_to_case      = random.choice([
                "Neighbour", "Bystander", "Shopkeeper", "Security", "Passer-by",
            ]),
            credibility_id        = lk.witness_cred(random.choice(["HIGH", "MEDIUM", "LOW"])),
            status                = "Active",
            statement_date        = reporting_dt + timedelta(days=random.randint(1, 5)),
            statement_recorded_by = f"Insp. {investigator_user.username.title()}",
            description           = random.choice([
                "Witness saw the suspect leaving the scene in a hurry.",
                "Heard commotion and looked outside; saw the incident unfold.",
                "Was at a nearby shop and observed the suspect approach the location.",
                "Provided general background on the victim's daily routine.",
            ]),
            cooperating           = True,
        ))

    # ── Evidence (2–5 items per case) ──────────────────────────────────────
    evidence_pool = ["PHOTO", "STATEMENT", "FORENSIC", "CCTV", "DNA",
                     "FINGERPRINT", "WEAPON", "DOCUMENT", "PHYSICAL", "DIGITAL",
                     "MEDICAL"]
    n_evidence = random.randint(2, 5)
    chosen_types = random.sample(evidence_pool, k=min(n_evidence, len(evidence_pool)))

    ev_descs = {
        "PHOTO":      "Crime scene photographs (wide and close-up).",
        "STATEMENT":  "Witness / complainant statement (recorded).",
        "FORENSIC":   "Forensic report from district laboratory.",
        "CCTV":       "CCTV footage from neighbouring premises.",
        "DNA":        "DNA sample collected at scene. Sent for typing.",
        "FINGERPRINT":"Partial fingerprint lifted from scene.",
        "WEAPON":     "Weapon recovered from scene with chain-of-custody log.",
        "DOCUMENT":   "Document seized as evidence.",
        "PHYSICAL":   "Physical object collected as evidence.",
        "DIGITAL":    "Digital evidence (logs, files, screenshots).",
        "MEDICAL":    "Medical examination report.",
    }

    for ev_seq, etype in enumerate(chosen_types, start=1):
        try:
            etype_id = lk.evidence_type(etype)
        except RuntimeError:
            continue
        db.add(Evidence(
            case_id_fk     = case.id,
            evidence_id    = f"EV-{2100 + seq_num:04d}-{ev_seq:02d}",
            type_id        = etype_id,
            description    = ev_descs.get(etype, "Evidence collected during investigation."),
            file_path      = f"evidence/{case_external_id}/{etype.lower()}-{ev_seq}.bin",
            file_name      = f"{etype.lower()}-{ev_seq}.bin",
            file_mime      = "application/octet-stream",
            file_size      = random.randint(102400, 5242880),
            sha256_hash    = "0" * 64,
            date_collected = reporting_dt + timedelta(days=random.randint(0, 7)),
            collected_by   = random.choice([
                f"Insp. {investigator_user.username.title()}",
                "Forensic Team A", "Forensic Team B", "Local Constabulary",
            ]),
        ))

    # ── Extracted entities (3–6 per case, varying confidence) ──────────────
    entity_specs = [
        ("PERSON",   victim_name,            random.uniform(0.85, 0.99), victim.id),
        ("LOCATION", f"{area}, {city_name}", random.uniform(0.88, 0.99), None),
        ("LOCATION", city_name,              0.99, None),
        ("DATE",     incident_dt.strftime("%d %B %Y"), 0.99, None),
    ]
    if is_suspect_known and suspect.full_name and "Unknown" not in suspect.full_name:
        entity_specs.append(
            ("PERSON", suspect.full_name, random.uniform(0.65, 0.93), suspect.id)
        )
    if weapon_code and weapon_code != "NONE":
        entity_specs.append(
            ("WEAPON", weapon_code.replace("_", " ").title(),
             random.uniform(0.75, 0.92), None)
        )
    if vehicle:
        entity_specs.append(
            ("VEHICLE", vehicle, random.uniform(0.78, 0.90), None)
        )

    for et, val, conf, pid in entity_specs:
        db.add(ExtractedEntity(
            case_id_fk         = case.id,
            entity_type        = et,
            entity_value       = val,
            confidence         = round(conf, 2),
            flagged_for_review = conf < 0.70,
            verified_by_user   = conf >= 0.90,
            matched_person_id  = pid,
        ))

    # ── Completeness report ────────────────────────────────────────────────
    score = random.randint(40, 95)
    cr = CompletenessReport(
        case_id_fk  = case.id,
        score       = score,
        last_run_at = datetime.now(UTC),
    )
    db.add(cr); db.flush()

    if score < 75:
        for field in random.sample([
            "Vehicle registration", "Suspect identification",
            "Witness contact details", "Forensic report submission",
            "CCTV timestamp verification", "Motive confirmation",
        ], k=random.randint(1, 3)):
            db.add(CompletenessMissingField(
                report_id   = cr.id,
                field_name  = field,
                severity_id = lk.severity(random.choice(["LOW", "MEDIUM", "HIGH"])),
            ))

    # ── Timeline events ────────────────────────────────────────────────────
    base_timeline = [
        ("FIR_FILED",       f"FIR Filed: {fir_number}",
         f"FIR registered at {station} under {profile['ppc']}.",
         "CRITICAL", reporting_dt, reporting_time),
        ("CASE_REGISTERED", f"Case Registered: {case_external_id}",
         f"Case created and assigned to {investigator_user.username}.",
         "NORMAL", reporting_dt, "09:00"),
        ("EVIDENCE_ADDED",  "Initial evidence added",
         "Crime scene photos and statements logged.",
         "NORMAL", reporting_dt + timedelta(days=1), "11:00"),
    ]
    extra_timeline = random.sample([
        ("FIELD_VISIT",        "Field visit to scene",
         "Officer visited crime scene for follow-up examination.", "NORMAL"),
        ("WITNESS_INTERVIEW",  "Witness interviewed",
         "Recorded statement from key witness.", "NORMAL"),
        ("SUSPECT_INTERVIEW",  "Suspect questioning",
         "Suspect interviewed at police station.", "HIGH"),
        ("FORENSIC_VISIT",     "Forensic team visit",
         "Forensic team collected additional samples.", "HIGH"),
        ("SURVEILLANCE",       "Surveillance operation",
         "Plain-clothes team observed suspect's known address.", "HIGH"),
    ], k=random.randint(1, 3))

    seq_ev = 1
    for ev_type, title, desc_t, sev, ev_date, ev_time in base_timeline:
        db.add(TimelineEvent(
            case_id_fk     = case.id,
            event_id       = f"EV-{2100 + seq_num:04d}-T{seq_ev:02d}",
            event_source   = "SYSTEM" if ev_type in ("FIR_FILED", "CASE_REGISTERED") else "MANUAL",
            event_type_id  = lk.timeline_event_type(ev_type),
            title          = title,
            description    = desc_t,
            officer_name   = case.reporting_officer,
            severity_id    = lk.severity(sev),
            event_date     = ev_date,
            event_time     = ev_time,
            editable       = ev_type not in ("FIR_FILED", "CASE_REGISTERED"),
        ))
        seq_ev += 1

    for ev_type, title, desc_t, sev in extra_timeline:
        ev_date = reporting_dt + timedelta(days=random.randint(2, 30))
        ev_time = f"{random.randint(9, 17):02d}:{random.choice([0, 15, 30, 45]):02d}"
        db.add(TimelineEvent(
            case_id_fk     = case.id,
            event_id       = f"EV-{2100 + seq_num:04d}-T{seq_ev:02d}",
            event_source   = "MANUAL",
            event_type_id  = lk.timeline_event_type(ev_type),
            title          = title,
            description    = desc_t,
            officer_name   = f"Insp. {investigator_user.username.title()}",
            severity_id    = lk.severity(sev),
            event_date     = ev_date,
            event_time     = ev_time,
            editable       = True,
        ))
        seq_ev += 1

    # ── Investigation notes (~60% of cases get 1–2 notes) ──────────────────
    if random.random() < 0.6:
        n_notes = random.choices([1, 2], weights=[70, 30])[0]
        for n_seq in range(n_notes):
            cat = random.choice([
                "FIELD_NOTE", "INTELLIGENCE", "HYPOTHESIS", "ACTION_ITEM",
            ])
            try:
                cat_id = lk.note_category(cat)
            except RuntimeError:
                continue
            db.add(InvestigationNote(
                note_id     = f"NOTE-{2100 + seq_num:04d}-{n_seq + 1:02d}",
                case_id_fk  = case.id,
                user_id     = investigator_user.id,
                category_id = cat_id,
                officer_name= f"Insp. {investigator_user.username.title()}",
                note_date   = reporting_dt + timedelta(days=random.randint(2, 20)),
                title       = random.choice([
                    "Initial site impressions", "Follow-up actions",
                    "Working hypothesis", "Pending verification items",
                    "Witness re-interview plan",
                ]),
                detail      = "Detailed field observations and next-step planning. "
                              "Investigation ongoing.",
            ))

    return case


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def seed_dummy_cases():
    print("\n=== Seeding 100 dummy cases ===\n")
    print(f"  Excluded investigator (no cases): {EMPTY_INVESTIGATOR_USERNAME}")
    print(f"  Active investigators ({len(ACTIVE_INVESTIGATOR_USERNAMES)}): "
          f"{', '.join(ACTIVE_INVESTIGATOR_USERNAMES)}\n")

    with session_scope() as db:
        # Idempotency check — look for our id range
        existing = (
            db.query(Case)
              .filter(Case.case_id.like("CASE-2026-21%"))
              .count()
        )
        if existing > 0:
            print(f"  Dummy cases already exist ({existing} found). Skipping.")
            print("  (To re-seed: DELETE FROM cases WHERE case_id LIKE 'CASE-2026-21%';)\n")
            return

        lk = L(db)

        # Resolve investigators by username
        investigators = (
            db.query(User)
              .filter(User.username.in_(ACTIVE_INVESTIGATOR_USERNAMES))
              .all()
        )
        # Verify each one has an Investigator profile (else FK fails)
        valid_investigators = [u for u in investigators if u.investigator]
        missing = set(ACTIVE_INVESTIGATOR_USERNAMES) - {u.username for u in valid_investigators}
        if missing:
            print(f"  ! Warning: investigator users not found / missing profile: {missing}")
        if not valid_investigators:
            raise RuntimeError(
                "No active investigator users with Investigator profile found. "
                "Run seed_users.py + the previous seeder first."
            )

        # Confirm the excluded investigator actually exists (so the test is real)
        excluded = (
            db.query(User)
              .filter_by(username=EMPTY_INVESTIGATOR_USERNAME)
              .first()
        )
        if excluded:
            print(f"  ✓ {EMPTY_INVESTIGATOR_USERNAME} exists and will receive 0 cases.\n")
        else:
            print(f"  ⚠ {EMPTY_INVESTIGATOR_USERNAME} not found — empty-dashboard test won't apply.\n")

        # ── Triple-shuffle so geo, type, and investigator vary independently ──
        # Each cycle is shuffled once at the start; iteration index then walks
        # each list at its own period — preventing "investigator X always gets
        # crime type Y in city Z" patterns.
        geo_order  = list(range(len(GEO)))
        type_order = list(range(len(CASE_TYPE_CODES)))
        inv_order  = list(range(len(valid_investigators)))
        random.shuffle(geo_order)
        random.shuffle(type_order)
        random.shuffle(inv_order)

        prov_count: dict[str, int] = {}
        type_count: dict[str, int] = {}
        inv_count : dict[str, int] = {}

        for i in range(1, 101):
            geo   = GEO[geo_order[(i - 1) % len(geo_order)]]
            ctype = CASE_TYPE_CODES[type_order[(i - 1) % len(type_order)]]
            inv   = valid_investigators[inv_order[(i - 1) % len(inv_order)]]

            build_case(
                db, lk, seq_num=i,
                case_type_code=ctype,
                geo_tuple=geo,
                investigator_user=inv,
            )
            db.flush()

            prov_count[geo[0]]      = prov_count.get(geo[0], 0)      + 1
            type_count[ctype]       = type_count.get(ctype, 0)       + 1
            inv_count[inv.username] = inv_count.get(inv.username, 0) + 1

            if i % 10 == 0:
                print(f"  ... {i:3d}/100 cases inserted")

        # ── Distribution summary ──────────────────────────────────────────
        print("\n  ── Distribution summary ──")
        print("  By province:")
        for p, c in sorted(prov_count.items(), key=lambda x: -x[1]):
            print(f"    {p:14s} {c:3d}")
        print("  By crime type:")
        for t, c in sorted(type_count.items(), key=lambda x: -x[1]):
            print(f"    {t:14s} {c:3d}")
        print("  By investigator:")
        for u, c in sorted(inv_count.items(), key=lambda x: -x[1]):
            print(f"    {u:20s} {c:3d}")
        print(f"    {EMPTY_INVESTIGATOR_USERNAME:20s}   0  (intentionally excluded)")

    print("\n✓ Seed complete. 100 dummy cases ready.\n")


if __name__ == "__main__":
    seed_dummy_cases()
