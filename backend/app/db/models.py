"""SQLAlchemy 2.0 ORM models — the 12 tables of the spec (section 3)."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import DefaultClause
from sqlalchemy.sql.elements import ClauseElement

from app.db.base import Base

# Portable JSON: generic JSON (SQLite/tests), JSONB on PostgreSQL.
JSONB = JSON().with_variant(_PG_JSONB, "postgresql")


class UserRole(enum.StrEnum):
    ADMIN = "ADMIN"
    RESEARCHER = "RESEARCHER"


class LanguageCode(enum.StrEnum):
    FR = "fr"
    EN = "en"
    DE = "de"
    ES = "es"


class ProjectStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


class ImportJobStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProjectMemberRole(enum.StrEnum):
    LEAD = "LEAD"
    ANNOTATOR = "ANNOTATOR"


class BackendType(enum.StrEnum):
    OLLAMA = "OLLAMA"
    LLAMA_CPP = "LLAMA_CPP"
    TRANSFORMERS = "TRANSFORMERS"
    ONNX_LOCAL = "ONNX_LOCAL"
    SYSTEMONE = "SYSTEMONE"
    OPENAI_API = "OPENAI_API"  # API externe compatible OpenAI (URL + clé + modèle)


class BenchmarkStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PromptType(enum.StrEnum):
    INCLUSION = "INCLUSION"
    EXTRACTION = "EXTRACTION"


class PromptStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    TESTED = "TESTED"
    PRODUCTION = "PRODUCTION"


class PredictionStatus(enum.StrEnum):
    AUTO_ACCEPTED = "AUTO_ACCEPTED"
    AUTO_REJECTED = "AUTO_REJECTED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"


class SettingScope(enum.StrEnum):
    GLOBAL = "GLOBAL"
    PROJECT = "PROJECT"
    USER = "USER"


def _enum(e: type[enum.Enum]) -> Enum:
    """Enum stored in a lengthless VARCHAR + CHECK (portable SQLite/PG), values = .value."""
    return Enum(
        e,
        native_enum=False,
        create_constraint=True,
        length=None,
        values_callable=lambda m: [x.value for x in m],
    )


class _UuidGenExpr(ClauseElement):
    """Database-side default: gen_random_uuid() on PG, NULL on SQLite (which rejects it)."""

    __visit_name__ = "uuid_gen_expr"


@compiles(_UuidGenExpr, "postgresql")
def _uuid_gen_pg(element, compiler, **kw):
    return "gen_random_uuid()"


@compiles(_UuidGenExpr, "sqlite")
def _uuid_gen_sqlite(element, compiler, **kw):
    return "NULL"


def _uuid_pk() -> dict:
    """UUID PK kwargs: Python default (uuid4) + database-side default (gen_random_uuid() on PG)."""
    return dict(
        primary_key=True,
        default=uuid.uuid4,
        server_default=DefaultClause(_UuidGenExpr()),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String)
    role: Mapped[UserRole | None] = mapped_column(_enum(UserRole), server_default="RESEARCHER")
    language_preference: Mapped[LanguageCode | None] = mapped_column(
        _enum(LanguageCode), server_default="fr"
    )
    data_consent_given: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    anonymized_hash: Mapped[str | None] = mapped_column(String)
    # Account disabled by an admin: no more login, data kept.
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    # Creator: can create projects. Otherwise an "invitee": exists only through the
    # projects they were invited to and is deleted when losing the last one
    # (cf. cleanup.purge_orphans).
    can_create_projects: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    # Incremented on password change / logout: revokes JWTs issued before.
    token_version: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def may_create_projects(self) -> bool:
        return self.role == UserRole.ADMIN or bool(self.can_create_projects)

    created_projects: Mapped[list["Project"]] = relationship(
        back_populates="creator", passive_deletes=True
    )
    memberships: Mapped[list["ProjectMember"]] = relationship(
        back_populates="user", passive_deletes=True
    )
    annotations: Mapped[list["HumanAnnotation"]] = relationship(
        back_populates="user", passive_deletes=True
    )
    settings: Mapped[list["SystemSettings"]] = relationship(
        back_populates="user", passive_deletes=True
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[ProjectStatus | None] = mapped_column(
        _enum(ProjectStatus), server_default="DRAFT"
    )
    default_language: Mapped[LanguageCode | None] = mapped_column(
        _enum(LanguageCode), server_default="en"
    )
    # Article parts fed to the models (kinds, cf. app.article_text); NULL = ["methods"].
    text_sections: Mapped[list[str] | None] = mapped_column(JSONB)
    # Question posed to the models + answer fields (cf. app.schema); NULL = historical schema.
    question: Mapped[str | None] = mapped_column(Text)
    # Text windows read per article by the decision models (NULL = default).
    read_windows: Mapped[int | None] = mapped_column(Integer)
    fields: Mapped[list[dict] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Retention: "Keep" (counts as activity) and date of the inactivity deletion
    # warning (cf. app.maintenance).
    kept_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    creator: Mapped["User | None"] = relationship(back_populates="created_projects")
    members: Mapped[list["ProjectMember"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    articles: Mapped[list["Article"]] = relationship(back_populates="project", passive_deletes=True)
    benchmark_runs: Mapped[list["BenchmarkRun"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    prompt_templates: Mapped[list["PromptTemplate"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    gold_articles: Mapped[list["GoldArticle"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    settings: Mapped[list["SystemSettings"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    import_jobs: Mapped[list["ImportJob"]] = relationship(
        back_populates="project", passive_deletes=True
    )
    inference_jobs: Mapped[list["InferenceJob"]] = relationship(
        back_populates="project", passive_deletes=True
    )


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_in_project: Mapped[ProjectMemberRole | None] = mapped_column(
        _enum(ProjectMemberRole), server_default="ANNOTATOR"
    )

    project: Mapped["Project"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        Index("ix_articles_project_id", "project_id"),
        # M2: deduplication on re-import — one article per (project_id, pmid).
        # Partial (pmid non-NULL): articles without a pmid are not affected.
        Index(
            "uq_articles_project_pmid",
            "project_id",
            "pmid",
            unique=True,
            postgresql_where=text("pmid IS NOT NULL"),
            sqlite_where=text("pmid IS NOT NULL"),
        ),
        # Phase 2 multi-sources: general identity (project_id, source, native_id); the PMID
        # uniqueness is kept (universal identifier that other sources provide).
        Index(
            "uq_articles_project_source_native",
            "project_id",
            "source",
            "native_id",
            unique=True,
            postgresql_where=text("native_id IS NOT NULL"),
            sqlite_where=text("native_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    # Database that provided the record (pubmed | crossref | file…) and its native identifier (PMID,
    # normalized DOI…). NULL: old data without a PMID whose source was never noted.
    source: Mapped[str | None] = mapped_column(String)
    native_id: Mapped[str | None] = mapped_column(String)
    pmid: Mapped[str | None] = mapped_column(String)
    pmcid: Mapped[str | None] = mapped_column(String)
    doi: Mapped[str | None] = mapped_column(String)
    title: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    methods_text: Mapped[str | None] = mapped_column(Text)
    # PMC full text split by section: [{"title", "kind", "text"}] (kind: cf. app.article_text).
    sections: Mapped[list[dict] | None] = mapped_column(JSONB)
    # Why there is no full text (e.g. "preprint_not_archived": the author's license
    # forbidding PMC archiving); erased as soon as a text is found.
    fulltext_note: Mapped[str | None] = mapped_column(String)
    fulltext_source: Mapped[str | None] = mapped_column(String)  # pmc | europepmc | pdf[:version]
    fulltext_url: Mapped[str | None] = mapped_column(String)  # lien public vers le texte / PDF
    journal: Mapped[str | None] = mapped_column(String)
    pub_date: Mapped[str | None] = mapped_column(String)  # ISO partiel : 2024, 2024-05, 2024-05-12
    pub_year: Mapped[int | None] = mapped_column(Integer)
    authors: Mapped[list[str] | None] = mapped_column(
        JSONB
    )  # « Nom Initiales », ordre de l'article
    pub_types: Mapped[list[str] | None] = mapped_column(JSONB)  # Review, Journal Article…
    # m3: free-form VARCHAR (not an enum) — languages outside en/fr/de/es keep their
    # raw PubMed label (e.g. "Italian"); no forced 'en' (spec §3: VARCHAR).
    original_language: Mapped[str | None] = mapped_column(String, server_default="en")
    # Where the article comes from: <source> (search: pubmed…) | references
    # (imported list) | citation
    # (citation chaining). NULL: earlier import. ``origin_seeds``: number of seed
    # articles that cite it or that it cites; ``origin_ref``: id of the import job.
    origin: Mapped[str | None] = mapped_column(String)
    origin_seeds: Mapped[int | None] = mapped_column(Integer)
    origin_ref: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project | None"] = relationship(back_populates="articles")
    gold_articles: Mapped[list["GoldArticle"]] = relationship(
        back_populates="article", passive_deletes=True
    )
    predictions: Mapped[list["ArticlePrediction"]] = relationship(
        back_populates="article", passive_deletes=True
    )
    annotations: Mapped[list["HumanAnnotation"]] = relationship(
        back_populates="article", passive_deletes=True
    )


class ArticleFile(Base):
    """Kept PDF of an article (manual upload or open copy found): RO-Crate material."""

    __tablename__ = "article_files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ExportJob(Base):
    """Background RO-Crate export: the zip is written to EXPORT_DIR (shared volume)."""

    __tablename__ = "export_jobs"
    __table_args__ = (Index("ix_export_jobs_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )  # destinataire de l'e-mail de fin
    # Exported search (NULL = the project's initial search).
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE")
    )
    # PENDING | RUNNING | COMPLETED | FAILED
    status: Mapped[str] = mapped_column(String, server_default="PENDING")
    # {done, total} while RUNNING, then {size, articles, pdf, text}
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str | None] = mapped_column(String)  # nom du zip dans EXPORT_DIR
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    backend_type: Mapped[BackendType | None] = mapped_column(
        _enum(BackendType), server_default="OLLAMA"
    )
    model_identifier: Mapped[str] = mapped_column(String, nullable=False)
    ram_usage_mb: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # OPENAI_API : clé chiffrée
    # Owner (external model defined by a user); NULL = platform model.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )


class ModelShare(Base):
    """Usage right of a model for a user, or for all LEADs of a project."""

    __tablename__ = "model_shares"
    __table_args__ = (
        Index("ix_model_shares_model_id", "model_id"),
        Index("ix_model_shares_user_id", "user_id"),
        Index("ix_model_shares_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    model_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_registry.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Invitation(Base):
    """Pending registration invitation (admin list, resend, revocation)."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    language: Mapped[str] = mapped_column(String, server_default="fr")
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    role: Mapped[str | None] = mapped_column(String)
    # Account created by this invitation: project creator (admin invite) or invitee.
    can_create_projects: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), default=False
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PasswordReset(Base):
    """Password reset request: only the SHA-256 hash of the token is stored
    (the raw token goes in the email only)."""

    __tablename__ = "password_resets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"
    __table_args__ = (Index("ix_benchmark_runs_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Search whose lot serves as the test bench (NULL = initial search).
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE")
    )
    # Compared variants: [{name, prompt_template_id | system_prompt | None (= generated)}].
    variants: Mapped[list[dict] | None] = mapped_column(JSONB)
    # {use_gold: validate against the gold, gold_examples: k gold examples in the prompt}
    options: Mapped[dict | None] = mapped_column(JSONB)
    models_tested: Mapped[list[str] | None] = mapped_column(JSONB)
    progress: Mapped[dict | None] = mapped_column(JSONB)  # {done, total} pendant le RUNNING
    status: Mapped[BenchmarkStatus | None] = mapped_column(
        _enum(BenchmarkStatus), server_default="PENDING"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project | None"] = relationship(back_populates="benchmark_runs")
    results: Mapped[list["BenchmarkResult"]] = relationship(
        back_populates="benchmark_run", passive_deletes=True
    )


class BenchmarkResult(Base):
    __tablename__ = "benchmark_results"
    __table_args__ = (Index("ix_benchmark_results_benchmark_run_id", "benchmark_run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    benchmark_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("benchmark_runs.id", ondelete="CASCADE")
    )
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    variant: Mapped[str | None] = mapped_column(String)  # variante de prompt (NULL = unique)
    n_scored: Mapped[int | None] = mapped_column(Integer)  # articles notés (gold hors exemples)
    # Metrics per item, confusion matrix, agreement between variants…
    details: Mapped[dict | None] = mapped_column(JSONB)
    execution_time_seconds: Mapped[float | None] = mapped_column(Float)
    accuracy: Mapped[float | None] = mapped_column(Float)
    precision_score: Mapped[float | None] = mapped_column(Float)
    recall_score: Mapped[float | None] = mapped_column(Float)
    f1_score: Mapped[float | None] = mapped_column(Float)

    benchmark_run: Mapped["BenchmarkRun | None"] = relationship(back_populates="results")


class BenchmarkPrediction(Base):
    """Answer of a variant (model × prompt) for an article of the test bench: side-by-side
    comparison, disagreements, metric recompute without re-running the models."""

    __tablename__ = "benchmark_predictions"
    __table_args__ = (Index("ix_benchmark_predictions_run_id", "benchmark_run_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    benchmark_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("benchmark_runs.id", ondelete="CASCADE"), nullable=False
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String, nullable=False)
    variant: Mapped[str | None] = mapped_column(String)
    decision_json: Mapped[dict | None] = mapped_column(JSONB)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String)  # ex. invalid_json
    seconds: Mapped[float | None] = mapped_column(Float)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (Index("ix_prompt_templates_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    version_number: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    system_prompt: Mapped[str | None] = mapped_column(Text)
    user_prompt_schema: Mapped[str | None] = mapped_column(Text)
    prompt_type: Mapped[PromptType | None] = mapped_column(
        _enum(PromptType), server_default="EXTRACTION"
    )
    status: Mapped[PromptStatus | None] = mapped_column(_enum(PromptStatus), server_default="DRAFT")

    project: Mapped["Project | None"] = relationship(back_populates="prompt_templates")


class Search(Base):
    """A search: one (or several) questions posed to a lot of the project's articles.

    It carries its own question, its answer items and its text parts read; its
    predictions, re-reads, analyses and references (gold) are attached to it. The lot is either
    the whole corpus (``lot_all``, dynamic) or a frozen list (``search_articles``), for
    example "the articles where search X answered yes" (``parent_id`` + ``parent_spec``).
    """

    __tablename__ = "searches"
    __table_args__ = (Index("ix_searches_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    fields: Mapped[list[dict] | None] = mapped_column(JSONB)
    text_sections: Mapped[list[str] | None] = mapped_column(JSONB)
    read_windows: Mapped[int | None] = mapped_column(Integer)
    # Fingerprint of (question, items): same fingerprint = same question, shared references.
    definition_hash: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, server_default="standard", default="standard")
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    lot_all: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="SET NULL")
    )
    parent_spec: Mapped[dict | None] = mapped_column(JSONB)
    # Scheduled analysis: {model, prefilter_model, instructions, selection, error?}. The lot
    # does not exist yet: computed when the origin search has finished (cf. app.scheduler).
    schedule: Mapped[dict | None] = mapped_column(JSONB)
    schedule_state: Mapped[str | None] = mapped_column(String)  # waiting | started | failed
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SearchArticle(Base):
    """Frozen lot of a search (absent when ``Search.lot_all``)."""

    __tablename__ = "search_articles"

    search_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), primary_key=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class GoldArticle(Base):
    __tablename__ = "gold_articles"
    __table_args__ = (
        Index("ix_gold_articles_project_id", "project_id"),
        Index("ix_gold_articles_article_id", "article_id"),
        Index("uq_gold_articles_search_article", "search_id", "article_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE")
    )
    ground_truth_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Search (hence question) this is the reference of; NULL = the project's default search.
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    # Origin re-read (back link to the card) and re-reader; frozen copy of the answers.
    source_annotation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("human_annotations.id", ondelete="SET NULL")
    )
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )

    project: Mapped["Project | None"] = relationship(back_populates="gold_articles")
    article: Mapped["Article | None"] = relationship(back_populates="gold_articles")


class ArticlePrediction(Base):
    __tablename__ = "article_predictions"
    __table_args__ = (
        Index("ix_article_predictions_article_id", "article_id"),
        Index("ix_article_predictions_prompt_template_id", "prompt_template_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE")
    )
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("prompt_templates.id")
    )
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    # Frozen at prediction time (registry renamable/deletable, tag re-pullable);
    # NULL = old data or version not recorded.
    model_identifier: Mapped[str | None] = mapped_column(String)
    model_version: Mapped[str | None] = mapped_column(String)
    decision_json: Mapped[dict | None] = mapped_column(JSONB)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    evidence_quotes: Mapped[list[str] | None] = mapped_column(JSONB)
    # Search that produced it; NULL = the project's default search (old data).
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[PredictionStatus | None] = mapped_column(
        _enum(PredictionStatus), server_default="NEEDS_HUMAN_REVIEW"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    article: Mapped["Article | None"] = relationship(back_populates="predictions")


class HumanAnnotation(Base):
    __tablename__ = "human_annotations"
    __table_args__ = (
        Index("ix_human_annotations_article_id", "article_id"),
        Index("ix_human_annotations_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    annotator_hash: Mapped[str | None] = mapped_column(String)
    # Search (question) this re-read answers; NULL = default search.
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    # Blind re-read: done without seeing the model's prediction.
    blind: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    decision_json: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    article: Mapped["Article | None"] = relationship(back_populates="annotations")
    user: Mapped["User | None"] = relationship(back_populates="annotations")


class SystemSettings(Base):
    __tablename__ = "system_settings"
    __table_args__ = (
        UniqueConstraint("scope", "project_id", "user_id", "key_name"),
        # The UNIQUE constraint above has NULL holes (SQLite/PG: NULL != NULL in
        # UNIQUE): a GLOBAL duplicate (project_id=NULL, user_id=NULL) would pass. The
        # partial unique indexes below close these holes, scope by scope.
        Index(
            "uq_settings_global",
            "key_name",
            unique=True,
            postgresql_where=text("scope = 'GLOBAL' AND project_id IS NULL AND user_id IS NULL"),
            sqlite_where=text("scope = 'GLOBAL' AND project_id IS NULL AND user_id IS NULL"),
        ),
        Index(
            "uq_settings_project",
            "project_id",
            "key_name",
            unique=True,
            postgresql_where=text("scope = 'PROJECT' AND user_id IS NULL"),
            sqlite_where=text("scope = 'PROJECT' AND user_id IS NULL"),
        ),
        Index(
            "uq_settings_user",
            "user_id",
            "key_name",
            unique=True,
            postgresql_where=text("scope = 'USER' AND project_id IS NULL"),
            sqlite_where=text("scope = 'USER' AND project_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    scope: Mapped[SettingScope] = mapped_column(_enum(SettingScope), server_default="GLOBAL")
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE")
    )
    key_name: Mapped[str] = mapped_column(String, nullable=False)
    value_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    is_secret: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project | None"] = relationship(back_populates="settings")
    user: Mapped["User | None"] = relationship(back_populates="settings")


class ImportJob(Base):
    """PubMed import job (lot 4A): persists the query + status + result.

    MODULE 6 (reproducibility): the exact PubMed query is kept here.
    """

    __tablename__ = "import_jobs"
    __table_args__ = (Index("ix_import_jobs_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str | None] = mapped_column(String, server_default="import")  # import | fulltext
    # Database queried (pubmed…); NULL: references (mixed sources), crate, full text.
    source: Mapped[str | None] = mapped_column(String)
    job_id: Mapped[str | None] = mapped_column(String)  # id Celery du task
    status: Mapped[ImportJobStatus | None] = mapped_column(
        _enum(ImportJobStatus), server_default="PENDING"
    )
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped["Project | None"] = relationship(back_populates="import_jobs")


class InferenceJobStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InferenceJob(Base):
    """Mass inference job (MODULE 4, lot 5): status + key statistics.

    MODULE 6 (reproducibility): the prompt + model used are kept.
    In-app notifications: the frontend polls ``GET /projects/{id}/inference``.
    """

    __tablename__ = "inference_jobs"
    __table_args__ = (Index("ix_inference_jobs_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, **_uuid_pk())
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE")
    )
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("prompt_templates.id")
    )
    model_used: Mapped[str] = mapped_column(String, nullable=False)
    # Optional stage 1 (ONNX encoder on abstracts); NULL = no prefilter.
    prefilter_model: Mapped[str | None] = mapped_column(String)
    # Frozen snapshot at start: {"main": snap, "prefilter": snap | None} with
    # snap = {name, backend, identifier, version, ...}; NULL = old job.
    model_info: Mapped[dict | None] = mapped_column(JSONB)
    search_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("searches.id", ondelete="CASCADE"), index=True
    )
    # {gold_examples: k gold examples in the prompt, validate_with_gold: bool}
    options: Mapped[dict | None] = mapped_column(JSONB)
    # Resuming an interrupted job: articles already predicted since this date are skipped.
    resume_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[InferenceJobStatus | None] = mapped_column(
        _enum(InferenceJobStatus), server_default="PENDING"
    )
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped["Project | None"] = relationship(back_populates="inference_jobs")
