"""A readable reply bubble that follows the pet independently of the input."""
import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout


class ReplyBubble(QDialog):
    def __init__(self, companion):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.companion = companion
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle('蓝色大肥鱼 · 悄悄话')
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setStyleSheet('QLabel {color:#315576; background:rgba(241,248,255,238); '
                                 'border:1px solid rgba(177,210,236,150); border-radius:14px; '
                                 'padding:13px 16px; font-size:17px;}')
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.addWidget(self.label)
        self.setFixedWidth(340)
        self.deadline = 0
        self.timer = QTimer(self, timeout=self.tick)
        self.timer.setInterval(200)

    def show_reply(self, text):
        self.label.setText(text[:400])
        self.adjustSize()
        self.deadline = time.monotonic() + min(40, max(16, len(text) * .10))
        if self.companion.pet.bubbles_on and self.companion.pet.isVisible():
            self.anchor(); self.show(); self.raise_(); self.timer.start()

    def anchor(self):
        pet = self.companion.pet
        area = pet._screen_rect()
        reference = pet.reply_anchor_rect()
        x = max(area.left(), min(reference.center().x()-self.width()//2, area.right()+1-self.width()))
        y = reference.top()-self.height()-8
        if y < area.top():
            y = min(area.bottom()+1-self.height(), reference.bottom()+8)
        self.move(x,max(area.top(),min(y,area.bottom()+1-self.height())))

    def tick(self):
        pet = self.companion.pet
        if not pet.isVisible() or not pet.bubbles_on:
            self.hide(); self.timer.stop(); return
        if self.underMouse():
            self.deadline = max(self.deadline,time.monotonic()+2)
        if time.monotonic() > self.deadline:
            self.hide(); self.timer.stop()
        else:
            self.anchor()

    def closeEvent(self, event):
        self.hide(); self.timer.stop(); event.ignore()
