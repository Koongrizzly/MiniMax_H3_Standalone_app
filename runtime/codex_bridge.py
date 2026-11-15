"""Local GrizzlyMax automation mailbox. No Qt, model imports or queue writes."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAILBOX = ROOT / 'runtime' / 'codex_mailbox'


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def receipt(request_id):
    return MAILBOX / 'responses' / (uuid.UUID(request_id).hex + '.json')


def main(argv=None):
    p = argparse.ArgumentParser(description='Submit jobs to the running GrizzlyMax application.')
    p.add_argument('--timeout', type=float, default=300, help='Seconds to wait for a receipt, not generation.')
    sub = p.add_subparsers(dest='action', required=True)
    for action in ('enqueue-batch', 'validate-batch'):
        s = sub.add_parser(action)
        s.add_argument('file', help='JSON or TXT batch; relative media paths are relative to this file.')
    sub.add_parser('status')
    sub.add_parser('settings')
    s = sub.add_parser('result'); s.add_argument('request_id')
    s = sub.add_parser('upscale'); s.add_argument('source'); s.add_argument('--scale', type=int, required=True); s.add_argument('--engine', choices=['nvidia_vsr','relayvsr'], default='nvidia_vsr')
    s = sub.add_parser('schema')
    args = p.parse_args(argv)
    try:
        if args.action == 'schema':
            from runtime.minimax_batch_import import _SUPPORTED, _ALIASES
            print(json.dumps({'format': {'defaults': {}, 'jobs': [{'mode':'ref2va','prompt':'...', 'ref_images':['absolute/path.png']}]}, 'supported_fields': sorted(_SUPPORTED), 'aliases': _ALIASES, 'modes':['t2va','fl2va','ref2va']}, indent=2)); return 0
        if args.action == 'result':
            path = receipt(args.request_id)
            if not path.is_file():
                print(json.dumps({'ok':False,'request_id':args.request_id,'error':'No receipt yet. Do not resubmit until the original outcome is known.'})); return 2
            result = read_json(path); print(json.dumps(result, ensure_ascii=False)); return 0 if result.get('ok') else 1
        # No request is written if the running GUI has not advertised a session.
        session_path = MAILBOX / 'session.json'
        if not session_path.is_file():
            raise RuntimeError('Open GrizzlyMax first; no running automation session found.')
        session = read_json(session_path)
        if time.time() - float(session['heartbeat']) > 120:
            raise RuntimeError('GrizzlyMax bridge is not responding. Open GrizzlyMax and try again.')
        request_id = uuid.uuid4().hex
        request = {'id':request_id,'session':session['id'],'action':args.action}
        if args.action in ('enqueue-batch','validate-batch'):
            from runtime.minimax_batch_import import parse_file
            path = Path(args.file).resolve()
            request.update(specs=parse_file(path), source_dir=str(path.parent))
        elif args.action == 'upscale':
            request.update(source=str(Path(args.source).resolve()), scale=args.scale, engine=args.engine)
        atomic_json(MAILBOX / 'requests' / (request_id + '.json'), request)
        print('Request ID: ' + request_id, file=sys.stderr, flush=True)
        deadline = time.monotonic() + max(0, args.timeout)
        while time.monotonic() < deadline:
            path = receipt(request_id)
            if path.is_file():
                result = read_json(path); print(json.dumps(result, ensure_ascii=False)); return 0 if result.get('ok') else 1
            time.sleep(.2)
        print(json.dumps({'ok':False,'request_id':request_id,'error':'Receipt timeout; request may still be processing. Use result with this ID; do not resubmit.'})); return 2
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)}, ensure_ascii=False)); return 1


if __name__ == '__main__':
    raise SystemExit(main())
