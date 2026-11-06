"""Out-of-process word transcription to avoid cross-environment imports."""
import argparse,json,sys
from pathlib import Path

def _ensure_pyav_compatibility():
    """Support faster-whisper releases using an argument removed by PyAV 19.

    The compatibility wrapper is confined to the isolated transcription process.
    PyAV 19 always uses UTF-8/surrogateescape for metadata, so dropping the
    obsolete metadata_errors option is equivalent to the intended behavior.
    """
    import av

    try:
        major = int(str(av.__version__).split(".", 1)[0])
    except (AttributeError, ValueError):
        return
    if major < 19:
        return

    original_open = av.open

    def compatible_open(*args, **kwargs):
        kwargs.pop("metadata_errors", None)
        kwargs.pop("metadata_encoding", None)
        return original_open(*args, **kwargs)

    av.open = compatible_open


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--audio',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True)
    ap.add_argument('--device',default='cpu');ap.add_argument('--compute-type',default='int8');args=ap.parse_args()
    _ensure_pyav_compatibility()
    from faster_whisper import WhisperModel
    engine=WhisperModel(args.model,device=args.device,compute_type=args.compute_type,local_files_only=True)
    segments,_=engine.transcribe(args.audio,beam_size=5,word_timestamps=True,condition_on_previous_text=False,vad_filter=False)
    rows=[]
    for li,seg in enumerate(segments):
        for word in seg.words or []:
            token=word.word.strip()
            if token: rows.append({'w':token,'t0':round(word.start,3),'t1':round(word.end,3),'line':li})
    if not rows: raise RuntimeError('No words detected')
    Path(args.output).write_text(json.dumps(rows,ensure_ascii=False),encoding='utf-8')
if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'Shared Whisper error: {exc}',file=sys.stderr);sys.exit(2)
