from __future__ import annotations

import re

from qq_personal_bot.core.models import MessageEvent

_QUOTED_TEXT = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"|`[^`]*`')
_REQUEST_PREFIX = (
    r"(?:(?:请你|请|麻烦你|麻烦|帮我|帮忙|给我|给大家|你来|你|"
    r"能不能|能否|可不可以|可以|能|来个|来段|来|如何|怎么|对|给|"
    r"客观地?|中立地?|简单地?|认真地?|全面地?|好好地?)\s*)*+"
)
_EVALUATION_REQUEST = re.compile(
    r"^" + _REQUEST_PREFIX
    + r"(?:锐评|评价|点评|评论|评说|评判|评一评|评评|批判|批评|吐槽|分析)"
    + r"(?!了|过|的|是|这个词|是什么意思|功能|一下是什么意思)"
)
_OPINION_REQUEST = re.compile(
    r"^(?:请问|请说说|说说)?(?:你|你们|大家)?(?:"
    r"(?:怎么|如何)看(?:待)?|"
    r"对.{0,40}(?:怎么看|有什么看法|有何看法|有什么评价|有何评价|印象如何|印象怎么样)|"
    r"(?:觉得|认为).{0,40}(?:怎么样|如何|是个什么样的人|是什么样的人))"
)


def is_person_evaluation_request(text: str) -> bool:
    """Recognize requests, excluding quoted examples and ordinary reported speech."""
    text = _QUOTED_TEXT.sub("", text).strip(" \t\r\n，,。.!！?？:：")
    return bool(_EVALUATION_REQUEST.search(text) or _OPINION_REQUEST.search(text))


def evaluation_target_ids(event: MessageEvent, *, self_id: int | str) -> list[int]:
    """Only actual top-level mentions count; replies, @all and the bot do not."""
    targets: list[int] = []
    for segment in event.segments:
        if segment.get("type") != "at":
            continue
        try:
            user_id = int((segment.get("data") or {}).get("qq"))
        except (TypeError, ValueError):
            continue
        if user_id > 0 and str(user_id) != str(self_id) and user_id not in targets:
            targets.append(user_id)
    return targets
