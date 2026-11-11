"""Portable MiniMax H3 batch importer. Parsing only; no Qt or file-system mutations."""
from __future__ import annotations
import json
import re
from pathlib import Path

_HEADER = re.compile(r'^\s*(\d{3})\s*\|\s*([^|\n]+)\|\s*([^\n]+?)\s*$', re.M)
_SECTION = re.compile(r'^\s*BEGIN PROMPT\s*$\n?(.*?)^\s*END PROMPT\s*$', re.M | re.S | re.I)
_SEPARATOR = re.compile(r'^\s*(?:={5,}|-{5,}|---\s*PROMPT\s*---)\s*$', re.M | re.I)
_PATH = re.compile(r'^\s*Subject\s+(\d+)\s*:\s*(.+?)\s*$', re.M | re.I)
_SUPPORTED = {
    'prompt','name','mode','frames','resolution','width','height','aspect','steps','seed',
    'cfg','shift','audio_shift','sampler','scheduler','reference_images','ref_images',
    'reference_videos','ref_videos','reference_audio','ref_audios','reference_mods','refmods',
    'audio_1','audio_subject','audio_subjects','ref_audio_subjects','lock_source_audio','exact_audio',
    'experimental_long_duration','output_name','output_folder','ref_size',
    'use_hybrid_model','hybrid_model','loras','vram_manager_enabled',
    'spectrum_enabled','sage_attention_enabled','sol_attention_enabled','sla_attention_enabled',
    'first','last','continue_video','continue_last_result','continue_context_frames',
    'glue_results','continue_audio_memory','latent_continuation','combine_frames_latent',
    'refmod_qwen_vision','t2va_use_refmods','generation_vsr_enabled','generation_vsr_scale',
    'generation_vsr_engine', 'vram_manager_auto_bypass','vram_residency_engine',
    'vram_runtime_free_gb','vram_text_headroom_gb','vram_diffusion_headroom_gb',
    'vram_offload_chunk_mb','vram_max_resident_weights_gb','vram_residency_fill',
    'vram_residency_target_free_gb','vram_residency_warmup_blocks',
    'vram_residency_refill_interval','vram_block_check_interval','vram_async_streams',
    'vram_video_vae_reserve_gb','vram_audio_vae_reserve_gb','vram_video_vae_tile_size',
    'vram_video_vae_tile_overlap', 'extended_logging','tile_debugging',
}
_ALIASES = {'reference_images':'ref_images','reference_videos':'ref_videos',
            'reference_audio':'ref_audios','reference_mods':'refmods','exact_audio':'lock_source_audio',
            'ref_audio_subjects':'audio_subjects'}


def parse_file(path):
    path = Path(path)
    data = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.json':
        payload = json.loads(data)
        if isinstance(payload, list):
            defaults, clips = {}, payload
        elif isinstance(payload, dict):
            defaults = payload.get('defaults', {})
            clips = payload.get('jobs', payload.get('clips'))
            if clips is None and 'prompt' in payload:
                clips = [payload]
            if clips is None:
                raise ValueError('JSON must contain "jobs" or "clips" (or a single prompt).')
        else:
            raise ValueError('JSON must be a list or an object.')
        if not isinstance(defaults, dict) or not isinstance(clips, list):
            raise ValueError('JSON defaults must be an object and jobs must be a list.')
        result = []
        for index, clip in enumerate(clips, 1):
            if isinstance(clip, str):
                clip = {'prompt':clip}
            if not isinstance(clip, dict):
                raise ValueError(f'Job {index}: expected object or prompt string.')
            spec = {**defaults, **clip}
            normalize_spec(spec, index)
            result.append(spec)
        if not result: raise ValueError('Batch is empty.')
        return result
    if path.suffix.lower() != '.txt':
        raise ValueError('Select a .txt or .json file.')
    matches = list(_SECTION.finditer(data))
    if matches:
        result = []
        for index, match in enumerate(matches, 1):
            # Take the immediately preceding numbered header, not the notes and
            # not the header from the previous prompt.
            start = matches[index-2].end() if index > 1 else 0
            meta = data[start:match.start()]
            headers = list(_HEADER.finditer(meta))
            if not headers:
                raise ValueError(f'Prompt block {index}: missing numbered job header.')
            meta = meta[headers[-1].start():]
            header = headers[-1]
            spec = {'name':header.group(3).strip(), 'prompt':match.group(1).strip()}
            mode = re.search(r'^MODE:\s*(.+?)\s*$',meta,re.M|re.I)
            if mode: spec['mode'] = mode.group(1).strip()
            hq = re.search(r'^HQ:\s*(\d+)\s*x\s*(\d+)\s*\|\s*(\d+)\s*steps\s*\|\s*(\d+)\s*frames',meta,re.M|re.I)
            if hq:
                spec.update(width=int(hq.group(1)),height=int(hq.group(2)),steps=int(hq.group(3)),frames=int(hq.group(4)))
            seed = re.search(r'^SEED:\s*(-?\d+)\s*$',meta,re.M|re.I)
            if seed: spec['seed']=int(seed.group(1))
            ref_section = re.search(r'^REFERENCE IMAGES[^\n]*:\s*\n(.*?)(?=^[A-Z][A-Z 0-9/]+:|\Z)', meta, re.M|re.S)
            if ref_section:
                indexed = [(int(n), p.strip()) for n,p in _PATH.findall(ref_section.group(1))]
                if indexed:
                    indexed.sort()
                    if [n for n,_ in indexed] != list(range(1,len(indexed)+1)):
                        raise ValueError(f'Job {index}: reference subjects must be numbered 1, 2, ...')
                    spec['ref_images'] = [p for _,p in indexed]
            audio = re.search(r'^AUDIO 1:\s*(.*?)\s*$',meta,re.M|re.I)
            if audio:
                value=audio.group(1).strip()
                spec['ref_audios'] = [] if value.lower().startswith('none') else [value]
            exact = re.search(r'^USE AUDIO 1 AS EXACT SOURCE / OUTPUT:\s*(ON|OFF)\s*$',meta,re.M|re.I)
            if exact: spec['lock_source_audio'] = exact.group(1).upper()=='ON'
            subject = re.search(r'^AUDIO SUBJECT:\s*Subject\s*(\d+)',meta,re.M|re.I)
            if subject: spec['audio_subject']=int(subject.group(1))
            normalize_spec(spec,index)
            result.append(spec)
        return result
    # Simple .txt: one prompt per delimiter. A file without delimiters is one prompt.
    chunks = [x.strip() for x in _SEPARATOR.split(data) if x.strip()]
    if not chunks: raise ValueError('Text file does not contain any prompts.')
    return [{'name':f'clip_{n:03d}', 'prompt':chunk} for n,chunk in enumerate(chunks,1)]


def normalize_spec(spec, index):
    unknown = sorted(set(spec) - _SUPPORTED)
    if unknown:
        raise ValueError(f'Job {index}: unknown settings: {", ".join(unknown)}')
    for source, dest in _ALIASES.items():
        if source in spec:
            if dest in spec: raise ValueError(f'Job {index}: both {source} and {dest} were supplied.')
            spec[dest] = spec.pop(source)
    prompt = spec.get('prompt')
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError(f'Job {index}: prompt is empty.')
    spec['prompt'] = prompt.strip()
    for key in ('ref_images','ref_videos','ref_audios','refmods'):
        if key in spec and (not isinstance(spec[key],list) or any(not isinstance(p,str) for p in spec[key])):
            raise ValueError(f'Job {index}: {key} must be a list of paths.')
    if 'audio_1' in spec:
        path = spec.pop('audio_1')
        if path is not None and not isinstance(path,str): raise ValueError(f'Job {index}: audio_1 must be a path or null.')
        spec['ref_audios'] = [path] if path else []
    if 'audio_subjects' in spec and not isinstance(spec['audio_subjects'],list):
        raise ValueError(f'Job {index}: audio_subjects must be a list.')
