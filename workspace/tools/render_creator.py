#!/usr/bin/env python3
import argparse,json,math,shutil,subprocess,wave
from pathlib import Path
import studio
ROOT=studio.ROOT
# Пауза, после которой начинается новая реплика. live_voice.py prepare сокращает паузы длиннее 0,5 с до 0,3 с,
# поэтому в готовой записи граница реплики видна как зазор от 0,25 с.
CAPTION_PAUSE=0.25
SENTENCE_END=('.','!','?','…')
def caption_groups(words,max_words=4,max_chars=38,pause=CAPTION_PAUSE):
    """Группы субтитров: до max_words слов и max_chars знаков, не через паузу и не через конец фразы."""
    groups=[];start=0
    while start<len(words):
        end=start+1
        while end<len(words) and end-start<max_words:
            prev=words[end-1]
            if words[end]['start']-prev['end']>=pause or prev['text'].rstrip('»")').endswith(SENTENCE_END):break
            if len(' '.join(w['text'] for w in words[start:end+1]))>max_chars:break
            end+=1
        groups.append({'from':start,'to':end});start=end
    return groups
def main():
    ap=argparse.ArgumentParser();ap.add_argument('id');ap.add_argument('variant');a=ap.parse_args();ep=studio.job(a.id);key=a.variant
    with studio.locked(ep):
        v=studio.require_approval(ep,key);studio.current(ep,'voice_qa',key);studio.current(ep,'scene',key)
        path=ep/'variants'/key;plan=studio.read(path/'scene.json');cfg=studio.read(ep/'profile.snapshot.json');words=studio.read(path/'voice_qa.json')['words']
        target=path/'render-source'
        if target.exists():raise ValueError('Исходники рендера заморожены: для замены сделайте revise со слоем visual')
        target.mkdir();(target/'public').mkdir();(target/'scenes').mkdir()
        duration=float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path/'voice.bin')],capture_output=True,text=True,check=True).stdout)
        pcm=subprocess.run(['ffmpeg','-v','error','-i',str(path/'voice.bin'),'-ac','1','-ar','48000','-f','f32le','-'],capture_output=True,check=True).stdout
        import numpy as np
        values=np.frombuffer(pcm,dtype='<f4');rms=[float(np.sqrt(np.mean(values[i:i+1600]**2))) for i in range(0,len(values),1600)];peak=max(rms or [1]);rms=[x/max(peak,.001) for x in rms]
        sources=studio.inside(plan['scene_source'],ep)
        for p in sources.rglob('*'):
            if p.is_file():
                if p.suffix not in ['.ts','.tsx','.json']:raise ValueError('В папке сцены только исходники; картинки и звук указываются в assets')
                dest=target/'scenes'/p.relative_to(sources);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        for source in plan.get('assets',[]):
            p=path/'media-snapshot'/source['name'];dest=target/'public'/source['name']
            if not dest.resolve().is_relative_to(target/'public'):raise ValueError('Некорректное имя медиафайла')
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        groups=caption_groups(words)
        film={'frames':math.ceil(duration*30),'format':cfg['format'],'platforms':v['platforms'],'words':words,'rms':rms,'captionGroups':groups,'style':cfg['style'],'plan':plan}
        if cfg['style'].get('font_asset'):shutil.copy2(studio.inside(cfg['style']['font_asset']),target/'public/own-font.woff2')
        if cfg['format']=='cartoon':
            rig=cfg['presenter'];film.update(presenter=rig,poseEvents=plan['pose_events'])
            if not plan['pose_events'] or plan['pose_events'][0]['at']!=0 or not all(e.get('reason') for e in plan['pose_events']):raise ValueError('Спланируйте смену поз по смыслу, начиная с нулевого кадра')
            for pose in rig['poses']:
                for state in ['rest','aa','oo','blink']:
                    if not pose.get(state):continue
                    p=studio.inside(pose[state]);dest=target/'public'/pose[state];dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        else:
            studio.current(ep,'avatar',key);shutil.copy2(path/'avatar.mp4',target/'public/avatar.mp4');film['avatarTimeline']=plan['avatar_timeline']
            avatar_duration=float(subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(path/'avatar.mp4')],capture_output=True,text=True,check=True).stdout)
            if avatar_duration<duration-.15:raise ValueError('Клип аватара закончился раньше проверенной озвучки')
            if not film['avatarTimeline'] or film['avatarTimeline'][0]['at']!=0:raise ValueError('Таймлайн ведущего должен начинаться с нулевого кадра')
            for row in film['avatarTimeline']:
                if row.get('hidden'):continue
                if not 0<=row['x']<1080 or not 0<=row['y']<1920 or not 0<row['width']<=1080-row['x'] or not 0<row['height']<=1920-row['y']:raise ValueError('Окно ведущего должно оставаться внутри кадра')
            if 'instagram' in v['platforms'] and any(x.get('hidden') for x in film['avatarTimeline']):raise ValueError('Для Instagram ведущий нужен в кадре до самого конца')
        shutil.copy2(path/'voice.bin',target/'public/voice.mp3');studio.write(target/'public/film.json',film)
        for name in ['Root.tsx','package.json','tsconfig.json']:shutil.copy2(ROOT/'engine/remotion'/name,target/name)
        (target/'node_modules').symlink_to((ROOT/'runtime/node_modules').resolve(),target_is_directory=True)
        studio.write(path/'render-manifest.json',{'files':[{'path':str(p.relative_to(target)),'sha256':studio.sha(p)} for p in target.rglob('*') if p.is_file() and 'node_modules' not in p.parts],'audio_sha256':studio.sha(path/'voice.bin')})
        studio.run(ep,['npx','--no-install','tsc','--noEmit'],target)
        final=path/'render.mp4';studio.run(ep,['npx','--no-install','remotion','render','Root.tsx','CreatorFilm',final,'--codec','h264','--audio-codec','aac','--audio-bitrate','192k','--pixel-format','yuv420p','--crf','17','--concurrency','2'],target)
        # Remove source/tool comments and timestamps, preserving video/audio streams.
        cleaned=path/'render-clean.mp4';studio.run(ep,['ffmpeg','-v','error','-y','-i',final,'-i',path/'voice.bin','-map','0:v:0','-map','1:a:0','-map_metadata','-1','-metadata','comment=','-c:v','copy','-c:a','aac','-ar','48000','-b:a','192k','-t',str(film['frames']/30),'-movflags','+faststart',cleaned]);cleaned.replace(final)
        studio.attach(ep,'render',key,str(final.relative_to(ROOT)))
if __name__=='__main__':main()
