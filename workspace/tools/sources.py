import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
ROOT = Path(__file__).resolve().parent.parent
FORMATS = ('cartoon', 'avatar')
HANDLE = re.compile('^[a-z0-9._]{1,30}$')
CODE = re.compile('^[A-Za-z0-9_-]+$')

def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def parse_url(url):
    u = urlsplit(url.strip())
    if u.scheme != 'https' or u.hostname not in ('instagram.com', 'www.instagram.com') or u.username or u.password or u.port:
        raise ValueError('Нужна публичная ссылка https на профиль или Reel в Instagram')
    parts = [p for p in u.path.split('/') if p]
    if len(parts) == 2 and parts[0] in ('p', 'reel', 'reels') and CODE.fullmatch(parts[1]):
        return {'kind': 'reel', 'key': 'instagram:' + parts[1], 'url': f'https://www.instagram.com/reel/{parts[1]}/'}
    if len(parts) == 1 and HANDLE.fullmatch(parts[0].lower()) and (parts[0].lower() not in ('reels', 'explore', 'stories', 'accounts', 'direct', 'p', 'reel')):
        handle = parts[0].lower()
        return {'kind': 'profile', 'handle': handle, 'url': f'https://www.instagram.com/{handle}/'}
    raise ValueError('Такая ссылка Instagram не поддерживается: сначала раскройте короткую ссылку')

def normalized_handle(value):
    value = value.lstrip('@').lower().strip()
    if not HANDLE.fullmatch(value):
        raise ValueError('Некорректное имя автора')
    return value

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
        temporary = Path(f.name)
    temporary.replace(path)

@contextmanager
def registry(root, format_name):
    if format_name not in FORMATS:
        raise ValueError('Неизвестный формат')
    path = root / 'config' / f'montazh-{format_name}-competitors.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with (root / 'config' / '.creator-sources.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.exists() else {'schema': 'creator-competitors-v1', 'format': format_name, 'creators': {}}
        yield data
        write_json(path, data)

def upsert(root, format_name, url, handle=None, evidence=None, event_ref=None, language=None):
    source = parse_url(url)
    if not evidence or not event_ref:
        raise ValueError('Нужны проверенные метаданные и ссылка на исходное сообщение')
    if source['kind'] == 'reel' and (not handle):
        raise ValueError("Определите автора ролика до сохранения: код ролика не имя автора")
    handle = normalized_handle(handle or source['handle'])
    if source['kind'] == 'profile' and source['handle'] != handle:
        raise ValueError('Имя автора не совпало с присланным профилем')
    with registry(root, format_name) as data:
        creator = data['creators'].setdefault(handle, {'platform': 'instagram', 'handle': handle, 'profile_url': f'https://www.instagram.com/{handle}/', 'first_seen_at': timestamp(), 'examples': [], 'observations': [], 'ranking_history': [], 'queue': []})
        if language:
            creator['language'] = language
        observation = {'event_ref': event_ref, 'input_url': source['url'], 'evidence': evidence}
        if not any((all((x.get(k) == v for k, v in observation.items())) for x in creator['observations'])):
            creator['observations'].append({**observation, 'observed_at': timestamp()})
            creator['last_seen_at'] = timestamp()
        if source['kind'] == 'reel' and source['url'] not in creator['examples']:
            creator['examples'].append(source['url'])
        return {'handle': handle, 'format': format_name, 'input': source, 'creator_count': len(data['creators'])}

def ranked_snapshot(snapshot):
    for key in ('handle', 'captured_at', 'provider', 'metric_basis', 'coverage', 'reels'):
        if key not in snapshot:
            raise ValueError(f'Нет снимка: {key}')
    normalized_handle(snapshot['handle'])
    coverage = snapshot['coverage']
    if not isinstance(coverage, dict) or type(coverage.get('complete')) is not bool or (not coverage.get('description')):
        raise ValueError('Нужны признак полноты выборки и понятное описание её границы')
    parsed = datetime.fromisoformat(snapshot['captured_at'].replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('У времени замера должен быть часовой пояс')
    if not snapshot['provider'] or not snapshot['metric_basis']:
        raise ValueError('Назовите источник данных и что считается просмотром')
    seen, unknown = ({}, [])
    for item in snapshot['reels']:
        source = parse_url(item['url'])
        if source['kind'] != 'reel':
            raise ValueError('В рейтинге есть ссылка не на Reel')
        if item.get('author_handle') and normalized_handle(item['author_handle']) != normalized_handle(snapshot['handle']):
            raise ValueError("В рейтинге есть ролик другого автора")
        if item.get('metric_basis', snapshot['metric_basis']) != snapshot['metric_basis']:
            raise ValueError('Не смешивайте просмотры, прослушивания, лайки и счётчики с разным смыслом')
        count = item.get('views')
        if count is None:
            unknown.append(source['url'])
            continue
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError('Просмотры: целое число не меньше нуля или null, без округлённого текста')
        record = {**item, 'url': source['url'], 'source_key': source['key']}
        if source['key'] in seen and seen[source['key']]['views'] != count:
            raise ValueError('У одного ролика в снимке два разных счётчика')
        seen[source['key']] = record
    ranked = sorted(seen.values(), key=lambda x: (-x['views'], x['source_key']))[:10]
    for n, item in enumerate(ranked, 1):
        item['rank'] = n
    complete = coverage['complete'] and (not unknown)
    return {'captured_at': snapshot['captured_at'], 'provider': snapshot['provider'], 'metric_basis': snapshot['metric_basis'], 'coverage': coverage, 'ranking_label': 'top_by_views_of_complete_returned_archive' if complete else 'top_by_views_of_available_sample', 'measured_reels': len(seen), 'unknown_view_urls': sorted(set(unknown)), 'top': ranked}

def rank(root, format_name, snapshot_path):
    raw = snapshot_path.read_bytes()
    snapshot = json.loads(raw)
    ranking = ranked_snapshot(snapshot)
    handle = normalized_handle(snapshot['handle'])
    ranking['snapshot_sha256'] = hashlib.sha256(raw).hexdigest()
    ranking['snapshot_path'] = str(snapshot_path.resolve())
    try:
        ranking['snapshot_path'] = str(snapshot_path.resolve().relative_to(root.resolve()))
    except ValueError:
        pass
    with registry(root, format_name) as data:
        creator = data['creators'].get(handle)
        if not creator:
            raise ValueError('Сначала сохраните конкурента, потом ранжируйте')
        if not any((x['snapshot_sha256'] == ranking['snapshot_sha256'] for x in creator['ranking_history'])):
            creator['ranking_history'].append(ranking)
        old = {x['source_key']: x for x in creator['queue']}
        current = [{**x, 'state': old.get(x['source_key'], {}).get('state', 'candidate'), 'reel_id': old.get(x['source_key'], {}).get('reel_id')} for x in ranking['top']]
        current_keys = {x['source_key'] for x in current}
        current += [x for x in creator['queue'] if x['source_key'] not in current_keys and x['state'] != 'candidate']
        creator['queue'] = current
        creator['latest_ranking_sha256'] = ranking['snapshot_sha256']
    return ranking

def mark(root, format_name, handle, url, state, reel_id=None):
    source = parse_url(url)
    if state not in ('reserved', 'delivered', 'skipped'):
        raise ValueError('Состояние очереди: reserved, delivered или skipped')
    if state in ('reserved', 'delivered') and (not reel_id):
        raise ValueError('Привяжите выпуск к настоящему id ролика')
    with registry(root, format_name) as data:
        creator = data['creators'][normalized_handle(handle)]
        entry = next((x for x in creator['queue'] if x['source_key'] == source.get('key')))
        entry.update(state=state, reel_id=reel_id)
        return entry

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--format', choices=FORMATS, required=True)
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('add')
    q.add_argument('url')
    q.add_argument('--handle')
    q.add_argument('--language')
    q.add_argument('--evidence', required=True)
    q.add_argument('--event-ref', required=True)
    q = sub.add_parser('rank')
    q.add_argument('snapshot', type=Path)
    q = sub.add_parser('mark')
    q.add_argument('handle')
    q.add_argument('url')
    q.add_argument('state', choices=('reserved', 'delivered', 'skipped'))
    q.add_argument('--reel-id')
    a = p.parse_args()
    if a.command == 'add':
        result = upsert(a.root, a.format, a.url, a.handle, a.evidence, a.event_ref, a.language)
    elif a.command == 'rank':
        result = rank(a.root, a.format, a.snapshot)
    else:
        result = mark(a.root, a.format, a.handle, a.url, a.state, a.reel_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
if __name__ == '__main__':
    main()
