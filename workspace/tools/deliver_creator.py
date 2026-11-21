#!/usr/bin/env python3
import argparse,json,os,subprocess
from pathlib import Path
import studio
def call(method,fields,path=None):
    token=os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:raise ValueError('Не задан токен вашего Telegram-бота (TELEGRAM_BOT_TOKEN)')
    args=['curl','--silent','--show-error','--config','-','--connect-timeout','20','--max-time','180']
    for k,v in fields.items():args+=['--form-string',f'{k}={v}']
    if path:args+=['--form','document=@'+str(path)]
    p=subprocess.run(args,input='url = '+json.dumps('https://api.telegram.org/bot'+token+'/'+method)+'\n',capture_output=True,text=True)
    if p.returncode:raise ValueError('Результат отправки в Telegram не подтверждён: изучите запись доставки до повтора')
    try:r=json.loads(p.stdout)
    except ValueError:raise ValueError('Результат отправки в Telegram не подтверждён') from None
    if not r.get('ok'):raise ValueError('Telegram не подтвердил доставку')
    return r['result']
def main():
    ap=argparse.ArgumentParser();ap.add_argument('id');ap.add_argument('variant');ap.add_argument('--reconcile');ap.add_argument('--retry-inspection');a=ap.parse_args();ep=studio.job(a.id)
    with studio.locked(ep):
        for stage in ['technical_qa','visual_qa']:studio.current(ep,stage,a.variant)
        v=studio.variant(ep,a.variant);folder=ep/'variants'/a.variant;file=folder/'render.mp4';path=folder/'delivery.json';digest=studio.sha(file);cfg=studio.profile()['delivery']
        if a.reconcile:
            e=studio.read(studio.inside(a.reconcile));selected=folder/e.get('receipt_name','delivery.json')
            if selected.parent!=folder or not selected.is_file():raise ValueError('Выберите существующую квитанцию доставки этого варианта')
            previous=studio.read(selected)
            if previous.get('status')!='uncertain' or e.get('receipt_sha256')!=studio.sha(selected) or e.get('role')!='user' or not e.get('quote') or not e.get('message_ref') or e.get('chat_id')!=previous.get('chat_id'):raise ValueError('Закрытие должно относиться к реальной неопределённой отправке и решению человека')
            raw=studio.inside(e['evidence_file'])
            if not raw.is_file() or studio.sha(raw)!=e.get('evidence_sha256'):raise ValueError('Подтверждение проверки Telegram не найдено')
            if e['resolution']=='confirmed_sent':
                if not isinstance(e.get('message_id'),int):raise ValueError('Нужен настоящий message_id из Telegram')
                if selected==path:
                    received=studio.inside(e['received_file'])
                    if not received.is_file() or studio.sha(received)!=digest:raise ValueError('Скачанный из Telegram файл должен совпасть с готовым видео')
                studio.write(selected,{**previous,'status':'sent','message_id':e['message_id'],'reconciliation':e})
            elif e['resolution']=='confirmed_absent':studio.write(selected,{**previous,'status':'confirmed_absent','reconciliation':e})
            else:raise ValueError('Такое закрытие доставки не поддерживается')
            return
        for prior in ep.rglob('delivery.json'):
            r=studio.read(prior)
            if r.get('status')=='uncertain':raise ValueError('Сначала выясните прошлую доставку, потом отправляйте снова')
        if path.exists():
            r=studio.read(path)
            if r.get('sha256')!=digest or r.get('status') not in ['sent','local_ready','confirmed_absent']:raise ValueError('Запись доставки относится к другому файлу или результату: изучите её')
            if r.get('status')=='confirmed_absent':
                inspection=studio.read(studio.inside(a.retry_inspection)) if a.retry_inspection else {}
                if inspection.get('receipt_sha256')!=studio.sha(path) or not inspection.get('reason'):raise ValueError('Перед повтором нужна проверка, что сообщения нет')
                path.rename(folder/('delivery-absent-'+str(len(list(folder.glob('delivery-absent-*.json'))))+'.json'))
            elif r.get('status')=='local_ready':
                if cfg['transport']=='local':print(json.dumps(r,ensure_ascii=False));return
                path.rename(folder/('delivery-local-'+str(len(list(folder.glob('delivery-local-*.json'))))+'.json'))
            elif cfg['transport']=='telegram' and (r.get('chat_id')!=cfg.get('chat_id') or r.get('bot_id')!=cfg.get('bot_id')):raise ValueError('Сохранённая квитанция относится к другому получателю или боту: изучите её до смены')
            elif cfg['transport']=='local':print(json.dumps({'status':'local_ready','file':str(file),'sha256':digest,'previous_telegram_receipt':r},ensure_ascii=False));return
        if cfg['transport']=='local':
            r={'status':'local_ready','sha256':digest,'file':str(file),'at':studio.now()};studio.write(path,r);print(json.dumps(r,ensure_ascii=False));return
        if cfg['transport']!='telegram' or not isinstance(cfg.get('chat_id'),int) or not isinstance(cfg.get('bot_id'),int):raise ValueError('Настройте свой проверенный приватный чат')
        me=call('getMe',{});chat=call('getChat',{'chat_id':cfg['chat_id']})
        if me['id']!=cfg['bot_id'] or chat['id']!=cfg['chat_id'] or chat['type']!='private':raise ValueError('Получатель не совпал с настроенным')
        if file.stat().st_size>50*1024*1024:raise ValueError('Файл больше допустимого размера для отправки')
        caption=v.get('caption','')
        if not path.exists():
            pending={'status':'uncertain','sha256':digest,'chat_id':cfg['chat_id'],'bot_id':cfg['bot_id'],'delivery_id':digest+':'+a.variant,'at':studio.now()};studio.write(path,pending)
            response=call('sendDocument',{'chat_id':cfg['chat_id'],'caption':caption if len(caption)<=1024 else v.get('title',file.name)},file)
            if response.get('document',{}).get('file_size')!=file.stat().st_size or studio.sha(file)!=digest:raise ValueError('Доставленный файл не подтверждён')
            r={**pending,'status':'sent','message_id':response['message_id']};studio.write(path,r)
        outputs=[path]
        if len(caption)>1024:
            for i in range(0,len(caption),3800):
                chunk=caption[i:i+3800];receipt=folder/f'description-{i//3800:03d}.json';outputs.append(receipt)
                if receipt.exists():
                    status=studio.read(receipt).get('status')
                    if status=='sent':continue
                    if status=='confirmed_absent':
                        inspection=studio.read(studio.inside(a.retry_inspection)) if a.retry_inspection else {}
                        if inspection.get('receipt_sha256')!=studio.sha(receipt) or not inspection.get('reason'):raise ValueError('Перед повтором описания нужна проверка, что его нет')
                        receipt.rename(receipt.with_name(receipt.stem+'-absent-'+str(len(list(folder.glob(receipt.stem+'-absent-*'))))+'.json'))
                    else:raise ValueError('Доставка описания не подтверждена: проверьте до повтора')
                studio.write(receipt,{'status':'uncertain','text_sha256':studio.canon(chunk),'chat_id':cfg['chat_id']})
                response=call('sendMessage',{'chat_id':cfg['chat_id'],'text':chunk});studio.write(receipt,{'status':'sent','message_id':response['message_id'],'text_sha256':studio.canon(chunk),'chat_id':cfg['chat_id']})
        studio.complete(ep,'delivery:'+a.variant,{'sha256':digest,'caption':studio.canon(caption)},outputs);print(json.dumps(studio.read(path),ensure_ascii=False))
if __name__=='__main__':main()
