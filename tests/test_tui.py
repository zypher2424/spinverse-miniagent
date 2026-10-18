"""用真实 UI 事件与后端验证交互，不把渲染快照当作运行成功。"""
import asyncio
import json
import threading
from pathlib import Path

from textual.widgets import Input

from context import Context
from llm import ChatResult
from main import parser, create_controller
from tools import workspace as workspace_module
from tui import TUIBridge, MiniAgentApp, PermissionDialog


def make_tui(tmp_path, yes=False):
    args = parser().parse_args(['--demo', '--session-dir', str(tmp_path / 'sessions')]
                               + (['--yes'] if yes else []))
    bridge = TUIBridge()
    controller = create_controller(args, bridge)
    return MiniAgentApp(controller, bridge), controller


async def until(pilot, condition, tries=200):
    for _ in range(tries):
        await pilot.pause(.03)
        if condition():
            return
    raise AssertionError('UI/worker 未在期限内进入预期状态')


def test_tui_send_approve_patch_check_and_resume(tmp_path):
    async def run():
        app, controller = make_tui(tmp_path)
        try:
            original_test = (controller.workspace / 'test_arithmetic.py').read_bytes()
            async with app.run_test(size=(120, 42)) as pilot:
                await pilot.pause(.1)
                # 输入框的真实 Enter 事件发送命令。
                app.query_one('#prompt', Input).value = '/demo'
                await pilot.press('enter')
                approved = 0
                while app.busy:
                    await until(pilot, lambda: isinstance(app.screen, PermissionDialog) or not app.busy)
                    if isinstance(app.screen, PermissionDialog):
                        if approved == 0:
                            assert 'return a + b' in app.confirmation.description
                        await pilot.press('y')
                        approved += 1
                assert approved == 3
                assert controller.last_result.completed
                assert not app.fatal
                assert all(line.outcome and line.outcome['status'] == 'success' for line in app.tool_lines)
                assert (controller.workspace / 'test_arithmetic.py').read_bytes() == original_test
                Context.validate(controller.agent.context.messages)
                assert app.query_one('#composer').region.bottom <= app.size.height
                old_id = controller.session.id
                actions = controller.session.data['actions']
                await app.submit('/new')
                await until(pilot, lambda: not app.busy)
                assert controller.session.id != old_id
                assert not app.tool_lines
                assert not any(m.role == "user" for m in app.query(".chat-message"))
                await app.submit('/resume ' + old_id)
                await until(pilot, lambda: not app.busy)
                assert controller.session.id == old_id
                assert controller.session.data['actions'] == actions
                assert any(m.role == "user" and "arithmetic.py" in m.body for m in app.query(".chat-message"))
                assert not app.fatal
        finally:
            controller.close()
    asyncio.run(run())


def test_tui_deny_does_not_modify_file(tmp_path):
    async def run():
        app, controller = make_tui(tmp_path)
        try:
            original = (controller.workspace / 'arithmetic.py').read_bytes()
            async with app.run_test(size=(100, 34)) as pilot:
                await app.submit('/demo')
                await until(pilot, lambda: isinstance(app.screen, PermissionDialog))
                await pilot.press('escape')
                await until(pilot, lambda: not app.busy)
                assert not controller.last_result.completed
                assert (controller.workspace / 'arithmetic.py').read_bytes() == original
                assert any(a.get('result', {}).get('error_code') == 'USER_DENIED'
                           for a in controller.session.data['actions'])
                assert not app.fatal
        finally:
            controller.close()
    asyncio.run(run())


def test_cancel_inflight_model_prevents_next_side_effect(tmp_path):
    class SlowModel:
        model = 'slow-test'
        entered = threading.Event()
        release = threading.Event()
        def chat(self, *a, **kw):
            self.entered.set()
            assert self.release.wait(5)
            return ChatResult(None, [{'id': 'late', 'name': 'write_file', 'arguments': json.dumps({'path': 'late.txt', 'content': 'must not happen'})}])
    async def run():
        app, controller = make_tui(tmp_path, yes=True)
        model = SlowModel()
        controller.args.direct = True
        controller.agent.llm = model
        try:
            async with app.run_test(size=(100, 34)) as pilot:
                await app.submit('write')
                await until(pilot, model.entered.is_set)
                await pilot.press('ctrl+c')
                assert controller.cancel_event.is_set()
                assert app.state == 'Cancelling'
                model.release.set()
                await until(pilot, lambda: not app.busy)
                assert controller.last_result.stop_reason == 'cancelled'
                assert not (controller.workspace / 'late.txt').exists()
                assert not controller.session.data['actions']
                assert app.state == 'Cancelled'
                Context.validate(controller.agent.context.messages)
        finally:
            model.release.set()
            controller.close()
    asyncio.run(run())


def test_cancel_shell_kills_process_and_preserves_pairing(tmp_path):
    async def run():
        app, controller = make_tui(tmp_path, yes=True)
        (controller.workspace / 'slow.py').write_text('import time\nprint("started",flush=True)\ntime.sleep(30)\n')
        class Model:
            def chat(self, *a, **kw):
                return ChatResult(None, [{'id': 'slow', 'name': 'shell', 'arguments': json.dumps({'command': 'python', 'args': ['slow.py']})}])
        controller.agent.llm = Model()
        controller.args.direct = True
        try:
            async with app.run_test(size=(100, 34)) as pilot:
                await app.submit('run slow')
                await until(pilot, lambda: any(a['phase'] == 'started' for a in controller.session.data['actions']))
                await pilot.press('ctrl+c')
                await until(pilot, lambda: not app.busy)
                assert controller.last_result.stop_reason == 'cancelled'
                last = controller.session.data['actions'][-1]
                assert last['phase'] == 'completed'
                assert last['result']['error_code'] == 'CANCELLED'
                assert not controller.session.unknown_actions()
                Context.validate(controller.agent.context.messages)
        finally:
            controller.close()
    asyncio.run(run())


def test_small_terminal_resize_keeps_composer_visible(tmp_path):
    async def run():
        app, controller = make_tui(tmp_path, yes=True)
        try:
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause(.1)
                composer = app.query_one('#composer')
                assert 0 <= composer.region.y and composer.region.bottom <= 24
                assert app.query_one('#prompt').region.height > 0
                await pilot.resize_terminal(60, 20)
                await pilot.pause(.1)
                assert composer.region.bottom <= 20
                assert app.query_one('#conversation').region.height > 0
                # 命令经后台路由执行，长记录滚动也不会推走固定输入框。
                await app.submit('/help')
                await until(pilot, lambda: not app.busy)
                assert composer.region.bottom <= 20
                assert not app.fatal
        finally:
            controller.close()
    asyncio.run(run())
