"""Nonblocking local Harness connection and the pet's command/chat window."""
import html
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit, urlencode
import uuid
from animation_policy import BalancedChooser, WORK_POOLS, SUCCESS_RULES, ERROR_RULES, tool_activity, is_service_busy

from PySide6.QtCore import QObject, Signal, QTimer, QUrl, Qt
from PySide6.QtGui import QKeySequence, QShortcut, QColor
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply, QNetworkProxy
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QComboBox, QPushButton,
                              QTextBrowser, QLineEdit, QCheckBox, QFileDialog, QWidget, QGraphicsDropShadowEffect)


def message_text(data):
    if not isinstance(data, dict):
        return ''
    message = data.get('message', data)
    if not isinstance(message, dict):
        return ''
    content = message.get('content', [])
    if isinstance(content, str):
        return content
    return '\n'.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text')


class Activity:
    """Reduce durable events and streams, keeping approvals and tools dominant."""
    def __init__(self):
        self.state='idle'; self.cursor=-1; self.failed=False
        self.pending_approvals=set(); self.pending_questions=set(); self.pending_tools={}

    def resolve(self, fallback):
        if self.pending_approvals or self.pending_questions:
            return 'waiting'
        if self.pending_tools:
            rank={'coding':6,'executing':5,'delegating':4,'searching':3,'reading':2,'working':1,'waiting':0}
            return max(self.pending_tools.values(),key=lambda value:rank.get(value,0))
        return fallback

    def stream(self, kind):
        self.state=self.resolve('replying' if kind=='text-delta' else 'thinking')
        return self.state

    def consume(self,event,replay=False):
        seq=event.get('seq',-1)
        if not replay and seq<=self.cursor: return None
        self.cursor=max(self.cursor,seq)
        kind=event.get('type',''); data=event.get('data',{})
        data=data if isinstance(data,dict) else {}
        target=self.state
        if kind=='turn/start':
            self.failed=False; self.pending_tools.clear(); self.pending_questions.clear(); self.pending_approvals.clear()
            target='thinking'
        elif kind in ('step/start','request/header','request/context'):
            target='thinking'
        elif kind=='tool/call':
            call=data.get('call',data)
            call=call if isinstance(call,dict) else {}
            name=call.get('name',''); identity=data.get('callId',call.get('id',seq))
            target=tool_activity(name,call.get('arguments'))
            self.pending_tools[identity]=target
            if target=='waiting': self.pending_questions.add(identity)
        elif kind=='tool/result':
            result=data.get('message',data.get('result',{}))
            result=result if isinstance(result,dict) else {}
            source=result.get('source',{}); source=source if isinstance(source,dict) else {}
            identity=result.get('callId',result.get('toolCallId',source.get('callId',data.get('callId'))))
            self.pending_tools.pop(identity,None); self.pending_questions.discard(identity)
            target='retrying' if result.get('isError') else 'thinking'
        elif kind=='approval/asked':
            self.pending_approvals.add(data.get('id',seq))
        elif kind=='approval/decided':
            self.pending_approvals.discard(data.get('id')); target='thinking'
        elif kind=='request/error':
            target='busy' if is_service_busy(data) else 'retrying'
        elif kind=='agent/error':
            self.failed=True; self.pending_tools.clear(); self.pending_questions.clear(); self.pending_approvals.clear(); target='error'
        elif kind=='turn/end':
            terminal=data.get('reason',data.get('outcome',{}))
            terminal=terminal.get('kind','') if isinstance(terminal,dict) else str(terminal)
            self.pending_tools.clear(); self.pending_questions.clear(); self.pending_approvals.clear()
            if any(word in terminal for word in ('cancel','interrupt','abort')): target='idle'
            elif any(word in terminal for word in ('error','fail')): target='error'
            elif terminal in ('completed','done','success'): target='idle' if replay else 'success'
            else: target='error' if self.failed else 'idle'
        self.state=self.resolve(target)
        return self.state

    def snapshot(self,frame):
        self.__init__()
        for record in frame.get('records',[]):
            if record.get('type')=='event': self.consume(record.get('event',{}),replay=True)
        self.cursor=frame.get('cursor',self.cursor)


class BridgeClient(QObject):
    sessions = Signal(object)
    frame = Signal(object)
    connection = Signal(bool, str)
    problem = Signal(str)

    def __init__(self, connection_file, parent=None):
        super().__init__(parent)
        self.connection_file = Path(connection_file)
        self.manager = QNetworkAccessManager(self)
        self.manager.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self.enabled = False
        self.online = False
        self.url = self.token = ''
        self.session_id = ''
        self.stream = None
        self.buffer = b''
        self.generation = 0
        self.polling = False
        self.timer = QTimer(self, timeout=self.refresh)
        self.timer.setInterval(2000)

    def enable(self, on):
        self.enabled = bool(on)
        self.polling = False
        self.generation += 1
        if on:
            self.timer.start()
            self.refresh()
        else:
            self.timer.stop()
            self.stop_stream()
            self.online = False
            self.connection.emit(False, '独立陪伴')

    def _credentials(self):
        data = json.loads(self.connection_file.read_text(encoding='utf-8'))
        parsed = urlsplit(data.get('url', ''))
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username:
            raise ValueError('Invalid local bridge address')
        token = data.get('token', '')
        if not isinstance(token, str) or len(token) != 64:
            raise ValueError('Invalid local bridge credential')
        if self.token and self.token != token:
            self.stop_stream()
        self.url, self.token = data['url'].rstrip('/'), token

    def request(self, route, data=None, callback=None, failure=None):
        if not self.enabled:
            if failure:
                failure('请先切换到 跟随 dsh 模式。')
            return None
        try:
            self._credentials()
        except (OSError, ValueError, TypeError):
            if failure:
                failure('尚未连接 dsh。请确认本地桥接插件已启用。')
            return None
        request = QNetworkRequest(QUrl(self.url + route))
        request.setRawHeader(b'Authorization', ('Bearer ' + self.token).encode('ascii'))
        request.setTransferTimeout(10000)
        generation = self.generation
        if data is None:
            reply = self.manager.get(request)
        else:
            request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
            reply = self.manager.post(request, json.dumps(data, ensure_ascii=False).encode('utf-8'))
        def finished():
            raw = bytes(reply.readAll())
            error = reply.error()
            reply.deleteLater()
            if generation != self.generation:
                return
            try:
                value = json.loads(raw.decode('utf-8')) if raw else {}
            except (ValueError, UnicodeError):
                value = {}
            if error != QNetworkReply.NoError or 'error' in value:
                # Never include authenticated URLs or credentials in user messages.
                message = str(value.get('error') or '无法连接 dsh，请确认它正在运行。')
                if failure:
                    failure(message)
            elif callback:
                callback(value)
        reply.finished.connect(finished)
        return reply

    def refresh(self):
        if not self.enabled or self.polling:
            return
        self.polling = True
        def success(value):
            self.polling = False
            self.online = True
            self.connection.emit(True, '已连接本机 dsh')
            self.sessions.emit(value.get('items', []))
            if self.session_id and self.stream is None:
                self.follow(self.session_id)
        def failed(message):
            self.polling = False
            self.online = False
            self.connection.emit(False, message)
            self.stop_stream()
        reply = self.request('/sessions', callback=success, failure=failed)
        if reply is None:
            failed('等待 dsh 桥接插件启动…')

    def stop_stream(self):
        reply, self.stream = self.stream, None
        self.buffer = b''
        if reply is not None:
            reply.abort()

    def follow(self, session_id):
        self.session_id = session_id
        self.stop_stream()
        if not self.enabled or not self.online or not session_id:
            return
        request = QNetworkRequest(QUrl(self.url + '/follow?' + urlencode({'sessionId':session_id})))
        request.setRawHeader(b'Authorization', ('Bearer ' + self.token).encode('ascii'))
        reply = self.manager.get(request)
        self.stream = reply
        def read():
            if self.stream is not reply:
                return
            self.buffer += bytes(reply.readAll())
            if len(self.buffer) > 16 * 1024 * 1024:
                self.problem.emit('dsh 回复过大，请在 dsh 中查看完整内容。')
                self.stop_stream()
                return
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                if not line.strip():
                    continue
                try:
                    frame = json.loads(line.decode('utf-8'))
                except (ValueError, UnicodeError):
                    self.problem.emit('dsh 事件格式异常，将重新连接。')
                    self.stop_stream()
                    return
                self.frame.emit(frame)
        def end():
            if self.stream is reply:
                read()
                self.stream = None
                if self.enabled:
                    self.connection.emit(False, '连接中断，正在重连…')
            reply.deleteLater()
        reply.readyRead.connect(read)
        reply.finished.connect(end)


STATE_LABELS = {'idle':'等待指令', 'thinking':'正在思考', 'coding':'正在修改文件', 'reading':'正在读取 / 查看', 'executing':'正在执行命令',
                'delegating':'正在委派工作', 'working':'正在使用工具', 'busy':'服务繁忙，等待恢复',
                'searching':'正在查找资料', 'replying':'正在回复', 'waiting':'需要你确认，请在 dsh 中处理',
                'retrying':'遇到问题，继续尝试', 'success':'工作完成！', 'error':'工作遇到错误'}


class CommandInput(QLineEdit):
    """One-line command entry; Alt-drag moves the floating window."""
    def __init__(self, parent):
        super().__init__(parent)
        self.drag_offset = None

    def toPlainText(self):
        return self.text()

    def setPlainText(self, text):
        self.setText(text)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.AltModifier:
            self.drag_offset = event.globalPosition().toPoint() - self.window().pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag_offset is not None:
            self.drag_offset = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.window().open_context_menu(event.globalPos())


class CommandWindow(QDialog):
    def __init__(self, companion):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.companion = companion
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle('蓝色大肥鱼')
        self.resize(360, 58); self.setFixedHeight(58); self.setMinimumWidth(240)
        self.last_feedback = ''
        self.last_reply = ''
        layout = QHBoxLayout(self); layout.setContentsMargins(10, 10, 10, 10)
        self.input = CommandInput(self); self.input.setFixedHeight(38)
        self.input.setStyleSheet('''QLineEdit {
            background: rgba(235,245,255,185); color: #315576;
            border: 1px solid rgba(255,255,255,210); border-radius: 13px;
            padding: 7px 12px; font-size: 12px; selection-background-color: #aacced;
        } QLineEdit:focus {
            background: rgba(242,249,255,205); border-color: rgba(155,194,231,150);
        }''')
        shadow = QGraphicsDropShadowEffect(self.input)
        shadow.setBlurRadius(16); shadow.setOffset(0, 3); shadow.setColor(QColor(40,72,110,40))
        self.input.setGraphicsEffect(shadow); layout.addWidget(self.input)
        self.input.returnPressed.connect(self.send)
        self.shortcut = QShortcut(QKeySequence('Ctrl+Return'),self); self.shortcut.activated.connect(self.send)

        # The same session and delivery model serves the context menu. Its state
        # widgets stay under a hidden parent; only the input is visible.
        self.controls = QWidget(self); self.controls.hide()
        self.mode = QComboBox(self.controls); self.mode.addItem('独立陪伴','standalone'); self.mode.addItem('跟随 dsh','dsh')
        self.mode.currentIndexChanged.connect(lambda: companion.set_mode(self.mode.currentData()))
        self.sessions = QComboBox(self.controls); self.sessions.currentIndexChanged.connect(self.select_session)
        self.status = QLabel('独立陪伴',self.controls)
        self.refresh_button = QPushButton(self.controls)
        self.new_button = QPushButton(self.controls)
        self.send_button = QPushButton(self.controls)
        self.stop_button = QPushButton(self.controls)
        self.auto = QCheckBox(self.controls); self.auto.setChecked(companion.auto_follow)
        self.auto.toggled.connect(companion.set_auto_follow)
        self.project = QLineEdit(companion.project,self.controls)
        self.steer = QCheckBox(self.controls)
        self.transcript = QTextBrowser(self.controls); self.transcript.document().setMaximumBlockCount(3000)
        self.live = QLabel(self.controls); self.live.setTextFormat(Qt.PlainText)
        self.update_mode(companion.mode)

    def update_mode(self, mode):
        self.input.setPlaceholderText('和蓝色大肥鱼说点什么…')
        self.update_tooltip()

    def update_tooltip(self):
        details = self.last_reply or self.last_feedback or ('已连接 dsh' if self.companion.connected else '等待连接 dsh' if self.companion.mode == 'dsh' else '独立陪伴')
        self.input.setToolTip('<b>蓝色大肥鱼</b><br>' + html.escape(details[:1500]).replace('\n','<br>') + '<br><br>回车发送 · Alt + 拖动移动 · 右键设置')

    def append(self, speaker, text):
        if not text:
            return
        self.transcript.append('<p><b>' + html.escape(speaker) + '</b><br>' + html.escape(text).replace('\n','<br>') + '</p>')
        if speaker != '你':
            if speaker == 'dsh':
                self.last_reply = text
            self.last_feedback = text
            self.update_tooltip()
            if speaker == '蓝色大肥鱼' and self.companion.pet.bubbles_on:
                self.companion.pet.bubble = [text.splitlines()[0][:24],4.0]
                self.companion.pet._refresh_frame()

    def open_context_menu(self, position):
        comp = self.companion
        menu = self.input.createStandardContextMenu()
        menu.setStyleSheet('QMenu {background:#f2f7fd; color:#315576; border:1px solid #ccdfef; padding:5px;} '
                            'QMenu::item {padding:6px 18px;} QMenu::item:selected {background:#dceafb;}')
        menu.addSeparator()
        menu.addAction('蓝色大肥鱼 · ' + (STATE_LABELS.get(comp.activity.state,'已连接') if comp.connected else '等待连接' if comp.mode=='dsh' else '独立陪伴')).setEnabled(False)
        modes = menu.addMenu('陪伴方式')
        for mode,label in (('standalone','独立陪伴'),('dsh','跟随 dsh')):
            action=modes.addAction(label); action.setCheckable(True); action.setChecked(comp.mode==mode)
            action.triggered.connect(lambda _=False, selected=mode: comp.set_mode(selected))
        if comp.mode == 'dsh':
            sessions=menu.addMenu('当前会话')
            for index in range(self.sessions.count()):
                identity=self.sessions.itemData(index)
                action=sessions.addAction(self.sessions.itemText(index)); action.setCheckable(True); action.setChecked(identity==comp.session_id)
                action.triggered.connect(lambda _=False, identity=identity: self.pick_session(identity))
            auto=menu.addAction('跟随正在工作的会话'); auto.setCheckable(True); auto.setChecked(comp.auto_follow)
            auto.triggered.connect(self.auto.setChecked)
            menu.addAction('刷新会话').triggered.connect(comp.client.refresh)
            menu.addAction('新会话').triggered.connect(comp.new_session)
            menu.addAction('工作目录…').triggered.connect(self.choose_folder)
            steer=menu.addAction('插入当前工作'); steer.setCheckable(True); steer.setChecked(self.steer.isChecked()); steer.triggered.connect(self.steer.setChecked)
            stop=menu.addAction('停止当前工作'); stop.setEnabled(comp.connected and bool(comp.session_id) and not comp.creating); stop.triggered.connect(comp.cancel)
        menu.addSeparator()
        menu.addAction('收起输入框').triggered.connect(self.hide)
        menu.exec(position); menu.deleteLater()

    def pick_session(self, identity):
        self.auto.setChecked(False); self.companion.select(identity)

    def send(self):
        text=self.input.text().strip()
        if text:
            self.companion.send(text,'steer' if self.steer.isChecked() else 'queue')

    def choose_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'选择工作目录',self.project.text())
        if folder:
            self.project.setText(folder); self.companion.set_project(folder)

    def select_session(self):
        if self.sessions.currentData():
            self.pick_session(self.sessions.currentData())

    def closeEvent(self,event):
        self.hide(); event.ignore()


class PetCompanion(QObject):
    def __init__(self, pet, data_dir, saved=None, mode=None):
        super().__init__(pet)
        saved = saved or {}
        self.pet = pet
        self.mode = mode or saved.get('pet_mode', 'standalone')
        self.home = saved.get('dsh_home', os.path.join(os.environ.get('USERPROFILE', str(Path.home())), '.dsh'))
        default_project = next((p for p in Path(__file__).resolve().parents if (p/'启动桌宠.bat').is_file()), Path(__file__).resolve().parent)
        self.project = saved.get('dsh_project', str(default_project))
        self.session_id = saved.get('dsh_session', '')
        self.auto_follow = saved.get('dsh_auto_follow', True)
        self.activity = Activity()
        self.busy = False
        self.connected = False
        self.live_text = ''
        self.window = None
        self.sending = False
        self.creating = False
        self.rendered_state = 'idle'
        self.clip_state = ''; self.clip_deadline = 0.0
        self.pending_outcome = None; self.outcome_deadline = 0.0; self.outcome_animation = None
        self.work_pools = {state:BalancedChooser(pool,pet.rng) for state,pool in WORK_POOLS.items()}
        self.outcome_pools = {'success':BalancedChooser(SUCCESS_RULES,pet.rng), 'error':BalancedChooser(ERROR_RULES,pet.rng)}
        self.animation_timer = QTimer(self,timeout=self.animation_tick)
        self.animation_timer.setInterval(500)
        if self.mode == 'dsh': self.animation_timer.start()
        self.client = BridgeClient(Path(data_dir) / 'dsh-bridge.json', self)
        self.client.sessions.connect(self.sessions_changed)
        self.client.frame.connect(self.frame_received)
        self.client.connection.connect(self.connection_changed)
        self.client.problem.connect(self.report)
        self.finished_timer = QTimer(self, singleShot=True, timeout=self.finish_animation)
        self.celebration_timer = QTimer(self, singleShot=True, timeout=self.celebrate)
        QTimer.singleShot(0, lambda: self.client.enable(self.mode == 'dsh'))

    def preferences(self):
        return {'pet_mode':self.mode, 'dsh_home':self.home, 'dsh_project':self.project,
                'dsh_session':self.session_id, 'dsh_auto_follow':self.auto_follow}

    def open_window(self):
        if self.window is None:
            self.window = CommandWindow(self)
            area = self.pet._screen_rect()
            x = max(area.left(), min(self.pet.x() + self.pet.width() // 2 - self.window.width() // 2, area.right() + 1 - self.window.width()))
            y = max(area.top(), min(self.pet.y() - self.window.height() - 8, area.bottom() + 1 - self.window.height()))
            self.window.move(x, y)
            self.window.mode.blockSignals(True); self.window.mode.setCurrentIndex(1 if self.mode == 'dsh' else 0); self.window.mode.blockSignals(False)
            self.connection_changed(self.connected, '已连接本机 dsh' if self.connected else '等待 dsh 桥接插件启动…' if self.mode == 'dsh' else '独立陪伴')
            self.client.refresh()
        self.window.show(); self.window.raise_(); self.window.activateWindow(); self.window.input.setFocus()

    def set_mode(self, mode):
        if mode not in ('standalone','dsh') or mode == self.mode:
            return
        self.mode = mode
        self.pending_outcome = None; self.clip_state = ''; self.clip_deadline = 0.0
        self.animation_timer.start() if mode == 'dsh' else self.animation_timer.stop()
        self.busy = False
        self.sending = False
        self.creating = False
        self.activity = Activity()
        self.rendered_state = 'idle'
        self.finished_timer.stop()
        self.celebration_timer.stop()
        self.pet.set_state('idle')
        self.client.polling = False
        self.client.enable(mode == 'dsh')
        if self.window:
            self.window.send_button.setEnabled(True)
            self.window.mode.blockSignals(True); self.window.mode.setCurrentIndex(1 if mode == 'dsh' else 0); self.window.mode.blockSignals(False)
        self.pet.schedule_save()

    def set_auto_follow(self, on):
        self.auto_follow = bool(on); self.pet.schedule_save()

    def set_project(self, project):
        self.project = project.strip(); self.pet.schedule_save()

    def connection_changed(self, online, label):
        self.connected = online
        if not online:
            self.busy = False
            self.clip_state = ''; self.clip_deadline = 0.0; self.pending_outcome = None
            if self.pet.anim and (self.pet.anim.name.startswith('dsh_') or (self.pet.state=='expr' and self.pet.state_len>=1e8)):
                self.pet.set_state('idle')
        if self.window:
            self.window.update_mode(self.mode)
            for control in (self.window.sessions, self.window.auto, self.window.project, self.window.steer):
                control.setEnabled(self.mode == 'dsh')
            self.window.status.setText(STATE_LABELS.get(self.activity.state, label) if online else label)
            self.window.update_tooltip()
            for button in (self.window.new_button, self.window.refresh_button, self.window.stop_button):
                button.setEnabled(self.mode == 'dsh' and (online or button is self.window.refresh_button))

    def sessions_changed(self, rows):
        rows = [row for row in rows if not row.get('parentSessionId')]
        ids = {row['sessionId'] for row in rows}
        running = next((row for row in rows if row.get('running')), None)
        wanted = running['sessionId'] if self.auto_follow and running else self.session_id if self.session_id in ids else rows[0]['sessionId'] if rows else ''
        if self.window:
            combo = self.window.sessions; combo.blockSignals(True); combo.clear()
            for row in rows:
                name = row.get('title') or Path(row.get('cwd') or '').name or row['sessionId'][:12]
                combo.addItem(('● ' if row.get('running') else '') + name[:60], row['sessionId'])
            combo.setCurrentIndex(combo.findData(wanted)); combo.blockSignals(False)
        if not wanted and self.session_id:
            self.select('')
        if wanted and wanted != self.session_id:
            self.select(wanted)
        elif wanted and self.client.session_id != wanted:
            self.client.follow(wanted)

    def select(self, session_id):
        self.session_id = session_id
        self.activity = Activity(); self.busy = False; self.live_text = ''
        self.pending_outcome = None; self.clip_state = ''; self.clip_deadline = 0.0
        self.rendered_state = 'idle'
        self.finished_timer.stop()
        if self.pet.anim.name.startswith('dsh_') or (self.pet.state=='expr' and self.pet.state_len>=1e8):
            self.pet.set_state('idle')
        if self.window:
            self.window.transcript.clear(); self.window.live.clear(); self.window.live.hide()
            self.window.last_reply = self.window.last_feedback = ''
            self.window.update_tooltip()
        self.client.follow(session_id); self.pet.schedule_save()

    def report(self, message):
        self.open_window(); self.window.append('蓝色大肥鱼', message)

    def new_session(self):
        if self.sending or self.creating:
            return
        if not Path(self.project).is_dir():
            self.report('请先选择一个存在的工作目录。'); return
        self.creating = True
        if self.window:
            self.window.new_button.setEnabled(False); self.window.send_button.setEnabled(False)
        def created(value):
            self.creating = False
            if self.window:
                self.window.new_button.setEnabled(True); self.window.send_button.setEnabled(True)
            self.set_auto_follow(False)
            if self.window:
                self.window.auto.setChecked(False)
            self.select(value['sessionId']); self.client.refresh()
            self.report('新会话已创建，可以发送指令。')
        def failed(message):
            self.creating = False
            if self.window:
                self.window.new_button.setEnabled(True); self.window.send_button.setEnabled(True)
            self.report(message)
        self.client.request('/session', {'cwd':self.project}, created, failed)

    def send(self, text, mode='queue'):
        if self.sending or self.creating:
            return
        if len(text) > 32000:
            self.report('指令过长，请拆成几条发送。'); return
        if self.mode == 'standalone':
            self.local_command(text)
            if self.window:
                self.window.input.clear()
            return
        if not self.connected or not self.session_id:
            self.report('请先连接 dsh 并选择会话，或点击“新会话”。'); return
        self.sending = True
        if self.window:
            self.window.send_button.setEnabled(False)
        request_id = str(uuid.uuid4())
        def done(value):
            self.sending = False
            if self.window:
                self.window.send_button.setEnabled(True)
                if self.window.input.toPlainText().strip() == text:
                    self.window.input.clear()
                self.window.append('蓝色大肥鱼', '指令已提交到当前工作。' if mode == 'steer' else '指令已发送；工作进行中时会排队。')
        def failed(message):
            self.sending = False
            if self.window:
                self.window.send_button.setEnabled(True)
            self.report(message + '\n指令已保留在输入框中，请检查后再发送。')
        self.client.request('/prompt', {'sessionId':self.session_id, 'requestId':request_id, 'text':text,
                                       'mode':mode, 'clientTimeZone':'Asia/Shanghai'}, done, failed)

    def cancel(self):
        if self.session_id and not self.creating:
            self.client.request('/cancel', {'sessionId':self.session_id}, lambda _: self.report('已请求停止当前工作。'), self.report)

    def local_command(self, text):
        self.pet._touch()
        self.open_window(); self.window.append('你', text)
        commands = {'喂饭':lambda: self.pet.interact('rice'), '投喂':lambda: self.pet.interact('rice'),
                    '睡觉':lambda: self.pet.set_state('sleepy'), '醒醒':lambda: self.pet.recall(),
                    '安静':lambda: self.pet.set_quiet(True), '活泼':lambda: self.pet.set_quiet(False),
                    '隐藏':self.pet.hide_pet, '散步':lambda: self.pet.do_action('walk', 0),
                    '写代码':lambda: self.pet.do_action('ds_code_eureka', 0),
                    '摸尾巴':lambda: self.pet.interact('tail'), '摸头':lambda: self.pet.do_action('petted', 2), '开心':lambda: self.pet.do_action('happy', 0)}
        command = text.strip().lstrip('/')
        if command in commands:
            commands[command](); self.window.append('蓝色大肥鱼', '收到～')
        elif command in ('dsh','连接dsh'):
            self.set_mode('dsh'); self.window.append('蓝色大肥鱼', '正在连接本机 dsh…')
        else:
            self.window.append('蓝色大肥鱼', '独立模式支持：喂饭、睡觉、醒醒、安静、活泼、隐藏、散步、写代码、摸尾巴、摸头、开心。\n发送自然语言工作指令，请切换到 跟随 dsh。')

    def frame_received(self, frame):
        kind = frame.get('type')
        if kind == 'bridge-error':
            self.report(frame.get('error', 'dsh 连接异常')); return
        if kind == 'snapshot':
            self.activity.snapshot(frame)
            if self.window:
                self.window.transcript.clear()
                for record in frame.get('records', []):
                    self.display_event(record.get('event', {}))
            self.set_activity(self.activity.state)
            return
        if kind == 'event':
            event = frame.get('event', {})
            state = self.activity.consume(event)
            if state is not None:
                self.display_event(event); self.set_activity(state)
            return
        if kind == 'assistant-stream':
            value = frame.get('frame', {})
            if value.get('type') == 'start':
                self.live_text = ''; self.set_activity(self.activity.stream('start'))
                if self.window:
                    self.window.live.clear(); self.window.live.hide()
            elif value.get('type') == 'chunk':
                chunk = value.get('chunk', {})
                if isinstance(chunk, dict) and chunk.get('type') == 'reasoning-delta':
                    self.set_activity(self.activity.stream('reasoning-delta'))
                elif isinstance(chunk, dict) and chunk.get('type') == 'text-delta':
                    self.live_text = (self.live_text + str(chunk.get('text', chunk.get('delta', ''))))[-1000:]
                    if self.window:
                        self.window.live.setText('dsh 正在回复：' + self.live_text[-250:])
                        self.window.live.show()
                    self.set_activity(self.activity.stream('text-delta'))
            elif value.get('type') == 'end' and self.window:
                self.window.live.clear(); self.window.live.hide()

    def display_event(self, event):
        if not self.window:
            return
        kind = event.get('type')
        if kind == 'user/message' and event.get('data', {}).get('source', {}).get('kind', 'user') != 'user':
            return
        if kind in ('user/message','assistant/message'):
            self.window.append('你' if kind == 'user/message' else 'dsh', message_text(event.get('data', {})))
        elif kind == 'approval/asked':
            self.window.append('需要确认', 'dsh 正在等待许可，请在 dsh 窗口中确认。')

    def can_animate(self):
        if self.pet.dragging or self.pet.menu_open or self.pet.press_pos is not None or not self.pet.isVisible():
            return False
        return self.pet.animation_origin in ('idle','automatic','work') or self.pet.state == 'idle' or (self.pet.state == 'expr' and self.pet.state_len >= 1e8)

    def set_activity(self,state):
        if self.activity.pending_approvals or self.activity.pending_questions: state='waiting'
        changed=state!=self.rendered_state
        self.rendered_state=state; self.activity.state=state
        self.busy=state in WORK_POOLS and self.mode=='dsh'
        if self.window:
            self.window.status.setText(STATE_LABELS.get(state,'已连接 dsh')); self.window.update_tooltip()
        if self.busy:
            self.pending_outcome=None; self.finished_timer.stop(); self.resume_animation()
        elif state in ('success','error') and changed:
            self.pending_outcome=state; self.outcome_deadline=time.monotonic()+20
            self.show_outcome()
        elif state=='idle':
            self.pending_outcome=None
            if self.can_animate() and self.pet.state!='idle': self.pet.set_state('idle')

    def show_outcome(self):
        state=self.pending_outcome
        if not state or self.mode!='dsh' or self.activity.state!=state: return
        now=time.monotonic()
        if now>self.outcome_deadline:
            self.pending_outcome=None; return
        if not self.can_animate(): return
        animation=self.outcome_pools[state].choose(now)
        self.pending_outcome=None
        if animation in ('dsh_success','happy'): self.pet.set_state(animation,origin='work')
        elif animation=='tsun_hmph':
            self.pet.animation_origin='work'; self.pet.start_behaviour(animation)
        elif animation: self.pet.set_state('expr',animation,length=4.0,origin='work')
        self.outcome_animation=animation
        self.finished_timer.start(4000)

    def celebrate(self):
        self.show_outcome()

    def animation_tick(self):
        if self.mode!='dsh': return
        if self.busy: self.resume_animation()
        elif self.pending_outcome: self.show_outcome()

    def resume_animation(self):
        if not self.busy or not self.can_animate(): return
        now=time.monotonic(); state=self.activity.state
        if state not in self.work_pools: return
        owns_clip=self.pet.state=='expr' and self.pet.state_len>=1e8
        if state==self.clip_state and now<self.clip_deadline and owns_clip: return
        animation=self.work_pools[state].choose(now)
        self.clip_state=state; self.clip_deadline=now+self.pet.rng.uniform(12,22)
        if animation and (self.pet.anim.name!=animation or not owns_clip):
            self.finished_timer.stop(); self.pet.set_state('expr',animation,length=1e9,origin='work')

    def finish_animation(self):
        if not self.busy:
            self.activity.state='idle'; self.rendered_state='idle'
            if self.pet.animation_origin=='work' and self.pet.anim.name==self.outcome_animation:
                self.pet.set_state('idle')

    def shutdown(self):
        self.animation_timer.stop(); self.celebration_timer.stop(); self.finished_timer.stop()
        self.client.enable(False)
        if self.window: self.window.hide()
