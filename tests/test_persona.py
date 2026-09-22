from __future__ import annotations

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.persona import (
    PersonaExample,
    build_persona_guidance,
    retrieve_similar_examples,
)
from qq_personal_bot.settings import AppSettings


def test_retrieval_prefers_current_topic_without_source_qq():
    examples = [
        PersonaExample("原神新池子又歪了", "骂它也不出货的", 100),
        PersonaExample("太刀这武器怎么样", "太刀怎么你了", 200),
        PersonaExample("今天吃什么", "白送的还嫌啥", 300),
    ]

    selected = retrieve_similar_examples(examples, "原神抽卡又歪了", limit=2)

    assert selected[0].reply == "骂它也不出货的"
    assert all(not hasattr(item, "target_user_id") for item in selected)


def test_build_persona_guidance_uses_independent_examples_and_ai_memory(tmp_path):
    db_path = tmp_path / "qqbot.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=()))
    knowledge = store.create_dsapi_knowledge_base(
        name="春雨",
        prompt="像普通群友一样接话",
        actor_id=0,
        relationship_memory_enabled=True,
        similar_examples=3,
        style_examples=[
            {"topic": "原神抽卡又歪了", "response": "骂它也不出货的"},
            {"topic": "有人吹自己很强", "response": "就这？"},
        ],
    )
    store.upsert_dsapi_relationship_memory(
        knowledge_id=knowledge["id"],
        user_id=100,
        group_id=123,
        summary="关系熟悉，习惯轻微互怼",
    )

    guidance = build_persona_guidance(
        store,
        knowledge,
        target_user_id=100,
        topic_text="抽卡又歪了",
    )

    assert "QQ 100" in guidance
    assert "关系熟悉" in guidance
    assert "骂它也不出货的" in guidance
    assert "参考角色 QQ" not in guidance
