"""Free local production helpers. Model files are reused, never copied or downloaded."""
from __future__ import annotations
import argparse
from array import array
import glob
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import wave

APP_ROOT = Path(__file__).resolve().parents[1]


def _first(paths, directory=False):
    for item in paths:
        if item:
            path = Path(item).expanduser()
            if (path.is_dir() if directory else path.is_file()):
                return str(path.resolve())
    return None


def discover_audio():
    """Find existing Kokoro assets and its Python 3.12 runtime; never download."""
    user = Path.home()
    roots = [os.environ.get('STUDIO_AUDIO_ROOT'), APP_ROOT/'tools'/'kokoro', user/'Downloads'/'kokoro']
    roots = [Path(p).expanduser() for p in roots if p]
    model = _first([os.environ.get('KOKORO_MODEL_PATH')] + [p/'models'/'kokoro-v1.0.onnx' for p in roots] + [p/'kokoro-v1.0.onnx' for p in roots])
    voices = _first([os.environ.get('KOKORO_VOICES_PATH')] + [p/'models'/'voices-v1.0.bin' for p in roots] + [p/'voices-v1.0.bin' for p in roots])
    packages = _first([os.environ.get('KOKORO_PACKAGES_PATH')] + [p/'packages' for p in roots], directory=True)
    bundled = user/'.cache'/'codex-runtimes'/'codex-primary-runtime'/'dependencies'/'python'/'python.exe'
    candidates = [os.environ.get('KOKORO_PYTHON'), APP_ROOT/'tools'/'python312'/'python.exe', bundled, shutil.which('python3.12')]
    candidates += glob.glob(str(user/'AppData'/'Local'/'Programs'/'Python'/'Python312*'/'python.exe'))
    if sys.version_info[:2] == (3, 12):
        candidates.append(sys.executable)
    runtime = _first(candidates)
    return {'available': bool(model and voices and packages and runtime), 'model': model, 'voices': voices,
            'packages': packages, 'python': runtime,
            'license_directories': [str(p/'licenses') for p in roots if (p/'licenses').is_dir()],
            'voice_type': 'Kokoro v1.0 stock synthetic voices; no cloud voice API', 'downloads_required': False}


def _tools():
    try:
        from .local_render import discover_tools
    except ImportError:
        from local_render import discover_tools
    return discover_tools()


def _call(args):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    subprocess.run([str(a) for a in args], check=True, creationflags=flags)


def _new_output(value, overwrite=False):
    path = Path(value).expanduser().resolve()
    if path.exists() and not overwrite:
        raise ValueError(f'Output already exists: {path}. Choose a new name or pass --overwrite.')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _stamp(seconds):
    hours, remainder = divmod(round(seconds*1000), 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'


def _chunks(text, max_chars=380):
    """Bounded sentence-sized pieces support long narrations without huge arrays."""
    for paragraph in re.split(r'\n\s*\n', text.strip()):
        for sentence in re.split(r'(?<=[.!?])\s+', re.sub(r'\s+', ' ', paragraph.strip())):
            while len(sentence) > max_chars:
                split = sentence.rfind(' ', 0, max_chars)
                if split < 1:
                    split = max_chars
                yield sentence[:split].strip()
                sentence = sentence[split:].strip()
            if sentence:
                yield sentence


def _kokoro_worker(args):
    assets = discover_audio()
    if not assets['available']:
        raise RuntimeError('Kokoro assets or its compatible runtime were not found; run discover')
    sys.path.insert(0, assets['packages'])
    os.environ.setdefault('OMP_NUM_THREADS', '3')
    import numpy as np
    import onnxruntime as ort
    from kokoro_onnx import Kokoro
    opts = ort.SessionOptions(); opts.intra_op_num_threads = 3; opts.inter_op_num_threads = 1
    session = ort.InferenceSession(assets['model'], sess_options=opts, providers=['CPUExecutionProvider'])
    model = Kokoro.from_session(session, assets['voices'])
    available = sorted(model.voices.files if hasattr(model.voices, 'files') else model.voices.keys())
    if args.command == 'voices':
        print(json.dumps({'voices': available, 'description': 'Installed stock synthetic voices'}, indent=2))
        return
    if args.voice not in available:
        raise ValueError(f'Unknown stock voice {args.voice}; run voices for installed names')
    text = Path(args.text_file).read_text(encoding='utf-8-sig')
    if not text.strip() or len(text) > 200000:
        raise ValueError('Narration must contain 1 to 200000 characters; use separate scenes for larger scripts')
    if not .5 <= args.speed <= 2 or not 0 <= args.pause <= 2:
        raise ValueError('Speed must be 0.5 to 2, and pause 0 to 2 seconds')
    output = _new_output(args.output, args.overwrite)
    pieces = list(_chunks(text)); segments = []; rate = 24000; at = 0; peak = 0.0; clipped = 0
    silence = np.zeros(round(rate*args.pause), dtype='<i2')
    with wave.open(str(output), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(rate)
        for index, sentence in enumerate(pieces):
            data, sample_rate = model.create(sentence, voice=args.voice, speed=args.speed, lang=args.lang)
            if sample_rate != rate:
                raise RuntimeError(f'Unexpected Kokoro sample rate: {sample_rate}')
            data = np.asarray(data, dtype=np.float32).reshape(-1)
            active = np.flatnonzero(np.abs(data) > .002)
            if len(active):
                data = data[max(0, active[0]-720):min(len(data), active[-1]+2160)]
            edge = min(100, len(data)//8)
            if edge:
                data[:edge] *= np.linspace(0,1,edge); data[-edge:] *= np.linspace(1,0,edge)
            maximum = float(np.max(np.abs(data))) if len(data) else 0
            if maximum > .95:
                data *= .95/maximum
            peak = max(peak, float(np.max(np.abs(data))) if len(data) else 0)
            clipped += int(np.sum(np.abs(data) >= 1))
            stream.writeframesraw((data*32767).astype('<i2').tobytes())
            end = at + len(data)/rate
            segments.append({'start': round(at,3), 'end': round(end,3), 'text': sentence, 'voice': args.voice})
            at = end
            if index != len(pieces)-1:
                stream.writeframesraw(silence.tobytes()); at += args.pause
            print(f'TTS_SEGMENT {index+1}/{len(pieces)} seconds={end:.2f}', flush=True)
    metadata = {'engine': 'Kokoro v1.0 ONNX', 'voice': args.voice, 'synthetic': True, 'language': args.lang,
                'duration_seconds': round(at,3), 'sample_rate': rate, 'channels': 1,
                'peak_dbfs': round(20*math.log10(peak),2) if peak else None, 'clipped_samples': clipped,
                'segments': segments, 'qa': 'Numerical clipping and timing checks; pronunciation/performance need listening review.',
                'model_file': assets['model'], 'voices_file': assets['voices'], 'model_license': 'Apache-2.0', 'wrapper_license': 'MIT'}
    output.with_suffix('.audio.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
    output.with_suffix('.srt').write_text('\n\n'.join(f"{i}\n{_stamp(s['start'])} --> {_stamp(s['end'])}\n{s['text']}" for i,s in enumerate(segments,1))+'\n', encoding='utf-8')
    print(json.dumps({'output': str(output), 'duration_seconds': round(at,3), 'voice': args.voice}, indent=2))


def synthesize(args):
    assets = discover_audio()
    if not assets['available']:
        raise RuntimeError('Local Kokoro narration is not configured. Run discover and set STUDIO_AUDIO_ROOT or KOKORO_* overrides. No assets were downloaded.')
    command = [assets['python'], Path(__file__).resolve(), 'synthesize', '--worker', '--text-file', Path(args.text_file).resolve(), '--output', Path(args.output).resolve(), '--voice', args.voice, '--speed', args.speed, '--lang', args.lang, '--pause', args.pause]
    if args.overwrite:
        command.append('--overwrite')
    _call(command)


def ambient(output, duration, seed=7, overwrite=False):
    """Stream original soft wind and sparse birdlike chirps; no recordings/music."""
    if not 0 < duration <= 7200:
        raise ValueError('Ambient duration must be greater than zero and at most 7200 seconds')
    target = _new_output(output, overwrite); rate = 24000; rng = random.Random(seed)
    smooth = [0.,0.]; phase = rng.random()*3.2
    with wave.open(str(target), 'wb') as audio:
        audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(rate)
        for chunk_start in range(0, round(duration*rate), rate):
            samples = array('h')
            for sample in range(chunk_start, min(chunk_start+rate, round(duration*rate))):
                t = sample/rate; fade = max(0,min(1,t,duration-t)); ct = (t+phase)%4.8
                chirp = .012*math.sin(2*math.pi*(1800*ct+2100*ct*ct))*math.sin(math.pi*ct/.13)**2 if ct<.13 else 0
                for channel in range(2):
                    smooth[channel] = smooth[channel]*.88+rng.uniform(-1,1)*.12
                    breeze = smooth[channel]*.006*(.7+.3*math.sin(t*.27+channel))
                    samples.append(round(32767*(breeze+chirp*(.7 if channel else 1))*fade))
            if sys.byteorder != 'little':
                samples.byteswap()
            audio.writeframesraw(samples.tobytes())
    target.with_suffix('.audio.json').write_text(json.dumps({'description': 'Original procedural wind and birdlike chirps. No music or external recordings.', 'duration_seconds': duration, 'seed': seed, 'sample_rate': rate, 'channels': 2}, indent=2), encoding='utf-8')
    return target


def encode(args):
    ffmpeg = _tools()['ffmpeg']
    if not ffmpeg:
        raise RuntimeError('FFmpeg was not found')
    if not 1 <= args.input_fps <= 120 or not 1 <= args.fps <= 120:
        raise ValueError('Frame rates must be between 1 and 120')
    output = _new_output(args.output, args.overwrite)
    command = [ffmpeg, '-y' if args.overwrite else '-n', '-framerate', args.input_fps, '-start_number', args.start_number, '-i', Path(args.frames).resolve()]
    if args.audio:
        command += ['-i', Path(args.audio).resolve(), '-map', '0:v:0', '-map', '1:a:0']
    command += ['-r', args.fps, '-vf', 'scale=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p']
    if args.audio:
        command += ['-af', 'apad', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-shortest']
    if args.duration is not None:
        if not 0 < args.duration <= 7200:
            raise ValueError('Duration must be greater than zero and at most 7200 seconds')
        command += ['-t', args.duration]
    command += ['-movflags', '+faststart', output]
    _call(command)
    return output


def concat(args):
    ffmpeg = _tools()['ffmpeg']
    if not ffmpeg:
        raise RuntimeError('FFmpeg was not found')
    paths = json.loads(Path(args.clips_list).read_text(encoding='utf-8-sig'))
    if not isinstance(paths,list) or not 1 <= len(paths) <= 2000 or not all(isinstance(p,str) for p in paths):
        raise ValueError('clips-list must be a JSON array of 1 to 2000 video file paths')
    source_base = Path(args.clips_list).resolve().parent; output = _new_output(args.output,args.overwrite); entries = []
    for item in paths:
        path = Path(item)
        if not path.is_absolute():
            path = source_base/path
        path = path.resolve()
        if not path.is_file() or path == output or any(c in str(path) for c in '\r\n'):
            raise ValueError(f'Invalid input clip: {path}')
        escaped = path.as_posix().replace("'", "'\\''")
        entries.append("file '"+escaped+"'")
    listing = output.with_suffix('.concat.txt')
    listing.write_text('\n'.join(entries)+'\n',encoding='utf-8')
    _call([ffmpeg,'-y' if args.overwrite else '-n','-f','concat','-safe','0','-i',listing,'-c','copy','-movflags','+faststart',output])
    return output


def verify(video):
    ffmpeg = _tools()['ffmpeg']
    if not ffmpeg:
        raise RuntimeError('FFmpeg was not found')
    path = Path(video).resolve()
    if not path.is_file():
        raise ValueError('Video file does not exist')
    probe = Path(ffmpeg).with_name('ffprobe.exe' if os.name=='nt' else 'ffprobe')
    metadata = {'video':str(path),'full_decode_verified':False}
    flags = subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    if probe.exists():
        result = subprocess.run([str(probe),'-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,check=True,creationflags=flags)
        metadata['probe'] = json.loads(result.stdout)
    _call([ffmpeg,'-v','error','-i',path,'-f','null','-'])
    metadata['full_decode_verified'] = True
    path.with_suffix('.verification.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    commands.add_parser('discover',help='Print detected tools and existing voice assets')
    voices = commands.add_parser('voices',help='List installed stock Kokoro voices')
    voices.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    speech = commands.add_parser('synthesize',help='Create local narration WAV, timed SRT and JSON')
    speech.add_argument('--text-file',required=True); speech.add_argument('--output',required=True)
    speech.add_argument('--voice',default='af_heart'); speech.add_argument('--speed',type=float,default=1.)
    speech.add_argument('--lang',default='en-us'); speech.add_argument('--pause',type=float,default=.18)
    speech.add_argument('--overwrite',action='store_true'); speech.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    wind = commands.add_parser('ambient',help='Generate original quiet wind and birdlike chirps')
    wind.add_argument('--output',required=True); wind.add_argument('--duration',type=float,required=True)
    wind.add_argument('--seed',type=int,default=7); wind.add_argument('--overwrite',action='store_true')
    movie = commands.add_parser('encode',help='Encode Blender frames as H.264 MP4')
    movie.add_argument('--frames',required=True,help='Example: shot/frames/frame_%%05d.png')
    movie.add_argument('--audio'); movie.add_argument('--output',required=True)
    movie.add_argument('--input-fps',type=float,default=24); movie.add_argument('--fps',type=float,default=24)
    movie.add_argument('--start-number',type=int,default=1); movie.add_argument('--duration',type=float)
    movie.add_argument('--overwrite',action='store_true')
    join = commands.add_parser('concat',help='Join matching clips listed in a JSON array')
    join.add_argument('--clips-list',required=True); join.add_argument('--output',required=True); join.add_argument('--overwrite',action='store_true')
    check = commands.add_parser('verify',help='Probe and fully decode an exported film')
    check.add_argument('--video',required=True)
    args = parser.parse_args()
    if args.command=='discover':
        print(json.dumps({'tools':_tools(),'audio':discover_audio()},indent=2));return
    if args.command in ('voices','synthesize'):
        if args.worker:
            _kokoro_worker(args)
        elif args.command=='synthesize':
            synthesize(args)
        else:
            assets=discover_audio()
            if not assets['available']:raise RuntimeError('Kokoro is not configured; run discover')
            _call([assets['python'],Path(__file__).resolve(),'voices','--worker'])
        return
    if args.command=='ambient':print(ambient(args.output,args.duration,args.seed,args.overwrite))
    elif args.command=='encode':print(encode(args))
    elif args.command=='concat':print(concat(args))
    elif args.command=='verify':print(json.dumps(verify(args.video),indent=2))


if __name__=='__main__':
    try:
        main()
    except (ValueError,RuntimeError,OSError,subprocess.CalledProcessError) as error:
        print(f'Production tool error: {error}',file=sys.stderr)
        raise SystemExit(1)
