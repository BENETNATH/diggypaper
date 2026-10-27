"""Cascade 2 étages (petit modèle sur titre + abstract → modèle principal) — offline."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Article,
    ArticlePrediction,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    PredictionStatus,
    Project,
    ProjectStatus,
    PromptTemplate,
)
from app.tasks.inference import _run_inference


class FakeChat:
    """L'étage 1 (« small:1b ») rejette l'article « low » ; l'étage 2 accepte."""

    def __init__(self):
        self.models: list[str] = []

    async def infer(self, model, system_prompt, user_prompt) -> dict:
        if '{"ok": true}' in user_prompt:  # warm-up : ne compte pas
            return {"ok": True}
        self.models.append(model)
        score = 0.05 if model == "small:1b" and "low" in user_prompt else 0.9
        return {"confidence_score": score, "evidence_quotes": [], "reasoning_summary": ""}


async def test_prefilter_rejects_before_main_model(engine, session, fake_redis):
    project = Project(title="P", status=ProjectStatus.FROZEN)
    session.add(project)
    await session.commit()
    prompt = PromptTemplate(
        project_id=project.id,
        version_number="v1",
        system_prompt="S",
        user_prompt_schema="{article_text}",
    )
    session.add_all(
        [
            prompt,
            ModelRegistry(name="Small", model_identifier="small:1b"),
            ModelRegistry(name="Big", model_identifier="big:30b"),
            Article(project_id=project.id, pmid="1", title="t1", abstract="low"),
            Article(project_id=project.id, pmid="2", title="t2", abstract="mid"),
        ]
    )
    await session.commit()
    job = InferenceJob(
        project_id=project.id,
        prompt_template_id=prompt.id,
        model_used="Big",
        prefilter_model="Small",
        status=InferenceJobStatus.PENDING,
    )
    session.add(job)
    await session.commit()

    chat = FakeChat()
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    result = await _run_inference(
        str(job.id), session_factory=factory, inference=chat, redis=fake_redis
    )

    # étage 1 sur les 2 articles ; l'étage 2 ne voit que l'article « mid »
    assert sorted(chat.models) == ["big:30b", "small:1b", "small:1b"]
    assert (
        result["prefiltered"] == 1 and result["auto_rejected"] == 1 and result["auto_accepted"] == 1
    )
    preds = {
        p.model_used: p.status for p in (await session.execute(select(ArticlePrediction))).scalars()
    }
    assert preds == {"Small": PredictionStatus.AUTO_REJECTED, "Big": PredictionStatus.AUTO_ACCEPTED}
