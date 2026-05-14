from datetime import UTC, datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float, DateTime, Date,
    ForeignKey, JSON, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

# SECTION 1 — LOOKUP TABLES
class Province(Base):
    __tablename__ = "lkp_provinces"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

    cities = relationship("City", back_populates="province", passive_deletes=True)

class City(Base):
    """City belongs to Province. RESTRICT — deleting a province with cities
    would leave the cities orphaned, which is meaningless."""
    __tablename__ = "lkp_cities"
    id          = Column(Integer, primary_key=True)
    province_id = Column(
        Integer,
        ForeignKey("lkp_provinces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name        = Column(String(120), nullable=False)
    sort_order  = Column(Integer, default=0)
    active      = Column(Boolean, default=True)

    province = relationship("Province", back_populates="cities")

    __table_args__ = (UniqueConstraint("province_id", "name", name="uq_city_province"),)

class CaseType(Base):
    __tablename__ = "lkp_case_types"
    id            = Column(Integer, primary_key=True)
    code          = Column(String(40), unique=True, nullable=False)
    label         = Column(String(80), nullable=False)
    subtype_table = Column(String(40), nullable=True)
    sort_order    = Column(Integer, default=0)
    active        = Column(Boolean, default=True)

class Weapon(Base):
    __tablename__ = "lkp_weapons"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    category   = Column(String(40), nullable=True)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

class EvidenceType(Base):
    __tablename__ = "lkp_evidence_types"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    is_digital = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

class CaseStatus(Base):
    __tablename__ = "lkp_case_statuses"
    id          = Column(Integer, primary_key=True)
    code        = Column(String(40), unique=True, nullable=False)
    label       = Column(String(80), nullable=False)
    is_terminal = Column(Boolean, default=False)
    sort_order  = Column(Integer, default=0)
    active      = Column(Boolean, default=True)

class SuspectStatus(Base):
    __tablename__ = "lkp_suspect_statuses"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

class VictimStatus(Base):
    __tablename__ = "lkp_victim_statuses"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

class WitnessType(Base):
    __tablename__ = "lkp_witness_types"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

class WitnessCredibility(Base):
    """WITNESS_CREDIBILITY constant
    (High — Corroborated / Medium — Unverified / Low — Contradicted / Unknown)."""
    __tablename__ = "lkp_witness_credibility"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

class Severity(Base):
    __tablename__ = "lkp_severities"
    id        = Column(Integer, primary_key=True)
    code      = Column(String(40), unique=True, nullable=False)
    label     = Column(String(40), nullable=False)
    rank      = Column(Integer, nullable=False)
    color_hex = Column(String(7), nullable=True)

class LeadType(Base):
    __tablename__ = "lkp_lead_types"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

class LeadStatus(Base):
    __tablename__ = "lkp_lead_statuses"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(40), nullable=False)
    sort_order = Column(Integer, default=0)

class TimelineEventType(Base):
    __tablename__ = "lkp_timeline_event_types"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    is_system  = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)

class Permission(Base):
    __tablename__ = "lkp_permissions"
    id          = Column(Integer, primary_key=True)
    code        = Column(String(80), unique=True, nullable=False)
    label       = Column(String(120), nullable=False)
    description = Column(String(255), nullable=True)
    module      = Column(String(40), nullable=False)

class TicketStatus(Base):
    __tablename__ = "lkp_ticket_statuses"

    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)  # OPEN / IN_PROGRESS / RESOLVED / CLOSED
    label      = Column(String(80), nullable=False)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

# SECTION 2 — USERS & ROLES
class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    badge_number  = Column(String(40), unique=True, index=True)
    username      = Column(String(80), unique=True, index=True, nullable=False)
    email         = Column(String(120), unique=True, index=True, nullable=False)
    password      = Column(String(255), nullable=False)
    status        = Column(String(20), default="active")
    contact_info  = Column(String(60), nullable=True)
    address       = Column(String(255), nullable=True)
    role          = Column(String(20), nullable=False)
    picture_url   = Column(String(500), nullable=True)

    last_login         = Column(DateTime, nullable=True)
    failed_login_count = Column(Integer, default=0)
    locked_until       = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    investigator = relationship(
        "Investigator", back_populates="user", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True,
    )
    admin = relationship(
        "Admin", back_populates="user", uselist=False,
        cascade="all, delete-orphan", passive_deletes=True,
    )
    user_roles = relationship(
        "UserRole", back_populates="user",
        cascade="all, delete-orphan", passive_deletes=True,
    )

class Investigator(Base):
    """1:1 with User. CASCADE — investigator role evaporates with the user."""
    __tablename__ = "investigators"

    id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    department     = Column(String(80))
    rank           = Column(String(40))
    shift          = Column(String(20), nullable=True)
    specialization = Column(String(80), nullable=True)

    user = relationship("User", back_populates="investigator")

class Admin(Base):
    """1:1 with User. Same CASCADE reasoning."""
    __tablename__ = "admins"

    id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    admin_level = Column(String(40))

    user = relationship("User", back_populates="admin")

class UserRole(Base):
    __tablename__ = "user_roles"

    id        = Column(Integer, primary_key=True, index=True)
    role_name = Column(String(40), nullable=False)
    user_id   = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    user = relationship("User", back_populates="user_roles")
    permissions = relationship(
        "UserRolePermission", back_populates="user_role",
        cascade="all, delete-orphan", passive_deletes=True,
    )

class UserRolePermission(Base):
    """M:N junction. RESTRICT on Permission (don't let admins delete a
    permission that's currently granted to anyone)."""
    __tablename__ = "user_role_permissions"

    user_role_id = Column(
        Integer,
        ForeignKey("user_roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id = Column(
        Integer,
        ForeignKey("lkp_permissions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    granted_at = Column(DateTime, default=datetime.now(UTC))

    user_role  = relationship("UserRole", back_populates="permissions")
    permission = relationship("Permission")

class UserSession(Base):
    __tablename__ = "user_sessions"

    id          = Column(Integer, primary_key=True)
    session_id  = Column(String(50), unique=True, index=True, nullable=False)

    user_id     = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    token_hash  = Column(String(64), unique=True, index=True, nullable=False)

    ip_address  = Column(String(45), nullable=True)
    user_agent  = Column(String(255), nullable=True)
    machine     = Column(String(60), nullable=True)
    long_lived  = Column(Boolean, default=False)   # "Remember Me" flag

    started_at  = Column(DateTime, default=datetime.now(UTC))
    last_seen_at= Column(DateTime, default=datetime.now(UTC))
    expires_at  = Column(DateTime, nullable=False)
    revoked_at  = Column(DateTime, nullable=True)
    revoke_reason = Column(String(60), nullable=True)
    # ↑ "logout" / "expired" / "admin_revoked" / "password_changed"

    user = relationship("User")

    __table_args__ = (
        Index("ix_session_user_active", "user_id", "revoked_at"),
    )

class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id         = Column(Integer, primary_key=True)
    user_id    = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Store the SHA-256 of the token, not the token itself — same as how
    # passwords are hashed. Token is emailed/shown to user once.
    token_hash = Column(String(64), unique=True, index=True, nullable=False)

    expires_at = Column(DateTime, nullable=False)
    used_at    = Column(DateTime, nullable=True)
    requested_ip = Column(String(45), nullable=True)

    created_at = Column(DateTime, default=datetime.now(UTC))

    user = relationship("User")

class UserPreference(Base):
    __tablename__ = "user_preferences"

    id      = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Examples of pref_key:
    #   email_notifications, case_update_alerts, ai_lead_notifications,
    #   sound_alerts, compact_view, auto_save_drafts
    pref_key   = Column(String(60), nullable=False)
    pref_value = Column(String(255), nullable=False)
    # ↑ stored as string so booleans/numbers/strings all fit; cast on read.

    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("user_id", "pref_key", name="uq_user_pref"),
    )

# SECTION 3 — PERSON
class Person(Base):
    __tablename__ = "persons"

    id            = Column(Integer, primary_key=True, index=True)
    cnic          = Column(String(20), unique=True, index=True, nullable=True)
    full_name     = Column(String(150), nullable=True)
    gender        = Column(String(20),  nullable=True)
    age           = Column(Integer, nullable=True)
    date_of_birth = Column(Date, nullable=True)
    contact       = Column(String(30), nullable=True)
    address       = Column(Text, nullable=True)
    occupation    = Column(String(120), nullable=True)
    physical_description = Column(Text, nullable=True)
    is_unknown    = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    # Reverse only — no cascade. RESTRICT lives on the FK side.
    suspect_roles = relationship("CaseSuspect", back_populates="person")
    victim_roles  = relationship("CaseVictim",  back_populates="person")
    witness_roles = relationship("CaseWitness", back_populates="person")

    photo = relationship("PersonPhoto", back_populates="person", uselist=False) 
    
    __table_args__ = (Index("ix_person_name", "full_name"),)

class PersonPhoto(Base):
    __tablename__ = "person_photos"

    id        = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.id", ondelete="CASCADE"), unique=True, nullable=False)  # unique = one photo per person
    file_path = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=True)
    file_mime = Column(String(80),  nullable=True)
    file_size = Column(Integer,     nullable=True)
    uploaded_at = Column(DateTime, default=datetime.now(UTC))

    person = relationship("Person", back_populates="photo")

# SECTION 4 — CASE
class Case(Base):
    __tablename__ = "cases"

    id          = Column(Integer, primary_key=True, index=True)
    case_id     = Column(String(50), unique=True, index=True, nullable=False)
    fir_number  = Column(String(50), unique=True, index=True, nullable=False)

    fir_file_path  = Column(String(500), nullable=True)
    fir_file_name  = Column(String(255), nullable=True)
    fir_language   = Column(String(20),  default="English")
    fir_text_raw   = Column(Text, nullable=True)
    fir_text_clean = Column(Text, nullable=True)
    manual_entry   = Column(Boolean, default=False)

    case_type_id   = Column(
        Integer,
        ForeignKey("lkp_case_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    case_status_id = Column(
        Integer,
        ForeignKey("lkp_case_statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    priority_id    = Column(
        Integer,
        ForeignKey("lkp_severities.id", ondelete="RESTRICT"),
        nullable=False,
    )

    case_title    = Column(String(255), nullable=False)
    ppc_sections  = Column(String(255), nullable=True)
    description   = Column(Text, nullable=False)

    incident_date  = Column(Date, nullable=False)
    incident_time  = Column(String(10), nullable=True)
    reporting_date = Column(Date, nullable=False)
    reporting_time = Column(String(10), nullable=True)

    reporting_officer = Column(String(150), nullable=True)
    complainant_name    = Column(String(150), nullable=True)
    complainant_contact = Column(String(60),  nullable=True)
    complainant_cnic    = Column(String(20),  nullable=True)

    assigned_investigator_id = Column(
        Integer,
        ForeignKey("investigators.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    weapon_id = Column(
        Integer,
        ForeignKey("lkp_weapons.id", ondelete="RESTRICT"),
        nullable=True,
    )
    weapon_description = Column(Text, nullable=True)
    vehicle_used       = Column(String(255), nullable=True)
    motive             = Column(Text, nullable=True)
    modus_operandi     = Column(Text, nullable=True)
    cctv_available     = Column(Boolean, default=False)
    crime_description  = Column(Text, nullable=True)

    progress_percent  = Column(Integer, default=0)
    next_hearing_date = Column(Date, nullable=True)

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))
    closed_at  = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False)

    case_type   = relationship("CaseType")
    case_status = relationship("CaseStatus")
    priority    = relationship("Severity")
    weapon      = relationship("Weapon")
    assigned_to = relationship("Investigator", foreign_keys=[assigned_investigator_id])
    created_by  = relationship("User",         foreign_keys=[created_by_id])

    location = relationship(
        "Location", uselist=False, back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    murder_details = relationship(
        "MurderDetails", uselist=False, back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    sa_details = relationship(
        "SexualAssaultDetails", uselist=False, back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    theft_details = relationship(
        "TheftDetails", uselist=False, back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    suspects = relationship(
        "CaseSuspect", back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    victims = relationship(
        "CaseVictim", back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    witnesses = relationship(
        "CaseWitness", back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    evidences = relationship(
        "Evidence", back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    extracted_entities = relationship(
        "ExtractedEntity", back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    leads = relationship(
        "Lead", back_populates="case",
        foreign_keys="Lead.case_id_fk",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    timeline_events = relationship(
        "TimelineEvent", back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    completeness_report = relationship(
        "CompletenessReport", uselist=False, back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    update_notes = relationship(
        "CaseUpdateNote", back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    investigation_notes = relationship(
        "InvestigationNote", back_populates="case",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    linked_cases_out = relationship(
        "CaseLink", foreign_keys="CaseLink.source_case_id",
        back_populates="source_case",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    linked_cases_in = relationship(
        "CaseLink", foreign_keys="CaseLink.target_case_id",
        back_populates="target_case",
    )
    activities = relationship(
        "Activity", back_populates="case", cascade="all, delete-orphan"
    )
    
# SECTION 5 — CRIME-TYPE SUBTYPES (1:1 with Case)
class MurderDetails(Base):
    __tablename__ = "murder_details"

    id              = Column(Integer, primary_key=True)
    case_id_fk      = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )

    cause_of_death  = Column(String(80), nullable=True)
    body_location   = Column(String(80), nullable=True)
    time_of_death   = Column(String(50), nullable=True)
    postmortem_done = Column(Boolean, default=False)
    forensic_done   = Column(Boolean, default=False)

    case = relationship("Case", back_populates="murder_details")

class SexualAssaultDetails(Base):
    __tablename__ = "sexual_assault_details"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )

    medical_exam       = Column(String(80), nullable=True)
    victim_counseling  = Column(Boolean, default=False)
    protection_order   = Column(Boolean, default=False)
    confidential_notes = Column(Text, nullable=True)

    case = relationship("Case", back_populates="sa_details")

class TheftDetails(Base):
    __tablename__ = "theft_details"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )

    stolen_items    = Column(Text, nullable=True)
    stolen_value    = Column(Float, nullable=True)
    recovery_status = Column(String(40), nullable=True)
    entry_point     = Column(String(40), nullable=True)

    case = relationship("Case", back_populates="theft_details")

# SECTION 6 — LOCATION (1:1 with Case)
class Location(Base):
    __tablename__ = "locations"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )

    province_id = Column(
        Integer,
        ForeignKey("lkp_provinces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    city_id = Column(
        Integer,
        ForeignKey("lkp_cities.id", ondelete="RESTRICT"),
        nullable=False,
    )
    area            = Column(String(120), nullable=True)
    police_station  = Column(String(150), nullable=True)
    full_address    = Column(Text, nullable=False)
    display_address = Column(Text, nullable=True)

    latitude  = Column(Float, nullable=True, index=True)
    longitude = Column(Float, nullable=True, index=True)

    crime_scene_type = Column(String(80), nullable=True)
    scene_access     = Column(String(80), default="Secured by Police")
    landmarks        = Column(Text, nullable=True)

    case     = relationship("Case", back_populates="location")
    province = relationship("Province")
    city     = relationship("City")

# SECTION 7 — JUNCTION TABLES (Person × Case)
class CaseSuspect(Base):
    __tablename__ = "case_suspects"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    suspect_id = Column(String(50), unique=True, index=True, nullable=False)

    status_id = Column(
        Integer,
        ForeignKey("lkp_suspect_statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    relation_to_case    = Column(String(60), nullable=True)
    reason              = Column(Text, nullable=True)
    alibi               = Column(Text, nullable=True)
    known_affiliations  = Column(String(255), nullable=True)
    arrival_method      = Column(String(60), nullable=True)
    vehicle_description = Column(String(255), nullable=True)
    criminal_record     = Column(Boolean, default=False)
    arrested            = Column(Boolean, default=False)
    notes               = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    case   = relationship("Case",   back_populates="suspects")
    person = relationship("Person", back_populates="suspect_roles")
    status = relationship("SuspectStatus")

    __table_args__ = (
        UniqueConstraint("case_id_fk", "person_id", name="uq_case_suspect"),
    )

class CaseVictim(Base):
    __tablename__ = "case_victims"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    victim_id = Column(String(50), unique=True, index=True, nullable=False)

    status_id = Column(
        Integer,
        ForeignKey("lkp_victim_statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    primary_label       = Column(String(120), nullable=True)
    relation_to_suspect = Column(String(60), nullable=True)
    injury_type         = Column(String(60), nullable=True)
    nature_of_injuries  = Column(Text, nullable=True)
    cause_of_death      = Column(String(255), nullable=True)
    declared_dead       = Column(String(60), nullable=True)
    postmortem_autopsy  = Column(String(60), nullable=True)
    injury_summary      = Column(Text, nullable=True)
    injury_recorded_by  = Column(String(150), nullable=True)
    statement           = Column(Text, nullable=True)

    medical_report      = Column(Boolean, default=False)
    postmortem          = Column(Boolean, default=False)
    protection_required = Column(Boolean, default=False)
    cooperative         = Column(Boolean, default=True)

    threat_level_id = Column(
        Integer,
        ForeignKey("lkp_severities.id", ondelete="RESTRICT"),
        nullable=True,
    )
    protection_assigned = Column(String(255), nullable=True)
    protection_notes    = Column(Text, nullable=True)
    next_follow_up      = Column(Date, nullable=True)

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    case         = relationship("Case",         back_populates="victims")
    person       = relationship("Person",       back_populates="victim_roles")
    status       = relationship("VictimStatus")
    threat_level = relationship("Severity")

    forensic_findings = relationship(
        "VictimForensicFinding", back_populates="case_victim",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    timeline_entries = relationship(
        "VictimTimelineEntry", back_populates="case_victim",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    legal_milestones = relationship(
        "VictimLegalMilestone", back_populates="case_victim",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("case_id_fk", "person_id", name="uq_case_victim"),
    )

class CaseWitness(Base):
    __tablename__ = "case_witnesses"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="RESTRICT"),
        nullable=False,
    )
    credibility_id = Column(
          Integer,
          ForeignKey("lkp_witness_credibility.id", ondelete="RESTRICT"),
          nullable=True,
      )
    witness_id = Column(String(50), unique=True, index=True, nullable=False)

    witness_type_id = Column(
        Integer,
        ForeignKey("lkp_witness_types.id", ondelete="RESTRICT"),
        nullable=True,
    )
    relation_to_case = Column(String(40), nullable=True)
    credibility      = Column(String(40), nullable=True)
    status           = Column(String(40), default="Active")

    statement_date        = Column(Date, default=datetime.now(UTC))
    statement_recorded_by = Column(String(150), nullable=True)
    description           = Column(Text, nullable=True)

    anonymous           = Column(Boolean, default=False)
    protection_required = Column(Boolean, default=False)
    cooperating         = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    case         = relationship("Case",        back_populates="witnesses")
    person       = relationship("Person",      back_populates="witness_roles")
    witness_type = relationship("WitnessType")
    credibility = relationship("WitnessCredibility")

    __table_args__ = (
        UniqueConstraint("case_id_fk", "person_id", name="uq_case_witness"),
    )

# SECTION 8 — VICTIM CHILD TABLES
class VictimForensicFinding(Base):
    __tablename__ = "victim_forensic_findings"

    id             = Column(Integer, primary_key=True)
    case_victim_id = Column(
        Integer,
        ForeignKey("case_victims.id", ondelete="CASCADE"),
        nullable=False,
    )
    finding_text = Column(Text, nullable=False)
    recorded_at  = Column(DateTime, default=datetime.now(UTC))

    case_victim = relationship("CaseVictim", back_populates="forensic_findings")

class VictimTimelineEntry(Base):
    __tablename__ = "victim_timeline_entries"

    id             = Column(Integer, primary_key=True)
    case_victim_id = Column(
        Integer,
        ForeignKey("case_victims.id", ondelete="CASCADE"),
        nullable=False,
    )
    entry_date = Column(Date, nullable=False)
    entry_text = Column(Text, nullable=False)

    case_victim = relationship("CaseVictim", back_populates="timeline_entries")

class VictimLegalMilestone(Base):
    __tablename__ = "victim_legal_milestones"

    id             = Column(Integer, primary_key=True)
    case_victim_id = Column(
        Integer,
        ForeignKey("case_victims.id", ondelete="CASCADE"),
        nullable=False,
    )
    label        = Column(String(255), nullable=False)
    done         = Column(Boolean, default=False)
    completed_at = Column(DateTime, nullable=True)

    case_victim = relationship("CaseVictim", back_populates="legal_milestones")

# SECTION 9 — EVIDENCE
class Evidence(Base):
    __tablename__ = "evidences"

    id          = Column(Integer, primary_key=True)
    case_id_fk  = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_id = Column(String(50), unique=True, index=True, nullable=False)

    type_id = Column(
        Integer,
        ForeignKey("lkp_evidence_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    description = Column(Text, nullable=True)

    file_path   = Column(String(500), nullable=True)
    file_name   = Column(String(255), nullable=True)
    file_mime   = Column(String(80),  nullable=True)
    file_size   = Column(Integer, nullable=True)
    sha256_hash = Column(String(64), nullable=True)

    date_collected = Column(Date, nullable=True)
    collected_by   = Column(String(150), nullable=True)

    created_at = Column(DateTime, default=datetime.now(UTC))
    updated_at = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    case   = relationship("Case",         back_populates="evidences")
    type   = relationship("EvidenceType")
    photos = relationship(
        "EvidencePhoto", back_populates="evidence",
        cascade="all, delete-orphan", passive_deletes=True,
    )

class EvidencePhoto(Base):
    __tablename__ = "evidence_photos"

    id          = Column(Integer, primary_key=True)
    evidence_id = Column(
        Integer,
        ForeignKey("evidences.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path   = Column(String(500), nullable=False)
    file_name   = Column(String(255), nullable=True)
    file_mime   = Column(String(80),  nullable=True)
    file_size   = Column(Integer, nullable=True)
    caption     = Column(Text, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.now(UTC))

    evidence = relationship("Evidence", back_populates="photos")

# SECTION 10 — AI MODULE
class ExtractedEntity(Base):
    __tablename__ = "extracted_entities"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )

    entity_type  = Column(String(40), nullable=False, index=True)
    entity_value = Column(String(255), nullable=False, index=True)
    confidence   = Column(Float, default=0.0)
    char_start   = Column(Integer, nullable=True)
    char_end     = Column(Integer, nullable=True)

    flagged_for_review   = Column(Boolean, default=False)
    verified_by_user     = Column(Boolean, default=False)
    user_corrected_value = Column(String(255), nullable=True)

    matched_person_id = Column(
        Integer,
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
    )

    extracted_at = Column(DateTime, default=datetime.now(UTC))

    case           = relationship("Case",   back_populates="extracted_entities")
    matched_person = relationship("Person")

    __table_args__ = (Index("ix_entity_type_value", "entity_type", "entity_value"),)

class CompletenessReport(Base):
    __tablename__ = "completeness_reports"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        unique=True, nullable=False,
    )
    score       = Column(Integer, default=0)
    last_run_at = Column(DateTime, default=datetime.now(UTC))

    case = relationship("Case", back_populates="completeness_report")
    missing_fields = relationship(
        "CompletenessMissingField", back_populates="report",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    inconsistencies = relationship(
        "CompletenessInconsistency", back_populates="report",
        cascade="all, delete-orphan", passive_deletes=True,
    )

class CompletenessMissingField(Base):
    __tablename__ = "completeness_missing_fields"

    id        = Column(Integer, primary_key=True)
    report_id = Column(
        Integer,
        ForeignKey("completeness_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_name  = Column(String(80), nullable=False)
    severity_id = Column(
        Integer,
        ForeignKey("lkp_severities.id", ondelete="RESTRICT"),
        nullable=True,
    )

    report   = relationship("CompletenessReport", back_populates="missing_fields")
    severity = relationship("Severity")

class CompletenessInconsistency(Base):
    __tablename__ = "completeness_inconsistencies"

    id        = Column(Integer, primary_key=True)
    report_id = Column(
        Integer,
        ForeignKey("completeness_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_name  = Column(String(80), nullable=False)
    problem     = Column(Text, nullable=False)
    severity_id = Column(
        Integer,
        ForeignKey("lkp_severities.id", ondelete="RESTRICT"),
        nullable=True,
    )

    report   = relationship("CompletenessReport", back_populates="inconsistencies")
    severity = relationship("Severity")

class Lead(Base):
    __tablename__ = "leads"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    lead_id = Column(String(50), unique=True, index=True, nullable=False)

    event_source = Column(String(20), default="AI")

    type_id = Column(
        Integer,
        ForeignKey("lkp_lead_types.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status_id = Column(
        Integer,
        ForeignKey("lkp_lead_statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    severity_id = Column(
        Integer,
        ForeignKey("lkp_severities.id", ondelete="RESTRICT"),
        nullable=True,
    )

    description = Column(Text, nullable=False)
    confidence  = Column(Float, default=0.0)
    next_step   = Column(Text, nullable=True)

    suggested_suspect_id = Column(
        Integer,
        ForeignKey("case_suspects.id", ondelete="SET NULL"),
        nullable=True,
    )
    similar_case_id = Column(
        Integer,
        ForeignKey("cases.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    generated_at = Column(DateTime, default=datetime.now(UTC))
    updated_at   = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    case              = relationship("Case",        foreign_keys=[case_id_fk], back_populates="leads")
    type              = relationship("LeadType")
    status            = relationship("LeadStatus")
    severity          = relationship("Severity")
    suggested_suspect = relationship("CaseSuspect")
    similar_case      = relationship("Case",        foreign_keys=[similar_case_id])
    created_by        = relationship("User")

class Activity(Base):
    """
    Activity feed entry. Every case event (registration, status change,
    lead generated, etc.) creates a row here. The dashboard reads the
    most recent N rows for the logged-in investigator.
    """
    __tablename__ = "activities"
 
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    type = Column(String, default="update")
 
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
 
    case = relationship("Case", back_populates="activities")
    user = relationship("User")

class CaseLink(Base):
    __tablename__ = "case_links"

    id = Column(Integer, primary_key=True)
    source_case_id = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    
    target_case_id = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    link_type        = Column(String(40), nullable=False)
    similarity_score = Column(Float, default=0.0)
    explanation      = Column(Text, nullable=True)
    created_at       = Column(DateTime, default=datetime.now(UTC))

    source_case = relationship("Case", foreign_keys=[source_case_id], back_populates="linked_cases_out")
    target_case = relationship("Case", foreign_keys=[target_case_id], back_populates="linked_cases_in")

    shared_entities = relationship(
        "CaseLinkSharedEntity", back_populates="link",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("source_case_id", "target_case_id", "link_type", name="uq_case_link"),
    )

class CaseLinkSharedEntity(Base):
    __tablename__ = "case_link_shared_entities"

    id      = Column(Integer, primary_key=True)
    link_id = Column(
        Integer,
        ForeignKey("case_links.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type  = Column(String(40), nullable=False)
    entity_value = Column(String(255), nullable=False)
    person_id    = Column(
        Integer,
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
    )

    link   = relationship("CaseLink", back_populates="shared_entities")
    person = relationship("Person")

# SECTION 11 — TIMELINE & UPDATE HISTORY
class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id = Column(String(50), unique=True, index=True, nullable=False)

    event_source  = Column(String(20), default="MANUAL")
    event_type_id = Column(
        Integer,
        ForeignKey("lkp_timeline_event_types.id", ondelete="RESTRICT"),
        nullable=True,
    )
    title           = Column(String(255), nullable=False)
    description     = Column(Text, nullable=True)
    officer_name    = Column(String(150), nullable=True)
    severity_id     = Column(
        Integer,
        ForeignKey("lkp_severities.id", ondelete="RESTRICT"),
        nullable=True,
    )
    location        = Column(String(255), nullable=True)
    outcome         = Column(Text, nullable=True)
    attachment_note = Column(Text, nullable=True)

    follow_up_required = Column(Boolean, default=False)
    follow_up_date     = Column(Date, nullable=True)

    event_date = Column(Date, nullable=False)
    event_time = Column(String(10), nullable=True)
    created_at = Column(DateTime, default=datetime.now(UTC))
    editable   = Column(Boolean, default=True)

    case       = relationship("Case", back_populates="timeline_events")
    event_type = relationship("TimelineEventType")
    severity   = relationship("Severity")

class CaseUpdateNote(Base):
    __tablename__ = "case_update_notes"

    id         = Column(Integer, primary_key=True)
    case_id_fk = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    note       = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now(UTC))

    case = relationship("Case", back_populates="update_notes")
    user = relationship("User")

    fields_changed = relationship(
        "CaseUpdateFieldChange", back_populates="update_note",
        cascade="all, delete-orphan", passive_deletes=True,
    )

class NoteCategory(Base):
    """NOTE_CATEGORIES from caseEventConstants.js
    (General Observation / Field Note / Intelligence / Follow-Up /
     Reminder / Hypothesis / Question for Team / Action Item / Other)."""
    __tablename__ = "lkp_note_categories"
    id         = Column(Integer, primary_key=True)
    code       = Column(String(40), unique=True, nullable=False)
    label      = Column(String(80), nullable=False)
    sort_order = Column(Integer, default=0)
    active     = Column(Boolean, default=True)

class InvestigationNote(Base):
    """
    Free-form investigator note attached to a case. Maps 1:1 with the
    `createNote()` factory in caseEventConstants.js.

    NOT to be confused with CaseUpdateNote (the version-history record).
    """
    __tablename__ = "investigation_notes"

    id          = Column(Integer, primary_key=True)
    note_id     = Column(String(50), unique=True, index=True, nullable=False)
    # ↑ display ID like "NOTE-001" — frontend already generates this shape

    case_id_fk  = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id     = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    category_id = Column(
        Integer,
        ForeignKey("lkp_note_categories.id", ondelete="RESTRICT"),
        nullable=True,
    )

    officer_name = Column(String(150), nullable=True)   # cached for display
                                                        # even if user deleted
    note_date    = Column(Date, nullable=False)
    title        = Column(String(255), nullable=False)
    detail       = Column(Text, nullable=False)

    created_at   = Column(DateTime, default=datetime.now(UTC))
    updated_at   = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    case     = relationship("Case", back_populates="investigation_notes")
    user     = relationship("User")
    category = relationship("NoteCategory")

class CaseUpdateFieldChange(Base):
    __tablename__ = "case_update_field_changes"

    id             = Column(Integer, primary_key=True)
    update_note_id = Column(
        Integer,
        ForeignKey("case_update_notes.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_name = Column(String(80), nullable=False)
    old_value  = Column(Text, nullable=True)
    new_value  = Column(Text, nullable=True)

    update_note = relationship("CaseUpdateNote", back_populates="fields_changed")

# SECTION 12 — AUDIT, SETTINGS, BACKUPS, REPORTS (admin module)
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id     = Column(Integer, primary_key=True)
    log_id = Column(String(20), unique=True, index=True, nullable=False)

    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action      = Column(String(20), nullable=False, index=True)
    module      = Column(String(40), nullable=False, index=True)
    detail      = Column(Text, nullable=True)
    target_type = Column(String(40), nullable=True)
    target_id   = Column(String(50), nullable=True)

    ip_address = Column(String(45), nullable=True)
    machine    = Column(String(60), nullable=True)
    status     = Column(String(20), default="Success")
    timestamp  = Column(DateTime, default=datetime.now(UTC), index=True)

    user = relationship("User")

class Notification(Base):
    __tablename__ = "notifications"

    id              = Column(Integer, primary_key=True)
    notification_id = Column(String(50), unique=True, index=True, nullable=False)

    # The user receiving the notification.
    user_id         = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # What triggered it. NEW_LEAD / CASE_UPDATE / CASE_ASSIGNED /
    # MENTION / SYSTEM_ALERT / BACKUP_COMPLETE / SECURITY_ALERT
    type            = Column(String(40), nullable=False, index=True)
    severity_id     = Column(
        Integer,
        ForeignKey("lkp_severities.id", ondelete="RESTRICT"),
        nullable=True,
    )

    title           = Column(String(255), nullable=False)
    message         = Column(Text, nullable=True)
    link_url        = Column(String(500), nullable=True)

    # Optional FK back to the case this notification is about.
    related_case_id = Column(
        Integer,
        ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=True,
    )

    is_read         = Column(Boolean, default=False, index=True)
    read_at         = Column(DateTime, nullable=True)

    created_at      = Column(DateTime, default=datetime.now(UTC), index=True)

    user         = relationship("User")
    severity     = relationship("Severity")
    related_case = relationship("Case")

    __table_args__ = (
        Index("ix_notif_user_read", "user_id", "is_read"),
    )

class SystemSetting(Base):
    __tablename__ = "system_settings"

    id            = Column(Integer, primary_key=True)
    key           = Column(String(80), unique=True, nullable=False)
    value         = Column(Text, nullable=True)
    updated_at    = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))
    updated_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    updated_by = relationship("User")

class BackupRecord(Base):
    __tablename__ = "backup_records"

    id        = Column(Integer, primary_key=True)
    backup_id = Column(String(50), unique=True, index=True, nullable=False)
    type      = Column(String(20), default="Full")
    status    = Column(String(20), default="In Progress")
    file_path = Column(String(500), nullable=True)
    file_size = Column(Integer, nullable=True)
    encrypted = Column(Boolean, default=True)
    note      = Column(Text, nullable=True)

    started_at    = Column(DateTime, default=datetime.now(UTC))
    completed_at  = Column(DateTime, nullable=True)
    started_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    started_by = relationship("User")

class GeneratedReport(Base):
    __tablename__ = "generated_reports"

    id        = Column(Integer, primary_key=True)
    report_id = Column(String(50), unique=True, index=True, nullable=False)

    report_type = Column(String(40), nullable=False)
    case_id_fk  = Column(
        Integer,
        ForeignKey("cases.id", ondelete="SET NULL"),
        nullable=True,
    )
    filters   = Column(JSON, default=dict)   # genuinely schemaless filter set
    format    = Column(String(10), default="PDF")
    file_path = Column(String(500), nullable=True)
    file_size = Column(Integer, nullable=True)

    generated_by_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    generated_at = Column(DateTime, default=datetime.now(UTC))

    case         = relationship("Case")
    generated_by = relationship("User")


class Ticket(Base):
    """
    A message sent by any user to an admin via the Contact Administrator modal.
    Admins can reply and change status; the sender sees replies in notifications.
    """
    __tablename__ = "tickets"

    id        = Column(Integer, primary_key=True)
    ticket_id = Column(String(50), unique=True, index=True, nullable=False)
    # e.g. "TKT-20260513-001"

    # Who sent it
    sender_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Priority: normal / urgent / critical
    priority  = Column(String(20), default="normal", nullable=False)

    subject   = Column(String(255), nullable=False)
    message   = Column(Text, nullable=False)

    status_id = Column(
        Integer,
        ForeignKey("lkp_ticket_statuses.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Admin who picked it up (nullable until assigned)
    assigned_to_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    admin_notes  = Column(Text, nullable=True)   # internal admin note
    resolved_at  = Column(DateTime, nullable=True)

    created_at   = Column(DateTime, default=datetime.now(UTC))
    updated_at   = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))

    sender      = relationship("User", foreign_keys=[sender_id])
    assigned_to = relationship("User", foreign_keys=[assigned_to_id])
    status      = relationship("TicketStatus")
    replies     = relationship(
        "TicketReply", back_populates="ticket",
        cascade="all, delete-orphan", passive_deletes=True,
        order_by="TicketReply.created_at",
    )


class TicketReply(Base):
    """Admin (or sender) reply thread on a ticket."""
    __tablename__ = "ticket_replies"

    id        = Column(Integer, primary_key=True)
    ticket_id = Column(
        Integer,
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    body       = Column(Text, nullable=False)
    is_admin   = Column(Boolean, default=False)   # True = written by admin
    created_at = Column(DateTime, default=datetime.now(UTC))

    ticket = relationship("Ticket", back_populates="replies")
    author = relationship("User")

class CaseDraft(Base):
    """
    Case-registration drafts.
 
    Lives in its own table — NOT in `cases` — so half-filled drafts don't
    pollute All Cases / search / hotspots / reports. Each draft is private
    to its owner (FK to users, scoped by user_id on every query).
 
    The `form_data` JSON column is whatever the register-case wizard
    submitted via POST /api/investigator/case-drafts — same shape the
    wizard renders from, so a Resume action just feeds it back.
    Schemaless on purpose: when the wizard adds a new field tomorrow, we
    don't need a migration here.
 
    When the investigator finally submits the draft as a real case, the
    frontend calls deleteDraft() right after registerCaseFull succeeds,
    so this row goes away cleanly. Orphan drafts that the user abandoned
    just sit here until they're explicitly deleted from the Drafts page.
    """
    __tablename__ = "case_drafts"
 
    id          = Column(Integer, primary_key=True)
    # External, human-readable id — "DR-XXXX". Matches the same pattern as
    # case_id / suspect_id / etc. Generated by case_draft_service._next_draft_id.
    draft_id    = Column(String(50), unique=True, index=True, nullable=False)
 
    user_id     = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
 
    # Best-effort label shown in the Drafts list. The service auto-fills
    # this from form_data.caseTitle / form_data.firNumber if not provided.
    title       = Column(String(255), nullable=True)
 
    # Opaque JSON snapshot of the wizard's formData. Stored as JSON so
    # SQLAlchemy can decode it for us automatically.
    form_data   = Column(JSON, nullable=False)
 
    created_at  = Column(DateTime, default=datetime.now(UTC))
    updated_at  = Column(DateTime, default=datetime.now(UTC), onupdate=datetime.now(UTC))
 
    user = relationship("User")