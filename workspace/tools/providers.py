#!/usr/bin/env python3
"""Owner-configured provider adapters. One paid POST per explicit call; uncertain outcomes persist."""
import argparse,base64,hashlib,json,math,os,shutil,subprocess,urllib.request,urllib.error,uuid
from pathlib import Path
import studio
ROOT=studio.ROOT
def request(url,key,body=None,header='xi-api-key'):
    req=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers={header:key,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=180) as r:return json.load(r),dict(r.headers)
    except Exception:raise ValueError('Запрос к сервису не подтверждён; ключи и технические детали скрыты') from None
def ledger(ep):
    return studio.read(ep/'provider-costs.json') if (ep/'provider-costs.json').exists() else {'attempts':[]}
def committed_cost(attempt):
    value=attempt.get('actual_cost_eur')
    if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):raise ValueError('Фактическая стоимость должна быть конечным неотрицательным числом или неизвестной')
    return max(attempt['reserved_eur'],value or 0)
def reserve(ep,key,provider,payload,retry_inspection=None,kind=None):
    p=studio.read(ep/'profile.snapshot.json');v=studio.variant(ep,key);language=v['language'];budget=p['budgets'][language];cost=budget['reserve_per_request_eur'];d=ledger(ep)
    if any(a['status']=='uncertain' for a in d['attempts']):raise ValueError('Сначала выясните результат прошлого запроса к сервису')
    fp=studio.canon({'provider':provider,'variant':key,'payload':payload})
    matching=[x for x in d['attempts'] if x['fingerprint']==fp]
    if matching:
        if not retry_inspection:raise ValueError('Такой запрос уже был. Используйте его ответ или приложите проверку после подтверждённого сбоя')
        inspected=studio.read(studio.inside(retry_inspection));last=matching[-1]
        if inspected.get('attempt_id')!=last['id'] or not inspected.get('reason') or not inspected.get('reviewer'):raise ValueError('Проверка перед повтором должна назвать прошлую попытку и причину')
        if last['status']=='response_saved':
            report=studio.read(studio.inside(inspected['quality_report_file']))
            if report.get('passed') is not False or report.get('audio_sha256')!=last.get('audio_sha256') or not report.get('blocking_findings'):raise ValueError('Повтор по качеству должен назвать отклонённую запись и конкретный дефект')
        elif last['status'] not in ['confirmed_absent','confirmed_failed'] or inspected.get('resolution_sha256')!=studio.sha(ep/'provider-attempts'/last['id']/'resolution.json'):raise ValueError('Проверка перед повтором должна назвать закрытую прошлую попытку')
    if any(type(budget.get(k)) not in (int,float) or not math.isfinite(budget[k]) or budget[k]<0 for k in ['reserve_per_request_eur','cap_eur']):raise ValueError('Значения бюджета должны быть конечными и неотрицательными')
    used=sum(committed_cost(a) for a in d['attempts'] if a['language']==language)
    if used+cost>budget['cap_eur']:raise ValueError('Все площадки и правки делят бюджет языка: лимит исчерпан')
    attempt={'id':uuid.uuid4().hex,'provider':provider,'language':language,'variant':key,'fingerprint':fp,'reserved_eur':cost,'actual_cost_eur':None,'status':'uncertain','started_at':studio.now()}
    if kind:attempt['kind']=kind
    d['attempts'].append(attempt);studio.write(ep/'provider-costs.json',d);return attempt
def finish(ep,attempt,**values):
    d=ledger(ep);row=next(x for x in d['attempts'] if x['id']==attempt['id'])
    if 'actual_cost_eur' in values:
        committed_cost({**row,**values})
        known=[x for x in [row.get('actual_cost_eur'),values['actual_cost_eur']] if x is not None]
        values['actual_cost_eur']=max(known) if known else None
    updated={**row,**values};committed_cost(updated);row.update(values);studio.write(ep/'provider-costs.json',d)
def tts(ep,key,retry_inspection=None):
    v=studio.require_approval(ep,key);p=studio.read(ep/'profile.snapshot.json');cfg=p['voices'][v['language']];secret=os.environ.get('ELEVENLABS_API_KEY')
    if cfg.get('mode','tts')=='live':raise ValueError('В профиле голос живой: синтез не нужен. Прикрепите подготовленную запись через studio.py attach voice.')
    if 'voice:'+key in studio.state(ep)['stages']:studio.current(ep,'voice',key);raise ValueError('Проверенная запись голоса уже есть: используйте её или пересоберите слой голоса через revise')
    if not secret:raise ValueError('Задайте свою переменную окружения ELEVENLABS_API_KEY (ключ не вставляйте в чат)')
    # Read-only availability checks happen before the durable paid reservation.
    models,_=request('https://api.elevenlabs.io/v1/models',secret)
    if cfg['model_id'] not in [m['model_id'] for m in models if m.get('can_do_text_to_speech')]:raise ValueError('Выбранная модель озвучки недоступна в вашем аккаунте: выберите другую')
    request('https://api.elevenlabs.io/v1/voices/'+cfg['voice_id'],secret)
    body={'text':v['text'],'model_id':cfg['model_id'],'language_code':v['language'],'voice_settings':cfg.get('settings',{})}
    # Direction is saved separately; do not alter the approved spoken words.
    if cfg.get('delivery_tag'):
        tag=cfg['delivery_tag']
        if not cfg['model_id'].startswith(('eleven_v3','eleven_v4')) or not tag.startswith('[') or not tag.endswith(']'):raise ValueError('Теги подачи нужны модели с поддержкой тегов и записываются в квадратных скобках')
        body['text']=tag+'\n'+v['text']
    a=reserve(ep,key,'elevenlabs',body,retry_inspection);folder=ep/'provider-attempts'/a['id'];folder.mkdir(parents=True);studio.write(folder/'request.json',{'body':body,'voice_id':cfg['voice_id'],'script_sha256':studio.canon(v)})
    result,headers=request('https://api.elevenlabs.io/v1/text-to-speech/'+cfg['voice_id']+'/with-timestamps',secret,body)
    studio.write(folder/'response.json',result)
    audio=base64.b64decode(result['audio_base64'],validate=True);(folder/'voice.mp3').write_bytes(audio)
    if not audio:raise ValueError('Сервис вернул пустой звук')
    finish(ep,a,status='response_saved',audio_sha256=studio.sha(folder/'voice.mp3'),provider_request_id=headers.get('request-id') or headers.get('Request-Id'),confirmed_at=studio.now())
    studio.attach(ep,'voice',key,str((folder/'voice.mp3').relative_to(ROOT)))
def normalized(text):
    import unicodedata,re
    return re.findall(r'[^\W_]+',unicodedata.normalize('NFKC',text.lower().replace('’',"'")),flags=re.UNICODE)
def asr_transcribe(source,language,model_dir):
    """Расшифровка без подсказки. Возвращает (сырой результат, список слов с временами)."""
    from faster_whisper import WhisperModel
    model=WhisperModel(model_dir,device='cpu',compute_type='int8',cpu_threads=2)
    # Decode with the required FFmpeg binary, independent of optional PyAV ABI/keyword differences.
    import numpy as np
    pcm=subprocess.run(['ffmpeg','-v','error','-i',str(source),'-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True).stdout
    audio=np.frombuffer(pcm,dtype='<f4').copy()
    if not len(audio):raise ValueError('В записи нет звука')
    segments,info=model.transcribe(audio,language=language,word_timestamps=True,condition_on_previous_text=False,initial_prompt=None,vad_filter=False,temperature=0)
    raw=[];words=[]
    for s in segments:
        row={'start':s.start,'end':s.end,'text':s.text,'words':[{'text':w.word.strip(),'start':w.start,'end':w.end,'probability':w.probability} for w in (s.words or [])]};raw.append(row);words.extend(row['words'])
    return {'segments':raw,'language':info.language,'audio_sha256':studio.sha(source),'initial_prompt':None,'condition_on_previous_text':False,'vad_filter':False},words
def _tokens(text):
    """Слова текста: (как показать в субтитрах, ключ для сравнения). Пунктуация остаётся в показе;
    отдельно стоящий знак (тире, многоточие) приклеивается к предыдущему слову."""
    out=[]
    for piece in text.split():
        norm=''.join(normalized(piece.replace('ё','е').replace('Ё','Е')))
        if norm:out.append((piece,norm))
        elif out:out[-1]=(out[-1][0]+' '+piece,out[-1][1])
    return out
_LAT={'a':'а','b':'б','c':'к','d':'д','e':'е','f':'ф','g':'г','h':'х','i':'и','j':'дж','k':'к','l':'л','m':'м','n':'н','o':'о','p':'п','q':'к','r':'р','s':'с','t':'т','u':'у','v':'в','w':'в','x':'кс','y':'и','z':'з'}
_EN=[('sh','ш'),('ch','ч'),('zh','ж'),('th','т'),('ph','ф'),('ck','к'),('qu','кв'),('au','о'),('aw','о'),('oo','у'),('ee','и'),('ou','ау')]
def _translit(word,english):
    import re
    w=re.sub(r'(?<=[^aeiouy])e$','',word) if len(word)>3 else word   # немая e на конце: code, claude
    if english:
        for a,b in _EN:w=w.replace(a,b)
    w=re.sub(r'c(?=[eiy])','с',w)
    return ''.join(_LAT.get(ch,ch) for ch in w)
def _variants(norm):
    """Варианты произношения слова для сравнения: латиница в кириллицу (побуквенно и по английским правилам), двойные буквы схлопнуты."""
    import re
    if re.search('[a-z]',norm):forms={_translit(norm,False),_translit(norm,True)}
    else:forms={norm}
    return {re.sub(r'(.)\1+',r'\1',f) for f in forms}
def _close(a,b):
    """Одно и то же слово с точностью до формы, окончания и ошибки распознавания."""
    import difflib
    if a==b:return True
    short=min(len(a),len(b))
    if short<3:return False
    prefix=0
    for x,y in zip(a,b):
        if x!=y:break
        prefix+=1
    if prefix>=max(3,short-2):return True
    return short>=5 and difflib.SequenceMatcher(None,a,b,autojunk=False).ratio()>=0.8
def _similar(va,vb):
    return any(_close(a,b) for a in va for b in vb)
def _fuzzy_opcodes(script_keys,heard_keys):
    """Как difflib.get_opcodes, но слова сравниваются нечётко (наибольшая общая подпоследовательность)."""
    S=[_variants(k) for k in script_keys];H=[_variants(k) for k in heard_keys];n,m=len(S),len(H)
    eq=[[_similar(S[i],H[j]) for j in range(m)] for i in range(n)]
    dp=[[0]*(m+1) for _ in range(n+1)]
    for i in range(n-1,-1,-1):
        for j in range(m-1,-1,-1):
            dp[i][j]=dp[i+1][j+1]+1 if eq[i][j] else max(dp[i+1][j],dp[i][j+1])
    pairs=[];i=j=0
    while i<n and j<m:
        if eq[i][j] and dp[i][j]==dp[i+1][j+1]+1:pairs.append((i,j));i+=1;j+=1
        elif dp[i+1][j]>=dp[i][j+1]:i+=1
        else:j+=1
    ops=[];i=j=0
    for a,b in pairs+[(n,m)]:
        if a>i and b>j:ops.append(('replace',i,a,j,b))
        elif a>i:ops.append(('delete',i,a,j,j))
        elif b>j:ops.append(('insert',i,i,j,b))
        if a<n:
            if ops and ops[-1][0]=='equal' and ops[-1][2]==a:ops[-1]=('equal',ops[-1][1],a+1,ops[-1][3],b+1)
            else:ops.append(('equal',a,a+1,b,b+1))
        i,j=a+1,b+1
    return ops
LIVE_MIN_MATCH=studio.LIVE_MIN_MATCH
LIVE_MAX_GAP=5
def align_words(script_text,asr_words):
    """Выровнять слова утверждённого текста по временам расшифровки. Возвращает слова со временами и список расхождений."""
    S=_tokens(script_text);A=[]
    for w in asr_words:
        t=_tokens(w['text'])
        if t:A.append({'shown':w['text'].strip(),'norm':''.join(x[1] for x in t),'start':float(w['start']),'end':float(w['end'])})
    timed=[None]*len(S);bad=[False]*len(S);issues=[];matched=0
    for tag,i1,i2,j1,j2 in _fuzzy_opcodes([n for _,n in S],[w['norm'] for w in A]):
        if tag=='equal':
            for k in range(i2-i1):timed[i1+k]=(A[j1+k]['start'],A[j1+k]['end'])
            matched+=i2-i1
        elif tag=='replace':
            part=A[j1:j2];lo,hi=part[0]['start'],part[-1]['end'];weights=[len(S[i][1]) for i in range(i1,i2)];total=sum(weights);cursor=lo
            for i,wt in zip(range(i1,i2),weights):
                step=(hi-lo)*wt/total;timed[i]=(cursor,cursor+step);cursor+=step;bad[i]=True
            issues.append({'type':'заменено','script':' '.join(S[i][0] for i in range(i1,i2)),'heard':' '.join(w['shown'] for w in part),'at_s':round(lo,2)})
        elif tag=='delete':
            for i in range(i1,i2):bad[i]=True
            issues.append({'type':'не прозвучало','script':' '.join(S[i][0] for i in range(i1,i2)),'heard':'','at_s':None,'_at':i1})
        else:
            part=A[j1:j2];issues.append({'type':'лишнее в записи','script':'','heard':' '.join(w['shown'] for w in part),'at_s':round(part[0]['start'],2)})
    # слова без времени: распределить по промежутку между соседями
    i=0;end_all=A[-1]['end'] if A else 0.0
    while i<len(S):
        if timed[i] is not None:i+=1;continue
        j=i
        while j<len(S) and timed[j] is None:j+=1
        left=timed[i-1][1] if i>0 else (A[0]['start'] if A else 0.0)
        right=timed[j][0] if j<len(S) else end_all
        right=max(right,left);weights=[len(S[k][1]) for k in range(i,j)];total=sum(weights);cursor=left
        for k,wt in zip(range(i,j),weights):
            step=(right-left)*wt/total;timed[k]=(cursor,cursor+step);cursor+=step
        i=j
    for it in issues:
        if it['at_s'] is None:it['at_s']=round(timed[it.pop('_at')][0],2)
        else:it.pop('_at',None)
    words=[{'text':S[k][0],'start':round(timed[k][0],3),'end':round(timed[k][1],3),'probability':None} for k in range(len(S))]
    gap=best=0
    for flag in bad:
        gap=gap+1 if flag else 0;best=max(best,gap)
    return {'words':words,'issues':issues,'match_ratio':round(matched/len(S),4) if S else 0.0,'max_gap_words':best,'script_words':len(S),'matched_words':matched}
def voice_qa(ep,key,asr=None,prep_journal=None):
    v=studio.require_approval(ep,key);studio.current(ep,'voice',key);folder=ep/'variants'/key;source=folder/'voice.bin';p=studio.read(ep/'profile.snapshot.json')
    mode=p['voices'][v['language']].get('mode','tts')
    raw,words=(asr or asr_transcribe)(source,v['language'],p.get('whisper_model','runtime/models/whisper'))
    strict=normalized(v['text'])==normalized(' '.join(w['text'] for w in words))
    studio.write(folder/'raw-asr.json',raw)
    report={'audio_sha256':studio.sha(source),'script_sha256':studio.canon(v),'unprompted_asr':True,'raw_asr_file':str((folder/'raw-asr.json').relative_to(ROOT)),'listening_status':'not_available'}
    if mode=='live':
        al=align_words(v['text'],words)
        ok=bool(words) and al['match_ratio']>=LIVE_MIN_MATCH and al['max_gap_words']<=LIVE_MAX_GAP
        studio.write(folder/'voice.strict-report.json',{'passed':strict,'rule':'точное совпадение слов с согласованным текстом','script_words':al['script_words'],'asr_words':len(words),'note':'Строгая проверка для живой записи не обязательна; отчёт сохранён рядом.'})
        prep=None
        if prep_journal:
            journal=studio.read(studio.inside(prep_journal));shutil.copy2(studio.inside(prep_journal),folder/'audio-prep.json')
            prep={'journal_file':str((folder/'audio-prep.json').relative_to(ROOT)),'journal_sha256':studio.sha(folder/'audio-prep.json'),'cuts':len(journal.get('cuts',[])),'removed_seconds':journal.get('removed_seconds'),'speech_cut':bool(journal.get('speech_cut')),'tempo_changed':bool(journal.get('tempo_changed'))}
        basis=f"живая запись: совпало {al['matched_words']} из {al['script_words']} слов ({al['match_ratio']:.0%}, нужно не меньше {LIVE_MIN_MATCH:.0%}); самый длинный разрыв {al['max_gap_words']} слов (допустимо до {LIVE_MAX_GAP})"
        report.update(passed=ok,mode='live',words=al['words'],timing_source='слова согласованного текста выровнены по временам расшифровки без подсказки (нечёткое сравнение слов: формы, латиница и кириллица)',match_ratio=al['match_ratio'],max_gap_words=al['max_gap_words'],asr_discrepancies=al['issues'],passed_basis=basis,strict_report_file=str((folder/'voice.strict-report.json').relative_to(ROOT)),strict_passed=strict,audio_post_processing=prep)
        studio.write(folder/'voice.report.json',report)
        if not ok:raise ValueError('Запись не совпала с согласованным текстом: '+basis+'. Запишите заново или поправьте текст вместе с автором. Список расхождений в voice.report.json.')
    else:
        report.update(passed=strict,words=words,timing_source='fresh acoustic word timestamps from unprompted Whisper; provider input is not verification')
        studio.write(folder/'voice.report.json',report)
        if not strict:raise ValueError('Распознанные слова не совпали с согласованным текстом: проверьте имена, числа, пропуски и повторы. Не подгоняйте текст под распознавание.')
    studio.attach(ep,'voice_qa',key,str((folder/'voice.report.json').relative_to(ROOT)))
def avatar(ep,key,audio_url,retry_inspection=None):
    if studio.edition()=='free':raise ValueError('В бесплатной версии аватар недоступен')
    studio.current(ep,'voice_qa',key);studio.require_approval(ep,key);p=studio.read(ep/'profile.snapshot.json');cfg=p['avatar'];secret=os.environ.get('HEYGEN_API_KEY')
    if not secret:raise ValueError('Задайте свою переменную окружения HEYGEN_API_KEY (ключ не вставляйте в чат)')
    if 'avatar:'+key in studio.state(ep)['stages']:studio.current(ep,'avatar',key);raise ValueError('Готовый аватар уже есть: используйте его при правках картинки')
    if not audio_url.startswith('https://'):raise ValueError('Нужна ваша временная ссылка https на звук; не кладите её в публичный пакет')
    staged=hashlib.sha256();count=0
    with urllib.request.urlopen(audio_url,timeout=60) as response:
        for chunk in iter(lambda:response.read(1048576),b''):
            count+=len(chunk)
            if count>100*1024*1024:raise ValueError('Запись больше допустимого размера')
            staged.update(chunk)
    if staged.hexdigest()!=studio.sha(ep/'variants'/key/'voice.bin'):raise ValueError('По ссылке лежит не та запись, что прошла проверку')
    body={**cfg.get('generation_options',{}),'video_inputs':[{'character':{'type':'avatar','avatar_id':cfg['avatar_id'],'avatar_style':cfg.get('avatar_style','normal')},'voice':{'type':'audio','audio_url':audio_url},'background':{'type':'color','value':cfg.get('background','#FFFFFF')}}],'dimension':{'width':cfg['width'],'height':cfg['height']}}
    a=reserve(ep,key,'heygen',body,retry_inspection);folder=ep/'provider-attempts'/a['id'];folder.mkdir(parents=True)
    # Store sensitive temporary URLs locally; excluded from export and public logs.
    studio.write(folder/'request.json',body)
    result,_=request('https://api.heygen.com/v2/video/generate',secret,body,'X-Api-Key');studio.write(folder/'response.json',result)
    job=result.get('data',{}).get('video_id')
    if not job:raise ValueError('Результат аватара не подтверждён: изучите попытку до повтора')
    finish(ep,a,status='job_submitted',provider_job_id=job,audio_sha256=studio.sha(ep/'variants'/key/'voice.bin'))
    print(json.dumps({'attempt':a['id'],'provider_job_id':job,'next':'опрашивайте эту же задачу, не отправляйте заново'},ensure_ascii=False))
def poll_avatar(ep,attempt):
    d=ledger(ep);a=next(x for x in d['attempts'] if x['id']==attempt)
    if a['provider']!='heygen' or not a.get('provider_job_id'):raise ValueError('Нужна реально сохранённая задача HeyGen')
    key=a['variant'];secret=os.environ.get('HEYGEN_API_KEY');result,_=request('https://api.heygen.com/v1/video_status.get?video_id='+a['provider_job_id'],secret,None,'X-Api-Key');data=result['data'];folder=ep/'provider-attempts'/a['id'];studio.write(folder/'status.json',result)
    if data['status'] in ['failed','error']:
        studio.write(folder/'resolution.json',{'status':'confirmed_failed','provider_job_id':a['provider_job_id'],'evidence_sha256':studio.sha(folder/'status.json'),'at':studio.now()});finish(ep,a,status='confirmed_failed');print(json.dumps({'status':'confirmed_failed'},ensure_ascii=False));return
    if data['status']!='completed':print(json.dumps({'status':data['status'],'job_id':a['provider_job_id']},ensure_ascii=False));return
    video=folder/'avatar.mp4'
    with urllib.request.urlopen(data['video_url'],timeout=180) as r,video.open('wb') as f:
        import shutil;shutil.copyfileobj(r,f)
    receipt={'completed':True,'provider_job_id':a['provider_job_id'],'audio_sha256':a['audio_sha256'],'video_sha256':studio.sha(video)};studio.write(folder/'receipt.json',receipt)
    finish(ep,a,status='response_saved',video_sha256=studio.sha(video))
    studio.attach(ep,'avatar',key,str(video.relative_to(ROOT)),str((folder/'receipt.json').relative_to(ROOT)))
def resolve_attempt(ep,attempt_id,evidence_path):
    if not evidence_path:raise ValueError('Приложите подтверждение проверки: журнал не правят, чтобы скрыть неизвестный результат')
    a=next(x for x in ledger(ep)['attempts'] if x['id']==attempt_id);folder=ep/'provider-attempts'/attempt_id;e=studio.read(studio.inside(evidence_path))
    if e.get('attempt_id')!=attempt_id or e.get('request_fingerprint')!=a['fingerprint'] or not e.get('reviewer') or not e.get('source_ref') or not e.get('note'):raise ValueError('Проверка должна относиться к этой попытке, этому запросу и реальному подтверждению сервиса')
    raw=studio.inside(e['provider_evidence_file'])
    if not raw.is_file() or studio.sha(raw)!=e.get('provider_evidence_sha256'):raise ValueError('Подтверждение из истории сервиса не найдено или изменено')
    resolution=e.get('resolution')
    if resolution in ['confirmed_absent','confirmed_failed']:
        if e.get('role')!='user' or not e.get('quote') or not e.get('message_ref'):raise ValueError('Человек должен подтвердить, что результата у сервиса нет или попытка не удалась')
        studio.write(folder/'resolution.json',e);finish(ep,a,status=resolution,resolution_sha256=studio.sha(folder/'resolution.json'));return
    if a['provider']=='elevenlabs' and resolution=='recover_voice':
        key=os.environ.get('ELEVENLABS_API_KEY');history_id=e['history_item_id'];history,_=request('https://api.elevenlabs.io/v1/history/'+history_id,key);original=studio.read(folder/'request.json')
        if history.get('text')!=original['body']['text'] or history.get('voice_id')!=original['voice_id']:raise ValueError('История сервиса не подтверждает именно этот голос и текст')
        path=folder/'recovered.mp3';req=urllib.request.Request('https://api.elevenlabs.io/v1/history/'+history_id+'/audio',headers={'xi-api-key':key})
        try:
            with urllib.request.urlopen(req,timeout=180) as r,path.open('wb') as f:
                import shutil;shutil.copyfileobj(r,f)
        except Exception:raise ValueError('Скачивание не подтверждено; исходная попытка остаётся в журнале') from None
        studio.write(folder/'recovered-history.json',history);studio.write(folder/'resolution.json',e);finish(ep,a,status='response_saved',audio_sha256=studio.sha(path),provider_request_id=history_id)
        studio.attach(ep,'voice',a['variant'],str(path.relative_to(ROOT)));return
    if a['provider']=='heygen' and resolution=='recover_job':
        if e.get('role')!='user' or not e.get('quote') or not e.get('message_ref'):raise ValueError('Свяжите задачу из кабинета с этим запросом и решением человека')
        job=e['provider_job_id'];request('https://api.heygen.com/v1/video_status.get?video_id='+job,os.environ.get('HEYGEN_API_KEY'),None,'X-Api-Key')
        studio.write(folder/'resolution.json',e);finish(ep,a,status='job_submitted',provider_job_id=job,audio_sha256=studio.sha(ep/'variants'/a['variant']/'voice.bin'));return
    raise ValueError('Такое закрытие попытки не поддерживается: попытка остаётся неопределённой')
def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['tts','voice-qa','avatar','avatar-status','resolve-attempt']);ap.add_argument('id');ap.add_argument('variant_or_attempt');ap.add_argument('--audio-url');ap.add_argument('--retry-inspection');ap.add_argument('--evidence');ap.add_argument('--prep-journal');a=ap.parse_args();ep=studio.job(a.id)
    with studio.locked(ep):
        if a.command=='tts':tts(ep,a.variant_or_attempt,a.retry_inspection)
        elif a.command=='voice-qa':voice_qa(ep,a.variant_or_attempt,prep_journal=a.prep_journal)
        elif a.command=='avatar':avatar(ep,a.variant_or_attempt,a.audio_url or '',a.retry_inspection)
        elif a.command=='avatar-status':poll_avatar(ep,a.variant_or_attempt)
        else:resolve_attempt(ep,a.variant_or_attempt,a.evidence)
if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError,StopIteration) as e:raise SystemExit(str(e))
