"""Reusable SJTU lecture media helpers. Commands execute only when invoked."""
import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent


def config():
    path = SKILL / 'references/local_config.json'
    cfg = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    root = os.environ.get('SJTU_CANVAS2NOTE_ROOT') or cfg.get('tool_root')
    if not root:
        raise ValueError('Set SJTU_CANVAS2NOTE_ROOT or create references/local_config.json from the example')
    return {**cfg, 'tool_root': root}


def tool_root():
    root = Path(config()['tool_root']).resolve()
    if not (root / 'canvas/video_current.py').is_file():
        raise FileNotFoundError('Configured Canvas2Note installation unavailable')
    return root


def seconds(value):
    parts = value.split(':')
    if len(parts) > 3:
        raise argparse.ArgumentTypeError('Use seconds, MM:SS or HH:MM:SS')
    try:
        values = [float(v) for v in parts]
        total = sum(v*60**i for i, v in enumerate(reversed(values)))
        if not math.isfinite(total) or total < 0 or any(v < 0 for v in values) or any(v >= 60 for v in values[1:]):
            raise ValueError()
        return total
    except ValueError:
        raise argparse.ArgumentTypeError('Invalid timestamp')


def stamp(value, srt=False):
    ms = round(value*1000)
    text = f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}'
    return text + (f',{ms%1000:03}' if srt else '')


def doctor(_):
    root = tool_root()
    settings = json.loads((root / 'settings.json').read_text(encoding='utf-8')) if (root / 'settings.json').exists() else {}
    print(json.dumps({
        'tool_root': str(root), 'python': sys.executable,
        'modules': {n: importlib.util.find_spec(n) is not None for n in ['av', 'faster_whisper', 'requests', 'PIL', 'yaml']},
        'login_saved': bool(settings.get('ja_auth_cookie')),
        'gpu_library_dirs': [str(p) for p in (Path(sys.prefix) / 'Lib/site-packages/nvidia').glob('*/bin')],
        'model_cache': str(root / 'work/models'),
        'note': 'Local checks only; no authentication, download or ASR executed.'
    }, ensure_ascii=False, indent=2))


def video_client(course_id):
    root = tool_root()
    sys.path.insert(0, str(root))
    from canvas.video_client import VideoClient
    settings = json.loads((root / 'settings.json').read_text(encoding='utf-8'))
    client = VideoClient(ja_auth_cookie=settings.get('ja_auth_cookie', ''))
    client.bind_canvas_course(course_id)
    return client


def videos(args):
    print(json.dumps(video_client(args.course_id).list_course_videos(args.course_id), ensure_ascii=False, indent=2))


def download(args):
    output = Path(args.output).resolve()
    if output.suffix.lower() != '.mp4':
        raise ValueError('Output must be an MP4 path')
    def progress(p):
        # Media URLs and credentials are never printed.
        print(json.dumps({'downloaded_mb': round(p.processed/1048576, 1), 'total_mb': round(p.total/1048576, 1)}), flush=True)
    result = video_client(args.course_id).download_video(args.video_id, output, title=output.stem, play_index=args.track, progress_handler=progress)
    print('Saved:', result)


def frames(args):
    import av
    video = Path(args.video).resolve(strict=True)
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    index = output / 'frames-index.json'
    if index.exists():
        raise FileExistsError(index)
    for sec in args.times:
        if (output / f'frame_{sec:010.3f}s.png').exists():
            raise FileExistsError('Requested frame output already exists')
    records = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else (container.duration / av.time_base if container.duration else None)
        for sec in args.times:
            if duration is not None and sec >= duration:
                raise ValueError(f'Timestamp {sec} exceeds recording duration {duration}')
            container.seek(int(sec / stream.time_base), stream=stream, backward=True)
            for frame in container.decode(stream):
                if frame.time is not None and frame.time >= sec:
                    path = output / f'frame_{sec:010.3f}s.png'
                    if path.exists():
                        raise FileExistsError(path)
                    frame.to_image().save(path)
                    records.append({'requested_seconds': sec, 'actual_seconds': frame.time, 'width': frame.width, 'height': frame.height, 'file': path.name})
                    break
            else:
                raise RuntimeError(f'No frame decoded at {sec}')
    index.write_text(json.dumps({'video': str(video), 'frames': records}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(records, ensure_ascii=False, indent=2))


def transcribe(args):
    import ctranslate2
    from faster_whisper import WhisperModel, BatchedInferencePipeline
    root = tool_root()
    video = Path(args.video).resolve(strict=True)
    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    stem = args.stem or video.stem
    if any(c in stem for c in '<>:"/\\|?*') or stem in {'.', '..'}:
        raise ValueError('Invalid output stem')
    files = {ext: out / (stem + '.' + ext) for ext in ['txt', 'srt', 'json']}
    if any(p.exists() for p in files.values()):
        raise FileExistsError('Transcript output already exists; choose a new stem')
    dirs = list((Path(sys.prefix) / 'Lib/site-packages/nvidia').glob('*/bin'))
    handles = [os.add_dll_directory(str(p.resolve())) for p in dirs] if os.name == 'nt' else []
    os.environ['PATH'] = os.pathsep.join(str(p.resolve()) for p in dirs) + os.pathsep + os.environ['PATH']
    device = args.device
    if device == 'auto':
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=memory.free', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5)
            free = int(result.stdout.splitlines()[0]) if result.returncode == 0 else 0
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            free = 0
        libs_ready = os.name != 'nt' or (any((p / 'cublas64_12.dll').exists() for p in dirs) and any((p / 'cudnn64_9.dll').exists() for p in dirs))
        device = 'cuda' if ctranslate2.get_cuda_device_count() > 0 and free >= 4500 and libs_ready else 'cpu'
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
    model = WhisperModel(args.model, device=device, compute_type='float16' if device == 'cuda' else 'int8', cpu_threads=8, download_root=str(root / 'work/models'))
    segments, info = BatchedInferencePipeline(model=model).transcribe(str(video), language=args.language, batch_size=4, beam_size=5, vad_filter=True)
    records = []
    txt_part = files['txt'].with_suffix('.txt.part')
    srt_part = files['srt'].with_suffix('.srt.part')
    with txt_part.open('w', encoding='utf-8') as txt, srt_part.open('w', encoding='utf-8') as srt:
        txt.write('自动语音识别草稿（ASR draft），数学符号、字母、数字等仍需核对。\n\n')
        for i, seg in enumerate(segments, 1):
            records.append({'start': seg.start, 'end': seg.end, 'text': seg.text.strip()})
            txt.write(f'[{stamp(seg.start)}–{stamp(seg.end)}] {seg.text.strip()}\n'); txt.flush()
            srt.write(f'{i}\n{stamp(seg.start, True)} --> {stamp(seg.end, True)}\n{seg.text.strip()}\n\n'); srt.flush()
            if i % 20 == 0:
                print('Recognized through:', stamp(seg.end), flush=True)
    files['json'].write_text(json.dumps({'status': 'complete', 'model': args.model, 'device': device, 'language': args.language, 'duration': info.duration, 'video': str(video), 'segments': records}, ensure_ascii=False, indent=2), encoding='utf-8')
    txt_part.replace(files['txt']); srt_part.replace(files['srt'])
    print('Saved transcript:', files['txt'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor').set_defaults(func=doctor)
    p = sub.add_parser('videos'); p.add_argument('--course-id', type=int, required=True); p.set_defaults(func=videos)
    p = sub.add_parser('download'); p.add_argument('--course-id', type=int, required=True); p.add_argument('--video-id', required=True); p.add_argument('--output', required=True); p.add_argument('--track', type=int, default=-1); p.set_defaults(func=download)
    p = sub.add_parser('frames'); p.add_argument('--video', required=True); p.add_argument('--output-dir', required=True); p.add_argument('--times', nargs='+', type=seconds, required=True); p.set_defaults(func=frames)
    p = sub.add_parser('transcribe'); p.add_argument('--video', required=True); p.add_argument('--output-dir', required=True); p.add_argument('--stem'); p.add_argument('--model', default='medium'); p.add_argument('--device', choices=['auto','cuda','cpu'], default='auto'); p.add_argument('--language', default='zh'); p.set_defaults(func=transcribe)
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        # Requests exceptions may embed signed media URLs; do not expose them.
        if args.command in {'videos', 'download'}:
            parser.exit(1, f'{type(exc).__name__}: lecture service request failed; check login and local diagnostics.\n')
        parser.exit(1, f'{type(exc).__name__}: {exc}\n')


if __name__ == '__main__':
    main()
