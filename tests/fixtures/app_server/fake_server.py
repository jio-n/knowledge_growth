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
    if method == 'initialize':
        if mode == 'malformed': print('not-json', flush=True); continue
        if mode == 'timeout': time.sleep(5); continue
        if mode == 'bad_envelope': emit({'id': request['id'], 'result': {}, 'error': {}}); continue
        if mode == 'oversize': print('x' * (2*1024*1024+1), flush=True); continue
        if mode == 'stderr':
            sys.stderr.write('Authorization: Bearer SUPER_SECRET\n{"accessToken":"SUPER_SECRET"}\n'); sys.stderr.flush()
        result = {'userAgent': 'fake/0.159.0-alpha.3'}
    elif method == 'config/read':
        result = {'config':{'mcp_servers':{'inherited':{'env':{'accessToken':'SUPER_SECRET'}}}}}
    elif method == 'account/read':
        result = {'account': {'type': 'chatgpt', 'planType': 'plus', 'email': 'not-public', 'accessToken': 'SUPER_SECRET'} if signed_in else None,
                  'requiresOpenaiAuth': True}
        if mode == 'api_auth': result['account'] = {'type': 'apiKey'}
        if mode == 'bad_account': result = {'account': 'bad'}
    elif method == 'account/login/start':
        login_count += 1
        if mode == 'login_rejected':
            emit({'id': request['id'], 'error': {'code': 400, 'message': 'SUPER_SECRET'}}); continue
        result = {'type': 'chatgpt', 'loginId': 'login-1', 'authUrl': 'https://auth.openai.com/oauth/authorize?state=fixture', 'accessToken': 'SUPER_SECRET'}
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
        result = {'thread': {'id': 'thread-1'}, 'model': 'server-default'}
    elif method == 'turn/start': result = {'turn': {'id': 'turn-1', 'status': 'inProgress'}}
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
