#!/usr/bin/env python3
"""Генератор картинок героя: сухой адаптер. Провайдер и ключ задаются в config/providers.json.

Сейчас есть только интерфейс и сухой режим: проверка настройки и план запроса без сети и без расхода.
Живой вызов подключается после выбора провайдера. Ключ читается только из переменной окружения, имя которой указано в настройке.
"""
import argparse,json,math,os
from pathlib import Path
import studio
ROOT=studio.ROOT
def config():
    path=ROOT/'config'/'providers.json'
    try:data=studio.read(path)['image']
    except (OSError,ValueError,KeyError):raise ValueError('Не найден config/providers.json или в нём нет блока image') from None
    return {'provider':data.get('provider') or '','model':data.get('model') or '','env_key':data.get('env_key') or '','price_hint_eur':data.get('price_hint_eur')}
def check():
    c=config();missing=[k for k in ['provider','model','env_key'] if not c[k]]
    return {'configured':not missing,'missing':missing,'provider':c['provider'],'key_present':bool(c['env_key'] and os.environ.get(c['env_key'])),'price_hint_eur':c['price_hint_eur']}
def plan(prompt,out):
    c=config()
    if not c['provider']:raise ValueError('Генератор картинок не выбран: впишите провайдера, модель и имя переменной ключа в config/providers.json')
    if not prompt.strip():raise ValueError('Пустое описание картинки')
    return {'dry_run':True,'provider':c['provider'],'model':c['model'],'env_key':c['env_key'],'prompt_chars':len(prompt),'output':str(out),'network_calls':0,'paid_calls':0}
def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest='cmd',required=True)
    sub.add_parser('check')
    p=sub.add_parser('generate');p.add_argument('--prompt-file',required=True);p.add_argument('--out',required=True);p.add_argument('--live',action='store_true')
    a=ap.parse_args()
    if a.cmd=='check':print(json.dumps(check(),ensure_ascii=False));return
    if a.live:raise ValueError('Живой вызов генератора картинок ещё не подключён. Сейчас доступен только сухой режим без --live.')
    prompt=studio.inside(a.prompt_file).read_text()
    print(json.dumps(plan(prompt,studio.inside(a.out)),ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except (ValueError,OSError) as e:raise SystemExit(str(e))
