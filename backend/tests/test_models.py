"""Tests des 12 modèles ORM (spec section 3) — métadonnées + comportement sur SQLite async."""

import uuid

import pytest
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    select,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from app.db.base import Base
from app.db.models import (
    Article,
    ArticlePrediction,
    BackendType,
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkStatus,
    GoldArticle,
    HumanAnnotation,
    ImportJob,
    ImportJobStatus,
    LanguageCode,
    ModelRegistry,
    PredictionStatus,
    Project,
    ProjectMember,
    ProjectMemberRole,
    ProjectStatus,
    PromptStatus,
    PromptTemplate,
    PromptType,
    SettingScope,
    SystemSettings,
    User,
    UserRole,
)

EXPECTED_TABLES = {
    "users",
    "projects",
    "project_members",
    "articles",
    "model_registry",
    "benchmark_runs",
    "benchmark_results",
    "prompt_templates",
    "gold_articles",
    "article_predictions",
    "human_annotations",
    "system_settings",
    "import_jobs",  # lot 4A : jobs d'import PubMed (query + statut + résultat/erreur)
    "inference_jobs",  # lot 5 : jobs d'inférence massive (statut + statistiques)
    "article_files",  # PDF conservés (RO-Crate)
    "export_jobs",  # export RO-Crate asynchrone
    "model_shares",  # droits d'usage des modèles
    "invitations",  # invitations en attente
}


def fk(col):
    return next(iter(col.foreign_keys))


def test_all_tables_registered():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


# --- users ---


def test_users_columns():
    c = User.__table__.columns
    assert set(c.keys()) == {
        "id",
        "email",
        "hashed_password",
        "full_name",
        "role",
        "language_preference",
        "data_consent_given",
        "anonymized_hash",
        "is_active",
        "created_at",
    }
    assert c["id"].primary_key
    assert isinstance(c["id"].type, Uuid)
    assert c["id"].default is not None
    assert isinstance(c["email"].type, String)
    assert not c["email"].nullable
    assert c["email"].unique
    assert isinstance(c["hashed_password"].type, String)
    assert not c["hashed_password"].nullable
    assert c["full_name"].nullable
    assert c["role"].type.enums == ["ADMIN", "RESEARCHER"]
    assert c["role"].server_default.arg == "RESEARCHER"
    assert c["language_preference"].type.enums == ["fr", "en", "de", "es"]
    assert c["language_preference"].server_default.arg == "fr"
    assert isinstance(c["data_consent_given"].type, Boolean)
    assert c["data_consent_given"].server_default is not None
    assert c["anonymized_hash"].nullable
    assert isinstance(c["created_at"].type, DateTime)
    assert c["created_at"].type.timezone
    assert c["created_at"].server_default is not None


async def test_users_email_unique(session):
    session.add(User(email="a@b.c", hashed_password="h"))
    await session.commit()
    session.add(User(email="a@b.c", hashed_password="h2"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_user_role_and_language_enum(session):
    session.add(
        User(
            email="ok@b.c",
            hashed_password="h",
            role=UserRole.ADMIN,
            language_preference=LanguageCode.ES,
        )
    )
    await session.commit()
    u = (await session.execute(select(User))).scalar_one()
    assert u.role == UserRole.ADMIN
    assert u.language_preference == LanguageCode.ES

    session.add(User(email="bad@b.c", hashed_password="h", role="SUPERUSER"))
    with pytest.raises(IntegrityError):
        await session.commit()


# --- projects ---


def test_projects_columns():
    c = Project.__table__.columns
    assert set(c.keys()) == {
        "id",
        "title",
        "description",
        "created_by",
        "status",
        "default_language",
        "text_sections",
        "read_windows",
        "question",
        "fields",
        "created_at",
    }
    assert c["id"].primary_key
    assert isinstance(c["id"].type, Uuid)
    assert not c["title"].nullable
    assert c["description"].nullable
    assert fk(c["created_by"]).column is User.__table__.c.id
    assert fk(c["created_by"]).ondelete == "SET NULL"
    assert c["status"].type.enums == ["DRAFT", "FROZEN", "PROCESSING", "COMPLETED"]
    assert c["status"].server_default.arg == "DRAFT"
    assert c["default_language"].type.enums == ["fr", "en", "de", "es"]
    assert c["default_language"].server_default.arg == "en"
    assert isinstance(c["created_at"].type, DateTime)
    assert c["created_at"].server_default is not None


# --- project_members ---


def test_project_members_composite_pk_and_fks():
    c = ProjectMember.__table__.columns
    assert set(c.keys()) == {"project_id", "user_id", "role_in_project"}
    assert c["project_id"].primary_key and c["user_id"].primary_key
    assert len(ProjectMember.__table__.primary_key) == 2
    assert fk(c["project_id"]).column is Project.__table__.c.id
    assert fk(c["project_id"]).ondelete == "CASCADE"
    assert fk(c["user_id"]).column is User.__table__.c.id
    assert fk(c["user_id"]).ondelete == "CASCADE"
    assert c["role_in_project"].type.enums == ["LEAD", "ANNOTATOR"]
    assert c["role_in_project"].server_default.arg == "ANNOTATOR"


async def test_project_members_composite_pk_unique(session):
    user = User(email="a@b.c", hashed_password="h")
    session.add(user)
    await session.flush()
    project = Project(title="p")
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id))
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id, user_id=user.id, role_in_project=ProjectMemberRole.LEAD
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


# --- articles ---


def test_articles_columns():
    c = Article.__table__.columns
    assert set(c.keys()) == {
        "id",
        "project_id",
        "pmid",
        "pmcid",
        "doi",
        "title",
        "abstract",
        "methods_text",
        "sections",
        "fulltext_source",
        "journal",
        "fulltext_url",
        "pub_date",
        "pub_year",
        "authors",
        "pub_types",
        "original_language",
        "created_at",
    }
    assert c["id"].primary_key
    assert fk(c["project_id"]).column is Project.__table__.c.id
    assert fk(c["project_id"]).ondelete == "CASCADE"
    for name in ("pmid", "pmcid", "doi"):
        assert isinstance(c[name].type, String) and c[name].nullable
    for name in ("title", "abstract", "methods_text"):
        assert isinstance(c[name].type, Text) and c[name].nullable
    # m3 : VARCHAR libre (pas un enum) — les langues hors en/fr/de/es gardent
    # leur libellé PubMed brut (pas de forçage 'en').
    assert isinstance(c["original_language"].type, String) and c["original_language"].nullable
    assert c["original_language"].server_default.arg == "en"
    assert c["created_at"].server_default is not None


def test_articles_pmid_partial_unique_index():
    """M2 : index unique partiel (project_id, pmid) WHERE pmid IS NOT NULL."""
    indexes = {ix.name: ix for ix in Article.__table__.indexes}
    assert "uq_articles_project_pmid" in indexes
    ix = indexes["uq_articles_project_pmid"]
    assert ix.unique
    assert [col.name for col in ix.columns] == ["project_id", "pmid"]
    assert ix.dialect_options["sqlite"]["where"] is not None
    assert ix.dialect_options["postgresql"]["where"] is not None


async def test_articles_pmid_unique_enforced(session):
    """M2 : (project_id, pmid) en double → IntegrityError ; pmid NULL ×2 → OK."""
    project = Project(title="p")
    session.add(project)
    await session.flush()
    session.add_all(
        [
            Article(project_id=project.id, pmid="111"),
            Article(project_id=project.id, pmid=None),
            Article(project_id=project.id, pmid=None),
        ]
    )
    await session.commit()
    session.add(Article(project_id=project.id, pmid="111"))
    with pytest.raises(IntegrityError):
        await session.commit()


# --- import_jobs (lot 4A) ---


def test_import_job_columns():
    c = ImportJob.__table__.columns
    assert set(c.keys()) == {
        "id",
        "project_id",
        "query",
        "job_id",
        "kind",
        "status",
        "result_json",
        "error",
        "created_at",
        "completed_at",
    }
    assert c["id"].primary_key
    assert isinstance(c["id"].type, Uuid)
    assert fk(c["project_id"]).column is Project.__table__.c.id
    assert fk(c["project_id"]).ondelete == "CASCADE"
    assert isinstance(c["query"].type, Text) and not c["query"].nullable
    assert isinstance(c["job_id"].type, String) and c["job_id"].nullable
    assert c["status"].type.enums == ["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    assert c["status"].server_default.arg == "PENDING"
    assert isinstance(c["result_json"].type, JSON)
    assert isinstance(c["error"].type, Text) and c["error"].nullable
    assert isinstance(c["created_at"].type, DateTime) and c["created_at"].type.timezone
    assert c["created_at"].server_default is not None
    assert isinstance(c["completed_at"].type, DateTime) and c["completed_at"].nullable


async def test_import_job_defaults(session):
    project = Project(title="p")
    session.add(project)
    await session.flush()
    session.add(ImportJob(project_id=project.id, query="cancer", job_id="job-1"))
    await session.commit()
    job = (await session.execute(select(ImportJob))).scalar_one()
    assert job.status == ImportJobStatus.PENDING
    assert job.result_json is None
    assert job.error is None
    assert job.created_at is not None
    assert job.completed_at is None


# --- model_registry ---


def test_model_registry_columns():
    c = ModelRegistry.__table__.columns
    assert set(c.keys()) == {
        "id",
        "name",
        "backend_type",
        "model_identifier",
        "ram_usage_mb",
        "is_active",
        "api_key_enc",
        "owner_id",
    }
    assert c["id"].primary_key
    assert c["name"].unique
    assert not c["name"].nullable
    assert c["backend_type"].type.enums == [
        "OLLAMA",
        "LLAMA_CPP",
        "TRANSFORMERS",
        "ONNX_LOCAL",
        "SYSTEMONE",
        "OPENAI_API",
    ]
    assert c["backend_type"].server_default.arg == "OLLAMA"
    assert not c["model_identifier"].nullable
    assert isinstance(c["ram_usage_mb"].type, Integer) and c["ram_usage_mb"].nullable
    assert isinstance(c["is_active"].type, Boolean)
    assert c["is_active"].server_default is not None


async def test_model_registry_name_unique(session):
    session.add(ModelRegistry(name="Laya-400M", model_identifier="laya:400m"))
    await session.commit()
    session.add(ModelRegistry(name="Laya-400M", model_identifier="laya:400m-v2"))
    with pytest.raises(IntegrityError):
        await session.commit()


# --- benchmark_runs ---


def test_benchmark_runs_columns():
    c = BenchmarkRun.__table__.columns
    assert set(c.keys()) == {
        "id",
        "project_id",
        "prompt_template_id",
        "progress",
        "models_tested",
        "status",
        "created_at",
    }
    assert c["id"].primary_key
    assert fk(c["project_id"]).column is Project.__table__.c.id
    assert fk(c["project_id"]).ondelete == "CASCADE"
    assert not c["prompt_template_id"].foreign_keys  # spec : UUID simple, pas de FK
    assert isinstance(c["models_tested"].type, JSON)
    assert c["status"].type.enums == ["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    assert c["status"].server_default.arg == "PENDING"
    assert c["created_at"].server_default is not None


# --- benchmark_results ---


def test_benchmark_results_columns():
    c = BenchmarkResult.__table__.columns
    assert set(c.keys()) == {
        "id",
        "benchmark_run_id",
        "model_name",
        "execution_time_seconds",
        "accuracy",
        "precision_score",
        "recall_score",
        "f1_score",
    }
    assert c["id"].primary_key
    assert fk(c["benchmark_run_id"]).column is BenchmarkRun.__table__.c.id
    assert fk(c["benchmark_run_id"]).ondelete == "CASCADE"
    assert not c["model_name"].nullable
    for name in (
        "execution_time_seconds",
        "accuracy",
        "precision_score",
        "recall_score",
        "f1_score",
    ):
        assert isinstance(c[name].type, Float) and c[name].nullable


# --- prompt_templates ---


def test_prompt_templates_columns():
    c = PromptTemplate.__table__.columns
    assert set(c.keys()) == {
        "id",
        "project_id",
        "version_number",
        "title",
        "system_prompt",
        "user_prompt_schema",
        "prompt_type",
        "status",
    }
    assert c["id"].primary_key
    assert fk(c["project_id"]).column is Project.__table__.c.id
    assert fk(c["project_id"]).ondelete == "CASCADE"
    assert not c["version_number"].nullable
    assert c["title"].nullable
    assert isinstance(c["system_prompt"].type, Text)
    assert isinstance(c["user_prompt_schema"].type, Text)
    assert c["prompt_type"].type.enums == ["INCLUSION", "EXTRACTION"]
    assert c["prompt_type"].server_default.arg == "EXTRACTION"
    assert c["status"].type.enums == ["DRAFT", "TESTED", "PRODUCTION"]
    assert c["status"].server_default.arg == "DRAFT"


# --- gold_articles ---


def test_gold_articles_columns():
    c = GoldArticle.__table__.columns
    assert set(c.keys()) == {"id", "project_id", "article_id", "ground_truth_json"}
    assert c["id"].primary_key
    assert fk(c["project_id"]).column is Project.__table__.c.id
    assert fk(c["project_id"]).ondelete == "CASCADE"
    assert fk(c["article_id"]).column is Article.__table__.c.id
    assert fk(c["article_id"]).ondelete == "CASCADE"
    assert isinstance(c["ground_truth_json"].type, JSON)
    assert not c["ground_truth_json"].nullable


# --- article_predictions ---


def test_article_predictions_columns():
    c = ArticlePrediction.__table__.columns
    assert set(c.keys()) == {
        "id",
        "article_id",
        "prompt_template_id",
        "model_used",
        "decision_json",
        "confidence_score",
        "evidence_quotes",
        "status",
        "created_at",
    }
    assert c["id"].primary_key
    assert fk(c["article_id"]).column is Article.__table__.c.id
    assert fk(c["article_id"]).ondelete == "CASCADE"
    assert fk(c["prompt_template_id"]).column is PromptTemplate.__table__.c.id
    assert fk(c["prompt_template_id"]).ondelete is None  # spec : pas de clause ON DELETE
    assert not c["model_used"].nullable
    assert isinstance(c["decision_json"].type, JSON)
    assert isinstance(c["confidence_score"].type, Float)
    assert isinstance(c["evidence_quotes"].type, JSON)
    assert c["status"].type.enums == ["AUTO_ACCEPTED", "AUTO_REJECTED", "NEEDS_HUMAN_REVIEW"]
    assert c["status"].server_default.arg == "NEEDS_HUMAN_REVIEW"


# --- human_annotations ---


def test_human_annotations_columns():
    c = HumanAnnotation.__table__.columns
    assert set(c.keys()) == {
        "id",
        "article_id",
        "user_id",
        "annotator_hash",
        "decision_json",
        "notes",
        "created_at",
    }
    assert c["id"].primary_key
    assert fk(c["article_id"]).column is Article.__table__.c.id
    assert fk(c["article_id"]).ondelete == "CASCADE"
    assert fk(c["user_id"]).column is User.__table__.c.id
    assert fk(c["user_id"]).ondelete == "SET NULL"
    assert c["annotator_hash"].nullable
    assert isinstance(c["decision_json"].type, JSON)
    assert isinstance(c["notes"].type, Text)
    assert c["created_at"].server_default is not None


# --- system_settings ---


def test_system_settings_columns():
    c = SystemSettings.__table__.columns
    assert set(c.keys()) == {
        "id",
        "scope",
        "project_id",
        "user_id",
        "key_name",
        "value_encrypted",
        "is_secret",
        "updated_at",
    }
    assert c["id"].primary_key
    assert isinstance(c["id"].type, Uuid)
    assert c["id"].default is not None
    assert c["scope"].type.enums == ["GLOBAL", "PROJECT", "USER"]
    assert not c["scope"].nullable
    assert c["scope"].server_default.arg == "GLOBAL"
    assert fk(c["project_id"]).column is Project.__table__.c.id
    assert fk(c["project_id"]).ondelete == "CASCADE"
    assert fk(c["user_id"]).column is User.__table__.c.id
    assert fk(c["user_id"]).ondelete == "CASCADE"
    assert not c["key_name"].nullable
    assert isinstance(c["value_encrypted"].type, LargeBinary)
    assert c["value_encrypted"].nullable
    assert isinstance(c["is_secret"].type, Boolean)
    assert c["is_secret"].server_default is not None
    assert isinstance(c["updated_at"].type, DateTime)
    assert c["updated_at"].server_default is not None


def test_system_settings_unique_constraint():
    uq = [c for c in SystemSettings.__table__.constraints if isinstance(c, UniqueConstraint)]
    assert len(uq) == 1
    assert [col.name for col in uq[0].columns] == ["scope", "project_id", "user_id", "key_name"]


async def test_system_settings_defaults(session):
    session.add(SystemSettings(key_name="k"))
    await session.commit()
    s = (await session.execute(select(SystemSettings))).scalar_one()
    assert s.scope == SettingScope.GLOBAL
    assert s.is_secret is False
    assert s.updated_at is not None
    assert s.value_encrypted is None


async def test_system_settings_unique(session):
    project = Project(title="p")
    session.add(project)
    await session.flush()
    user = User(email="a@b.c", hashed_password="h")
    session.add(user)
    await session.flush()
    session.add(
        SystemSettings(
            scope=SettingScope.PROJECT,
            project_id=project.id,
            user_id=user.id,
            key_name="smtp_password",
        )
    )
    await session.commit()
    session.add(
        SystemSettings(
            scope=SettingScope.PROJECT,
            project_id=project.id,
            user_id=user.id,
            key_name="smtp_password",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_settings_global_scope_unique(session):
    """Deux settings GLOBAL même key_name → IntegrityError (index partiel uq_settings_global)."""
    session.add(SystemSettings(scope=SettingScope.GLOBAL, key_name="smtp_host"))
    await session.commit()
    session.add(SystemSettings(scope=SettingScope.GLOBAL, key_name="smtp_host"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_settings_project_scope_unique(session):
    """Deux settings PROJECT même (project_id, key_name) → IntegrityError."""
    project = Project(title="p")
    session.add(project)
    await session.flush()
    session.add(
        SystemSettings(scope=SettingScope.PROJECT, project_id=project.id, key_name="smtp_host")
    )
    await session.commit()
    session.add(
        SystemSettings(scope=SettingScope.PROJECT, project_id=project.id, key_name="smtp_host")
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_settings_user_scope_unique(session):
    """Deux settings USER même (user_id, key_name) → IntegrityError."""
    user = User(email="a@b.c", hashed_password="h")
    session.add(user)
    await session.flush()
    session.add(SystemSettings(scope=SettingScope.USER, user_id=user.id, key_name="smtp_host"))
    await session.commit()
    session.add(SystemSettings(scope=SettingScope.USER, user_id=user.id, key_name="smtp_host"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_settings_same_key_different_scopes_ok(session):
    """Même key_name dans des scopes différents (GLOBAL vs PROJECT) → pas d'erreur."""
    project = Project(title="p")
    session.add(project)
    await session.flush()
    session.add_all(
        [
            SystemSettings(scope=SettingScope.GLOBAL, key_name="smtp_host"),
            SystemSettings(scope=SettingScope.PROJECT, project_id=project.id, key_name="smtp_host"),
        ]
    )
    await session.commit()
    assert len((await session.execute(select(SystemSettings))).scalars().all()) == 2


async def test_system_settings_cascade_delete(session):
    project = Project(title="p")
    session.add(project)
    await session.flush()
    user = User(email="a@b.c", hashed_password="h")
    session.add(user)
    await session.flush()
    user_id = user.id
    session.add_all(
        [
            SystemSettings(scope=SettingScope.PROJECT, project_id=project.id, key_name="k1"),
            SystemSettings(scope=SettingScope.USER, user_id=user_id, key_name="k2"),
        ]
    )
    await session.commit()

    await session.delete(project)
    await session.commit()
    session.expire_all()
    remaining = (await session.execute(select(SystemSettings))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].user_id == user_id

    await session.delete(user)
    await session.commit()
    session.expire_all()
    assert (await session.execute(select(SystemSettings))).first() is None


# --- valeurs par défaut à l'insertion ---


async def test_defaults_on_insert(session):
    user = User(email="a@b.c", hashed_password="h")
    session.add(user)
    await session.flush()
    project = Project(title="p", created_by=user.id)
    session.add(project)
    await session.flush()
    article = Article(project_id=project.id)
    session.add_all(
        [
            ProjectMember(project_id=project.id, user_id=user.id),
            article,
            ModelRegistry(name="Laya-400M", model_identifier="laya:400m"),
            BenchmarkRun(project_id=project.id),
            PromptTemplate(project_id=project.id, version_number="v1.0"),
            ArticlePrediction(article_id=article.id, model_used="Laya-400M"),
        ]
    )
    await session.commit()

    u = (await session.execute(select(User))).scalar_one()
    assert isinstance(u.id, uuid.UUID)
    assert u.id.version == 4  # gen_random_uuid() = v4
    assert u.role == UserRole.RESEARCHER
    assert u.language_preference == LanguageCode.FR
    assert u.data_consent_given is False
    assert u.created_at is not None

    p = (await session.execute(select(Project))).scalar_one()
    assert p.status == ProjectStatus.DRAFT
    assert p.default_language == LanguageCode.EN
    assert p.created_at is not None

    m = (await session.execute(select(ProjectMember))).scalar_one()
    assert m.role_in_project == ProjectMemberRole.ANNOTATOR

    a = (await session.execute(select(Article))).scalar_one()
    assert a.original_language == LanguageCode.EN
    assert a.created_at is not None

    mr = (await session.execute(select(ModelRegistry))).scalar_one()
    assert mr.backend_type == BackendType.OLLAMA
    assert mr.is_active is True

    r = (await session.execute(select(BenchmarkRun))).scalar_one()
    assert r.status == BenchmarkStatus.PENDING
    assert r.created_at is not None

    t = (await session.execute(select(PromptTemplate))).scalar_one()
    assert t.prompt_type == PromptType.EXTRACTION
    assert t.status == PromptStatus.DRAFT

    pred = (await session.execute(select(ArticlePrediction))).scalar_one()
    assert pred.status == PredictionStatus.NEEDS_HUMAN_REVIEW


# --- comportement des FK (CASCADE / SET NULL) ---


async def test_cascade_delete_project(session):
    user = User(email="a@b.c", hashed_password="h")
    session.add(user)
    await session.flush()
    project = Project(title="p", created_by=user.id)
    session.add(project)
    await session.flush()
    session.add_all(
        [
            ProjectMember(project_id=project.id, user_id=user.id),
            Article(project_id=project.id),
            BenchmarkRun(project_id=project.id),
            PromptTemplate(project_id=project.id, version_number="v1.0"),
        ]
    )
    await session.commit()

    await session.delete(project)
    await session.commit()
    session.expire_all()

    assert (await session.execute(select(ProjectMember))).first() is None
    assert (await session.execute(select(Article))).first() is None
    assert (await session.execute(select(BenchmarkRun))).first() is None
    assert (await session.execute(select(PromptTemplate))).first() is None
    assert (await session.execute(select(User))).scalar_one() is not None


async def test_cascade_delete_article(session):
    project = Project(title="p")
    session.add(project)
    await session.flush()
    article = Article(project_id=project.id)
    session.add(article)
    await session.flush()
    session.add_all(
        [
            GoldArticle(project_id=project.id, article_id=article.id, ground_truth_json={"k": 1}),
            ArticlePrediction(article_id=article.id, model_used="m"),
            HumanAnnotation(article_id=article.id, annotator_hash="h1"),
        ]
    )
    await session.commit()

    await session.delete(article)
    await session.commit()
    session.expire_all()

    assert (await session.execute(select(GoldArticle))).first() is None
    assert (await session.execute(select(ArticlePrediction))).first() is None
    assert (await session.execute(select(HumanAnnotation))).first() is None
    assert (await session.execute(select(Project))).scalar_one() is not None


async def test_cascade_delete_benchmark_run(session):
    project = Project(title="p")
    session.add(project)
    await session.flush()
    run = BenchmarkRun(project_id=project.id)
    session.add(run)
    await session.flush()
    session.add(BenchmarkResult(benchmark_run_id=run.id, model_name="Laya-400M", f1_score=0.9))
    await session.commit()

    await session.delete(run)
    await session.commit()
    session.expire_all()

    assert (await session.execute(select(BenchmarkResult))).first() is None
    assert (await session.execute(select(Project))).scalar_one() is not None


# --- flux RGPD : suppression d'un user ---


async def test_rgpd_user_deletion(session):
    user = User(email="u@x.y", hashed_password="h")
    session.add(user)
    await session.flush()
    project = Project(title="p", created_by=user.id)
    session.add(project)
    await session.flush()
    session.add_all(
        [
            ProjectMember(project_id=project.id, user_id=user.id),
            Article(project_id=project.id),
        ]
    )
    await session.flush()
    article = (await session.execute(select(Article))).scalar_one()
    session.add(
        HumanAnnotation(article_id=article.id, user_id=user.id, annotator_hash="sha256-abc")
    )
    await session.commit()

    await session.delete(user)
    await session.commit()
    session.expire_all()

    ann = (await session.execute(select(HumanAnnotation))).scalar_one()
    assert ann.user_id is None
    assert ann.annotator_hash == "sha256-abc"

    proj = (await session.execute(select(Project))).scalar_one()
    assert proj.created_by is None

    assert (await session.execute(select(ProjectMember))).first() is None


# --- index sur les colonnes FK (finding sécurité MINOR) ---


def test_fk_indexes_exist():
    expected = {
        "articles": {"ix_articles_project_id"},
        "benchmark_runs": {"ix_benchmark_runs_project_id"},
        "benchmark_results": {"ix_benchmark_results_benchmark_run_id"},
        "prompt_templates": {"ix_prompt_templates_project_id"},
        "gold_articles": {"ix_gold_articles_project_id", "ix_gold_articles_article_id"},
        "article_predictions": {
            "ix_article_predictions_article_id",
            "ix_article_predictions_prompt_template_id",
        },
        "human_annotations": {
            "ix_human_annotations_article_id",
            "ix_human_annotations_user_id",
        },
        "import_jobs": {"ix_import_jobs_project_id"},
    }
    for table_name, index_names in expected.items():
        indexes = {ix.name for ix in Base.metadata.tables[table_name].indexes}
        assert index_names <= indexes, f"{table_name}: index manquants {index_names - indexes}"


# --- DDL PostgreSQL : UUID par défaut côté base + VARCHAR sans longueur ---


def test_pg_ddl_uuid_default_and_varchar():
    tables_with_uuid_pk = [
        User,
        Project,
        Article,
        ModelRegistry,
        BenchmarkRun,
        BenchmarkResult,
        PromptTemplate,
        GoldArticle,
        ArticlePrediction,
        HumanAnnotation,
        SystemSettings,
        ImportJob,
    ]
    for model in tables_with_uuid_pk:
        ddl = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert "DEFAULT gen_random_uuid()" in ddl, model.__tablename__

    users_ddl = str(CreateTable(User.__table__).compile(dialect=postgresql.dialect()))
    assert "role VARCHAR" in users_ddl  # sans longueur
    assert "VARCHAR(10)" not in users_ddl

    # SQLite : le défaut côté base se compile en NULL (gen_random_uuid() inconnu).
    sqlite_ddl = str(CreateTable(User.__table__).compile(dialect=sqlite.dialect()))
    assert "DEFAULT NULL" in sqlite_ddl


# --- JSONB : round-trip ---


async def test_jsonb_roundtrip(session):
    project = Project(title="p")
    session.add(project)
    await session.flush()
    article = Article(project_id=project.id)
    session.add(article)
    await session.flush()
    template = PromptTemplate(project_id=project.id, version_number="v1.0")
    session.add(template)
    await session.flush()

    session.add_all(
        [
            GoldArticle(
                project_id=project.id,
                article_id=article.id,
                ground_truth_json={
                    "use_ai_behavior": True,
                    "animal_sex": "BOTH_SEXES",
                    "n": [1, 2],
                },
            ),
            ArticlePrediction(
                article_id=article.id,
                prompt_template_id=template.id,
                model_used="Laya-400M",
                decision_json={"use_ai_behavior": False, "confidence_score": 0.42},
                evidence_quotes=["rats were housed in pairs", "n=12"],
            ),
            BenchmarkRun(project_id=project.id, models_tested=["Laya-400M", "Kev-0.8B"]),
        ]
    )
    await session.commit()

    g = (await session.execute(select(GoldArticle))).scalar_one()
    assert g.ground_truth_json == {"use_ai_behavior": True, "animal_sex": "BOTH_SEXES", "n": [1, 2]}

    p = (await session.execute(select(ArticlePrediction))).scalar_one()
    assert p.decision_json == {"use_ai_behavior": False, "confidence_score": 0.42}
    assert p.evidence_quotes == ["rats were housed in pairs", "n=12"]

    r = (await session.execute(select(BenchmarkRun))).scalar_one()
    assert r.models_tested == ["Laya-400M", "Kev-0.8B"]
