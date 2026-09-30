import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
import sys,random,unittest
from collections import Counter
from unittest.mock import patch
APP=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(APP))
from animation_policy import BalancedChooser,IDLE_RULES,WORK_POOLS,SUCCESS_RULES,tool_activity
from dsh_companion import Activity,PetCompanion
from whale_pet import Pet,Sprites
from pet_support import configure_fonts
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint
app=QApplication.instance() or QApplication([]); configure_fonts(app); sprites=Sprites(str(APP/'assets'))
def event(seq,kind,**data): return {'seq':seq,'type':kind,'data':data}

class PolicyTests(unittest.TestCase):
    def test_tool_semantics_and_shell_reads(self):
        for tool,state in {'read_file':'reading','list_directory':'reading','apply_patch':'coding','web_search':'searching',
                           'spawn_agent':'delegating','ask_user_question':'waiting','unknown_tool':'working'}.items():
            self.assertEqual(tool_activity(tool),state)
        self.assertEqual(tool_activity('exec_command',{'cmd':'rg -n test src'}),'reading')
        self.assertEqual(tool_activity('exec_command',{'cmd':'npm run build'}),'executing')
        self.assertEqual(tool_activity('exec_command',{'cmd':'cat file; rm file'}),'executing')

    def test_installed_harness_names_and_result_source(self):
        for tool,args,state in (
            ('pwsh',{'command':'Get-Content README.md'},'reading'),
            ('bash',{'command':'npm run build'},'executing'),
            ('str_replace_editor',{'command':'view'},'reading'),
            ('str_replace_editor',{'command':'str_replace'},'coding'),
            ('subagent',{},'delegating'),('list_subagent_models',{},'reading'),
            ('readFile',{},'reading'),('send_message_to_thread',{},'working'),
            ('run_code',{},'executing'),
        ): self.assertEqual(tool_activity(tool,args),state)
        activity=Activity(); activity.consume(event(0,'turn/start'))
        activity.consume(event(1,'tool/call',name='read',callId='installed-read',arguments={'file_path':'sample.txt'}))
        self.assertEqual(activity.state,'reading')
        self.assertEqual(activity.consume(event(2,'tool/result',message={'role':'tool','source':{'kind':'tool','callId':'installed-read'},'content':[]})),'thinking')
        activity.consume(event(3,'tool/call',name='write',callId='installed-write'))
        self.assertEqual(activity.consume(event(4,'tool/result',message={'toolCallId':'installed-write','isError':True})),'retrying')
        self.assertFalse(activity.pending_tools)
    def test_tools_approvals_and_questions_survive_stream_chunks(self):
        activity=Activity(); activity.consume(event(0,'turn/start'))
        activity.consume(event(1,'tool/call',name='read_file',callId='read'))
        self.assertEqual(activity.stream('text-delta'),'reading')
        activity.consume(event(2,'approval/asked',id='approve'))
        self.assertEqual(activity.stream('reasoning-delta'),'waiting')
        self.assertEqual(activity.consume(event(3,'approval/decided',id='approve')),'reading')
        activity.consume(event(4,'tool/call',name='write_file',callId='write'))
        self.assertEqual(activity.consume(event(5,'tool/result',message={'callId':'write'})),'reading')
        self.assertEqual(activity.consume(event(6,'tool/result',message={'callId':'read'})),'thinking')

    def test_recovered_requests_and_terminal_outcomes(self):
        activity=Activity(); activity.consume(event(0,'turn/start'))
        self.assertEqual(activity.consume(event(1,'request/error',error={'statusCode':429})),'busy')
        self.assertEqual(activity.consume(event(2,'step/start')),'thinking')
        self.assertEqual(activity.consume(event(3,'turn/end',reason={'kind':'completed'})),'success')
        self.assertEqual(activity.consume(event(4,'turn/end',reason={'kind':'interrupted'})),'idle')
        self.assertEqual(activity.consume(event(5,'turn/end',reason={'kind':'unknown'})),'idle')
        self.assertEqual(activity.consume(event(6,'turn/end',reason={'kind':'failed'})),'error')

    def test_long_run_has_no_missing_clips_repeats_or_cooldown_violations(self):
        rules={r.name:r for r in IDLE_RULES}
        for seed in range(10):
            picker=BalancedChooser(IDLE_RULES,random.Random(seed)); counts=Counter(); recent=[]; last={}; families={}
            for now in range(0,7200,20):
                name=picker.choose(now)
                if not name: continue
                rule=rules[name]; family=rule.family or name
                self.assertNotIn(name,recent[-2:])
                self.assertGreaterEqual(now-last.get(name,-1e6),rule.cooldown)
                self.assertGreaterEqual(now-families.get(family,-1e6),rule.cooldown)
                last[name]=families[family]=now; counts[name]+=1; recent.append(name)
                picker.note(name,now) # pet state entry repeats the accounting note
            self.assertEqual(set(counts),set(rules))
            self.assertGreaterEqual(min(counts.values()),10)
            self.assertLess(max(counts.values())/min(counts.values()),3)

    def test_completion_cooldown_and_pool_context(self):
        chooser=BalancedChooser(SUCCESS_RULES,random.Random(0))
        self.assertEqual(chooser.choose(0),'dsh_success')
        self.assertNotEqual(chooser.choose(5),'dsh_success')
        self.assertNotEqual(chooser.choose(10),'dsh_success')
        for state,rules in WORK_POOLS.items():
            self.assertTrue(all(rule.name in sprites.anims for rule in rules),state)
        idle={rule.name for rule in IDLE_RULES}
        self.assertFalse(idle.intersection({'ds_outsource_nap','ds_shock_sit','ds_server_busy','ds_code_eureka','ds_beg_rice','ds_rice_slurp'}))

    def test_manual_actions_finish_before_work_or_completion_and_tokens_do_not_restart(self):
        pet=Pet(sprites,selftest=True); pet.timer.stop(); pet.show()
        comp=PetCompanion(pet,APP/'_validation/missing-bridge',mode='dsh'); pet._assistant=comp
        comp.client.timer.stop(); comp.animation_timer.stop()
        try:
            pet.animation_origin='automatic'; pet.start_behaviour('idle_fishing')
            comp.set_activity('reading'); self.assertEqual(pet.anim.name,'curious')
            pet.do_action('idle_fishing',2); comp.set_activity('coding'); self.assertEqual(pet.state,'idle_fishing')
            pet.set_state('idle'); comp.set_activity('reading')
            pet.t=1.25; comp.frame_received({'type':'assistant-stream','frame':{'type':'chunk','chunk':{'type':'text-delta','text':'x'}}})
            # Simulate a live tool: the reducer priority is covered above; same state never restarts a clip.
            comp.set_activity('reading'); pet.t=1.25; comp.set_activity('reading'); self.assertEqual(pet.t,1.25)
            pet.interact('rice'); comp.set_activity('coding'); self.assertEqual(pet.state,'ds_rice_slurp')
            comp.set_activity('success'); self.assertEqual(pet.state,'ds_rice_slurp')
            pet.set_state('idle'); comp.show_outcome(); self.assertEqual(pet.anim.name,'dsh_success')
            pet.interact('rice'); comp.finish_animation(); self.assertEqual(pet.state,'ds_rice_slurp')
            pet.set_state('idle'); comp.set_activity('waiting'); self.assertEqual(pet.anim.name,'dsh_waiting')
            self.assertTrue(pet.anim.loop)
            pet.t=0; a=pet.frame_index(); pet.t=.25; self.assertNotEqual(a,pet.frame_index())
        finally:
            comp.shutdown(); pet.deleteLater(); app.processEvents()

    def test_due_idle_action_not_starved_by_walk_and_active_cursor_prevents_sleep(self):
        pet=Pet(sprites,selftest=True); pet.timer.stop(); pet._auto_ds=False; pet._cursor_override=QPoint(-2000,-2000)
        pet._cur_anchor=pet._cursor_override; pet.next_attention=1e9; pet._force_behaviour='wave'
        try:
            pet.next_walk=-1; pet.next_behaviour=-3; pet.t=0; pet._tick()
            self.assertEqual(pet.state,'wave')
            pet.set_state('idle'); pet.next_walk=pet.next_behaviour=1e9
            import time
            pet.last_input=time.monotonic()-1000; pet.last_cursor_move=time.monotonic(); pet._tick()
            self.assertEqual(pet.state,'idle')
        finally:
            pet.deleteLater(); app.processEvents()
if __name__=='__main__': unittest.main()