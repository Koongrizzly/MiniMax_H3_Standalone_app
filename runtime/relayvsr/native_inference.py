"""GrizzlyMax queue adapter for native ComfyUI RelayVSR (Comfy-Org INT8).

Spawns an isolated loopback-only ComfyUI process, queries its actual node
schemas, submits a native RelayVSR workflow, retrieves its result and exits.
No modification or imports of MiniMax's vendored ComfyUI. No model conversion.
"""
import argparse
from fractions import Fraction
import math
import tempfile
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
COMFY = ROOT/'models/relayvsr/native/native_comfy'
MODELS = ROOT/'models/relayvsr/native'
FILES = {
  2: ('relayvsr_x2_lightx2v_int8_convrot.safetensors', 'relayvsr_flash_vae_x2_fp32.safetensors'),
  4: ('relayvsr_x4_int8_convrot.safetensors', 'relayvsr_flash_vae_x4_fp32.safetensors'),
}


def log(message): print('[RELAY-NATIVE] '+str(message),flush=True)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RELAY_COUNTER = re.compile(r"(?:RelayVSR(?: Flash decode)?|RelayVSR Flash decode):\s*(\d{1,3})%.*?\b(\d+)\s*/\s*(\d+)")


class BackendLogForwarder:
    """Forward native ComfyUI progress without flooding normal GUI logging.

    Full native log remains on disk. Detailed lines go through the queue's
    Extended logging filter; simple progress lines remain visible in Normal.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.position = 0
        self.pending = ''
        self.last_progress = {}
        self.last_stage = None
        self.total_frames = 0
        self.completed_frames = 0
        self.current_segment_frames = 0
        self.last_overall_frames = -1

    def configure_video(self, total_frames):
        """Track overall output frames; individual ComfyUI steps are segment-local."""
        self.total_frames = int(total_frames)
        self.completed_frames = 0
        self.current_segment_frames = 0
        self.last_overall_frames = -1
        self._overall_progress(0)

    def begin_segment(self, completed, segment_frames):
        self.completed_frames = int(completed)
        self.current_segment_frames = int(segment_frames)
        self.last_progress.clear()
        self._overall_progress(self.completed_frames)

    def finish_segment(self):
        self.completed_frames = min(self.total_frames,
                                    self.completed_frames + self.current_segment_frames)
        self.current_segment_frames = 0
        self._overall_progress(self.completed_frames)

    def _overall_progress(self, completed, finished=False):
        if self.total_frames <= 0:
            return
        completed = max(self.last_overall_frames, min(self.total_frames, int(completed)))
        if completed == self.last_overall_frames and not finished:
            return
        self.last_overall_frames = completed
        # 100% is reserved for the fully assembled, verified video.
        percent = round(completed * 100 / self.total_frames)
        if not finished:
            percent = min(99, percent)
        print(f'RELAY_TOTAL_PROGRESS Upscaling | {percent}% | '
              f'{completed}/{self.total_frames}', flush=True)

    def poll(self, final=False):
        try:
            with self.path.open('r', encoding='utf-8', errors='replace') as stream:
                stream.seek(self.position)
                new = stream.read()
                self.position = stream.tell()
        except OSError:
            return
        if not new and not (final and self.pending):
            return
        data = self.pending + new
        # tqdm overwrites one terminal line with carriage returns, but the
        # native log file is not a terminal. Treat each update as a record.
        parts = re.split(r'[\r\n]', data)
        self.pending = '' if final else parts.pop()
        for raw in parts:
            self._line(raw)
        if final and self.pending:
            self._line(self.pending)
            self.pending = ''

    def _line(self, raw):
        line = _ANSI_RE.sub('', raw).strip()
        if not line:
            return
        # Extended mode gets every actual backend line, including model-load
        # and VRAM details. Normal mode filters RELAY_DETAIL lines out.
        print('[RELAY_DETAIL] ' + line, flush=True)
        match = _RELAY_COUNTER.search(line)
        if match:
            percent, current, total = (int(v) for v in match.groups())
            phase = 'Flash decode' if 'Flash decode' in line else 'Wan restoration'
            previous = self.last_progress.get(phase, -1)
            # Normal logging: report 0%, every 5%, and 100%; the raw backend
            # still provides all intermediate steps with Extended logging.
            if percent == 100 or percent == 0 and previous < 0 or percent >= previous + 5:
                self.last_progress[phase] = percent
                print(f'RELAY_PROGRESS {phase} | {percent}% | {current}/{total}', flush=True)
            # Flash decoding produces output frames. The native decoder can
            # include padded/overlap frames, so scale its progress to the
            # segment's *kept* source frames, never use 192/192 as the job total.
            if phase == 'Flash decode' and self.current_segment_frames and total:
                done = self.completed_frames + round(
                    self.current_segment_frames * min(current, total) / total)
                self._overall_progress(done)
            return
        stages = (
            ('Requested to load WAN22_RelayVSR', 'Loading INT8 Wan model'),
            ('Requested to load RelayFlash', 'Loading Flash decoder'),
            ('got prompt', 'Starting RelayVSR'),
            ('Prompt executed in', 'Native processing finished'),
        )
        for marker, label in stages:
            if marker in line and self.last_stage != label:
                self.last_stage = label
                print('RELAY_STAGE ' + label, flush=True)
                break


def api(port,path,payload=None,timeout=10):
    data=None if payload is None else json.dumps(payload).encode('utf-8')
    req=Request('http://127.0.0.1:%d%s'%(port,path),data=data,
                headers={'Content-Type':'application/json'} if data else {})
    with urlopen(req,timeout=timeout) as response:
        return json.load(response)


def pick_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0))
        return s.getsockname()[1]


def validate_files(scale):
    if not (COMFY/'comfy/ldm/wan/model_vsr.py').is_file():
        raise RuntimeError('Native ComfyUI RelayVSR backend not installed; use Settings > Install/Repair')
    if scale is not None:
        wan,vae=FILES[4]  # X4 checkpoint for both requested output sizes.
        for file in (MODELS/'diffusion_models'/wan, MODELS/'vae'/vae):
            if not file.is_file() or file.stat().st_size<50_000_000:
                raise RuntimeError('Native RelayVSR model missing: '+str(file))


def node(nodes,name):
    if name not in nodes:
        raise RuntimeError('Native ComfyUI node unavailable: %s. Installed backend may be too old.'%name)
    return nodes[name]


def outputs(nodes,name):
    return node(nodes,name).get('output',[])


def index_of_output(nodes,name,type_name,ordinal=0):
    matches=[i for i,x in enumerate(outputs(nodes,name)) if x==type_name]
    if len(matches)<=ordinal:
        raise RuntimeError('%s has no output of type %s'%(name,type_name))
    return matches[ordinal]


def input_info(nodes,name):
    obj=node(nodes,name)
    info=obj.get('input',{})
    return {k:v for group in ('required','optional') for k,v in info.get(group,{}).items()}


def set_node(nodes, graph, name, values):
    inputs=input_info(nodes,name)
    required=node(nodes,name).get('input',{}).get('required',{})
    missing=[key for key in required if key not in values]
    if missing:
        raise RuntimeError('%s missing required native inputs: %s'%(name, ', '.join(missing)))
    unknown=[key for key in values if key not in inputs]
    if unknown:
        raise RuntimeError('%s unexpected native inputs: %s'%(name, ', '.join(unknown)))
    key=str(len(graph)+1)
    graph[key]={'class_type':name,'inputs':values}
    return key


def pick_scalar(nodes,name,key,desired=None):
    info=input_info(nodes,name)[key]
    if desired is not None:
        return desired
    choices=info[0]
    meta=info[1] if len(info)>1 and isinstance(info[1],dict) else {}
    if isinstance(choices,list):
        return choices[0]
    if 'default' in meta:return meta['default']
    if choices=='BOOLEAN':return False
    if choices=='INT':return max(1,int(meta.get('min',1)))
    if choices=='FLOAT':return float(meta.get('min',0))
    if choices=='STRING':return ''
    if choices in ('COMFY_DYNAMICCOMBO_V3', 'COMFY_DYNAMICCOMBO_V2'):
        # Native ComfyUI dynamic selectors are represented by their schema type,
        # not by an inline list of choices. Only default a known field; never
        # guess arbitrarily for unrelated dynamic controls.
        defaults={('SaveVideo','format'):'mp4', ('SaveVideo','codec'):'auto'}
        if (name,key) in defaults:return defaults[name,key]
    raise RuntimeError('Cannot infer native input %s.%s type=%s'%(name,key,choices))


def wire(nodes,name,provided):
    result=dict(provided)
    for key in node(nodes,name).get('input',{}).get('required',{}):
        if key not in result:
            result[key]=pick_scalar(nodes,name,key)
    return result


def graph_for_video(nodes,filename,scale):
    # The node schema is queried from the running native ComfyUI build, not assumed.
    wan,vae=FILES[4]  # X4 checkpoint for both requested output sizes.
    graph={}
    loader='UNETLoader' if 'UNETLoader' in nodes else 'LoadDiffusionModel'
    model_inputs=input_info(nodes,loader)
    model_path_key=next((k for k in ('unet_name','model_name') if k in model_inputs),None)
    if not model_path_key:raise RuntimeError('Unsupported native diffusion model loader schema')
    model=set_node(nodes,graph,loader,wire(nodes,loader,{model_path_key:wan}))
    vae_loader='VAELoader'
    vae_key='vae_name'
    vae_node=set_node(nodes,graph,vae_loader,wire(nodes,vae_loader,{vae_key:vae}))

    # Core VIDEO workflow; avoids building an entire output IMAGE tensor in our adapter.
    load='LoadVideo'
    load_inputs=input_info(nodes,load)
    file_key=next((k for k in ('video','file','filename','path') if k in load_inputs),None)
    if file_key is None:raise RuntimeError('LoadVideo schema has no supported file input')
    source=set_node(nodes,graph,load,wire(nodes,load,{file_key:filename}))
    components='GetVideoComponents'
    source_video=[source,index_of_output(nodes,load,'VIDEO')]
    comp_inputs=input_info(nodes,components)
    comp_key=next((k for k in ('video','input') if k in comp_inputs),None)
    if comp_key is None:raise RuntimeError('GetVideoComponents schema is unsupported')
    comp=set_node(nodes,graph,components,wire(nodes,components,{comp_key:source_video}))
    image=[comp,index_of_output(nodes,components,'IMAGE')]

    cond_name='RelayVSRConditioning'
    cond_fields=input_info(nodes,cond_name)
    cond_args={}
    for key,spec in cond_fields.items():
        if spec[0]=='IMAGE':cond_args[key]=image
        elif spec[0]=='MODEL':cond_args[key]=[model,index_of_output(nodes,loader,'MODEL')]
    conditioning=set_node(nodes,graph,cond_name,wire(nodes,cond_name,cond_args))
    cond_outputs=outputs(nodes,cond_name)
    pos=[conditioning,index_of_output(nodes,cond_name,'CONDITIONING')]
    latent=[conditioning,index_of_output(nodes,cond_name,'LATENT')]
    negative=[conditioning,index_of_output(nodes,cond_name,'CONDITIONING',1)] if cond_outputs.count('CONDITIONING')>1 else pos
    sampler_name='KSampler'
    sampler=set_node(nodes,graph,sampler_name,wire(nodes,sampler_name,{
      'model':[model,index_of_output(nodes,loader,'MODEL')],
      'positive':pos,'negative':negative,'latent_image':latent,
      'seed':0,'steps':1,'cfg':1.0,'sampler_name':'euler','scheduler':'simple','denoise':1.0,
    }))
    decode_name='VSRDecode'
    decode_inputs=input_info(nodes,decode_name)
    decode_args={}
    for key,spec in decode_inputs.items():
        typ=spec[0]
        if typ=='VAE':decode_args[key]=[vae_node,index_of_output(nodes,vae_loader,'VAE')]
        elif typ=='IMAGE':decode_args[key]=image
        elif typ=='LATENT':decode_args[key]=[sampler,index_of_output(nodes,sampler_name,'LATENT')]
        elif key=='detail':decode_args[key]=1.0
    decoded=set_node(nodes,graph,decode_name,wire(nodes,decode_name,decode_args))
    decoded_images=[decoded,index_of_output(nodes,decode_name,'IMAGE')]
    if scale in (1, 2, 3):
        # X4 diffusion and Flash restoration for every scale; resize decoded
        # frames to the requested final output size. No X1/X2/X3 model downloads.
        resize_name='ImageScaleBy'
        if resize_name not in nodes:
            raise RuntimeError('Native ComfyUI missing ImageScaleBy; cannot resize X4 restoration')
        resized=set_node(nodes,graph,resize_name,wire(nodes,resize_name,{
            'images':decoded_images, 'upscale_method':'lanczos', 'scale_by':scale/4.0,
        }))
        decoded_images=[resized,index_of_output(nodes,resize_name,'IMAGE')]

    # Create video preserving original frame rate. Original audio will be remuxed
    # by GrizzlyMax's outer runner, avoiding duplicates from both pipelines.
    create='CreateVideo'
    create_inputs=input_info(nodes,create)
    create_args={}
    for key,spec in create_inputs.items():
        if spec[0]=='IMAGE':create_args[key]=decoded_images
        elif spec[0] in ('FLOAT','INT') and ('fps' in key.lower() or 'frame_rate' in key.lower()):
            fps_out=next((i for i,t in enumerate(outputs(nodes,components)) if t in ('FLOAT','INT')),None)
            if fps_out is not None:create_args[key]=[comp,fps_out]
    created=set_node(nodes,graph,create,wire(nodes,create,create_args))
    save='SaveVideo'
    save_inputs=input_info(nodes,save)
    save_args={}
    for key,spec in save_inputs.items():
        if spec[0]=='VIDEO':save_args[key]=[created,index_of_output(nodes,create,'VIDEO')]
        elif key=='filename_prefix':save_args[key]='relayvsr_native_'+uuid4().hex[:12]
        elif key=='format':
            # The ComfyUI 0.44 API uses COMFY_DYNAMICCOMBO_V3 for this input.
            # SaveVideo accepts a string value ("mp4") as well as a nested
            # dynamic-combo mapping; don't try to infer it from the type name.
            save_args[key]='mp4'
        elif key=='codec':
            # An explicit H.264 codec prevents auto selecting AV1/WebM.
            save_args[key]='h264'
    saved=set_node(nodes,graph,save,wire(nodes,save,save_args))
    return graph,saved




def find_saved_video(history, save_node, graph, output_dir):
    """Resolve a completed ComfyUI SaveVideo output across API versions.

    ComfyUI currently records saved MP4 files under outputs[node]['images']
    with animated=true. Older node builds may use videos/gifs instead.
    Only accept files within our isolated output directory and belonging to
    this job's unique filename prefix.
    """
    root=Path(output_dir).resolve()
    prefix=str(graph[save_node]['inputs']['filename_prefix'])
    candidates=[]
    out=history.get('outputs',{})
    if not isinstance(out,dict):out={}
    for node_id, result in out.items():
        if node_id != save_node or not isinstance(result,dict):
            continue
        for key in ('images','videos','gifs'):
            records=result.get(key,[])
            if not isinstance(records,list):continue
            for record in records:
                if not isinstance(record,dict):continue
                filename=record.get('filename','')
                if not isinstance(filename,str) or not filename.lower().endswith('.mp4'):
                    continue
                if not Path(filename).name.startswith(prefix):
                    continue
                if record.get('type','output') != 'output':
                    continue
                subfolder=record.get('subfolder','')
                if not isinstance(subfolder,str):continue
                candidate=(root/subfolder/filename).resolve()
                if candidate.is_relative_to(root):candidates.append(candidate)
    # The file is the source of truth when an API version omits/renames its
    # SaveVideo history payload; scanning is safe because the prefix is unique.
    for candidate in root.rglob(prefix+'*.mp4'):
        candidate=candidate.resolve()
        if candidate.is_relative_to(root):candidates.append(candidate)
    valid=[]
    for candidate in set(candidates):
        try:
            if candidate.is_file() and candidate.stat().st_size>1024:
                valid.append(candidate)
        except OSError:
            pass
    if not valid:
        return None
    if len(valid)!=1:
        raise RuntimeError('Multiple files matched this RelayVSR job: '+', '.join(str(v) for v in valid))
    return valid[0]


def video_meta(source):
    """Read stream geometry and duration without decoding video frames."""
    ffprobe=ROOT/'presets/bin/ffprobe.exe'
    if not ffprobe.is_file():
        raise RuntimeError('Bundled ffprobe.exe is required for RelayVSR frame budgeting')
    p=subprocess.run([str(ffprobe),'-v','error','-select_streams','v:0',
        '-show_entries','stream=width,height,avg_frame_rate,nb_frames,duration',
        '-show_entries','format=duration','-of','json',str(source)],
        capture_output=True,text=True,check=True)
    data=json.loads(p.stdout)
    stream=data['streams'][0]
    fps=Fraction(stream.get('avg_frame_rate','24/1'))
    if fps<=0:fps=Fraction(24,1)
    count=int(stream.get('nb_frames') or 0)
    if count<=0:
        duration=float(stream.get('duration') or data.get('format',{}).get('duration') or 0)
        count=math.ceil(duration*float(fps))
    if count<=0:raise RuntimeError('Could not determine source frame count')
    return int(stream['width']),int(stream['height']),fps,count


def frame_plan(width, height, fps, total_frames, scale, free_gib=None, total_gib=None):
    """Performance-first frame segments, scaled by available VRAM and X4 area.

    Start large and let the GPU work. A full-frame X4 restoration has the same
    internal size regardless of requested output scale. Segment caps avoid
    unbounded CPU video buffering and excessive file sizes.
    """
    pixels = width * height * 16
    if free_gib is None or total_gib is None:
        import torch
        free, total = torch.cuda.mem_get_info()
        free_gib, total_gib = free / (1024**3), total / (1024**3)
    # Available memory is more useful than GPU name or advertised capacity.
    # These aggressive first-pass targets are not verified hardware guarantees.
    # A lower-res input can use more frames for the same output pixel budget.
    if free_gib >= 19:
        base = 480
    elif free_gib >= 13:
        base = 320
    elif free_gib >= 9:
        base = 240
    elif free_gib >= 6.5:
        base = 160
    elif free_gib >= 4.5:
        base = 96
    else:
        base = 48
    # Reference: 960x544 input (~8.36M internal X4 pixels).
    reference_pixels = 960 * 544 * 16
    limit = int(base * (reference_pixels / max(1, pixels))**0.68)
    # Avoid tiny chunks; 480 frames is already an ambitious trial, rather than
    # assuming unlimited clips can fit within a single graph.
    limit = max(24, min(640, (limit // 8) * 8))
    segmented = total_frames > limit
    return {'limit': limit, 'segmented': segmented, 'total': total_frames,
            'pixels': pixels, 'fps': fps, 'free_gib': free_gib,
            'total_gib': total_gib, 'base_frames': base}


def select_performance_launch_profile(plan):
    """Fast only for bounded worksets; never choose HIGHVRAM solely by card size."""
    import torch
    free,total=torch.cuda.mem_get_info()
    gib=float(1024**3)
    free_gib,total_gib=free/gib,total/gib
    # High residency benefits a *short* job whose X4 activation footprint is
    # small. DynamicVRAM is more appropriate for long sequences / high res.
    high=(not plan['segmented'] and plan['pixels'] <= 1_500_000
          and total_gib>=23 and free_gib>=19)
    if high:
        flags=['--highvram','--disable-async-offload','--reserve-vram','1.5',
               '--fast','fp16_accumulation','cublas_ops']
        label='FAST GPU residency (bounded small workload)'
    else:
        flags=['--reserve-vram','1.5','--fast','fp16_accumulation','cublas_ops']
        label='Adaptive DynamicVRAM (avoids WDDM shared-memory spill)'
    log(f'[RELAY-PERF] {label} | workset={plan["limit"]} frames, '
        f'input={plan["total"]} frames | CUDA free={free_gib:.2f}/{total_gib:.2f} GiB')
    return flags


def segment_frames(total,limit,overlap=4):
    """Return (decode_start,decode_end,keep_start,keep_end) frame spans."""
    spans=[]
    for start in range(0,total,limit):
        end=min(total,start+limit)
        decode_start=max(0,start-overlap)
        decode_end=min(total,end+overlap)
        spans.append((decode_start,decode_end,start-decode_start,end-decode_start))
    return spans


def execute(command):
    log('[RELAY-STAGE] '+str(command[0])+' processing')
    proc=subprocess.run([str(x) for x in command],capture_output=True,text=True,errors='replace')
    if proc.returncode:
        raise RuntimeError('Video segment/assembly failed: '+proc.stderr[-2500:])


_NVENC_AVAILABLE = None


def nvenc_available():
    """Only prefer NVENC when this portable FFmpeg binary advertises it.

    Driver/runtime failures are handled by an automatic CPU-encoder fallback.
    No global settings are persisted.
    """
    global _NVENC_AVAILABLE
    if _NVENC_AVAILABLE is None:
        ffmpeg=ROOT/'presets/bin/ffmpeg.exe'
        if not ffmpeg.is_file():
            _NVENC_AVAILABLE=False
        else:
            try:
                result=subprocess.run([str(ffmpeg),'-hide_banner','-encoders'],
                                      capture_output=True,text=True,timeout=15,errors='replace')
                _NVENC_AVAILABLE=(result.returncode == 0 and
                                  re.search(r'(?m)^\s*V[^\n]*\bh264_nvenc\b', result.stdout) is not None)
            except (OSError,subprocess.TimeoutExpired):
                _NVENC_AVAILABLE=False
    return _NVENC_AVAILABLE


def segment_encode(command_prefix, destination, cpu_preset, cpu_crf):
    """Prefer hardware H.264 for repeated intermediate encodes.

    Keep the original high-quality x264 path as a reliable fallback for
    older RTX drivers or FFmpeg builds that cannot initialize NVENC.
    """
    if nvenc_available():
        try:
            execute(command_prefix + ['-c:v','h264_nvenc','-preset','p4',
              '-rc','vbr','-cq','14','-b:v','0','-pix_fmt','yuv420p',destination])
            return
        except RuntimeError as exc:
            # A failed NVENC initialization often leaves a partial output.
            # Fall back once rather than failing a whole long upscaling job.
            global _NVENC_AVAILABLE
            _NVENC_AVAILABLE=False
            Path(destination).unlink(missing_ok=True)
    execute(command_prefix + ['-c:v','libx264','-preset',cpu_preset,
                             '-crf',str(cpu_crf),'-pix_fmt','yuv420p',destination])


def create_segment(source,destination,begin,end,fps):
    ffmpeg=ROOT/'presets/bin/ffmpeg.exe'
    if not ffmpeg.is_file():raise RuntimeError('Bundled ffmpeg.exe is required')
    # Do input seeking, NOT trim=start_frame=N after -i: the latter decodes
    # all earlier frames again for every segment (quadratic work on long clips).
    # FFmpeg's accurate input seeking decodes only from the nearest keyframe.
    # The content is CFR (the runner already outputs CFR and uses a frame FPS).
    frame_count=end-begin
    if frame_count<=0:raise ValueError('Segment has no frames')
    begin_seconds=float(Fraction(begin,1)/fps)
    command=[ffmpeg,'-hide_banner','-loglevel','error','-y',
      '-ss',f'{begin_seconds:.9f}','-i',source,
      '-an','-fps_mode','cfr','-r',str(fps),'-frames:v',str(frame_count)]
    segment_encode(command,destination,'ultrafast',13)


def trim_output(source,destination,keep_begin,keep_end,fps):
    ffmpeg=ROOT/'presets/bin/ffmpeg.exe'
    command=[ffmpeg,'-hide_banner','-loglevel','error','-y','-i',source,
      '-vf',f'trim=start_frame={keep_begin}:end_frame={keep_end},setpts=PTS-STARTPTS',
      '-an','-fps_mode','cfr','-r',str(fps)]
    segment_encode(command,destination,'veryfast',12)


def join_segments(paths,destination,tmp_dir):
    ffmpeg=ROOT/'presets/bin/ffmpeg.exe'
    concat=tmp_dir/'concat.txt'
    # Paths are generated inside one temporary folder; use safe quoted local names.
    concat.write_text(''.join("file '"+p.name+"'\n" for p in paths),encoding='utf-8')
    execute([ffmpeg,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0',
             '-i',concat,'-an','-c:v','copy','-movflags','+faststart',destination])


def require_cuda():
    try:
        import torch
        if torch.version.cuda is None or not torch.cuda.is_available():
            raise RuntimeError('RelayVSR requires CUDA PyTorch and an NVIDIA GPU. Use Install / Repair.')
        log(f'CUDA_OK: torch={torch.__version__} cuda={torch.version.cuda} gpu={torch.cuda.get_device_name(0)}')
    except (ImportError, AssertionError, OSError) as exc:
        raise RuntimeError('RelayVSR CUDA environment is missing or broken; use Install / Repair') from exc

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--verify-imports',action='store_true')
    parser.add_argument('--input')
    parser.add_argument('--output')
    parser.add_argument('--scale',type=int,choices=(1,2,3,4))
    args=parser.parse_args()
    # Do not launch native ComfyUI or accept a misleading verify result on CPU Torch.
    require_cuda()
    validate_files(None if args.verify_imports else args.scale)
    if args.verify_imports:
        # This deliberately does not call native inference or falsely claim a GPU test.
        log('NATIVE_SOURCE_OK: native RelayVSR model code present; GPU inference not verified')
        return
    if not args.input or not args.output or args.scale is None:
        parser.error('--input --output --scale are required')
    source=Path(args.input).resolve()
    output=Path(args.output).resolve()
    if not source.is_file() or output.exists():
        raise RuntimeError('Input missing or output already exists')
    if output.suffix.lower()!='.mp4':raise RuntimeError('Output must be MP4')
    output.parent.mkdir(parents=True,exist_ok=True)
    input_dir=ROOT/'runtime/relayvsr/native_input'
    output_dir=ROOT/'runtime/relayvsr/native_output'
    input_dir.mkdir(parents=True,exist_ok=True)
    output_dir.mkdir(parents=True,exist_ok=True)
    width,height,fps,total_frames=video_meta(source)
    import torch
    _free, _total = torch.cuda.mem_get_info()
    plan=frame_plan(width,height,fps,total_frames,args.scale,
                    _free/(1024**3), _total/(1024**3))
    log(f'[RELAY-PERF] performance-first segments: {plan["limit"]} frames '
        f'(available VRAM={plan["free_gib"]:.2f} GiB; '
        f'internal X4 area={plan["pixels"]} pixels)')
    # Chunked jobs use a shared ComfyUI process, never reload model per chunk.
    temp_dir=tempfile.TemporaryDirectory(prefix='relayvsr_segments_')
    work=Path(temp_dir.name)
    linked=[]
    if not plan['segmented']:
        filename='relayvsr_'+uuid4().hex+source.suffix.lower()
        path=input_dir/filename
        try:os.link(source,path)
        except OSError:shutil.copy2(source,path)
        linked.append(path)
    else:
        filename=None
        log(f'[RELAY-PERF] long clip: {total_frames} frames -> '
            f'{len(segment_frames(total_frames,plan["limit"]))} bounded segments '
            f'(max {plan["limit"]} output frames each)')
    port=pick_port()
    # Register the portable checkpoints without copying or altering the vendored backend.
    # This generated YAML only configures the temporary native ComfyUI process;
    # all persistent user settings remain JSON under presets/setsave.
    model_paths=ROOT/'runtime/relayvsr/native_model_paths.yaml'
    model_paths.write_text('grizzlymax_relayvsr:\n  base_path: '+str(MODELS).replace('\\','/')+
                           '\n  diffusion_models: diffusion_models\n  vae: vae\n',encoding='utf-8')
    command=[sys.executable,'-u',str(COMFY/'main.py'),'--listen','127.0.0.1',
             '--port',str(port),'--disable-auto-launch',
             '--extra-model-paths-config',str(model_paths),
             '--input-directory',str(input_dir),'--output-directory',str(output_dir)]
    command.extend(select_performance_launch_profile(plan))
    log('Launching isolated native ComfyUI on loopback; requested output=X%d, model=X4 INT8'%args.scale)
    server_log=ROOT/'logs/relayvsr_native_backend.log'
    server_log.parent.mkdir(parents=True,exist_ok=True)
    server_stream=server_log.open('w',encoding='utf-8',errors='replace')
    backend_forwarder=BackendLogForwarder(server_log)
    backend_forwarder.configure_video(total_frames)
    server=subprocess.Popen(command,cwd=str(COMFY),stdout=server_stream,
                            stderr=subprocess.STDOUT)
    try:
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            backend_forwarder.poll()
            if server.poll() is not None:
                server_stream.flush()
                tail=server_log.read_text(encoding='utf-8',errors='replace')[-6000:]
                raise RuntimeError('Native ComfyUI startup failed:\n'+tail)
            try:
                schemas=api(port,'/object_info',timeout=4)
                break
            except (OSError,ValueError):time.sleep(1)
        else:raise RuntimeError('Native ComfyUI startup timed out')
        log('Native graph validated against %d installed node schemas'%len(schemas))
        segments=segment_frames(total_frames,plan['limit']) if plan['segmented'] else [(0,total_frames,0,total_frames)]
        collected=[]
        for segment_idx,(begin,end,keep_begin,keep_end) in enumerate(segments):
            # Each segment's output includes only its non-overlapping region.
            backend_forwarder.begin_segment(
                segment_idx * plan['limit'] if plan['segmented'] else 0,
                keep_end - keep_begin)
            if plan['segmented']:
                segment_source=work/f'source_{segment_idx:04d}.mp4'
                create_segment(source,segment_source,begin,end,fps)
                filename=f'relayvsr_seg_{uuid4().hex}.mp4'
                path=input_dir/filename
                try:os.link(segment_source,path)
                except OSError:shutil.copy2(segment_source,path)
                linked.append(path)
            graph,save_node=graph_for_video(schemas,filename,args.scale)
            submitted=api(port,'/prompt',{'prompt':graph,'client_id':'grizzlymax-relayvsr'},timeout=30)
            if 'error' in submitted:
                raise RuntimeError('Native workflow rejected: '+json.dumps(submitted)[:2000])
            pid=submitted['prompt_id']
            log(f'Running native RelayVSR segment {segment_idx+1}/{len(segments)}: '+pid)
            deadline=time.monotonic()+7200
            while time.monotonic()<deadline:
                backend_forwarder.poll()
                if server.poll() is not None:raise RuntimeError('Native ComfyUI process exited during inference')
                history=api(port,'/history/'+pid,timeout=20).get(pid)
                if history:
                    backend_forwarder.poll()
                    status=history.get('status',{})
                    if status.get('status_str')=='error':
                        raise RuntimeError('Native ComfyUI error: '+json.dumps(status.get('messages',[]))[-3000:])
                    result_path=find_saved_video(history,save_node,graph,output_dir)
                    if result_path is None:
                        node_result=history.get('outputs',{}).get(save_node,{})
                        raise RuntimeError('Workflow finished but SaveVideo MP4 is missing; '
                                           'history fields=%s, output_dir=%s' %
                                           (list(node_result) if isinstance(node_result,dict) else [], output_dir))
                    if plan['segmented']:
                        clean=work/f'clean_{segment_idx:04d}.mp4'
                        trim_output(result_path,clean,keep_begin,keep_end,fps)
                        collected.append(clean)
                        result_path.unlink(missing_ok=True)
                    else:
                        os.replace(result_path,output)
                    break
                time.sleep(2)
            else:
                raise RuntimeError('Native inference timed out')
            # Always remove completed segment input immediately.
            if plan['segmented']:
                linked[-1].unlink(missing_ok=True)
                segment_source.unlink(missing_ok=True)
            backend_forwarder.finish_segment()
            log(f'[RELAY-PERF] segment {segment_idx+1}/{len(segments)} complete')
        if plan['segmented']:
            join_segments(collected,output,work)
            _w,_h,_fps,actual_frames=video_meta(output)
            if abs(actual_frames-total_frames)>1:
                raise RuntimeError(f'Assembled frame-count mismatch: {actual_frames} vs {total_frames}')
        # Report 100% only after the final MP4 has been assembled/verified.
        backend_forwarder._overall_progress(total_frames, finished=True)
        log('NATIVE_COMPLETE: '+str(output))
        return
    finally:
        backend_forwarder.poll()
        if server.poll() is None:
            server.terminate()
            try:server.wait(timeout=12)
            except subprocess.TimeoutExpired:server.kill();server.wait()
        server_stream.close()
        backend_forwarder.poll(final=True)
        for path in linked:
            path.unlink(missing_ok=True)
        temp_dir.cleanup()


if __name__=='__main__':
    try:main()
    except Exception as exc:
        log('NATIVE_FAILED: '+str(exc))
        sys.exit(1)
