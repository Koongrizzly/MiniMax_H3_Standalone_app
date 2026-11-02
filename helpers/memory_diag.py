from __future__ import annotations
import os, ctypes, torch, time

_GIB=1024**3
_LAST_MEM_LOG={}

# V8: Windows WDDM per-process GPU memory counters. These are intentionally
# independent from torch.cuda.* because Task Manager's Shared GPU memory is
# tracked by WDDM/PerfLib, not by PyTorch's caching allocator.
_WDDM_DIAG = None

class _WDDMProcessMemory:
    PDH_FMT_LARGE = 0x00000400
    PDH_MORE_DATA = 0x800007D2

    class _ValueUnion(ctypes.Union):
        _fields_=[('longValue',ctypes.c_long),('doubleValue',ctypes.c_double),('largeValue',ctypes.c_longlong),('AnsiStringValue',ctypes.c_char_p),('WideStringValue',ctypes.c_wchar_p)]
    class _FmtValue(ctypes.Structure):
        pass
    _FmtValue._fields_=[('CStatus',ctypes.c_ulong),('value',_ValueUnion)]
    class _Item(ctypes.Structure):
        pass
    _Item._fields_=[('szName',ctypes.c_wchar_p),('FmtValue',_FmtValue)]

    def __init__(self):
        self.pid=os.getpid()
        self.pdh=ctypes.WinDLL('pdh.dll')
        self.pdh.PdhOpenQueryW.argtypes=[ctypes.c_wchar_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_void_p)]
        self.pdh.PdhOpenQueryW.restype=ctypes.c_long
        self.pdh.PdhAddCounterW.argtypes=[ctypes.c_void_p,ctypes.c_wchar_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_void_p)]
        self.pdh.PdhAddCounterW.restype=ctypes.c_long
        if hasattr(self.pdh,'PdhAddEnglishCounterW'):
            self.pdh.PdhAddEnglishCounterW.argtypes=self.pdh.PdhAddCounterW.argtypes
            self.pdh.PdhAddEnglishCounterW.restype=ctypes.c_long
        self.pdh.PdhCollectQueryData.argtypes=[ctypes.c_void_p]
        self.pdh.PdhCollectQueryData.restype=ctypes.c_long
        self.pdh.PdhGetFormattedCounterArrayW.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong),ctypes.POINTER(ctypes.c_ulong),ctypes.c_void_p]
        self.pdh.PdhGetFormattedCounterArrayW.restype=ctypes.c_long
        self.pdh.PdhCloseQuery.argtypes=[ctypes.c_void_p]
        self.queries={}
        self.error=None
        self._open('dedicated', r'\GPU Process Memory(*)\Dedicated Usage')
        self._open('shared', r'\GPU Process Memory(*)\Shared Usage')

    def _open(self,key,path):
        query=ctypes.c_void_p(); counter=ctypes.c_void_p()
        rc=self.pdh.PdhOpenQueryW(None,0,ctypes.byref(query))
        if rc: raise OSError(f'PdhOpenQueryW={rc & 0xffffffff:#x}')
        add=getattr(self.pdh,'PdhAddEnglishCounterW',self.pdh.PdhAddCounterW)
        rc=add(query,path,0,ctypes.byref(counter))
        if rc:
            self.pdh.PdhCloseQuery(query); raise OSError(f'PDH counter {path}: {rc & 0xffffffff:#x}')
        self.pdh.PdhCollectQueryData(query)
        self.queries[key]=(query,counter)

    def _read(self,key):
        query,counter=self.queries[key]
        self.pdh.PdhCollectQueryData(query)
        size=ctypes.c_ulong(0); count=ctypes.c_ulong(0)
        rc=self.pdh.PdhGetFormattedCounterArrayW(counter,self.PDH_FMT_LARGE,ctypes.byref(size),ctypes.byref(count),None)
        rc_u=rc & 0xffffffff
        if rc_u not in (self.PDH_MORE_DATA,0) or size.value==0: return None
        buf=ctypes.create_string_buffer(size.value)
        rc=self.pdh.PdhGetFormattedCounterArrayW(counter,self.PDH_FMT_LARGE,ctypes.byref(size),ctypes.byref(count),buf)
        if rc: return None
        arr=ctypes.cast(buf,ctypes.POINTER(self._Item))
        prefix=f'pid_{self.pid}_'.lower()
        total=0; found=False
        for i in range(count.value):
            name=(arr[i].szName or '').lower()
            if name.startswith(prefix) and arr[i].FmtValue.CStatus == 0:
                total += max(0,int(arr[i].FmtValue.value.largeValue)); found=True
        return total if found else 0

    def sample(self):
        return self._read('dedicated'), self._read('shared')

def _wddm_process_gpu_mem():
    if os.name != 'nt': return None,None
    global _WDDM_DIAG
    try:
        if _WDDM_DIAG is None: _WDDM_DIAG=_WDDMProcessMemory()
        return _WDDM_DIAG.sample()
    except Exception as exc:
        # Disable repeated PDH setup attempts but expose the reason once.
        if _WDDM_DIAG is None:
            _WDDM_DIAG=False
            print(f'[WDDM] per-process GPU counters unavailable: {exc}', flush=True)
        return None,None


def _fmt(n):
    if n is None: return 'n/a'
    return f'{n/_GIB:.2f} GiB'

def _process_mem():
    if os.name != 'nt':
        try:
            import resource
            rss=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*1024
            return rss, None, None
        except Exception:
            return None, None, None
    try:
        class PMC(ctypes.Structure):
            _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong),('PeakWorkingSetSize',ctypes.c_size_t),('WorkingSetSize',ctypes.c_size_t),('QuotaPeakPagedPoolUsage',ctypes.c_size_t),('QuotaPagedPoolUsage',ctypes.c_size_t),('QuotaPeakNonPagedPoolUsage',ctypes.c_size_t),('QuotaNonPagedPoolUsage',ctypes.c_size_t),('PagefileUsage',ctypes.c_size_t),('PeakPagefileUsage',ctypes.c_size_t)]
        pmc=PMC(); pmc.cb=ctypes.sizeof(PMC)
        ok=ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),ctypes.byref(pmc),pmc.cb)
        return (int(pmc.WorkingSetSize), int(pmc.PagefileUsage), int(pmc.PeakWorkingSetSize)) if ok else (None,None,None)
    except Exception:
        return None,None,None

def _system_mem():
    if os.name != 'nt': return None,None,None,None
    try:
        class MS(ctypes.Structure):
            _fields_=[('dwLength',ctypes.c_ulong),('dwMemoryLoad',ctypes.c_ulong),('ullTotalPhys',ctypes.c_ulonglong),('ullAvailPhys',ctypes.c_ulonglong),('ullTotalPageFile',ctypes.c_ulonglong),('ullAvailPageFile',ctypes.c_ulonglong),('ullTotalVirtual',ctypes.c_ulonglong),('ullAvailVirtual',ctypes.c_ulonglong),('ullAvailExtendedVirtual',ctypes.c_ulonglong)]
        s=MS(); s.dwLength=ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
        return int(s.ullAvailPhys), int(s.ullTotalPhys), int(s.ullAvailPageFile), int(s.ullTotalPageFile)
    except Exception:
        return None,None,None,None

def reset_cuda_peaks():
    if torch.cuda.is_available():
        try: torch.cuda.reset_peak_memory_stats()
        except Exception: pass

def log_mem(label, tensor=None, sync=False):
    if sync and torch.cuda.is_available():
        try: torch.cuda.synchronize()
        except Exception: pass
    rss,pagefile,peak_rss=_process_mem(); ram_avail,ram_total,pf_avail,pf_total=_system_mem()
    wddm_dedicated,wddm_shared=_wddm_process_gpu_mem()
    parts=[f'[MEM] {label}', f'RSS={_fmt(rss)}', f'RSS_peak={_fmt(peak_rss)}', f'commit={_fmt(pagefile)}', f'RAM_avail={_fmt(ram_avail)}', f'RAM_total={_fmt(ram_total)}', f'pagefile_avail={_fmt(pf_avail)}', f'WDDM_dedicated={_fmt(wddm_dedicated)}', f'WDDM_shared={_fmt(wddm_shared)}']
    if torch.cuda.is_available():
        try:
            free,total=torch.cuda.mem_get_info()
            parts += [f'CUDA_alloc={_fmt(torch.cuda.memory_allocated())}', f'CUDA_reserved={_fmt(torch.cuda.memory_reserved())}', f'CUDA_peak_alloc={_fmt(torch.cuda.max_memory_allocated())}', f'CUDA_peak_reserved={_fmt(torch.cuda.max_memory_reserved())}', f'CUDA_free={_fmt(free)}', f'CUDA_total={_fmt(total)}']
        except Exception as e: parts += [f'CUDA_mem_error={e}']
    if tensor is not None:
        try: parts += [f'shape={tuple(tensor.shape)}', f'dtype={tensor.dtype}', f'device={tensor.device}']
        except Exception: pass
    print(' | '.join(parts), flush=True)

def log_mem_throttled(key, label, tensor=None, interval=1.0, force=False, sync=False):
    """Rate-limit high-frequency memory traces while preserving stage logs."""
    now=time.monotonic()
    last=_LAST_MEM_LOG.get(str(key), -1e30)
    if force or (now-last) >= float(interval):
        _LAST_MEM_LOG[str(key)] = now
        log_mem(label, tensor=tensor, sync=sync)
        return True
    return False

def install_comfy_load_trace(comfy):
    """V8 diagnostic wrapper: logs WDDM/PyTorch memory immediately before and
    after every Comfy load_models_gpu call, including calls made inside KSampler.
    """
    try:
        mm=comfy.model_management
        original=mm.load_models_gpu
    except Exception:
        return lambda: None
    def traced(models,*args,**kwargs):
        req=kwargs.get('memory_required', args[0] if args else None)
        minimum=kwargs.get('minimum_memory_required',None)
        print(f'[WDDM-TRACE] Comfy load_models_gpu ENTER | models={len(models)} memory_required={req} minimum={minimum}',flush=True)
        log_mem('Comfy load_models_gpu BEFORE',sync=True)
        out=original(models,*args,**kwargs)
        log_mem('Comfy load_models_gpu AFTER',sync=True)
        return out
    mm.load_models_gpu=traced
    def cleanup():
        try: mm.load_models_gpu=original
        except Exception: pass
    return cleanup

def install_sampling_block_trace(model, expected_steps=None):
    """Attach lightweight forward hooks to MiniMax DiT/refiner blocks.
    Returns a cleanup function. Only use for diagnostic runs.
    """
    try: modules=list(model.model.named_modules())
    except Exception:
        return lambda: None
    targets=[(name,m) for name,m in modules if m.__class__.__name__ in ('DiTBlock','RefinerBlock')]
    dit_names=[name for name,m in targets if m.__class__.__name__=='DiTBlock']
    first_dit=dit_names[0] if dit_names else None
    handles=[]; state={'step':0,'calls':0}
    print(f'[TRACE] MiniMax sampling blocks discovered: DiT={len(dit_names)} Refiner={len(targets)-len(dit_names)} expected_steps={expected_steps}', flush=True)
    for name,module in targets:
        cls=module.__class__.__name__
        def pre(mod,args,_name=name,_cls=cls):
            if _name==first_dit:
                state['step']+=1
                print(f'[STEP] denoise pass {state["step"]}' + (f'/{expected_steps}' if expected_steps else ''), flush=True)
                log_mem(f'step {state["step"]} ENTER diffusion', args[0] if args else None)
            state['calls']+=1
            log_mem_throttled('sampling_blocks', f'{_cls} {_name} ACTIVE | block_call={state["calls"]}', args[0] if args else None, interval=1.0)
        def post(mod,args,out,_name=name,_cls=cls):
            return None
        handles.append(module.register_forward_pre_hook(pre))
        handles.append(module.register_forward_hook(post))
    def cleanup():
        for h in handles:
            try: h.remove()
            except Exception: pass
        print(f'[TRACE] Sampling block trace removed after {state["calls"]} block calls / {state["step"]} diffusion passes.', flush=True)
    return cleanup
