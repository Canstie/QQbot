"""Shared database limits and transcript normalization."""

import re
from datetime import timedelta, timezone

CHINA_TZ = timezone(timedelta(hours=8))
_GROUP_MESSAGE_RETENTION_DAYS = 7
_RUNTIME_CACHE_TTL_SECONDS = 5.0
_MAX_DSAPI_HISTORY_TURNS = 50
_MAX_DSAPI_CONTEXT_MESSAGES = 100
_MAX_DSAPI_SIMILAR_EXAMPLES = 8
_MAX_DSAPI_REPLY_MESSAGES = 3


def _strip_cq_segments(message: str) -> str:
    return re.sub(r"\[CQ:[^\]]+\]", "", message)
