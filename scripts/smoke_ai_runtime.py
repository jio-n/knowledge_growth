"""Offline AIRuntime smoke by default; --manual opts into a signed-in ChatGPT turn."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ai import create_runtime
from app.ai.base import AIError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manual', action='store_true', help='Use configured runtime and existing Codex keyring authentication')
    args = parser.parse_args()
    ai = create_runtime() if args.manual else create_runtime({'type':'mock'})
    try:
        ai.start()
        status = ai.status(refresh=True)
        print(f'{status.runtime}: {status.state}')
        if status.state != 'ready':
            print('アプリのAI状態メニューからChatGPTで接続してください。API keyは不要です。')
            return 2
        session = ai.create_session()
        events = list(ai.send_turn(session, 'Reply with exactly: runtime-ok'))
        passed = ''.join(event.text for event in events).strip() == 'runtime-ok' and events[-1].kind == 'completed'
        print('runtime-ok: PASS' if passed else 'runtime-ok: FAIL')
        return 0 if passed else 1
    except AIError as error:
        print(str(error))
        return 1
    finally:
        ai.close()


if __name__ == '__main__':
    raise SystemExit(main())
