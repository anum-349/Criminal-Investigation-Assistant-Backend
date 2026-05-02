from datetime import UTC, datetime, date, timedelta
from db import session_scope
from models import (
    # Lookups (we resolve FK ids by code)
    Province, City, CaseType, CaseStatus, Severity, Weapon, EvidenceType,
    SuspectStatus, VictimStatus, WitnessType, WitnessCredibility,
    LeadType, LeadStatus, TimelineEventType, NoteCategory,
    # Operational
    User, Investigator, Person, Case, Location,
    MurderDetails, SexualAssaultDetails, TheftDetails,
    CaseSuspect, CaseVictim, CaseWitness,
    VictimForensicFinding, VictimTimelineEntry, VictimLegalMilestone,
    Evidence, EvidencePhoto,
    ExtractedEntity, CompletenessReport,
    CompletenessMissingField, CompletenessInconsistency,
    Lead, CaseLink, CaseLinkSharedEntity,
    TimelineEvent, CaseUpdateNote, CaseUpdateFieldChange,
    InvestigationNote, Notification, AuditLog,
)


# ════════════════════════════════════════════════════════════════════════════
# Lookup-resolver helper
# ────────────────────────────────────────────────────────────────────────────
# Service code uses lookup IDs (FKs) but human-written seed data is more
# readable with codes ("MURDER", "HIGH"). This helper resolves on demand
# and caches the result.
# ════════════════════════════════════════════════════════════════════════════

class L:
    """Lazy lookup resolver. Usage: L(db).case_type("MURDER") → returns id."""
    def __init__(self, db):
        self.db = db
        self._cache = {}

    def _id(self, model, code, code_field="code"):
        key = (model.__name__, code)
        if key in self._cache:
            return self._cache[key]
        row = self.db.query(model).filter(getattr(model, code_field) == code).first()
        if not row:
            raise RuntimeError(f"Lookup miss: {model.__name__}.{code_field}={code!r} — run seed_lookups.py first")
        self._cache[key] = row.id
        return row.id

    # Convenience methods
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
    def lead_type(self, c):      return self._id(LeadType, c)
    def lead_status(self, c):    return self._id(LeadStatus, c)
    def witness_cred(self, c):    return self._id(WitnessCredibility, c)
    def timeline_event_type(self, c): return self._id(TimelineEventType, c)
    def note_category(self, c):  return self._id(NoteCategory, c)

    def _id_pair(self, model, f1, v1, f2, v2):
        key = (model.__name__, v1, v2)
        if key in self._cache:
            return self._cache[key]
        row = self.db.query(model).filter(getattr(model, f1) == v1, getattr(model, f2) == v2).first()
        if not row:
            raise RuntimeError(f"Lookup miss: {model.__name__} where {f1}={v1!r}, {f2}={v2}")
        self._cache[key] = row.id
        return row.id


# ════════════════════════════════════════════════════════════════════════════
# Find-or-create Person (deduplication by CNIC)
# ────────────────────────────────────────────────────────────────────────────
# Same human (Bilal Hussain) is a suspect in multiple cases → must point
# to the same Person row so case-linkage AI can detect the overlap.
# ════════════════════════════════════════════════════════════════════════════

def upsert_person(db, **kwargs) -> Person:
    """Find by CNIC if provided, else by exact name match. Create if missing."""
    cnic = kwargs.get("cnic")
    if cnic:
        existing = db.query(Person).filter_by(cnic=cnic).first()
        if existing:
            return existing
    elif kwargs.get("full_name"):
        existing = db.query(Person).filter_by(full_name=kwargs["full_name"], is_unknown=False).first()
        if existing:
            return existing
    p = Person(**kwargs)
    db.add(p)
    db.flush()
    return p


# ════════════════════════════════════════════════════════════════════════════
# CASE 1 — C-2053 — Murder in Clifton (the flagship demo case)
# ────────────────────────────────────────────────────────────────────────────
# Mirrors all the rich detail from CaseTimeline.jsx + CaseLocation.jsx.
# ════════════════════════════════════════════════════════════════════════════

def build_case_2053(db, lk: L, investigator_user: User):
    """Murder of Tariq Mehmood in Clifton apartment. Bilal Hussain detained,
    second suspect at large. Under investigation."""

    # ── People involved ────────────────────────────────────────────────────
    bilal = upsert_person(db,
        cnic="35201-1234567-1",
        full_name="Bilal Hussain",
        gender="Male", age=34,
        contact="+92-321-4567890",
        address="House 47, Defence Phase 5, Karachi",
        occupation="Unemployed",
        physical_description="Medium build, 5'9\", dark complexion, scar on left cheek.",
    )

    unknown_male = upsert_person(db,
        is_unknown=True,
        full_name="Unknown Male (Suspect-2)",
        gender="Male",
        physical_description="Approximately 6'0\" tall, wearing dark hoodie. Face obscured by mask in CCTV footage.",
    )

    tariq = upsert_person(db,
        cnic="42101-9876543-3",
        full_name="Tariq Mehmood",
        gender="Male", age=52,
        contact="+92-300-7777888",
        address="Flat 12, Clifton Apartments Block 5, Karachi",
        occupation="Retired Bank Manager",
    )

    farooq = upsert_person(db,
        cnic="42101-1112223-4",
        full_name="Farooq Sheikh",
        gender="Male", age=40,
        contact="+93-456-7867654",
        address="Block 5, Clifton, Karachi",
        occupation="Shopkeeper",
    )

    ayesha = upsert_person(db,
        cnic="42101-3334445-5",
        full_name="Ayesha Noor",
        gender="Female", age=27,
        contact="+92-300-1234567",
        address="Apartment 8, Clifton Apartments Block 5, Karachi",
        occupation="Teacher",
    )

    imran = upsert_person(db,
        cnic="42101-5556667-6",
        full_name="Imran Khan",
        gender="Male", age=45,
        contact="+92-333-5551234",
        address="Security post, Clifton Apartments Block 5, Karachi",
        occupation="Security Guard",
    )

    # ── Case row ───────────────────────────────────────────────────────────
    case = Case(
        case_id="CASE-2026-2053",
        fir_number="FIR-2026-1042",
        fir_language="English",
        fir_text_raw="On the night of 14 March 2026 at approximately 22:00 hours, the deceased Tariq Mehmood (aged 52) was found dead in his apartment at Flat 12, Clifton Apartments Block 5, Karachi. The body was discovered by his daughter who returned home from work. Initial examination indicates death by stab wounds. No signs of forced entry...",
        fir_text_clean="Victim Tariq Mehmood found deceased in his Clifton apartment on 14 March 2026 at ~22:00. Multiple stab wounds. No forced entry. Discovered by daughter on returning home.",
        case_type_id=lk.case_type("MURDER"),
        case_status_id=lk.case_status("UNDER_INVESTIGATION"),
        priority_id=lk.severity("CRITICAL"),
        ppc_sections="PPC 302, 460",
        case_title="Murder – Tariq Mehmood, Clifton Block 5",
        description="Homicide investigation following the murder of retired bank manager Tariq Mehmood in his Clifton apartment. Multiple stab wounds. Primary suspect Bilal Hussain detained; second suspect remains at large.",
        incident_date=date(2026, 3, 14),
        incident_time="22:00",
        reporting_date=date(2026, 3, 15),
        reporting_time="08:00",
        reporting_officer="DSP Asif Malik",
        assigned_investigator_id=investigator_user.investigator.id,
        created_by_id=investigator_user.id,
        complainant_name="Saima Mehmood (daughter)",
        complainant_contact="+92-300-1239876",
        complainant_cnic="42101-9999111-2",
        weapon_id=lk.weapon("KNIFE"),
        weapon_description="Kitchen knife, ~20cm blade, recovered from scene with bloodstains.",
        vehicle_used="White Toyota Corolla (registration partial: BFE-2***), seen on CCTV leaving the building.",
        motive="Financial — victim had recently withdrawn a large sum from his bank account (PKR 2.5M).",
        modus_operandi="Forced entry attempt failed; suspect likely admitted by victim. Surprise attack with knife.",
        cctv_available=True,
        crime_description="Victim found in living room with multiple stab wounds. No signs of forced entry. White sedan seen on neighbouring CCTV at approximately 22:35.",
        progress_percent=65,
        next_hearing_date=date(2026, 5, 12),
    )
    db.add(case)
    db.flush()

    # ── Crime-type subtype ─────────────────────────────────────────────────
    db.add(MurderDetails(
        case_id_fk=case.id,
        cause_of_death="Multiple stab wounds (chest and abdomen)",
        body_location="At Scene of Crime",
        time_of_death="21:30 PM – 22:30 PM",
        postmortem_done=True,
        forensic_done=True,
    ))

    # ── Location ───────────────────────────────────────────────────────────
    db.add(Location(
        case_id_fk=case.id,
        province_id=lk.province("SINDH"),
        city_id=lk.city("Karachi", "SINDH"),
        area="Clifton",
        police_station="Clifton Police Station",
        full_address="Flat 12, Clifton Apartments Block 5, Clifton, Karachi",
        display_address="Clifton Apartments Block 5, Clifton, Karachi, Sindh, Pakistan",
        latitude=24.8133, longitude=67.0286,
        crime_scene_type="Residential – Indoor",
        scene_access="Forensic Team Present",
        landmarks="Sea View Beach – 0.5 km; Dolmen Mall Clifton – 1.0 km; Clifton Bridge – 1.4 km",
    ))

    # ── Suspects ───────────────────────────────────────────────────────────
    db.add(CaseSuspect(
        case_id_fk=case.id, person_id=bilal.id, suspect_id="SUS-2053-01",
        status_id=lk.suspect_status("DETAINED"),
        relation_to_case="Acquaintance",
        reason="CCTV match; informant identification; vehicle traced to suspect's address.",
        alibi="Claims he was at a restaurant in DHA Phase 5 between 21:30 and 23:00. Restaurant CCTV does not confirm his presence.",
        known_affiliations="Suspected of prior associations with local extortion ring.",
        arrival_method="Car",
        vehicle_description="White Toyota Corolla, BFE-2*** (partial)",
        criminal_record=True,
        arrested=True,
        notes="Held under remand. Awaiting forensic match on fingerprint sample E-2053-06.",
    ))
    sus2 = CaseSuspect(
        case_id_fk=case.id, person_id=unknown_male.id, suspect_id="SUS-2053-02",
        status_id=lk.suspect_status("AT_LARGE"),
        relation_to_case="Stranger",
        reason="Second figure in CCTV footage. Identity unknown.",
        notes="Search ongoing. Description circulated to area police stations.",
    )
    db.add(sus2)
    db.flush()

    # ── Victim ─────────────────────────────────────────────────────────────
    victim_row = CaseVictim(
        case_id_fk=case.id, person_id=tariq.id, victim_id="VIC-2053-01",
        status_id=lk.victim_status("DECEASED"),
        primary_label="Primary Victim — Murder",
        relation_to_suspect="Acquaintance — Banking client of suspect's brother",
        injury_type="Fatal (Death)",
        nature_of_injuries="Five stab wounds — chest (3), abdomen (2). Defensive wounds on forearms.",
        cause_of_death="Hemorrhagic shock due to penetrating chest trauma",
        declared_dead="At scene",
        postmortem_autopsy="Conducted",
        injury_summary="Fatal stab wounds. Death within 5–10 minutes of attack.",
        injury_recorded_by="Dr. Saadia Khan, JPMC Forensic Unit",
        medical_report=True, postmortem=True, cooperative=False,
        threat_level_id=lk.severity("CRITICAL"),
        statement="N/A — deceased",
    )
    db.add(victim_row)
    db.flush()

    # Forensic findings (child rows of victim)
    for finding in [
        "Knife wound pattern consistent with single-edged kitchen knife.",
        "DNA recovered from under victim's fingernails — sent for AFIS comparison.",
        "Defensive wounds suggest victim resisted briefly.",
        "Time of death estimated 21:30–22:30 based on body temperature and rigor.",
    ]:
        db.add(VictimForensicFinding(case_victim_id=victim_row.id, finding_text=finding))

    # Victim timeline (legal milestones leading up to death)
    for d, t in [
        ("2026-03-10", "Withdrew PKR 2.5M from UBL Clifton branch."),
        ("2026-03-12", "Mentioned to neighbour he was expecting a visitor that weekend."),
        ("2026-03-14", "Last seen alive at 19:00 by building security."),
        ("2026-03-14", "Time of death estimated between 21:30 and 22:30."),
        ("2026-03-15", "Body discovered by daughter at 06:30."),
    ]:
        db.add(VictimTimelineEntry(
            case_victim_id=victim_row.id,
            entry_date=date.fromisoformat(d), entry_text=t,
        ))

    for label, done in [
        ("Postmortem completed",        True),
        ("FIR registered",              True),
        ("Forensic report submitted",   False),
        ("Charge sheet filed",          False),
        ("Case heard at sessions court", False),
    ]:
        db.add(VictimLegalMilestone(
            case_victim_id=victim_row.id,
            label=label, done=done,
            completed_at=datetime.now(UTC) if done else None,
        ))

    # ── Witnesses ──────────────────────────────────────────────────────────
    for p, wid, wtype, rel, cred, status, statement in [
        (farooq, "WIT-2053-01", "EARWITNESS", "Neighbor", "MEDIUM", "Active",
         "Heard a loud argument from Apt 12 at approximately 22:30 PM. Did not see the suspect but heard a male voice raised in Urdu."),
        (ayesha, "WIT-2053-02", "EYEWITNESS", "Resident", "MEDIUM", "Active",
         "Saw a tall man in dark hoodie leaving the building around 22:45 PM. Could not identify facial features clearly."),
        (imran,  "WIT-2053-03", "EYEWITNESS", "Security", "HIGH", "Active",
         "Confirmed entry of Bilal Hussain in white sedan at 21:55. Logged in entry register. Sedan exited at 22:50."),
    ]:
        db.add(CaseWitness(
            case_id_fk=case.id, person_id=p.id, witness_id=wid,
            witness_type_id=lk.witness_type(wtype),
            relation_to_case=rel,
            credibility_id=lk.witness_cred(cred),
            status=status,
            statement_date=date(2026, 3, 18),
            statement_recorded_by="Insp. Rana Ali Khan",
            description=statement,
            cooperating=True,
        ))

    # ── Evidence ───────────────────────────────────────────────────────────
    evidence_specs = [
        ("EV-2053-01", "WEAPON",   "Kitchen knife, ~20cm blade. Recovered from kitchen sink. Bloodstains visible.",
         "evidence/2053/weapon-knife.jpg", date(2026, 3, 15), "Forensic Team A"),
        ("EV-2053-02", "DNA",      "Blood sample from victim's clothing. Sent for typing and AFIS match.",
         "evidence/2053/dna-clothing.json", date(2026, 3, 16), "Dr. Saadia Khan"),
        ("EV-2053-03", "PHOTO",    "38 crime scene photos with measurements. Wide-angle shots of all rooms.",
         "evidence/2053/scene-photos.zip", date(2026, 3, 16), "Insp. Rana Ali Khan"),
        ("EV-2053-04", "CCTV",     "12 hours of CCTV footage from neighbouring shop. 3 timestamps flagged for AI enhancement.",
         "evidence/2053/cctv-shop.mp4", date(2026, 3, 17), "Insp. Rana Ali Khan"),
        ("EV-2053-05", "FORENSIC", "Postmortem report with cause of death and time of death analysis.",
         "evidence/2053/postmortem.pdf", date(2026, 3, 18), "Dr. Saadia Khan"),
        ("EV-2053-06", "FINGERPRINT", "Partial print recovered from bedroom door handle. Sent to AFIS.",
         "evidence/2053/fp-doorhandle.tif", date(2026, 3, 19), "AFIS Lab"),
        ("EV-2053-07", "PHYSICAL", "Synthetic fibre from window latch. Sent for material analysis.",
         "evidence/2053/fibre-window.jpg", date(2026, 3, 19), "Forensic Team A"),
        ("EV-2053-08", "DOCUMENT", "Building entry-log printout for 14 March 2026.",
         "evidence/2053/entry-log.pdf", date(2026, 3, 24), "Insp. Rana Ali Khan"),
    ]
    evidences = []
    for eid, etype, desc, path, dt, who in evidence_specs:
        ev = Evidence(
            case_id_fk=case.id, evidence_id=eid,
            type_id=lk.evidence_type(etype),
            description=desc,
            file_path=path, file_name=path.split("/")[-1],
            file_mime="application/octet-stream", file_size=2048000,
            sha256_hash="a" * 64,  # demo placeholder
            date_collected=dt, collected_by=who,
        )
        db.add(ev)
        evidences.append(ev)
    db.flush()

    # A few photos under EV-2053-03
    for caption, path in [
        ("Living room — wide angle", "evidence/2053/photo-livingroom.jpg"),
        ("Body position",             "evidence/2053/photo-body.jpg"),
        ("Kitchen — knife location",  "evidence/2053/photo-kitchen.jpg"),
        ("Bedroom door handle",       "evidence/2053/photo-doorhandle.jpg"),
    ]:
        db.add(EvidencePhoto(
            evidence_id=evidences[2].id,  # EV-2053-03
            file_path=path, file_name=path.split("/")[-1],
            file_mime="image/jpeg", file_size=850000, caption=caption,
        ))

    # ── NLP-extracted entities ─────────────────────────────────────────────
    for et, val, conf, char_start, char_end, person_id in [
        ("PERSON",   "Tariq Mehmood",   0.97, 56,  69,  tariq.id),
        ("PERSON",   "Bilal Hussain",   0.91, None, None, bilal.id),
        ("LOCATION", "Clifton Apartments Block 5", 0.95, 110, 137, None),
        ("LOCATION", "Karachi",         0.99, None, None, None),
        ("DATE",     "14 March 2026",   0.99, 19, 32, None),
        ("WEAPON",   "kitchen knife",   0.88, None, None, None),
        ("VEHICLE",  "White Toyota Corolla", 0.84, None, None, None),
        ("ORG",      "UBL Clifton branch",   0.79, None, None, None),
        ("PERSON",   "Saima Mehmood",   0.66, None, None, None),  # low-confidence → flagged
    ]:
        db.add(ExtractedEntity(
            case_id_fk=case.id, entity_type=et, entity_value=val,
            confidence=conf, char_start=char_start, char_end=char_end,
            flagged_for_review=(conf < 0.70),
            verified_by_user=(conf >= 0.90),
            matched_person_id=person_id,
        ))

    # ── Completeness report ────────────────────────────────────────────────
    cr = CompletenessReport(case_id_fk=case.id, score=82, last_run_at=datetime.now(UTC))
    db.add(cr); db.flush()
    for f in ["Suspect 2 identification", "Forensic report submission date"]:
        db.add(CompletenessMissingField(
            report_id=cr.id, field_name=f,
            severity_id=lk.severity("MEDIUM"),
        ))
    db.add(CompletenessInconsistency(
        report_id=cr.id, field_name="time_of_death",
        problem="Witness statement (door slam at 22:35) suggests later TOD than postmortem estimate (21:30–22:30).",
        severity_id=lk.severity("LOW"),
    ))

    # ── Leads ──────────────────────────────────────────────────────────────
    leads_specs = [
        # (lead_id, source, type_code, status, severity, conf, desc, next_step)
        ("LD-2053-01", "AI",     "CCTV_ANALYSIS",   "ACTIONED",     "HIGH",     78.0,
         "Enhanced CCTV still from neighbouring shop matches a person of interest. 78% confidence on facial geometry.",
         "Verify via informant — Rana Ali Khan to coordinate."),
        ("LD-2053-02", "AI",     "VEHICLE",         "ACTIONED",     "HIGH",     87.0,
         "Cross-camera analysis matched suspect vehicle (white Toyota Corolla) to 3 prior incidents in adjacent districts.",
         "Pull Punjab Highway camera records for Toyota Corolla matches."),
        ("LD-2053-03", "AI",     "SUSPECT_PATTERN", "UNDER_REVIEW", "HIGH",     82.0,
         "MO similarity detected with C-2040 (also Karachi, similar weapon, financial motive). Possible serial pattern.",
         "Compare suspect lists across both cases — Bilal Hussain appears in both."),
        ("LD-2053-04", "MANUAL", "INFORMANT",       "ACTIONED",     "HIGH",     None,
         "Informant 'Z' identified the figure in Block 5 CCTV as Bilal Hussain. Provided current address.",
         "Surveillance approved; arrest warrant requested."),
        ("LD-2053-05", "AI",     "FINGERPRINT",     "IN_PROGRESS",  "CRITICAL", 95.0,
         "AFIS partial-match on door-handle print returned 95% match against Bilal Hussain's record.",
         "Verify with full AFIS database scan and confirm chain of custody."),
        ("LD-2053-06", "MANUAL", "WITNESS_LEAD",    "NEW",          "MEDIUM",   None,
         "Apartment 14 resident heard door slam matching timeline. Possibly heard the second suspect leaving.",
         "Re-interview Apt 14 resident; show photo array."),
    ]
    bilal_role = db.query(CaseSuspect).filter_by(case_id_fk=case.id, person_id=bilal.id).first()
    for lid, src, ltype, lstatus, lsev, conf, desc, ns in leads_specs:
        db.add(Lead(
            case_id_fk=case.id, lead_id=lid, event_source=src,
            type_id=lk.lead_type(ltype),
            status_id=lk.lead_status(lstatus),
            severity_id=lk.severity(lsev),
            description=desc, confidence=conf or 0.0, next_step=ns,
            suggested_suspect_id=bilal_role.id if "Bilal" in desc else None,
            created_by_user_id=investigator_user.id if src == "MANUAL" else None,
        ))

    # ── Timeline events (compressed but realistic) ─────────────────────────
    timeline_specs = [
        ("EV-2053-T01", "MANUAL",    "FIELD_VISIT",        "Crime scene first response",
         "Responding officer arrived at Apt 12, Clifton Block 5. No forced entry. Front door locked from inside. Body found in living room.",
         "DSP Asif Malik", "CRITICAL", "Apt 12, Clifton Block 5",
         "Scene secured; forensic team called in.", date(2026, 3, 15), "06:30"),
        ("EV-2053-T02", "SYSTEM",    "FIR_FILED",          "FIR Filed: FIR-2026-1042",
         "First Information Report filed at Clifton Police Station. Sections 302, 460 of Pakistan Penal Code.",
         "DSP Asif Malik", "CRITICAL", None, None, date(2026, 3, 15), "08:00"),
        ("EV-2053-T03", "SYSTEM",    "CASE_REGISTERED",    "Case Registered",
         "Case CASE-2026-2053 created in the system. Assigned to Inspector Rana Ali Khan.",
         "Insp. Rana Ali Khan", "NORMAL", None, None, date(2026, 3, 15), "09:00"),
        ("EV-2053-T04", "SYSTEM",    "EVIDENCE_ADDED",     "Evidence Added: CCTV Footage (neighbouring shop)",
         "12 hours of footage uploaded to evidence locker (EV-2053-04). Initial review flagged 3 timestamps for AI enhancement.",
         "Insp. Rana Ali Khan", "NORMAL", None, None, date(2026, 3, 17), "11:00"),
        ("EV-2053-T05", "AI",        "AI_LEAD_GENERATED",  "AI Lead Generated: CCTV figure identification",
         "Enhanced still from neighbouring shop CCTV. Confidence 78% match against persons-of-interest database.",
         "System (AI)", "HIGH", None, None, date(2026, 3, 18), "09:45"),
        ("EV-2053-T06", "MANUAL",    "INFORMANT_CONTACT",  "Met with confidential informant 'Z'",
         "Informant identified the figure in the Block 5 CCTV footage as Bilal Hussain, known to frequent the area.",
         "Insp. Rana Ali Khan", "HIGH", "Sea View Park (offsite meet)",
         "Suspect ID actioned; surveillance approved.", date(2026, 3, 20), "20:00"),
        ("EV-2053-T07", "MANUAL",    "SURVEILLANCE",       "Surveillance of suspect's known address",
         "Plain-clothes team observed the address for 6 hours. Suspect arrived at 22:40 in a white sedan matching CCTV.",
         "Insp. Rana Ali Khan", "HIGH", "Defence Phase 5",
         "Vehicle confirmed; arrest warrant requested.", date(2026, 3, 21), "16:00"),
        ("EV-2053-T08", "SYSTEM",    "SUSPECT_ADDED",      "Suspect Added: Bilal Hussain",
         "Primary suspect entry created. Identified via CCTV review and informant tip.",
         "Insp. Rana Ali Khan", "NORMAL", None, None, date(2026, 3, 22), "13:20"),
        ("EV-2053-T09", "MANUAL",    "ARREST",             "Arrest of Bilal Hussain",
         "Suspect arrested at his Defence Phase 5 residence at 23:00. White Toyota Corolla seized.",
         "Insp. Rana Ali Khan", "CRITICAL", "Defence Phase 5",
         "Suspect taken into custody; vehicle impounded.", date(2026, 3, 22), "23:00"),
        ("EV-2053-T10", "MANUAL",    "SUSPECT_INTERVIEW",  "Initial questioning of Bilal Hussain",
         "Suspect denied being at the scene. Provided alibi: claims he was at a restaurant in DHA Phase 5 between 21:30 and 23:00.",
         "Insp. Rana Ali Khan", "HIGH", "Clifton PS interview room 2",
         "Alibi noted; awaiting CCTV verification at restaurant.", date(2026, 3, 23), "11:00"),
        ("EV-2053-T11", "MANUAL",    "WITNESS_INTERVIEW",  "Interviewed Farooq Sheikh (WIT-2053-01)",
         "Confirmed hearing argument at 22:30 PM. Did not see the suspect but heard a male voice raised in Urdu.",
         "Insp. Rana Ali Khan", "NORMAL", "Apt 12, Clifton Block 5",
         "Statement recorded; matches earlier witness account.", date(2026, 3, 24), "15:30"),
        ("EV-2053-T12", "MANUAL",    "FORENSIC_VISIT",     "Site walkthrough with forensic team",
         "Re-examined apartment with the forensic team. Identified two new sample points — partial fingerprint and fibre.",
         "Insp. Rana Ali Khan", "HIGH", "Apt 12, Clifton Block 5",
         "Two new samples collected; logged as EV-2053-06 and EV-2053-07.", date(2026, 3, 25), "10:00"),
        ("EV-2053-T13", "MANUAL",    "COURT_HEARING",      "Preliminary court date set",
         "Magistrate set a preliminary briefing for 12 May. Forensic report and witness summary due by 5 May.",
         "DSP Asif Malik", "NORMAL", "Karachi Sessions Court",
         "Hearing scheduled; deadline noted.", date(2026, 4, 1), "11:30"),
    ]
    for eid, src, ettype, title, desc, officer, sev, loc, outcome, ev_date, ev_time in timeline_specs:
        db.add(TimelineEvent(
            case_id_fk=case.id, event_id=eid, event_source=src,
            event_type_id=lk.timeline_event_type(ettype),
            title=title, description=desc, officer_name=officer,
            severity_id=lk.severity(sev),
            location=loc, outcome=outcome,
            event_date=ev_date, event_time=ev_time,
            follow_up_required=(outcome and "deadline" in outcome.lower()),
            follow_up_date=date(2026, 5, 5) if "deadline" in (outcome or "").lower() else None,
            editable=(src == "MANUAL"),
        ))

    # ── Investigation notes (Notes tab) ────────────────────────────────────
    for date_, cat, title, detail in [
        (date(2026, 3, 18), "FIELD_NOTE", "Initial site impressions",
         "Apartment kept very tidy except living room. Indicates surprise attack rather than struggle. Victim's daughter mentioned a recent visitor he was 'cautious about' — worth following up with her separately."),
        (date(2026, 3, 21), "INTELLIGENCE", "CI tip on suspect background",
         "CI 'Z' mentioned that Bilal Hussain has been associated with a local extortion ring previously. Worth pulling old case files for cross-reference."),
        (date(2026, 3, 23), "HYPOTHESIS", "Two-suspect theory",
         "If second figure in CCTV is real (not the same person re-entering), they may have been the lookout. Distance from front door at 22:50 suggests waiting role."),
        (date(2026, 3, 28), "ACTION_ITEM", "Things to check before next briefing",
         "1. Verify alibi at Cinnabon DHA Phase 5\n2. Get full UBL withdrawal logs for victim\n3. Re-interview building security on weekend traffic\n4. AFIS confirmation on EV-2053-06"),
    ]:
        db.add(InvestigationNote(
            note_id=f"NOTE-2053-{title[:8].upper().replace(' ', '')}",
            case_id_fk=case.id, user_id=investigator_user.id,
            category_id=lk.note_category(cat),
            officer_name="Insp. Rana Ali Khan",
            note_date=date_, title=title, detail=detail,
        ))

    # ── Update history ─────────────────────────────────────────────────────
    note = CaseUpdateNote(
        case_id_fk=case.id, user_id=investigator_user.id,
        note="Suspect arrested and detained. Status updated.",
        created_at=datetime(2026, 3, 22, 23, 30),
    )
    db.add(note); db.flush()
    db.add(CaseUpdateFieldChange(
        update_note_id=note.id, field_name="case_status",
        old_value="Open", new_value="Under Investigation",
    ))
    db.add(CaseUpdateFieldChange(
        update_note_id=note.id, field_name="progress_percent",
        old_value="20", new_value="50",
    ))

    return case


# ════════════════════════════════════════════════════════════════════════════
# CASE 2 — C-2040 — Armed Robbery (linked to C-2053 via Bilal Hussain)
# ════════════════════════════════════════════════════════════════════════════

def build_case_2040(db, lk: L, investigator_user: User):
    bilal = db.query(Person).filter_by(cnic="35201-1234567-1").first()  # reused

    bank_manager = upsert_person(db,
        cnic="42101-7654321-9",
        full_name="Naeem Akhtar",
        gender="Male", age=48,
        contact="+92-300-9988776",
        address="Bank Manager's residence, Saddar, Rawalpindi",
        occupation="Bank Branch Manager",
    )

    cashier = upsert_person(db,
        cnic="37402-5544332-1",
        full_name="Saleem Iqbal",
        gender="Male", age=29,
        contact="+92-333-2211990",
        address="Saddar, Rawalpindi",
        occupation="Bank Cashier",
    )

    case = Case(
        case_id="CASE-2026-2040",
        fir_number="FIR-2026-0876",
        fir_language="English",
        fir_text_clean="Armed robbery at HBL Saddar Branch on 20 October 2026 at 14:30. Three armed individuals fled with PKR 4.5M cash. No injuries.",
        case_type_id=lk.case_type("ROBBERY"),
        case_status_id=lk.case_status("ACTIVE"),
        priority_id=lk.severity("CRITICAL"),
        ppc_sections="PPC 392, 397, 34",
        case_title="Armed Robbery — HBL Saddar Branch",
        description="Three armed individuals robbed HBL Saddar Branch in broad daylight. Suspect description matches Bilal Hussain (currently held in connection with Case CASE-2026-2053).",
        incident_date=date(2026, 10, 20),
        incident_time="14:30",
        reporting_date=date(2026, 10, 20),
        reporting_time="15:15",
        reporting_officer="DSP Imran Shafique",
        assigned_investigator_id=investigator_user.investigator.id,
        created_by_id=investigator_user.id,
        complainant_name="Naeem Akhtar (Bank Manager)",
        complainant_contact="+92-300-9988776",
        weapon_id=lk.weapon("PISTOL"),
        weapon_description="Two pistols brandished. One suspect held a TT pistol; other had what appeared to be a 9mm.",
        vehicle_used="White Toyota Corolla — same vehicle later linked to Case CASE-2026-2053.",
        motive="Financial Gain",
        modus_operandi="Three masked men entered branch at peak hour; one held cashier at gunpoint while another emptied vault. Total time: 4 minutes.",
        cctv_available=True,
        crime_description="Bank robbery. Cash taken: PKR 4.5M. Three masked suspects fled in white sedan towards GT Road.",
        progress_percent=40,
    )
    db.add(case); db.flush()

    db.add(TheftDetails(
        case_id_fk=case.id,
        stolen_items="Cash (PKR 4.5M), branch security keycard.",
        stolen_value=4500000.0,
        recovery_status="Not Recovered",
        entry_point="Main Door",
    ))

    db.add(Location(
        case_id_fk=case.id,
        province_id=lk.province("PUNJAB"),
        city_id=lk.city("Rawalpindi", "PUNJAB"),
        area="Saddar",
        police_station="Saddar Police Station",
        full_address="HBL Saddar Branch, Bank Road, Saddar, Rawalpindi",
        display_address="Saddar, Rawalpindi, Punjab, Pakistan",
        latitude=33.5973, longitude=73.0479,
        crime_scene_type="Commercial – Indoor",
        scene_access="Released",
        landmarks="Mall Road – 0.4 km; Liaquat Bagh – 0.8 km",
    ))

    # Reuse Bilal as a suspect
    db.add(CaseSuspect(
        case_id_fk=case.id, person_id=bilal.id, suspect_id="SUS-2040-01",
        status_id=lk.suspect_status("DETAINED"),
        relation_to_case="Stranger",
        reason="Cross-case match (CASE-2026-2053). Vehicle description matches.",
        alibi="Has not been formally questioned on this case yet — awaiting transfer.",
        criminal_record=True, arrested=True,
        notes="Currently held under remand for CASE-2026-2053. Will be questioned about this incident.",
    ))

    # Bank manager and cashier as victims
    db.add(CaseVictim(
        case_id_fk=case.id, person_id=bank_manager.id, victim_id="VIC-2040-01",
        status_id=lk.victim_status("ALIVE"),
        primary_label="Primary Victim — Robbery",
        statement="Was held at gunpoint while suspects emptied the vault. No physical injury.",
        cooperative=True,
    ))
    db.add(CaseVictim(
        case_id_fk=case.id, person_id=cashier.id, victim_id="VIC-2040-02",
        status_id=lk.victim_status("ALIVE"),
        primary_label="Secondary Victim — Witness/Cashier",
        statement="Observed all three suspects clearly. Provided detailed description.",
        cooperative=True,
    ))
    db.flush()

    # Evidence
    for eid, etype, desc, dt, who in [
        ("EV-2040-01", "CCTV",     "Branch CCTV recording — full duration of robbery.", date(2026, 10, 20), "Insp. Rana Ali Khan"),
        ("EV-2040-02", "PHOTO",    "Crime scene photos — vault, counter, entry door.", date(2026, 10, 20), "Forensic Team B"),
        ("EV-2040-03", "STATEMENT","Cashier statement (Saleem Iqbal).", date(2026, 10, 21), "Insp. Rana Ali Khan"),
        ("EV-2040-04", "FORENSIC", "Tyre track impressions from getaway car.", date(2026, 10, 21), "Forensic Team B"),
    ]:
        db.add(Evidence(
            case_id_fk=case.id, evidence_id=eid,
            type_id=lk.evidence_type(etype),
            description=desc,
            file_path=f"evidence/2040/{eid.lower()}.bin",
            file_name=f"{eid.lower()}.bin",
            date_collected=dt, collected_by=who,
        ))

    # NLP entities
    for et, val, conf, person_id in [
        ("PERSON",   "Naeem Akhtar",    0.94, bank_manager.id),
        ("PERSON",   "Saleem Iqbal",    0.91, cashier.id),
        ("LOCATION", "HBL Saddar Branch", 0.96, None),
        ("LOCATION", "Rawalpindi",      0.99, None),
        ("DATE",     "20 October 2026", 0.99, None),
        ("WEAPON",   "TT pistol",       0.85, None),
        ("VEHICLE",  "white sedan",     0.81, None),
    ]:
        db.add(ExtractedEntity(
            case_id_fk=case.id, entity_type=et, entity_value=val,
            confidence=conf, matched_person_id=person_id,
            verified_by_user=(conf >= 0.90),
        ))

    cr = CompletenessReport(case_id_fk=case.id, score=68)
    db.add(cr); db.flush()
    for f in ["Suspect 2 identification", "Suspect 3 identification", "Vehicle license plate"]:
        db.add(CompletenessMissingField(
            report_id=cr.id, field_name=f, severity_id=lk.severity("HIGH"),
        ))

    # Leads
    db.add(Lead(
        case_id_fk=case.id, lead_id="LD-2040-01", event_source="AI",
        type_id=lk.lead_type("CASE_LINKAGE"),
        status_id=lk.lead_status("UNDER_REVIEW"),
        severity_id=lk.severity("HIGH"),
        description="Case linkage detected with CASE-2026-2053. Vehicle description and suspect modus operandi overlap significantly.",
        confidence=88.0,
        next_step="Review with case officer of CASE-2026-2053. Consider joint investigation.",
    ))
    db.add(Lead(
        case_id_fk=case.id, lead_id="LD-2040-02", event_source="AI",
        type_id=lk.lead_type("CCTV_ANALYSIS"),
        status_id=lk.lead_status("ACTIONED"),
        severity_id=lk.severity("HIGH"),
        description="CCTV facial geometry of suspect-2 matches Bilal Hussain (88% confidence).",
        confidence=88.0,
        next_step="Confirm via in-person identification by witnesses.",
    ))

    # Timeline (compressed)
    for eid, src, ettype, title, desc, officer, sev, ev_date, ev_time in [
        ("EV-2040-T01", "MANUAL", "FIELD_VISIT", "Crime scene response",
         "Responded to call at HBL Saddar at 14:45. Branch evacuated. Forensic team called.",
         "DSP Imran Shafique", "CRITICAL", date(2026, 10, 20), "14:45"),
        ("EV-2040-T02", "SYSTEM", "FIR_FILED", "FIR Filed: FIR-2026-0876", "FIR filed at Saddar PS.",
         "DSP Imran Shafique", "CRITICAL", date(2026, 10, 20), "15:15"),
        ("EV-2040-T03", "SYSTEM", "EVIDENCE_ADDED", "Evidence Added: Branch CCTV",
         "Full robbery captured. Three suspects clearly visible.",
         "Insp. Rana Ali Khan", "NORMAL", date(2026, 10, 21), "10:00"),
        ("EV-2040-T04", "AI", "AI_LEAD_GENERATED", "Case linkage suggested with CASE-2026-2053",
         "MO and vehicle overlap detected. 88% confidence.", "System (AI)", "HIGH",
         date(2026, 10, 22), "08:00"),
    ]:
        db.add(TimelineEvent(
            case_id_fk=case.id, event_id=eid, event_source=src,
            event_type_id=lk.timeline_event_type(ettype),
            title=title, description=desc, officer_name=officer,
            severity_id=lk.severity(sev),
            event_date=ev_date, event_time=ev_time,
            editable=(src == "MANUAL"),
        ))

    return case


# ════════════════════════════════════════════════════════════════════════════
# CASE 3 — C-2046 — Kidnapping in F-8 Islamabad (no shared suspects)
# ════════════════════════════════════════════════════════════════════════════

def build_case_2046(db, lk: L, investigator_user: User):
    nadia = upsert_person(db,
        cnic="61101-3322110-7",
        full_name="Nadia Arif",
        gender="Female", age=42,
        contact="+92-300-4561234",
        address="House 14, Street 2, F-8/2, Islamabad",
        occupation="Lawyer",
    )

    abducted = upsert_person(db,
        cnic="61101-9988776-5",
        full_name="Aliya Arif",
        gender="Female", age=12,
        address="House 14, Street 2, F-8/2, Islamabad",
        occupation="Student",
    )

    suspect_unknown = upsert_person(db,
        is_unknown=True, full_name="Unknown Suspect (Kidnapper)",
        physical_description="Tall, medium build, dark beard. Drove a silver Honda Civic.",
    )

    case = Case(
        case_id="CASE-2026-2046",
        fir_number="FIR-2026-1108",
        fir_language="Bilingual",
        fir_text_clean="Aliya Arif (12) abducted from her school van pickup point on 5 November 2026. One unknown male suspect in silver Honda Civic.",
        case_type_id=lk.case_type("KIDNAPPING"),
        case_status_id=lk.case_status("ACTIVE"),
        priority_id=lk.severity("CRITICAL"),
        ppc_sections="PPC 364-A, 365",
        case_title="Kidnapping — Aliya Arif (Minor), F-8 Sector",
        description="12-year-old Aliya Arif kidnapped from her school van pickup point in F-8/2. Suspect drove a silver Honda Civic. Family has not received ransom demand as of reporting.",
        incident_date=date(2026, 11, 5),
        incident_time="14:00",
        reporting_date=date(2026, 11, 5),
        reporting_time="14:45",
        reporting_officer="DSP Salman Tariq",
        assigned_investigator_id=investigator_user.investigator.id,
        created_by_id=investigator_user.id,
        complainant_name="Nadia Arif (mother)",
        complainant_contact="+92-300-4561234",
        complainant_cnic="61101-3322110-7",
        vehicle_used="Silver Honda Civic, registration unknown.",
        motive="Unknown — possibly ransom or personal dispute (mother is a high-profile lawyer).",
        modus_operandi="Targeted abduction at predictable school-van pickup point. Suspect knew the daily routine.",
        cctv_available=False,
        crime_description="Minor abducted in broad daylight at school van pickup. Witnesses report the suspect spoke Urdu with a Punjabi accent.",
        progress_percent=25,
    )
    db.add(case); db.flush()

    db.add(Location(
        case_id_fk=case.id,
        province_id=lk.province("ICT"),
        city_id=lk.city("Islamabad", "ICT"),
        area="F-8/2",
        police_station="Margalla Police Station",
        full_address="School Van Pickup Point, Street 2, F-8/2, Islamabad",
        display_address="F-8/2, Islamabad, ICT, Pakistan",
        latitude=33.7100, longitude=73.0500,
        crime_scene_type="Public Space",
        scene_access="Released",
        landmarks="F-8 Markaz – 0.3 km",
    ))

    # Suspect (unknown) and victim (the minor)
    db.add(CaseSuspect(
        case_id_fk=case.id, person_id=suspect_unknown.id, suspect_id="SUS-2046-01",
        status_id=lk.suspect_status("AT_LARGE"),
        relation_to_case="Stranger",
        reason="Witness description; vehicle traced via traffic cameras.",
        notes="Honda Civic search across ICT and Punjab borders activated.",
    ))

    db.add(CaseVictim(
        case_id_fk=case.id, person_id=abducted.id, victim_id="VIC-2046-01",
        status_id=lk.victim_status("MISSING"),
        primary_label="Abducted Minor — Active Search",
        injury_type="Psychological Trauma",
        protection_required=True,
        threat_level_id=lk.severity("CRITICAL"),
        protection_assigned="Family residence under watch.",
        statement="N/A — missing",
    ))

    # Witnesses
    other_kid_parent = upsert_person(db,
        cnic="61101-1100110-3",
        full_name="Sana Tariq",
        gender="Female", age=38,
        contact="+92-321-1100110",
        address="House 16, Street 2, F-8/2, Islamabad",
        occupation="Teacher",
    )
    db.add(CaseWitness(
        case_id_fk=case.id, person_id=other_kid_parent.id, witness_id="WIT-2046-01",
        witness_type_id=lk.witness_type("EYEWITNESS"),
        relation_to_case="Bystander", credibility_id=lk.witness_cred("HIGH"), status="Active",
        statement_date=date(2026, 11, 5),
        statement_recorded_by="Insp. Rana Ali Khan",
        description="Was waiting for her own child at the same pickup. Saw silver Honda Civic stop. Suspect grabbed Aliya and drove off. Estimates 15-second window.",
        cooperating=True,
    ))

    # Evidence
    db.add(Evidence(
        case_id_fk=case.id, evidence_id="EV-2046-01",
        type_id=lk.evidence_type("STATEMENT"),
        description="Witness statement from Sana Tariq.",
        date_collected=date(2026, 11, 5), collected_by="Insp. Rana Ali Khan",
    ))
    db.add(Evidence(
        case_id_fk=case.id, evidence_id="EV-2046-02",
        type_id=lk.evidence_type("DIGITAL"),
        description="Traffic camera footage from F-8 Markaz junction (5 Nov, 14:00–14:30).",
        file_path="evidence/2046/traffic-cam.mp4",
        date_collected=date(2026, 11, 6), collected_by="ICT Traffic Police",
    ))

    # NLP entities
    for et, val, conf, person_id in [
        ("PERSON",   "Aliya Arif",      0.93, abducted.id),
        ("PERSON",   "Nadia Arif",      0.95, nadia.id),
        ("LOCATION", "F-8/2 Islamabad", 0.96, None),
        ("VEHICLE",  "silver Honda Civic", 0.89, None),
        ("DATE",     "5 November 2026", 0.99, None),
    ]:
        db.add(ExtractedEntity(
            case_id_fk=case.id, entity_type=et, entity_value=val,
            confidence=conf, matched_person_id=person_id,
        ))

    cr = CompletenessReport(case_id_fk=case.id, score=55)
    db.add(cr); db.flush()
    for f in ["Suspect identification", "Vehicle registration plate", "Ransom contact"]:
        db.add(CompletenessMissingField(
            report_id=cr.id, field_name=f, severity_id=lk.severity("CRITICAL"),
        ))

    db.add(Lead(
        case_id_fk=case.id, lead_id="LD-2046-01", event_source="AI",
        type_id=lk.lead_type("VEHICLE"),
        status_id=lk.lead_status("IN_PROGRESS"),
        severity_id=lk.severity("CRITICAL"),
        description="Silver Honda Civic detected at F-8 Markaz traffic camera at 14:08. Partial plate captured: ICT-***-AB.",
        confidence=72.0,
        next_step="Run partial plate against ICT vehicle registry.",
    ))

    for eid, src, ettype, title, desc, officer, sev, ev_date, ev_time in [
        ("EV-2046-T01", "MANUAL", "FIELD_VISIT", "Pickup point inspection",
         "Inspected school van pickup point. Took witness statements from 3 parents.",
         "Insp. Rana Ali Khan", "CRITICAL", date(2026, 11, 5), "15:00"),
        ("EV-2046-T02", "SYSTEM", "FIR_FILED", "FIR Filed: FIR-2026-1108", "Filed at Margalla PS.",
         "DSP Salman Tariq", "CRITICAL", date(2026, 11, 5), "14:45"),
        ("EV-2046-T03", "SYSTEM", "EVIDENCE_ADDED", "Evidence Added: Traffic cam footage",
         "F-8 Markaz junction camera shows Honda Civic.",
         "ICT Traffic Police", "HIGH", date(2026, 11, 6), "11:00"),
    ]:
        db.add(TimelineEvent(
            case_id_fk=case.id, event_id=eid, event_source=src,
            event_type_id=lk.timeline_event_type(ettype),
            title=title, description=desc, officer_name=officer,
            severity_id=lk.severity(sev),
            event_date=ev_date, event_time=ev_time,
            editable=(src == "MANUAL"),
        ))

    return case


# ════════════════════════════════════════════════════════════════════════════
# CASE 4 — C-2044 — Cybercrime / Data Breach (standalone, low-link case)
# ════════════════════════════════════════════════════════════════════════════

def build_case_2044(db, lk: L, investigator_user: User):
    cto = upsert_person(db,
        cnic="35202-1010101-1",
        full_name="Hammad Raza",
        gender="Male", age=37,
        contact="+92-301-7777777",
        address="DHA Phase 6, Lahore",
        occupation="CTO, TechCorp Ltd.",
    )

    case = Case(
        case_id="CASE-2026-2044",
        fir_number="FIR-2026-1054",
        fir_language="English",
        fir_text_clean="TechCorp Ltd reports unauthorized access to customer database. ~120,000 customer records exfiltrated. Discovered by internal SOC team on 1 November 2026.",
        case_type_id=lk.case_type("FRAUD"),
        case_status_id=lk.case_status("PENDING"),
        priority_id=lk.severity("HIGH"),
        ppc_sections="PECA 2016 §3, §4, §14",
        case_title="Cybercrime — TechCorp Data Breach",
        description="Unauthorized intrusion into TechCorp's customer database. Approximately 120,000 records (names, emails, phone numbers, hashed passwords) exfiltrated. Initial forensics suggest credentials theft via phishing.",
        incident_date=date(2026, 10, 28),
        incident_time="03:00",
        reporting_date=date(2026, 11, 1),
        reporting_time="10:00",
        reporting_officer="DSP Khurram Iqbal (FIA Cyber Crime Wing)",
        assigned_investigator_id=investigator_user.investigator.id,
        created_by_id=investigator_user.id,
        complainant_name="TechCorp Ltd. (CTO: Hammad Raza)",
        complainant_contact="+92-301-7777777",
        motive="Financial Gain — likely sale of records on dark web.",
        modus_operandi="Credential theft via phishing → lateral movement → bulk export. Sophisticated operation.",
        cctv_available=False,
        crime_description="Database access logs show 4-hour exfiltration window starting 02:55 on 28 Oct. Exit point: ProtonVPN exit node in Romania.",
        progress_percent=15,
    )
    db.add(case); db.flush()

    db.add(Location(
        case_id_fk=case.id,
        province_id=lk.province("PUNJAB"),
        city_id=lk.city("Lahore", "PUNJAB"),
        area="DHA Phase 6",
        police_station="FIA Cyber Crime Wing, Lahore",
        full_address="TechCorp HQ, DHA Phase 6, Lahore",
        display_address="DHA Phase 6, Lahore, Punjab, Pakistan",
        latitude=31.4697, longitude=74.4076,
        crime_scene_type="Commercial – Indoor",
        scene_access="Restricted Access",
    ))

    db.add(CaseVictim(
        case_id_fk=case.id, person_id=cto.id, victim_id="VIC-2044-01",
        status_id=lk.victim_status("ALIVE"),
        primary_label="Reporting Party — Corporate Victim Representative",
        statement="Discovered intrusion via SOC anomaly alert at 06:00 on 1 Nov. Confirmed exfiltration via egress logs. Customer notification underway.",
        cooperative=True,
    ))

    db.add(Evidence(
        case_id_fk=case.id, evidence_id="EV-2044-01",
        type_id=lk.evidence_type("DIGITAL"),
        description="Database access logs (28 Oct 02:00–07:00).",
        file_path="evidence/2044/db-access-logs.csv",
        date_collected=date(2026, 11, 1), collected_by="TechCorp SOC team",
    ))
    db.add(Evidence(
        case_id_fk=case.id, evidence_id="EV-2044-02",
        type_id=lk.evidence_type("DIGITAL"),
        description="Phishing email sample (sent to 47 employees).",
        file_path="evidence/2044/phishing-email.eml",
        date_collected=date(2026, 11, 2), collected_by="TechCorp SOC team",
    ))

    for et, val, conf in [
        ("PERSON",   "Hammad Raza",   0.92),
        ("ORG",      "TechCorp Ltd.", 0.95),
        ("LOCATION", "Lahore",        0.99),
        ("DATE",     "28 October 2026", 0.99),
        ("ORG",      "ProtonVPN",     0.84),
    ]:
        db.add(ExtractedEntity(
            case_id_fk=case.id, entity_type=et, entity_value=val,
            confidence=conf,
            matched_person_id=cto.id if val == "Hammad Raza" else None,
        ))

    cr = CompletenessReport(case_id_fk=case.id, score=42)
    db.add(cr); db.flush()
    for f in ["Suspect identification", "VPN exit node attribution", "Sample of leaked records"]:
        db.add(CompletenessMissingField(
            report_id=cr.id, field_name=f, severity_id=lk.severity("HIGH"),
        ))

    db.add(Lead(
        case_id_fk=case.id, lead_id="LD-2044-01", event_source="MANUAL",
        type_id=lk.lead_type("PHONE_DIGITAL"),
        status_id=lk.lead_status("NEW"),
        severity_id=lk.severity("HIGH"),
        description="ProtonVPN exit IP requested via international cyber-cooperation channel.",
        next_step="Awaiting MLAT response from Romanian authorities.",
        created_by_user_id=investigator_user.id,
    ))

    for eid, src, ettype, title, desc, officer, sev, ev_date, ev_time in [
        ("EV-2044-T01", "SYSTEM", "FIR_FILED", "FIR Filed: FIR-2026-1054", "Filed at FIA Cyber Crime Wing.",
         "DSP Khurram Iqbal", "HIGH", date(2026, 11, 1), "10:00"),
        ("EV-2044-T02", "MANUAL", "EVIDENCE_COLLECTION", "Database logs collected",
         "Captured 5 hours of database access logs.", "Insp. Rana Ali Khan", "NORMAL",
         date(2026, 11, 1), "14:00"),
        ("EV-2044-T03", "MANUAL", "FIELD_VISIT", "Onsite at TechCorp HQ",
         "Met with SOC team. Reviewed network topology and segmentation.",
         "Insp. Rana Ali Khan", "NORMAL", date(2026, 11, 2), "11:00"),
    ]:
        db.add(TimelineEvent(
            case_id_fk=case.id, event_id=eid, event_source=src,
            event_type_id=lk.timeline_event_type(ettype),
            title=title, description=desc, officer_name=officer,
            severity_id=lk.severity(sev),
            event_date=ev_date, event_time=ev_time,
            editable=(src == "MANUAL"),
        ))

    return case


# ════════════════════════════════════════════════════════════════════════════
# CASE LINKS — connecting C-2053 ↔ C-2040 via shared suspect & MO
# ════════════════════════════════════════════════════════════════════════════

def build_case_links(db, lk: L, c2053: Case, c2040: Case):
    bilal = db.query(Person).filter_by(cnic="35201-1234567-1").first()

    link = CaseLink(
        source_case_id=c2053.id, target_case_id=c2040.id,
        link_type="SUSPECT_MATCH",
        similarity_score=0.91,
        explanation="Bilal Hussain (CNIC 35201-1234567-1) is a registered suspect in both cases. Vehicle description (white Toyota Corolla) and modus operandi (financial motive, weapon-based) overlap.",
    )
    db.add(link); db.flush()

    db.add(CaseLinkSharedEntity(
        link_id=link.id, entity_type="PERSON",
        entity_value="Bilal Hussain", person_id=bilal.id,
    ))
    db.add(CaseLinkSharedEntity(
        link_id=link.id, entity_type="VEHICLE",
        entity_value="White Toyota Corolla",
    ))

    # Reverse direction (target ↔ source)
    link2 = CaseLink(
        source_case_id=c2040.id, target_case_id=c2053.id,
        link_type="MODUS_MATCH",
        similarity_score=0.88,
        explanation="MO clustering algorithm flagged both cases for vehicle reuse and financial motive.",
    )
    db.add(link2); db.flush()

    db.add(CaseLinkSharedEntity(
        link_id=link2.id, entity_type="MOTIVE",
        entity_value="Financial Gain",
    ))


# ════════════════════════════════════════════════════════════════════════════
# Notifications + Audit Logs (general activity feed)
# ════════════════════════════════════════════════════════════════════════════

def build_notifications_and_audit(db, lk: L, investigator_user: User, cases: list):
    """Add a few notifications for the investigator and seed audit log entries
    for the demo activity feed."""
    c2053, c2040, c2046, c2044 = cases

    notif_specs = [
        ("NTF-001", "AI_LEAD",      "HIGH",     "New AI lead on CASE-2026-2053",
         "Fingerprint match (95% confidence) detected against suspect record.", c2053),
        ("NTF-002", "CASE_LINKED",  "HIGH",     "Cross-case linkage detected",
         "CASE-2026-2053 may be linked to CASE-2026-2040 (shared suspect: Bilal Hussain).", c2053),
        ("NTF-003", "CASE_UPDATE",  "NORMAL",   "Case status changed",
         "CASE-2026-2053 status updated from Open to Under Investigation.", c2053),
        ("NTF-004", "CASE_ASSIGNED","NORMAL",   "Case assigned to you",
         "CASE-2026-2046 has been assigned to your investigative team.", c2046),
        ("NTF-005", "AI_LEAD",      "CRITICAL", "Critical AI lead — vehicle match",
         "Silver Honda Civic from CASE-2026-2046 spotted on F-8 traffic camera.", c2046),
        ("NTF-006", "SYSTEM_ALERT", "NORMAL",   "Backup completed",
         "Database backup completed successfully at 02:00.", None),
    ]
    is_read_pattern = [True, False, True, False, False, True]
    for (nid, ntype, sev, title, msg, case), is_read in zip(notif_specs, is_read_pattern):
        db.add(Notification(
            notification_id=nid,
            user_id=investigator_user.id,
            type=ntype,
            severity_id=lk.severity(sev),
            title=title, message=msg,
            link_url=f"/investigator/case?id={case.case_id}" if case else None,
            related_case_id=case.id if case else None,
            is_read=is_read,
            read_at=datetime.now(UTC) if is_read else None,
        ))

    # Audit logs — sample of recent activity
    audit_specs = [
        ("LOG-1001", "LOGIN",  "Authentication",  "User logged in successfully",       None,        None,        "Success"),
        ("LOG-1002", "CREATE", "Case Management", f"Created case {c2053.case_id}",     "case",      c2053.case_id, "Success"),
        ("LOG-1003", "VIEW",   "Case Management", f"Viewed case {c2053.case_id}",       "case",      c2053.case_id, "Success"),
        ("LOG-1004", "UPDATE", "Case Management", f"Updated case {c2053.case_id}",     "case",      c2053.case_id, "Success"),
        ("LOG-1005", "CREATE", "Evidence",        "Added evidence EV-2053-06 (fingerprint)", "evidence", "EV-2053-06", "Success"),
        ("LOG-1006", "CREATE", "Case Management", f"Created case {c2040.case_id}",     "case",      c2040.case_id, "Success"),
        ("LOG-1007", "EXPORT", "Reports",         "Exported Case Summary as PDF",      "report",    None,        "Success"),
        ("LOG-1008", "VIEW",   "Audit",           "Viewed audit logs",                 None,        None,        "Success"),
        ("LOG-1009", "BACKUP", "Database",        "Database backup completed",         None,        None,        "Success"),
        ("LOG-1010", "LOGIN",  "Authentication",  "Failed login attempt",              None,        None,        "Failed"),
    ]
    for log_id, action, module, detail, ttype, tid, status in audit_specs:
        db.add(AuditLog(
            log_id=log_id,
            user_id=investigator_user.id,
            action=action, module=module, detail=detail,
            target_type=ttype, target_id=tid,
            ip_address="192.168.1.10", machine="PC-101",
            status=status,
        ))


# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════

def seed_demo():
    print("\n=== Seeding demo cases ===\n")

    with session_scope() as db:
        lk = L(db)

        # Sanity check
        investigator = (
            db.query(User)
              .filter_by(username="wajdan.mustafa")
              .first()
        )
        if not investigator:
            raise RuntimeError("wajdan.mustafa user not found. Run seed_users.py first.")

        # Idempotency check — if any of our case_ids exist, skip
        existing = db.query(Case).filter(Case.case_id.like("CASE-2026-%")).count()
        if existing > 0:
            print(f"  Demo cases already exist ({existing} cases found). Skipping.")
            print("  (Delete them manually if you want to re-seed: DELETE FROM cases WHERE case_id LIKE 'CASE-2026-%';)")
            return

        c2053 = build_case_2053(db, lk, investigator)
        print(f"  + {c2053.case_id} — {c2053.case_title}")

        c2040 = build_case_2040(db, lk, investigator)
        print(f"  + {c2040.case_id} — {c2040.case_title}")

        c2046 = build_case_2046(db, lk, investigator)
        print(f"  + {c2046.case_id} — {c2046.case_title}")

        c2044 = build_case_2044(db, lk, investigator)
        print(f"  + {c2044.case_id} — {c2044.case_title}")

        build_case_links(db, lk, c2053, c2040)
        print(f"  + Case links: {c2053.case_id} ↔ {c2040.case_id}")

        build_notifications_and_audit(db, lk, investigator, [c2053, c2040, c2046, c2044])
        print(f"  + 6 notifications + 10 audit log entries")

    print("\n✓ Demo seed complete.\n")


if __name__ == "__main__":
    seed_demo()