"""Small, explicit companion memory; task logs belong to dsh."""
import json
import re
import time
from pathlib import Path
import uuid

SECRET = re.compile(r'(?i)(?:\b(?:sk|ds)[-_][a-z0-9_-]{16,}|Bearer\s+[a-z0-9._-]{12,}|(?:api[_ -]?key|token|password|密码|密钥)\s*[=:：]\s*[^\s,，;；]{6,})')


def clean(text, limit=1000):
    return SECRET.sub('[已隐藏凭据]', str(text))[:limit]


class CompanionMemory:
    MAX_FACTS = 24
    MAX_MESSAGES = 12
    HISTORY_CHARS = 4000
    TTL = 7 * 86400

    def __init__(self, folder):
        self.path = Path(folder) / 'companion-memory.json'
        self.data = {'version': 1, 'facts': [], 'recent': [], 'summary': ''}
        try:
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            if loaded.get('version') == 1:
                self.data.update({k: loaded[k] for k in ('facts', 'recent', 'summary', 'summary_at') if k in loaded})
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self._bound()

    def _bound(self):
        now = time.time()
        if not isinstance(self.data['facts'],list): self.data['facts']=[]
        if not isinstance(self.data['recent'],list): self.data['recent']=[]
        self.data['facts'] = [dict(id=str(x.get('id', uuid.uuid4())), key=clean(x.get('key', ''), 32),
                                   text=clean(x.get('text', ''), 160), source='explicit-user')
                              for x in self.data['facts'] if isinstance(x, dict) and x.get('text')][-self.MAX_FACTS:]
        self.data['recent'] = [dict(role=x['role'], text=clean(x.get('text', ''), 800), at=float(x.get('at', 0)))
                               for x in self.data['recent'] if isinstance(x, dict) and x.get('role') in ('user', 'assistant')
                               and isinstance(x.get('at'), (int, float)) and now - x['at'] < self.TTL][-self.MAX_MESSAGES:]
        while sum(len(x['text']) for x in self.data['recent']) > self.HISTORY_CHARS:
            self.data['recent'].pop(0)
        self.data['summary'] = clean(self.data['summary'], 1000)
        stamp=self.data.get('summary_at',0)
        if not isinstance(stamp,(int,float)) or now-stamp>self.TTL:
            self.data['summary'] = ''

    def save(self):
        self._bound()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.path)

    def remember(self, text):
        if SECRET.search(text):
            return '这条里有凭据，我就不记进长期记忆啦。'
        key, separator, value = text.partition('=')
        if not separator:
            key, separator, value = text.partition('＝')
        key, value = (key.strip()[:32], value.strip()) if separator else ('', text.strip())
        if not value:
            return '告诉我要记住什么呀，例如：记住：称呼=主人。'
        value = clean(value, 160)
        facts = self.data['facts']
        old = next((x for x in facts if (key and x['key'] == key) or (not key and x['text'] == value)), None)
        if old:
            old['text'] = value
        else:
            facts.append({'id': str(uuid.uuid4()), 'key': key, 'text': value, 'source': 'explicit-user'})
        self.save()
        return '记住啦：' + (key + '=' if key else '') + value + '。哼，我记性才不差呢。'

    def forget(self, query):
        query = query.strip()
        if not query:
            return '要忘记哪一条呀？可以写“忘记：称呼”。'
        old = self.data['facts']
        self.data['facts'] = [x for x in old if query != x['key'] and query != x['id'] and query != x['text']]
        count = len(old) - len(self.data['facts'])
        self.save()
        return f'已经忘记 {count} 条啦。' if count else '没找到这条记忆，输入“记忆”可以看看。'

    def add_turn(self, user, reply):
        now = time.time()
        rows = self.data['recent'] + [{'role': 'user', 'text': clean(user, 800), 'at': now},
                                      {'role': 'assistant', 'text': clean(reply, 800), 'at': now}]
        removed = []
        while len(rows) > self.MAX_MESSAGES or sum(len(x['text']) for x in rows) > self.HISTORY_CHARS:
            removed.append(rows.pop(0))
        if removed:
            fragments = [f"{x['role']}: {x['text'][:100]}" for x in removed]
            self.data['summary'] = (self.data['summary'] + '\n' + '\n'.join(fragments))[-1000:]
            self.data['summary_at'] = now
        self.data['recent'] = rows
        self.save()

    def context(self, query=''):
        self._bound()
        # Keep the addressed preference, names and latest explicit corrections
        # ahead of older unrelated facts in the 1200-character memory budget.
        facts=sorted(enumerate(self.data['facts']),key=lambda pair:(
            bool(pair[1]['key'] and pair[1]['key'] in query),
            pair[1]['key'] in ('称呼','名字','姓名','语言','偏好','爱好'),pair[0]),reverse=True)
        selected=[]; remaining=1200
        for _,fact in facts:
            size=len(fact['key'])+len(fact['text'])+2
            if size<=remaining: selected.append(fact); remaining-=size
        return {'facts': selected, 'summary': self.data['summary'],
                'history': [{'role': x['role'], 'text': x['text']} for x in self.data['recent']]}

    def describe(self):
        facts = self.data['facts']
        lines = [(x['key'] + '=' if x['key'] else '') + x['text'] for x in facts]
        return '我记住的事：\n' + '\n'.join(lines) if lines else '现在还没有长期记忆。你说“记住：称呼=主人”，我才会记下来。'

    def clear(self, long_term=False):
        if long_term:
            self.data['facts'] = []
        else:
            self.data['recent'] = []; self.data['summary'] = ''; self.data['summary_at'] = 0
        self.save()
