"""Add a marked, removable bridge row; retain the user's existing profile config."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil

parser = argparse.ArgumentParser()
parser.add_argument('--home', required=True)
parser.add_argument('--profiles', default='desktop')
args = parser.parse_args()
plugin = Path(__file__).resolve().parents[1] / 'app/dsh_plugin/index.mjs'
home = Path(args.home).resolve()
begin, end = '# whale-pet-bridge:begin', '# whale-pet-bridge:end'
matched = 0
for profile in args.profiles.split(','):
    if profile not in ('desktop', 'web'):
        raise SystemExit('Unsupported profile')
    path = home / 'profiles' / profile / 'cordis.patch.yml'
    if not path.parent.is_dir():
        continue
    matched += 1
    original = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    if 'id: whale-pet-bridge' in original and begin not in original:
        raise SystemExit('An unmarked bridge row already exists; preserve it for review.')
    value = re.sub(re.escape(begin) + r'.*?' + re.escape(end) + r'\s*', '', original, flags=re.S).rstrip()
    if value.strip() == '[]':
        value = ''
    row = f'{begin}\n- insert:\n    - id: whale-pet-bridge\n      name: {json.dumps(plugin.as_uri(), ensure_ascii=False)}\n{end}\n'
    updated = (value + '\n\n' if value else '') + row
    if original == updated:
        print(profile + ': already installed')
        continue
    if path.exists():
        backup = path.with_name(path.name + '.whale-pet-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.bak')
        shutil.copy2(path, backup)
    path.write_text(updated, encoding='utf-8')
    print(profile + ': bridge row installed (previous profile config backed up)')

if not matched:
    raise SystemExit('No compatible dsh profile found. Start dsh Desktop once and verify DSH_HOME.')
