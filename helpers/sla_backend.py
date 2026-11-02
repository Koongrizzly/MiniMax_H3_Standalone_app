from __future__ import annotations

import json
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

_MIN_VERSION = (0, 2, 32)
_REQ = "comfy-kitchen>=0.2.32"


def _version_tuple(text: str):
    vals=[]
    for p in str(text).split('.'):
        digits=''.join(ch for ch in p if ch.isdigit())
        if not digits:
            break
        vals.append(int(digits))
    return tuple((vals + [0,0,0])[:3])


def _probe():
    code = (
        "import json, comfy_kitchen as ck; "
        "print(json.dumps({'sol_attn': bool(hasattr(ck,'sol_attn')), "
        "'file': str(getattr(ck,'__file__',''))}))"
    )
    p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    if p.returncode:
        return False, {'error': (p.stderr or p.stdout).strip()}
    try:
        data=json.loads((p.stdout or '').strip().splitlines()[-1])
    except Exception:
        data={'raw': (p.stdout or '').strip()}
    return bool(data.get('sol_attn')), data


def _installed_version():
    for name in ('comfy-kitchen','comfy_kitchen'):
        try:
            return version(name)
        except PackageNotFoundError:
            pass
        except Exception:
            pass
    return 'unknown'


def _install_upgrade():
    uv = shutil.which('uv')
    if uv:
        cmd=[uv, 'pip', 'install', '--python', sys.executable, '--upgrade', _REQ]
        method='uv'
    else:
        cmd=[sys.executable, '-m', 'pip', 'install', '--upgrade', _REQ]
        method='pip'
    print(f"[SLA] comfy_kitchen sol_attn missing; upgrading {_REQ} with {method}...", flush=True)
    return subprocess.run(cmd, text=True).returncode


def ensure_comfy_kitchen_sla_backend(auto_upgrade: bool=True) -> bool:
    """Guarantee the exact SLA Comfy Kitchen backend exists before sampling.

    Uses a child-process probe so a native comfy_kitchen module is never loaded
    into the launcher before an upgrade. This avoids Windows DLL replacement
    problems. If an upgrade is needed, the current MiniMax environment itself
    is updated, then probed again in a fresh process.
    """
    ver=_installed_version()
    ok, data=_probe()
    if ok and (ver == 'unknown' or _version_tuple(ver) >= _MIN_VERSION):
        print(f"[SLA] comfy_kitchen sol_attn available | version={ver} | backend={data.get('file','unknown')}", flush=True)
        return True
    if not auto_upgrade:
        print(f"[SLA] ERROR: comfy_kitchen backend is not SLA-capable | version={ver} | sol_attn={ok}", flush=True)
        return False
    if _install_upgrade() != 0:
        print("[SLA] ERROR: comfy_kitchen upgrade failed; generation stopped before sampling so it cannot silently fall back to dense.", flush=True)
        return False
    ver=_installed_version()
    ok, data=_probe()
    if not ok:
        print(f"[SLA] ERROR: upgraded comfy_kitchen still has no sol_attn | version={ver} | detail={data}", flush=True)
        return False
    print(f"[SLA] comfy_kitchen sol_attn available after upgrade | version={ver} | backend={data.get('file','unknown')}", flush=True)
    return True
