#!/usr/bin/env python3
"""Resumable creator video production. Creative stages belong to the calling agent."""
import argparse
from contextlib import contextmanager
from datetime import datetime,timezone
import fcntl,hashlib,json,os,re,shutil,subprocess,sys,uuid
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit

ROOT=Path(__file__).resolve().parents[1]
LIVE_MIN_MATCH=0.85
STAGES=['script','approval','voice','voice_qa','avatar','scene','render','technical_qa','visual_qa','delivery']
def now():return datetime.now(timezone.utc).isoformat()
def read(p):return json.loads(Path(p).read_text())
def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700);tmp=p.with_name(p.name+'.tmp-'+uuid.uuid4().hex);tmp.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n');tmp.chmod(0o600);os.replace(tmp,p)
def sha(p):return hashlib.file_digest(Path(p).open('rb'),'sha256').hexdigest()
def canon(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def inside(p,base=ROOT):
    path=Path(p);result=(base/path).resolve()
    if path.is_absolute() or '..' in path.parts or not result.is_relative_to(base.resolve()):raise ValueError('Путь должен оставаться внутри проекта')
    return result
def profile():return read(ROOT/'profile.local.json')
def edition():
    # Редакция записана установщиком в .video-skill-project.json; нет файла или он нечитаем - считаем бесплатной.
    try:return read(ROOT/'.video-skill-project.json').get('edition','free')
    except (OSError,ValueError):return 'free'
FREE_MAX_SECONDS=60.0
def job(identity):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,79}',identity):raise ValueError('Некорректный id выпуска: допустимы латиница, цифры и дефис')
    return ROOT/'episodes'/identity
@contextmanager
def locked(folder):
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/'.lock').open('a+') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Этот выпуск сейчас занят другой командой: подождите')
        try:yield
        finally:fcntl.flock(f,fcntl.LOCK_UN)
def event(ep,kind,**values):
    with (ep/'events.jsonl').open('a') as f:f.write(json.dumps({'at':now(),'event':kind,**values},ensure_ascii=False)+'\n')
def state(ep):return read(ep/'state.json')
def current(ep,stage,variant=None,seen=None):
    m=state(ep);record=m['stages'].get(stage if not variant else stage+':'+variant)
    if not record or record.get('status')!='complete':raise ValueError(stage+': сначала завершите этот этап')
    seen=set(seen or []);name=stage if not variant else stage+':'+variant
    if name in seen:raise ValueError('Цикл зависимостей в состоянии выпуска')
    seen.add(name)
    for dependency,fingerprint in record.get('dependencies',{}).items():
        upstream=current(ep,*dependency.split(':',1),seen=seen)
        if upstream['fingerprint']!=fingerprint:raise ValueError('Предыдущий этап изменился: пересоберите зависимый результат')
    for item in record['outputs']:
        p=inside(item['path'],ep)
        if not p.is_file() or sha(p)!=item['sha256']:raise ValueError('Результат этапа устарел или изменён, пересоберите его: '+item['path'])
    return record
def complete(ep,name,inputs,paths,extra=None):
    m=state(ep);fingerprint=canon(inputs);old=m['stages'].get(name)
    if old:
        if old['fingerprint']!=fingerprint:raise ValueError('Входы завершённого этапа изменились: используйте revise')
        current(ep,*name.split(':',1));return old
    for p in paths:
        if not Path(p).resolve().is_relative_to(ep.resolve()) or not Path(p).is_file():raise ValueError('Результат должен лежать внутри папки выпуска')
    stage,_,key=name.partition(':')
    deps={'script':[],'approval':['script'],'voice':['approval'],'voice_qa':['voice'],'avatar':['voice_qa'],'scene':['voice_qa'],'render':['scene'],'technical_qa':['render','voice_qa'],'visual_qa':['technical_qa'],'delivery':['visual_qa','render']}[stage]
    if stage=='render' and m['format']=='avatar':deps+=['avatar']
    dependencies={}
    for dep in deps:
        dependency=dep+':'+key if key else dep
        dependencies[dependency]=current(ep,dep,key or None)['fingerprint']
    r={'status':'complete','fingerprint':fingerprint,'inputs':inputs,'dependencies':dependencies,'outputs':[{'path':str(Path(p).relative_to(ep)),'sha256':sha(p)} for p in paths],'at':now(),**(extra or {})}
    m['stages'][name]=r;write(ep/'state.json',m);event(ep,'stage_complete',stage=name,fingerprint=fingerprint);return r
def variant(ep,key):
    if not re.fullmatch('[a-z][a-z0-9_-]{0,39}',key):raise ValueError('Некорректный ключ варианта')
    current(ep,'script',key);return read(ep/'variants'/key/'script.json')
def require_approval(ep,key):
    current(ep,'approval',key);v=variant(ep,key);approval=read(ep/'variants'/key/'approval.json')
    if approval['variant_sha256']!=canon(v):raise ValueError('Согласование этого варианта устарело: текст изменился')
    return v
def run(ep,args,cwd=None):
    folder=ep/'logs';folder.mkdir(exist_ok=True)
    with (folder/'commands.log').open('a') as f:
        p=subprocess.run([str(x) for x in args],stdout=f,stderr=subprocess.STDOUT,cwd=cwd)
    if p.returncode:raise ValueError('Команда не выполнилась: смотрите logs/commands.log в папке выпуска')
def source_key(url):
    p=urlsplit(url)
    if p.scheme not in ['https','http'] or not p.hostname:raise ValueError('Нужна ссылка на источник')
    path=re.sub(r'^/(p|reels)/','/reel/',p.path).rstrip('/')
    return urlunsplit(('https',p.hostname.lower().removeprefix('www.'),path,'',''))
def initialize(a):
    ep=job(a.id);p=profile()
    if ep.exists() and not (ep/'state.json').exists() and any(ep.iterdir()):raise ValueError('Папка выпуска не пуста и без состояния: проверьте её перед резервом источника')
    if edition()=='free' and p.get('format')!='cartoon':raise ValueError('В бесплатной версии доступен только мультик (cartoon). Аватар входит в платную версию.')
    if p.get('ready') is not True:raise ValueError('Сначала завершите настройку: автор, голос, стиль и герой')
    q=Path(p['shared_queue']).expanduser().resolve();q.mkdir(parents=True,exist_ok=True)
    source=source_key(a.source) if a.source else 'idea:'+sha(inside(a.idea))
    with locked(q):
        index=read(q/'sources.json') if (q/'sources.json').exists() else {'sources':{},'families':{}}
        previous=index['sources'].get(source)
        same=previous and previous['project']==str(ROOT) and previous['episode']==a.id
        if (previous and not same) or (a.family in index['families'] and index['families'][a.family]!=source):raise ValueError('Этот источник или семейство уже заняты: продолжайте исходный выпуск')
        if (ep/'state.json').exists():
            m=state(ep)
            if m['source']!=source or m['family']!=a.family:raise ValueError('Выпуск с таким id уже создан для другого источника: продолжайте его')
        else:
            ep.mkdir(parents=True,exist_ok=True);write(ep/'profile.snapshot.json',p)
            write(ep/'state.json',{'schema':1,'id':a.id,'revision':1,'format':p['format'],'source':source,'family':a.family,'created_at':now(),'stages':{}})
            if a.idea:shutil.copy2(inside(a.idea),ep/'author-source.txt')
            event(ep,'reserved',source=source,family=a.family)
        index['sources'][source]={'project':str(ROOT),'episode':a.id,'format':p['format'],'at':now()};index['families'][a.family]=source;write(q/'sources.json',index)
    print(ep)
def register_script(ep,path):
    src=inside(path);data=read(src);p=read(ep/'profile.snapshot.json');rows=data.get('variants',[])
    if not rows or len({v['id'] for v in rows})!=len(rows):raise ValueError('Нужны уникальные точные тексты по площадкам')
    for v in rows:
        v['platforms']=[x.strip().lower() for x in v.get('platforms',[])]
        if not v.get('text','').strip() or v['language'] not in p['languages'] or not v.get('platforms') or not set(v['platforms'])<=set(p['platforms']):raise ValueError('Сценарий пуст или выходит за выбранные языки и площадки')
        if not re.fullmatch('[a-z][a-z0-9_-]{0,39}',v['id']):raise ValueError('Некорректный id варианта')
        if '[' in v['text'] or ']' in v['text']:raise ValueError('Пометки для монтажа в квадратных скобках не должны попадать в текст для озвучки')
    m=state(ep)
    scope=m.get('script_revision_scope')
    if scope:
        original=scope['original_variants'];incoming={v['id']:canon(v) for v in rows}
        if set(original)!=set(incoming) or any(incoming[k]!=h for k,h in original.items() if k!=scope['target']):raise ValueError('Правка сценария может менять только выбранный вариант')
    if m['stages'].get('script'):
        if canon(data)!=canon(read(ep/'script.json')):raise ValueError('Чтобы изменить сценарий, используйте revise со слоем script')
        return
    write(ep/'script.json',data);complete(ep,'script',{'script_sha256':sha(ep/'script.json')},[ep/'script.json'])
    for v in rows:
        name='script:'+v['id']
        if name in state(ep)['stages']:
            if canon(variant(ep,v['id']))!=canon(v):raise ValueError('Неправленый вариант изменился')
            continue
        dest=ep/'variants'/v['id']/'script.json';write(dest,v);complete(ep,name,{'variant_sha256':canon(v)},[dest])
def mandate_ok(e):
    # Standing author mandate for the autonomous cycle: one real human decision in strategy/mandate.json covers script approval.
    if edition()=='free':return False
    p=ROOT/'strategy'/'mandate.json'
    if e.get('decision')!='approve_script_by_mandate' or not p.exists():return False
    m=read(p)
    return m.get('role')=='user' and m.get('decision')=='autonomous_cycle' and bool(m.get('quote')) and bool(m.get('message_ref')) and e.get('mandate_sha256')==sha(p) and e.get('quote')==m.get('quote') and e.get('message_ref')==m.get('message_ref')
def approve(ep,evidence):
    current(ep,'script');e=read(inside(evidence))
    if e.get('role')!='user' or not (e.get('decision')=='approve_script' or mandate_ok(e)) or e.get('script_sha256')!=sha(ep/'script.json') or not e.get('quote') or not e.get('message_ref'):raise ValueError('Нужно реальное решение автора и точный SHA сценария: проверка не может согласовать текст за автора')
    if state(ep)['stages'].get('approval'):
        current(ep,'approval');event(ep,'script_approval_reaffirmed',evidence=e);return
    write(ep/'approval.json',e);complete(ep,'approval',{'script':sha(ep/'script.json'),'evidence':e},[ep/'approval.json'])
    for v in read(ep/'script.json')['variants']:
        key=v['id']
        if 'approval:'+key in state(ep)['stages']:continue
        dest=ep/'variants'/key/'approval.json';write(dest,{**e,'variant_sha256':canon(v)})
        complete(ep,'approval:'+key,{'script':canon(v),'evidence':e},[dest])
    m=state(ep);m.pop('script_revision_scope',None);write(ep/'state.json',m)
def attach(ep,stage,key,file,report=None):
    v=require_approval(ep,key);target=ep/'variants'/key;target.mkdir(parents=True,exist_ok=True);src=inside(file)
    if stage=='voice':
        if edition()=='free':
            length=float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(src)],capture_output=True,text=True).stdout.strip() or 0)
            if length>FREE_MAX_SECONDS+0.5:raise ValueError(f'Запись длиннее {FREE_MAX_SECONDS:g} секунд (лимит бесплатной версии). Сократите паузы через live_voice.py prepare или запишите короче.')
        inputs={'script':canon(v),'profile':sha(ep/'profile.snapshot.json')}
    elif stage=='voice_qa':
        current(ep,'voice',key);d=read(src)
        if d.get('passed') is not True or d.get('audio_sha256')!=sha(target/'voice.bin') or d.get('script_sha256')!=canon(v) or d.get('unprompted_asr') is not True or not d.get('raw_asr_file') or not d.get('words') or not d.get('timing_source'):raise ValueError('Нужны настоящая расшифровка без подсказки и свежие тайминги слов')
        if d.get('listening_status') not in ['listened','not_available']:raise ValueError('Укажите, прослушана ли запись')
        configured=read(ep/'profile.snapshot.json')['voices'][v['language']].get('mode','tts')
        if d.get('mode','tts')!=configured:raise ValueError('Режим проверки голоса не совпал с режимом голоса в профиле')
        if configured=='live':
            ratio=d.get('match_ratio')
            if type(ratio) not in (int,float) or ratio<LIVE_MIN_MATCH or not isinstance(d.get('asr_discrepancies'),list) or not d.get('passed_basis') or 'audio_post_processing' not in d:raise ValueError('Для живой записи нужны доля совпавших слов не ниже порога, список расхождений, основание приёмки и сведения об обработке звука')
            if d['audio_post_processing'] and (d['audio_post_processing'].get('speech_cut') or d['audio_post_processing'].get('tempo_changed')):raise ValueError('В живой записи разрешено только сокращение пауз и обрезка краёв: речь вырезать и ускорять нельзя')
        raw=inside(d['raw_asr_file'])
        if not raw.is_file():raise ValueError('Файл расшифровки не найден')
        raw_data=read(raw)
        if raw_data.get('initial_prompt') is not None or raw_data.get('condition_on_previous_text') is not False or raw_data.get('vad_filter') is not False or not raw_data.get('segments'):raise ValueError('Расшифровка должна быть без подсказки и содержать реальные сегменты')
        if raw_data.get('audio_sha256')!=sha(target/'voice.bin'):raise ValueError('Расшифровка сделана для другой записи')
        d['raw_asr_sha256']=sha(raw)
        inputs={'audio':sha(target/'voice.bin'),'script':canon(v)}
    elif stage=='avatar':
        if edition()=='free':raise ValueError('В бесплатной версии аватар недоступен')
        current(ep,'voice_qa',key);r=read(inside(report)) if report else {}
        if r.get('audio_sha256')!=sha(target/'voice.bin') or not r.get('provider_job_id') or r.get('completed') is not True:raise ValueError('Завершённая задача аватара должна относиться к этой записи')
        inputs={'voice':sha(target/'voice.bin'),'receipt':r}
    elif stage=='scene':
        current(ep,'voice_qa',key);plan=read(src)
        if not plan.get('beats') or not all(b.get('action') and b.get('result') for b in plan['beats']):raise ValueError('У каждой сцены нужны конкретное действие и видимый результат')
        source=inside(plan['scene_source'],ep)
        if not (source/'Episode.tsx').is_file():raise ValueError('Сцена должна экспортировать Scene из Episode.tsx')
        for asset in plan.get('assets',[]):
            if not inside(asset['source']).is_file():raise ValueError('Заявленный медиафайл не найден')
            inside(asset['name'],target/'media-snapshot')
        inputs={'voice':sha(target/'voice.bin'),'timing':sha(target/'voice_qa.json')}
    elif stage=='render':
        current(ep,'scene',key)
        if state(ep)['format']=='avatar':current(ep,'avatar',key)
        inputs={'voice':sha(target/'voice.bin'),'scene':sha(target/'scene.json')}
    elif stage in ['technical_qa','visual_qa']:
        current(ep,'render',key);d=read(src)
        if d.get('sha256')!=sha(target/'render.mp4') or d.get('passed') is not True:raise ValueError('Проверка должна быть пройдена для этого точного MP4')
        if stage=='visual_qa':
            current(ep,'technical_qa',key)
            if not d.get('independent') or not d.get('reviewer') or not d.get('scene_author') or not d.get('reviewed_frames') or d.get('blocking_findings') or d.get('reviewer')==d.get('scene_author'):raise ValueError('Нужен независимый просмотр готового файла')
        inputs={'video':sha(target/'render.mp4')}
    else:raise ValueError('Этот этап нельзя прикрепить')
    extension={'voice':'.bin','voice_qa':'.json','avatar':'.mp4','scene':'.json','render':'.mp4','technical_qa':'.json','visual_qa':'.json'}[stage]
    dest=target/(stage+extension)
    if dest.exists() and sha(dest)!=sha(src):raise ValueError('Результат этапа не перезаписывается: используйте revise')
    if src!=dest:shutil.copy2(src,dest)
    outputs=[dest]
    if stage=='voice_qa':
        snapshot=target/'raw-asr.json'
        if snapshot!=raw:shutil.copy2(raw,snapshot)
        outputs.append(snapshot)
    if stage=='scene':
        source=inside(plan['scene_source'],ep)
        if not (source/'Episode.tsx').is_file():raise ValueError('Сцена должна экспортировать Scene из Episode.tsx')
        outputs.extend(p for p in source.rglob('*') if p.is_file())
        assets=[]
        for asset in plan.get('assets',[]):
            path=inside(asset['source'])
            if not path.is_file():raise ValueError('Заявленный медиафайл не найден')
            snap=inside(asset['name'],target/'media-snapshot');assets.append((path,snap))
        for path,snap in assets:
            # Project assets are copied into an episode snapshot before scene registration.
            snap.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,snap);outputs.append(snap)
        inputs['source_files']=[{'path':str(p.relative_to(ep)),'sha256':sha(p)} for p in outputs]
    complete(ep,stage+':'+key,inputs,outputs)
def revise(ep,layer,key=None):
    m=state(ep);archive=ep/'revisions'/f'r{m["revision"]:03d}'
    if archive.exists():raise ValueError('Архив этой ревизии уже существует')
    if key and key not in {v['id'] for v in read(ep/'script.json')['variants']}:raise ValueError('Неизвестный вариант для правки')
    costs=read(ep/'provider-costs.json') if (ep/'provider-costs.json').exists() else {'attempts':[]}
    if any(x.get('status') in ['uncertain','job_submitted'] for x in costs['attempts']):raise ValueError('Сначала выясните неопределённый результат у сервиса, потом правьте')
    for receipt in ep.rglob('*.json'):
        if (receipt.name=='delivery.json' or receipt.name.startswith('description-')) and read(receipt).get('status')=='uncertain':raise ValueError('Сначала выясните неопределённую доставку, потом архивируйте ревизию')
    archive.mkdir(parents=True);shutil.copy2(ep/'state.json',archive/'state.json')
    index={'script':0,'voice':2,'visual':5}[layer]
    if key:
        original={v['id']:canon(v) for v in read(ep/'script.json')['variants']}
        if key not in original:raise ValueError('Неизвестный вариант для правки')
        archive_variant=archive/'variants'/key;archive_variant.mkdir(parents=True)
        keep={'visual':{'voice.bin','voice_qa.json','raw-asr.json','avatar.mp4','script.json','approval.json'},'voice':{'script.json','approval.json'},'script':set()}[layer]
        target=ep/'variants'/key
        for f in list(target.iterdir()):
            if f.name not in keep:shutil.move(f,archive_variant/f.name)
        m['stages']={k:v for k,v in m['stages'].items() if not (k.endswith(':'+key) and STAGES.index(k.split(':')[0])>=index)}
        if layer=='script':
            for name in ['script.json','approval.json']:
                if (ep/name).exists():shutil.move(ep/name,archive/name)
            m['stages'].pop('script',None);m['stages'].pop('approval',None);m['script_revision_scope']={'target':key,'original_variants':original}
        m['revision']+=1;write(ep/'state.json',m);event(ep,'revision',layer=layer,variant=key);return
    for name in ['script.json','approval.json','variants']:
        src=ep/name
        if not src.exists():continue
        if layer=='script':shutil.move(src,archive/name)
        elif name=='variants':
            for d in src.iterdir():
                if not d.is_dir():continue
                out=archive/'variants'/d.name;out.mkdir(parents=True)
                for f in d.iterdir():
                    keep=['script.json','approval.json'] if layer=='voice' else ['script.json','approval.json','voice.bin','voice_qa.json','raw-asr.json','avatar.mp4']
                    if f.name not in keep:shutil.move(f,out/f.name)
    m['stages']={k:v for k,v in m['stages'].items() if STAGES.index(k.split(':')[0])<index};m['revision']+=1;write(ep/'state.json',m);event(ep,'revision',layer=layer)
def technical(ep,key):
    current(ep,'render',key);current(ep,'voice_qa',key);p=ep/'variants'/key;final=p/'render.mp4'
    raw=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(final)],capture_output=True,text=True,check=True);d=json.loads(raw.stdout)
    video=next(s for s in d['streams'] if s['codec_type']=='video');audio=next(s for s in d['streams'] if s['codec_type']=='audio');duration=float(d['format']['duration'])
    errors=[]
    if video['width']!=1080 or video['height']!=1920 or video['codec_name']!='h264':errors.append('Expected 1080x1920 H264')
    if audio['codec_name']!='aac' or int(audio['sample_rate'])!=48000:errors.append('Expected AAC 48kHz')
    if abs(float(video.get('duration',duration))-float(audio.get('duration',duration)))>.15:errors.append('Audio/video duration mismatch')
    probe=subprocess.run(['ffmpeg','-v','error','-i',str(final),'-f','null','-'],capture_output=True,text=True)
    if probe.returncode or probe.stderr.strip():errors.append('Decode error')
    def pcm(path):return subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vn','-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True).stdout
    import numpy as np
    a=np.frombuffer(pcm(p/'voice.bin'),dtype='<f4');b=np.frombuffer(pcm(final),dtype='<f4');n=min(len(a),len(b))
    correlation=float(np.corrcoef(a[:n],b[:n])[0,1]) if n else 0
    if correlation<.95 or abs(len(a)-len(b))/16000>.15:errors.append('Final audio differs from verified narration; inspect mix and repeat final ASR')
    report={'sha256':sha(final),'passed':not errors,'errors':errors,'duration':duration,'audio_correlation':correlation,'streams':d['streams'],'decode_checked':True,'scope':'Technical media and narration timing. Motion, source fidelity and listening require independent review.'}
    write(p/'technical.report.json',report)
    if errors:raise ValueError('Техническая проверка видео не пройдена: '+', '.join(errors))
    attach(ep,'technical_qa',key,str((p/'technical.report.json').relative_to(ROOT)))
def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('init');p.add_argument('id');g=p.add_mutually_exclusive_group(required=True);g.add_argument('--source');g.add_argument('--idea');p.add_argument('--family',required=True)
    p=sub.add_parser('status');p.add_argument('id')
    for cmd in ['script','approve']:
        p=sub.add_parser(cmd);p.add_argument('id');p.add_argument('file')
    p=sub.add_parser('attach');p.add_argument('id');p.add_argument('stage',choices=STAGES);p.add_argument('variant');p.add_argument('file');p.add_argument('--report')
    p=sub.add_parser('revise');p.add_argument('id');p.add_argument('--layer',choices=['script','voice','visual'],required=True);p.add_argument('--variant')
    p=sub.add_parser('technical');p.add_argument('id');p.add_argument('variant')
    a=ap.parse_args()
    if a.cmd=='init':initialize(a);return
    ep=job(a.id)
    if not (ep/'state.json').is_file():raise ValueError('Такого выпуска нет: сначала выполните init')
    with locked(ep):
        if a.cmd=='status':print(json.dumps(state(ep),ensure_ascii=False,indent=2))
        elif a.cmd=='script':register_script(ep,a.file)
        elif a.cmd=='approve':approve(ep,a.file)
        elif a.cmd=='attach':attach(ep,a.stage,a.variant,a.file,a.report)
        elif a.cmd=='revise':revise(ep,a.layer,a.variant)
        elif a.cmd=='technical':technical(ep,a.variant)
if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError,StopIteration) as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(2)
