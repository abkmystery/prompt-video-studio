from __future__ import annotations
import json
import re
import secrets
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from .common import atomic_json, safe_error

ACTIVE = {'queued','running'}
DURATIONS = {30,60,180,300,600,900,1200}
STYLES = {'anime','cinematic','3d','storybook'}


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_captions(path, duration):
    content = path.read_text(encoding='utf-8-sig').replace('\r\n','\n').strip()
    blocks = re.split(r'\n\s*\n',content)
    previous = 0.0
    if not content:
        raise RuntimeError('The caption file is empty. Resume to finish the captions.')
    pattern = r'(\d{2,}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2,}):(\d{2}):(\d{2}),(\d{3})'
    for block in blocks:
        lines = block.splitlines()
        match = re.fullmatch(pattern,lines[1]) if len(lines) >= 3 else None
        if not match or not lines[0].isdigit() or not ''.join(lines[2:]).strip():
            raise RuntimeError('The caption file contains an invalid subtitle. Resume to repair it.')
        values = list(map(int,match.groups()))
        if any(values[i] >= 60 for i in [1,2,5,6]):
            raise RuntimeError('A caption timestamp is invalid.')
        start = values[0]*3600+values[1]*60+values[2]+values[3]/1000
        end = values[4]*3600+values[5]*60+values[6]+values[7]/1000
        if start < previous-.001 or end <= start or end > duration+.1:
            raise RuntimeError('Captions overlap or extend outside the film. Resume to repair their timing.')
        previous = end
    return len(blocks)


def verify_video(directory, request, tools):
    ffmpeg = tools.get('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('FFmpeg is needed to verify the output. Resume after installing it.')
    probe = Path(ffmpeg).with_name('ffprobe.exe' if Path(ffmpeg).suffix.lower() == '.exe' else 'ffprobe')
    if not probe.is_file():
        raise RuntimeError('ffprobe was not found next to FFmpeg.')
    video = directory/'video.mp4'
    if not video.is_file() or video.stat().st_size < 100:
        raise RuntimeError('Production paused before the finished video was created. Resume to continue from saved files.')
    flags = getattr(subprocess,'CREATE_NO_WINDOW',0)
    result = subprocess.run([str(probe),'-v','error','-show_format','-show_streams','-of','json',str(video)],
                            capture_output=True,text=True,timeout=90,creationflags=flags)
    if result.returncode:
        raise RuntimeError('The output video could not be read by ffprobe. Resume to repair the export.')
    data = json.loads(result.stdout)
    streams = data.get('streams',[])
    visual = next((s for s in streams if s.get('codec_type') == 'video'),None)
    sound = next((s for s in streams if s.get('codec_type') == 'audio'),None)
    duration = float(data['format']['duration'])
    if not visual:
        raise RuntimeError('The output has no video stream.')
    if abs(duration-request['duration']) > max(2,request['duration']*.02):
        raise RuntimeError(f"The export is {duration:.1f}s, but the target is {request['duration']}s. Resume to finish the requested film.")
    expected = 9/16 if request['aspect'] == '9:16' else 16/9
    if abs(visual['width']/visual['height'] - expected) > .02:
        raise RuntimeError('The export does not match the selected aspect ratio. Resume to correct it.')
    if visual.get('codec_name') != 'h264' or visual.get('pix_fmt') != 'yuv420p':
        raise RuntimeError('The export needs H.264 with yuv420p for broad player support. Resume to fix the encoding.')
    if request.get('narration') and not sound:
        raise RuntimeError('Narration was requested but the export has no audio track. Resume to finish the audio.')
    caption_count = 0
    if request.get('captions'):
        if not (directory/'captions.srt').is_file():
            raise RuntimeError('The requested caption file is missing. Resume to create it.')
        caption_count = validate_captions(directory/'captions.srt',duration)
    if request.get('provider') != 'demo':
        for file in ['script.md','story_bible.json','manifest.json','credits.md','verification.json']:
            if not (directory/file).is_file():
                raise RuntimeError(f'{file} is missing from the production package. Resume to complete the handoff.')
        report = json.loads((directory/'verification.json').read_text(encoding='utf-8-sig'))
        if report.get('verified') is not True:
            raise RuntimeError('The production agent has not finished its verification. Resume to complete the checks.')
    if not any((directory/name).is_file() for name in ['project.blend','project.zip']):
        raise RuntimeError('The editable production project is missing. Resume to package it.')
    if not (directory/'thumbnail.png').is_file():
        raise RuntimeError('The film thumbnail is missing. Resume to export it.')
    # Verify actual decode independently of the agent's completion text.
    result = subprocess.run([str(ffmpeg),'-v','error','-i',str(video),'-f','null','-'],
                            capture_output=True,timeout=max(120,int(duration)),creationflags=flags)
    if result.returncode or result.stderr.strip():
        raise RuntimeError('The export did not pass full decoding. Resume to repair the film.')
    report = {'verified':True,'checked_at':now(),'duration_seconds':duration,
              'width':visual['width'],'height':visual['height'],'video_codec':visual['codec_name'],
              'audio_present':bool(sound),'audio_codec':sound.get('codec_name') if sound else None,
              'full_decode_passed':True,'caption_count':caption_count,'bytes':video.stat().st_size,
              'note':'Container and decoding checks do not establish story quality, character continuity or human audio review.'}
    atomic_json(directory/'app-verification.json',report)
    return report


class JobManager:
    def __init__(self, root:Path, codex):
        self.root = root.resolve()
        self.outputs = self.root/'outputs'
        self.outputs.mkdir(parents=True,exist_ok=True)
        self.codex = codex
        self.lock = threading.RLock()
        self.jobs = {}
        self.events = {}
        self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix='film-production')
        for file in self.outputs.glob('*/job.json'):
            try:
                job = json.loads(file.read_text(encoding='utf-8-sig'))
                if not re.fullmatch(r'[a-f0-9]{16}',job['id']) or job['id'] != file.parent.name:
                    continue
                if job['provider'] not in {'codex','demo'}:
                    continue
                if job['status'] in ACTIVE:
                    job.update(status='interrupted',message='The app stopped. Your scenes and Codex thread are saved; resume when ready.')
                    atomic_json(file,job)
                self.jobs[job['id']] = job
            except (ValueError,KeyError,OSError):
                continue

    def capabilities(self):
        from .local_render import discover_tools
        tools = discover_tools()
        try:
            from .production_tools import discover_audio
            audio = discover_audio()
        except (ImportError,AttributeError):
            audio = {}
        return {'blender':bool(tools.get('blender')),'ffmpeg':bool(tools.get('ffmpeg')),
                'kokoro':bool(audio.get('available')),'free_tools':True}

    def _public(self, job):
        result = dict(job)
        directory = self.outputs/job['id']
        files = [('thumbnail_url','thumbnail.png'),('plan_url','manifest.json'),('captions_url','captions.srt'),
                 ('script_url','script.md'),('credits_url','credits.md')]
        for field,name in files:
            result[field] = f'/outputs/{job["id"]}/{name}' if (directory/name).is_file() else None
        result['project_url'] = next((f'/outputs/{job["id"]}/{name}' for name in ['project.zip','project.blend'] if (directory/name).is_file()),None)
        result['video_url'] = f'/outputs/{job["id"]}/video.mp4' if job['status'] == 'completed' and (directory/'video.mp4').is_file() else None
        result['can_resume'] = job['provider'] == 'codex' and job['status'] in {'failed','cancelled','interrupted'}
        approvals = self.codex.pending_approvals() if hasattr(self.codex,'pending_approvals') else []
        result['approvals'] = approvals if job['status'] == 'running' and job['provider'] == 'codex' else []
        return result

    def list(self):
        with self.lock:
            return [self._public(j) for j in sorted(self.jobs.values(),key=lambda j:j['created_at'],reverse=True)]

    def get(self,id):
        with self.lock:
            if id not in self.jobs:
                raise KeyError('Project not found.')
            return self._public(self.jobs[id])

    def _update(self,id,**fields):
        with self.lock:
            job = self.jobs[id]
            job.update(fields)
            atomic_json(self.outputs/id/'job.json',job)

    def _on_event(self,id,event):
        with self.lock:
            job = self.jobs[id]
            kind = event.get('type','message')
            if kind == 'thread':
                thread_id = event.get('thread_id') or event.get('threadId')
                if thread_id:
                    job['thread_id'] = thread_id
            text = safe_error(event.get('text') or event.get('message') or event.get('title') or '')
            if kind == 'tool':
                text = safe_error(f"{event.get('name', 'Tool')}: {event.get('status', 'working')}" + (f"\n{event['command']}" if event.get('command') else ''))
            if kind == 'message':
                match = re.search(r'STUDIO_STAGE\s+(\{[^\n]+\})',text)
                if match:
                    try:
                        marker = json.loads(match[1])
                        if marker.get('stage') in {'planning','assets','audio','animation','rendering','assembly','verification'}:
                            job['stage'] = marker['stage']
                        if type(marker.get('progress')) in {int,float}:
                            job['progress'] = min(95,max(1,int(marker['progress'])))
                        if isinstance(marker.get('message'),str):
                            job['message'] = marker['message'][:300]
                            text = job['message']
                    except (ValueError,TypeError):
                        pass
            if text:
                job.setdefault('events',[]).append({'type':kind,'text':text[:1000],'time':now()})
                job['events'] = job['events'][-80:]
            if kind == 'approval':
                job['message'] = 'Codex needs your approval for an action. Review it below.'
            atomic_json(self.outputs/id/'job.json',job)

    def submit(self,payload):
        if not isinstance(payload,dict):
            raise ValueError('Expected a JSON object.')
        provider = payload.get('provider','codex')
        if provider not in {'codex','demo'}:
            raise ValueError('This studio uses Codex and free local tools only.')
        prompt = payload.get('prompt','')
        if not isinstance(prompt,str) or len(prompt.strip()) > 12000 or (provider != 'demo' and len(prompt.strip()) < 10):
            raise ValueError('Describe the film in 10–12,000 characters.')
        duration = 8 if provider == 'demo' else payload.get('duration',60)
        if type(duration) is not int or (provider != 'demo' and duration not in DURATIONS):
            raise ValueError('Choose a supported duration from 30 seconds to 20 minutes.')
        aspect, style, quality = payload.get('aspect','16:9'),payload.get('style','anime'),payload.get('quality','draft')
        if aspect not in {'9:16','16:9'} or style not in STYLES or quality not in {'draft','standard'}:
            raise ValueError('Choose a supported format, style and quality.')
        for option in ['narration','captions','music']:
            if option in payload and type(payload[option]) is not bool:
                raise ValueError(f'{option} must be enabled or disabled.')
        if provider == 'codex' and not self.codex.status().get('connected'):
            raise ValueError('Sign in to Codex with ChatGPT before starting production.')
        if provider == 'demo':
            caps = self.capabilities()
            if not caps['blender'] or not caps['ffmpeg']:
                raise ValueError('The offline demo needs Blender and FFmpeg. Codex can help set up missing free tools.')
        with self.lock:
            if any(j['status'] in ACTIVE for j in self.jobs.values()):
                raise ValueError('A production is already active. Finish or stop it before starting another.')
            id = secrets.token_hex(8)
            directory = self.outputs/id
            directory.mkdir()
            job = {'id':id,'provider':provider,'prompt':prompt.strip(),'duration':duration,'aspect':aspect,
                   'style':style,'quality':quality,'narration':payload.get('narration',True),
                   'captions':payload.get('captions',True),'music':payload.get('music',False),
                   'title':'Local animation demo' if provider == 'demo' else prompt.strip().splitlines()[0][:80],
                   'status':'queued','stage':'planning','progress':0,'message':'Preparing production…',
                   'error':None,'created_at':now(),'thread_id':None,'events':[]}
            self.jobs[id] = job
            self.events[id] = threading.Event()
            atomic_json(directory/'request.json',{key:value for key,value in job.items() if key in {
                'provider','prompt','duration','aspect','style','quality','narration','captions','music'}})
            atomic_json(directory/'job.json',job)
            self.executor.submit(self._run,id,False)
            return self._public(job)

    def cancel(self,id):
        with self.lock:
            job = self.jobs.get(id)
            if not job:
                raise KeyError('Project not found.')
            if job['status'] in ACTIVE:
                self.events[id].set()
                self._update(id,message='Stopping Codex. Completed scenes and assets will be kept.')
            return self._public(job)

    def resume(self,id,key=None):
        with self.lock:
            if not self.get(id)['can_resume']:
                raise ValueError('This project cannot be resumed right now.')
            if any(j['status'] in ACTIVE for j in self.jobs.values()):
                raise ValueError('Stop or finish the current production before resuming another.')
            if not self.codex.status().get('connected'):
                raise ValueError('Reconnect to Codex before resuming this production.')
            self.events[id] = threading.Event()
            self._update(id,status='queued',error=None,message='Continuing from saved scenes and the same Codex thread…')
            self.executor.submit(self._run,id,True)
            return self.get(id)

    def _production_prompt(self,job,resume):
        contract = (self.root/'PRODUCTION.md').read_text(encoding='utf-8-sig')
        directory = self.outputs/job['id']
        from .local_render import discover_tools
        detected = discover_tools()
        return (contract + '\n\nHOST-DETECTED TOOLS (sandbox discovery can have different environment paths)\n' + json.dumps(detected) + '\n\nAPPLICATION PATHS\n' +
                f"Application: {self.root}\nCurrent job: {directory}\nReusable free tools: {self.root/'tools'}\nShared assets: {self.root/'assets'}\n" +
                f"Read {self.root/'TOOLS.md'} for existing Blender, FFmpeg and local speech helpers. " +
                f"Helper module: {self.root/'studio'/'production_tools.py'}. " +
                'Do not edit application code. All movie files belong in this job directory.\n' +
                ('CONTINUE the existing production. Inspect saved manifests and files first. Fix any prior validation issue: ' + str(job.get('last_error','')) + '\n' if resume else '') +
                'USER REQUEST (request.json is the authoritative configuration):\n' +
                json.dumps({k:job[k] for k in ['prompt','duration','aspect','style','quality','narration','captions','music']},ensure_ascii=False))

    def _run(self,id,resume):
        event = self.events[id]
        try:
            with self.lock:
                job = dict(self.jobs[id])
            directory = self.outputs/id
            self._update(id,status='running',message='Codex is preparing the story and tools…')
            if event.is_set():
                raise InterruptedError('Production stopped before starting.')
            if job['provider'] == 'demo':
                from .local_render import demo_plan,render
                plan = demo_plan(8)
                atomic_json(directory/'plan.json',plan)
                render(plan,directory,job['aspect'],lambda p,m:self._update(id,progress=min(95,round(p)),message=m),event)
            else:
                result = self.codex.start_production(directory,self._production_prompt(job,resume),
                    lambda update:self._on_event(id,update),event,thread_id=job.get('thread_id'))
                if result.get('thread_id'):
                    self._update(id,thread_id=result['thread_id'])
                if result.get('status') not in {'completed','complete'}:
                    raise InterruptedError(result.get('text') or 'Codex paused. Resume from the saved production state.')
            if event.is_set():
                raise InterruptedError('Production stopped. Saved scenes and assets are ready to resume.')
            self._update(id,stage='verification',progress=97,message='Checking the exported video, audio and project files…')
            from .local_render import discover_tools
            report = verify_video(directory,job,discover_tools())
            self._update(id,status='completed',progress=100,message='Your film and editable project are ready.',error=None,verification=report)
        except Exception as exc:
            message = safe_error(exc)
            self._update(id,status='cancelled' if event.is_set() else 'interrupted' if job['provider'] == 'codex' else 'failed',
                         message=message,error=message,last_error=message)

    def close(self):
        for event in self.events.values():
            event.set()
        self.executor.shutdown(wait=False,cancel_futures=True)
