"""Bound walking through the real scheduler, including delayed GUI updates."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys,unittest
APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPoint,QRect
from whale_pet import Pet,Sprites
from animation_policy import WALK_GAP
app=QApplication.instance() or QApplication([])
sprites=Sprites(str(APP/'assets'))

class WalkTests(unittest.TestCase):
    def setUp(self):
        self.pet=Pet(sprites,selftest=True);self.pet.timer.stop();self.pet.show()
        self.ms=0;self.pet.last_ms=0;self.pet.clock=SimpleNamespace(elapsed=lambda:self.ms)
        self.pet._screen_rect=lambda:QRect(0,0,500,800)
        self.pet._cursor_override=QPoint(-2000,-2000);self.pet._auto_ds=False
        self.pet.next_attention=self.pet.next_behaviour=1e9
    def tearDown(self):
        self.pet.deleteLater();app.processEvents()
    def step(self,ms=50):
        self.ms+=ms;self.pet._tick()
    def start(self):
        with patch('whale_pet.random.uniform',return_value=2.5),patch('whale_pet.random.random',return_value=1):
            self.pet.do_action('walk',0)
        self.assertEqual(self.pet.state,'walk')
    def test_delayed_update_cannot_leave_walk_running_after_its_deadline(self):
        self.start();self.step(8000)
        self.assertEqual(self.pet.state,'trip')
        self.assertGreaterEqual(self.pet.next_walk,WALK_GAP[0])
        for _ in range(100):
            if self.pet.state=='idle':break
            self.step()
        self.assertEqual(self.pet.state,'idle')
    def test_walk_stops_after_short_trip_even_when_bouncing_at_screen_edge(self):
        self.pet.move(240,550);self.start()
        for _ in range(72):
            if self.pet.state=='trip':break
            self.step()
        self.assertEqual(self.pet.state,'trip')
        for _ in range(100):
            if self.pet.state=='idle':break
            self.step()
        self.assertEqual(self.pet.state,'idle')
        self.assertGreaterEqual(self.pet.next_walk,WALK_GAP[0]-1)
        self.assertEqual(sprites['walk_right'].fps,8)
    def test_menu_pause_is_excluded_from_walk_duration_and_toggle_can_stop_it(self):
        self.start();self.pet.menu_open=True;self.step(10000)
        self.assertEqual(self.pet.state,'walk');self.assertEqual(self.pet.state_t,0)
        self.pet.menu_open=False;self.step()
        self.assertEqual(self.pet.state,'walk')
        self.pet.set_walking(False);self.assertEqual(self.pet.state,'idle')

if __name__=='__main__':unittest.main()
