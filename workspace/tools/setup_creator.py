#!/usr/bin/env python3
"""Validate the new author's choices; API keys never belong to this profile."""
import argparse,json,math,re,secrets,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
import studio
HEROES=['hero-1','hero-2','hero-3']
def load_hero(name):
    if name not in HEROES:raise ValueError('Выберите героя из готовых: hero-1, hero-2 или hero-3')
    path=ROOT/'heroes'/name/'manifest.json'
    if not path.is_file():raise ValueError('Не найден манифест героя '+name)
    m=json.loads(path.read_text())
    if m.get('ready') is not True:raise ValueError('Картинки героя '+name+' ещё не положены в папку heroes/'+name+' (см. README.md в ней)')
    return m
CAPTION_BOTTOM=round(1920*0.78)   # нижний край субтитров не ниже 78% высоты кадра
CAPTION_LINES=2;CAPTION_LINE_HEIGHT=1.17
def presets():
    path=ROOT/'config/style-presets.json'
    return json.loads(path.read_text()) if path.is_file() else {'presets':[]}
def apply_preset(style):
    """style.preset = имя готового набора: незаполненные поля берутся из него, заполненные автором не трогаем."""
    name=style.get('preset')
    if not name:return style
    found=next((x for x in presets()['presets'] if x['name']==name),None)
    if not found:raise ValueError('Нет такого набора стиля: '+str(name)+'. Есть: '+', '.join(x['name'] for x in presets()['presets']))
    return {**found['style'],**{k:v for k,v in style.items() if v not in ('',None)}}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--profile',type=Path,required=True);a=ap.parse_args();p=json.loads(a.profile.read_text())
    def credentials(v):
        if isinstance(v,dict):return any(k.lower().endswith(('token','api_key','secret','password')) or credentials(x) for k,x in v.items())
        if isinstance(v,list):return any(credentials(x) for x in v)
        return False
    if credentials(p):raise ValueError('Ключи и токены не храним в профиле: только в переменных окружения')
    free=studio.edition()=='free'
    if free and p.get('format')!='cartoon':raise ValueError('В бесплатной версии доступен только мультик (format: cartoon). Аватар входит в платную версию.')
    if free and len(p.get('languages',[]))!=1:raise ValueError('В бесплатной версии один язык ролика')
    p['platforms']=[x.strip().lower() for x in p['platforms']]
    if not set(p['platforms'])<=set(['instagram','tiktok','youtube','facebook','pinterest','linkedin']):raise ValueError('Выберите площадки из списка: instagram, tiktok, youtube, facebook, pinterest, linkedin')
    style=p['style']=apply_preset(p.get('style') or {})
    if not all(re.fullmatch(r'#[0-9a-fA-F]{6}',style.get(k,'')) for k in ['background','accent','text']):raise ValueError('Выберите свои цвета фона, акцента и текста в формате #RRGGBB')
    if not style.get('fontFamily'):raise ValueError('Выберите свой шрифт')
    for k in ['captionX','captionY','captionWidth','captionFontSize']:
        if type(style.get(k)) not in (int,float) or not math.isfinite(style[k]) or style[k]<0:raise ValueError('Некорректные размеры субтитров')
    if not 0<style['captionWidth']<=1080-style['captionX'] or not 20<=style['captionFontSize']<=140 or style['captionY']>1740:raise ValueError('Субтитры выходят за безопасную читаемую область')
    if style['captionY']+CAPTION_LINES*style['captionFontSize']*CAPTION_LINE_HEIGHT>CAPTION_BOTTOM:raise ValueError(f'Субтитры опускаются ниже {CAPTION_BOTTOM} px (78% высоты кадра): их закроет интерфейс площадки. Поднимите captionY или уменьшите шрифт.')
    if style.get('font_asset'):
        font=(ROOT/style['font_asset']).resolve()
        if not font.is_relative_to(ROOT) or not font.is_file() or font.suffix!='.woff2':raise ValueError('Файл шрифта не найден: укажите woff2 внутри проекта (по умолчанию fonts/Montserrat-ExtraBold.woff2)')
    if p['format'] not in ['cartoon','avatar'] or not p['author_approval'] or not p['style_approval'] or not p['languages'] or not p['platforms']:raise ValueError('Нужны ваши решения по автору, стилю, языку и площадкам')
    for language in p['languages']:
        v=p['voices'][language];mode=v.get('mode','tts')
        if mode=='live':
            if not v.get('ownership_confirmed'):raise ValueError('Подтвердите, что это ваш собственный голос (ownership_confirmed)')
            budget=p.get('budgets',{}).get(language)
            if budget is not None and not 0<=budget['reserve_per_request_eur']<=budget['cap_eur']:raise ValueError('Некорректный бюджет на язык')
        elif mode=='tts':
            if not v.get('voice_id') or not v.get('model_id') or not v.get('ownership_confirmed'):raise ValueError('Выберите свой голос: живую запись или проверенный голос в ElevenLabs')
            budget=p['budgets'][language]
            if not 0<budget['reserve_per_request_eur']<=budget['cap_eur']:raise ValueError('Задайте бюджет на язык до платных запросов')
        else:raise ValueError('Режим голоса должен быть live (своя запись) или tts (синтез)')
    if free:
        hero=load_hero(p.get('hero',''))
        p['presenter']={'eyeAnchor':hero['eyeAnchor'],'scale':hero.get('scale',1),'poses':hero['poses']}
    else:
        source=(ROOT/p.get('source_photo','')).resolve()
        if not p.get('source_photo') or not source.is_relative_to(ROOT) or not source.is_file():raise ValueError('Положите свою фотографию внутрь личного проекта и укажите путь')
    if p['format']=='cartoon':
        rig=p['presenter']
        if len(rig['poses'])<2:raise ValueError('Нужно минимум две позы героя с разными руками и взглядом')
        for pose in rig['poses']:
            for state in ['rest','aa','oo']:
                path=(ROOT/pose[state]).resolve()
                if not path.is_relative_to(ROOT) or not path.is_file():raise ValueError('Не найдены картинки поз (rest, aa, oo)')
        if not p.get('character_approval'):raise ValueError('Подтвердите выбранного героя')
    else:
        if not p['avatar'].get('avatar_id') or not p['avatar'].get('identity_confirmed'):raise ValueError('Выберите и подтвердите свой аватар в аккаунте')
        if not all(type(p['avatar'].get(k)) is int and 128<=p['avatar'][k]<=4096 for k in ['width','height']):raise ValueError('Укажите размеры аватара из настроек сервиса')
    p['ready']=True;dest=ROOT/'profile.local.json'
    if dest.exists():raise ValueError('Профиль уже есть. Не перезаписывайте его: правки вносите отдельно и осознанно')
    dest.write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');dest.chmod(0o600)
    print(json.dumps({'ready_for_pilot':True,'format':p['format'],'paid_calls':0,'next':'Сделайте один пробный ролик; независимая проверка готового файла обязательна.'},ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,OSError) as e:raise SystemExit(str(e) if isinstance(e,ValueError) else 'Профиль заполнен не до конца или файл недоступен: '+repr(e))
