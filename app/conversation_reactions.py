"""Small, bounded vocabulary for the companion's existing conversation clips."""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Reaction:
    state: str
    length: float = 0.0
    animation: str | None = None
    cooldown: float = 6.0

# Model output is a semantic label, never a free-form animation or command.
REACTIONS = {
    'teased': Reaction('tsun_hmph', 2.0),
    'shy': Reaction('praised_shy', 3.2),
    'happy': Reaction('happy'),
    'proud': Reaction('tsun_proud', 3.0),
    'curious': Reaction('curious', 3.0),
    'sad': Reaction('expr', 3.5, 'expr_sad'),
    'surprised': Reaction('surprised'),
    'smile': Reaction('expr', 3.0, 'expr_smile'),
    'greeting': Reaction('wave', 2.4),
    'grateful': Reaction('curtsy'),
    'petting': Reaction('petted', 2.0),
    'tail': Reaction('ds_tail_touch', cooldown=15.0),
    'dance': Reaction('dance', 3.0, cooldown=10.0),
    'tea': Reaction('tea', 4.0, cooldown=10.0),
    'stretch': Reaction('stretch', cooldown=10.0),
    'gift': Reaction('gift_cookie', 2.4, cooldown=10.0),
    'peek': Reaction('tsun_peek', 2.2),
    'cry': Reaction('sad', 3.0),
}

# Work instructions and quoted/reported dialogue need the model's judgement.
WORK_WORDS = r'修复|修改|创建|代码|脚本|文件|程序|测试|构建|搜索|配置|打包|日志|命令|提交|翻译|总结|分析|台词|句子|关键词|分类|触发|表情|动画|例如|比如|他说|她说|别人|骂我'

def is_neutral_chat(text):
    """Skip a second inference only for whole, clearly neutral questions."""
    text = text.strip()
    if text.startswith('/聊 '): text = text[3:].strip()
    text = re.sub(r'[\s，,。.!！?？；;]', '', text)
    text = re.sub(r'(?:请)?用一句话回答$', '', text)
    return bool(re.fullmatch(
        r'(?:你是谁|你叫什么(?:名字)?|今天(?:是)?星期几|现在(?:是)?几点|'
        r'(?:今天|今晚|早上|中午|晚上)?(?:想吃什么|吃什么|想喝什么))', text))

def keyword_reaction(text):
    """Only obvious live conversation; ambiguity goes to the chat model."""
    text = text.strip()
    if text.startswith('/聊 '): text = text[3:].strip()
    if not text or len(text) > 100 or text.startswith('/'):
        return 'none'
    if re.search(WORK_WORDS, text) or re.search(r'["“”「」『』`]|不是|不要|别再|别叫|不许|不想|不喜欢|不准|不用|这个词|这个字|词语|词义|定义|拼音|什么意思|的意思|怎么读|怎么写|不(?:太|怎么|算|很)?(?:可爱|漂亮|好看|聪明|厉害|开心|高兴)', text):
        return 'none'
    rules = (
        ('tail', r'(?:摸|碰|捏)(?:摸|一下|一摸|你的|你的那条|她的|蓝色大肥鱼的|一下你的|你的)?尾巴'),
        ('petting', r'摸摸|摸头|摸一下头|揉揉(?:你|脑袋|头)'),
        ('teased', r'^(?:(?:你|蓝色大肥鱼|大肥鱼|小鱼)(?:是|真是|这个|个|就是|真|好|怎么这么)?\s*)?(?:大?笨蛋|小笨蛋|笨笨|笨鱼|傻鱼|大傻瓜)[呀啊哦啦呢哼～~!！。,.，？?]*$|你.{0,5}(?:笨蛋|傻瓜)|(?:生气了|气鼓鼓|哼一个)'),
        ('shy', r'可爱|漂亮|好看|喜欢你|爱你|亲亲|害羞'),
        ('proud', r'真棒|好棒|厉害|做得好|干得漂亮|夸夸|聪明|能干'),
        ('sad', r'难过|伤心|委屈|孤独|不开心|好累|累死|安慰'),
        ('dance', r'跳(?:个|支|一支|一下|舞)?舞|跳舞'),
        ('tea', r'喝茶|泡茶|来杯茶'),
        ('stretch', r'伸(?:个)?懒腰|伸展一下'),
        ('gift', r'给我.{0,3}饼干|要.{0,2}饼干'),
        ('cry', r'哭一个|哭哭'),
        ('peek', r'偷看|探头'),
        ('grateful', r'谢谢|辛苦(?:你|啦|了)|行(?:个)?礼'),
        ('greeting', r'你好|早上好|早安|晚上好|晚安|下午好|再见|拜拜|我回来|挥(?:个|下|一挥)?手|hello|hi\b'),
        ('happy', r'开心|高兴|好耶|太好啦|太好了'),
        ('surprised', r'吓你|吓一跳|惊讶|吓到了|哇[！!]|惊喜'),
        ('curious', r'好奇|猜猜|猜一下|猜一猜'),
    )
    return next((name for name, pattern in rules if re.search(pattern, text, re.I)), 'none')
