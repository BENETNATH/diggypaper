"""Chat on the corpus (Chat RAG, spec 2026-11-07-chat-rag).

§1 core (pure), §2 service streaming, §3 endpoint (SSE) — 100% offline:
fake embedder for the corpus, mocked LLM transport, monkeypatched stream.
"""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from test_vectorize import FakeEmbedder

from app import embeddings as embeddings_mod
from app.core.security import hash_password
from app.db.models import (
    Article,
    InferenceJob,
    InferenceJobStatus,
    ModelRegistry,
    ModelShare,
    Project,
    ProjectMember,
    ProjectMemberRole,
    User,
    UserRole,
)
from app.inference import InferenceError, OpenAICompatibleService
from app.tasks.vectorize import vectorize_project

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_embedder():
    emb = FakeEmbedder()
    embeddings_mod._embedder = emb
    yield emb
    embeddings_mod._embedder = None


async def _make_user(session, email: str, role: UserRole, password: str) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        role=role,
        data_consent_given=True,
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def outsider(client, session) -> User:
    """A user with no project membership at all."""
    return await _make_user(session, "outsider@lab.fr", UserRole.RESEARCHER, "outsider-pass-123")


async def _chat_project(session, researcher_user, *, vectorize=True) -> str:
    """Project (LEAD: researcher) with 2 articles / 4 sections; vectorized."""
    project = Project(title="Chat P", created_by=researcher_user.id)
    session.add(project)
    await session.commit()
    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=researcher_user.id,
            role_in_project=ProjectMemberRole.LEAD,
        )
    )
    session.add_all(
        [
            Article(
                project_id=project.id,
                title="Zebrafish regeneration",
                pmid="91",
                sections=[
                    {
                        "title": "Methods",
                        "kind": "methods",
                        "text": "Cryoinjury was performed on adult zebrafish hearts.",
                    },
                    {
                        "title": "Results",
                        "kind": "results",
                        "text": "Phlda2 cells drive myocardial regeneration.",
                    },
                ],
            ),
            Article(
                project_id=project.id,
                title="Rat ventricle",
                pmid="92",
                sections=[
                    {
                        "title": "Methods",
                        "kind": "methods",
                        "text": "Rat ventricular tissue was sectioned and stained.",
                    },
                    {
                        "title": "Results",
                        "kind": "results",
                        "text": "Fibrosis limited the regenerative scar.",
                    },
                ],
            ),
        ]
    )
    await session.commit()
    if vectorize:
        await vectorize_project(session, project.id)
    session.add(ModelRegistry(name="Kev-4B", model_identifier="kef/kev-4b"))
    await session.flush()
    model = (
        await session.execute(select(ModelRegistry).where(ModelRegistry.name == "Kev-4B"))
    ).scalar_one()
    session.add(ModelShare(model_id=model.id, project_id=project.id))
    await session.commit()
    return str(project.id)


# ---------------------------------------------------------------------------
# §1 core — app/rag_chat
# ---------------------------------------------------------------------------


def test_build_context_numbers_sections_in_order():
    from app.rag_chat import build_context

    sections = [
        {
            "article": {"id": "a1", "title": "A"},
            "section_title": "Methods",
            "excerpt": "t1",
            "score": 0.9,
        },
        {
            "article": {"id": "a2", "title": "B"},
            "section_title": "Results",
            "excerpt": "t2",
            "score": 0.8,
        },
    ]
    block, included = build_context(sections)
    assert "[1]" in block and "[2]" in block
    assert "Methods" in block and "Results" in block
    assert [s["n"] for s in included] == [1, 2]
    assert included[0]["section_title"] == "Methods"


def test_build_context_budget_excludes_whole_sections():
    from app.rag_chat import build_context

    sections = [
        {
            "article": {"id": "a1", "title": "A"},
            "section_title": "Long",
            "excerpt": "x" * 500,
            "score": 0.9,
        },
        {
            "article": {"id": "a2", "title": "B"},
            "section_title": "Short",
            "excerpt": "y",
            "score": 0.8,
        },
        {
            "article": {"id": "a3", "title": "C"},
            "section_title": "Shorter",
            "excerpt": "z",
            "score": 0.7,
        },
    ]
    block, included = build_context(sections, budget_chars=300)
    # The 500-char section must be EXCLUDED (never cut in the middle).
    assert "Long" not in block
    assert all(len(s["excerpt"]) < 300 for s in included)
    # What fits keeps its original numbering order (excluded = [1]).
    assert [s["n"] for s in included] in ([1, 2], [2], [2, 3])
    assert block == "" or "[2]" in block


def test_build_context_empty_and_none_fit():
    from app.rag_chat import build_context

    assert build_context([]) == ("", [])
    block, included = build_context(
        [
            {
                "article": {"id": "a1", "title": "A"},
                "section_title": "Huge",
                "excerpt": "x" * 9999,
                "score": 0.9,
            }
        ],
        budget_chars=100,
    )
    assert (block, included) == ("", [])


def test_build_chat_messages_order_and_context():
    from app.rag_chat import build_chat_messages

    history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "r1"}]
    messages = build_chat_messages("SYS", history, "question?", "[1] T — ctx")
    assert messages[0] == {"role": "system", "content": "SYS"}
    assert messages[1] == {"role": "user", "content": "q1"}
    assert messages[2] == {"role": "assistant", "content": "r1"}
    last = messages[-1]
    assert last["role"] == "user"
    assert "[1] T — ctx" in last["content"] and "question?" in last["content"]


def test_build_chat_messages_history_bounded_to_last_6_pairs():
    from app.rag_chat import build_chat_messages

    history = []
    for i in range(10):
        history.append({"role": "user", "content": f"q{i}"})
        history.append({"role": "assistant", "content": f"r{i}"})
    messages = build_chat_messages("SYS", history, "final?", "CTX")
    # system + 12 messages (6 pairs) + final user
    assert len(messages) == 1 + 12 + 1
    joined = "".join(m["content"] for m in messages[1:-1])
    assert "q0" not in joined and "r0" not in joined
    assert "q7" in joined and "q9" in joined  # last 6 pairs kept


def test_build_chat_messages_drops_unknown_roles():
    from app.rag_chat import build_chat_messages

    history = [
        {"role": "user", "content": "ok"},
        {"role": "tool", "content": "drop me"},
        {"role": "assistant", "content": "fine"},
    ]
    messages = build_chat_messages("SYS", history, "q?", "CTX")
    assert all(m["role"] in ("user", "assistant") for m in messages[1:-1])
    assert all("drop me" not in m["content"] for m in messages)


def test_extract_citations():
    from app.rag_chat import extract_citations

    assert extract_citations("Selon [1] et [3], oui.", n_sections=4) == [1, 3]
    assert extract_citations("[2][2][1]", n_sections=3) == [1, 2]  # dedup + sorted
    assert extract_citations("[1] et [9] hors périmètre", n_sections=3) == [1]
    assert extract_citations("aucun marqueur", n_sections=3) == []


# ---------------------------------------------------------------------------
# §2 service streaming — OpenAICompatibleService.chat_stream
# ---------------------------------------------------------------------------


def _ndjson(*lines: dict) -> httpx.Response:
    body = "\n".join(json.dumps(ln) for ln in lines)
    return httpx.Response(
        200, content=body.encode(), headers={"content-type": "application/x-ndjson"}
    )


def _sse(*data: str) -> httpx.Response:
    body = "\n".join(f"data: {d}" for d in data) + "\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


async def test_chat_stream_ollama_ndjson():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["req"] = req
        return _ndjson(
            {
                "model": "kef/kev-4b",
                "message": {"role": "assistant", "content": "Salut "},
                "done": False,
            },
            {
                "model": "kef/kev-4b",
                "message": {"role": "assistant", "content": "[1] monde"},
                "done": True,
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = OpenAICompatibleService("http://llm.test/v1", native_ollama=True, http=client)
    chunks = [
        c
        async for c in svc.chat_stream(
            "kef/kev-4b", [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
        )
    ]
    assert "".join(chunks) == "Salut [1] monde"
    # The request must hit the NATIVE /api/chat, stream=true, no json format.
    body = json.loads(captured["req"].content)
    assert str(captured["req"].url) == "http://llm.test/api/chat"
    assert body["stream"] is True
    assert "format" not in body and "response_format" not in body


async def test_chat_stream_openai_sse():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: _sse(
                '{"choices": [{"delta": {"content": "Salut "}}]}',
                '{"choices": [{"delta": {"content": "[1] ok"}}]}',
            )
        )
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    chunks = [c async for c in svc.chat_stream("m", [{"role": "user", "content": "q"}])]
    assert "".join(chunks) == "Salut [1] ok"


async def test_chat_stream_http_error_raises():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(400, content=b"bad"))
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    with pytest.raises(InferenceError):
        async for _ in svc.chat_stream("m", [{"role": "user", "content": "q"}]):
            pass


async def test_chat_stream_invalid_line_raises():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, content=b"not-json\n", headers={"content-type": "application/x-ndjson"}
            )
        )
    )
    svc = OpenAICompatibleService("http://llm.test/v1", native_ollama=True, http=client)
    with pytest.raises(InferenceError):
        async for _ in svc.chat_stream("m", [{"role": "user", "content": "q"}]):
            pass


async def test_chat_stream_openai_malformed_delta_raises():
    """A non-dict ``delta`` (malformed provider line) raises InferenceError —
    previously the AttributeError propagated: silent truncation after the 200."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, content=b'data: {"choices":[{"delta":"oops"}]}\n'
            )
        )
    )
    svc = OpenAICompatibleService("http://llm.test/v1", http=client)
    with pytest.raises(InferenceError):
        async for _ in svc.chat_stream("m", [{"role": "user", "content": "q"}]):
            pass


# ---------------------------------------------------------------------------
# §3 endpoint — POST /projects/{id}/chat (SSE)
# ---------------------------------------------------------------------------


def _sse_events(lines: list[str]) -> dict[str, list[dict]]:
    """Parses raw SSE lines into {event: [data dicts]}."""
    events: dict[str, list[dict]] = {}
    current = None
    for line in lines:
        if line.startswith("event: "):
            current = line[len("event: ") :].strip()
            events.setdefault(current, [])
        elif line.startswith("data: "):
            events[current].append(json.loads(line[len("data: ") :]))
    return events


async def _read_sse(client, project_id: str, body: dict):
    async with client.stream("POST", f"/api/v1/projects/{project_id}/chat", json=body) as resp:
        lines = [line async for line in resp.aiter_lines()]
        return resp.status_code, _sse_events(lines)


async def test_chat_non_member_403(client, session, researcher_user, do_login, outsider):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)
    await do_login("outsider@lab.fr", "outsider-pass-123", consent=True)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/chat",
        json={"question": "comment ?", "model": "Kev-4B"},
    )
    assert resp.status_code == 403


async def test_chat_unknown_model_400(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/chat",
        json={"question": "comment ?", "model": "Nope-9B"},
    )
    assert resp.status_code == 400
    assert "unknown_model" in resp.text


async def test_chat_empty_corpus_400(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user, vectorize=False)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/chat",
        json={"question": "comment ?", "model": "Kev-4B"},
    )
    assert resp.status_code == 400
    assert "corpus_empty" in resp.text


async def test_chat_model_busy_409(client, session, researcher_user, do_login):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)
    job = InferenceJob(
        project_id=uuid.UUID(project_id),
        model_used="Kev-4B",
        status=InferenceJobStatus.RUNNING,
    )
    session.add(job)
    await session.commit()
    resp = await client.post(
        f"/api/v1/projects/{project_id}/chat",
        json={"question": "comment ?", "model": "Kev-4B"},
    )
    assert resp.status_code == 409
    assert "model_busy" in resp.text


async def test_chat_happy_path_sse(client, session, researcher_user, do_login, monkeypatch):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)

    async def fake_stream(self, model, messages):
        # Contract: messages end with the user turn carrying the context.
        assert messages[0]["role"] == "system"
        assert "[1]" in messages[-1]["content"]
        yield "D'après "
        yield "[1] la cryolésion "
        yield "et [2] la fibrose."

    monkeypatch.setattr(OpenAICompatibleService, "chat_stream", fake_stream)

    status, events = await _read_sse(client, project_id, {"question": "Quoi ?", "model": "Kev-4B"})
    assert status == 200
    tokens = "".join(e["t"] for e in events.get("token", []))
    assert tokens == "D'après [1] la cryolésion et [2] la fibrose."
    done = events["done"][0]
    assert done["full_text"] == tokens
    assert done["model"] == "Kev-4B"
    assert done["n_citations"] == 2
    citations = events["citations"][0]
    # The cited sections carry their real corpus refs (article + title + excerpt).
    keys = {c["n"]: c for c in citations}
    assert set(keys) == {1, 2}
    assert all("title" in c and "excerpt" in c and "article_id" in c for c in citations)


async def test_chat_happy_path_with_history(
    client, session, researcher_user, do_login, monkeypatch
):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)
    seen = {}

    async def fake_stream(self, model, messages):
        seen["n"] = len(messages)
        seen["hist"] = [m["content"] for m in messages if m["role"] in ("user", "assistant")][-3:]
        yield "ok [1]"

    monkeypatch.setattr(OpenAICompatibleService, "chat_stream", fake_stream)
    status, events = await _read_sse(
        client,
        project_id,
        {
            "question": "et donc ?",
            "model": "Kev-4B",
            "history": [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "r1"},
            ],
        },
    )
    assert status == 200
    # system + q1 + r1 + final = 4 messages
    assert seen["n"] == 4
    assert seen["hist"][:2] == ["q1", "r1"]
    # The final user turn carries the context + the question (spec §1).
    assert seen["hist"][-1].startswith("CONTEXTE:")
    assert seen["hist"][-1].endswith("QUESTION:\net donc ?")


async def test_chat_rate_limit_21st_429(client, session, researcher_user, do_login, monkeypatch):
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)

    async def fake_stream(self, model, messages):
        yield "x"

    monkeypatch.setattr(OpenAICompatibleService, "chat_stream", fake_stream)
    last = None
    for i in range(21):
        resp = await client.post(
            f"/api/v1/projects/{project_id}/chat",
            json={"question": f"q{i}", "model": "Kev-4B"},
        )
        last = resp
    assert last.status_code == 429


# --- run-3: midstream exceptions — sanitized error event, never silent truncation ----


async def test_chat_midstream_unhandled_exception_error_event(
    client, session, researcher_user, do_login, monkeypatch
):
    """A non-InferenceError exception after the 200 (a provider/decrypt crash):
    the stream ends with a sanitized ``error`` event — no partial-silent answer,
    no exception detail in the response."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)

    async def broken_stream(model, service, messages):
        yield "partie "
        raise RuntimeError("boom internal detail")

    monkeypatch.setattr("app.api.v1.rag_chat.stream_with", broken_stream)
    status, events = await _read_sse(client, project_id, {"question": "Q ?", "model": "Kev-4B"})
    assert status == 200
    assert "".join(e["t"] for e in events.get("token", [])) == "partie "
    assert events["error"][0]["detail"] == "provider_error"
    assert "done" not in events
    assert "boom" not in str(events)  # the exception message never leaks


async def test_chat_decrypt_failure_error_event(client, session, researcher_user, do_login):
    """A corrupted ``api_key_enc`` (the decrypt crashes inside stream_with) ends
    the stream with a sanitized ``error`` event."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)
    m = ModelRegistry(
        name="Ext-4B",
        backend_type="OPENAI_API",
        model_identifier="http://ext.example/v1#m",
        api_key_enc=b"\x00\x01",
    )
    session.add(m)
    await session.flush()
    session.add(ModelShare(model_id=m.id, project_id=uuid.UUID(project_id)))
    await session.commit()

    status, events = await _read_sse(client, project_id, {"question": "Q ?", "model": "Ext-4B"})
    assert status == 200
    assert events["error"][0]["detail"] == "invalid_response"
    assert "done" not in events


async def test_chat_http_error_sse_fixed_code(
    client, session, researcher_user, do_login, monkeypatch
):
    """The SSE error event carries a fixed code only: the provider status
    (HTTP 401) would tell a project member that the owner's key is rejected."""
    import app.api.v1.rag_chat as rc

    async def fake_stream(model, service, messages):
        raise InferenceError("HTTP 401")
        yield ""  # pragma: no cover

    monkeypatch.setattr(rc, "stream_with", fake_stream)
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)
    status, events = await _read_sse(
        client, project_id, {"question": "Q ?", "model": "Kev-4B"}
    )
    assert status == 200
    assert events["error"][0]["detail"] == "http_error"
    assert "401" not in json.dumps(events)


async def test_chat_project_shared_model_annotator_gate(
    client, session, researcher_user, do_login, outsider
):
    """A project share grants the model to the LEADs only: an ANNOTATOR member
    no longer passes the model gate (unknown_model, not corpus_empty)."""
    await do_login("researcher@lab.fr", "researcher-pass-123", consent=True)
    project_id = await _chat_project(session, researcher_user)
    session.add(
        ProjectMember(
            project_id=uuid.UUID(project_id),
            user_id=outsider.id,
            role_in_project=ProjectMemberRole.ANNOTATOR,
        )
    )
    await session.commit()
    await do_login("outsider@lab.fr", "outsider-pass-123", consent=True)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/chat", json={"question": "q", "model": "Kev-4B"}
    )
    assert resp.status_code == 400
    assert "unknown_model" in resp.text
