"""Smoke test for the explanation builder.

Imports the pure-Python helpers from case_linker_engine.py (the
ones that don't touch SQLAlchemy) and verifies that the explanation
output looks like an investigator's note.

Run: python smoke_test.py
"""
import sys, os, types

# Stub out the models import so we don't need a real DB or SQLAlchemy.
fake_models = types.ModuleType("models")
class _Fake:
    pass
for name in ("Case", "CaseSuspect", "CaseVictim", "Evidence", "Person"):
    setattr(fake_models, name, _Fake)
sys.modules["models"] = fake_models

# Stub sqlalchemy bits the engine imports at module level.
fake_sa_orm = types.ModuleType("sqlalchemy.orm")
class _Sess: pass
def joinedload(*a, **k): return None
fake_sa_orm.Session = _Sess
fake_sa_orm.joinedload = joinedload
sys.modules["sqlalchemy.orm"] = fake_sa_orm

fake_sa = types.ModuleType("sqlalchemy")
def or_(*a): return None
fake_sa.or_ = or_
sys.modules["sqlalchemy"] = fake_sa

sys.path.insert(0, ".")
from services import case_linker_engine as eng
from datetime import date

# ── Build two synthetic CaseFeatures bundles ────────────────────────────────
a = eng.CaseFeatures(
    case_id_internal=1, case_id_external="C-2040", case_title="Armed robbery at jeweller",
    suspects={
        "cnic:35201-1234567-1": eng.PersonInfo(
            person_id=10, key="cnic:35201-1234567-1",
            full_name="Bilal Hussain", cnic="35201-1234567-1", age=34,
            gender="Male", address="Karachi",
            criminal_record=True,
            known_affiliations="Anjuman gang",
            vehicle_description="white Toyota Corolla",
            relation_to_case="Primary suspect",
        ),
    },
    weapon_label="9mm pistol", weapon_family="firearm",
    motive="financial gain", modus_operandi="armed robbery during business hours",
    crime_type_id=3, crime_type_label="Armed Robbery", crime_type_family="property",
    city_id=1, city_label="Karachi", police_station="saddar",
    area="saddar", latitude=24.8607, longitude=67.0011,
    incident_date=date(2025, 9, 12),
    incident_hour=14.5,
    evidence_types={"firearm rounds", "cctv footage"},
)

b = eng.CaseFeatures(
    case_id_internal=2, case_id_external="C-2053", case_title="Robbery on Tariq Road",
    suspects={
        "cnic:35201-1234567-1": eng.PersonInfo(
            person_id=10, key="cnic:35201-1234567-1",
            full_name="Bilal Hussain", cnic="35201-1234567-1", age=34,
            gender="Male", address="Karachi",
            criminal_record=True,
            known_affiliations="Anjuman gang",
            vehicle_description="white Toyota Corolla",
            relation_to_case="Primary suspect",
        ),
    },
    weapon_label="9mm pistol", weapon_family="firearm",
    motive="financial gain", modus_operandi="armed robbery during business hours",
    crime_type_id=3, crime_type_label="Armed Robbery", crime_type_family="property",
    city_id=1, city_label="Karachi", police_station="aram bagh",
    area="tariq road", latitude=24.8721, longitude=67.0258,
    incident_date=date(2025, 9, 23),
    incident_hour=15.0,
    evidence_types={"firearm rounds", "witness statement"},
)

edge = eng._score_pair(a, b)
print("="*78)
print(f"Total score: {edge.total:.3f}")
print(f"Primary dimension: {edge.primary}")
print(f"Fired dimensions: {list(edge.dimensions.keys())}")
print("="*78)

proposal = eng._make_proposal(a.case_id_internal, edge, a, b)
print("\n┌─ HEADLINE (goes in table cell) ─────────────────────────────────────")
print(f"│ {proposal.headline}")
print("└─" + "─"*72)
print("\n┌─ DETAIL (goes in expandable drawer) ────────────────────────────────")
for line in proposal.detail.split("\n"):
    print(f"│ {line}")
print("└─" + "─"*72)
print("\n┌─ SHARED ENTITIES (written to case_link_shared_entities) ────────────")
for ent_type, ent_value, person_id in proposal.shared_entities:
    print(f"│ type={ent_type:12s}  value={ent_value:40s}  person_id={person_id}")
print("└─" + "─"*72)