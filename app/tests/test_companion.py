import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
import sys
import tempfile
import unittest
import json
import time
from types import SimpleNamespace
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from companion_memory import CompanionMemory
from PySide6.QtWidgets import QApplication, QLineEdit
from whale_pet import Sprites, Pet
from dsh_companion import PetCompanion
from pet_support import Preferences
from PySide6.QtCore import QPoint, QPointF, QRect, Qt, QEvent
from PySide6.QtGui import QMouseEvent
from conversation_reactions import keyword_reaction, REACTIONS, is_neutral_chat
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

    def test_profile_switch_isolates_sessions_and_discards_old_chat(self):
        self.comp.send('你好'); stale=self.last_call('/chat')[2]
        self.comp.set_dsh_profile('web')
        self.assertEqual(self.comp.client.profile,'web'); self.assertFalse(self.comp.connected)
        self.assertEqual(self.comp.profile_sessions['desktop'],'real'); self.assertEqual(self.comp.session_id,'')
        stale({'reply':'OLD-PROFILE-REPLY','action':'task'})
        self.assertNotIn('OLD-PROFILE',self.comp.last_persona_reply)
        self.comp.session_id='web-only'
        self.comp.set_dsh_profile('desktop'); self.assertEqual(self.comp.session_id,'real')
        self.comp.set_dsh_profile('web'); self.assertEqual(self.comp.session_id,'web-only')
        preferences=Preferences(str(Path(self.temp.name)/'prefs.json'))
        preferences.save(self.comp.preferences()); saved=preferences.load()
        self.assertEqual(saved['dsh_profile'],'web'); self.assertEqual(saved['dsh_profile_sessions']['desktop'],'real')

    def test_authoritative_status_stops_stale_thinking_without_replaying_history(self):
        self.comp.set_activity('thinking'); self.comp.activity.cursor=10
        self.comp.task_status_received({'sessionId':'real','cursor':10,'running':False,'state':'idle',
            'outcome':'unknown','pendingCalls':[],'approvalIds':[],'questionIds':[]})
        self.assertFalse(self.comp.busy);self.assertEqual(self.pet.state,'idle')
        self.comp.task_status_received({'sessionId':'real','cursor':12,'running':False,'state':'success',
            'pendingCalls':[],'approvalIds':[],'questionIds':[]})
        self.assertEqual(self.pet.state,'idle');self.assertIsNone(self.comp.pending_outcome)
        self.comp.set_activity('coding');self.comp.activity.cursor=20
        self.comp.task_status_received({'sessionId':'real','cursor':19,'running':False,'state':'idle'})
        self.assertTrue(self.comp.busy);self.assertEqual(self.comp.activity.state,'coding')
        self.comp.task_status_received({'sessionId':'other','cursor':30,'running':False,'state':'idle'})
        self.assertTrue(self.comp.busy)

    def test_snapshot_stream_baseline_and_poll_preserve_current_reply(self):
        self.comp.frame_received({'type':'snapshot','cursor':11,'records':[{'type':'event','event':{'seq':11,'type':'step/start','data':{}}}],
            'assistantStream':{'activeAttempt':{'stream':[{'time':1,'chunk':{'type':'text-delta','text':'PRIVATE'}}]}}})
        self.assertEqual(self.comp.activity.state,'replying')
        self.comp.last_stream_at=time.monotonic()
        self.comp.task_status_received({'sessionId':'real','cursor':11,'running':True,'state':'thinking',
            'pendingCalls':[],'approvalIds':[],'questionIds':[]})
        self.assertEqual(self.comp.activity.state,'replying')
        self.comp.task_status_received({'sessionId':'real','cursor':12,'running':True,'state':'coding',
            'pendingCalls':[{'id':'edit','name':'write','state':'coding'}],'approvalIds':[],'questionIds':[]})
        self.assertEqual(self.comp.activity.state,'coding');self.assertIn('edit',self.comp.activity.pending_tools)

    def test_thinking_plays_once_then_waits_for_real_progress(self):
        self.comp.set_activity('thinking')
        self.assertFalse(self.pet.anim.loop)
        duration=len(self.pet.anim)/self.pet.anim.fps
        self.assertEqual(self.pet.anim.fps,sprites['dsh_thinking'].fps)
        self.pet.t=self.pet.state_t=duration-.01
        self.pet.last_ms=self.pet.clock.elapsed()-20;self.pet._tick()
        self.assertEqual(self.pet.state,'idle')
        self.assertTrue(self.comp.busy);self.assertEqual(self.comp.activity.state,'thinking')
        self.pet.next_behaviour=self.pet.next_walk=self.pet.next_attention=1e9
        self.pet._auto_ds=False
        for _ in range(5):
            self.comp.frame_received({'type':'assistant-stream','frame':{'type':'chunk','chunk':{'type':'reasoning-delta','text':'still thinking'}}})
            self.comp.clip_deadline=0;self.comp.animation_tick();self.pet._tick()
            self.assertEqual(self.pet.state,'idle')
        self.comp.task_status_received({'sessionId':'real','cursor':44,'running':True,'state':'thinking',
            'pendingCalls':[],'approvalIds':[],'questionIds':[]})
        self.assertEqual(self.pet.state,'idle')
        self.comp.set_activity('coding');self.assertEqual(self.pet.anim.name,'dsh_typing')
        self.comp.set_activity('thinking');self.assertFalse(self.pet.anim.loop)
        self.assertEqual(self.pet.t,0)

    def test_normal_idle_actions_resume_after_single_thinking_clip(self):
        self.comp.set_activity('thinking')
        self.pet.t=self.pet.state_t=len(self.pet.anim)/self.pet.anim.fps
        self.pet._tick();self.assertEqual(self.pet.state,'idle')
        self.pet._auto_ds=False;self.pet._force_behaviour='wave'
        self.pet.next_attention=self.pet.next_walk=1e9
        self.pet.next_behaviour=-1;self.pet.t=0
        self.pet._tick();self.assertEqual(self.pet.state,'wave')
        self.comp.set_activity('thinking');self.comp.animation_tick()
        self.assertEqual(self.pet.state,'wave')
        self.assertTrue(self.comp.busy)
        self.comp.set_activity('coding');self.assertEqual(self.pet.anim.name,'dsh_typing')

    def test_manual_interruption_does_not_repeat_the_same_thinking_clip(self):
        self.comp.set_activity('thinking')
        self.pet.interact('rice');self.comp.set_activity('thinking')
        self.assertEqual(self.pet.state,'ds_rice_slurp')
        self.pet.set_state('idle');self.comp.animation_tick()
        self.assertEqual(self.pet.state,'idle')
        self.comp.set_activity('success');self.assertIsNotNone(self.comp.outcome_animation)

    def test_work_updates_preserve_complete_walk_and_fall_sequence(self):
        self.pet._auto_ds=False;self.pet.animation_origin='automatic'
        self.pet._start_walk()
        self.comp.set_activity('coding');self.assertEqual(self.pet.state,'walk')
        ms=0;self.pet.last_ms=0;self.pet.clock=SimpleNamespace(elapsed=lambda:ms)
        for _ in range(150):
            if self.pet.state=='trip':break
            ms+=50;self.pet._tick();self.comp.animation_tick()
        self.assertEqual(self.pet.state,'trip')
        for _ in range(100):
            if self.pet.state=='idle':break
            ms+=50;self.pet._tick()
            if self.pet.state=='trip':self.comp.animation_tick()
        self.assertEqual(self.pet.state,'idle')
        self.comp.animation_tick();self.assertEqual(self.pet.anim.name,'dsh_typing')

    def test_profile_credentials_do_not_fall_back_to_other_host(self):
        directory=Path(self.temp.name)
        record={'url':'http://127.0.0.1:12345','token':'a'*64}
        (directory/'dsh-bridge.json').write_text(json.dumps(record))
        self.comp.client._credentials(); self.assertEqual(self.comp.client.token,'a'*64)
        self.comp.client.set_profile('web')
        with self.assertRaises(OSError): self.comp.client._credentials()
        record.update(profile='web',token='b'*64)
        (directory/'dsh-bridge-web.json').write_text(json.dumps(record))
        self.comp.client._credentials(); self.assertEqual(self.comp.client.token,'b'*64)
        record['profile']='desktop'; (directory/'dsh-bridge-web.json').write_text(json.dumps(record))
        with self.assertRaises(ValueError): self.comp.client._credentials()

    def test_empty_input_drag_and_text_selection_and_position_save(self):
        window=self.comp.window; widget=window.input
        window.move(200,200); app.processEvents()
        local=QPoint(80,18); before=window.pos(); global_pos=widget.mapToGlobal(local)
        def event(kind,position,global_point,button,buttons):
            QApplication.sendEvent(widget,QMouseEvent(kind,QPointF(position),QPointF(global_point),button,buttons,Qt.NoModifier))
        event(QEvent.MouseButtonPress,local,global_pos,Qt.LeftButton,Qt.LeftButton)
        delta=QPoint(65,35)
        event(QEvent.MouseMove,local+delta,global_pos+delta,Qt.NoButton,Qt.LeftButton)
        event(QEvent.MouseButtonRelease,local+delta,global_pos+delta,Qt.LeftButton,Qt.NoButton)
        self.assertEqual(window.pos(),before+delta)
        self.assertEqual(self.comp.preferences()['input_x'],window.x())
        widget.setText('保留输入和选字'); before=window.pos(); global_pos=widget.mapToGlobal(local)
        event(QEvent.MouseButtonPress,local,global_pos,Qt.LeftButton,Qt.LeftButton)
        self.assertIsNone(widget.drag_offset)
        event(QEvent.MouseButtonRelease,local,global_pos,Qt.LeftButton,Qt.NoButton)
        self.assertEqual(window.pos(),before); self.assertEqual(widget.text(),'保留输入和选字')
        edge=QPoint(4,18); global_pos=widget.mapToGlobal(edge)
        event(QEvent.MouseButtonPress,edge,global_pos,Qt.LeftButton,Qt.LeftButton)
        event(QEvent.MouseMove,edge+delta,global_pos+delta,Qt.NoButton,Qt.LeftButton)
        event(QEvent.MouseButtonRelease,edge+delta,global_pos+delta,Qt.LeftButton,Qt.NoButton)
        self.assertEqual(window.pos(),before+delta); self.assertEqual(widget.text(),'保留输入和选字')

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

    def test_reply_follows_pet_and_ignores_input_position_with_screen_clamping(self):
        area=QRect(0,0,1400,1000)
        self.pet._screen_rect=lambda:area
        self.pet.move(600,600);self.comp.window.move(100,100)
        self.comp.send('你好');self.last_call('/chat')[2]({'reply':'主人，我在这里哦。','action':'chat'})
        bubble=self.comp.reply_bubble
        self.assertTrue(bubble.isVisible())
        self.assertLessEqual(abs(bubble.geometry().center().x()-self.pet.geometry().center().x()),1)
        self.assertEqual(bubble.geometry().bottom()+9,self.pet.y())
        before=bubble.pos()
        self.comp.window.move(50,800);bubble.tick()
        self.assertEqual(bubble.pos(),before)
        self.comp.window.hide();bubble.tick()
        self.assertEqual(bubble.pos(),before)
        self.pet.move(self.pet.pos()+QPoint(60,-80));bubble.tick()
        self.assertEqual(bubble.pos(),before+QPoint(60,-80))
        self.pet.move(0,0);bubble.tick()
        self.assertTrue(area.contains(bubble.geometry()))
        self.assertGreaterEqual(bubble.y(),self.pet.geometry().bottom()+8)
        self.pet.move(area.right()-self.pet.width()+1,600);bubble.tick()
        self.assertTrue(area.contains(bubble.geometry()))

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
        self.assertEqual(set(self.last_call('/emotion')[1]),{'text','turnId','sessionId'})
        self.assertEqual(self.last_call('/emotion')[1]['sessionId'],'real')
        stale=self.last_call('/emotion')[2]
        self.last_call('/chat')[2]({'reply':'好呀，今天过得怎么样？','action':'chat'})
        self.assertFalse(self.comp.chatting); self.assertTrue(self.comp.reply_bubble.isVisible())
        self.assertEqual(self.pet.state,'idle')
        self.comp.send('早安呀')
        self.assertEqual(len([c for c in self.calls if c[0]=='/emotion']),1)
        self.assertEqual(self.pet.state,'wave')
        stale({'emotion':'cry'}); self.assertEqual(self.pet.state,'wave')

    def test_stream_preview_shows_prose_without_completing_or_dispatching_and_stale_turns_are_ignored(self):
        self.comp.send('你好呀')
        _,body,done,_=self.last_call('/chat')
        self.assertTrue(body['stream'])
        self.comp.client.chat_partial.emit({'turnId':body['turnId'],'reply':'主人，你好'})
        self.assertTrue(self.comp.reply_bubble.isVisible());self.assertTrue(self.comp.chatting)
        self.assertEqual(self.comp.last_persona_reply,'主人，你好')
        self.assertFalse(self.comp.memory.data['recent']);self.assertFalse(any(call[0]=='/prompt' for call in self.calls))
        done({'reply':'主人，你好呀。','action':'chat'})
        self.assertFalse(self.comp.chatting);self.assertEqual(len(self.comp.memory.data['recent']),2)
        self.comp.send('聊聊今天吧');self.comp.select('other')
        self.comp.client.chat_partial.emit({'turnId':body['turnId'],'reply':'STALE-PREVIEW'})
        self.assertNotIn('STALE',self.comp.last_persona_reply)

    def test_neutral_or_failed_classifier_keeps_default_and_reply_succeeds(self):
        for failure in (False, True):
            self.comp.send('我们聊些什么呢')
            request=self.last_call('/emotion')
            request[3]('slow classifier') if failure else request[2]({'emotion':'none'})
            self.last_call('/chat')[2]({'reply':'今天是星期三。','action':'chat'})
            self.assertEqual(self.pet.state,'idle')
            self.assertEqual(self.comp.last_persona_reply,'今天是星期三。')

    def test_exact_neutral_questions_skip_classifier_without_swallowing_emotions_or_work(self):
        for text in ('你是谁？', '/聊 你叫什么名字', '今天是星期几', '现在几点？', '今天想吃什么？用一句话回答。'):
            with self.subTest(text=text): self.assertTrue(is_neutral_chat(text))
        for text in ('好难过，你是谁？', '你是谁啊，我好奇', '帮我看看今天吃什么', '修改文件，现在几点', '今天有件特别的事想告诉你'):
            with self.subTest(text=text): self.assertFalse(is_neutral_chat(text))
        self.comp.send('今天想吃什么？用一句话回答。')
        self.assertEqual([c[0] for c in self.calls], ['/chat'])
        self.last_call('/chat')[2]({'reply':'想吃小饼干。','action':'chat'})
        self.assertEqual(self.pet.state,'idle')
        self.assertEqual(self.comp.last_persona_reply,'想吃小饼干。')

if __name__=='__main__': unittest.main()
