"""Context-specific animation pools with cooldowns and bounded neglect."""
from collections import deque
from dataclasses import dataclass
import random
import re

@dataclass(frozen=True)
class Rule:
    name: str
    weight: float = 1.0
    cooldown: float = 60.0
    family: str = ''

IDLE_GAP = (7.0, 13.0)
WALK_GAP = (50.0, 85.0)
FIRST_IDLE_GAP = (4.0, 8.0)
WORK_ROTATION_GAP = (10.0, 18.0)

IDLE_RULES = (
    Rule('wave',1,45), Rule('stretch',1,60), Rule('curtsy',.9,60),
    Rule('tea',.8,60,'tea'), Rule('curious',1,45), Rule('dance',.7,75),
    Rule('sit',.8,65), Rule('tsun_proud',.7,75), Rule('tsun_peek',.7,55),
    Rule('idle_tea',.8,75,'tea'), Rule('idle_fishing',.55,120),
    Rule('idle_butterfly',.7,75), Rule('ds_basin',.4,150),
    Rule('swim',.8,95),
)
WORK_POOLS = {
    'thinking': (Rule('dsh_thinking_once',1,0),),
    'coding': (Rule('dsh_typing',1,0),),
    'reading': (Rule('curious',2,0), Rule('expr_neutral',1,25)),
    'searching': (Rule('curious',2,0), Rule('expr_neutral',1,25)),
    'executing': (Rule('dsh_typing',2,0), Rule('expr_neutral',1,25)),
    'working': (Rule('curious',2,0), Rule('expr_neutral',1,25)),
    'replying': (Rule('dsh_typing',3,0), Rule('expr_smile',1,30)),
    'delegating': (Rule('dsh_delegating',1,0),),
    'waiting': (Rule('dsh_waiting',2,0), Rule('expr_neutral',1,30)),
    'retrying': (Rule('dsh_thinking',2,0), Rule('expr_neutral',1,30)),
    'busy': (Rule('dsh_busy',2,0), Rule('dsh_thinking',1,30)),
}
SUCCESS_RULES = (Rule('dsh_success',3,45), Rule('happy',1,30), Rule('expr_smile',1,0))
ERROR_RULES = (Rule('tsun_hmph',2,90), Rule('expr_sad',1,0))

class BalancedChooser:
    """Choose only eligible clips; repay missed opportunities, never cooldowns.

    Deficit weighting supplies a target mix. Eligible clips that missed two
    pool rotations get priority. A family cooldown and two-clip repeat guard
    stop several names for the same action dominating the schedule.
    """
    def __init__(self, rules, rng=None):
        self.rules = tuple(rules)
        self.rng = rng or random.Random()
        self.last = {}
        self.family_last = {}
        self.debt = {r.name:0.0 for r in self.rules}
        self.missed = {r.name:0 for r in self.rules}
        self.recent = deque(maxlen=2)

    def note(self, name, now):
        rule = next((r for r in self.rules if r.name == name),None)
        if rule is None: return
        self.last[name] = now
        self.family_last[rule.family or name] = now
        self.missed[name] = 0
        if not self.recent or self.recent[-1] != name:
            self.recent.append(name)

    def choose(self, now, allowed=None):
        eligible = [r for r in self.rules if (allowed is None or r.name in allowed)
                    and now-self.last.get(r.name,-1e12)>=r.cooldown
                    and now-self.family_last.get(r.family or r.name,-1e12)>=r.cooldown]
        if not eligible: return None
        guarded = [r for r in eligible if r.name not in self.recent]
        # A single-state working loop is allowed to continue, without restarting.
        candidates = guarded or eligible
        total = sum(r.weight for r in eligible)
        for r in eligible:
            self.debt[r.name] += r.weight/total
            self.missed[r.name] += 1
        overdue = [r for r in candidates if self.missed[r.name]>=2*len(self.rules)]
        winner = max(overdue,key=lambda r:self.missed[r.name]) if overdue else max(candidates,key=lambda r:self.debt[r.name]+self.rng.random()*.03)
        self.debt[winner.name] -= 1
        self.note(winner.name,now)
        return winner.name


def tool_activity(name, arguments=None):
    """Classify the actual tool operation; unknown tools keep a neutral pose."""
    name = re.sub(r'([a-z0-9])([A-Z])',r'\1_\2',str(name))
    name = re.sub(r'[^a-z0-9]+','_',name.lower()).strip('_')
    args = arguments if isinstance(arguments,dict) else {}
    def has(pattern): return re.search(r'(?:^|_)(?:'+pattern+r')(?:_|$)',name) is not None
    if has('ask_user_question'): return 'waiting'
    # Harness's Minimal preset uses one editor for both viewing and changing files.
    if has('str_replace_editor'):
        return 'reading' if args.get('command')=='view' else 'coding'
    if has('list_subagent_models'): return 'reading'
    if re.search(r'(?:^|_)(?:subagent|spawn_agent|start_subagent|create_subagent|delegate|subagent_run|wait_subagent|wait_agent|send_message|interrupt_agent)$',name):
        return 'delegating'
    if has('search|web|fetch|browse|query'): return 'searching'
    if has('read|list|glob|grep|inspect|view|describe|stat_file'): return 'reading'
    if has('edit|write|patch|replace|create_file|apply_diff'): return 'coding'
    if has('bash|pwsh|powershell|shell|exec|terminal|run|command'):
        command = str(args.get('cmd',args.get('command',''))).strip()
        readonly = r'^(?:rg|ls|dir|cat|type|pwd|findstr|Get-Content|Get-ChildItem|Get-Location|Select-String)\b|^git\s+(?:status|diff|log|show)\b'
        # Compound commands can write after a read; keep them in execution.
        if command and not re.search(r'[;|&>]',command) and re.search(readonly,command,re.I): return 'reading'
        return 'executing'
    return 'working'

def is_service_busy(data):
    error = data.get('error',data) if isinstance(data,dict) else {}
    if not isinstance(error,dict): error = {'message':str(error)}
    values = ' '.join(str(error.get(k,'')) for k in ('code','status','statusCode','message')).lower()
    return any(word in values for word in ('429','503','rate_limit','rate limit','overloaded','server busy'))
