from __future__ import annotations
import argparse, gc, torch
from pathlib import Path
from PIL import Image, ImageDraw
from runtime.headless_h3 import load_vae, decode_video, _flush_models
from runtime.memory_diag import log_mem, log_mem_throttled, reset_cuda_peaks

def _compute_tile_plan(fs, video):
    height = int(video.shape[-2] * fs.vae_ratio)
    width = int(video.shape[-1] * fs.vae_ratio)
    y_idx, y_len, y_overlap = fs.split_tiles(height)
    x_idx, x_len, x_overlap = fs.split_tiles(width)
    placements = []
    out_y = 0
    for i, (i_pos, i_len) in enumerate(zip(y_idx, y_len)):
        tile_h = i_len
        if i < len(y_idx) - 1:
            tile_h -= y_overlap[i]
        out_x = 0
        for j, (j_pos, j_len) in enumerate(zip(x_idx, x_len)):
            tile_w = j_len
            if j < len(x_idx) - 1:
                tile_w -= x_overlap[j]
            placements.append({
                'row': i, 'col': j,
                'src_x': int(j_pos), 'src_y': int(i_pos),
                'src_w': int(j_len), 'src_h': int(i_len),
                'dst_x': int(out_x), 'dst_y': int(out_y),
                'dst_w': int(tile_w), 'dst_h': int(tile_h),
                'right_overlap': int(x_overlap[j]) if j < len(x_overlap) else 0,
                'bottom_overlap': int(y_overlap[i]) if i < len(y_overlap) else 0,
            })
            out_x += tile_w
        out_y += tile_h
    return {
        'frame_width': width,
        'frame_height': height,
        'y_idx': [int(v) for v in y_idx],
        'y_len': [int(v) for v in y_len],
        'y_overlap': [int(v) for v in y_overlap],
        'x_idx': [int(v) for v in x_idx],
        'x_len': [int(v) for v in x_len],
        'x_overlap': [int(v) for v in x_overlap],
        'placements': placements,
    }

def _write_tile_plan(plan, out_dir: Path):
    txt = out_dir / 'tile_debug_plan.txt'
    lines = []
    lines.append(f"frame_size={plan['frame_width']}x{plan['frame_height']}")
    lines.append(f"y_idx={plan['y_idx']}")
    lines.append(f"y_len={plan['y_len']}")
    lines.append(f"y_overlap={plan['y_overlap']}")
    lines.append(f"x_idx={plan['x_idx']}")
    lines.append(f"x_len={plan['x_len']}")
    lines.append(f"x_overlap={plan['x_overlap']}")
    lines.append('')
    for p in plan['placements']:
        lines.append(
            f"tile r{p['row']} c{p['col']} | src=({p['src_x']},{p['src_y']},{p['src_w']},{p['src_h']}) | "
            f"dst=({p['dst_x']},{p['dst_y']},{p['dst_w']},{p['dst_h']}) | "
            f"right_overlap={p['right_overlap']} bottom_overlap={p['bottom_overlap']}"
        )
    txt.write_text('\n'.join(lines), encoding='utf-8')
    print(f'Tile debug plan saved: {txt}', flush=True)

def _save_grid_overlay(plan, out_dir: Path):
    w, h = plan['frame_width'], plan['frame_height']
    grid = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(grid)
    colors = [(255,0,0,110),(0,255,0,110),(0,128,255,110),(255,200,0,110),(255,0,255,110),(0,255,255,110)]
    for idx, p in enumerate(plan['placements']):
        x0, y0 = p['dst_x'], p['dst_y']
        x1, y1 = x0 + p['dst_w'] - 1, y0 + p['dst_h'] - 1
        color = colors[idx % len(colors)]
        draw.rectangle([x0, y0, x1, y1], outline=color, width=3)
        label = f"{p['row']},{p['col']}"
        tx = min(max(2, x0 + 6), max(2, w - 36))
        ty = min(max(2, y0 + 6), max(2, h - 16))
        draw.rectangle([tx-2, ty-2, tx+28, ty+12], fill=(0,0,0,140))
        draw.text((tx, ty), label, fill=(255,255,255,255))
    grid_path = out_dir / 'tile_debug_grid.png'
    grid.save(grid_path)
    print(f'Tile debug grid saved: {grid_path}', flush=True)

def _save_overlay_on_frame(plan, frames_dir: Path):
    frame0 = frames_dir / 'frame_000000.png'
    if not frame0.exists():
        return
    grid_path = frames_dir / 'tile_debug_grid.png'
    if not grid_path.exists():
        return
    base = Image.open(frame0).convert('RGBA')
    grid = Image.open(grid_path).convert('RGBA')
    over = Image.alpha_composite(base, grid)
    out = frames_dir / 'tile_debug_overlay_frame0.png'
    over.save(out)
    print(f'Tile debug overlay saved: {out}', flush=True)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--latents',required=True); ap.add_argument('--vae',required=True); ap.add_argument('--frames-dir',required=True); ap.add_argument('--extended-logging', action='store_true'); ap.add_argument('--tile-debugging', action='store_true'); ap.add_argument('--tile-size', type=int, default=256); ap.add_argument('--tile-overlap', type=int, default=128)
    ns=ap.parse_args()
    if ns.tile_size < 128:
        ap.error('--tile-size must be at least 128 px')
    if ns.tile_overlap < 0 or ns.tile_overlap >= ns.tile_size:
        ap.error('--tile-overlap must be >= 0 and smaller than --tile-size')
    torch.set_grad_enabled(False)
    d=torch.load(ns.latents,map_location='cpu',weights_only=False); video=d['video']
    import comfy.model_management as mm
    vae_dev=mm.vae_device()
    if ns.extended_logging:
        print(f'Comfy VAE device resolved to: {vae_dev}', flush=True)
        print(f'Autograd enabled before decode: {torch.is_grad_enabled()}', flush=True)
    if ns.extended_logging:
        reset_cuda_peaks(); log_mem('worker start', video)
    print('Loading video VAE...', flush=True)
    if ns.extended_logging: print('Video VAE memory mode: dynamic CPU offload', flush=True)
    vv=load_vae(ns.vae)
    if ns.extended_logging: log_mem('after VAE object/load_state_dict')

    # Apply the selected tile geometry before diagnostics as well as decode, so the
    # tile-debug grid always describes the settings that actually produced the MP4.
    fs = vv.first_stage_model
    if hasattr(fs, 'tile_size'):
        fs.tile_size = int(ns.tile_size)
    if hasattr(fs, 'tile_overlap_min'):
        fs.tile_overlap_min = int(ns.tile_overlap)

    # Tile-debug artifacts are independently opt-in. Extended logging controls only
    # verbose memory/offload traces; normal generations do not extract tile files.
    plan = None
    if ns.tile_debugging:
        try:
            plan = _compute_tile_plan(vv.first_stage_model, video)
            out_dir = Path(ns.frames_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            _write_tile_plan(plan, out_dir)
            _save_grid_overlay(plan, out_dir)
            print(f"Tile plan summary: {len(plan['y_idx'])} rows x {len(plan['x_idx'])} cols", flush=True)
        except Exception as e:
            print(f'Tile plan diagnostic failed: {e}', flush=True)

    import comfy.ldm.minimax.vae as mmvae
    original_forward=mmvae.TransformerBlock.forward
    if ns.extended_logging:
        call_counter={'n':0}
        def traced_forward(self, x, rotary_pos_emb=None):
            n=call_counter['n']; call_counter['n']=n+1
            log_mem_throttled('video_vae_blocks', f'video VAE transformer ACTIVE | block_call={n}', x, interval=1.0)
            out=original_forward(self,x,rotary_pos_emb)
            return out
        mmvae.TransformerBlock.forward=traced_forward

    print('Decoding video frames...', flush=True)
    if ns.extended_logging: print(f'VAE decode details: torch.inference_mode(), {ns.tile_size}px MiniMax tiles / {ns.tile_overlap}px overlap', flush=True)
    try:
        with torch.inference_mode():
            if ns.extended_logging:
                print(f'Autograd enabled inside inference context: {torch.is_grad_enabled()}', flush=True)
                log_mem('immediately before decode', video)
            images=decode_video(video,vv,force_tiled=True,tile_size=ns.tile_size,tile_overlap=ns.tile_overlap)
            if ns.extended_logging: log_mem('immediately after decode', images)
    finally:
        if ns.extended_logging:
            mmvae.TransformerBlock.forward=original_forward
            print(f'[TRACE] Video VAE transformer trace removed after {call_counter["n"]} internal block calls; memory samples were rate-limited to ~1/sec.', flush=True)
    out=Path(ns.frames_dir); out.mkdir(parents=True,exist_ok=True)
    imgs=(images.clamp(0,1).numpy()*255.0+0.5).astype('uint8')
    for i,frame in enumerate(imgs): Image.fromarray(frame).save(out/f'frame_{i:06d}.png')
    if plan is not None:
        _save_overlay_on_frame(plan, out)
    del images, imgs, vv, video, d; _flush_models(); gc.collect()
    if ns.extended_logging: log_mem('video worker finished')
    print(f'Video decode stage complete: {i+1} frames written.', flush=True)
    return 0
if __name__=='__main__': raise SystemExit(main())
