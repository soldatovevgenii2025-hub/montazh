#!/usr/bin/env python3
"""Project-local runtime; public model files only, never credentials or paid inference."""
import argparse, hashlib, json, os
from pathlib import Path
import shutil, subprocess, sys, urllib.request, venv, wave

ROOT=Path(__file__).resolve().parents[1]
# Поддерживаемый Python: на 3.14 нет готовых колёс закреплённого numpy 2.2.6.
PY_MIN=(3,11);PY_MAX=(3,13)
def edition_pro():
    try:return json.loads((ROOT/'.video-skill-project.json').read_text()).get('edition')=='pro'
    except (OSError,ValueError):return False
def run(args,**kw):subprocess.run([str(x) for x in args],check=True,**kw)
def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()
class ChecksumError(RuntimeError):pass
def download(url,path,expected=None,mirrors=()):
    """Скачать файл модели: сначала основной источник, потом зеркала по порядку.
    Недоступный источник пропускаем; несовпадение SHA256 - остановка, другой файл не подставляем."""
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_file() and (not expected or digest(path)==expected):return
    tmp=path.with_suffix(path.suffix+'.partial');errors=[]
    for source in [url,*mirrors]:
        try:
            with urllib.request.urlopen(source,timeout=90) as r,tmp.open('wb') as f:shutil.copyfileobj(r,f)
        except (OSError,ValueError) as e:
            tmp.unlink(missing_ok=True);errors.append(f'{source}: {e}')
            print('Источник недоступен, пробую следующий:',source,'-',e,file=sys.stderr);continue
        if expected and digest(tmp)!=expected:
            tmp.unlink(missing_ok=True)
            raise ChecksumError('Контрольная сумма модели не совпала: '+path.name+' (источник '+source+'). Модель не подменяем: запустите команду ещё раз, если снова не сошлось - остановитесь и напишите автору навыка.')
        tmp.replace(path);return
    raise RuntimeError('Не удалось скачать '+path.name+' ни из одного источника:\n'+'\n'.join(errors))
SYSTEM_BROWSERS={
 'darwin':['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome','/Applications/Chromium.app/Contents/MacOS/Chromium'],
 'linux':['google-chrome','google-chrome-stable','chromium','chromium-browser']}
def system_browser(platform=None,which=shutil.which,exists=os.path.isfile):
    """Путь к системному Chrome/Chromium или None."""
    platform=platform or ('darwin' if sys.platform=='darwin' else 'linux')
    for name in SYSTEM_BROWSERS.get(platform,[]):
        if name.startswith('/'):
            if exists(name):return name
        else:
            found=which(name)
            if found:return found
    return None
def ensure_browser(runtime,config,runner=None,finder=None):
    """Браузер для рендера: штатный Remotion, а если его скачать нельзя - системный Chrome/Chromium.
    Путь к системному браузеру записывается в engine/runtime.json как browserExecutable."""
    runner=runner or run;finder=finder or system_browser
    data=json.loads(config.read_text()) if config.is_file() else {}
    try:
        runner(['npx','--no-install','remotion','browser','ensure'],cwd=runtime)
        if data.pop('browserExecutable',None):config.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        return None
    except (subprocess.CalledProcessError,OSError):
        browser=finder()
        if not browser:raise SystemExit('Не удалось скачать браузер для рендера (remotion browser ensure), и системный Chrome или Chromium не найден. Поставьте Google Chrome (macOS: brew install --cask google-chrome; Linux/WSL: sudo apt install chromium) и запустите команду снова.')
        data['browserExecutable']=browser;config.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        print('Браузер Remotion скачать не удалось, для рендера взят системный:',browser)
        return browser
INSTALL_HELP={
 'node':'macOS: brew install node@20; Linux/WSL: sudo apt install nodejs npm (нужна версия 20 или новее)',
 'npm':'ставится вместе с Node.js',
 'npx':'ставится вместе с Node.js',
 'ffmpeg':'macOS: brew install ffmpeg; Linux/WSL: sudo apt install ffmpeg',
 'ffprobe':'ставится вместе с FFmpeg'}
def preflight():
    """Что есть и чего нет. Ничего не устанавливает."""
    rows=[];ok=True
    py_ok=PY_MIN<=sys.version_info[:2]<=PY_MAX
    rows.append(('python','есть '+'.'.join(map(str,sys.version_info[:3])) if py_ok else 'нужна версия 3.11–3.13, сейчас '+'.'.join(map(str,sys.version_info[:3])),'macOS: brew install python@3.13, затем запускайте python3.13; Linux/WSL: sudo apt install python3.13 python3.13-venv (или 3.12)'))
    ok=ok and py_ok
    for cmd in ['node','npm','npx','ffmpeg','ffprobe']:
        found=bool(shutil.which(cmd));ok=ok and found
        rows.append((cmd,'есть' if found else 'не найден',INSTALL_HELP[cmd]))
    windows=os.name=='nt';ok=ok and not windows
    if windows:rows.append(('система','Windows','Запускайте в WSL2 (Ubuntu), не в Windows Python: код использует fcntl'))
    for name,state,hint in rows:print(f"{name:8} {state}"+('' if state.startswith('есть') else '  -> '+hint))
    return ok
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--models',action='store_true');ap.add_argument('--check',action='store_true',help='только проверить окружение');ap.add_argument('--whisper',choices=['small','medium'],default='small',help='модель распознавания: small по умолчанию, medium точнее на живой речи (около 1,5 ГБ)');a=ap.parse_args()
    ready=preflight()
    if a.check:raise SystemExit(0 if ready else 1)
    if not ready:raise SystemExit('Окружение не готово: поставьте недостающее по подсказкам выше и запустите команду снова.')
    runtime=ROOT/'runtime';runtime.mkdir(exist_ok=True);py=runtime/'venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not py.exists():venv.create(runtime/'venv',with_pip=True,symlinks=True)
    run([py,'-m','pip','install','-r',ROOT/'requirements-pro.txt' if (ROOT/'requirements-pro.txt').exists() and edition_pro() else ROOT/'requirements.txt'])
    shutil.copy2(ROOT/'engine/remotion/package.json',runtime/'package.json')
    run(['npm','install','--no-audit','--no-fund'],cwd=runtime)
    target=ROOT/'engine/runtime.json'
    if not target.exists():shutil.copy2(ROOT/'engine/runtime.example.json',target)
    ensure_browser(runtime,target)
    silence=runtime/'silence.wav'
    if not silence.exists():
        with wave.open(str(silence),'wb') as f:f.setnchannels(2);f.setsampwidth(2);f.setframerate(48000);f.writeframes(b'\0'*(48000*4))
    if a.models:
        # Pin downloads to a recorded revision and validate LFS objects. No latest-model substitution.
        lock=json.loads((ROOT/'model-lock.json').read_text())
        for model in lock['models']:
            if model.get('optional') and model.get('id')!=a.whisper:continue
            for f in model['files']:
                download(f['url'],ROOT/f['path'],f.get('sha256'),f.get('mirrors',[]))
        if (ROOT/'tools/finish_models.py').exists():run([py,ROOT/'tools/finish_models.py'])
    receipt={'python':str(py.relative_to(ROOT)),'model_files':[]}
    for p in sorted((runtime/'models').rglob('*')):
        if p.is_file():receipt['model_files'].append({'path':str(p.relative_to(ROOT)),'sha256':digest(p)})
    (runtime/'install-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    if (ROOT/'engine/cli.py').exists():run([sys.executable,ROOT/'engine/cli.py','preflight'])
    else:
        run([py,'-c','import faster_whisper,numpy,PIL'])
        print('Окружение готово. Дальше настройка по AUTOPILOT.md; платных вызовов не было.')
        if a.whisper=='medium':print('Модель medium скачана. Чтобы её использовать, укажите в профиле: "whisper_model": "runtime/models/whisper-medium"')
if __name__=='__main__':main()
