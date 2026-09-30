#!/usr/bin/env python3
"""蓝色大肥鱼桌宠 (Whale-maid desktop pet) - PySide6.

Transparent, frameless, always-on-top window that plays sprites from assets/atlas.png + atlas.json.
Behaviour: idle breathing + random blinks; random idle behaviours (wave / stretch / curtsy / tea / curious /
dance / sit, and now and then a tsundere 'proud' pose or a sneaky peek); random walks along the bottom of the
screen (occasionally trips). Tsundere interactions (all tunable in the configuration block below):
  click = petted (the first 1-2 clicks); more clicks within ~3 s or holding the mouse on her head > 2 s = 'hmph';
  6 quick pokes = flustered pointing ('stop poking!') -> angry; double-click = happy jump;
  hovering near her > 1.5 s = she peeks at you; ignored for 2-3 min = she taps the screen (or peeks);
  ~5 min without interaction = sleepy -> sleep (click wakes her);
  drag = dangling; dragged > 3 s = flustered dangle, after landing 'hmph';
  idle play (random idle actions + the 'Play' menu): brewing tea, fishing (sits on a stool for 8-15 s, a click
  interrupts it like any other animation), catching a butterfly (caught or it escapes);
  DeepSeek 鲸鱼娘 meme set ('蓝色大肥鱼'): a hunger meter (hungry after ~40 awake minutes) -> she begs with an
  empty rice bowl (又要到饭了), also once around lunch / dinner time; feed her rice (投喂 -> 白米饭) -> 暴风吸饭 ->
  happy; begging ignored ~3 times -> she sulks with the bowl upside-down on her head. Clicking her TAIL (own hit
  zone, beats petted) -> 不许摸尾巴！ tail slams. When you leave the computer alone for 60-100 s she secretly nibbles
  a gold token; move the mouse and she hides it behind her back, whistling. Rarely: 君权盆授 (steel basin crown).
  Round 2: clicking her BELLY (apron zone; tail > belly > head) -> 我不是大肥鱼！ stomps, then secretly nibbles an
  onigiri; now and then she drifts off into 深度思考 (thought cloud -> rice bowl, '才、才没有在想吃的！'); 外包然后睡觉
  (folds a paper airplane, throws it away and naps; a click wakes her); click spam (still clicking after the 6-poke
  chain, or >= 10 clicks in 6 s) -> 服务器繁忙 sign, clicks ignored during it + 5 s; woken from sleep by a click:
  30 % 震惊瘫坐 (shock plop) instead of the normal wake.
  right-click menu: Interact (praise / ask for a cookie / poke / hand her some work / poke her tummy), Feed (taiyaki /
  rice), Play (tea / fishing / butterfly / basin / token / deep think / server busy / shock), Expressions, Actions,
  size, speech bubbles on/off, Sleep, Quit. Speech bubbles are drawn by the app (not baked into the frames).
"""
import json, os, random, sys, time, math, hashlib
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, QPoint, QPointF, QRect, QRectF, QElapsedTimer
from PySide6.QtGui import (QPixmap, QPainter, QAction, QCursor, QGuiApplication, QIcon, QImage, QFont,
                           QFontMetrics, QPainterPath, QPen, QColor, QPolygonF, QImageReader)
from PySide6.QtWidgets import QApplication, QWidget, QMenu, QSystemTrayIcon, QMessageBox
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from pet_support import FrameCache, LazyFrames, PngFrames, Preferences, configure_fonts
from animation_policy import IDLE_RULES, BalancedChooser, IDLE_GAP, WALK_GAP, FIRST_IDLE_GAP


def resource_dir():
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'assets')


EXPRESSIONS = [('neutral', '平静'), ('smile', '微笑'), ('laugh', '大笑'), ('blush', '害羞'), ('angry', '生气'),
               ('sad', '难过'), ('surprised', '惊讶'), ('sleepy', '困'), ('wink', '眨眼'), ('smug', '得意')]
# ------------------------------------------------------------------ behaviour configuration (tsundere personality)
SLEEP_TIMEOUT = 300.0              # s without interaction -> sleepy -> sleep
ATTENTION_FIRST = (120.0, 180.0)   # s without interaction -> she asks for attention (poke_screen / tsun_peek)
ATTENTION_REPEAT = (150.0, 240.0)    # ...and again every this many s until she falls asleep
ATTENTION_PEEK_CHANCE = 0.3        # share of the attention calls that are a sneaky tsun_peek instead of poke_screen
PET_CLICKS_OK = 2                  # the first N clicks of a click chain are enjoyed (petted)
PET_REPEAT_WINDOW = 3.0            # a click within this many s of the previous one continues the chain -> tsun_hmph
POKE_ANGRY_COUNT = 6               # this many clicks within POKE_WINDOW s -> tsun_point ('stop poking!') -> angry
POKE_WINDOW = 8.0
HOLD_HMPH_SECONDS = 2.0            # mouse held down on her head (no drag) this long -> tsun_hmph
HEAD_ZONE_Y = 140                  # a press above this cell y (px at 100 %) counts as 'on her head'
HOVER_PEEK_SECONDS = 1.5           # cursor near her without clicking this long -> tsun_peek
HOVER_MARGIN = 24                  # 'near' = inside the pet window grown by this many px (at 100 %)
HOVER_COOLDOWN = 90.0              # s before hovering can trigger the next peek (also after any click)
DRAG_FLUSTER_SECONDS = 3.0         # dragged longer than this -> flustered dangle; after landing -> tsun_hmph
SPEECH_BUBBLES = True              # tiny speech bubbles above her head (menu toggle)
BUBBLE_SECONDS = 2.2
BUBBLE_FONT_PX = 12                # at 100 %; never smaller than 10 px on screen
BUBBLE_FONTS = ['Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', 'Hiragino Sans GB', 'Noto Sans CJK SC',
                'Source Han Sans SC', 'WenQuanYi Micro Hei', 'Noto Serif CJK SC', 'sans-serif']
SPEECH = {'tsun_hmph': ['哼！', '才、才不是喜欢被摸呢！'], 'tsun_proud': ['才、才不是为了你呢'], 'tsun_point': ['别戳啦！'],
          'eat_taiyaki': ['好吃…'], 'gift_cookie': ['给、给你的。'], 'poke_screen': ['喂…理理我嘛'],
          'tsun_peek': ['才没有在看你！'], 'praised_shy': ['诶、诶嘿…'], 'dangle_tsun': ['放、放我下来！'],
          'idle_tea': ['呼…好香'], 'idle_fishing': ['钓到啦！'], 'idle_butterfly': ['抓到了～'],
          'idle_butterfly_miss': ['才、才不是没抓到！'],
          'ds_rice_slurp': ['干饭！'], 'ds_basin': ['君权盆授！'], 'ds_token_hide': ['我、我什么都没吃！'],
          'ds_tail_touch': ['不许摸尾巴！'], 'ds_beg_rice': ['还要…'], 'ds_bowl_sulk': ['哼，不给就算了'],
          'ds_not_fat': ['我不是大肥鱼！'], 'ds_deep_think': ['已深度思考…'], 'ds_outsource_nap': ['这活交给别人了～'],
          'ds_server_busy': ['好、好了，恢复服务！'], 'ds_shock_sit': ['欸——？！']}
# a second bubble later in the same state (rice: '干饭！' at the start, '嗝～' when the slurping ends = the burp)
SPEECH2 = {'ds_rice_slurp': ['嗝～'], 'ds_not_fat': ['…嚼嚼'], 'ds_deep_think': ['才、才没有在想吃的！']}
SPEAK2_AT = {'ds_rice_slurp': 'release', 'ds_not_fat': 'release', 'ds_deep_think': 'release'}
# when the bubble of a state appears: 'start' (default), 'section' (when its loop section is reached), 'release'
# (when the loop section ends, e.g. the peek speaks when she is caught, the angler when the fish bites) or 'frame'
# (when the frame given by the atlas 'speak_frame' is reached, e.g. the paper-airplane throw)
SPEAK_AT = {'tsun_peek': 'release', 'idle_tea': 'section', 'idle_fishing': 'release', 'idle_butterfly': 'section',
            'idle_butterfly_miss': 'section', 'ds_basin': 'section', 'ds_beg_rice': 'section',
            'ds_outsource_nap': 'frame', 'ds_server_busy': 'release'}
# how long the loop section of each tsundere animation is held (s, from the start of the state; 0 = play it once)
TSUN_LEN = {'tsun_hmph': 1.6, 'tsun_proud': 3.0, 'tsun_peek': 2.2, 'tsun_point': 1.6, 'poke_screen': 0.0,
            'eat_taiyaki': 4.0, 'praised_shy': 3.2, 'gift_cookie': 2.4}
TSUN_STATES = tuple(TSUN_LEN)
# idle play: how long the loop section is held (random range in s, counted from the moment the section starts):
# tea = sipping, fishing = sitting on the stool waiting for a bite (the longest), butterfly = it rests on her finger
# (caught) / she huffs (missed)
PLAY_HOLD = {'idle_tea': (2.5, 4.5), 'idle_fishing': (8.0, 15.0), 'idle_butterfly': (1.5, 3.0),
             'idle_butterfly_miss': (1.2, 2.0)}
BUTTERFLY_CATCH_CHANCE = 0.6       # the butterfly is caught this often, otherwise it escapes (tsundere huff)
PLAY_STATES = tuple(PLAY_HOLD)
# ------------------------------------------------------------------ DeepSeek 鲸鱼娘 meme set (蓝色大肥鱼)
# loop-section hold of each meme animation (random range in s, counted from the moment the section starts)
DS_HOLD = {'ds_rice_slurp': (1.6, 2.4), 'ds_basin': (2.0, 3.2), 'ds_token_nibble': (4.0, 8.0),
           'ds_token_hide': (2.0, 2.0), 'ds_beg_rice': (2.4, 3.6), 'ds_bowl_sulk': (2.0, 2.8), 'ds_tail_touch': (0.0, 0.0)}
# ---- round 2 (我不是大肥鱼！ / 深度思考 / 外包然后睡觉 / 服务器繁忙 / 震惊瘫坐): loop-section holds (s, random range)
#   deep think = dreaming of the rice bowl, outsource = the nap on the floor, busy = the sign, shock = sitting stunned
DS_HOLD.update({'ds_not_fat': (0.0, 0.0), 'ds_deep_think': (1.6, 2.6), 'ds_outsource_nap': (6.0, 12.0),
                'ds_server_busy': (2.4, 3.6), 'ds_shock_sit': (1.5, 2.5)})
NOT_FAT_STOMPS = (2, 3)            # 我不是大肥鱼！ (belly click): stomps (loop-section cycles, random), then the onigiri
SPAM_CLICKS = 10                   # this many clicks within SPAM_WINDOW s -> 服务器繁忙; so does any click that keeps
SPAM_WINDOW = 6.0                  # coming after the 6-poke chain (while she points 'stop poking!' / pouts)
BUSY_COOLDOWN = 5.0                # s after 服务器繁忙 during which clicks stay ignored (as during it)
SHOCK_WAKE_CHANCE = 0.3            # a click that wakes her from sleep -> 震惊瘫坐 instead of the normal wake
DS_STATES = tuple(DS_HOLD)
TAIL_ALLOW_WAIT = 4.0              # active seconds of the offer; clicks confirm once
TAIL_ALLOW_STATES = ('ds_tail_allow', 'ds_tail_allow_touched', 'ds_tail_allow_timeout')
TAIL_SLAMS = (2, 3)                # 不许摸尾巴！: tail slams per touch (loop-section cycles, random)
# hunger meter: 1.0 = full ... 0.0 = hungry. Decays only while she is awake (not while sleepy / asleep)
HUNGRY_AFTER_MIN = 40.0            # awake minutes from full to hungry
BEG_EVERY = (60.0, 100.0)           # s between two begs (ds_beg_rice) from idle while she is hungry
BEG_FIRST = (2.0, 8.0)             # s from getting hungry to the first beg
BEG_IGNORE_SULK = 3                # this many begs in a row without food -> ds_bowl_sulk ('哼，不给就算了')
BEG_SULK_SECONDS = 90.0            # ...or begging for this long (first beg -> end of a beg) without food
BEG_AFTER_SULK = (300.0, 480.0)    # s of peace after a sulk before she begs again
BEG_STREAK_RESET = 300.0           # a beg more than this many s after the previous one starts a new ignore count
TAIYAKI_HUNGER = 0.35              # a taiyaki refills this much; rice (白米饭) fills her up (1.0)
RICE_AFTER = ('happy', 'dance')    # after the rice: happy jump or a little dance (random)
RICE_DANCE_SECONDS = 3.0
# meal time: in each window (local time, [start, end)) she begs once, unless she was fed recently
MEAL_WINDOWS = [((11, 30), (12, 30)), ((17, 30), (18, 30))]
RECENTLY_FED_MIN = 45.0
# 偷啃 token: plays only when the user has been idle (no clicks on her AND the mouse cursor has not moved)
TOKEN_IDLE = (60.0, 100.0)         # s of user idle (random per round) -> ds_token_nibble
TOKEN_MOVE_PX = 8                  # the cursor moving more than this (manhattan, screen px) counts as activity
TOKEN_HIDE_SECONDS = 2.0           # caught (cursor moved / any input): ds_token_hide loop this long, then idle
TOKEN_MENU_GRACE = 2.0             # started from the Play menu: cursor moves in the first s do not count
TRIP_CHANCE = 0.05           # probability that a walk ends in a trip
# random idle behaviours: (state, weight). 'idle' = just keep standing / breathing a while longer
IDLE_BEHAVIOURS = [(rule.name, rule.weight) for rule in IDLE_RULES]
# state -> default animation
STATE_ANIM = {'idle': 'idle', 'walk': 'walk_right', 'petted': 'petted', 'petted_end': 'petted_end', 'happy': 'happy_jump',
              'dangle': 'dangle', 'fall': 'fall', 'land': 'land', 'sleepy': 'sleepy', 'sleep': 'sleep', 'wake': 'wake',
              'angry': 'angry', 'sad': 'sad_cry', 'surprised': 'surprised', 'expr': 'expr_neutral', 'wave': 'wave',
              'curtsy': 'curtsy', 'stretch': 'stretch', 'tea': 'tea', 'curious': 'curious', 'dance': 'dance',
              'sit_down': 'sit_down', 'sit': 'sit', 'stand_up': 'stand_up', 'trip': 'trip'}
STATE_ANIM.update({k: k for k in TSUN_STATES + PLAY_STATES + DS_STATES})
LOOP_STATES = ('petted', 'angry', 'sad', 'expr', 'wave', 'tea', 'curious', 'dance', 'sit')      # end after state_len
ONESHOT_STATES = ('happy', 'land', 'surprised', 'curtsy', 'stretch', 'petted_end', 'sit_down', 'stand_up', 'trip',
                  'wake', 'sleepy') + TSUN_STATES + PLAY_STATES + DS_STATES   # entry, loop section (held), exit to idle
# Actions menu: (label, state, length)
ACTIONS = [('开心跳跃 Happy', 'happy', 0), ('摸头 Pet', 'petted', 2.0), ('生气 Angry', 'angry', 3.0), ('哭哭 Sad', 'sad', 3.5),
           ('惊讶 Surprised', 'surprised', 0), ('散步 Walk', 'walk', 0), ('挥手 Wave', 'wave', 2.4),
           ('提裙礼 Curtsy', 'curtsy', 0), ('伸懒腰 Stretch', 'stretch', 0), ('喝茶 Tea', 'tea', 5.0),
           ('好奇 Curious', 'curious', 4.0), ('跳舞 Dance', 'dance', 3.0), ('坐下 Sit', 'sit', 10.0),
           ('摔倒 Trip', 'trip', 0), ('睡觉 Sleep', 'sleepy', 0),
           ('哼！Hmph', 'tsun_hmph', 0), ('得意 Proud', 'tsun_proud', 0), ('偷看 Peek', 'tsun_peek', 0),
           ('指指 Point', 'tsun_point', 0), ('戳屏幕 Tap the screen', 'poke_screen', 0)]
# Interact menu: (label, kind)
INTERACT = [('夸夸她 Praise', 'praise'), ('要饼干 Ask for a cookie', 'cookie'), ('戳一下 Poke', 'poke'),
            ('派活给她 Hand her some work', 'outsource'), ('戳肚子 Poke her tummy', 'belly'), ('摸尾巴', 'tail')]
# Feed menu (投喂): (label, kind)
FEED = [('鲷鱼烧 Taiyaki', 'feed'), ('白米饭 Rice', 'rice')]
# Play menu (玩耍/待机): (label, kind)
PLAY = [('泡茶 Brew tea', 'tea'), ('钓鱼 Go fishing', 'fishing'), ('抓蝴蝶 Catch a butterfly', 'butterfly'),
        ('君权盆授 Basin crown', 'basin'), ('偷啃 token Nibble a token', 'token'),
        ('深度思考 Deep think', 'think'), ('服务器繁忙 Server busy', 'busy'), ('震惊 Shock', 'shock')]
PLAY_KIND = {'tea': 'idle_tea', 'fishing': 'idle_fishing', 'butterfly': 'idle_butterfly', 'basin': 'ds_basin',
             'token': 'ds_token_nibble', 'think': 'ds_deep_think', 'busy': 'ds_server_busy', 'shock': 'ds_shock_sit'}
HOLD = dict(PLAY_HOLD, **DS_HOLD)
GRAB = (128, 50)             # grab point (cell coords) when picked up: top of the head
STATE_ANIM.update({'ds_code_eureka':'ds_code_eureka', 'dsh_success':'dsh_success'})
STATE_ANIM.update({name:name for name in TAIL_ALLOW_STATES})
ONESHOT_STATES += ('ds_code_eureka', 'dsh_success') + TAIL_ALLOW_STATES
SPEECH.update({'ds_code_eureka':['有了！'], 'dsh_success':['有了！完成啦～']})
SPEAK_AT.update({'ds_code_eureka':'frame', 'dsh_success':'frame', 'ds_tail_allow':'section',
                 'ds_tail_allow_touched':'frame', 'ds_tail_allow_timeout':'frame'})
SPEECH.update({'ds_tail_allow':['只、只能摸一下哦'], 'ds_tail_allow_touched':['呜…就、就一下！'],
               'ds_tail_allow_timeout':['哼，不摸拉倒']})
ACTIONS.append(('写代码 → 灵光一闪', 'ds_code_eureka', 0))


class Anim:
    def __init__(self, name, frames, fps, loop, root_motion=None, section=None, flips=None, speak_frame=None):
        self.name, self.frames, self.fps, self.loop = name, frames, fps, loop
        self.speak_frame = speak_frame                  # SPEAK_AT 'frame': the bubble appears at this frame
        self.flips = flips or [False] * len(frames)     # frame drawn mirrored (flip_x alias)
        # forward x step (cell px at scale 1) applied when frame i is shown: keeps the planted foot fixed on screen
        self.root_motion = root_motion
        # [a, b): frames that repeat while the state is held (entry frames before, exit-to-idle frames after)
        self.section = tuple(section) if section else None

    def __len__(self):
        return len(self.frames)


def page_zip(image):
    """release zip that ships an atlas page: atlas.png -> whale-pet-app.zip, atlas_N.png -> whale-pet-app-atlas-N.zip"""
    stem = os.path.splitext(image)[0]
    return 'whale-pet-app.zip' if stem == 'atlas' else f"whale-pet-app-atlas-{stem.split('_')[-1]}.zip"


class AtlasPageMissing(RuntimeError):
    def __init__(self, images, folder):
        self.images, self.folder = images, folder
        super().__init__(f"cannot load atlas page(s) {', '.join(images)} (put every atlas page next to atlas.json in {folder})")

    def zips(self):
        return sorted(set(page_zip(i) for i in self.images))


class Sprites:
    def __init__(self, folder):
        with open(os.path.join(folder, 'atlas.json'), encoding='utf-8') as source:
            meta = json.load(source)
        plist = meta['meta'].get('pages', [{'image': meta['meta']['image']}])
        bad = [pg['image'] for pg in plist if not QImageReader(os.path.join(folder, pg['image'])).canRead()]
        if bad:                      # name EVERY missing page (and the zip it ships in), not just the first one
            raise AtlasPageMissing(bad, folder)
        self.cell = meta['meta']['cell']['w']
        self.baseline = meta['meta']['baseline_y']
        self.hit = meta['meta'].get('hit_boxes', {})
        self.cache = FrameCache(folder, plist, self.cell)
        self.anims = {}
        for name, a in meta['animations'].items():
            frames = LazyFrames(self.cache, a['frames'])
            self.anims[name] = Anim(name, frames, a['fps'], a['loop'], a.get('root_motion_x'), a.get('loop_section'),
                                    [bool(f.get('flip_x')) for f in a['frames']], a.get('speak_frame'))
        extra = os.path.join(folder, 'extra', 'ds_code_eureka')
        with open(os.path.join(extra, 'timing.json'), encoding='utf-8') as source:
            timing = json.load(source)
        paths = [os.path.join(extra, f'{i:02d}.png') for i in range(timing['frames'])]
        missing = [path for path in paths if not os.path.isfile(path)]
        if missing:
            raise RuntimeError('Missing eureka animation frames: ' + ', '.join(missing))
        frames = PngFrames(self.cache, paths)
        self.anims['ds_code_eureka'] = Anim('ds_code_eureka', frames, timing['fps'], False, speak_frame=41)
        self.anims['dsh_typing'] = Anim('dsh_typing', frames[:25], timing['fps'], True)
        self.anims['dsh_thinking'] = Anim('dsh_thinking', frames[25:40], timing['fps'], True)
        self.anims['dsh_success'] = Anim('dsh_success', frames[40:76], timing['fps'], False, speak_frame=1)
        tail = os.path.join(folder, 'extra', 'ds_tail_allow')
        with open(os.path.join(tail, 'timing.json'), encoding='utf-8') as source:
            timing = json.load(source)
        paths = [os.path.join(tail, f'{i:02d}.png') for i in range(timing['frames'])]
        missing = [path for path in paths if not os.path.isfile(path)]
        if missing:
            raise RuntimeError('Missing tail-allow animation frames: ' + ', '.join(missing))
        frames = PngFrames(self.cache, paths)
        self.anims['ds_tail_allow'] = Anim('ds_tail_allow', frames[:28], timing['fps'], False, section=(16,28))
        self.anims['ds_tail_allow_touched'] = Anim('ds_tail_allow_touched', frames[28:46], timing['fps'], False, speak_frame=1)
        self.anims['ds_tail_allow_timeout'] = Anim('ds_tail_allow_timeout', frames[46:60], timing['fps'], False, speak_frame=1)
        for alias,source_name in (('dsh_waiting','tsun_peek'),('dsh_busy','ds_server_busy')):
            source = self.anims[source_name]
            first,last = source.section
            self.anims[alias] = Anim(alias,LazyFrames(self.cache,source.frames.records[first:last]),source.fps,True)
        source = self.anims['ds_outsource_nap']
        self.anims['dsh_delegating'] = Anim('dsh_delegating',source.frames,source.fps,False,section=source.section)

    def __getitem__(self, k):
        return self.anims[k]


class Pet(QWidget):
    def __init__(self, sprites, scale=1.0, selftest=False, preferences=None):
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
        # Qt.Tool keeps the pet out of the Windows taskbar; on macOS tool windows hide when the app
        # loses focus, so use a normal window there.
        flags |= Qt.Window if sys.platform == 'darwin' else Qt.Tool
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('蓝色大肥鱼')
        self.sp = sprites
        self.scale = scale
        self.selftest = selftest
        self.preferences = preferences
        self.quiet = False
        self.walking = True
        self._paint_key = None
        self._tray = None
        self._assistant = None
        self.save_timer = QTimer(self, singleShot=True, timeout=self.save_preferences)
        self._resize()
        # state
        self.animation_origin = 'idle'
        self.input_revision = 0
        self.state = None
        self.anim = None
        self.t = 0.0                 # time inside the current animation
        self.state_t = 0.0           # time inside the current state
        self.state_len = 0.0
        self.next_blink = self._rand_blink()
        self.blink_t = -1.0
        self.idle_chooser = BalancedChooser(IDLE_RULES)
        self.next_walk = random.uniform(*WALK_GAP)
        self.next_behaviour = random.uniform(*FIRST_IDLE_GAP)
        self.walk_dir = 1
        self.walk_trip_at = None
        self.queue = []              # states to play after the current one-shot / loop: (state, anim, length)
        self.vy = 0.0
        self.expr = None
        self.last_input = time.monotonic()
        self.press_pos = None
        self.dragging = False
        self.pet_times = []           # click times inside POKE_WINDOW
        self.pet_chain = 0            # clicks in the current chain (each within PET_REPEAT_WINDOW of the previous)
        self.last_click = None
        self.fall_from = 0
        self.press_t = 0.0
        self.press_cell = None
        self.press_consumed = False   # the press already triggered something (hold -> hmph): the release is not a click
        self.drag_t0 = 0.0
        self.flustered = False
        self.after_land = None        # state to play after land (flustered drag -> tsun_hmph)
        self.next_attention = random.uniform(*ATTENTION_FIRST)
        self.hover_t = 0.0
        self.hover_block_until = 0.0
        self.menu_open = False
        self.bubbles_on = SPEECH_BUBBLES
        self.bubble = None            # (text, seconds left)
        self._cursor_override = None  # selftest: fake global cursor position
        self._force_attention = None  # selftest: deterministic attention choice
        self._force_behaviour = None  # selftest: deterministic random-idle choice
        self.sec_rel = None
        self._sec_cyc = 0
        # DeepSeek meme set: hunger, begging, meal time, token gate, tail zone
        self.hunger = 1.0
        self.next_beg = random.uniform(*BEG_FIRST)
        self.beg_ignored = 0
        self.first_beg = None
        self.last_beg_end = None
        self.last_fed = -1e9
        self.meal_done = None
        self._clock_override = None   # selftest: fake local time (datetime)
        self._auto_ds = True          # selftest: token / hunger / meal-time triggers off between the scripted steps
        self.last_cursor_move = time.monotonic()
        self._cur_anchor = None
        self.token_at = random.uniform(*TOKEN_IDLE)
        self.token_t0 = 0.0
        self.token_grace_until = 0.0
        self._token_menu = False
        self.click_cell = None
        # round 2: click spam -> server busy (+ cooldown), sleep-click shock RNG (seedable for the selftest)
        self.click_log = []           # accepted single-click times inside SPAM_WINDOW
        self._poke_hot = False        # the 6-poke chain just fired tsun_point: more clicks = spam
        self.busy_quiet_until = 0.0   # clicks ignored until this time (after 服务器繁忙)
        self.rng = random.Random()
        self._said_frame = False
        self.click_timer = QTimer(self, singleShot=True, timeout=self._single_click)
        self.clock = QElapsedTimer(); self.clock.start()
        self.last_ms = 0
        self.timer = QTimer(self, timeout=self._tick)
        self.timer.start(16)
        self.set_state('idle')
        self._place_on_ground(center=True)
        if preferences:
            self.restore_preferences(preferences.load())
        if not selftest:
            app = QGuiApplication.instance()
            app.screenAdded.connect(self._screens_changed)
            app.screenRemoved.connect(self._screens_changed)
            for screen in app.screens():
                self._watch_screen(screen)

    # ------------------------------------------------------------------ geometry helpers
    def _resize(self):
        s = int(round(self.sp.cell * self.scale))
        self.setFixedSize(s, s)

    def _screen_rect(self):
        point = self.geometry().center()
        scr = QGuiApplication.screenAt(point)
        if scr is None:
            scr = min(QGuiApplication.screens(), key=lambda s:
                      (s.availableGeometry().center() - point).manhattanLength())
        return scr.availableGeometry()

    def _ground_y(self):
        """window y at which the feet touch the bottom of the work area"""
        r = self._screen_rect()
        return r.bottom() + 1 - int(round(self.sp.baseline * self.scale))

    def _place_on_ground(self, center=False):
        r = self._screen_rect()
        x = r.center().x() - self.width() // 2 if center else self.x()
        self.move(x, self._ground_y())
        self._clamp_position()

    def _clamp_position(self, rect=None, ground=True):
        r = rect or self._screen_rect()
        # The cell has a little transparent padding below the feet.
        x = max(r.left(), min(self.x(), max(r.left(), r.right() + 1 - self.width())))
        y = r.bottom() + 1 - int(round(self.sp.baseline * self.scale)) if ground else max(r.top(), min(self.y(), self._ground_y()))
        self.move(x, y)

    def _watch_screen(self, screen):
        screen.availableGeometryChanged.connect(self._screens_changed)

    def _screens_changed(self, screen=None):
        if screen is not None and screen in QGuiApplication.screens():
            try:
                screen.availableGeometryChanged.disconnect(self._screens_changed)
            except RuntimeError:
                pass
            self._watch_screen(screen)
        if not self.dragging:
            self._clamp_position(ground=self.state not in ('dangle', 'fall'))
            self.schedule_save()

    # ------------------------------------------------------------------ portable preferences and visibility
    def restore_preferences(self, data):
        self.bubbles_on = data.get('bubbles', SPEECH_BUBBLES)
        self.quiet = data.get('quiet', False)
        self.walking = data.get('walking', True)
        self.hunger = data.get('hunger', 1.0)
        if 'fed_at' in data:
            self.last_fed = time.monotonic() - max(0.0, time.time() - data['fed_at'])
        self.meal_done = data.get('meal_done')
        screen = next((s for s in QGuiApplication.screens() if s.name() == data.get('screen')),
                      QGuiApplication.primaryScreen())
        r = screen.availableGeometry()
        self.move(data.get('x', r.center().x() - self.width() // 2), r.top())
        self._clamp_position(r)

    def schedule_save(self):
        if self.preferences and not self.selftest:
            self.save_timer.start(800)

    def save_preferences(self):
        if not self.preferences or self.selftest:
            return
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        data = {'scale': self.scale, 'bubbles': self.bubbles_on, 'quiet': self.quiet, 'walking': self.walking,
                'hunger': self.hunger, 'x': self.x(), 'screen': screen.name(), 'meal_done': self.meal_done}
        if self.last_fed > 0:
            data['fed_at'] = time.time() - max(0.0, time.monotonic() - self.last_fed)
        if self._assistant is not None:
            data.update(self._assistant.preferences())
        self.preferences.save(data)

    def set_quiet(self, on):
        self.quiet = bool(on)
        self._touch()
        if self.quiet and self.state not in ('sleep', 'sleepy', 'dangle', 'fall'):
            self.set_state('idle')
        self.bubble = None
        self.next_walk = random.uniform(*WALK_GAP)
        self.next_behaviour = random.uniform(*IDLE_GAP)
        self.update()
        self.schedule_save()

    def set_walking(self, on):
        self.walking = bool(on)
        if not self.walking and self.state == 'walk':
            self.set_state('idle')
        self.schedule_save()

    def recall(self, center=False):
        self.click_timer.stop()
        self.press_pos = self.press_cell = self.click_cell = None
        self.press_consumed = self.dragging = self.flustered = False
        self.after_land = None
        self.vy = 0.0
        self._touch()
        self.hover_block_until = time.monotonic() + HOVER_COOLDOWN
        self.set_state('idle')
        if center:
            r = QGuiApplication.primaryScreen().availableGeometry()
            self.move(r.center().x() - self.width() // 2, r.top())
            self._clamp_position(r)
        else:
            self._clamp_position()
        self.last_ms = self.clock.elapsed()
        self.timer.start(50)
        self.show()
        self.raise_()
        self.schedule_save()
        if self._assistant is not None:
            self._assistant.resume_animation()

    def hide_pet(self):
        if self._tray is None:
            return
        self.click_timer.stop()
        self.timer.stop()
        self.save_preferences()
        self.hide()

    def closeEvent(self, event):
        if self._tray is not None:
            self.hide_pet()
            event.ignore()
        else:
            self.save_preferences()
            event.accept()
            QApplication.quit()

    def show_help(self):
        QMessageBox.information(self, '蓝色大肥鱼 · 操作说明',
            '单击：摸头；尾巴和肚子有专属反应。\n双击：开心跳跃。\n按住拖动：拎起她，松手落地。\n'
            '右键：投喂、互动、玩耍、大小和陪伴设置。\n\n'
            '安静陪伴：保留呼吸和眨眼，暂停主动讨饭、求关注、散步和随机小动作；手动互动仍可用。\n'
            '隐藏后：双击系统托盘图标，或再次双击启动文件即可召回。\n'
            '大小、气泡、陪伴模式、饱腹度和位置会自动保存。')

    @staticmethod
    def _rand_blink():
        return random.uniform(2.0, 5.5)

    # ------------------------------------------------------------------ state machine
    def set_state(self, state, anim=None, length=0.0, keep_queue=False, origin=None):
        if origin is not None:
            self.animation_origin = origin
        elif state == 'idle':
            self.animation_origin = 'idle'
        if self.state == 'ds_server_busy' and state != 'ds_server_busy':     # recovered: clicks stay ignored a while
            self.busy_quiet_until = time.monotonic() + BUSY_COOLDOWN
        if state == 'idle':
            self._poke_hot = False
        if not keep_queue:
            self.queue = []
        self.state = state
        self.anim = self.sp[anim or STATE_ANIM[state]]
        self.t = 0.0
        self.state_t = 0.0
        self.state_len = length
        self.idle_chooser.note(state,time.monotonic())
        self.blink_t = -1.0
        self._rm_frame = 0            # last frame whose root motion was applied (frame 0 is shown on entry)
        self._rm_acc = 0.0
        self.sec_rel = None           # time at which the loop section was released (exit frames play from there)
        self._sec_cyc = 0
        self._sec_said = False
        self._said_frame = False
        if state in TAIL_ALLOW_STATES:
            self.bubble = None
            if state == 'ds_tail_allow' and not length:
                self.state_len = self.anim.section[0] / self.anim.fps + TAIL_ALLOW_WAIT
        if state == 'ds_token_nibble':
            self.token_t0 = time.monotonic()
            self.token_grace_until = self.token_t0 + (TOKEN_MENU_GRACE if self._token_menu else 0.0)
            self._token_menu = False
        if SPEAK_AT.get(state, 'start') == 'start':
            self.say(state)
        self.update()

    def say(self, key, table=SPEECH):
        if self.animation_origin == 'conversation':
            return  # Persona dialogue supplies the words for these reactions.
        lines = table.get(key)
        if lines and self.bubbles_on:
            self.bubble = [random.choice(lines), BUBBLE_SECONDS]

    def frame_index(self):
        a = self.anim
        i = int(self.t * a.fps)
        if a.loop:
            return i % len(a)
        if a.section:
            s0, s1 = a.section
            if self.sec_rel is None:
                if i >= s0:
                    i = s0 + (i - s0) % (s1 - s0)
            else:
                i = s1 + int((self.t - self.sec_rel) * a.fps + 1e-6)
        return min(i, len(a) - 1)

    def _section_step(self):
        """loop-section animations: keep repeating frames [a, b) until the state length is reached, then release at
        the next wrap so the exit frames play (no jump in the middle of the section)"""
        a = self.anim
        if not a.section or self.sec_rel is not None:
            return
        s0, s1 = a.section
        j = int(self.t * a.fps)
        if j >= s0 and not self._sec_said and SPEAK_AT.get(self.state) == 'section':
            self._sec_said = True
            self.say(self.state)
        if j < s1:
            return
        cyc = (j - s0) // (s1 - s0)
        if cyc != self._sec_cyc:
            self._sec_cyc = cyc
            if self.state_t >= self.state_len:
                self.sec_rel = (s0 + cyc * (s1 - s0)) / a.fps
                if SPEAK_AT.get(self.state) == 'release':
                    self.say(self.state)
                if SPEAK2_AT.get(self.state) == 'release':
                    self.say(self.state, SPEECH2)

    def _root_motion(self):
        """advance the window by the root motion of every frame shown since the last tick. The sprite only changes
        at the animation fps, so the window moves in the same discrete steps: the planted foot stays put on screen."""
        rm = self.anim.root_motion
        fi = self.frame_index()
        if not rm or fi == self._rm_frame:
            return self.x()
        k, adv = self._rm_frame, 0.0
        while k != fi:
            k = (k + 1) % len(rm)
            adv += rm[k]
        self._rm_frame = fi
        self._rm_acc += self.walk_dir * adv * self.scale
        step = int(round(self._rm_acc))
        self._rm_acc -= step
        return self.x() + step

    def anim_done(self):
        a = self.anim
        if a.loop:
            return False
        if a.section:
            return self.sec_rel is not None and a.section[1] + (self.t - self.sec_rel) * a.fps >= len(a)
        return self.t * a.fps >= len(a)

    def next_in_queue(self):
        if self.queue:
            st, an, ln = self.queue.pop(0)
            self.set_state(st, an, ln, keep_queue=True)
        else:
            self.set_state('idle')

    def start_behaviour(self, st, length=None):
        """start an idle behaviour / menu action, including its entry/exit transitions"""
        dflt = {'wave': random.uniform(1.6, 3.2), 'tea': random.uniform(4.0, 6.5), 'curious': 4.0,
                'dance': random.uniform(2.5, 4.0), 'sit': random.uniform(8, 16)}
        dflt.update(TSUN_LEN)
        ln = length if length else dflt.get(st, 0.0)
        if st == 'sit':
            self.set_state('sit_down')
            self.queue = [('sit', None, ln), ('stand_up', None, 0.0)]
        elif st == 'petted':
            self.set_state('petted', length=ln or 2.0)
            self.queue = [('petted_end', None, 0.0)]
        elif st == 'trip':
            self.set_state('trip', 'trip' if self.walk_dir < 0 else 'trip_right')
        elif st == 'walk':
            self._start_walk()
        elif st == 'ds_tail_touch':
            self.start_tail_touch()
        elif st == 'ds_not_fat':
            self.start_not_fat()
        elif st in ('idle_butterfly', 'idle_butterfly_miss') + PLAY_STATES + DS_STATES:
            self.play_state(st, length)
        else:
            self.set_state(st, length=ln)

    def play_state(self, st, hold=None, outcome=None):
        """idle play: the state length = time until the loop section starts + the hold time (PLAY_HOLD)"""
        if st == 'idle_butterfly':          # caught or escaped
            if outcome is None:
                outcome = 'catch' if random.random() < BUTTERFLY_CATCH_CHANCE else 'miss'
            st = 'idle_butterfly' if outcome == 'catch' else 'idle_butterfly_miss'
        self.set_state(st, length=self.hold_len(st, hold))

    def hold_len(self, st, hold=None):
        """state length = time until the loop section starts + the hold time (HOLD ranges)"""
        a = self.sp[STATE_ANIM[st]]
        s0 = a.section[0] / a.fps if a.section else 0.0
        return s0 + (hold if hold else random.uniform(*HOLD[st]))

    def play(self, kind, hold=None, outcome=None):
        """Play menu (玩耍/待机): tea / fishing / butterfly / basin / token"""
        self._touch()
        st = PLAY_KIND[kind]
        self._token_menu = st == 'ds_token_nibble'     # preview: moving the mouse off the menu is not 'caught'
        if self.state in ('sit', 'sit_down'):          # stand up first
            if kind == 'butterfly':
                oc = outcome or ('catch' if random.random() < BUTTERFLY_CATCH_CHANCE else 'miss')
                st = 'idle_butterfly' if oc == 'catch' else 'idle_butterfly_miss'
            ln = self.hold_len(st, hold)
            self.set_state('stand_up')
            self.queue = [(st, None, ln)]
            return
        self.play_state(st, hold, outcome)

    # ------------------------------------------------------------------ DeepSeek meme set helpers
    def local_now(self):
        return self._clock_override or datetime.now()

    def hungry(self):
        return self.hunger <= 0.0

    def fed(self, amount):
        """food: hunger up (1.0 = full); no longer hungry -> the begging streak is over"""
        self.hunger = min(1.0, self.hunger + amount)
        self.last_fed = time.monotonic()
        if not self.hungry():
            self.beg_ignored, self.first_beg = 0, None
            self.next_beg = random.uniform(*BEG_EVERY)
        self.schedule_save()

    def start_beg(self):
        now = time.monotonic()
        if self.last_beg_end is not None and now - self.last_beg_end > BEG_STREAK_RESET:
            self.beg_ignored, self.first_beg = 0, None
        if self.first_beg is None:
            self.first_beg = now
        self.play_state('ds_beg_rice')

    def _beg_done(self):
        """a beg played to its end without food: count it; ignored too often / too long -> sulk"""
        now = time.monotonic()
        self.last_beg_end = now
        self.beg_ignored += 1
        if self.beg_ignored >= BEG_IGNORE_SULK or (self.first_beg is not None and now - self.first_beg >= BEG_SULK_SECONDS):
            self.beg_ignored, self.first_beg = 0, None
            self.next_beg = random.uniform(*BEG_AFTER_SULK)
            self.play_state('ds_bowl_sulk')
        else:
            self.next_in_queue()

    def _meal_due(self):
        """(date, window) key when it is meal time, she has not begged in this window yet and was not fed recently"""
        t = self.local_now()
        hm = (t.hour, t.minute)
        for k, (a, b) in enumerate(MEAL_WINDOWS):
            if tuple(a) <= hm < tuple(b):
                key = (t.date().isoformat(), k)
                if key != self.meal_done and time.monotonic() - self.last_fed > RECENTLY_FED_MIN * 60:
                    return key
        return None

    def user_idle(self, now_m):
        return now_m - max(self.last_input, self.last_cursor_move)

    def _track_cursor(self, now_m):
        p = self._cursor_pos()
        if self._cur_anchor is None or (p - self._cur_anchor).manhattanLength() > TOKEN_MOVE_PX:
            self._cur_anchor = QPoint(p)
            self.last_cursor_move = now_m
            self.token_at = random.uniform(*TOKEN_IDLE)

    def hide_token(self):
        """caught nibbling the token: hard cut to the hands-behind-back whistle"""
        self.set_state('ds_token_hide', length=self.hold_len('ds_token_hide', TOKEN_HIDE_SECONDS))

    def tail_box(self, anim=None, frame=None):
        """tail click zone (cell px) of the current frame, or None when the frame has no tail zone; flip-aware"""
        return self.zone_box('tail', anim, frame)

    def in_tail_zone(self, cell):
        return self.in_zone('tail', cell)

    def start_tail_touch(self, slams=None):
        a = self.sp['ds_tail_touch']
        s0, s1 = a.section
        n = slams or random.randint(*TAIL_SLAMS)
        self.set_state('ds_tail_touch', length=(s0 + n * (s1 - s0)) / a.fps - 0.01)
        self.queue = [('ds_tail_allow', None, 0.0)]

    def tail_offer_ready(self):
        return (self.state == 'ds_tail_allow' and self.t * self.anim.fps >= self.anim.section[0]
                and self.state_t < self.state_len)

    def tail_sequence_active(self):
        return self.state in TAIL_ALLOW_STATES or (self.state == 'ds_tail_touch'
               and any(state == 'ds_tail_allow' for state, _, _ in self.queue))

    def finish_tail_allow(self, touched):
        if self.state == 'ds_tail_allow':
            self.set_state('ds_tail_allow_touched' if touched else 'ds_tail_allow_timeout')

    def consume_tail_click(self):
        if not self.tail_sequence_active():
            return False
        self.click_cell = None
        if self.tail_offer_ready():
            self.finish_tail_allow(True)
        return True

    # ------------------------------------------------------------------ round 2 helpers
    def zone_box(self, kind, anim=None, frame=None):
        """click zone 'tail' / 'belly' (cell px) of the current frame, or None when the frame has none; flip-aware"""
        hb = self.sp.hit
        a = anim or self.anim
        if kind not in hb or a.name not in hb.get(f'{kind}_anims', []):
            return None
        x0, y0, x1, y1 = hb[kind]
        i = self.frame_index() if frame is None else frame
        if a.flips[min(i, len(a.flips) - 1)]:
            x0, x1 = self.sp.cell - x1, self.sp.cell - x0
        return x0, y0, x1, y1

    def in_zone(self, kind, cell):
        b = self.zone_box(kind)
        return b is not None and cell is not None and b[0] <= cell[0] <= b[2] and b[1] <= cell[1] <= b[3]

    def not_fat_len(self, stomps=None):
        a = self.sp['ds_not_fat']
        s0, s1 = a.section
        n = stomps or random.randint(*NOT_FAT_STOMPS)
        return (s0 + n * (s1 - s0)) / a.fps - 0.01

    def start_not_fat(self, stomps=None):
        """我不是大肥鱼！ (belly poke): 2-3 stomps, then the onigiri nibble plays out"""
        self.set_state('ds_not_fat', length=self.not_fat_len(stomps))

    def in_nap(self):
        a = self.anim
        return (self.state == 'ds_outsource_nap' and a.section is not None and self.sec_rel is None
                and self.frame_index() >= a.section[0])

    def wake_nap(self):
        """a click during the outsourced nap: hard cut out of the nap loop into the wake frames (stretch -> idle)"""
        self.sec_rel = self.t

    def clicks_ignored(self, now=None):
        now = time.monotonic() if now is None else now
        return self.state == 'ds_server_busy' or now < self.busy_quiet_until

    def start_server_busy(self):
        self.click_log, self._poke_hot = [], False
        self.pet_times, self.pet_chain, self.last_click = [], 0, None
        self.queue = []
        self.play_state('ds_server_busy')

    def _start_walk(self):
        r = self._screen_rect()
        self.walk_dir = random.choice((-1, 1))
        if self.x() < r.left() + 80:
            self.walk_dir = 1
        if self.x() + self.width() > r.right() - 80:
            self.walk_dir = -1
        ln = random.uniform(2.5, 6)
        self.walk_trip_at = random.uniform(1.0, ln) if random.random() < TRIP_CHANCE else None
        self.set_state('walk', 'walk_right' if self.walk_dir > 0 else 'walk_left', ln)

    def _tick(self):
        now = self.clock.elapsed()
        dt = min(0.1, (now - self.last_ms) / 1000.0)
        self.last_ms = now
        if self.menu_open:
            return
        self.t += dt
        self.state_t += dt
        st = self.state
        now_m = time.monotonic()
        idle_for = now_m - self.last_input
        self._track_cursor(now_m)
        if st == 'idle' and self._assistant is not None and self._assistant.busy:
            self._assistant.resume_animation()
            st = self.state
        if st not in ('sleep', 'sleepy'):                  # hunger decays while she is awake
            was = self.hungry()
            self.hunger = max(0.0, self.hunger - dt / (HUNGRY_AFTER_MIN * 60.0))
            if self.hungry() and not was:
                self.next_beg = min(self.next_beg, random.uniform(*BEG_FIRST))
        if st == 'ds_token_nibble' and now_m >= self.token_grace_until and \
                max(self.last_input, self.last_cursor_move) > self.token_t0:
            self.hide_token()                              # caught! (cursor moved / clicked / menu)
            st = self.state
        if self.bubble:
            self.bubble[1] -= dt
            if self.bubble[1] <= 0 or not self.bubbles_on:
                self.bubble = None
        # mouse held on her head without dragging -> 'hmph'
        if (self.press_pos is not None and not self.dragging and not self.press_consumed and self.press_cell is not None
                and self.press_cell[1] < HEAD_ZONE_Y and now_m - self.press_t > HOLD_HMPH_SECONDS
                and st not in ('sleep', 'sleepy', 'wake', 'dangle', 'fall') and not self.clicks_ignored(now_m)):
            self.press_consumed = True
            self.click_timer.stop()
            self._touch()
            self.start_behaviour('tsun_hmph')
            st = self.state
        if st == 'dangle' and self.dragging and not self.flustered and now_m - self.drag_t0 > DRAG_FLUSTER_SECONDS:
            self.flustered = True                     # dragged too long: flustered dangle (same state, other frames)
            self.anim = self.sp['dangle_tsun']
            self.t = 0.0
            self.say('dangle_tsun')

        if st in ('idle', 'expr', 'walk'):
            # blink scheduler (idle-type states use index-synced blink frames)
            self.next_blink -= dt
            if self.next_blink <= 0 and self.blink_t < 0:
                self.blink_t = 0.0
                self.next_blink = self._rand_blink() if random.random() > 0.15 else 0.35   # sometimes a double blink
            if self.blink_t >= 0:
                self.blink_t += dt
                if self.blink_t > 0.24:
                    self.blink_t = -1.0

        if st == 'idle':
            self.animation_origin = 'automatic'
            self.next_walk -= dt
            self.next_behaviour -= dt
            self.next_beg -= dt
            at_loop_start = self.t * self.anim.fps % len(self.anim) < 1
            meal = self._meal_due() if (self._auto_ds and not self.quiet and at_loop_start) else None
            if self.quiet:
                pass
            elif self._hover_step(dt, now_m):
                pass
            elif self.user_idle(now_m) > SLEEP_TIMEOUT:
                self.set_state('sleepy')
            elif idle_for > self.next_attention and at_loop_start:     # ignored for a while: ask for attention
                self.next_attention = idle_for + random.uniform(*ATTENTION_REPEAT)
                self.next_behaviour = max(self.next_behaviour, 6.0)
                self.start_behaviour(self._pick_attention())
            elif self._auto_ds and self.user_idle(now_m) > self.token_at and at_loop_start:   # nobody watching...
                self.token_at = self.user_idle(now_m) + random.uniform(*TOKEN_IDLE)
                self.play_state('ds_token_nibble')
            elif meal is not None:                                    # lunch / dinner time: begs once
                self.meal_done = meal
                self.next_beg = max(self.next_beg, random.uniform(*BEG_EVERY))
                self.start_beg()
            elif self._auto_ds and self.hungry() and self.next_beg <= 0 and at_loop_start:
                self.next_beg = random.uniform(*BEG_EVERY)
                self.start_beg()
            elif self.walking and self.next_walk <= 0 and at_loop_start and (self.next_behaviour > 0 or self.next_walk < self.next_behaviour):
                self._start_walk()
            elif self.next_behaviour <= 0 and at_loop_start:
                self.next_behaviour = random.uniform(*IDLE_GAP)
                choice = self._force_behaviour or self.idle_chooser.choose(now_m) or 'idle'
                if choice != 'idle':
                    self.next_walk = max(self.next_walk, 3.0)
                    self.start_behaviour(choice)
        elif st == 'walk':
            r = self._screen_rect()
            x = self._root_motion()
            if x < r.left() or x + self.width() > r.right() + 1:
                self.walk_dir *= -1                  # turn round; same frame index in the mirrored cycle
                self.anim = self.sp['walk_right' if self.walk_dir > 0 else 'walk_left']
                x = max(r.left(), min(x, max(r.left(), r.right() + 1 - self.width())))
            self.move(x, self._ground_y())
            if self.walk_trip_at is not None and self.state_t > self.walk_trip_at and self.frame_index() == 0:
                self.walk_trip_at = None
                self.next_walk = random.uniform(*WALK_GAP)
                self.set_state('trip', 'trip' if self.walk_dir < 0 else 'trip_right')
            elif self.state_t > self.state_len and self.frame_index() == 0:
                self.next_walk = random.uniform(*WALK_GAP)
                self.set_state('idle')
        elif st in LOOP_STATES:
            if self.state_t > self.state_len and (self.frame_index() == 0):
                self.next_in_queue()
        elif st == 'ds_tail_allow':
            if self.t * self.anim.fps >= self.anim.section[0] and not self._sec_said:
                self._sec_said = True
                self.say(st)
                if self.bubble:
                    self.bubble[1] = TAIL_ALLOW_WAIT
            if self.state_t >= self.state_len:
                self.finish_tail_allow(False)
        elif st in ONESHOT_STATES:
            if (SPEAK_AT.get(st) == 'frame' and not self._said_frame and self.anim.speak_frame is not None
                    and self.frame_index() >= self.anim.speak_frame):
                self._said_frame = True
                self.say(st)
            self._section_step()
            if self.anim.root_motion:                # trip: keeps walking forward until the fall
                r = self._screen_rect()
                x = max(r.left(), min(self._root_motion(), max(r.left(), r.right() + 1 - self.width())))
                self.move(x, self._ground_y())
            if self.anim_done():
                if st == 'land' and self.after_land:
                    nxt, self.after_land = self.after_land, None
                    self.start_behaviour(nxt)
                elif st == 'land' and self.fall_from > 0.55:
                    self.set_state('sad', length=3.0)
                elif st == 'surprised' and self.state_t < 1.6:
                    pass
                elif st == 'sleepy':
                    self.set_state('sleep')
                elif st == 'wake':
                    self.set_state('surprised')
                elif st == 'ds_beg_rice' and not self.queue:
                    self._beg_done()
                else:
                    self.next_in_queue()
        elif st == 'fall':
            self.vy += 2600 * self.scale * dt
            gy = self._ground_y()
            y = self.y() + self.vy * dt
            if y >= gy:
                self.move(self.x(), gy)
                self.set_state('land')
            else:
                self.move(self.x(), int(y))
        self._refresh_frame()
        if not self.selftest:
            interval = 16 if self.state in ('fall', 'dangle', 'walk', 'trip') else min(50, max(16, int(500 / self.anim.fps)))
            if self.timer.interval() != interval:
                self.timer.setInterval(interval)

    def _frame_key(self):
        i = self.frame_index()
        name = self.anim.name
        if self.blink_t >= 0 and name in ('idle', 'expr_neutral'):
            phase = 'half' if (self.blink_t < 0.06 or self.blink_t > 0.17) else 'closed'
            name = f'idle_blink_{phase}'
        return name, i

    def _refresh_frame(self):
        key = self._frame_key() + (self.bubble[0] if self.bubble and self.bubbles_on else None, self.scale)
        if key != self._paint_key:
            self._paint_key = key
            self.update()

    def _cursor_pos(self):
        return self._cursor_override if self._cursor_override is not None else QCursor.pos()

    def _hover_step(self, dt, now_m):
        """cursor lingering near her (no button pressed, no menu open) for HOVER_PEEK_SECONDS -> tsun_peek"""
        m = int(HOVER_MARGIN * self.scale)
        near = (self.geometry().adjusted(-m, -m, m, m).contains(self._cursor_pos()) and self.press_pos is None
                and not self.menu_open and (self._cursor_override is not None or QApplication.mouseButtons() == Qt.NoButton))
        self.hover_t = self.hover_t + dt if near else 0.0
        if self.hover_t > HOVER_PEEK_SECONDS and now_m >= self.hover_block_until:
            self.hover_t = 0.0
            self.hover_block_until = now_m + HOVER_COOLDOWN
            self._touch()
            self.start_behaviour('tsun_peek')
            return True
        return False

    def _pick_attention(self):
        if self._force_attention:
            return self._force_attention
        return 'tsun_peek' if random.random() < ATTENTION_PEEK_CHANCE else 'poke_screen'

    def current_pixmap(self):
        name, i = self._frame_key()
        return self.sp[name].frames[i]

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.scale != 1.0)
        p.drawPixmap(self.rect(), self.current_pixmap())
        reply_visible = self._assistant is not None and self._assistant.reply_bubble is not None and self._assistant.reply_bubble.isVisible()
        if self.bubble and self.bubbles_on and not reply_visible:
            self._draw_bubble(p, self.bubble[0])
        p.end()

    def _draw_bubble(self, p, text):
        """small speech bubble above her head, inside the window: white rounded box, 1 px navy outline, tail pointing
        down at the head. Coordinates are snapped to whole pixels (+0.5 for the 1 px outline) so it stays crisp."""
        f = QFont()
        f.setFamilies(BUBBLE_FONTS)
        f.setPixelSize(max(10, int(round(BUBBLE_FONT_PX * self.scale))))
        f.setBold(True)
        fm = QFontMetrics(f)
        th = fm.height()
        pad_x, pad_y = max(4, int(5 * self.scale)), max(2, int(2 * self.scale))
        W = self.width()
        text = fm.elidedText(text, Qt.ElideRight, max(1, W - 8 - 2 * pad_x))
        tw = fm.horizontalAdvance(text)
        bw, bh = min(W - 4, tw + 2 * pad_x), th + 2 * pad_y
        top = max(1, int(3 * self.scale))
        cx = int(round(128 * self.scale))
        x0 = max(2, min(W - 2 - bw, cx - bw // 2))
        tail_h = max(3, int(4 * self.scale))
        rect = QRectF(x0 + 0.5, top + 0.5, bw, bh)
        path = QPainterPath()
        path.addRoundedRect(rect, bh / 2.2, bh / 2.2)
        tx = min(max(cx - int(10 * self.scale), x0 + bh // 2 + 2), x0 + bw - bh // 2 - 8)
        tail = QPainterPath()
        tail.addPolygon(QPolygonF([QPointF(tx + 0.5, top + bh - 1.5), QPointF(tx + 6.5, top + bh - 1.5),
                                   QPointF(tx + 1.5, top + bh + tail_h + 0.5)]))
        path = path.united(tail)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setPen(QPen(QColor(34, 40, 96), 1.0))
        p.setBrush(QColor(255, 255, 255, 245))
        p.drawPath(path)
        p.setFont(f)
        p.setPen(QColor(34, 40, 96))
        p.drawText(QRectF(x0, top, bw + 1, bh + 1), Qt.AlignCenter, text)

    # ------------------------------------------------------------------ input
    def _touch(self):
        self.input_revision += 1
        self.animation_origin = 'manual'
        self.last_input = time.monotonic()
        self.next_attention = random.uniform(*ATTENTION_FIRST)

    def mousePressEvent(self, e):
        self._touch()
        self.hover_block_until = time.monotonic() + HOVER_COOLDOWN
        if e.button() == Qt.LeftButton:
            if self.consume_tail_click():
                self.click_timer.stop()
                self.press_pos = None
                self.press_consumed = True
                return
            self.press_pos = e.globalPosition().toPoint()
            lp = e.position()
            self.press_cell = (lp.x() / self.scale, lp.y() / self.scale)
            self.press_t = time.monotonic()
            self.press_consumed = False
            self.dragging = False
            if self.state == 'ds_token_nibble':          # caught in the act: hide it (the release is not a click)
                self.hide_token()
                self.press_consumed = True

    def mouseMoveEvent(self, e):
        if self.press_pos is None or not (e.buttons() & Qt.LeftButton):
            return
        g = e.globalPosition().toPoint()
        if not self.dragging and (g - self.press_pos).manhattanLength() > 6:
            self.dragging = True
            self.click_timer.stop()
            self.drag_t0 = time.monotonic()
            self.flustered = False
            self.set_state('dangle')
        if self.dragging:
            self._touch()
            self.move(g.x() - int(GRAB[0] * self.scale), g.y() - int(GRAB[1] * self.scale))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        self._touch()
        if self.dragging:
            self.dragging = False
            self.press_pos = None
            if self.flustered:               # 'put me down!' -> after landing: hmph
                self.flustered = False
                self.after_land = 'tsun_hmph'
            gy = self._ground_y()
            r = self._screen_rect()
            self._clamp_position(r, ground=False)
            self.fall_from = max(0.0, (gy - self.y()) / max(1, r.height()))
            if self.y() >= gy - 2:
                self.move(self.x(), gy)
                self.set_state('land')
            else:
                self.vy = 0.0
                self.set_state('fall')
            self.schedule_save()
            return
        self.press_pos = None
        if self.press_consumed:              # the hold already made her 'hmph'
            self.press_consumed = False
            return
        if self.state in ('dangle', 'fall'):
            return
        self.click_cell = self.press_cell
        self.click_timer.start(QApplication.doubleClickInterval())

    def _single_click(self):
        self._touch()
        now = time.monotonic()
        if self.consume_tail_click():
            return
        if self.clicks_ignored(now):                     # 服务器繁忙 (+ cooldown): no new reaction at all
            self.click_cell = None
            return
        self.click_log = [t for t in self.click_log if now - t < SPAM_WINDOW] + [now]
        if self.state == 'sleep':                        # woken abruptly: sometimes a shock plop instead
            if self.rng.random() < SHOCK_WAKE_CHANCE:
                self.play_state('ds_shock_sit')
            else:
                self.set_state('wake')
            return
        if self.state in ('sleepy', 'wake'):
            self.set_state('surprised')
            return
        if self.in_nap():                                # outsourced nap: a click wakes her (stretch -> idle)
            self.wake_nap()
            return
        if self.state in ('sit', 'sit_down'):                  # stand up first, then enjoy the head pat
            self.set_state('stand_up')
            self.queue = [('petted', None, 2.0), ('petted_end', None, 0.0)]
            return
        if (self._poke_hot and self.state in ('tsun_point', 'angry')) or len(self.click_log) >= SPAM_CLICKS:
            self.click_cell = None                       # click spam -> 服务器繁忙
            self.start_server_busy()
            return
        cell, self.click_cell = self.click_cell, None
        if self.in_tail_zone(cell):                      # 不许摸尾巴！ (beats petted; not part of the poke chain)
            self.start_tail_touch()
            return
        if self.in_zone('belly', cell):                  # 我不是大肥鱼！ (tail > belly > head; not part of the chain)
            self.start_not_fat()
            return
        self.pet_times = [t for t in self.pet_times if now - t < POKE_WINDOW] + [now]
        self.pet_chain = self.pet_chain + 1 if (self.last_click is not None and now - self.last_click < PET_REPEAT_WINDOW) else 1
        self.last_click = now
        if len(self.pet_times) >= POKE_ANGRY_COUNT:      # poked far too much: 'stop poking!' -> pout
            self.pet_times = []
            self.pet_chain = 0
            self.set_state('tsun_point', length=TSUN_LEN['tsun_point'])
            self.queue = [('angry', None, 2.4)]
            self._poke_hot = True
        elif self.pet_chain > PET_CLICKS_OK:              # 'it's not like I enjoy it!'
            if self.state == 'tsun_hmph' and self.sec_rel is None:
                self.state_len = max(self.state_len, self.state_t + 1.2)    # keep huffing, no restart
            else:
                self.set_state('tsun_hmph', length=TSUN_LEN['tsun_hmph'])
        elif self.state == 'petted':
            self.state_len = max(self.state_len, self.state_t + 2.0)       # keep enjoying it, no restart
        else:
            self.start_behaviour('petted', 2.0)

    def mouseDoubleClickEvent(self, e):
        self._touch()
        self.click_timer.stop()
        # Qt sends a release after the double-click. Do not schedule another single-click.
        self.press_consumed = True
        self.click_cell = None
        if e.button() == Qt.LeftButton and self.consume_tail_click():
            return
        if e.button() == Qt.LeftButton and self.state not in ('dangle', 'fall') and not self.clicks_ignored():
            self.set_state('happy')

    def contextMenuEvent(self, e):
        self._touch()
        self.click_timer.stop()
        if self.state == 'ds_token_nibble':
            self.hide_token()
        self.menu_open = True
        menu = self.build_menu()
        try:
            menu.exec(e.globalPos())
        finally:
            menu.deleteLater()
            self.menu_open = False
            self.hover_block_until = time.monotonic() + HOVER_COOLDOWN

    def build_menu(self, menu=None, tray=False):
        m = menu if menu is not None else QMenu(self)
        if menu is not None:
            for action in m.actions():
                if action.menu() is not None:
                    action.menu().deleteLater()
            m.clear()
        m.setStyleSheet('QMenu { background: #f8fbff; color: #22365a; border: 1px solid #c3d5ea; '
                        'padding: 6px; font-family: "Microsoft YaHei UI"; font-size: 12px; } '
                        'QMenu::item { padding: 6px 24px 6px 12px; border-radius: 4px; } '
                        'QMenu::item:selected { background: #deedfa; } '
                        'QMenu::item:disabled { color: #7890ab; } '
                        'QMenu::separator { height: 1px; background: #dbe5f0; margin: 5px 8px; }')
        m.addAction(f'蓝色大肥鱼 · 饱腹度 {round(self.hunger * 100)}%').setEnabled(False)
        if self._assistant is not None:
            m.addAction('输入指令…').triggered.connect(self._assistant.open_window)
            modes = m.addMenu('运行模式')
            for key, label in (('standalone', '独立陪伴'), ('dsh', '跟随 dsh')):
                action = modes.addAction(label); action.setCheckable(True); action.setChecked(self._assistant.mode == key)
                action.triggered.connect(lambda _=False, mode=key: self._assistant.set_mode(mode))
        if tray:
            m.addAction('显示蓝色大肥鱼' if not self.isVisible() else '召回蓝色大肥鱼').triggered.connect(lambda: self.recall())
        m.addAction('回到主屏幕中央').triggered.connect(lambda: self.recall(center=True))
        m.addSeparator()
        it = m.addMenu('互动 Interact')
        for label, kind in INTERACT:
            a = it.addAction(label)
            a.triggered.connect(lambda _=False, k=kind: self.interact(k))
        fd = m.addMenu('投喂 Feed')
        for label, kind in FEED:
            a = fd.addAction(label)
            a.triggered.connect(lambda _=False, k=kind: self.interact(k))
        fd.addSeparator()
        fd.addAction(f'饱腹度 {int(round(self.hunger * 100))}%' + (' (饿了)' if self.hungry() else '')).setEnabled(False)
        pl = m.addMenu('玩耍/待机 Play')
        for label, kind in PLAY:
            a = pl.addAction(label)
            a.triggered.connect(lambda _=False, k=kind: self.play(k))
        ex = m.addMenu('表情 Expressions')
        for key, zh in EXPRESSIONS:
            a = ex.addAction(f'{zh} {key}')
            a.triggered.connect(lambda _=False, k=key: self.show_expression(k))
        act = m.addMenu('动作 Actions')
        for label, st, ln in ACTIONS:
            a = act.addAction(label)
            a.triggered.connect(lambda _=False, s=st, l=ln: self.do_action(s, l))
        size = m.addMenu('大小 Size')
        for s in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            a = size.addAction(f'{int(s * 100)}%'); a.setCheckable(True); a.setChecked(abs(s - self.scale) < 1e-6)
            a.triggered.connect(lambda _=False, v=s: self.set_scale(v))
        b = m.addAction('对话气泡 Speech bubbles'); b.setCheckable(True); b.setChecked(self.bubbles_on)
        b.triggered.connect(self.set_bubbles)
        q = m.addAction('安静陪伴'); q.setCheckable(True); q.setChecked(self.quiet)
        q.triggered.connect(self.set_quiet)
        w = m.addAction('自由散步'); w.setCheckable(True); w.setChecked(self.walking)
        w.setEnabled(not self.quiet)
        w.triggered.connect(self.set_walking)
        m.addSeparator()
        m.addAction('睡觉 Sleep').triggered.connect(lambda: self.set_state('sleepy'))
        m.addAction('叫醒她').triggered.connect(lambda: (self._touch(), self.set_state('wake')))
        if self._tray is not None and self.isVisible():
            m.addAction('暂时隐藏').triggered.connect(self.hide_pet)
        m.addAction('操作说明').triggered.connect(self.show_help)
        m.addSeparator()
        m.addAction('退出 Quit').triggered.connect(QApplication.quit)
        return m

    def do_action(self, st, ln):
        self._touch()
        self.start_behaviour(st, ln)

    def set_bubbles(self, on):
        self.bubbles_on = bool(on)
        if not on:
            self.bubble = None
        self.update()
        self.schedule_save()

    def interact(self, kind):
        """Interact / Feed menus: praise -> shy then proud; feed -> taiyaki then happy (+hunger); rice -> 暴风吸饭 then
        happy or dance (full); cookie -> gift; poke -> 'stop poking!'"""
        self._touch()
        if kind == 'tail':
            if self.state in ('sit', 'sit_down'):
                a = self.sp['ds_tail_touch']; s0, s1 = a.section
                duration = (s0 + random.randint(*TAIL_SLAMS) * (s1-s0)) / a.fps - .01
                self.set_state('stand_up')
                self.queue = [('ds_tail_touch', None, duration), ('ds_tail_allow', None, 0.0)]
            else:
                self.start_tail_touch()
            return
        if kind in ('outsource', 'belly'):             # 派活给她 -> outsource + nap; 戳肚子 -> 我不是大肥鱼！
            st = 'ds_outsource_nap' if kind == 'outsource' else 'ds_not_fat'
            ln = self.hold_len(st) if kind == 'outsource' else self.not_fat_len()
            if self.state in ('sit', 'sit_down'):
                self.set_state('stand_up')
                self.queue = [(st, None, ln)]
            else:
                self.set_state(st, length=ln)
            return
        if kind == 'feed':
            self.fed(TAIYAKI_HUNGER)
        elif kind == 'rice':
            self.fed(1.0)
        after = random.choice(RICE_AFTER) if kind == 'rice' else None
        plan = {'praise': [('praised_shy', None, TSUN_LEN['praised_shy']), ('tsun_proud', None, TSUN_LEN['tsun_proud'])],
                'feed': [('eat_taiyaki', None, TSUN_LEN['eat_taiyaki']), ('happy', None, 0.0)],
                'rice': [('ds_rice_slurp', None, self.hold_len('ds_rice_slurp')),
                         (after, None, RICE_DANCE_SECONDS if after == 'dance' else 0.0)],
                'cookie': [('gift_cookie', None, TSUN_LEN['gift_cookie'])],
                'poke': [('tsun_point', None, TSUN_LEN['tsun_point'])]}[kind]
        if self.state in ('sit', 'sit_down'):          # stand up first
            self.set_state('stand_up')
            self.queue = plan
            return
        st, an, ln = plan[0]
        self.set_state(st, an, ln)
        self.queue = plan[1:]

    def show_expression(self, key):
        self._touch()
        self.set_state('expr', f'expr_{key}', length=6.0)

    def set_scale(self, s):
        if not math.isfinite(s) or not 0.5 <= s <= 2.0:
            return
        cx, bottom = self.x() + self.width() // 2, self.y() + int(self.sp.baseline * self.scale)
        self.scale = s
        self._resize()
        self.move(cx - self.width() // 2, bottom - int(self.sp.baseline * s))
        if self.state not in ('dangle', 'fall'):
            self._clamp_position()
        self._paint_key = None
        self.update()
        self.schedule_save()


def main():
    import argparse
    ap = argparse.ArgumentParser(description='Whale-maid desktop pet')
    ap.add_argument('--scale', type=float, default=None)
    ap.add_argument('--selftest', action='store_true', help='run a scripted tour of all states, save screenshots, quit')
    ap.add_argument('--out', default='selftest_out')
    ap.add_argument('--data-dir', help='override the portable preferences directory')
    ap.add_argument('--mode', choices=('standalone','dsh'), help='desktop companion mode')
    ap.add_argument('--chat', action='store_true', help='open the command window')
    args = ap.parse_args()
    if args.scale is not None and (not math.isfinite(args.scale) or not 0.5 <= args.scale <= 2.0):
        ap.error('--scale must be a finite number between 0.5 and 2.0')
    app = QApplication(sys.argv)
    configure_fonts(app)
    app.setApplicationName('WhalePet')
    app.setOrganizationName('WhalePet')
    app.setQuitOnLastWindowClosed(False)
    base = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
    server = None
    if not args.selftest:
        name = 'WhalePet_' + hashlib.sha256(os.path.normcase(base).encode('utf-8')).hexdigest()[:24]
        socket = QLocalSocket(app)
        socket.connectToServer(name)
        if socket.waitForConnected(300):
            socket.write((json.dumps({'mode':args.mode, 'chat':args.chat}) + '\n').encode('utf-8'))
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return 0
        server = QLocalServer(app)
        if not server.listen(name):
            QLocalServer.removeServer(name)
            if not server.listen(name):
                QMessageBox.critical(None, '蓝色大肥鱼', '无法启动蓝色大肥鱼：' + server.errorString())
                return 1
    preferences = None if args.selftest else Preferences(os.path.join(args.data_dir or os.path.join(base, 'userdata'), 'settings.json'))
    saved = preferences.load() if preferences else {}
    scale = args.scale if args.scale is not None else saved.get('scale', 1.0)
    try:
        sp = Sprites(resource_dir())
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as e:
        zips = e.zips() if isinstance(e, AtlasPageMissing) else ['whale-pet-app.zip', 'whale-pet-app-atlas-*.zip']
        QMessageBox.critical(None, 'Whale Pet', f"{e}\n\n缺少图集文件：请把 {'、'.join(zips)} 解压到同一个 whale-pet 文件夹"
                             f"（所有 whale-pet-app-atlas-*.zip 都要解压）。\nMissing atlas page(s): unzip {', '.join(zips)} into "
                             f"the same whale-pet folder.")
        sys.exit(1)
    pet = Pet(sp, scale, args.selftest, preferences)
    if not args.selftest:
        from dsh_companion import PetCompanion
        pet._assistant = PetCompanion(pet, args.data_dir or os.path.join(base, 'userdata'), saved, args.mode)
        app.aboutToQuit.connect(pet._assistant.shutdown)
    app.aboutToQuit.connect(pet.save_preferences)
    if preferences:
        # Also checkpoint hunger while the pet is running.
        checkpoint = QTimer(app, timeout=pet.save_preferences)
        checkpoint.start(60000)
    if server is not None:
        def summon():
            while server.hasPendingConnections():
                client = server.nextPendingConnection()
                pet.recall()
                def receive(client=client):
                    if not client.canReadLine():
                        return
                    try:
                        command = json.loads(bytes(client.readLine()).decode('utf-8'))
                        if command.get('mode'):
                            pet._assistant.set_mode(command['mode'])
                        if command.get('chat'):
                            pet._assistant.open_window()
                    except (ValueError, UnicodeError, AttributeError):
                        pass
                client.readyRead.connect(receive)
                client.disconnected.connect(client.deleteLater)
                receive()
        server.newConnection.connect(summon)
    icon = QIcon(sp['expr_smile'].frames[0])
    app.setWindowIcon(icon)
    if QSystemTrayIcon.isSystemTrayAvailable() and not args.selftest:
        tray = QSystemTrayIcon(icon, app)
        tray.setToolTip('蓝色大肥鱼')
        tm = QMenu()
        def show_tray_menu():
            pet._touch()
            if pet.state == 'ds_token_nibble':
                pet.hide_token()
            pet.build_menu(tm, tray=True)
            pet.menu_open = True
        def hide_tray_menu():
            pet.menu_open = False
            pet.hover_block_until = time.monotonic() + HOVER_COOLDOWN
        tm.aboutToShow.connect(show_tray_menu)
        tm.aboutToHide.connect(hide_tray_menu)
        tray.setContextMenu(tm)
        tray.activated.connect(lambda reason: pet.recall() if reason == QSystemTrayIcon.DoubleClick else None)
        tray.show()
        pet._tray = tray
    pet.show()
    if args.chat and pet._assistant is not None:
        QTimer.singleShot(0, pet._assistant.open_window)
    pet.schedule_save()
    if args.selftest:
        run_selftest(app, pet, args.out)
    return app.exec()


def run_selftest(app, pet, out):
    """drives the state machine without a real mouse; saves one grab per step and verifies the transitions,
    including the tsundere triggers (click chain, poke x6, hold on the head, hover, ignored, Interact menu,
    long drag) and the speech bubbles."""
    pet.rng.seed(0)       # normal first wake; later steps explicitly seed and verify both outcomes
    os.makedirs(out, exist_ok=True)
    log = []
    now = time.monotonic

    def press_on_head():
        pet.press_pos = pet.mapToGlobal(QPoint(int(128 * pet.scale), int(80 * pet.scale)))
        pet.press_cell = (128.0, 80.0)
        pet.press_t = now() - HOLD_HMPH_SECONDS - 0.1
        pet.press_consumed = False
        pet.dragging = False

    def hover_on():
        pet._cursor_override = pet.geometry().center()
        pet.hover_t = 0.0
        pet.hover_block_until = 0.0

    def ignored(kind, secs):
        pet._force_attention = kind
        pet.last_input = pet.last_cursor_move = now() - secs
        pet.next_attention = secs - 1
        pet.set_state('idle')

    def long_drag():
        pet.set_state('dangle')
        pet.dragging = True
        pet.flustered = False
        pet.drag_t0 = now() - DRAG_FLUSTER_SECONDS - 0.1
        pet.press_pos = pet.mapToGlobal(QPoint(128, 50))

    def release_drag():
        pet.move(pet.x(), pet._ground_y() - 300)
        pet.mouseReleaseEvent(_FakeEvt())

    def menu_play(label):              # trigger an entry of the real right-click Play submenu
        m = pet.build_menu()
        sub = [a.menu() for a in m.actions() if a.menu() and a.text().startswith('玩耍')][0]
        [a for a in sub.actions() if a.text().startswith(label)][0].trigger()
        pet.state_len = pet.anim.section[0] / pet.anim.fps + 1.0      # hold the loop section 1 s (not 8-15 s)

    def force_idle(beh, state='idle'):  # the random idle picker, with a fixed choice
        pet.set_state(state)
        pet._force_behaviour = beh
        pet.next_behaviour = 0.0

    def clicks(n):
        pet.pet_times = []
        pet.click_log = []
        pet.last_click = None
        for _ in range(n):
            pet._single_click()

    # ---- DeepSeek meme set helpers
    def tail_click(inside):
        pet.set_state('idle')
        pet.pet_times, pet.pet_chain, pet.last_click = [], 0, None
        b = pet.tail_box()
        pet.click_cell = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if inside else (128.0, 80.0)   # tail / head
        pet._single_click()
        pet.queue.clear()  # legacy slam timing; follow-up covered by test_tail_allow.py

    def menu_entry(top, label, hold=None):     # trigger an entry of a real right-click submenu
        m = pet.build_menu()
        sub = [a.menu() for a in m.actions() if a.menu() and a.text().startswith(top)][0]
        [a for a in sub.actions() if a.text().startswith(label)][0].trigger()
        if hold is not None:
            pet.state_len = pet.anim.section[0] / pet.anim.fps + hold

    def hungry_soon():                   # hunger just above 0: the next ticks decay it -> hungry -> beg from idle
        pet._auto_ds = True
        pet.hunger = 0.00001             # one or two ticks of decay
        pet.next_beg = 0.0               # (normally BEG_FIRST = 2-8 s after getting hungry)
        pet.set_state('idle')

    def beg_ignored_setup(n_before, first_ago):
        pet._auto_ds = True
        pet.hunger = 0.0
        pet.beg_ignored = n_before
        pet.first_beg = now() - first_ago
        pet.last_beg_end = now() - 5
        pet.next_beg = 0.0
        pet.set_state('idle')

    def beg_hold(sec):
        pet.state_len = pet.anim.section[0] / pet.anim.fps + sec

    def token_idle():
        pet._auto_ds = True
        pet._cursor_override = QPoint(40, 40)
        pet._track_cursor(now())
        pet.last_input = pet.last_cursor_move = now() - TOKEN_IDLE[1] - 1
        pet.token_at = TOKEN_IDLE[1]
        pet.next_attention = 1e9
        pet.set_state('idle')

    def meal_at(h, m, fed_ago_min=None, hunger=0.8):
        pet._auto_ds = True
        pet._clock_override = datetime(2026, 9, 30, h, m)
        pet.hunger = hunger
        pet.last_fed = -1e9 if fed_ago_min is None else now() - fed_ago_min * 60
        pet.set_state('idle')

    # ---- round 2 helpers
    def belly_click(head=False):
        pet.set_state('idle')
        pet.pet_times, pet.pet_chain, pet.last_click, pet.click_log = [], 0, None, []
        b = pet.zone_box('belly')
        pet.click_cell = (128.0, 80.0) if head else ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        pet._single_click()

    def overlap_click():                 # belly zone moved onto the tail zone: a click there -> the TAIL wins
        pet.set_state('idle')
        pet.pet_times, pet.pet_chain, pet.last_click, pet.click_log = [], 0, None, []
        saved = pet.sp.hit['belly']
        pet.sp.hit['belly'] = list(pet.sp.hit['tail'])
        b = pet.zone_box('tail')
        pet.click_cell = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        both = pet.in_zone('belly', pet.click_cell) and pet.in_zone('tail', pet.click_cell)
        pet._single_click()
        pet.queue.clear()  # isolate the legacy slam; new branch tests cover the follow-up
        pet.sp.hit['belly'] = saved
        prio[0] = both

    def sleep_click(seed):               # a click wakes her from sleep; the shock chance uses a seeded RNG
        pet.set_state('sleep')
        pet.rng = random.Random(seed)
        pet.click_cell = (128.0, 120.0)
        pet._single_click()
        if pet.state == 'ds_shock_sit':
            pet.state_len = pet.anim.section[0] / pet.anim.fps + 1.5

    def spam10():                        # 9 clicks in the last 4.5 s + this one = 10 within 6 s
        pet.set_state('idle')
        pet.pet_times, pet.pet_chain, pet.last_click = [], 0, None
        pet.click_log = [now() - 0.5 * k for k in range(1, SPAM_CLICKS)]
        pet.click_cell = (128.0, 80.0)
        pet._single_click()

    def busy_short(hold):
        pet.state_len = pet.anim.section[0] / pet.anim.fps + hold

    def nap_click():
        pet.click_cell = (128.0, 120.0)
        pet._single_click()

    prio = [False]

    # (time, action, expected state after 0.25 s, extra checks: anim=..., bubble=True/False, no_click=True)
    steps = [
        (0.0, lambda: None, 'idle', {}),
        (0.6, lambda: setattr(pet, 'blink_t', 0.1), 'idle', {}),
        (0.9, lambda: (setattr(pet, 'walk_dir', 1), pet.set_state('walk', 'walk_right', 0.8)), 'walk', {}),
        (2.2, lambda: pet._single_click(), 'petted', {}),
        (4.8, lambda: None, 'petted_end', {}),
        (5.6, lambda: None, 'idle', {}),
        (5.9, lambda: pet.mouseDoubleClickEvent(_FakeEvt()), 'happy', {}),
        (7.2, lambda: pet.set_state('dangle'), 'dangle', {}),
        (7.8, lambda: (pet.move(pet.x(), pet._ground_y() - 300), setattr(pet, 'vy', 0.0), pet.set_state('fall')), 'fall', {}),
        (8.2, lambda: None, 'land', {}),
        (9.1, lambda: None, 'idle', {}),
        (9.4, lambda: pet.show_expression('wink'), 'expr', {}),
        (9.9, lambda: pet.do_action('wave', 1.0), 'wave', {}),
        (11.6, lambda: pet.do_action('curtsy', 0), 'curtsy', {}),
        (13.3, lambda: pet.do_action('stretch', 0), 'stretch', {}),
        (15.4, lambda: pet.do_action('tea', 1.0), 'tea', {}),
        (17.6, lambda: pet.do_action('curious', 1.0), 'curious', {}),
        (19.8, lambda: pet.do_action('dance', 1.0), 'dance', {}),
        (22.0, lambda: pet.do_action('sit', 1.0), 'sit_down', {}),
        (22.6, lambda: None, 'sit', {}),
        (23.2, lambda: pet._single_click(), 'stand_up', {}),
        (23.8, lambda: None, 'petted', {}),
        (27.0, lambda: (setattr(pet, 'walk_dir', -1), pet.do_action('trip', 0)), 'trip', {}),
        (29.6, lambda: None, 'idle', {}),
        (30.0, lambda: (setattr(pet, 'walk_dir', 1), pet.set_state('walk', 'walk_right', 5.0),
                        setattr(pet, 'walk_trip_at', 0.3)), 'walk', {}),
        (31.2, lambda: None, 'trip', {}),
        (33.7, lambda: (setattr(pet, 'last_input', now() - SLEEP_TIMEOUT - 1), setattr(pet, 'last_cursor_move', now() - SLEEP_TIMEOUT - 1), pet.set_state('idle')), 'sleepy', {}),
        (37.8, lambda: None, 'sleep', {}),
        (38.4, lambda: pet._single_click(), 'wake', {}),
        (40.0, lambda: None, 'surprised', {}),
        (41.8, lambda: None, 'idle', {}),
        # ---- tsundere triggers
        (42.2, lambda: clicks(3), 'tsun_hmph', dict(bubble=True)),              # 3rd click within 3 s -> hmph
        (43.4, lambda: None, 'tsun_hmph', {}),
        (45.2, lambda: None, 'idle', {}),
        (45.5, lambda: clicks(6), 'tsun_point', dict(bubble=True)),             # 6 pokes -> 'stop poking!'
        (48.3, lambda: None, 'angry', {}),                                      # ... -> angry
        (51.6, lambda: None, 'idle', {}),
        (51.8, press_on_head, 'tsun_hmph', {}),                                 # held on the head > 2 s
        (52.4, lambda: pet.mouseReleaseEvent(_FakeEvt()), 'tsun_hmph', dict(no_click=True)),
        (55.0, lambda: None, 'idle', {}),
        (55.2, hover_on, 'idle', {}),                                           # cursor lingers near her
        (57.2, lambda: setattr(pet, '_cursor_override', None), 'tsun_peek', {}),
        (60.6, lambda: None, 'tsun_peek', dict(bubble=True)),                   # caught peeking: speaks now
        (62.0, lambda: None, 'idle', {}),
        (62.3, lambda: ignored('poke_screen', ATTENTION_FIRST[1] + 1), 'poke_screen', dict(bubble=True)),   # ignored
        (65.2, lambda: None, 'idle', {}),
        (65.4, lambda: ignored('tsun_peek', ATTENTION_FIRST[0] + 5), 'tsun_peek', {}),
        (70.4, lambda: None, 'idle', {}),
        (70.6, lambda: pet.interact('praise'), 'praised_shy', dict(bubble=True)),   # Interact menu
        (76.0, lambda: None, 'tsun_proud', dict(bubble=True)),
        (80.6, lambda: None, 'idle', {}),
        (80.8, lambda: pet.interact('feed'), 'eat_taiyaki', dict(bubble=True)),
        (86.2, lambda: None, 'happy', {}),
        (87.4, lambda: None, 'idle', {}),
        (87.6, lambda: pet.interact('cookie'), 'gift_cookie', dict(bubble=True)),
        (92.2, lambda: None, 'idle', {}),
        (92.4, lambda: pet.interact('poke'), 'tsun_point', dict(bubble=True)),
        (95.2, lambda: None, 'idle', {}),
        (95.4, long_drag, 'dangle', dict(anim='dangle_tsun', bubble=True)),    # dragged > 3 s: flustered
        (96.0, release_drag, 'fall', {}),
        (97.3, lambda: None, 'tsun_hmph', {}),                                   # fall -> land -> hmph
        (100.2, lambda: None, 'idle', {}),
        (100.4, lambda: (pet.set_bubbles(False), pet.interact('poke')), 'tsun_point', dict(bubble=False)),  # bubbles off
        (103.2, lambda: pet.set_bubbles(True), 'idle', {}),
        # ---- idle play: tea / fishing / butterfly (Play menu, random idle pool, click interrupts, bubbles)
        (103.5, lambda: pet.play('tea', hold=1.0), 'idle_tea', dict(bubble=False)),
        (107.6, lambda: None, 'idle_tea', dict(bubble=True, section=True)),       # sipping: 呼…好香
        (110.4, lambda: None, 'idle', {}),
        (110.6, lambda: menu_play('钓鱼'), 'idle_fishing', dict(bubble=False)),   # via the Play submenu
        (113.7, lambda: None, 'idle_fishing', dict(section=True)),               # on the stool, waiting
        (115.8, lambda: None, 'idle_fishing', dict(bubble=True)),                # a bite: 钓到啦！
        (118.6, lambda: None, 'idle', {}),
        (118.8, lambda: pet.play('fishing', hold=10.0), 'idle_fishing', {}),
        (122.0, lambda: pet._single_click(), 'petted', {}),                      # a click interrupts the fishing
        (125.4, lambda: None, 'idle', {}),
        (125.6, lambda: pet.play('butterfly', hold=0.5, outcome='catch'), 'idle_butterfly', {}),
        (129.2, lambda: None, 'idle_butterfly', dict(bubble=True, section=True)),  # on her finger: 抓到了～
        (131.8, lambda: None, 'idle', {}),
        (132.0, lambda: pet.play('butterfly', hold=0.5, outcome='miss'), 'idle_butterfly_miss', {}),
        (135.6, lambda: None, 'idle_butterfly_miss', dict(bubble=True, section=True)),   # 才、才不是没抓到！
        (137.6, lambda: None, 'idle', {}),
        (137.8, lambda: force_idle('idle_tea'), 'idle_tea', {}),                 # random idle picker -> tea
        (138.4, lambda: (setattr(pet, '_force_behaviour', None), pet.set_state('idle')), 'idle', {}),
        (138.6, lambda: force_idle('idle_fishing'), 'idle_fishing', {}),         # ... -> fishing
        (139.2, lambda: (setattr(pet, '_force_behaviour', None), pet.set_state('idle')), 'idle', {}),
        (139.4, lambda: force_idle('idle_butterfly'), 'idle_butterfly*', {}),    # ... -> butterfly (either outcome)
        (140.0, lambda: force_idle('idle_fishing', 'sleep'), 'sleep', {}),       # never while she sleeps
        (140.6, lambda: (setattr(pet, '_force_behaviour', None), pet.set_state('idle')), 'idle', {}),
        (140.8, lambda: (pet.set_state('sit'), pet.play('tea', hold=1.0)), 'stand_up', {}),   # sitting: stands up first
        (141.6, lambda: None, 'idle_tea', {}),
        (142.0, lambda: pet.set_state('idle'), 'idle', {}),
        # ---- DeepSeek meme set: tail zone, hunger -> beg -> rice, beg ignored -> sulk, token gate, meal time, menus
        (142.4, lambda: tail_click(True), 'ds_tail_touch', dict(say='不许摸尾巴！', check=('click in the tail zone beats petted, no poke chain', lambda: pet.pet_chain == 0 and not pet.pet_times))),
        (144.1, lambda: None, 'ds_tail_touch', dict(section=True)),                 # tail slams
        (146.6, lambda: None, 'idle', {}),
        (146.8, lambda: tail_click(False), 'petted', {}),                            # outside the tail zone: petted
        (150.6, lambda: None, 'idle', {}),
        (150.8, hungry_soon, 'ds_beg_rice', dict(check=('hunger decayed to 0 -> hungry', lambda: pet.hungry()))),
        (152.2, lambda: None, 'ds_beg_rice', dict(say='还要…', section=True)),
        (152.6, lambda: (menu_entry('投喂', '白米饭'), setattr(pet, 'state_len', 1.3 + 1.5)), 'ds_rice_slurp',
         dict(say='干饭！', check=('fed rice: hunger full, beg streak reset', lambda: pet.hunger > 0.999 and pet.beg_ignored == 0))),
        (154.2, lambda: None, 'ds_rice_slurp', dict(section=True)),                  # slurping
        (155.5, lambda: None, 'ds_rice_slurp', dict(say='嗝～')),                    # burp
        (157.1, lambda: None, 'happy|dance', {}),                                    # -> happy jump or dance
        (161.0, lambda: pet.set_state('idle'), 'idle', {}),
        (161.2, lambda: beg_ignored_setup(BEG_IGNORE_SULK - 1, 10), 'ds_beg_rice', {}),   # 3rd ignored beg...
        (161.5, lambda: beg_hold(1.0), 'ds_beg_rice', {}),
        (164.0, lambda: None, 'ds_bowl_sulk', dict(say='哼，不给就算了', check=('ignored x3 -> sulk, streak reset', lambda: pet.beg_ignored == 0))),
        (168.2, lambda: None, 'idle', {}),
        (168.4, lambda: beg_ignored_setup(0, BEG_SULK_SECONDS + 1), 'ds_beg_rice', {}),   # ...or begging > 90 s
        (168.7, lambda: beg_hold(1.0), 'ds_beg_rice', {}),
        (171.2, lambda: None, 'ds_bowl_sulk', {}),
        (175.4, lambda: pet.set_state('idle'), 'idle', {}),
        (175.6, token_idle, 'ds_token_nibble', dict(bubble=False)),                  # user idle > 100 s: nibbles
        (176.6, lambda: None, 'ds_token_nibble', dict(section=True, bubble=False)),
        (177.0, lambda: setattr(pet, '_cursor_override', QPoint(90, 60)), 'ds_token_hide', dict(say='我、我什么都没吃！')),  # cursor moved: caught
        (178.2, lambda: None, 'ds_token_hide', dict(section=True)),
        (181.4, lambda: None, 'idle', {}),
        (181.6, lambda: menu_entry('玩耍', '偷啃 token', 6.0), 'ds_token_nibble', {}),    # Play menu preview
        (182.6, lambda: pet.mousePressEvent(_FakePress()), 'ds_token_hide', {}),     # clicked while nibbling: caught
        (182.9, lambda: pet.mouseReleaseEvent(_FakeEvt()), 'ds_token_hide', dict(no_click=True)),
        (186.6, lambda: None, 'idle', {}),
        (186.8, lambda: meal_at(12, 0), 'ds_beg_rice', dict(check=('12:00 = lunch window', lambda: pet.meal_done is not None))),
        (187.1, lambda: beg_hold(1.0), 'ds_beg_rice', {}),
        (189.6, lambda: None, 'idle', {}),                                           # not hungry: no sulk
        (189.8, lambda: pet.set_state('idle') or setattr(pet, '_auto_ds', True), 'idle', dict(check=('begs only once per window', lambda: pet.state == 'idle'))),
        (190.2, lambda: meal_at(18, 0, fed_ago_min=10), 'idle', dict(check=('18:00 but fed 10 min ago: no beg', lambda: pet.meal_done[1] == 0))),
        (190.6, lambda: meal_at(15, 0), 'idle', {}),                                 # outside the windows
        (191.0, lambda: menu_entry('玩耍', '君权盆授', 1.0), 'ds_basin', {}),         # Play menu
        (192.8, lambda: None, 'ds_basin', dict(say='君权盆授！', section=True)),
        (195.4, lambda: None, 'idle', {}),
        (195.6, lambda: (setattr(pet, 'hunger', 0.1), menu_entry('投喂', '鲷鱼烧')), 'eat_taiyaki',
         dict(check=('taiyaki: hunger +0.35', lambda: abs(pet.hunger - 0.45) < 0.01))),
        (196.2, lambda: pet.set_state('idle'), 'idle', {}),
        # ---- round 2: belly zone (tail > belly > head), click spam -> server busy (+ cooldown), sleep-click shock,
        #      outsource + nap (menu, click wakes her), deep think (random pick), menu entries
        (196.5, lambda: belly_click(), 'ds_not_fat', dict(say='我不是大肥鱼！', check=('belly zone -> not fat, not in the poke chain', lambda: pet.pet_chain == 0 and not pet.pet_times))),
        (197.3, lambda: None, 'ds_not_fat', dict(section=True)),                        # stomping
        (198.85, lambda: None, 'ds_not_fat', dict(say='…嚼嚼', check=('onigiri nibble after the stomps', lambda: pet.frame_index() >= pet.anim.section[1]))),
        (201.3, lambda: None, 'idle', {}),
        (201.5, overlap_click, 'ds_tail_touch', dict(check=('cell in BOTH zones -> tail wins (priority tail > belly)', lambda: prio[0]))),
        (206.0, lambda: None, 'idle', {}),
        (206.2, lambda: belly_click(head=True), 'petted', {}),                          # head: petted as before
        (210.2, lambda: None, 'idle', {}),
        (210.4, lambda: clicks(6), 'tsun_point', {}),                                     # 6 pokes -> 'stop poking!'
        (210.9, lambda: pet._single_click(), 'ds_server_busy', dict(check=('a click after the poke chain -> server busy', lambda: pet.clicks_ignored()))),
        (211.5, lambda: (busy_short(1.0), pet._single_click(), pet.mouseDoubleClickEvent(_FakeEvt())), 'ds_server_busy',
         dict(check=('clicks / double-click during it are ignored (no restart)', lambda: pet.state_t > 0.7), section=True)),
        (213.6, lambda: None, 'idle', dict(say='好、好了，恢复服务！')),                    # recovered: bounced back
        (214.6, lambda: None, 'idle', dict(check=('5 s cooldown running', lambda: pet.busy_quiet_until > now()))),
        (214.9, lambda: pet._single_click(), 'idle', dict(bubble=False, check=('click in the cooldown ignored', lambda: not pet.click_log or pet.click_log[-1] < now() - 0.5))),
        (219.8, lambda: pet._single_click(), 'petted', dict(check=('cooldown over: clicks work again', lambda: not pet.clicks_ignored()))),
        (223.6, lambda: None, 'idle', {}),
        (223.8, lambda: sleep_click(1), 'ds_shock_sit', dict(say='欸——？！', check=('seeded RNG 0.134 < 0.3 -> shock instead of wake', lambda: True))),
        (225.4, lambda: None, 'ds_shock_sit', dict(section=True)),                      # plopped, soul puff
        (228.5, lambda: None, 'idle', {}),
        (228.7, lambda: sleep_click(0), 'wake', dict(check=('seeded RNG 0.844 >= 0.3 -> normal wake', lambda: True))),
        (230.3, lambda: None, 'surprised', {}),
        (232.1, lambda: None, 'idle', {}),
        (232.3, spam10, 'ds_server_busy', dict(check=('>= 10 clicks within 6 s -> server busy', lambda: not pet.click_log))),
        (232.6, lambda: busy_short(0.5), 'ds_server_busy', {}),
        (235.0, lambda: None, 'idle', {}),
        (235.2, lambda: menu_entry('互动', '派活给她'), 'ds_outsource_nap', {}),                  # Interact menu forces it
        (236.9, lambda: None, 'ds_outsource_nap', dict(say='这活交给别人了～')),           # the throw
        (239.0, lambda: None, 'ds_outsource_nap', dict(section=True, check=('napping (hold 6-12 s)', lambda: pet.state_len >= pet.anim.section[0] / pet.anim.fps + DS_HOLD['ds_outsource_nap'][0]))),
        (240.2, nap_click, 'ds_outsource_nap', dict(check=('click wakes her: out of the nap loop -> stretch', lambda: pet.sec_rel is not None and pet.frame_index() >= pet.anim.section[1]))),
        (242.1, lambda: None, 'idle', {}),
        (242.3, lambda: force_idle('ds_deep_think'), 'ds_deep_think', dict(say='已深度思考…')),   # random idle pick
        (242.9, lambda: busy_short(1.0), 'ds_deep_think', {}),
        (246.0, lambda: None, 'ds_deep_think', dict(section=True)),                     # dreaming of rice
        (246.8, lambda: None, 'ds_deep_think', dict(say='才、才没有在想吃的！')),        # head shake
        (248.3, lambda: None, 'idle', {}),
        (248.5, lambda: (setattr(pet, '_force_behaviour', None), menu_entry('玩耍', '深度思考', 0.5)), 'ds_deep_think', {}),
        (249.0, lambda: pet.set_state('idle'), 'idle', {}),
        (249.2, lambda: menu_entry('玩耍', '服务器繁忙', 0.3), 'ds_server_busy', dict(check=('Play menu busy: clicks ignored', lambda: pet.clicks_ignored()))),
        (249.8, lambda: pet.set_state('idle'), 'idle', {}),
        (250.0, lambda: menu_entry('玩耍', '震惊', 0.5), 'ds_shock_sit', dict(say='欸——？！')),
        (250.6, lambda: pet.set_state('idle'), 'idle', {}),
        (250.8, lambda: menu_entry('互动', '戳肚子'), 'ds_not_fat', dict(say='我不是大肥鱼！')),
        (251.4, lambda: pet.set_state('idle'), 'idle', {}),
        (251.6, lambda: force_idle('ds_outsource_nap'), 'ds_outsource_nap', {}),          # forced contextual clips
        (252.1, lambda: (setattr(pet, '_force_behaviour', None), pet.set_state('idle')), 'idle', {}),
        (252.3, lambda: force_idle('ds_shock_sit'), 'ds_shock_sit', dict(say='欸——？！')),
        (252.8, lambda: (setattr(pet, '_force_behaviour', None), setattr(pet, '_clock_override', None), pet.set_state('idle')), 'idle', {}),
    ]
    idx = [0]
    pet._clock_override = datetime(2026, 1, 1, 15, 0)     # never meal time during the scripted tour (mocked clock)
    pool = [b for b, _ in IDLE_BEHAVIOURS]
    tb = pet.tail_box(pet.sp['idle'], 0)
    ia = pet.sp['idle']                                  # flip-aware: box of a mirrored frame = mirrored box
    fb = pet.tail_box(Anim('idle', ia.frames, ia.fps, ia.loop, flips=[True] * len(ia)), 0)
    flip_ok = tb is not None and fb == (pet.sp.cell - tb[2], tb[1], pet.sp.cell - tb[0], tb[3])
    ds_ok = (dict(IDLE_BEHAVIOURS).get('ds_basin') == .4 and 'ds_token_nibble' not in pool and 'ds_beg_rice' not in pool
             and all(a in pet.sp.anims for a in DS_STATES + ('ds_bowl_sulk',)) and tb is not None
             and TOKEN_IDLE[1] < SLEEP_TIMEOUT and HUNGRY_AFTER_MIN > 0 and flip_ok)
    bb = pet.zone_box('belly', pet.sp['idle'], 0)
    fbb = pet.zone_box('belly', Anim('idle', ia.frames, ia.fps, ia.loop, flips=[True] * len(ia)), 0)
    w = dict(IDLE_BEHAVIOURS)
    def labels(top):
        m = pet.build_menu()
        sub = [a.menu() for a in m.actions() if a.menu() and a.text().startswith(top)][0]
        return [a.text() for a in sub.actions()]
    pl_l, it_l = labels('玩耍'), labels('互动')
    menus_ok = (all(any(t.startswith(k) for t in pl_l) for k in ('深度思考', '服务器繁忙', '震惊'))
                and all(any(t.startswith(k) for t in it_l) for k in ('派活给她', '戳肚子')))
    r = random.Random(2026)
    shock_rate = sum(r.random() < SHOCK_WAKE_CHANCE for _ in range(10000)) / 10000
    no_overlap = bb is not None and tb is not None and (bb[2] < tb[0] or tb[2] < bb[0] or bb[3] < tb[1] or tb[3] < bb[1])
    ds2_ok = (all(name not in w for name in ('ds_deep_think','ds_outsource_nap','ds_shock_sit'))
              and 'ds_not_fat' not in w and 'ds_server_busy' not in w and bb is not None and no_overlap
              and fbb == (pet.sp.cell - bb[2], bb[1], pet.sp.cell - bb[0], bb[3]) and menus_ok
              and abs(shock_rate - SHOCK_WAKE_CHANCE) < 0.02 and pet.sp['ds_outsource_nap'].speak_frame is not None
              and all(a in pet.sp.anims for a in ('ds_not_fat', 'ds_deep_think', 'ds_outsource_nap', 'ds_server_busy', 'ds_shock_sit')))
    cfg_ok = ('tsun_proud' in pool and 'tsun_peek' in pool and SLEEP_TIMEOUT > ATTENTION_FIRST[1]
              and all(k in pool for k in ('idle_tea', 'idle_fishing', 'idle_butterfly'))
              and PLAY_HOLD['idle_fishing'][0] >= max(v[1] for k, v in PLAY_HOLD.items() if k != 'idle_fishing'))

    def step():
        if idx[0] >= len(steps):
            log.append(f'config: idle pool has tsun_proud + tsun_peek + idle_tea/idle_fishing/idle_butterfly, attention '
                       f'{ATTENTION_FIRST} s < sleep {SLEEP_TIMEOUT} s, fishing hold {PLAY_HOLD["idle_fishing"]} s is the longest '
                       + ('OK' if cfg_ok else 'MISMATCH'))
            log.append(f'config: ds_basin weight 0.4 with cooldown and rotation compensation, token/beg not in the random pool, tail zone {tb} (idle), token idle '
                       f'{TOKEN_IDLE} s < sleep, flipped frame -> mirrored zone {fb}, hungry after {HUNGRY_AFTER_MIN} awake min, meal windows {MEAL_WINDOWS} '
                       + ('OK' if ds_ok else 'MISMATCH'))
            log.append(f'config: round 2 idle weights deep_think {w.get("ds_deep_think")} / outsource_nap {w.get("ds_outsource_nap")} / '
                       f'shock_sit {w.get("ds_shock_sit")}, belly zone {bb} (idle; no overlap with the tail {tb}), flipped -> {fbb}, '
                       f'menus 玩耍+{{深度思考, 服务器繁忙, 震惊}} 互动+{{派活给她, 戳肚子}}, spam {SPAM_CLICKS} clicks / {SPAM_WINDOW} s, '
                       f'cooldown {BUSY_COOLDOWN} s, sleep-click shock rate {shock_rate:.3f} (seeded, target {SHOCK_WAKE_CHANCE}) '
                       + ('OK' if ds2_ok else 'MISMATCH'))
            ok = all(l.endswith('OK') for l in log)
            open(os.path.join(out, 'selftest.log'), 'w', encoding='utf-8').write('\n'.join(log) + f'\nRESULT {"PASS" if ok else "FAIL"}\n')
            print('\n'.join(log)); print('SELFTEST', 'PASS' if ok else 'FAIL')
            app.exit(0 if ok else 1)
            return
        t, fn, expect_after, extra = steps[idx[0]]
        pet.next_walk = pet.next_behaviour = 1e9      # no random walks / idle behaviours during the scripted tour
        pet._auto_ds = False                           # token / hunger / meal triggers only in their own steps
        fn()

        def check(i=idx[0], exp=expect_after, extra=extra):
            pet._tick()
            img = pet.grab()
            img.save(os.path.join(out, f'{i:02d}_{pet.state}_{pet.anim.name}.png'))
            got = pet.state
            ok = any(got == e or (e.endswith('*') and got.startswith(e[:-1])) for e in exp.split('|'))
            notes = []
            if 'say' in extra:
                ok &= bool(pet.bubble) and pet.bubble[0] == extra['say']; notes.append(f'says {extra["say"]}')
            if 'check' in extra:
                desc, fn = extra['check']
                good = bool(fn()); ok &= good; notes.append(desc + ('' if good else ' (NO)'))
            if extra.get('section'):
                s0, s1 = pet.anim.section
                ok &= s0 <= pet.frame_index() < s1 and pet.sec_rel is None; notes.append(f'in loop section [{s0},{s1})')
            if 'anim' in extra:
                ok &= pet.anim.name == extra['anim']; notes.append(f'anim=={extra["anim"]}')
            if 'bubble' in extra:
                has = pet.bubble is not None
                ok &= has == extra['bubble']; notes.append('bubble ' + ('shown' if has else 'none'))
            if extra.get('no_click'):
                ok &= not pet.click_timer.isActive(); notes.append('release after hold is not a click')
            bub = pet.bubble[0] if pet.bubble else '-'
            log.append(f'step {i:02d} expect={exp:11s} got={got:11s} anim={pet.anim.name:16s} frame={pet.frame_index():2d} '
                       f'pos=({pet.x()},{pet.y()}) bubble={bub} {" ".join(notes)} {"OK" if ok else "MISMATCH"}')
            idx[0] += 1
            nt = steps[idx[0]][0] if idx[0] < len(steps) else t + 0.3
            QTimer.singleShot(int(max(50, (nt - t - 0.25) * 1000)), step)   # steps run at ~t (no drift)
        QTimer.singleShot(250, check)
    QTimer.singleShot(300, step)


class _FakeEvt:
    def button(self):
        return Qt.LeftButton


class _FakePress(_FakeEvt):
    def position(self):
        return QPointF(128.0, 120.0)

    def globalPosition(self):
        return QPointF(500.0, 500.0)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        import traceback
        error = traceback.format_exc()
        base = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__))
        log = os.path.join(base, 'userdata', 'last_error.log')
        try:
            os.makedirs(os.path.dirname(log), exist_ok=True)
            with open(log, 'w', encoding='utf-8') as target:
                target.write(error)
        except OSError:
            pass
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, '蓝色大肥鱼 · 启动失败', '启动失败，详细信息已写入：\n' + log + '\n\n' + error[-1200:])
        sys.exit(1)
