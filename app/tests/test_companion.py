import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import tempfile
import unittest
import json
import time
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from companion_memory import CompanionMemory
from PySide6.QtWidgets import QApplication, QLineEdit
from whale_pet import Sprites, Pet
from dsh_companion import PetCompanion
from conversation_reactions import keyword_reaction, REACTIONS
app=QApplication.instance() or QApplication([])
sprites=Sprites(str(APP/'assets'))

class MemoryTests(unittest.TestCase):
    def test_explicit_memory_update_delete_and_credential_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            m=CompanionMemory(folder)
            m.remember('称呼=主人'); m.remember('称呼=舰长')
            self.assertEqual(len(m.data['facts']),1); self.assertEqual(m.data['facts'][0]['text'],'舰长')
            m.add_turn('我好累呀','要不要休息一下'); self.assertEqual(len(m.data['facts']),1)
            m.remember('api_key=sk-1234567890123456789012345'); self.assertEqual(len(m.data['facts']),1)
            self.assertEqual(CompanionMemory(folder).data['facts'][0]['text'],'舰长')
            m.forget('称呼'); self.assertFalse(m.data['facts'])

    def test_bounded_recent_summary_ttl_and_clear_are_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            m=CompanionMemory(folder); m.remember('爱好=小点心')
            for i in range(30): m.add_turn(str(i)+'鱼'*600,'好'*100)
            self.assertLessEqual(len(m.data['recent']),12)
            self.assertLessEqual(sum(len(x['text']) for x in m.data['recent']),4000)
            self.assertLessEqual(len(m.data['summary']),1000); self.assertTrue(CompanionMemory(folder).data['summary'])
            m.clear(); self.assertTrue(m.data['facts']); self.assertFalse(m.data['recent'])
            m.add_turn('hello','你好'); m.data['recent'][0]['at']=time.time()-8*86400; m.save()
            self.assertEqual(len(m.data['recent']),1)
            m.clear(True); self.assertFalse(m.data['facts']); self.assertTrue(m.data['recent'])

    def test_history_redacts_secrets_without_inventing_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            m=CompanionMemory(folder)
            m.add_turn('token=1234567890abcdef','我完成了任务')
            self.assertNotIn('1234567890abcdef',m.path.read_text(encoding='utf-8'))
            self.assertFalse(m.data['facts'])

class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.pet=Pet(sprites,selftest=True); self.pet.timer.stop(); self.pet.show()
        self.comp=PetCompanion(self.pet,self.temp.name,mode='dsh')
        self.pet._assistant=self.comp; self.comp.open_window(); self.comp.client.timer.stop(); self.comp.connected=True
        self.comp.project=str(APP); self.comp.session_id='real'
        self.comp.session_rows={'real':{'sessionId':'real','cwd':str(APP),'title':'真正的任务','workspaceId':'workspace'}}
        self.calls=[]
        self.comp.client.request=lambda route,data=None,callback=None,failure=None:self.calls.append((route,data,callback,failure))

    def tearDown(self):
        self.comp.shutdown(); self.comp.window.deleteLater(); self.pet.deleteLater(); app.processEvents(); self.temp.cleanup()

    def last_call(self, route):
        return next(call for call in reversed(self.calls) if call[0] == route)

    def test_one_input_persona_reply_visible_raw_task_output_stays_in_dsh(self):
        self.assertEqual(len([w for w in self.comp.window.findChildren(QLineEdit) if w.isVisible()]),1)
        self.comp.window.input.setText('今天开心吗'); self.comp.send('今天开心吗')
        route,body,done,failed=self.calls[-1]; self.assertEqual(route,'/chat')
        done({'reply':'哼，主人来了，我就稍微开心一点啦。','action':'chat'})
        self.assertTrue(self.comp.reply_bubble.isVisible()); self.assertIn('稍微开心',self.comp.reply_bubble.label.text())
        self.assertEqual(self.comp.window.input.text(),'')
        self.comp.display_event({'type':'assistant/message','data':{'content':[{'type':'text','text':'RAW-TECHNICAL-OUTPUT'}]}})
        self.assertNotIn('RAW-TECHNICAL',self.comp.reply_bubble.label.text())
        self.assertNotIn('RAW-TECHNICAL',self.comp.window.input.toolTip())

    def test_task_routes_original_instruction_and_pins_session(self):
        self.comp.window.input.setText('修复显示问题'); self.comp.send('修复显示问题')
        self.last_call('/chat')[2]({'reply':'我准备看看','action':'task'})
        route,body,done,_=self.calls[-1]; self.assertEqual(route,'/prompt'); self.assertEqual(body['text'],'修复显示问题')
        self.assertFalse(self.comp.auto_follow); self.assertEqual(body['sessionId'],'real')
        self.comp.window.input.setText('下一条先写在这里'); done({'accepted':True})
        self.assertEqual(self.comp.window.input.text(),'下一条先写在这里')

    def test_reports_never_dispatch_and_old_chat_cannot_reply_after_selection(self):
        self.comp.chat('',event='report'); done=self.calls[-1][2]
        done({'reply':'在读取文件哦','action':'task'})
        self.assertEqual([c[0] for c in self.calls],['/chat'])
        self.comp.send('你好'); old=self.calls[-1][2]
        self.comp.select('another'); old({'reply':'STALE-REPLY','action':'task'})
        self.assertNotIn('STALE-REPLY',self.comp.last_persona_reply)
        self.assertFalse(any(c[0]=='/prompt' for c in self.calls))

    def test_follow_only_current_project_and_never_validation_tasks(self):
        self.comp.auto_follow=True
        self.comp.sessions_changed([{'sessionId':'validation','running':True,'cwd':str(APP/'_validation/check')},
            {'sessionId':'unrelated','running':True,'cwd':'Y:/other'},self.comp.session_rows['real']])
        self.assertEqual(self.comp.session_id,'real')
        self.comp.select(''); self.comp.auto_follow=False
        self.comp.sessions_changed([{'sessionId':'old-idle-test','cwd':str(APP),'running':False}])
        self.assertEqual(self.comp.session_id,'')

    def test_explicit_task_creates_visible_session_before_prompt_and_chat_failure_retains_input(self):
        self.comp.session_id=''; self.comp.window.input.setText('/任务 修复显示')
        self.comp.send('/任务 修复显示')
        self.assertEqual(self.calls[-1][0],'/session')
        self.calls[-1][2]({'sessionId':'new','cwd':str(APP),'workspaceId':'workspace','title':'蓝色大肥鱼 · 修复显示'})
        prompt=next(c for c in self.calls if c[0]=='/prompt'); self.assertEqual(prompt[1]['sessionId'],'new')
        prompt[2]({'accepted':True})
        self.comp.window.input.setText('你好'); self.comp.send('你好'); self.calls[-1][3]('暂时连接不上')
        self.assertEqual(self.comp.window.input.text(),'你好'); self.assertFalse(self.comp.chatting)

    def test_keyword_reactions_cover_conversation_and_ignore_quoted_work(self):
        for text, emotion in {'大笨蛋':'teased','你个大笨蛋！':'teased','你真可爱':'shy','你好厉害':'proud',
            '我今天好难过':'sad','谢谢你':'grateful','早安呀':'greeting','给我跳个舞吧':'dance',
            '来喝茶吧':'tea','让我摸摸你的头':'petting','我能摸一下你的尾巴吗':'tail',
            '猜猜我带了什么':'curious','给我一块饼干':'gift'}.items():
            with self.subTest(text=text): self.assertEqual(keyword_reaction(text),emotion)
        for text in ('你不是笨蛋','不要跳舞','把“大笨蛋”写进文件','他说我是笨蛋','修复可爱的动画',
                     '/任务 做个挥手表情','比如我说大笨蛋','不喜欢你','别叫我笨蛋','难过这个词是什么意思','跳舞的拼音是什么','你不太厉害'):
            with self.subTest(text=text): self.assertEqual(keyword_reaction(text),'none')

    def test_teasing_is_immediate_and_reply_does_not_restart_or_override_it(self):
        self.comp.send('大笨蛋')
        self.assertEqual(self.pet.state,'tsun_hmph'); self.assertEqual(self.pet.animation_origin,'conversation')
        self.pet.t=0.7
        self.calls[-1][2]({'reply':'才没有呢，你再看看！','action':'chat','emotion':'shy'})
        self.assertEqual(self.pet.state,'tsun_hmph'); self.assertEqual(self.pet.t,0.7)
        self.assertTrue(self.comp.reply_bubble.isVisible())
        self.comp.send('大笨蛋'); self.assertEqual(self.pet.t,0.7)

    def test_model_reaction_uses_existing_clip_and_work_resumes_afterward(self):
        self.comp.set_activity('coding')
        self.comp.send('今天有件特别的事想告诉你')
        # A classification may finish first without completing the reply.
        self.last_call('/emotion')[2]({'emotion':'surprised'})
        self.assertTrue(self.comp.chatting)
        self.last_call('/chat')[2]({'reply':'咦，快告诉我呀。','action':'chat'})
        self.assertEqual(self.pet.state,'surprised')
        self.comp.set_activity('coding'); self.assertEqual(self.pet.state,'surprised')
        self.pet.set_state('idle'); self.comp.animation_tick()
        self.assertEqual(self.pet.anim.name,'dsh_typing')

    def test_manual_tail_queue_is_preserved_and_pending_reaction_expires(self):
        self.pet.animation_origin='manual'; self.pet.start_behaviour('ds_tail_touch')
        state=self.pet.state; queue=list(self.pet.queue)
        self.comp.react('shy')
        self.assertEqual(self.pet.state,state); self.assertEqual(self.pet.queue,queue)
        self.assertTrue(self.comp.pending_reaction)
        emotion,_,touch=self.comp.pending_reaction
        self.comp.pending_reaction=(emotion,time.monotonic()-1,touch)
        self.pet.set_state('idle'); self.comp.reaction_tick()
        self.assertEqual(self.pet.state,'idle'); self.assertIsNone(self.comp.pending_reaction)

    def test_task_report_unknown_and_late_emotions_do_not_play(self):
        self.comp.send('帮我修复文件')
        classify=self.last_call('/emotion')[2]
        self.last_call('/chat')[2]({'reply':'我来看看。','action':'task'})
        classify({'emotion':'dance'})
        self.assertNotEqual(self.pet.state,'dance')
        self.comp.sending=False
        self.comp.chat('',event='report')
        self.calls[-1][2]({'reply':'正在检查。','action':'chat','emotion':'teased'})
        self.assertNotEqual(self.pet.state,'tsun_hmph')
        self.comp.react('__arbitrary_animation__'); self.assertIsNone(self.comp.pending_reaction)
        self.comp.send('今天有件特别的事')
        self.pet._touch()
        self.last_call('/emotion')[2]({'emotion':'curious'})
        self.last_call('/chat')[2]({'reply':'让我猜猜。','action':'chat'})
        self.assertNotEqual(self.pet.state,'curious')

    def test_standalone_and_all_mapped_clips_keep_valid_transitions(self):
        self.comp.set_mode('standalone'); self.comp.connected=False
        self.comp.send('大笨蛋'); self.assertEqual(self.pet.state,'tsun_hmph')
        for emotion in REACTIONS:
            self.pet.set_state('idle'); self.comp.reaction_at=-1e12; self.comp.reaction_last.clear()
            self.comp.react(emotion)
            self.assertEqual(self.pet.state,REACTIONS[emotion].state)
            self.assertTrue(len(self.pet.anim.frames))
            self.assertEqual(self.pet.animation_origin,'conversation')

    def test_reply_does_not_wait_for_emotion_and_keyword_skips_classifier(self):
        self.comp.send('我们聊点日常吧')
        self.assertEqual([c[0] for c in self.calls],['/chat','/emotion'])
        self.assertEqual(set(self.last_call('/emotion')[1]),{'text','turnId'})
        stale=self.last_call('/emotion')[2]
        self.last_call('/chat')[2]({'reply':'好呀，今天过得怎么样？','action':'chat'})
        self.assertFalse(self.comp.chatting); self.assertTrue(self.comp.reply_bubble.isVisible())
        self.assertEqual(self.pet.state,'idle')
        self.comp.send('早安呀')
        self.assertEqual(len([c for c in self.calls if c[0]=='/emotion']),1)
        self.assertEqual(self.pet.state,'wave')
        stale({'emotion':'cry'}); self.assertEqual(self.pet.state,'wave')

    def test_neutral_or_failed_classifier_keeps_default_and_reply_succeeds(self):
        for failure in (False, True):
            self.comp.send('今天星期几')
            request=self.last_call('/emotion')
            request[3]('slow classifier') if failure else request[2]({'emotion':'none'})
            self.last_call('/chat')[2]({'reply':'今天是星期三。','action':'chat'})
            self.assertEqual(self.pet.state,'idle')
            self.assertEqual(self.comp.last_persona_reply,'今天是星期三。')

if __name__=='__main__': unittest.main()
