"""Bounded screen swimming, phase-aligned turns and click-triggered leaps."""
import random
import time

SWIM_STATES=('swim','swim_turn','swim_leap','swim_return')
SWIM_DURATION=(14.0,22.0)
SWIM_RETURN_SECONDS=1.2
SWIM_HOVER_OFFSET=8.0  # Original full canvas already has ~40 px clear below the swimmer.
SWIM_RENDER_SCALE=2/3  # ~300 px visible swimmer matches the ~200 px standing sprite.

class SwimBehavior:
    def _swim_anim(self, phase='loop'):
        if phase=='turn':return self.swim_family+'_turn_to_'+('right' if self.swim_dir>0 else 'left')
        suffix='_left' if self.swim_dir<0 else ''
        return self.swim_family+('_leap' if phase=='leap' else '')+suffix

    def start_swim(self, duration=None):
        self.swim_family='ds_swim_full'
        self.swim_duration=duration if duration is not None and duration>0 else random.uniform(*SWIM_DURATION)
        self.swim_elapsed=0.0;self.swim_pending_leap=False;self.swim_pending_turn=False
        self.swim_dir=random.choice((-1,1))
        cx=self.x()+self.width()/2
        self.set_state('swim',self._swim_anim())
        r=self._screen_rect()
        # Stay near the standing spot. The large transparent area above the
        # water belongs to the leap frames; it must not lift the whole swimmer.
        y=r.bottom()+1-self.height()-round(SWIM_HOVER_OFFSET*self.display_scale)
        self.move(round(cx-self.width()/2),max(r.top(),y))
        self._clamp_position(ground=False)
        self.swim_x=float(self.x());self.swim_y=float(self.y())
        self.idle_chooser.note('swim',time.monotonic())

    def request_swim_leap(self):
        if self.state not in SWIM_STATES:return False
        # One outstanding click is enough; repeated clicks cannot extend the trip.
        if self.state in ('swim','swim_turn'):self.swim_pending_leap=True
        self.click_cell=None
        return True

    def _resume_swim(self, frame):
        self.set_state('swim',self._swim_anim())
        self.t=frame/self.anim.fps
        self.swim_pending_turn=False

    def _begin_swim_return(self):
        self.swim_pending_leap=False;self.swim_pending_turn=False
        self.swim_return_from=float(self.y())
        phase=self.frame_index() if self.state=='swim' else 0
        self.set_state('swim_return',self._swim_anim())
        self.t=phase/self.anim.fps

    def _swim_step(self, previous_t, elapsed):
        self.swim_elapsed+=elapsed
        r=self._screen_rect()
        right=max(r.left(),r.right()+1-self.width())
        bottom=max(r.top(),r.bottom()+1-self.height())
        self.swim_x=max(r.left(),min(self.swim_x,right))
        self.swim_y=max(r.top(),min(self.swim_y,bottom))
        if self.state=='swim_return':
            f=min(1.0,self.state_t/SWIM_RETURN_SECONDS)
            ease=f*f*(3-2*f)
            y=self.swim_return_from+(bottom-self.swim_return_from)*ease
            self.move(round(self.swim_x),round(max(r.top(),min(y,bottom))))
            if f>=1:
                self.set_state('idle')
            return
        if self.swim_elapsed>=self.swim_duration and self.state=='swim':
            self._begin_swim_return();return
        if self.state in ('swim_turn','swim_leap'):
            if self.anim_done():
                if self.swim_elapsed>=self.swim_duration:self._begin_swim_return()
                else:self._resume_swim(2 if self.state=='swim_turn' else 1)
            return
        # Continuous window motion is separate from the original 10 fps sprites.
        x=self.swim_x+self.swim_dir*70*self.display_scale*min(elapsed,.1)
        if x<=r.left() or x>=right:
            self.swim_pending_turn=True
        self.swim_x=max(r.left(),min(x,right))
        self.move(round(self.swim_x),round(self.swim_y))
        cycle=len(self.anim)/self.anim.fps
        boundary=int(self.t/cycle)>int(previous_t/cycle)
        if boundary and self.swim_pending_turn:
            self.swim_dir*=-1
            self.set_state('swim_turn',self._swim_anim('turn'))
        elif boundary and self.swim_pending_leap:
            self.swim_pending_leap=False
            self.set_state('swim_leap',self._swim_anim('leap'))

    def set_swimming(self,on):
        self.swimming=bool(on)
        if not self.swimming and self.state in SWIM_STATES:self._begin_swim_return()
        self.schedule_save()
