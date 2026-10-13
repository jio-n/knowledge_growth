# Script body prefixed with test-only CONFIG by fixture factory. Never reads credentials.
import json
import pathlib
import subprocess
import sys
import time

marker = pathlib.Path(__file__).with_suffix('.signed_in')
signed_in = marker.exists() or CONFIG.get('authenticated', False)
if CONFIG.get('mode') == 'descendant':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    pathlib.Path(__file__).with_suffix('.child').write_text(str(child.pid))
mode = CONFIG.get('mode', 'normal')
active = None
login_count = 0
turn_count = 0
thread_started = False
mcp_servers = CONFIG.get('mcp_servers', {
    'tradingview_mcp': {'enabled': True, 'url': 'https://fixture.invalid/SUPER_SECRET'},
    'node_repl': {'enabled': False, 'env': {'accessToken': 'SUPER_SECRET'}},
})

def emit(message):
    print(json.dumps(message), flush=True)

def notify(method, params): emit({'method': method, 'params': params})
def completed(state='completed'):
    notify('turn/completed', {'threadId': 'thread-1', 'turn': {'id': 'turn-1', 'status': state, 'items': [], 'error': None}})

for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    params = request.get('params', {})
    if 'id' not in request: continue
    if 'method' not in request: continue   # response to unsupported server request
    if CONFIG.get('record'):
        with open(CONFIG['record'], 'a') as file: file.write(json.dumps(request) + '\n')
    result = {}
    time.sleep(CONFIG.get('rpc_delays', {}).get(method, 0))
    if method == 'initialize':
        if mode == 'malformed': print('not-json', flush=True); continue
        if mode == 'timeout': time.sleep(5); continue
        if mode == 'bad_envelope': emit({'id': request['id'], 'result': {}, 'error': {}}); continue
        if mode == 'oversize': print('x' * (2*1024*1024+1), flush=True); continue
        if mode == 'stderr':
            sys.stderr.write('Authorization: Bearer SUPER_SECRET\n{"accessToken":"SUPER_SECRET"}\n'); sys.stderr.flush()
        result = {'userAgent': 'fake/0.160.1'}
    elif method == 'config/read':
        if mode == 'config_rejected':
            emit({'id': request['id'], 'error': {'code': -32602, 'message': 'SUPER_SECRET'}}); continue
        result = {'config': CONFIG.get('effective_config', {'mcp_servers': mcp_servers})}
        if mode == 'malformed_mcp': result = {'config': {'mcp_servers': ['SUPER_SECRET']}}
    elif method == 'account/read':
        result = {'account': {'type': 'chatgpt', 'planType': CONFIG.get('plan', 'plus'), 'email': 'not-public', 'accessToken': 'SUPER_SECRET'} if signed_in else None,
                  'requiresOpenaiAuth': True}
        if CONFIG.get('null_on_force_refresh') and params.get('refreshToken') is True:
            result['account'] = None
        if mode == 'api_auth': result['account'] = {'type': 'apiKey'}
        if mode == 'bad_account': result = {'account': 'bad'}
    elif method == 'account/login/start':
        login_count += 1
        if mode == 'login_rejected':
            emit({'id': request['id'], 'error': {'code': 400, 'message': 'SUPER_SECRET'}}); continue
        result = {'type': 'chatgpt', 'loginId': 'login-1', 'authUrl': 'https://auth.openai.com/oauth/authorize?state=fixture', 'accessToken': 'SUPER_SECRET'}
        if 'auth_url' in CONFIG: result['authUrl'] = CONFIG['auth_url']
        if mode == 'unsafe_url': result['authUrl'] = 'javascript:alert(1)'
        if mode == 'token_url': result['authUrl'] = 'https://auth.openai.com/authorize?access_token=SUPER_SECRET'
    elif method == 'account/login/cancel':
        notify('account/login/completed', {'loginId': 'login-1', 'success': False, 'error': 'SUPER_SECRET'})
    elif method == 'account/logout':
        signed_in = False
        marker.unlink(missing_ok=True)
        notify('account/updated', {'authMode': None, 'planType': None})
    elif method == 'model/list':
        if mode == 'crash': sys.exit(9)
        if mode == 'rpc_error':
            emit({'id': request['id'], 'error': {'code': 500, 'message': 'Authorization: Bearer SUPER_SECRET'}}); continue
        result = {'data': [{'id': 'catalog-id', 'model': 'catalog-model', 'displayName': 'Catalog model'}], 'nextCursor': None}
    elif method in ('thread/start', 'thread/resume'):
        if mode == 'session_timeout': time.sleep(5); continue
        # Model just the isolation contract, not Codex's config parser. The old
        # dotted MCP leaf keys were accepted by this fake but rejected on Windows.
        config = params.get('config', {})
        expected = {name: {'enabled': False} for name in mcp_servers}
        if (not isinstance(config, dict) or any(key.startswith('mcp_servers.') for key in config)
                or config.get('mcp_servers') != expected or mode == 'mcp_rejected'):
            emit({'id': request['id'], 'error': {'code': -32602, 'message': 'MCP override rejected SUPER_SECRET'}}); continue
        thread_started = True
        result = {'thread': {'id': 'thread-1'}, 'model': 'server-default'}
    elif method == 'turn/start':
        if mode == 'turn_start_timeout': time.sleep(5); continue
        if not thread_started:
            emit({'id': request['id'], 'error': {'code': -32602, 'message': 'No isolated thread'}}); continue
        result = {'turn': {'id': 'turn-1', 'status': 'inProgress'}}
    elif method == 'turn/interrupt': pass
    else:
        emit({'id': request['id'], 'error': {'code': -32601, 'message': 'Unknown method'}}); continue
    emit({'id': request['id'], 'result': result})
    if mode == 'stall_writes' and method == 'account/read': time.sleep(60)
    if method == 'account/login/start':
        if mode == 'login_success' or (mode == 'browser' and login_count > 1):
            signed_in = True; marker.touch()
            notify('account/login/completed', {'loginId': 'login-1', 'success': True, 'error': None})
            notify('account/updated', {'authMode': 'chatgpt', 'planType': 'plus'})
        elif mode == 'login_failure':
            notify('account/login/completed', {'loginId': 'login-1', 'success': False, 'error': 'SUPER_SECRET'})
    elif method == 'turn/start':
        turn_count += 1
        if mode == 'turn_crash': sys.exit(9)
        if mode == 'bad_delta':
            notify('item/agentMessage/delta', {'threadId': 'thread-1', 'turnId': 'turn-1', 'delta': 1})
        elif mode != 'turn_timeout':
            notify('item/agentMessage/delta', {'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1', 'delta': 'runtime-'})
            notify('item/agentMessage/delta', {'threadId': 'thread-1', 'turnId': 'turn-1', 'itemId': 'item-1', 'delta': 'ok'})
        if mode not in ('cancel', 'turn_timeout') and not (mode == 'browser' and turn_count > 1):
            completed('failed' if mode == 'turn_failed' else 'completed')
    elif method == 'turn/interrupt': completed('interrupted')

if mode == 'ignore_eof': time.sleep(60)
