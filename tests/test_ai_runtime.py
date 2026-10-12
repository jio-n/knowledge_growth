"""Real subprocess/stdio acceptance tests, entirely offline and API-key-free."""
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient
from app.ai import create_runtime
from app.ai.base import AIError
from app.ai.codex import CodexRuntime
from app.ai.compat import RuntimeProvider
from app.ai.offline import NoAIRuntime, MockRuntime
from app.ai.process import child_environment
from app.main import create_app
from app.llm.base import LLMError
from tests.fixtures.pdf_factory import make_pdf
from tests.fixtures.kgpack_factory import package_data
from app.import_bridge.schema import Package
from app.import_bridge.validator import generate_package
import hashlib


@pytest.fixture
def fake(tmp_path):
    runtimes = []
    def build(mode='normal', authenticated=False, timeout=.5, **config):
        exe = tmp_path / f'codex-{len(runtimes)}'
        record = tmp_path / f'record-{len(runtimes)}.jsonl'
        body = Path('tests/fixtures/app_server/fake_server.py').read_text()
        exe.write_text(f'#!{sys.executable}\nCONFIG = {dict(mode=mode, authenticated=authenticated, record=str(record), **config)!r}\n' + body)
        exe.chmod(0o700)
        options = {'turn_timeout': .4}
        if timeout is not None: options['timeout'] = timeout
        runtime = CodexRuntime(str(exe), **options)
        runtime.record = record
        runtimes.append(runtime)
        runtime.start()
        return runtime
    yield build
    for runtime in runtimes: runtime.close()


def eventually(check):
    deadline = time.monotonic() + 2
    while not check():
        if time.monotonic() > deadline: raise AssertionError('condition timed out')
        time.sleep(.01)


def test_missing_executable():
    runtime = CodexRuntime('/nonexistent/kg-codex')
    runtime.start()
    assert runtime.status().state == 'unavailable'
    assert runtime.status().error == 'executable_missing'
    runtime.close()


def test_start_initialize_auth_required(fake):
    runtime = fake()
    assert runtime.status().state == 'auth_required'
    messages = [json.loads(line) for line in runtime.record.read_text().splitlines()]
    assert messages[0]['method'] == 'initialize'
    assert messages[1]['method'] == 'config/read'
    assert messages[2]['method'] == 'account/read'
    assert messages[2]['params'] == {'refreshToken': False}
    assert runtime.capabilities().authentication
    with pytest.raises(AIError, match='auth_required'): runtime.create_session()


@pytest.mark.parametrize('mode,code', [('malformed','malformed_protocol'), ('bad_envelope','malformed_protocol'),
                                      ('oversize','malformed_protocol'), ('timeout','rpc_timeout'), ('bad_account','malformed_account')])
def test_start_failures(fake, mode, code):
    runtime = fake(mode)
    assert runtime.status().state == 'error'
    assert runtime.status().error == code
    assert runtime.transport.process is None


def test_spawn_failure(monkeypatch):
    monkeypatch.setattr('app.ai.process.shutil.which', lambda _: '/fake')
    def fail(*args, **kwargs): raise OSError('SUPER_SECRET')
    monkeypatch.setattr('app.ai.process.subprocess.Popen', fail)
    runtime = CodexRuntime(); runtime.start()
    assert runtime.status().error == 'process_start_failed'


def test_login_success_restart_auth_reuse_resume_logout(fake):
    runtime = fake('login_success', null_on_force_refresh=True)
    assert runtime.login() == {'authorization_url': 'https://auth.openai.com/oauth/authorize?state=fixture'}
    eventually(lambda: runtime.status().state == 'ready')
    assert runtime.status().auth.login_state == 'completed'
    session = runtime.create_session(instructions='trusted system')
    process = runtime.transport.process
    runtime.restart()
    assert process.poll() is not None
    assert runtime.status().state == 'ready'
    assert runtime.resume_session(session.id).id == session.id
    runtime.logout()
    assert runtime.status().state == 'auth_required'
    runtime.restart()
    assert runtime.status().state == 'auth_required'
    reads = [json.loads(line) for line in runtime.record.read_text().splitlines()
             if json.loads(line)['method'] == 'account/read']
    assert len(reads) >= 5
    assert all(request['params'] == {'refreshToken': False} for request in reads)


def test_account_refresh_means_reread_without_force_refresh(fake):
    runtime = fake(authenticated=True, null_on_force_refresh=True)
    assert runtime.status().state == 'ready'
    before = len(runtime.record.read_text().splitlines())
    assert runtime.status(refresh=True).state == 'ready'
    assert len(runtime.record.read_text().splitlines()) == before + 1
    # An account/updated notification is a reason to reread, not an auth result.
    runtime._notification('account/updated', {'authMode': None, 'planType': None})
    assert runtime.status().state == 'ready'
    assert len(runtime.record.read_text().splitlines()) == before + 2
    reads = [json.loads(line) for line in runtime.record.read_text().splitlines()
             if json.loads(line)['method'] == 'account/read']
    assert all(request['params'] == {'refreshToken': False} for request in reads)
    # Prove the fake models the Windows probe, without using this in normal flow.
    assert runtime.transport.request('account/read', {'refreshToken': True})['account'] is None
    assert runtime.status(refresh=True).state == 'ready'


@pytest.mark.parametrize('plan,label', [
    ('plus', 'plus'), ('prolite', 'prolite'), ('promax', 'promax'),
    ('self_serve_business_prolite', 'business'), ('self_serve_business_usage_based', 'business'),
    ('enterprise_cbp_automation', 'enterprise'), ('enterprise_cbp_usage_based', 'enterprise'),
    ('ent26', 'enterprise'), ('edu_plus', 'edu'), ('edu_pro', 'edu'),
    ('future-plan-SUPER_SECRET', 'unknown'), ('<script>SUPER_SECRET</script>', 'unknown'),
    (None, 'unknown'), (['SUPER_SECRET'], 'unknown'), ({'token': 'SUPER_SECRET'}, 'unknown'),
])
def test_plan_is_safe_metadata_and_never_authentication(fake, plan, label):
    runtime = fake(authenticated=True, plan=plan)
    status = runtime.status(refresh=True)
    assert status.state == 'ready'
    assert status.auth.state == 'authenticated'
    assert status.auth.plan == label
    assert 'SUPER_SECRET' not in json.dumps(status.public())
    events = list(runtime.send_turn(runtime.create_session(), 'Reply with exactly: runtime-ok'))
    assert ''.join(event.text for event in events) == 'runtime-ok'
    assert events[-1].kind == 'completed'


def test_default_timeout_configuration():
    from app.config import load_config
    config = load_config()['runtime']
    configured = create_runtime(config)
    default = create_runtime({'type': 'codex_chatgpt_plan'})
    assert config['rpc_timeout_seconds'] == configured.transport.timeout == default.transport.timeout == 30
    assert configured.turn_timeout == default.turn_timeout == 120


def test_windows_slow_initialize_succeeds_with_default_budget(fake):
    # Real elapsed time, not a mocked transport: near the former 10s ceiling.
    runtime = fake(authenticated=True, timeout=None, rpc_delays={'initialize': 9.5},
                   plan='prolite', null_on_force_refresh=True)
    assert runtime.transport.timeout == 30
    assert runtime.status(refresh=True).state == 'ready'
    events = list(runtime.send_turn(runtime.create_session(), 'Reply with exactly: runtime-ok'))
    assert ''.join(event.text for event in events) == 'runtime-ok'
    assert events[-1].kind == 'completed'


@pytest.mark.parametrize('method', ['initialize', 'config/read', 'account/read'])
def test_handshake_exceeding_default_budget_is_bounded_and_reaped(fake, method):
    started = time.monotonic()
    runtime = fake(authenticated=True, timeout=None, rpc_delays={method: 31})
    assert 30 <= time.monotonic() - started < 36
    assert runtime.status().state == 'error'
    assert runtime.status().error == 'rpc_timeout'
    assert runtime.transport.process is None
    assert runtime.transport.workdir is None


@pytest.mark.parametrize('host', ['chatgpt.com', 'auth.openai.com'])
def test_current_official_browser_login_hosts(fake, host):
    from jsonschema import Draft7Validator
    url = (f'https://{host}/oauth/authorize?response_type=code&client_id=fixture&state=fixture'
           '&code_challenge=fixture&code_challenge_method=S256&id_token_add_organizations=true'
           '&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback')
    response_schema = json.loads(Path('tests/fixtures/app_server/LoginAccountResponse.schema.json').read_text())
    Draft7Validator(response_schema).validate({'type': 'chatgpt', 'loginId': 'fixture', 'authUrl': url})
    assert fake(auth_url=url).login() == {'authorization_url': url}


def test_current_account_schema_plan_values_match_safe_normalization():
    from jsonschema import Draft7Validator
    from app.ai.codex import PLAN_LABELS
    schema = json.loads(Path('tests/fixtures/app_server/GetAccountResponse.schema.json').read_text())
    assert set(schema['definitions']['PlanType']['enum']) == set(PLAN_LABELS)
    for plan in PLAN_LABELS:
        Draft7Validator(schema).validate({'account': {'type': 'chatgpt', 'email': None, 'planType': plan},
                                         'requiresOpenaiAuth': True})


@pytest.mark.parametrize('url', [
    'http://chatgpt.com/oauth/authorize', 'javascript:alert(1)',
    'https://example.com/', 'https://chatgpt.com.evil.example/',
    'https://evil.chatgpt.com/', 'https://auth.openai.com.evil.example/',
    'https://evil.openai.com/', 'https://chatgpt.com@evil.example/',
    'https://user:password@chatgpt.com/', 'https://@chatgpt.com/',
    'https://chatgpt.com:444/', 'https://chatgpt.com:invalid/',
    'https://chatgpt.com/#access_token=SUPER_SECRET', 'https://chatgpt.com/#',
    'https://chatgpt.com/?access_token=SUPER_SECRET',
    'https://auth.openai.com/?refresh_token=SUPER_SECRET',
    'https://chatgpt.com/?id_token=SUPER_SECRET',
    'https://chatgpt.com/?access_token=', 'https://chatgpt.com/?access%5Ftoken=SUPER_SECRET',
    'https://chatgpt.com/?AccessToken=SUPER_SECRET', 'https://chatgpt.com/?TOKEN=SUPER_SECRET',
    'https://chatgpt.com/?authorization=SUPER_SECRET', 'https://chatgpt.com/?client_secret=SUPER_SECRET',
    'https://chatgpt.com/?api_key=SUPER_SECRET', 'https://chatgpt.com/?code=SUPER_SECRET',
    'https://chatgpt.com\\@evil.example/', 'https://chatgpt.com/\n',
    'https://chatgpt.com/\t', ' https://chatgpt.com/', None, ['https://chatgpt.com/'],
])
def test_malicious_browser_login_urls_are_rejected(fake, url, caplog, capsys):
    runtime = fake(auth_url=url)
    with pytest.raises(AIError, match='malformed_login'): runtime.login()
    assert runtime.status().auth.login_state == 'failed'
    assert 'SUPER_SECRET' not in json.dumps(runtime.status().public()) + caplog.text + str(capsys.readouterr())


@pytest.mark.parametrize('mode', ['login_failure','login_rejected','unsafe_url','token_url'])
def test_login_failure(fake, mode):
    runtime = fake(mode)
    if mode == 'login_failure':
        runtime.login()
        eventually(lambda: runtime.status().auth.login_state == 'failed')
    else:
        with pytest.raises(AIError): runtime.login()
    assert runtime.status().auth.login_state == 'failed'
    assert 'SUPER_SECRET' not in json.dumps(runtime.status().public())


def test_login_cancel(fake):
    runtime = fake()
    runtime.login()
    assert runtime.status().auth.login_state == 'pending'
    runtime.cancel_login()
    assert runtime.status().auth.login_state == 'cancelled'
    assert runtime.status().state == 'auth_required'


def test_api_auth_is_not_plan_auth(fake):
    assert fake('api_auth', True).status().state == 'auth_required'


def test_models_not_entitlements(fake):
    model = fake().models()[0]
    assert model.id == 'catalog-model'
    assert model.entitlement == 'unverified'


def test_one_turn_and_streaming_trusted_boundary(fake):
    runtime = fake(authenticated=True)
    session = runtime.create_session(instructions='trusted instructions')
    events = list(runtime.send_turn(session, 'untrusted paper text'))
    assert [event.kind for event in events] == ['started','delta','delta','completed']
    assert ''.join(e.text for e in events) == 'runtime-ok'
    messages = [json.loads(line) for line in runtime.record.read_text().splitlines()]
    thread = next(m for m in messages if m['method'] == 'thread/start')['params']
    turn = next(m for m in messages if m['method'] == 'turn/start')['params']
    assert thread['baseInstructions'] == 'trusted instructions'
    assert 'untrusted paper text' not in json.dumps(thread)
    assert turn['input'][0]['text'] == 'untrusted paper text'
    assert turn['sandboxPolicy'] == {'type':'readOnly','networkAccess':False}
    assert thread['approvalPolicy'] == 'never'
    assert thread['config']['mcp_servers'] == {
        'tradingview_mcp': {'enabled': False}, 'node_repl': {'enabled': False},
    }
    assert 'SUPER_SECRET' not in json.dumps(messages)
    assert thread['config']['features.plugins'] is False
    assert 'untrusted source data' in thread['developerInstructions']
    assert 'model' not in thread


@pytest.mark.parametrize('names', [[], ['tradingview_mcp', 'node_repl'],
                                    ['normal_server', 'tradingview_mcp', 'node_repl', 'name.with.dot',
                                     'name-with-dash', 'name"with"quote', "name'with'quote"]])
def test_nested_mcp_isolation_full_thread_and_runtime_ok(fake, names):
    from app.ai.process import DISABLED_FEATURES
    inherited = {name: {'enabled': True, 'url': 'https://fixture.invalid/SUPER_SECRET',
                        'env': {'Authorization': 'SUPER_SECRET'}, 'command': 'DO_NOT_RUN'} for name in names}
    runtime = fake(authenticated=True, plan='prolite', mcp_servers=inherited)
    assert runtime.status().state == 'ready'
    assert runtime.status().auth.state == 'authenticated'
    assert runtime.models()[0].entitlement == 'unverified'
    expected = {name: {'enabled': False} for name in names}
    assert runtime._tool_overrides == {'mcp_servers': expected}
    session = runtime.create_session(instructions='trusted instructions')
    session = runtime.resume_session(session.id)
    events = list(runtime.send_turn(session, 'Reply with exactly: runtime-ok'))
    assert ''.join(event.text for event in events) == 'runtime-ok'
    assert events[-1].kind == 'completed'
    requests = [json.loads(line) for line in runtime.record.read_text().splitlines()]
    assert [r['method'] for r in requests] == ['initialize', 'config/read', 'account/read', 'model/list',
                                            'thread/start', 'thread/resume', 'turn/start']
    for request in requests:
        if request['method'] not in ('thread/start', 'thread/resume'): continue
        params = request['params']
        config = params['config']
        assert config == {'web_search': 'disabled', 'mcp_servers': expected,
                          **{f'features.{name}': False for name in DISABLED_FEATURES}}
        assert not any(key.startswith('mcp_servers.') for key in config)
        assert params['sandbox'] == 'read-only' and params['approvalPolicy'] == 'never'
        assert params['modelProvider'] == 'openai' and 'model' not in params
        assert params['cwd'] == runtime.transport.workdir.name
        assert 'untrusted source data' in params['developerInstructions']
        if request['method'] == 'thread/start': assert params['baseInstructions'] == 'trusted instructions'
    assert 'SUPER_SECRET' not in json.dumps(requests) + json.dumps(runtime._tool_overrides)
    assert 'DO_NOT_RUN' not in json.dumps(requests)
    assert not any(r['method'].startswith('config/') and r['method'] != 'config/read' for r in requests)


def test_fake_rejects_legacy_dotted_mcp_contract(fake):
    from jsonschema import Draft7Validator
    runtime = fake(authenticated=True)
    # The generic schema accepts this JSON; the current config contract rejects it.
    params = {**runtime._session_params(), 'config': {
        'mcp_servers."tradingview_mcp".enabled': False,
        'mcp_servers."node_repl".enabled': False,
    }}
    schema = json.loads(Path('tests/fixtures/app_server/client_requests.schema.json').read_text())
    Draft7Validator(schema).validate({'id': 1, 'method': 'thread/start', 'params': params})
    with pytest.raises(AIError, match='rpc_rejected'):
        runtime.transport.request('thread/start', params)
    with pytest.raises(AIError, match='rpc_rejected'):
        runtime.transport.request('turn/start', {'threadId': 'thread-1', 'input': []})
    session = runtime.create_session()
    assert ''.join(event.text for event in runtime.send_turn(session, 'smoke')) == 'runtime-ok'


@pytest.mark.parametrize('effective_config', [None, [], 'SUPER_SECRET', {'mcp_servers': None},
                                            {'mcp_servers': []}, {'mcp_servers': 'SUPER_SECRET'}])
def test_malformed_mcp_discovery_fails_closed(fake, effective_config, caplog, capsys):
    runtime = fake(authenticated=True, effective_config=effective_config)
    assert runtime.status().state == 'error' and runtime.status().error == 'malformed_config'
    assert runtime.transport.process is None
    with pytest.raises(AIError): runtime.create_session()
    requests = [json.loads(line) for line in runtime.record.read_text().splitlines()]
    assert [r['method'] for r in requests] == ['initialize', 'config/read']
    assert 'SUPER_SECRET' not in json.dumps(runtime.status().public()) + caplog.text + str(capsys.readouterr())


def test_mcp_discovery_rejection_fails_closed(fake):
    runtime = fake('config_rejected', authenticated=True)
    assert runtime.status().state == 'error' and runtime.status().error == 'rpc_rejected'
    assert runtime.transport.process is None
    with pytest.raises(AIError): runtime.create_session()
    assert 'thread/start' not in runtime.record.read_text()


def test_mcp_rejection_api_reports_error_without_starting_turn(fake, monkeypatch, tmp_path):
    monkeypatch.setenv('KG_DATA_DIR', str(tmp_path / 'data'))
    runtime = fake('mcp_rejected', authenticated=True, plan='prolite')
    with TestClient(create_app(runtime)) as client:
        assert client.get('/api/ai/status').json()['state'] == 'ready'
        response = client.post('/api/ai/smoke')
        assert response.status_code == 503 and 'rpc_rejected' in response.text
        assert 'SUPER_SECRET' not in response.text
        status = client.get('/api/ai/status?refresh=true').json()
        assert status['state'] == 'error' and status['error'] == 'rpc_rejected'
        assert runtime.transport.process is None
        assert 'turn/start' not in runtime.record.read_text()


@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('overrides', [None, {'mcp_servers': []}, {'mcp_servers': {}},
                                      {'mcp_servers': {'tradingview_mcp': {'enabled': True},
                                                       'node_repl': {'enabled': False}}}])
def test_mcp_isolation_rejection_reaps_process_without_unsafe_retry(fake, resume, overrides, caplog, capsys):
    runtime = fake('mcp_rejected' if overrides is None else 'normal', authenticated=True)
    if overrides is not None: runtime._tool_overrides = overrides
    process = runtime.transport.process
    with pytest.raises(AIError, match='rpc_rejected'):
        if resume: runtime.resume_session('existing-thread')
        else: runtime.create_session()
    assert process.poll() is not None and runtime.transport.process is None
    assert runtime.status().state == 'error' and runtime.status().error == 'rpc_rejected'
    with pytest.raises(LLMError): RuntimeProvider(runtime).complete('trusted', 'untrusted')
    requests = [json.loads(line) for line in runtime.record.read_text().splitlines()]
    assert [r['method'] for r in requests] == ['initialize', 'config/read', 'account/read',
                                            'thread/resume' if resume else 'thread/start']
    assert 'SUPER_SECRET' not in str(capsys.readouterr()) + caplog.text + json.dumps(runtime.status().public())


def test_compatibility_adapter(fake):
    result = RuntimeProvider(fake(authenticated=True)).complete('trusted', 'untrusted', hint='answer')
    assert result.text == 'runtime-ok'
    assert result.provider == 'codex_chatgpt_plan'
    assert result.model == 'server-default'


def test_cancellation(fake):
    runtime = fake('cancel', True)
    session = runtime.create_session()
    stream = runtime.send_turn(session, 'smoke')
    started = next(stream)
    runtime.cancel(session.id, started.turn_id)
    assert list(stream)[-1].kind == 'cancelled'


def test_abandoned_stream_interrupts(fake):
    runtime = fake('cancel', True)
    stream = runtime.send_turn(runtime.create_session(), 'smoke')
    next(stream)
    stream.close()
    assert 'turn/interrupt' in runtime.record.read_text()


@pytest.mark.parametrize('mode,code',[('turn_timeout','turn_timeout'),('turn_crash','process_exited'),
                                    ('turn_failed','turn_failed'),('bad_delta','malformed_turn_event')])
def test_turn_failure(fake, mode, code):
    runtime = fake(mode, True)
    with pytest.raises(AIError) as error: list(runtime.send_turn(runtime.create_session(), 'smoke'))
    assert error.value.code == code
    assert runtime.status().state == 'error'


def test_crash_detection_and_restart(fake):
    runtime = fake('crash', True)
    with pytest.raises(AIError, match='process_exited'): runtime.models()
    eventually(lambda: runtime.status().state == 'error')
    runtime.restart()
    assert runtime.status().state == 'ready'


def test_raw_errors_and_stderr_redacted(fake, caplog, capsys):
    runtime = fake('stderr', True)
    public = json.dumps(runtime.status(refresh=True).public())
    assert 'SUPER_SECRET' not in public and 'email' not in public
    rejected = fake('rpc_error')
    with pytest.raises(AIError) as error: rejected.models()
    assert 'SUPER_SECRET' not in str(error.value)
    time.sleep(.05)
    captured = capsys.readouterr()
    assert 'SUPER_SECRET' not in captured.out + captured.err + caplog.text


def test_child_env_no_api_keys(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','SUPER_SECRET')
    monkeypatch.setenv('ANTHROPIC_API_KEY','SUPER_SECRET')
    monkeypatch.setenv('OTHER_SECRET','SUPER_SECRET')
    assert 'SUPER_SECRET' not in json.dumps(child_environment())


def test_launch_security_options(fake):
    runtime = fake()
    args = runtime.transport.process.args
    assert 'cli_auth_credentials_store="keyring"' in args
    assert 'forced_login_method="chatgpt"' in args
    assert 'features.shell_tool=false' in args
    assert not Path(runtime.transport.workdir.name).samefile(Path.cwd())
    process = runtime.transport.process
    runtime.close()
    assert process.poll() is not None


def test_factory_defaults_no_metered(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('KG_LLM_PROVIDER', raising=False)
    monkeypatch.delenv('KG_AI_RUNTIME', raising=False)
    assert isinstance(create_runtime(), CodexRuntime)
    assert isinstance(create_runtime({'type':'api_provider'}), NoAIRuntime)
    assert isinstance(create_runtime({'type':'mock'}), MockRuntime)
    with pytest.raises(LLMError, match='AI未接続'): RuntimeProvider(NoAIRuntime()).complete('trusted','user')


def test_mock_smoke():
    runtime = MockRuntime()
    assert ''.join(e.text for e in runtime.send_turn(runtime.create_session(), 'Reply with exactly: runtime-ok')) == 'runtime-ok'


@pytest.mark.parametrize('mode', ['missing','auth_required','crash','timeout','malformed','malformed_mcp',
                                 'mcp_rejected','turn_failed','turn_timeout','no_ai'])
def test_no_ai_data_remains_usable(tmp_path, monkeypatch, fake, mode):
    monkeypatch.setenv('KG_DATA_DIR',str(tmp_path/'data'))
    monkeypatch.setenv('KG_AI_RUNTIME','codex_chatgpt_plan')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    if mode == 'missing': ai = CodexRuntime('/nonexistent/kg-codex')
    elif mode == 'no_ai': ai = NoAIRuntime()
    else: ai = fake(mode if mode != 'auth_required' else 'normal', mode != 'auth_required')
    if mode == 'crash':
        with pytest.raises(AIError): ai.models()
    elif mode == 'mcp_rejected':
        with pytest.raises(AIError, match='rpc_rejected'): ai.create_session()
    elif mode in ('turn_failed','turn_timeout'):
        with pytest.raises(AIError): list(ai.send_turn(ai.create_session(), 'smoke'))
    with TestClient(create_app(ai)) as client:
        pdf = make_pdf('visual_evidence')
        sid = client.post('/api/sources/pdf',files={'file':('synthetic.pdf',pdf,'application/pdf')}).json()['source']['id']
        assert client.get(f'/api/sources/{sid}/file').content == pdf
        assert client.get(f'/api/sources/{sid}/document').status_code == 200
        package = generate_package(Package.model_validate(package_data(hashlib.sha256(pdf).hexdigest())))
        preview = client.post('/api/import/kgpack/preview',files={'file':('fixture.kgpack',package)}).json()
        assert client.post('/api/import/kgpack/commit',json={'preview_id':preview['preview_id'],'confirmed':True}).status_code == 200
        assert client.get(f'/api/sources/{sid}/paper-brief').status_code == 200
        assert client.post('/api/knowledge',json={'source_id':sid,'section_key':'method','content':'user note','origin':'user','info_type':'user_thought'}).status_code == 200
        assert client.post('/api/highlights',json={'source_id':sid,'anchor':{'page':1}}).status_code == 200
        assert client.get(f'/api/sources/{sid}/note').status_code == 200
        assert client.get(f'/api/sources/{sid}/export.json').status_code == 200
        assert client.get(f'/api/sources/{sid}/export.md').status_code == 200
        error = client.post('/api/translate',json={'source_id':sid,'text':'untranslated'})
        assert error.status_code == 502 and 'AI未接続' in error.text
        assert client.post(f'/api/sources/{sid}/questions',json={'question_text':'question'}).status_code == 502
        assert client.get('/api/ai/status').status_code == 200
    assert ai.status().state == 'disconnected'


def test_runtime_api_stream_and_auth(fake, monkeypatch, tmp_path):
    monkeypatch.setenv('KG_DATA_DIR',str(tmp_path/'data'))
    ai = fake('login_success', plan='prolite', null_on_force_refresh=True)
    with TestClient(create_app(ai)) as client:
        assert client.post('/api/ai/login').status_code == 200
        eventually(lambda: client.get('/api/ai/status').json()['state'] == 'ready')
        response = client.post('/api/ai/smoke')
        assert response.status_code == 200
        assert 'runtime-' in response.text and '"kind": "completed"' in response.text
        assert client.get('/api/ai/models').json()['models'][0]['entitlement'] == 'unverified'
        status = client.get('/api/ai/status?refresh=true')
        assert 'SUPER_SECRET' not in status.text
        assert status.json()['state'] == 'ready'
        assert status.json()['auth']['plan'] == 'prolite'
        reads = [json.loads(line) for line in ai.record.read_text().splitlines()
                 if json.loads(line)['method'] == 'account/read']
        assert all(request['params'] == {'refreshToken': False} for request in reads)
        assert client.post('/api/ai/restart').json()['state'] == 'ready'
        assert client.post('/api/ai/logout').json()['state'] == 'auth_required'


def test_recorded_schema_validates_all_adapter_requests(fake):
    from jsonschema import Draft7Validator
    schema = json.loads(Path('tests/fixtures/app_server/client_requests.schema.json').read_text())
    validator = Draft7Validator(schema)
    runtime = fake('login_success')
    runtime.models()
    runtime.login()
    eventually(lambda: runtime.status().state == 'ready')
    session = runtime.create_session()
    list(runtime.send_turn(session, 'smoke'))
    runtime.cancel(session.id, 'turn-1')
    runtime.resume_session(session.id)
    runtime.cancel_login()
    runtime.logout()
    requests = [json.loads(line) for line in runtime.record.read_text().splitlines()]
    expected = {'initialize','account/read','account/login/start','account/login/cancel','account/logout',
                'thread/start','thread/resume','turn/start','turn/interrupt','model/list','config/read'}
    assert {r['method'] for r in requests} == expected
    for request in requests: validator.validate(request)


def test_windows_npm_shim_native_discovery(tmp_path, monkeypatch):
    from app.ai.process import discover_executable
    shim = tmp_path/'codex.cmd'
    monkeypatch.setattr('app.ai.process.shutil.which', lambda _: str(shim))
    with pytest.raises(AIError, match='native_executable_required'):
        discover_executable('codex', platform='nt')
    native = tmp_path/'node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/codex/codex.exe'
    native.parent.mkdir(parents=True)
    native.touch()
    assert discover_executable('codex', platform='nt') == str(native)


def test_control_rejects_cross_site(tmp_path, monkeypatch):
    monkeypatch.setenv('KG_DATA_DIR',str(tmp_path/'data'))
    with TestClient(create_app(MockRuntime())) as client:
        assert client.post('/api/ai/smoke',headers={'Origin':'https://untrusted.example'}).status_code == 403
        assert client.post('/api/ai/restart',headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
        assert client.post('/api/ai/smoke',headers={'Origin':'http://testserver'}).status_code == 200


def test_same_session_concurrency_rejected(fake):
    ai = fake('cancel',True)
    session = ai.create_session()
    stream = ai.send_turn(session,'first')
    first = next(stream)
    with pytest.raises(AIError,match='session_busy'): next(ai.send_turn(session,'second'))
    ai.cancel(session.id,first.turn_id)
    assert list(stream)[-1].kind == 'cancelled'


def test_shutdown_terminates_uncooperative_process(fake):
    ai = fake('ignore_eof')
    process = ai.transport.process
    ai.close()
    assert process.poll() is not None


@pytest.mark.skipif(os.name == 'nt',reason='POSIX process group assertion; Windows uses a Job object')
def test_shutdown_cleans_up_descendant(fake):
    ai = fake('descendant')
    child_pid = int(Path(ai.transport.executable).with_suffix('.child').read_text())
    ai.close()
    def stopped():
        stat = Path(f'/proc/{child_pid}/stat')
        return not stat.exists() or stat.read_text().split()[2] == 'Z'
    eventually(stopped)


def test_late_login_notification_does_not_override_cancel(fake):
    ai = fake()
    ai.login()
    ai.cancel_login()
    ai._notification('account/login/completed',{'loginId':'login-1','success':True})
    assert ai.status().auth.login_state == 'cancelled'


def test_write_to_stalled_process_times_out(fake):
    ai = fake('stall_writes')
    started = time.monotonic()
    with pytest.raises(AIError,match='rpc_timeout'):
        ai.transport.request('thread/start', {'data':'x'*1024*1024})
    assert time.monotonic() - started < 2
    assert ai.status().state == 'error'


def test_legacy_mock_uses_runtime_without_api_key_guidance(monkeypatch):
    from app.llm import get_provider
    monkeypatch.setenv('KG_AI_RUNTIME','mock')
    provider = get_provider(MockRuntime())
    assert isinstance(provider, RuntimeProvider)
    answer = provider.complete('trusted','question',hint='answer')
    assert 'モック回答' in answer.text
    assert 'ChatGPTで接続' in answer.text
    assert 'APIキーを設定' not in answer.text
    assert provider.complete_json('trusted','source').text == '{}'
    with pytest.raises(LLMError): get_provider(NoAIRuntime()).complete('trusted','source')


@pytest.mark.parametrize('mode', ['oversize', 'incomplete'])
def test_runtime_provider_bounds_output_and_requires_terminal_completion(mode):
    from app.ai.base import TurnEvent
    from app.ai.offline import MockRuntime
    class InvalidTurn(MockRuntime):
        def send_turn(self, session, text, *, hint=None):
            yield TurnEvent('delta',session.id,'turn','x'*(1024*1024+1) if mode=='oversize' else '{}')
    with pytest.raises(LLMError,match='output_too_large' if mode=='oversize' else 'incomplete_turn'):
        RuntimeProvider(InvalidTurn()).complete_json('trusted','untrusted')
