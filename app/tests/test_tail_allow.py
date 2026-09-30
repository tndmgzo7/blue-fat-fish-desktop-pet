"""Tail permission sequence, branch exclusivity and real input handling."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,sys,unittest
APP=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(APP))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage
from pet_support import configure_fonts
from whale_pet import Pet,Sprites,TAIL_ALLOW_WAIT,_FakePress,_FakeEvt
from dsh_companion import PetCompanion
app=QApplication.instance() or QApplication([]); configure_fonts(app)
sprites=Sprites(str(APP/'assets'))

class TailTests(unittest.TestCase):
    def setUp(self):
        self.pet=Pet(sprites,selftest=True); self.pet.timer.stop(); self.pet.show()
        self.pet.next_walk=self.pet.next_behaviour=self.pet.next_attention=1e9
        self.pet._auto_ds=False; self.pet._cursor_override=QPoint(-2000,-2000)
        self.ms=0; self.pet.last_ms=0; self.pet.clock=SimpleNamespace(elapsed=lambda:self.ms)
    def tearDown(self):
        self.pet.click_timer.stop(); self.pet.deleteLater(); app.processEvents()
    def step(self,count=1):
        for _ in range(count): self.ms+=50; self.pet._tick()
    def until(self,state,limit=400):
        for _ in range(limit):
            if self.pet.state==state: return
            self.step()
        self.fail('State not reached: '+state+'; current '+str(self.pet.state))
    def offer(self):
        self.pet.interact('tail'); self.until('ds_tail_allow')
        while not self.pet.tail_offer_ready(): self.step()
    def test_all_60_pngs_hash_pixels_alpha_size_and_branch_slices(self):
        folder=APP/'assets/extra/ds_tail_allow'
        record=json.loads((folder/'source.json').read_text(encoding='utf-8'))
        mapping=[('ds_tail_allow',0,28),('ds_tail_allow_touched',28,46),('ds_tail_allow_timeout',46,60)]
        for animation,start,end in mapping:
            self.assertEqual(len(sprites[animation]),end-start)
            for index in range(start,end):
                name=f'{index:02d}.png'; raw=(folder/name).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(),record['frames_sha256'][name])
                image=QImage.fromData(raw); self.assertEqual((image.width(),image.height()),(256,256)); self.assertTrue(image.hasAlphaChannel())
                self.assertEqual(sprites[animation].frames[index-start].toImage(),image.convertToFormat(QImage.Format_ARGB32_Premultiplied))
                self.assertLessEqual(len(sprites.cache.frames),64)
    def test_tail_hit_chains_into_offer_with_no_poke_chain(self):
        box=self.pet.zone_box('tail'); self.pet.click_cell=((box[0]+box[2])/2,(box[1]+box[3])/2)
        self.pet._single_click(); self.assertEqual(self.pet.state,'ds_tail_touch')
        self.assertEqual(self.pet.pet_chain,0); self.assertFalse(self.pet.pet_times)
        self.until('ds_tail_allow'); self.assertAlmostEqual(self.pet.state_len,1.6+TAIL_ALLOW_WAIT)
    def test_press_confirms_once_and_never_enters_drag_or_happy(self):
        self.offer(); self.assertEqual(self.pet.bubble[0],'只、只能摸一下哦')
        self.pet.mousePressEvent(_FakePress()); self.assertEqual(self.pet.state,'ds_tail_allow_touched')
        self.assertIsNone(self.pet.press_pos); self.assertFalse(self.pet.dragging)
        self.pet.mouseReleaseEvent(_FakeEvt()); self.assertFalse(self.pet.click_timer.isActive())
        self.pet.mouseDoubleClickEvent(_FakeEvt()); self.pet.mouseReleaseEvent(_FakeEvt())
        self.assertEqual(self.pet.state,'ds_tail_allow_touched'); self.step(3)
        self.assertEqual(self.pet.bubble[0],'呜…就、就一下！')
        self.until('idle'); self.assertNotEqual(self.pet.anim.name,'ds_tail_allow_timeout')
    def test_timeout_uses_only_timeout_frames_and_returns_idle(self):
        self.offer(); frames=[]
        while self.pet.state=='ds_tail_allow':
            frames.append(self.pet.frame_index()); self.step()
        self.assertEqual(self.pet.state,'ds_tail_allow_timeout')
        self.assertTrue(all(16<=frame<28 for frame in frames))
        self.step(3); self.assertEqual(self.pet.bubble[0],'哼，不摸拉倒')
        self.until('idle')
    def test_early_and_branch_clicks_are_consumed_without_restarting(self):
        self.pet.interact('tail'); self.pet.mousePressEvent(_FakePress()); self.pet.mouseReleaseEvent(_FakeEvt())
        self.assertEqual(self.pet.state,'ds_tail_touch'); self.assertFalse(self.pet.click_timer.isActive())
        self.until('ds_tail_allow'); self.pet.mousePressEvent(_FakePress()); self.pet.mouseReleaseEvent(_FakeEvt())
        self.assertEqual(self.pet.state,'ds_tail_allow'); self.assertFalse(self.pet.tail_offer_ready())
        self.pet.t=self.pet.state_t=self.pet.state_len; self.step()
        self.assertEqual(self.pet.state,'ds_tail_allow_timeout')
        self.pet._single_click(); self.assertEqual(self.pet.state,'ds_tail_allow_timeout')
    def test_menu_and_sitting_entry(self):
        menu=self.pet.build_menu()
        try:
            interact=next(a.menu() for a in menu.actions() if a.menu() and a.text().startswith('互动'))
            self.pet.set_state('sit'); next(a for a in interact.actions() if a.text()=='摸尾巴').trigger()
            self.assertEqual(self.pet.state,'stand_up'); self.until('ds_tail_touch'); self.until('ds_tail_allow')
        finally: menu.deleteLater()
    def test_bubbles_off_and_menu_pause(self):
        self.pet.set_bubbles(False); self.offer(); self.assertIsNone(self.pet.bubble)
        before=self.pet.state_t; self.pet.menu_open=True; self.step(120); self.assertEqual(self.pet.state_t,before)
        self.pet.menu_open=False; self.until('ds_tail_allow_timeout'); self.step(3); self.assertIsNone(self.pet.bubble)
    def test_dsh_work_resumes_after_tail_and_local_command_exists(self):
        comp=PetCompanion(self.pet,APP/'_validation/missing-bridge',mode='standalone'); self.pet._assistant=comp
        try:
            comp.local_command('摸尾巴'); self.assertEqual(self.pet.state,'ds_tail_touch')
            comp.set_mode('dsh'); comp.animation_timer.stop(); comp.client.timer.stop()
            comp.set_activity('coding'); self.pet.interact('tail'); comp.set_activity('reading')
            self.assertEqual(self.pet.state,'ds_tail_touch'); self.until('ds_tail_allow')
            self.until('ds_tail_allow_timeout'); self.until('idle'); self.step()
            self.assertEqual(self.pet.anim.name,'curious')
        finally: comp.shutdown()
if __name__=='__main__': unittest.main()