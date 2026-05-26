from db import session_scope, init_db
from models import (
    Province, City, CaseType, Weapon, EvidenceType, CaseStatus,
    SuspectStatus, VictimStatus, WitnessType, Severity,
    LeadType, LeadStatus, TimelineEventType, Permission,
    NoteCategory, WitnessCredibility,
    SystemSetting,
)


def upsert(db, model, rows, match_field="code"):
    """Insert each dict in `rows` if a row with the same match_field doesn't
    already exist. Returns (inserted, skipped) counts."""
    inserted = 0
    skipped  = 0
    for row in rows:
        existing = (
            db.query(model)
              .filter(getattr(model, match_field) == row[match_field])
              .first()
        )
        if existing:
            skipped += 1
            continue
        db.add(model(**row))
        inserted += 1
    return inserted, skipped


# ════════════════════════════════════════════════════════════════════════════
# 1. PROVINCES + CITIES
# ────────────────────────────────────────────────────────────────────────────
# Covers all 7 admin units of Pakistan + their major cities.
# Cities chosen based on:
#   • Population (top 3-5 per province)
#   • Frontend mock data (Karachi, Lahore, Islamabad, Peshawar, Rawalpindi
#     all appear in all-cases/page.jsx — must be present)
#   • Investigative relevance (border towns, port cities)
# ════════════════════════════════════════════════════════════════════════════

PROVINCES = [
    {"code": "PUNJAB",     "label": "Punjab",                       "sort_order": 1},
    {"code": "SINDH",      "label": "Sindh",                        "sort_order": 2},
    {"code": "KPK",        "label": "Khyber Pakhtunkhwa",           "sort_order": 3},
    {"code": "BALOCHISTAN","label": "Balochistan",                  "sort_order": 4},
    {"code": "ICT",        "label": "Islamabad Capital Territory",  "sort_order": 5},
    {"code": "GB",         "label": "Gilgit-Baltistan",             "sort_order": 6},
    {"code": "AJK",        "label": "Azad Jammu & Kashmir",         "sort_order": 7},
]

# Cities indexed by province code so we can resolve the FK after province
# rows are inserted.
CITIES = {
    "PUNJAB": [
        "Lahore", "Faisalabad", "Rawalpindi", "Multan", "Gujranwala",
        "Sialkot", "Bahawalpur", "Sargodha", "Sahiwal", "Jhang",
    ],
    "SINDH": [
        "Karachi", "Hyderabad", "Sukkur", "Larkana", "Mirpur Khas",
        "Nawabshah", "Thatta", "Badin",
    ],
    "KPK": [
        "Peshawar", "Mardan", "Abbottabad", "Mingora", "Kohat",
        "Bannu", "Dera Ismail Khan", "Hayatabad",
    ],
    "BALOCHISTAN": [
        "Quetta", "Gwadar", "Khuzdar", "Turbat", "Hub", "Chaman",
    ],
    "ICT": [
        "Islamabad",
    ],
    "GB": [
        "Gilgit", "Skardu", "Hunza", "Ghizer", "Diamer", "Astore",
    ],
    "AJK": [
        "Muzaffarabad", "Mirpur", "Kotli", "Rawalakot", "Bagh", "Poonch",
    ],
}


# ════════════════════════════════════════════════════════════════════════════
# 2. CASE TYPES (CASE_TYPES from caseFormConstants/page.jsx)
# ────────────────────────────────────────────────────────────────────────────
# `subtype_table` tells the service layer which subtype detail row to create
# when a case of this type is registered (Murder → MurderDetails, etc.).
# NULL means "no subtype, just save Case fields".
# ════════════════════════════════════════════════════════════════════════════

CASE_TYPES = [
    {"code": "MURDER",       "label": "Murder / Homicide",        "subtype_table": "murder_details",          "sort_order": 1},
    {"code": "RAPE",         "label": "Rape / Sexual Assault",    "subtype_table": "sexual_assault_details",  "sort_order": 2},
    {"code": "ROBBERY",      "label": "Robbery / Armed Robbery",  "subtype_table": "theft_details",           "sort_order": 3},
    {"code": "THEFT",        "label": "Theft / Burglary",         "subtype_table": "theft_details",           "sort_order": 4},
    {"code": "ASSAULT",      "label": "Assault / Battery",        "subtype_table": None,                      "sort_order": 5},
    {"code": "FRAUD",        "label": "Fraud / Cybercrime",       "subtype_table": None,                      "sort_order": 6},
    {"code": "KIDNAPPING",   "label": "Kidnapping / Abduction",   "subtype_table": None,                      "sort_order": 7},
    {"code": "DRUG",         "label": "Drug Trafficking",         "subtype_table": None,                      "sort_order": 8},
    {"code": "EXTORTION",    "label": "Extortion / Blackmail",    "subtype_table": None,                      "sort_order": 9},
    {"code": "MISSING",      "label": "Missing Person",           "subtype_table": None,                      "sort_order": 10},
    {"code": "TERRORISM",    "label": "Terrorism / Extremism",    "subtype_table": None,                      "sort_order": 11},
    {"code": "SMUGGLING",    "label": "Smuggling",                "subtype_table": None,                      "sort_order": 12},
    {"code": "ARSON",        "label": "Arson",                    "subtype_table": None,                      "sort_order": 13},
    {"code": "DOMESTIC",     "label": "Domestic Violence",        "subtype_table": None,                      "sort_order": 14},
    {"code": "OTHER",        "label": "Other",                    "subtype_table": None,                      "sort_order": 99},
]


# ════════════════════════════════════════════════════════════════════════════
# 3. WEAPONS (WEAPONS from caseFormConstants/page.jsx)
# ────────────────────────────────────────────────────────────────────────────
# Grouped by `category` so reports can roll up "all firearm-related cases"
# without each row needing to know its grouping.
# ════════════════════════════════════════════════════════════════════════════

WEAPONS = [
    {"code": "PISTOL",      "label": "Firearm (Pistol)",          "category": "Firearm",  "sort_order": 1},
    {"code": "RIFLE",       "label": "Firearm (Rifle)",           "category": "Firearm",  "sort_order": 2},
    {"code": "SHOTGUN",     "label": "Firearm (Shotgun)",         "category": "Firearm",  "sort_order": 3},
    {"code": "KNIFE",       "label": "Knife / Blade",             "category": "Bladed",   "sort_order": 4},
    {"code": "BLUNT",       "label": "Blunt Object",              "category": "Blunt",    "sort_order": 5},
    {"code": "VEHICLE",     "label": "Vehicle",                   "category": "Vehicle",  "sort_order": 6},
    {"code": "CHEMICAL",    "label": "Chemical / Poison",         "category": "Chemical", "sort_order": 7},
    {"code": "EXPLOSIVE",   "label": "Explosive Device",          "category": "Explosive","sort_order": 8},
    {"code": "PHYSICAL",    "label": "Hands / Feet (Physical)",   "category": "Physical", "sort_order": 9},
    {"code": "UNKNOWN",     "label": "Unknown",                   "category": "Other",    "sort_order": 10},
    {"code": "NONE",        "label": "None / Not Applicable",     "category": "Other",    "sort_order": 11},
]


# ════════════════════════════════════════════════════════════════════════════
# 4. EVIDENCE TYPES (EVIDENCE_TYPES from caseFormConstants/page.jsx)
# ────────────────────────────────────────────────────────────────────────────
# `is_digital` flag enables "show only digital evidence" filters and helps
# the chain-of-custody hashing decide whether SHA-256 is required.
# ════════════════════════════════════════════════════════════════════════════

EVIDENCE_TYPES = [
    {"code": "CCTV",        "label": "CCTV / Video Footage",   "is_digital": True,  "sort_order": 1},
    {"code": "PHOTO",       "label": "Photograph",             "is_digital": True,  "sort_order": 2},
    {"code": "FINGERPRINT", "label": "Fingerprint",            "is_digital": False, "sort_order": 3},
    {"code": "DNA",         "label": "DNA Sample",             "is_digital": False, "sort_order": 4},
    {"code": "WEAPON",      "label": "Weapon",                 "is_digital": False, "sort_order": 5},
    {"code": "DOCUMENT",    "label": "Document / Paper",       "is_digital": False, "sort_order": 6},
    {"code": "DIGITAL",     "label": "Digital Evidence",       "is_digital": True,  "sort_order": 7},
    {"code": "STATEMENT",   "label": "Witness Statement",      "is_digital": False, "sort_order": 8},
    {"code": "MEDICAL",     "label": "Medical Report",         "is_digital": False, "sort_order": 9},
    {"code": "FORENSIC",    "label": "Forensic Report",        "is_digital": False, "sort_order": 10},
    {"code": "PHYSICAL",    "label": "Physical Object",        "is_digital": False, "sort_order": 11},
    {"code": "OTHER",       "label": "Other",                  "is_digital": False, "sort_order": 99},
]


# ════════════════════════════════════════════════════════════════════════════
# 5. CASE STATUSES
# ────────────────────────────────────────────────────────────────────────────
# Combines the values from case-details/page.jsx ("Open", "Under Investigation",
# "Pending", "Closed", "Cold Case") with the all-cases/page.jsx tab keys
# ("Active", "Pending", "Closed"). "Active" is treated as an alias for the
# default working state. `is_terminal` tells reports/queries which statuses
# end the lifecycle.
# ════════════════════════════════════════════════════════════════════════════

CASE_STATUSES = [
    {"code": "OPEN",                "label": "Open",                "is_terminal": False, "sort_order": 1},
    {"code": "UNDER_INVESTIGATION", "label": "Under Investigation", "is_terminal": False, "sort_order": 2},
    {"code": "ACTIVE",              "label": "Active",              "is_terminal": False, "sort_order": 3},
    {"code": "PENDING",             "label": "Pending",             "is_terminal": False, "sort_order": 4},
    {"code": "CLOSED",              "label": "Closed",              "is_terminal": True,  "sort_order": 5},
    {"code": "COLD_CASE",           "label": "Cold Case",           "is_terminal": True,  "sort_order": 6},
    {"code": "ARCHIVED",            "label": "Archived",            "is_terminal": True,  "sort_order": 7},
]


# ════════════════════════════════════════════════════════════════════════════
# 6. SUSPECT STATUSES (SUSPECT_STATUSES from caseFormConstants/page.jsx)
# ════════════════════════════════════════════════════════════════════════════

SUSPECT_STATUSES = [
    {"code": "AT_LARGE",       "label": "At Large",            "sort_order": 1},
    {"code": "DETAINED",       "label": "Detained",            "sort_order": 2},
    {"code": "ARRESTED",       "label": "Arrested",            "sort_order": 3},
    {"code": "CHARGED",        "label": "Charged",             "sort_order": 4},
    {"code": "ABSCONDING",     "label": "Absconding",          "sort_order": 5},
    {"code": "SURVEILLANCE",   "label": "Under Surveillance",  "sort_order": 6},
    {"code": "RELEASED",       "label": "Released",            "sort_order": 7},
    {"code": "CLEARED",        "label": "Cleared",             "sort_order": 8},
]


# ════════════════════════════════════════════════════════════════════════════
# 7. VICTIM STATUSES (VICTIM_STATUSES)
# ════════════════════════════════════════════════════════════════════════════

VICTIM_STATUSES = [
    {"code": "ALIVE",        "label": "Alive",              "sort_order": 1},
    {"code": "DECEASED",     "label": "Deceased",           "sort_order": 2},
    {"code": "CRITICAL",     "label": "Critical Condition", "sort_order": 3},
    {"code": "HOSPITALIZED", "label": "Hospitalized",       "sort_order": 4},
    {"code": "MISSING",      "label": "Missing",            "sort_order": 5},
]


# ════════════════════════════════════════════════════════════════════════════
# 8. WITNESS TYPES (WITNESS_TYPES)
# ════════════════════════════════════════════════════════════════════════════

WITNESS_TYPES = [
    {"code": "EYEWITNESS",        "label": "Eyewitness",         "sort_order": 1},
    {"code": "EARWITNESS",        "label": "Earwitness",         "sort_order": 2},
    {"code": "CHARACTER",         "label": "Character Witness",  "sort_order": 3},
    {"code": "EXPERT",            "label": "Expert Witness",     "sort_order": 4},
    {"code": "VICTIM_WITNESS",    "label": "Victim-Witness",     "sort_order": 5},
    {"code": "HEARSAY",           "label": "Hearsay Witness",    "sort_order": 6},
    {"code": "OTHER",             "label": "Other",              "sort_order": 99},
]


# ════════════════════════════════════════════════════════════════════════════
# 9. WITNESS CREDIBILITY (WITNESS_CREDIBILITY — added in schema_additions)
# ════════════════════════════════════════════════════════════════════════════

WITNESS_CREDIBILITY = [
    {"code": "HIGH",     "label": "High — Corroborated",  "sort_order": 1},
    {"code": "MEDIUM",   "label": "Medium — Unverified",  "sort_order": 2},
    {"code": "LOW",      "label": "Low — Contradicted",   "sort_order": 3},
    {"code": "UNKNOWN",  "label": "Unknown",              "sort_order": 4},
]


# ════════════════════════════════════════════════════════════════════════════
# 10. SEVERITIES (SEVERITIES from caseEventConstants.js)
# ────────────────────────────────────────────────────────────────────────────
# `rank` enables threshold queries like "WHERE severity.rank >= 4"
# (i.e. High or Critical). `color_hex` matches the Tailwind palette used
# in the frontend so reports/exports stay visually consistent.
# ════════════════════════════════════════════════════════════════════════════

SEVERITIES = [
    {"code": "CRITICAL",        "label": "Critical",        "rank": 5, "color_hex": "#dc2626"},
    {"code": "HIGH",            "label": "High",            "rank": 4, "color_hex": "#ea580c"},
    {"code": "MEDIUM",          "label": "Medium",          "rank": 3, "color_hex": "#ca8a04"},
    {"code": "LOW",             "label": "Low",             "rank": 2, "color_hex": "#16a34a"},
    {"code": "NORMAL",          "label": "Normal",          "rank": 1, "color_hex": "#64748b"},
    {"code": "ADMINISTRATIVE",  "label": "Administrative",  "rank": 0, "color_hex": "#94a3b8"},
]


# ════════════════════════════════════════════════════════════════════════════
# 11. LEAD TYPES (LEAD_TYPES from caseEventConstants.js)
# ════════════════════════════════════════════════════════════════════════════

LEAD_TYPES = [
    {"code": "SUSPECT_MATCH",    "label": "Suspect Match",      "sort_order": 1},
    {"code": "SUSPECT_PATTERN",  "label": "Suspect Pattern",    "sort_order": 2},
    {"code": "LOCATION_PATTERN", "label": "Location Pattern",   "sort_order": 3},
    {"code": "CASE_LINKAGE",     "label": "Case Linkage",       "sort_order": 4},
    {"code": "CCTV_ANALYSIS",    "label": "CCTV Analysis",      "sort_order": 5},
    {"code": "FINGERPRINT",      "label": "Fingerprint Match",  "sort_order": 6},
    {"code": "DNA",              "label": "DNA Match",          "sort_order": 7},
    {"code": "PHONE_DIGITAL",    "label": "Phone / Digital",    "sort_order": 8},
    {"code": "FORENSIC",         "label": "Forensic Analysis",  "sort_order": 9},
    {"code": "WITNESS_LEAD",     "label": "Witness Lead",       "sort_order": 10},
    {"code": "INFORMANT",        "label": "Informant Tip",      "sort_order": 11},
    {"code": "VEHICLE",          "label": "Vehicle Match",      "sort_order": 12},
    {"code": "WEAPON",           "label": "Weapon Match",       "sort_order": 13},
    {"code": "OTHER",            "label": "Other",              "sort_order": 99},
]


# ════════════════════════════════════════════════════════════════════════════
# 12. LEAD STATUSES (LEAD_STATUSES from caseEventConstants.js)
# ════════════════════════════════════════════════════════════════════════════

LEAD_STATUSES = [
    {"code": "NEW",          "label": "New",          "sort_order": 1},
    {"code": "UNDER_REVIEW", "label": "Under Review", "sort_order": 2},
    {"code": "IN_PROGRESS",  "label": "In Progress",  "sort_order": 3},
    {"code": "ACTIONED",     "label": "Actioned",     "sort_order": 4},
    {"code": "DISMISSED",    "label": "Dismissed",    "sort_order": 5},
]


# ════════════════════════════════════════════════════════════════════════════
# 13. TIMELINE EVENT TYPES
# ────────────────────────────────────────────────────────────────────────────
# Combines SYSTEM_EVENT (auto-logged) + MANUAL_EVENT_TYPES (officer-entered)
# into one table. The `is_system` flag distinguishes them — used by the
# AddTimelineModal to filter the "Event Type" dropdown to manual-only.
# ════════════════════════════════════════════════════════════════════════════

TIMELINE_EVENT_TYPES = [
    # System-logged
    {"code": "CASE_REGISTERED",   "label": "Case Registered",            "is_system": True,  "sort_order": 1},
    {"code": "FIR_FILED",         "label": "FIR Filed",                  "is_system": True,  "sort_order": 2},
    {"code": "CASE_UPDATED",      "label": "Case Updated",               "is_system": True,  "sort_order": 3},
    {"code": "STATUS_CHANGED",    "label": "Status Changed",             "is_system": True,  "sort_order": 4},
    {"code": "VICTIM_ADDED",      "label": "Victim Added",               "is_system": True,  "sort_order": 5},
    {"code": "SUSPECT_ADDED",     "label": "Suspect Added",              "is_system": True,  "sort_order": 6},
    {"code": "WITNESS_ADDED",     "label": "Witness Statement Recorded", "is_system": True,  "sort_order": 7},
    {"code": "EVIDENCE_ADDED",    "label": "Evidence Added",             "is_system": True,  "sort_order": 8},
    {"code": "AI_LEAD_GENERATED", "label": "AI Lead Generated",          "is_system": True,  "sort_order": 9},
    {"code": "CASE_LINKED",       "label": "Linked Case Added",          "is_system": True,  "sort_order": 10},
    {"code": "REPORT_GENERATED",  "label": "Report Generated",           "is_system": True,  "sort_order": 11},
    {"code": "CASE_CLOSED",       "label": "Case Closed",                "is_system": True,  "sort_order": 12},
    {"code": "CASE_REOPENED",     "label": "Case Reopened",              "is_system": True,  "sort_order": 13},

    # Officer-entered (MANUAL_EVENT_TYPES)
    {"code": "FIELD_VISIT",         "label": "Field Visit",         "is_system": False, "sort_order": 20},
    {"code": "WITNESS_INTERVIEW",   "label": "Witness Interview",   "is_system": False, "sort_order": 21},
    {"code": "SUSPECT_INTERVIEW",   "label": "Suspect Interview",   "is_system": False, "sort_order": 22},
    {"code": "ARREST",              "label": "Arrest",              "is_system": False, "sort_order": 23},
    {"code": "EVIDENCE_COLLECTION", "label": "Evidence Collection", "is_system": False, "sort_order": 24},
    {"code": "COURT_HEARING",       "label": "Court Hearing",       "is_system": False, "sort_order": 25},
    {"code": "SURVEILLANCE",        "label": "Surveillance",        "is_system": False, "sort_order": 26},
    {"code": "INFORMANT_CONTACT",   "label": "Informant Contact",   "is_system": False, "sort_order": 27},
    {"code": "FORENSIC_VISIT",      "label": "Forensic Visit",      "is_system": False, "sort_order": 28},
    {"code": "STATUS_UPDATE",       "label": "Status Update",       "is_system": False, "sort_order": 29},
    {"code": "NOTE_OBSERVATION",    "label": "Note / Observation",  "is_system": False, "sort_order": 30},
    {"code": "OTHER_MANUAL",        "label": "Other",               "is_system": False, "sort_order": 99},
]


# ════════════════════════════════════════════════════════════════════════════
# 14. NOTE CATEGORIES (NOTE_CATEGORIES from caseEventConstants.js)
# ════════════════════════════════════════════════════════════════════════════

NOTE_CATEGORIES = [
    {"code": "GENERAL",      "label": "General Observation", "sort_order": 1},
    {"code": "FIELD_NOTE",   "label": "Field Note",          "sort_order": 2},
    {"code": "INTELLIGENCE", "label": "Intelligence",        "sort_order": 3},
    {"code": "FOLLOW_UP",    "label": "Follow-Up",           "sort_order": 4},
    {"code": "REMINDER",     "label": "Reminder",            "sort_order": 5},
    {"code": "HYPOTHESIS",   "label": "Hypothesis",          "sort_order": 6},
    {"code": "QUESTION",     "label": "Question for Team",   "sort_order": 7},
    {"code": "ACTION_ITEM",  "label": "Action Item",         "sort_order": 8},
    {"code": "OTHER",        "label": "Other",               "sort_order": 99},
]


# ════════════════════════════════════════════════════════════════════════════
# 15. PERMISSIONS (derived from UC1–UC10)
# ────────────────────────────────────────────────────────────────────────────
# Codes follow the convention "<resource>.<action>". The UI's UserRole
# table picks rows from here for each role:
#   admin         → all permissions
#   investigator  → case.*, lead.*, report.generate, person.read,
#                   evidence.read, evidence.create
#   (read-only)   → *.read only
# Seeding the lookup doesn't grant anything — admins assign these to roles
# via the User Management UI later.
# ════════════════════════════════════════════════════════════════════════════

PERMISSIONS = [
    # Authentication
    {"code": "auth.login",            "label": "Log in",                       "module": "Authentication",   "description": "Access the system"},
    {"code": "auth.password.change",  "label": "Change own password",          "module": "Authentication",   "description": ""},

    # User management
    {"code": "user.create",           "label": "Create user accounts",         "module": "User Management",  "description": ""},
    {"code": "user.read",             "label": "View user accounts",           "module": "User Management",  "description": ""},
    {"code": "user.update",           "label": "Update user accounts",         "module": "User Management",  "description": ""},
    {"code": "user.deactivate",       "label": "Deactivate user accounts",     "module": "User Management",  "description": ""},
    {"code": "user.delete",           "label": "Delete user accounts",         "module": "User Management",  "description": ""},
    {"code": "user.password.reset",   "label": "Reset any user's password",    "module": "User Management",  "description": ""},
    {"code": "user.role.assign",      "label": "Assign / change user roles",   "module": "User Management",  "description": ""},

    # Case management
    {"code": "case.create",           "label": "Register a new case",          "module": "Case Management",  "description": "UC1"},
    {"code": "case.read",             "label": "View any case",                "module": "Case Management",  "description": "UC2"},
    {"code": "case.update",           "label": "Update an existing case",      "module": "Case Management",  "description": "UC4"},
    {"code": "case.delete",           "label": "Delete a case",                "module": "Case Management",  "description": "Admin only"},
    {"code": "case.assign",           "label": "Assign investigator to case",  "module": "Case Management",  "description": ""},
    {"code": "case.status.change",    "label": "Change case status",           "module": "Case Management",  "description": ""},
    {"code": "case.link",             "label": "Link/unlink cases",            "module": "Case Management",  "description": ""},

    # Person/Suspect/Victim/Witness
    {"code": "person.create",         "label": "Create person record",         "module": "Persons",          "description": ""},
    {"code": "person.read",           "label": "View person record",           "module": "Persons",          "description": ""},
    {"code": "person.update",         "label": "Update person record",         "module": "Persons",          "description": ""},

    # Evidence
    {"code": "evidence.create",       "label": "Add evidence",                 "module": "Evidence",         "description": ""},
    {"code": "evidence.read",         "label": "View evidence",                "module": "Evidence",         "description": ""},
    {"code": "evidence.update",       "label": "Update evidence",              "module": "Evidence",         "description": ""},
    {"code": "evidence.delete",       "label": "Delete evidence",              "module": "Evidence",         "description": "Admin only"},

    # Leads
    {"code": "lead.create",           "label": "Add a manual lead",            "module": "Leads",            "description": ""},
    {"code": "lead.read",             "label": "View leads",                   "module": "Leads",            "description": "UC5"},
    {"code": "lead.update",           "label": "Update lead status",           "module": "Leads",            "description": ""},
    {"code": "lead.dismiss",          "label": "Dismiss a lead",               "module": "Leads",            "description": ""},

    # Notes
    {"code": "note.create",           "label": "Add investigation note",       "module": "Notes",            "description": ""},
    {"code": "note.read",             "label": "View investigation notes",     "module": "Notes",            "description": ""},
    {"code": "note.update",           "label": "Update own notes",             "module": "Notes",            "description": ""},
    {"code": "note.delete",           "label": "Delete own notes",             "module": "Notes",            "description": ""},

    # Timeline
    {"code": "timeline.create",       "label": "Add timeline event",           "module": "Timeline",         "description": ""},
    {"code": "timeline.read",         "label": "View timeline events",         "module": "Timeline",         "description": "UC8"},
    {"code": "timeline.update",       "label": "Update timeline event",        "module": "Timeline",         "description": ""},

    # Visualization & analytics
    {"code": "hotspot.read",          "label": "View crime hotspot map",       "module": "Visualization",    "description": "UC7"},
    {"code": "analytics.read",        "label": "View analytics dashboards",    "module": "Visualization",    "description": ""},

    # Reports
    {"code": "report.generate",       "label": "Generate reports",             "module": "Reports",          "description": "UC10"},
    {"code": "report.export.pdf",     "label": "Export report as PDF",         "module": "Reports",          "description": ""},
    {"code": "report.export.csv",     "label": "Export report as CSV",         "module": "Reports",          "description": ""},

    # AI / NLP
    {"code": "ai.analysis.run",       "label": "Run AI analysis on a case",    "module": "AI",               "description": ""},
    {"code": "ai.entity.verify",      "label": "Verify / correct AI entity",   "module": "AI",               "description": ""},

    # Database / admin
    {"code": "db.backup.create",      "label": "Create database backup",       "module": "Database",         "description": "UC3"},
    {"code": "db.backup.restore",     "label": "Restore from backup",          "module": "Database",         "description": "Admin only"},
    {"code": "db.optimize",           "label": "Optimize / reindex database",  "module": "Database",         "description": ""},
    {"code": "db.export",             "label": "Export bulk case data",        "module": "Database",         "description": ""},
    {"code": "db.import",             "label": "Import bulk case data",        "module": "Database",         "description": ""},

    # Audit / settings
    {"code": "audit.read",            "label": "View audit logs",              "module": "Audit",            "description": "Admin only"},
    {"code": "settings.read",         "label": "View system settings",         "module": "Settings",         "description": ""},
    {"code": "settings.update",       "label": "Change system settings",       "module": "Settings",         "description": "Admin only"},
]


# ════════════════════════════════════════════════════════════════════════════
# 16. DEFAULT SYSTEM SETTINGS
# ────────────────────────────────────────────────────────────────────────────
# Mirrors the DEFAULT object in src/pages/form/system-setting/page.jsx so
# the UI renders correct values on first load.
# Stored as text (key/value); cast on read in the service layer.
# ════════════════════════════════════════════════════════════════════════════

SYSTEM_SETTINGS = [
    {"key": "theme",                "value": "light"},
    {"key": "language",             "value": "en"},
    {"key": "encryption_enabled",   "value": "true"},
    {"key": "auto_backup",          "value": "true"},
    {"key": "backup_interval_hrs",  "value": "24"},
    {"key": "session_timeout_min",  "value": "30"},
    {"key": "audit_logging",        "value": "true"},
    {"key": "two_factor_admin",     "value": "false"},
    {"key": "password_expiry_days", "value": "90"},
    {"key": "notifications_enabled","value": "true"},
    {"key": "email_alerts",         "value": "false"},
    {"key": "max_failed_logins",    "value": "5"},
    {"key": "lockout_duration_min", "value": "15"},
]


# ════════════════════════════════════════════════════════════════════════════
# Main seed entry point
# ════════════════════════════════════════════════════════════════════════════

def seed_all():
    """Run all seeders in dependency order. City needs Province; everything
    else is independent."""
    print("\n=== Seeding lookup tables ===\n")
    init_db()  # ensures schema exists; harmless if already there

    with session_scope() as db:

        # 1. PROVINCES (must come before CITIES because of FK)
        ins, skip = upsert(db, Province, PROVINCES)
        print(f"  Province                 +{ins:3d}  skip {skip:3d}")

        db.flush()  # ensure provinces are visible inside this transaction

        # 2. CITIES (resolves province FK by code)
        city_ins, city_skip = 0, 0
        for province_code, city_names in CITIES.items():
            province = db.query(Province).filter_by(code=province_code).first()
            if not province:
                print(f"  ! Province {province_code} not found — skipping cities")
                continue
            for sort_idx, name in enumerate(city_names, start=1):
                exists = (
                    db.query(City)
                      .filter_by(province_id=province.id, name=name)
                      .first()
                )
                if exists:
                    city_skip += 1
                    continue
                db.add(City(province_id=province.id, name=name, sort_order=sort_idx))
                city_ins += 1
        print(f"  City                     +{city_ins:3d}  skip {city_skip:3d}")

        # 3-15. The rest — independent, just upsert by `code`.
        for model, rows, label in [
            (CaseType,           CASE_TYPES,            "CaseType"),
            (Weapon,             WEAPONS,               "Weapon"),
            (EvidenceType,       EVIDENCE_TYPES,        "EvidenceType"),
            (CaseStatus,         CASE_STATUSES,         "CaseStatus"),
            (SuspectStatus,      SUSPECT_STATUSES,      "SuspectStatus"),
            (VictimStatus,       VICTIM_STATUSES,       "VictimStatus"),
            (WitnessType,        WITNESS_TYPES,         "WitnessType"),
            (WitnessCredibility, WITNESS_CREDIBILITY,   "WitnessCredibility"),
            (Severity,           SEVERITIES,            "Severity"),
            (LeadType,           LEAD_TYPES,            "LeadType"),
            (LeadStatus,         LEAD_STATUSES,         "LeadStatus"),
            (TimelineEventType,  TIMELINE_EVENT_TYPES,  "TimelineEventType"),
            (NoteCategory,       NOTE_CATEGORIES,       "NoteCategory"),
            (Permission,         PERMISSIONS,           "Permission"),
        ]:
            ins, skip = upsert(db, model, rows)
            print(f"  {label:24s} +{ins:3d}  skip {skip:3d}")

        # 16. SYSTEM SETTINGS — match field is `key` not `code`.
        ins, skip = upsert(db, SystemSetting, SYSTEM_SETTINGS, match_field="key")
        print(f"  {'SystemSetting':24s} +{ins:3d}  skip {skip:3d}")

    print("\n✓ Seed complete\n")


if __name__ == "__main__":
    seed_all()