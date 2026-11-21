#!/usr/bin/env python3
"""Ставит один навык и отдельный личный проект, не затирая данные."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
RU={'usage: ':'запуск: ','options':'параметры','optional arguments':'параметры','%(prog)s: error: %(message)s\n':'%(prog)s: ошибка: %(message)s\n',
    'the following arguments are required: %s':'не хватает обязательных параметров: %s','invalid choice: %(value)r (choose from %(choices)s)':'недопустимое значение: %(value)r (можно: %(choices)s)',
    'unrecognized arguments: %s':'неизвестные параметры: %s','expected one argument':'нужно значение','argument %(argument_name)s: %(message)s':'параметр %(argument_name)s: %(message)s'}
_en=argparse._
argparse._=lambda text:RU.get(text,_en(text))   # служебные строки argparse по-русски
IGNORE=shutil.ignore_patterns('__pycache__','*.pyc','.DS_Store','.git','node_modules','.env','.env.*')

def main():
    ap=argparse.ArgumentParser(prog='install.py',description='Установка навыка МОНТАЖ: навык ставится в папку навыков клиента, личный проект создаётся отдельно. Данные существующего проекта не затираются.',add_help=False)
    ap.add_argument('-h','--help',action='help',help='показать эту справку и выйти')
    ap.add_argument('--client',choices=['claude','codex'],required=True,metavar='{claude,codex}',help='куда ставить: claude (Claude Code, ~/.claude/skills) или codex (Codex, ~/.agents/skills)')
    ap.add_argument('--project',type=Path,required=True,metavar='ПАПКА',help='личный проект с настройками и медиа, например ~/montazh-project')
    ap.add_argument('--skill-dir',type=Path,metavar='ПАПКА',help='своя папка навыка вместо стандартной (необязательно)')
    ap.add_argument('--state-dir',type=Path,metavar='ПАПКА',help='своя папка для указателя на проект (необязательно, для проверок)')
    a=ap.parse_args(); source=Path(__file__).resolve().parent
    manifest=json.loads((source/'PACKAGE.json').read_text());slug=manifest['name']
    targets={'claude':Path.home()/'.claude/skills','codex':Path.home()/'.agents/skills'}
    target=(a.skill_dir or targets[a.client]/slug).expanduser().resolve();project=a.project.expanduser().resolve()
    if target==source or target.is_relative_to(source) or source.is_relative_to(target):
        raise SystemExit('Сначала распакуйте архив во временную папку, затем ставьте из неё в папку навыка')
    for left,right in [(project,source),(project,target)]:
        if left==right or left.is_relative_to(right) or right.is_relative_to(left):raise SystemExit('Личный проект, установленный навык и распакованный архив должны лежать в разных папках, не внутри друг друга')
    if any(p.is_symlink() for p in source.rglob('*')):raise SystemExit('В архиве есть символическая ссылка: установка отклонена')
    if project.exists() and any(project.iterdir()):
        if not (project/'.video-skill-project.json').is_file(): raise SystemExit('Папка проекта не пуста: выберите новую')
        previous=json.loads((project/'.video-skill-project.json').read_text())
        if previous['name']!=slug: raise SystemExit('Эта папка принадлежит другому навыку')
        print('Личный проект уже есть и сохранён. Настройки и медиа не тронуты; обновление движка делается отдельно и осознанно.')
    else:
        project.mkdir(parents=True,exist_ok=True);shutil.copytree(source/'workspace',project,dirs_exist_ok=True,ignore=IGNORE)
        (project/'.video-skill-project.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    if target.exists():
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup=target.with_name(target.name+'.backup-'+stamp);shutil.copytree(target,backup)
        print('Резервная копия прежнего навыка:',backup)
    target.mkdir(parents=True,exist_ok=True)
    for p in source.iterdir():
        if p.name in ['workspace','__pycache__','.DS_Store','.git','node_modules'] or p.suffix=='.pyc' or p.name=='.env' or p.name.startswith('.env.'): continue
        if p.is_symlink(): raise SystemExit('Символические ссылки в источнике установки не принимаются')
        if p.is_dir():shutil.copytree(p,target/p.name,dirs_exist_ok=True,ignore=IGNORE)
        elif p.is_file():shutil.copy2(p,target/p.name)
    pointer=(a.state_dir or Path.home()/'.local/share/video-skills')/f'{slug}.json';pointer.parent.mkdir(parents=True,exist_ok=True)
    pointer.write_text(json.dumps({'project':str(project),'skill':str(target)},ensure_ascii=False,indent=2)+'\n');pointer.chmod(0o600)
    print(json.dumps({'installed':True,'client':a.client,'skill':str(target),'project':str(project),'next':'Откройте AUTOPILOT.md и продолжите настройку в личном проекте. Платных вызовов и публикаций не было.'},ensure_ascii=False))

if __name__=='__main__': main()
