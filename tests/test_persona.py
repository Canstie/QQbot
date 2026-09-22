from __future__ import annotations

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.persona import (
    PersonaExample,
    build_persona_guidance,
    retrieve_similar_examples,
)


def test_retrieval_prefers_current_topic_and_same_qq():
    examples = [
        PersonaExample(100, "原神新池子又歪了", "骂它也不出货的", 100),
        PersonaExample(200, "太刀这武器怎么样", "太刀怎么你了", 200),
        PersonaExample(100, "今天吃什么", "白送的还嫌啥", 300),
    ]

    selected = retrieve_similar_examples(
        examples,
        "原神抽卡又歪了",
        target_user_id=100,
        limit=2,
    )

    assert selected[0].reply == "骂它也不出货的"
    assert all(item.target_user_id == 100 for item in selected)


def test_build_persona_guidance_uses_relationship_and_filters_system_messages(tmp_path):
    store = PolicyStore(tmp_path / "qqbot.sqlite3")
    store.path.touch()
    rows = [
        {"user_id": 100, "content": "这个抽卡池怎么样", "created_at": 1},
        {"user_id": 999, "content": "[回复]下次还赌", "created_at": 2},
        {"user_id": 100, "content": "再帮我看看", "created_at": 3},
        {
            "user_id": 999,
            "content": "⚠️ agent 处理出错：余额不足，请及时充值",
            "created_at": 4,
        },
    ]
    store.get_recent_group_messages = lambda *args, **kwargs: rows

    guidance = build_persona_guidance(
        store,
        {
            "persona_group_id": 123,
            "persona_user_id": 999,
            "similar_examples": 3,
            "relationships": [{"user_id": 100, "note": "关系亲近，习惯轻微互怼"}],
        },
        target_user_id=100,
        topic_text="抽卡又歪了",
    )

    assert "QQ 100" in guidance
    assert "关系亲近" in guidance
    assert "下次还赌" in guidance
    assert "余额不足" not in guidance
