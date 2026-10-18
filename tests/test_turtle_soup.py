from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from qq_personal_bot import dsapi
from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.policy import PolicyEngine
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings
from qq_personal_bot.turtle_soup import GAME_CONTEXT, TurtleSoupService

PUZZLE = {'title': '雨伞', 'surface': '小明带着伞出门，回家时头发却湿了，为什么？',
          'solution': '小明是游泳教练，回家前刚在泳池游完泳。',
          'facts': ['小明是游泳教练', '头发是游泳时弄湿的', '伞没有损坏']}


@pytest.fixture
def game(tmp_path, monkeypatch):
    settings = AppSettings(db_path=tmp_path / 'soup.sqlite3', admins=(), dsapi_api_key='test',
                           per_group_seconds=0, per_user_per_minute=0)
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    for group in (123, 789):
        store.set_group_enabled(group, True, actor_id=0)
        store.enable_dsapi_group(group, actor_id=0)
    now = [1000.0]
    service = TurtleSoupService(store, settings, clock=lambda: now[0])
    request = Mock(side_effect=lambda settings, messages, **kw: json.dumps(
        PUZZLE if '创作' in messages[0]['content'] else {'answer': '不是。'}, ensure_ascii=False))
    monkeypatch.setattr(dsapi, '_request_chat_completion_with_fallback', request)
    send = AsyncMock()
    engine = PolicyEngine(store)
    events = []

    async def run(text, *, at=True, group_id=123, user_id=456, message_id=None, sender=None):
        event = MessageEvent(platform='onebot.v11', message_id=message_id or len(events) + 1,
                             group_id=group_id, user_id=user_id, raw_message=text,
                             is_at_bot=at, timestamp=now[0],
                             segments=({'type': 'at', 'data': {'qq': '999'}},) if at else ())
        events.append(event)
        return await service.handle(event, '999', sender or send,
                                    lambda: engine.evaluate(event, self_id=999))

    return SimpleNamespace(store=store, settings=settings, now=now, service=service,
                           request=request, send=send, run=run, events=events)


@pytest.mark.asyncio
async def test_game_keeps_fixed_solution_beyond_ordinary_history_and_only_records_mentions(game):
    assert await game.run('~海龟汤', at=False)
    assert PUZZLE['surface'] in game.send.call_args.args[0]
    assert PUZZLE['solution'] not in game.send.call_args.args[0]
    game.now[0] += 30
    assert not await game.run('大家聊点别的', at=False)
    assert game.service.active(123)['deadline'] == 1180
    for index in range(52):
        await game.run(f'这是第{index}个问题吗？')
    current = game.service.active(123)
    assert current['puzzle'] == PUZZLE and len(current['turns']) == 52
    messages = game.request.call_args.args[1]
    assert PUZZLE['solution'] in messages[0]['content']
    assert any(item['content'] == '这是第0个问题吗？' for item in messages)
    assert not any('大家聊点别的' in item['content'] for item in messages)
    assert game.store.get_dsapi_chat_history(123, 50) == []
    with game.store._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM dsapi_group_context').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_repeated_start_reuses_puzzle_and_groups_are_isolated(game):
    await game.run('~海龟汤', at=False)
    original = game.service.active(123)['id']
    await game.run('~海龟汤', at=False)
    assert game.request.call_count == 1
    assert game.service.active(123)['id'] == original
    assert not await game.run('普通聊天', group_id=789)
    await game.run('~海龟汤', at=False, group_id=789)
    await game.run('取消')
    assert game.service.active(123) is None and game.service.active(789)


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['~海龟汤', '~海龟汤 惊悚奇幻一点'])
async def test_self_sent_prefix_can_start_but_bot_outputs_do_not_become_player_turns(game, command):
    assert await game.run(command, at=False, user_id=999)
    assert game.service.active(123)
    assert not await game.run('海龟汤：雨伞', at=False, user_id=999)
    assert not await game.run('不是。', at=True, user_id=999)
    assert game.service.active(123)['turns'] == []
    assert game.request.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('command,requirements', [
    ('~海龟汤 惊悚奇幻一点', '惊悚奇幻一点'),
    ('  ~海龟汤   惊悚奇幻一点  ', '惊悚奇幻一点'),
    ('~海龟汤\n惊悚奇幻一点\n难度简单', '惊悚奇幻一点\n难度简单'),
    ('~海龟汤\t惊悚奇幻一点', '惊悚奇幻一点'),
])
async def test_themed_start_forwards_requirements_as_user_content(game, command, requirements):
    assert await game.run(command, at=False)
    assert game.service.active(123)['puzzle'] == PUZZLE
    game.request.assert_called_once()
    messages = game.request.call_args.args[1]
    assert messages[0]['role'] == 'system'
    assert requirements not in messages[0]['content']
    assert any(message['role'] == 'user' and requirements in message['content']
               for message in messages)
    system = messages[0]['content']
    for field in ('"title"', '"surface"', '"solution"', '"facts"'):
        assert field in system
    assert 'JSON' in system and '不能改变' in system
    assert '奇幻' in system and '固定' in system and '公平推理' in system
    assert game.service.active(123)['turns'] == []
    assert PUZZLE['solution'] not in game.send.call_args.args[0]


@pytest.mark.asyncio
async def test_start_without_requirements_still_requests_a_new_puzzle(game):
    assert await game.run('~海龟汤\n  ', at=False)
    assert game.service.active(123)['puzzle'] == PUZZLE
    messages = game.request.call_args.args[1]
    assert any(message['role'] == 'user' and '新题' in message['content']
               for message in messages)


@pytest.mark.asyncio
@pytest.mark.parametrize('requirements', ['换成科幻一点', '奇' * 2001])
async def test_active_game_ignores_new_requirements_and_shows_existing_puzzle(game, requirements):
    await game.run('~海龟汤 惊悚奇幻一点', at=False)
    await game.run('伞坏了吗？')
    original = game.service.active(123)
    calls = game.request.call_count
    assert await game.run('~海龟汤 ' + requirements, at=False)
    current = game.service.active(123)
    assert current['id'] == original['id']
    assert current['puzzle'] == original['puzzle']
    assert current['turns'] == original['turns']
    assert game.request.call_count == calls
    assert PUZZLE['surface'] in game.send.call_args.args[0]
    assert PUZZLE['solution'] not in game.send.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['~海龟汤圆 惊悚', '~海龟汤惊悚'])
async def test_near_match_does_not_start_game(game, command):
    assert not await game.run(command, at=False)
    assert game.service.active(123) is None
    game.request.assert_not_called()
    game.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_creation_requirements_limit_rejects_before_game_or_request(game, monkeypatch):
    create = Mock(wraps=game.store.create_turtle_soup_game)
    monkeypatch.setattr(game.store, 'create_turtle_soup_game', create)
    assert await game.run('~海龟汤 ' + '奇' * 2001, at=False)
    create.assert_not_called()
    game.request.assert_not_called()
    assert game.service.active(123) is None
    assert '2000' in game.send.call_args.args[0]
    requirements = '奇' * 2000
    assert await game.run('~海龟汤 ' + requirements, at=False)
    create.assert_called_once()
    game.request.assert_called_once()
    assert game.service.active(123)['puzzle'] == PUZZLE
    assert any(message['role'] == 'user' and requirements in message['content']
               for message in game.request.call_args.args[1])


@pytest.mark.asyncio
async def test_failure_excludes_waiting_time_and_retry_reuses_same_question(game, monkeypatch):
    await game.run('~海龟汤', at=False)

    async def fail(game_state, messages, **kw):
        assert game.service.active(123)['deadline'] is None
        game.now[0] += 250
        notice = AsyncMock()
        await game.service.expire(notice)
        notice.assert_not_awaited()
        raise dsapi.DSAPIError('connection failed')

    original = game.service._complete
    monkeypatch.setattr(game.service, '_complete', fail)
    await game.run('伞坏了吗？')
    current = game.service.active(123)
    assert current['deadline'] == 1430
    assert current['turns'][0]['answer'] == ''
    assert '进度已保存' in game.send.call_args.args[0]
    monkeypatch.setattr(game.service, '_complete', original)
    await game.run('重试', user_id=555)
    current = game.service.active(123)
    assert len(current['turns']) == 1
    assert game.request.call_args.args[1][-1]['content'] == '伞坏了吗？'
    assert current['turns'][0]['delivered']
    game.now[0] = 1429
    notice = AsyncMock()
    await game.service.expire(notice)
    notice.assert_not_awaited()
    game.now[0] = 1430
    await game.service.expire(notice)
    assert game.service.active(123) is None
    notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_qq_send_is_replayed_without_another_generation(game):
    await game.run('~海龟汤', at=False)
    failed_send = AsyncMock(side_effect=OSError('QQ offline'))
    await game.run('下雨了吗？', sender=failed_send)
    current = game.service.active(123)
    assert current['turns'][0]['answer'] == '不是。'
    assert not current['turns'][0]['delivered']
    calls = game.request.call_count
    await game.run('重试')
    assert game.request.call_count == calls
    assert game.send.call_args.args[0] == failed_send.call_args.args[0]
    assert game.service.active(123)['turns'][0]['delivered']


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['完成', '取消'])
async def test_exit_is_local_even_if_ai_disabled_and_restores_normal_history(game, command):
    game.store.record_dsapi_exchange(group_id=123, user_content='之前的问题',
                                    assistant_content='之前的答案', history_turns=2)
    with game.store._connect() as conn:
        conn.execute('UPDATE dsapi_chat_history SET created_at=990')
    await game.run('~海龟汤', at=False)
    game.now[0] = 3000
    game.store.set_feature_enabled('ai.master', False)
    game.service.settings = replace(game.settings, dsapi_api_key='')
    await game.run(command)
    assert game.service.active(123) is None
    if command == '完成':
        assert PUZZLE['solution'] in game.send.call_args.args[0]
    else:
        assert PUZZLE['solution'] not in game.send.call_args.args[0]
    assert '恢复普通' in game.send.call_args.args[0]
    assert game.request.call_count == 1
    assert game.store.expire_dsapi_chat_history(123, idle_seconds=1200, now=3000) == 0
    assert game.store.get_dsapi_chat_history(123, 2)[0]['content'] == '之前的问题'


@pytest.mark.asyncio
async def test_plain_finish_is_not_an_exit_and_duplicate_event_does_not_repeat_turn(game):
    await game.run('~海龟汤', at=False)
    assert not await game.run('完成', at=False)
    await game.run('伞坏了吗？', message_id=50)
    calls = game.send.await_count
    await game.run('伞坏了吗？', message_id=50)
    assert game.send.await_count == calls
    assert len(game.service.active(123)['turns']) == 1


@pytest.mark.asyncio
async def test_simultaneous_questions_are_serialized_with_updated_history(game, monkeypatch):
    await game.run('~海龟汤', at=False)
    entered, release = asyncio.Event(), asyncio.Event()
    inputs = []

    async def complete(state, messages, **kw):
        inputs.append(messages)
        if len(inputs) == 1:
            entered.set()
            await release.wait()
        return '{"answer":"不是。"}'

    monkeypatch.setattr(game.service, '_complete', complete)
    first = asyncio.create_task(game.run('第一问'))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(game.run('第二问', user_id=555))
    await asyncio.sleep(0)
    assert len(inputs) == 1 and game.service.active(123)['deadline'] is None
    release.set()
    await asyncio.gather(first, second)
    assert any(message['content'] == '第一问' for message in inputs[1][:-1])
    assert [turn['question'] for turn in game.service.active(123)['turns']] == ['第一问', '第二问']
    assert game.service.active(123)['deadline'] == 1180


@pytest.mark.asyncio
@pytest.mark.parametrize('opening', [True, False])
async def test_cancel_interrupts_generation_and_discards_late_response(game, monkeypatch, opening):
    if not opening:
        await game.run('~海龟汤', at=False)
    entered = asyncio.Event()

    async def complete(*args, **kw):
        entered.set()
        await asyncio.Event().wait()
        return ''

    original_to_thread = asyncio.to_thread

    def to_thread(func, *args, **kw):
        if func is dsapi._request_chat_completion_with_fallback:
            return complete()
        return original_to_thread(func, *args, **kw)

    monkeypatch.setattr(asyncio, 'to_thread', to_thread)
    pending = asyncio.create_task(game.run('~海龟汤' if opening else '第一问', at=not opening))
    await asyncio.wait_for(entered.wait(), 1)
    await game.run('取消')
    await asyncio.wait_for(pending, 1)
    assert game.service.active(123) is None
    assert '已取消' in game.send.call_args.args[0]
    assert game.service._generations == {} and game.service._waiting == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('response', ['not json', '[]', '{"surface":"题目"}'])
async def test_invalid_opening_returns_to_normal_chat(game, response):
    game.request.side_effect = None
    game.request.return_value = response
    await game.run('~海龟汤', at=False)
    assert game.service.active(123) is None
    assert '未开局' in game.send.call_args.args[0]


@pytest.mark.asyncio
async def test_invalid_answer_retains_puzzle_without_recording_failure_as_fact(game):
    await game.run('~海龟汤', at=False)
    game.request.side_effect = None
    game.request.return_value = '{"answer":123}'
    await game.run('他认识死者吗？')
    current = game.service.active(123)
    assert current['puzzle'] == PUZZLE
    assert current['turns'][0]['answer'] == ''
    assert '失败' in game.send.call_args.args[0]


def test_restart_restores_puzzle_pending_question_and_recovers_paused_timer(game):
    first = game.store.create_turtle_soup_game(123, '999', now=1000)
    game.store.update_turtle_soup_game(first['id'], puzzle=PUZZLE,
                                     turns=[{'question': '伞坏了吗？', 'answer': '',
                                             'message_id': '2', 'user_id': 456, 'delivered': False}])
    game.store.create_turtle_soup_game(789, '999', now=1000)
    store = PolicyStore(game.settings.db_path)
    store.initialize(game.settings)
    service = TurtleSoupService(store, game.settings, clock=lambda: 2000)
    service.recover()
    current = service.active(123)
    assert current['puzzle'] == PUZZLE
    assert current['turns'][0]['question'] == '伞坏了吗？'
    assert current['deadline'] == 2180
    assert service.active(789) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('gate', ['group', 'ai.master', 'ai.mention', 'ai.turtle_soup', 'blacklist'])
async def test_start_obeys_group_switches_and_blacklist(game, gate):
    if gate == 'group':
        game.store.set_group_enabled(123, False, actor_id=0)
    elif gate == 'blacklist':
        game.store.set_dsapi_user_blocked(456, True, actor_id=0)
    else:
        game.store.set_feature_enabled(gate, False)
    await game.run('~海龟汤', at=False)
    assert game.service.active(123) is None
    game.request.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_routes_mentions_before_other_ai_and_resumes_after_exit(game, monkeypatch):
    from qq_personal_bot.plugins import chat

    monkeypatch.setattr(chat, 'get_turtle_soup_service', lambda: game.service)
    monkeypatch.setattr(chat, 'get_store', lambda: game.store)
    monkeypatch.setattr(chat, 'get_settings', lambda: game.settings)
    monkeypatch.setattr(chat, 'get_policy_engine', lambda: PolicyEngine(game.store))
    monkeypatch.setattr(chat, '_record_group_activity', lambda *a, **kw: None)
    monkeypatch.setattr(chat, 'pending_lua_command', lambda event: None)
    monkeypatch.setattr(chat, 'handle_custom_flow', lambda event: None)
    monkeypatch.setattr(chat, 'run_lua_message', AsyncMock(
        return_value=SimpleNamespace(reply=None, stop=False)))
    mention, random, roast, digest = (AsyncMock(return_value='正常聊天') for _ in range(4))
    for name, mock in [('generate_mention_reply', mention), ('generate_random_group_reply', random),
                       ('generate_roast_reply', roast), ('generate_group_digest_report', digest)]:
        monkeypatch.setattr(chat, name, mock)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id='999')

    async def dispatch(text, at=False):
        event = SimpleNamespace(group_id=123, user_id=456, message_id=matcher.send.await_count + 20,
                                message=[{'type': 'text', 'data': {'text': text}}]
                                + ([{'type': 'at', 'data': {'qq': '999'}}] if at else []))
        await chat._dispatch_onebot_message(matcher, bot, event)

    await dispatch('~海龟汤')
    await dispatch('普通讨论')
    await dispatch('~总结')
    await dispatch('~锐评')
    await dispatch('伞坏了吗？', at=True)
    for mock in (mention, random, roast, digest):
        mock.assert_not_awaited()
    assert [turn['question'] for turn in game.service.active(123)['turns']] == ['伞坏了吗？']
    await dispatch('取消', at=True)
    await dispatch('正常聊天', at=True)
    mention.assert_awaited_once()
    assert game.service.active(123) is None


@pytest.mark.asyncio
async def test_graph_collection_continues_and_labels_game_discussion(game, monkeypatch):
    from qq_personal_bot.activity import close_group_activity
    from qq_personal_bot.memory_graph import refresh_memory_graph
    from qq_personal_bot.plugins import chat

    game.store.set_memory_groups([123], actor_id=0)
    monkeypatch.setattr(chat, 'get_store', lambda: game.store)
    await game.run('~海龟汤', at=False)
    chat._record_group_activity(MessageEvent(
        platform='onebot.v11', group_id=123, user_id=456, message_id=81,
        raw_message='我猜故事里的他是一名教练', timestamp=1001,
    ), self_id=999)
    chat._record_group_activity(MessageEvent(
        platform='onebot.v11', group_id=123, user_id=456, message_id=82,
        raw_message='伞坏了吗？', timestamp=1002, is_at_bot=True,
    ), self_id=999)
    await close_group_activity()
    records = game.store.get_memory_messages(123, 456)
    assert len(records) == 1 and records[0]['memory_context'] == GAME_CONTEXT
    game.request.side_effect = None
    game.request.return_value = '{"claims":[]}'
    assert await refresh_memory_graph(game.store, game.settings, 123, force=True)
    payload = json.loads(game.request.call_args.args[1][1]['content'])
    assert payload['messages'][0]['memory_context'] == GAME_CONTEXT


@pytest.mark.asyncio
async def test_ordinary_request_started_before_game_does_not_pollute_game_history(game, monkeypatch):
    async def to_thread(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if (func is dsapi._request_chat_completion_with_fallback
                or getattr(func, 'func', None) is dsapi._request_chat_completion_with_fallback):
            current = game.store.create_turtle_soup_game(123, '999', now=1000)
            game.store.update_turtle_soup_game(current['id'], puzzle=PUZZLE, deadline=1180)
        return result

    monkeypatch.setattr(asyncio, 'to_thread', to_thread)
    event = MessageEvent(platform='onebot.v11', group_id=123, user_id=456,
                         message_id=1, raw_message='正常聊天', is_at_bot=True)
    reply = await dsapi.generate_mention_reply(SimpleNamespace(), event, game.settings, game.store)
    assert reply is None
    assert game.store.get_dsapi_chat_history(123, 2) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_late_ordinary_reply_and_error_are_suppressed_after_game_starts(game, monkeypatch, failure):
    from qq_personal_bot.plugins import chat

    monkeypatch.setattr(chat, 'get_turtle_soup_service', lambda: game.service)
    monkeypatch.setattr(chat, 'get_store', lambda: game.store)
    monkeypatch.setattr(chat, 'get_settings', lambda: game.settings)
    monkeypatch.setattr(chat, 'get_policy_engine', lambda: PolicyEngine(game.store))
    monkeypatch.setattr(chat, '_record_group_activity', lambda *a, **kw: None)
    monkeypatch.setattr(chat, 'pending_lua_command', lambda event: None)
    monkeypatch.setattr(chat, 'handle_custom_flow', lambda event: None)
    monkeypatch.setattr(chat, 'run_lua_message', AsyncMock(
        return_value=SimpleNamespace(reply=None, stop=False)))

    async def ordinary(*args, **kw):
        await game.run('~海龟汤', at=False)
        if failure:
            raise dsapi.DSAPIError('old request failed')
        return '开局前的普通回答'

    monkeypatch.setattr(chat, 'generate_mention_reply', ordinary)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    event = SimpleNamespace(group_id=123, user_id=456, message_id=99,
                            message=[{'type': 'text', 'data': {'text': '普通聊天'}},
                                     {'type': 'at', 'data': {'qq': '999'}}])
    await chat._dispatch_onebot_message(matcher, SimpleNamespace(self_id='999'), event)
    matcher.finish.assert_not_awaited()
    matcher.send.assert_not_awaited()
    assert game.service.active(123)
