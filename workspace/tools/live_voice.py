#!/usr/bin/env python3
"""Живой голос: подготовка записи и расшифровка в черновик сценария.

Без сети и без платных вызовов. Команды:
  prepare INPUT --out OUT.mp3   привести голосовое к mp3 48 кГц моно, выровнять громкость,
                                убрать тишину по краям, сократить длинные паузы, записать журнал разрезов
  transcribe AUDIO --out DRAFT.json   расшифровать запись без подсказки и собрать черновик script.json

Речь не вырезается и темп не меняется: режутся только участки тишины.
"""
import argparse,hashlib,json,subprocess,sys
from pathlib import Path

SR=48000
FRAME=480            # 10 мс
FADE=960             # 20 мс
LEAD_KEEP=0.04       # сколько тишины оставить в начале, сек (не больше 0,05)
TAIL_KEEP=0.25       # и в конце
FILTERS='highpass=f=80,afftdn=nf=-25'
LOUDNORM='loudnorm=I=-16:TP=-1.5:LRA=11'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()


def duration(path):
    r=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path)],capture_output=True,text=True)
    try:return float(r.stdout.strip())
    except ValueError:raise ValueError('Не удалось прочитать длительность файла: '+str(path)) from None


def decode(path):
    import numpy as np
    r=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vn','-ac','1','-ar',str(SR),'-af',FILTERS,'-f','f32le','-'],capture_output=True)
    if r.returncode or not r.stdout:raise ValueError('Не удалось прочитать запись. Проверьте, что файл звуковой (ogg, opus, m4a, mp3, wav) и FFmpeg установлен.')
    return np.frombuffer(r.stdout,dtype='<f4').copy()


def silence_frames(x):
    """Вернуть (маска тишины по кадрам по 10 мс, порог в дБ)."""
    import numpy as np
    n=len(x)//FRAME
    if n==0:raise ValueError('Запись слишком короткая')
    f=x[:n*FRAME].reshape(n,FRAME)
    db=20*np.log10(np.maximum(np.sqrt(np.mean(f.astype('float64')**2,axis=1)),1e-5))
    speech_ref=float(np.percentile(db,95));noise_ref=float(np.percentile(db,10))
    threshold=max(min(max(noise_ref+8,speech_ref-40),speech_ref-12),-60.0)   # тише -60 дБ речью не считаем
    return db<threshold,threshold


def runs(mask):
    out=[];start=None
    for i,v in enumerate(mask):
        if v and start is None:start=i
        if not v and start is not None:out.append((start,i));start=None
    if start is not None:out.append((start,len(mask)))
    return out


def plan_cuts(x,pause_threshold,pause_keep):
    """Список разрезов в сэмплах: (начало, конец, тип). Режутся только участки тишины."""
    mask,threshold=silence_frames(x)
    if mask.all():raise ValueError('В записи не слышно речи. Запишите голосовое ещё раз.')
    n=len(mask);cuts=[]
    for s,e in runs(mask):
        a,b=s*FRAME,e*FRAME
        if s==0:
            keep=int(LEAD_KEEP*SR)
            if b-a>keep:cuts.append((a,b-keep,'начало'))
        elif e==n:
            keep=int(TAIL_KEEP*SR);b=len(x)
            if b-a>keep:cuts.append((a+keep,b,'конец'))
        elif (b-a)/SR>pause_threshold:
            half=int(pause_keep*SR/2)
            cuts.append((a+half,b-half,'пауза'))
    return cuts,mask,threshold


def apply_cuts(x,cuts):
    """Склеить куски между разрезами. Края разрезов плавно гасятся и нарастают внутри тишины."""
    import numpy as np
    pieces=[];log=[];pos=0;out_len=0
    for a,b,kind in cuts:
        seg=x[pos:a].copy()
        if pos>0 and len(seg):
            k=min(FADE,len(seg));seg[:k]*=np.linspace(0,1,k,dtype='float32')
        if kind!='начало' and len(seg):
            k=min(FADE,len(seg));seg[-k:]*=np.linspace(1,0,k,dtype='float32')
        pieces.append(seg);out_len+=len(seg)
        log.append({'type':kind,'src_start_s':round(a/SR,3),'src_end_s':round(b/SR,3),'removed_s':round((b-a)/SR,3),'out_at_s':round(out_len/SR,3)})
        pos=b
    last=x[pos:].copy()
    if pos>0 and len(last):
        k=min(FADE,len(last));last[:k]*=np.linspace(0,1,k,dtype='float32')
    pieces.append(last)
    return np.concatenate(pieces),log


def prepare(src,out,journal=None,pause_threshold=0.5,pause_keep=0.3,max_seconds=60.0):
    import numpy as np
    src=Path(src);out=Path(out)
    if not src.is_file():raise ValueError('Файл записи не найден: '+str(src))
    if not 0<pause_keep<=pause_threshold:raise ValueError('Оставляемая пауза должна быть больше нуля и не больше порога')
    x=decode(src);cuts,mask,threshold=plan_cuts(x,pause_threshold,pause_keep)
    speech_before=float((~mask).sum()*FRAME/SR)
    y,log=apply_cuts(x,cuts)
    new_mask,_=silence_frames(y)
    speech_after=float((~new_mask).sum()*FRAME/SR)
    seconds=len(y)/SR
    if seconds>max_seconds:
        raise ValueError(f'Запись длиннее {max_seconds:g} секунд даже после сокращения пауз ({seconds:.1f} с). Запишите заново короче: ускорять речь мы не будем.')
    out.parent.mkdir(parents=True,exist_ok=True)
    r=subprocess.run(['ffmpeg','-v','error','-y','-f','f32le','-ar',str(SR),'-ac','1','-i','-','-af',LOUDNORM,'-ar',str(SR),'-ac','1','-c:a','libmp3lame','-b:a','192k',str(out)],input=y.astype('<f4').tobytes(),capture_output=True)
    if r.returncode:raise ValueError('FFmpeg не смог собрать mp3. Проверьте установку FFmpeg с поддержкой mp3.')
    data={'schema':1,'tool':'live_voice.py prepare',
          'source':{'file':src.name,'sha256':sha(src),'duration_s':round(len(x)/SR,3)},
          'params':{'pause_threshold_s':pause_threshold,'pause_keep_s':pause_keep,'lead_keep_s':LEAD_KEEP,'tail_keep_s':TAIL_KEEP,'silence_threshold_db':round(threshold,1),'filters':FILTERS,'loudness':LOUDNORM,'sample_rate':SR,'channels':1},
          'cuts':log,'removed_seconds':round(sum(c['removed_s'] for c in log),3),
          'speech_seconds_before':round(speech_before,2),'speech_seconds_after':round(speech_after,2),
          'speech_cut':False,'tempo_changed':False,
          'output':{'file':out.name,'sha256':sha(out),'duration_s':round(duration(out),3),'pre_encode_duration_s':round(seconds,3)}}
    path=Path(journal) if journal else out.with_suffix('.cuts.json')
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    data['journal_file']=str(path)
    return data


def draft(raw,language,platforms,variant='main'):
    text=' '.join(s['text'].strip() for s in raw['segments'] if s['text'].strip())
    return {'variants':[{'id':variant,'language':language,'platforms':platforms,'text':text,'caption':''}]}


DEFAULT_PLATFORMS=['instagram']


def platforms_for(platforms=None):
    """Площадки до расшифровки: явный --platforms, потом профиль (готовый, затем черновик), иначе instagram."""
    if platforms:return list(platforms),'--platforms'
    root=Path(__file__).resolve().parents[1]
    for name in ('profile.local.json','profile.draft.json'):
        path=root/name
        try:found=json.loads(path.read_text()).get('platforms') if path.is_file() else None
        except (ValueError,OSError):found=None
        if found:return list(found),name
    return list(DEFAULT_PLATFORMS),'по умолчанию'


def transcribe(audio,out,language='ru',platforms=None,model_dir=None,from_asr=None):
    audio=Path(audio);out=Path(out)
    platforms,platforms_source=platforms_for(platforms)
    if from_asr:raw=json.loads(Path(from_asr).read_text())
    else:
        import providers
        model=model_dir or 'runtime/models/whisper'
        raw,_=providers.asr_transcribe(audio,language,model)
    asr_path=out.with_suffix('.asr.json');asr_path.write_text(json.dumps(raw,ensure_ascii=False,indent=2)+'\n')
    out.write_text(json.dumps(draft(raw,language,platforms),ensure_ascii=False,indent=2)+'\n')
    return {'draft':str(out),'asr':str(asr_path),'platforms':platforms,'platforms_source':platforms_source,'next':'Покажите автору текст, исправьте только ошибки распознавания (цифры, имена) с его согласия, затем studio.py script.'}


def main():
    ap=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter);sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('prepare');p.add_argument('input');p.add_argument('--out',required=True);p.add_argument('--journal')
    p.add_argument('--max-pause',type=float,default=0.5,help='паузы длиннее этого значения сокращаются, сек');p.add_argument('--pause-keep',type=float,default=0.3,help='сколько оставить от длинной паузы, сек');p.add_argument('--max-seconds',type=float,default=60.0)
    p=sub.add_parser('transcribe');p.add_argument('audio');p.add_argument('--out',required=True);p.add_argument('--lang',default='ru');p.add_argument('--platforms');p.add_argument('--model-dir');p.add_argument('--from-asr',help='готовая расшифровка вместо запуска модели')
    a=ap.parse_args()
    if a.cmd=='prepare':
        d=prepare(a.input,a.out,a.journal,a.max_pause,a.pause_keep,a.max_seconds)
        print(json.dumps({'prepared':d['output']['file'],'duration_s':d['output']['duration_s'],'removed_seconds':d['removed_seconds'],'cuts':len(d['cuts']),'journal':d['journal_file']},ensure_ascii=False))
    else:
        print(json.dumps(transcribe(a.audio,a.out,a.lang,[x.strip() for x in a.platforms.split(',')] if a.platforms else None,a.model_dir,a.from_asr),ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError) as e:raise SystemExit(str(e))
