#!/usr/bin/env python3
"""Track external image/scene attempts, including shared identity assets and unknown costs."""
import argparse,json,math,uuid
from pathlib import Path
import studio
def image_provider():
    # Имя провайдера картинок берётся из config/providers.json; пусто - старая метка external-media.
    try:name=studio.read(studio.ROOT/'config'/'providers.json')['image'].get('provider','')
    except (OSError,ValueError,KeyError):name=''
    return name or 'external-media'
def is_media(row):return row.get('kind')=='media' or row.get('provider')=='external-media'
def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['init-shared','reserve-shared','record-shared','reserve-episode','record-episode']);ap.add_argument('--evidence',required=True);ap.add_argument('--id');ap.add_argument('--variant');ap.add_argument('--attempt');a=ap.parse_args();e=studio.read(studio.inside(a.evidence))
    if a.command=='init-shared':
        if e.get('role')!='user' or not e.get('quote') or not e.get('message_ref') or type(e.get('cap_eur')) not in (int,float) or not math.isfinite(e['cap_eur']) or e['cap_eur']<0:raise ValueError('Выберите и запишите конечный бюджет на героя до первой платной картинки')
        p=studio.ROOT/'shared-asset-costs.json'
        if p.exists():raise ValueError('Журнал общих картинок уже есть: сохраните его попытки')
        studio.write(p,{'cap_eur':e['cap_eur'],'authorization':e,'attempts':[]});return
    if a.command.endswith('shared'):
        p=studio.ROOT/'shared-asset-costs.json'
        with studio.locked(studio.ROOT/'shared-asset-ledger'):
            d=studio.read(p)
            if a.command=='reserve-shared':
                reserve=e.get('reserve_eur')
                if type(reserve) not in (int,float) or not math.isfinite(reserve) or reserve<0 or not e.get('request_ref'):raise ValueError('Запишите запрос к сервису картинок и конечный резерв')
                import providers
                if any(x['status']=='uncertain' for x in d['attempts']) or sum(providers.committed_cost(x) for x in d['attempts'])+reserve>d['cap_eur']:raise ValueError('Сначала выясните прошлую попытку или общий лимит исчерпан')
                row={'id':uuid.uuid4().hex,'provider':image_provider(),'kind':'media','reserved_eur':reserve,'actual_cost_eur':None,'status':'uncertain','request':e};d['attempts'].append(row);studio.write(p,d);print(json.dumps(row,ensure_ascii=False));return
            row=next(x for x in d['attempts'] if x['id']==a.attempt)
            if e.get('status') not in ['response_saved','confirmed_failed','confirmed_absent'] or not e.get('provider_job_id') or not e.get('evidence_ref'):raise ValueError('Запишите результат попытки и подтверждение сервиса')
            verify_evidence(row,e)
            import providers
            updated={**row,'actual_cost_eur':e.get('actual_cost_eur')};providers.committed_cost(updated)
            known=[x for x in [row.get('actual_cost_eur'),e.get('actual_cost_eur')] if x is not None]
            row.update(status=e['status'],receipt=e,actual_cost_eur=max(known) if known else None);studio.write(p,d);return
    import providers
    ep=studio.job(a.id)
    with studio.locked(ep):
        if a.command=='reserve-episode':print(json.dumps(providers.reserve(ep,a.variant,image_provider(),e,kind='media'),ensure_ascii=False));return
        row=next(x for x in providers.ledger(ep)['attempts'] if x['id']==a.attempt)
        if not is_media(row):raise ValueError('Квитанции картинок не закрывают попытки синтеза речи или аватара')
        if e.get('status') not in ['response_saved','confirmed_failed','confirmed_absent'] or not e.get('provider_job_id') or not e.get('evidence_ref'):raise ValueError('Нужна квитанция сервиса картинок')
        verify_evidence(row,e)
        providers.finish(ep,row,status=e['status'],receipt=e,actual_cost_eur=e.get('actual_cost_eur'))
def verify_evidence(row,e):
    if e.get('attempt_id')!=row['id'] or not e.get('reviewer') or not e.get('note'):raise ValueError('Привяжите подтверждение к этой попытке генерации картинки')
    path=studio.inside(e['provider_evidence_file'])
    if not path.is_file() or studio.sha(path)!=e.get('provider_evidence_sha256'):raise ValueError('Файл подтверждения сервиса картинок не найден или изменён')
    if e['status'] in ['confirmed_absent','confirmed_failed'] and (e.get('role')!='user' or not e.get('quote') or not e.get('message_ref')):raise ValueError('Нужно подтверждение человека, что попытка не удалась или результата нет')
if __name__=='__main__':main()
