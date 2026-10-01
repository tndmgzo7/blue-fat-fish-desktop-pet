"""Exercise imported original frames, feeding and swimming through real Qt events/ticks."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,sys,tempfile,unittest,zipfile
from unittest.mock import patch
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint,QPointF,QRect,Qt,QEvent
from PySide6.QtGui import QImage,QMouseEvent,QRegion
from whale_pet import Pet,Sprites,_FakePress,_FakeEvt,FULL_BELLY_SECONDS
from swim_behavior import SWIM_STATES,SWIM_DURATION,SWIM_HOVER_OFFSET,SWIM_RENDER_SCALE
from animation_policy import IDLE_RULES,BalancedChooser
from pet_support import configure_fonts,Preferences
from dsh_companion import PetCompanion
from companion_bubble import ReplyBubble
app=QApplication.instance() or QApplication([]);configure_fonts(app)
sprites=Sprites(str(APP/'assets'))

class FoodSwimTests(unittest.TestCase):
    def setUp(self):
        self.pet=Pet(sprites,selftest=True);self.pet.timer.stop();self.pet.show()
        self.pet._screen_rect=lambda:QRect(0,0,1600,900)
        self.pet.move(450,self.pet._ground_y())
        self.pet.next_attention=self.pet.next_walk=self.pet.next_behaviour=1e9
        self.pet._auto_ds=False;self.pet._cursor_override=QPoint(-2000,-2000)
        self.ms=0;self.pet.last_ms=0;self.pet.clock=SimpleNamespace(elapsed=lambda:self.ms)
    def tearDown(self):
        self.pet.click_timer.stop();self.pet.deleteLater();app.processEvents()
    def step(self,count=1,ms=50):
        for _ in range(count):self.ms+=ms;self.pet._tick()
    def until(self,state,limit=450):
        for _ in range(limit):
            if self.pet.state==state:return
            self.step()
        self.fail('State not reached: '+state+'; currently '+self.pet.state)
    def test_imported_frames_are_complete_unchanged_rgba_and_original_speed(self):
        manifest=json.loads((APP/'assets/extra/additions.json').read_text(encoding='utf-8'))
        files={}
        for source in manifest['sources']:
            path=APP.parents[2]/source['archive']
            if not path.is_file():continue  # Source-only releases retain per-frame original digests.
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),source['sha256'])
            with zipfile.ZipFile(path) as z:
                for name in z.namelist():
                    if name.startswith('whale-pet/frames/') and name.endswith('.png'):
                        files[name.removeprefix('whale-pet/frames/')]=z.read(name)
        total=0
        for name,meta in manifest['animations'].items():
            anim=sprites[name];self.assertEqual(anim.fps,10);self.assertEqual(len(anim),meta['frames'])
            for index in range(len(anim)):
                relative=f"{meta['folder']}/{index:02d}.png"
                path=APP/'assets/extra'/relative
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),meta['frame_sha256'][index])
                if relative in files:self.assertEqual(path.read_bytes(),files[relative])
                image=QImage(str(path));self.assertTrue(image.hasAlphaChannel())
                self.assertEqual([image.width(),image.height()],meta['canvas']);total+=1
        self.assertEqual(total,257)
    def test_feed_to_full_plays_after_eating_then_exits_without_repeating_forever(self):
        for food in ('feed','rice'):
            self.pet.set_state('idle');self.pet.hunger=.8;self.pet.interact(food)
            self.assertEqual(self.pet.hunger,1)
            self.assertEqual(self.pet.queue,[('ds_full_belly',None,FULL_BELLY_SECONDS)])
            self.until('ds_full_belly')
            while self.pet.frame_index()<35:self.step()
            self.assertEqual(self.pet.bubble[0],'嗝…好撑')
            while self.pet.frame_index()<51:self.step()
            self.assertEqual(self.pet.bubble[0],'吃、吃不下了…')
            self.until('idle',limit=300)
            self.step(10);self.assertEqual(self.pet.state,'idle')
        self.pet.hunger=.1;self.pet.interact('feed')
        self.assertEqual(self.pet.queue,[('happy',None,0.0)])
    def test_manual_swim_is_bounded_moves_and_does_not_leap_without_clicks(self):
        self.pet.do_action('swim',0)
        self.assertGreaterEqual(self.pet.swim_duration,SWIM_DURATION[0])
        self.assertEqual((self.pet.width(),self.pet.height()),(256,384))
        x=self.pet.x();self.step(20);self.assertNotEqual(self.pet.x(),x)
        states=set()
        for _ in range(520):
            states.add(self.pet.state)
            if self.pet.state=='idle':break
            self.step()
            if self.pet.state in SWIM_STATES:self.assertTrue(self.pet._screen_rect().contains(self.pet.geometry()))
        self.assertNotIn('swim_leap',states);self.assertEqual(self.pet.state,'idle')
        self.assertEqual((self.pet.width(),self.pet.height()),(256,256))
        self.assertEqual(self.pet.y(),self.pet._ground_y())
    def test_single_click_leaps_at_loop_boundary_and_resumes_matching_direction_frame_one(self):
        for direction in (-1,1):
            self.pet.start_swim(duration=20);self.pet.swim_dir=direction
            self.pet._resume_swim(0)
            self.step(3);self.pet.mousePressEvent(_FakePress());self.pet.mouseReleaseEvent(_FakeEvt())
            self.assertTrue(self.pet.swim_pending_leap);self.assertFalse(self.pet.click_timer.isActive())
            self.pet.click_timer.stop();self.pet._single_click()
            self.assertEqual(self.pet.state,'swim');self.assertTrue(self.pet.swim_pending_leap)
            self.until('swim_leap',limit=40)
            self.assertEqual(self.pet.anim.name,self.pet.swim_family+'_leap'+('_left' if direction<0 else ''))
            self.until('swim',limit=45);self.assertEqual(self.pet.frame_index(),1)
    def test_turning_at_edges_uses_two_frame_turn_and_resumes_frame_two(self):
        self.pet.start_swim(duration=20);self.pet.swim_dir=1;self.pet._resume_swim(0)
        right=self.pet._screen_rect().right()+1-self.pet.width()
        self.pet.swim_x=right;self.pet.move(right,self.pet.y())
        self.until('swim_turn',limit=40)
        self.assertEqual(self.pet.anim.name,'ds_swim_full_turn_to_left')
        self.assertEqual(self.pet.swim_dir,-1)
        self.until('swim',limit=6);self.assertEqual(self.pet.frame_index(),2)
    def test_menu_pause_delayed_tick_and_repeated_clicks_cannot_make_swimming_infinite(self):
        self.pet.start_swim(duration=14);self.pet.menu_open=True;self.step(ms=30000)
        self.assertEqual(self.pet.swim_elapsed,0);self.pet.menu_open=False
        self.pet.mouseDoubleClickEvent(_FakeEvt());self.assertTrue(self.pet.swim_pending_leap)
        self.step(ms=30000);self.assertEqual(self.pet.state,'swim_return')
        for _ in range(30):
            if self.pet.state=='idle':break
            self.pet._single_click();self.step()
        self.assertEqual(self.pet.state,'idle')
    def test_scale_small_screen_and_manual_interrupt_restore_normal_canvas(self):
        self.pet._screen_rect=lambda:QRect(-800,40,700,600)
        self.pet.set_scale(2);self.pet.start_swim(duration=20)
        self.assertTrue(self.pet._screen_rect().contains(self.pet.geometry()))
        self.assertLess(self.pet.display_scale,2)
        self.pet.set_scale(.75);self.assertTrue(self.pet._screen_rect().contains(self.pet.geometry()))
        self.pet.interact('rice');self.assertEqual(self.pet.state,'ds_rice_slurp')
        self.assertEqual((self.pet.width(),self.pet.height()),(192,192));self.assertEqual(self.pet.y(),self.pet._ground_y())
    def test_drag_out_of_swim_keeps_existing_pickup_and_landing_behaviour(self):
        self.pet.start_swim(duration=20);self.pet.mousePressEvent(_FakePress())
        event=QMouseEvent(QEvent.MouseMove,QPointF(120,80),QPointF(600,300),Qt.NoButton,Qt.LeftButton,Qt.NoModifier)
        self.pet.mouseMoveEvent(event)
        self.assertTrue(self.pet.dragging);self.assertEqual(self.pet.state,'dangle')
        self.assertEqual((self.pet.width(),self.pet.height()),(256,256))
        self.pet.mouseReleaseEvent(_FakeEvt());self.assertFalse(self.pet.dragging)
        self.assertEqual(self.pet.state,'fall');self.until('idle',limit=260)
        self.assertEqual(self.pet.y(),self.pet._ground_y())
    def test_swim_toggle_filters_automatic_pool_and_finishes_current_trip(self):
        self.pet.start_swim(duration=20);self.pet.set_swimming(False)
        self.assertEqual(self.pet.state,'swim_return');self.until('idle',limit=30)
        self.pet.next_behaviour=0
        with patch.object(self.pet.idle_chooser,'choose',return_value='wave') as choose:
            self.step();allowed=choose.call_args.args[1]
            self.assertNotIn('swim',allowed);self.assertNotIn('swim_water',allowed)
        chooser=BalancedChooser(IDLE_RULES);used={chooser.choose(t) for t in range(0,10000,100)}
        self.assertIn('swim',used);self.assertNotIn('swim_water',used)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json';path.write_text('{"swimming":false}')
            self.assertFalse(Preferences(str(path)).load()['swimming'])
    def test_work_animation_waits_for_swim_and_reply_bubble_tracks_visible_character(self):
        with tempfile.TemporaryDirectory() as folder:
            comp=PetCompanion(self.pet,folder);comp.client.timer.stop()
            self.pet._assistant=comp;self.pet.start_swim(duration=20)
            self.assertFalse(comp.can_animate());comp.react('teased')
            self.assertEqual(self.pet.state,'swim')
            bubble=ReplyBubble(comp);bubble.label.setText('游一会儿～');bubble.adjustSize();bubble.anchor()
            anchor=self.pet.reply_anchor_rect()
            self.assertGreater(anchor.y(),self.pet.y()+130)
            self.assertEqual(bubble.geometry().bottom()+9,anchor.top())
            self.assertTrue(self.pet._screen_rect().contains(bubble.geometry()))
            bubble.deleteLater();comp.shutdown();self.pet._assistant=None

    def test_swimming_preserves_normal_scale_and_stays_just_above_ground_on_different_screens(self):
        for area in (QRect(0,0,1600,900),QRect(-1920,40,1920,1040)):
            self.pet._screen_rect=lambda:area
            for scale in (.75,1.0,1.25):
                self.pet.set_state('idle');self.pet.set_scale(scale)
                self.pet.move(area.left()+600,self.pet._ground_y())
                center_x=self.pet.geometry().center().x()
                standing_height=QRegion(self.pet.current_pixmap().mask()).boundingRect().height()*scale
                self.pet.start_swim(duration=20)
                self.assertEqual(self.pet.swim_family,'ds_swim_full')
                render_scale=scale*SWIM_RENDER_SCALE
                self.assertEqual(self.pet.display_scale,render_scale)
                self.assertLessEqual(abs(self.pet.geometry().center().x()-center_x),1)
                self.assertEqual(area.bottom()+1-self.pet.geometry().bottom()-1,round(SWIM_HOVER_OFFSET*render_scale))
                visible=self.pet.reply_anchor_rect()
                self.assertLess(abs(visible.height()-standing_height),standing_height*.08)
                self.assertGreater(visible.bottom(),area.bottom()-75*scale)
                self.assertLess(visible.bottom(),area.bottom()-15*scale)
                self.step(15);self.assertEqual(self.pet.y(),area.bottom()+1-self.pet.height()-round(SWIM_HOVER_OFFSET*render_scale))

if __name__=='__main__':unittest.main()
