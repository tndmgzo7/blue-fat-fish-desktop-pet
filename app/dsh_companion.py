"""Nonblocking local Harness connection and the pet's command/chat window."""
import html
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit, urlencode
import uuid
from companion_memory import CompanionMemory, clean
from companion_bubble import ReplyBubble
from swim_behavior import SWIM_STATES
from conversation_reactions import REACTIONS, keyword_reaction, is_neutral_chat
from animation_policy import BalancedChooser, WORK_POOLS, SUCCESS_RULES, ERROR_RULES, WORK_ROTATION_GAP, tool_activity, is_service_busy

from PySide6.QtCore import QObject, Signal, QTimer, QUrl, Qt, QPoint
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
        active=frame.get('assistantStream',{}).get('activeAttempt',{})
        for item in active.get('stream',[]):
            chunk=item.get('chunk',item)
            kind=chunk.get('type')
            if kind=='text-delta' or (kind=='block-end' and chunk.get('block',{}).get('type')=='text'):
                self.stream('text-delta')
            elif kind=='reasoning-delta': self.stream('reasoning-delta')

    def reconcile(self, status):
        if status.get('cursor',-1)<self.cursor: return False
        self.cursor=status.get('cursor',self.cursor)
        self.pending_tools={call['id']:call['state'] for call in status.get('pendingCalls',[]) if 'id' in call and 'state' in call}
        self.pending_approvals=set(status.get('approvalIds',[]))
        self.pending_questions=set(status.get('questionIds',[]))
        self.state=self.resolve(status.get('state','idle'))
        return True


class BridgeClient(QObject):
    sessions = Signal(object)
    frame = Signal(object)
    connection = Signal(bool, str)
    problem = Signal(str)
    task_status = Signal(object)
    chat_partial = Signal(object)

    def __init__(self, connection_file, parent=None, profile='desktop'):
        super().__init__(parent)
        self.connection_file = Path(connection_file)
        self.profile = profile
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

    def set_profile(self, profile):
        if profile not in ('web','desktop'):
            raise ValueError('Unsupported dsh profile')
        self.generation += 1
        self.stop_stream()
        self.profile = profile
        self.session_id = ''; self.polling = False; self.online = False
        self.url = self.token = ''
        self.connection.emit(False, '等待连接网页版 dsh' if profile == 'web' else '等待连接桌面版 dsh')
        if self.enabled: self.refresh()

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
        path = self.connection_file.with_name('dsh-bridge-' + self.profile + '.json')
        if self.profile == 'desktop' and not path.exists(): path = self.connection_file
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('profile', 'desktop') != self.profile:
            raise ValueError('Wrong dsh profile')
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
        request.setTransferTimeout(50000 if route == '/chat' else 10000)
        generation = self.generation
        if data is None:
            reply = self.manager.get(request)
        else:
            request.setHeader(QNetworkRequest.ContentTypeHeader, 'application/json')
            reply = self.manager.post(request, json.dumps(data, ensure_ascii=False).encode('utf-8'))
        streaming = route == '/chat' and isinstance(data,dict) and data.get('stream') is True
        chat_buffer = b''; chat_result = None; chat_error = ''
        def read_chat():
            nonlocal chat_buffer, chat_result, chat_error
            chat_buffer += bytes(reply.readAll())
            while b'\n' in chat_buffer:
                line,chat_buffer = chat_buffer.split(b'\n',1)
                try: frame = json.loads(line.decode('utf-8'))
                except (ValueError,UnicodeError): continue
                if not isinstance(frame,dict): continue
                if frame.get('type')=='result' and isinstance(frame.get('value'),dict):
                    chat_result = frame['value']
                elif frame.get('type')=='bridge-error':
                    chat_error = clean(frame.get('error') or '聊天回复中断，请再试一次。',300)
                elif (generation==self.generation and frame.get('type')=='reply'
                      and frame.get('turnId')==data.get('turnId') and isinstance(frame.get('reply'),str)):
                    self.chat_partial.emit({'turnId':frame['turnId'],'reply':clean(frame['reply'],300)})
        if streaming: reply.readyRead.connect(read_chat)
        def finished():
            if streaming:
                read_chat();raw=chat_buffer
            else: raw=bytes(reply.readAll())
            error = reply.error()
            reply.deleteLater()
            if generation != self.generation:
                return
            try:
                value = chat_result if streaming and chat_result is not None else json.loads(raw.decode('utf-8')) if raw else {}
            except (ValueError, UnicodeError):
                value = {}
            if not isinstance(value,dict): value={}
            if streaming and chat_error: value={'error':chat_error}
            elif streaming and chat_result is None and 'error' not in value and not isinstance(value.get('reply'),str):
                value={'error':'聊天回复中断，输入已经保留，请再试一次。'}
            if error != QNetworkReply.NoError or 'error' in value:
                # Never include authenticated URLs or credentials in user messages.
                message = clean(value.get('error') or '无法连接 dsh，请确认它正在运行。', 300)
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
            self.connection.emit(True, '已连接网页版 dsh' if self.profile == 'web' else '已连接桌面版 dsh')
            self.sessions.emit(value.get('items', []))
            if value.get('taskStatus'): self.task_status.emit(value['taskStatus'])
            if self.session_id and self.stream is None:
                self.follow(self.session_id)
        def failed(message):
            self.polling = False
            self.online = False
            self.connection.emit(False, message)
            self.stop_stream()
        route='/sessions'+('?' + urlencode({'sessionId':self.session_id}) if self.session_id else '')
        reply = self.request(route, callback=success, failure=failed)
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
    """Empty input and side padding drag; text still selects normally."""
    def __init__(self, parent):
        super().__init__(parent)
        self.drag_offset = None

    def toPlainText(self):
        return self.text()

    def setPlainText(self, text):
        self.setText(text)

    def mousePressEvent(self, event):
        edge = event.position().x() <= 12 or event.position().x() >= self.width() - 12
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and
                (not self.text() or edge or event.modifiers() & Qt.AltModifier)):
            self.drag_offset = event.globalPosition().toPoint() - self.window().pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & (Qt.LeftButton | Qt.MiddleButton):
            self.window().move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag_offset is not None:
            self.drag_offset = None
            self.window().companion.pet.schedule_save()
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
        source = '网页版 dsh' if self.companion.dsh_profile == 'web' else '桌面版 dsh'
        self.input.setToolTip('<b>蓝色大肥鱼 · ' + source + '</b><br>' + html.escape(details[:1500]).replace('\n','<br>') + '<br><br>回车发送 · 空白处或两侧拖动 · Alt + 拖动 · 右键设置')

    def append(self, speaker, text):
        if not text:
            return
        self.transcript.append('<p><b>' + html.escape(speaker) + '</b><br>' + html.escape(text).replace('\n','<br>') + '</p>')
        if speaker != '你':
            if speaker == 'dsh':
                self.companion.last_task_reply = text
                return
            self.last_feedback = text
            if speaker == '蓝色大肥鱼':
                self.last_reply = text
                self.companion.show_reply(text)
            self.update_tooltip()

    def open_context_menu(self, position):
        comp = self.companion
        menu = self.input.createStandardContextMenu()
        menu.setStyleSheet('QMenu {background:#f2f7fd; color:#315576; border:1px solid #ccdfef; padding:5px;} '
                            'QMenu::item {padding:6px 18px;} QMenu::item:selected {background:#dceafb;}')
        menu.addSeparator()
        menu.addAction('蓝色大肥鱼 · ' + (STATE_LABELS.get(comp.activity.state,'已连接') if comp.connected else '等待连接' if comp.mode=='dsh' else '独立陪伴')).setEnabled(False)
        comp.add_connection_menu(menu)
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
        menu.addAction('重看她的上一句').triggered.connect(lambda: comp.show_reply(comp.last_persona_reply))
        memory=menu.addMenu('她的记忆')
        memory.addAction('查看长期记忆').triggered.connect(lambda: comp.report(comp.memory.describe()))
        for fact in comp.memory.data['facts']:
            label=(fact['key']+'=' if fact['key'] else '')+fact['text']
            item=memory.addMenu(label[:50])
            item.addAction('查看这一条').triggered.connect(lambda _=False,label=label:comp.report(label))
            item.addAction('忘记这一条').triggered.connect(lambda _=False,identity=fact['id']:comp.report(comp.memory.forget(identity)))
        memory.addAction('清除近期聊天').triggered.connect(lambda: comp.clear_memory(False))
        memory.addAction('清除长期记忆').triggered.connect(lambda: comp.clear_memory(True))
        reports=menu.addAction('主动汇报任务进展'); reports.setCheckable(True); reports.setChecked(comp.reports_enabled)
        reports.triggered.connect(comp.set_reports)
        menu.addAction('收起输入框').triggered.connect(self.hide)
        menu.exec(position); menu.deleteLater()

    def pick_session(self, identity):
        self.auto.setChecked(False)
        row=self.companion.session_rows.get(identity,{})
        if row.get('cwd'):
            self.companion.project=row['cwd']; self.project.setText(row['cwd'])
        self.companion.select(identity)

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
    def __init__(self, pet, data_dir, saved=None, mode=None, profile=None):
        super().__init__(pet)
        saved = saved or {}
        self.pet = pet
        self.mode = mode or saved.get('pet_mode', 'standalone')
        self.home = saved.get('dsh_home', os.path.join(os.environ.get('USERPROFILE', str(Path.home())), '.dsh'))
        self.dsh_profile = profile or saved.get('dsh_profile', 'desktop')
        if self.dsh_profile not in ('web','desktop'): self.dsh_profile = 'desktop'
        self.input_position = QPoint(saved['input_x'], saved['input_y']) if type(saved.get('input_x')) is int and type(saved.get('input_y')) is int else None
        default_project = next((p for p in Path(__file__).resolve().parents if (p/'启动桌宠.bat').is_file()), Path(__file__).resolve().parent)
        self.project = saved.get('dsh_project', str(default_project))
        self.profile_sessions = dict(saved.get('dsh_profile_sessions', {}))
        self.profile_sessions.setdefault('desktop', saved.get('dsh_session', ''))
        self.session_id = self.profile_sessions.get(self.dsh_profile, '')
        self.auto_follow = saved.get('dsh_auto_follow', True)
        self.memory = CompanionMemory(data_dir)
        self.last_persona_reply = ''
        self.last_task_reply = ''
        self.reply_bubble = None
        self.chatting = False
        self.chat_reply = None
        self.chat_epoch = 0
        self.chat_turn_id = ''
        self.emotion_reply = None; self.emotion_epoch = 0
        self.session_rows = {}
        self.reports_enabled = saved.get('companion_reports', True)
        self.report_reason = ''
        self.report_identity = ''
        self.last_report_at = 0.0
        self.task_revision = 0
        self.reported_revision = 0
        self.recovering_sessions = set()
        self.closed = False
        self.activity = Activity()
        self.busy = False
        self.connected = False
        self.live_text = ''
        self.last_stream_at = 0.0
        self.window = None
        self.sending = False
        self.creating = False
        self.rendered_state = 'idle'
        self.clip_state = ''; self.clip_deadline = 0.0
        self.thinking_played = False
        self.pending_reaction = None
        self.reaction_last = {}; self.reaction_at = -1e12
        self.reaction_timer = QTimer(self, timeout=self.reaction_tick)
        self.reaction_timer.setInterval(250)
        self.pending_outcome = None; self.outcome_deadline = 0.0; self.outcome_animation = None
        self.work_pools = {state:BalancedChooser(pool,pet.rng) for state,pool in WORK_POOLS.items()}
        self.outcome_pools = {'success':BalancedChooser(SUCCESS_RULES,pet.rng), 'error':BalancedChooser(ERROR_RULES,pet.rng)}
        self.animation_timer = QTimer(self,timeout=self.animation_tick)
        self.animation_timer.setInterval(500)
        if self.mode == 'dsh': self.animation_timer.start()
        self.client = BridgeClient(Path(data_dir) / 'dsh-bridge.json', self, self.dsh_profile)
        self.client.sessions.connect(self.sessions_changed)
        self.client.frame.connect(self.frame_received)
        self.client.task_status.connect(self.task_status_received)
        self.client.chat_partial.connect(self.chat_partial_received)
        self.client.connection.connect(self.connection_changed)
        self.client.problem.connect(self.report)
        self.finished_timer = QTimer(self, singleShot=True, timeout=self.finish_animation)
        self.celebration_timer = QTimer(self, singleShot=True, timeout=self.celebrate)
        self.report_timer = QTimer(self, singleShot=True, timeout=self.task_report)
        self.progress_timer = QTimer(self, timeout=self.progress_report)
        self.progress_timer.start(90000)
        # Chat uses only the configured model transport; standalone mode does
        # not follow or execute a Harness task unless the user requests one.
        QTimer.singleShot(0, lambda: self.client.enable(not self.closed))

    def preferences(self):
        self.profile_sessions[self.dsh_profile] = self.session_id
        value = {'pet_mode':self.mode, 'dsh_home':self.home, 'dsh_project':self.project,
                'dsh_session':self.session_id, 'dsh_auto_follow':self.auto_follow,
                'companion_reports':self.reports_enabled, 'dsh_profile':self.dsh_profile,
                'dsh_profile_sessions':dict(self.profile_sessions)}
        position = self.window.pos() if self.window else self.input_position
        if position is not None: value.update(input_x=position.x(), input_y=position.y())
        return value

    def add_connection_menu(self, menu):
        sources = menu.addMenu('连接 dsh')
        for profile, label in (('web','网页版'), ('desktop','桌面版')):
            action = sources.addAction(label); action.setCheckable(True); action.setChecked(self.dsh_profile == profile)
            action.triggered.connect(lambda _=False, selected=profile: self.set_dsh_profile(selected))

    def set_dsh_profile(self, profile):
        if profile not in ('web','desktop') or profile == self.dsh_profile: return
        self.profile_sessions[self.dsh_profile] = self.session_id
        self.select('')
        self.sending = self.creating = self.connected = False
        self.session_rows.clear()
        self.dsh_profile = profile
        self.session_id = self.profile_sessions.get(profile, '')
        if self.window:
            self.window.sessions.blockSignals(True); self.window.sessions.clear(); self.window.sessions.blockSignals(False)
            self.window.send_button.setEnabled(True)
        self.client.set_profile(profile)
        self.pet.schedule_save()

    def open_window(self):
        if self.window is None:
            self.window = CommandWindow(self)
            area = self.pet._screen_rect()
            x = max(area.left(), min(self.pet.x() + self.pet.width() // 2 - self.window.width() // 2, area.right() + 1 - self.window.width()))
            y = max(area.top(), min(self.pet.y() - self.window.height() - 8, area.bottom() + 1 - self.window.height()))
            if self.input_position is not None:
                x = max(area.left(), min(self.input_position.x(), area.right()+1-self.window.width()))
                y = max(area.top(), min(self.input_position.y(), area.bottom()+1-self.window.height()))
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
        self.stop_chat()
        self.report_timer.stop(); self.report_reason = ''
        self.activity = Activity()
        self.rendered_state = 'idle'
        self.finished_timer.stop()
        self.celebration_timer.stop()
        self.pet.set_state('idle')
        self.client.polling = False
        if mode == 'standalone': self.client.stop_stream()
        else: self.client.refresh()
        if self.window:
            self.window.send_button.setEnabled(True)
            self.window.mode.blockSignals(True); self.window.mode.setCurrentIndex(1 if mode == 'dsh' else 0); self.window.mode.blockSignals(False)
        self.pet.schedule_save()

    def set_auto_follow(self, on):
        self.auto_follow = bool(on); self.pet.schedule_save()

    def set_project(self, project):
        self.project = project.strip(); self.select(''); self.pet.schedule_save(); self.client.refresh()

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
        rows = [row for row in rows if not row.get('parentSessionId') and not row.get('archived')]
        self.session_rows = {row['sessionId']:row for row in rows}
        scoped = [row for row in rows if self.same_project(row.get('cwd',''), self.project)]
        current = self.session_rows.get(self.session_id)
        # Recover the former unregistered test conversation, preserving its
        # messages, but never auto-follow validation folders again.
        if current and '_validation' in Path(current.get('cwd','')).parts:
            identity = current['sessionId']
            if not current.get('workspaceId') and identity not in self.recovering_sessions:
                self.recovering_sessions.add(identity)
                self.client.request('/attach', {'sessionId':identity}, failure=lambda _:None)
            if self.auto_follow: self.select(''); current = None
        if self.mode != 'dsh':
            self.client.stop_stream(); return
        if self.sending or self.creating or self.chatting:
            return
        running = next((row for row in scoped if row.get('running') and '_validation' not in Path(row.get('cwd','')).parts), None)
        wanted = running['sessionId'] if self.auto_follow and running else self.session_id if current else ''
        if self.window:
            combo = self.window.sessions; combo.blockSignals(True); combo.clear()
            for row in rows:
                name = row.get('title') or Path(row.get('cwd') or '').name or row['sessionId'][:12]
                folder=Path(row.get('cwd') or '').name
                combo.addItem(('● ' if row.get('running') else '') + name[:45] + ' · ' + folder, row['sessionId'])
            combo.setCurrentIndex(combo.findData(wanted)); combo.blockSignals(False)
        if not wanted and self.session_id:
            self.select('')
        if wanted and wanted != self.session_id:
            self.select(wanted)
        elif wanted and self.client.session_id != wanted:
            self.client.follow(wanted)

    def select(self, session_id):
        self.stop_chat()
        self.report_timer.stop(); self.report_reason = ''; self.task_revision = self.reported_revision = 0
        self.session_id = session_id
        self.activity = Activity(); self.busy = False; self.live_text = ''
        self.pending_outcome = None; self.clip_state = ''; self.clip_deadline = 0.0
        self.rendered_state = 'idle'
        self.finished_timer.stop()
        if self.pet.anim.name.startswith('dsh_') or (self.pet.state=='expr' and self.pet.state_len>=1e8):
            self.pet.set_state('idle')
        if self.window:
            self.window.live.clear(); self.window.live.hide()
            self.window.update_tooltip()
        if self.mode == 'dsh': self.client.follow(session_id)
        self.pet.schedule_save()

    @staticmethod
    def same_project(first, second):
        return bool(first and second) and os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))

    def show_reply(self, message):
        if not message: return
        self.last_persona_reply = clean(message,400)
        if self.reply_bubble is None: self.reply_bubble = ReplyBubble(self)
        self.pet.bubble = None
        self.reply_bubble.show_reply(self.last_persona_reply)
        self.pet.update()

    def report(self, message):
        if self.window: self.window.append('蓝色大肥鱼', clean(message,400))
        else: self.show_reply(message)

    def chat_partial_received(self, value):
        if not self.chatting or value.get('turnId')!=self.chat_turn_id: return
        if isinstance(value.get('reply'),str): self.show_reply(clean(value['reply'],300))

    def stop_chat(self):
        self.chat_epoch += 1; self.chatting = False
        self.chat_turn_id = ''
        self.stop_emotion()
        self.pending_reaction = None; self.reaction_timer.stop()
        reply,self.chat_reply = self.chat_reply,None
        if reply is not None: reply.abort()

    def stop_emotion(self):
        self.emotion_epoch += 1
        reply, self.emotion_reply = self.emotion_reply, None
        if reply is not None: reply.abort()

    def clear_memory(self, long_term):
        self.stop_chat(); self.memory.clear(long_term)
        self.report('长期记忆已清空啦。' if long_term else '近期聊天已经清空，我们重新聊吧。')

    def set_reports(self, enabled):
        self.reports_enabled = bool(enabled); self.pet.schedule_save()
        if not enabled: self.report_timer.stop(); self.report_reason = ''

    def new_session(self, callback=None, title=''):
        if isinstance(callback,bool): callback = None
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
            self.select(value['sessionId'])
            self.session_rows[value['sessionId']] = {'sessionId':value['sessionId'], 'cwd':value.get('cwd',self.project),
                'title':value.get('title',title), 'workspaceId':value.get('workspaceId')}
            self.client.refresh()
            if callback: callback()
            else: self.report('新会话准备好啦，主人可以交代工作了。在 dsh 对应工作目录下能找到它。')
        def failed(message):
            self.creating = False
            if self.window:
                self.window.new_button.setEnabled(True); self.window.send_button.setEnabled(True)
            self.report(message)
        title = title or '蓝色大肥鱼 · ' + time.strftime('%m-%d %H:%M')
        self.client.request('/session', {'cwd':self.project,'title':title}, created, failed)

    def send(self, text, mode='queue'):
        text = text.strip()
        if self.sending or self.creating or self.chatting:
            self.report('我还在处理上一句话呢，稍等一下。')
            return
        if len(text) > 32000:
            self.report('指令过长，请拆成几条发送。'); return
        if text.lstrip('/') in ('喂饭','投喂','睡觉','醒醒','安静','活泼','隐藏','散步','写代码','摸尾巴','摸头','开心','dsh','连接dsh'):
            self.local_command(text)
            self.clear_input(text)
            return
        memory_text = text.removeprefix('/').strip()
        if memory_text.startswith(('记住：','记住:','记住 ')):
            self.report(self.memory.remember(memory_text[3:].strip())); self.clear_input(text); return
        if memory_text.startswith(('忘记：','忘记:','忘记 ')):
            self.report(self.memory.forget(memory_text[3:].strip())); self.clear_input(text); return
        if memory_text in ('记忆','我的记忆','查看记忆'):
            self.report(self.memory.describe()); self.clear_input(text); return
        if memory_text == '清除聊天记忆':
            self.clear_memory(False); self.clear_input(text); return
        if text.startswith(('/任务 ','/任务：','/任务:')):
            self.send_task(text[4:].strip(), mode, original=text); return
        if text.startswith(('/插入 ','/插入：','/插入:')):
            self.send_task(text[4:].strip(), 'steer', original=text); return
        if text in ('/停止','停止当前任务','停止当前工作'):
            self.cancel(); self.clear_input(text); return
        if len(text)>4000:
            self.report('聊天每次最多四千字，长任务请在前面加“/任务 ”，我就直接交给 dsh。'); return
        if not self.connected:
            self.react(keyword_reaction(text))
            source = '网页版' if self.dsh_profile == 'web' else '桌面版'
            self.report('我还没连上' + source + ' dsh。先运行“接入本地DSH.bat”并打开对应版本，右键输入框的“连接 dsh”可以切换。'); return
        self.chat(text, mode)

    def clear_input(self, text):
        if self.window and self.window.input.text().strip() == text: self.window.input.clear()

    def chat(self, text, delivery='queue', event='user', reason=''):
        self.chatting = True
        epoch=self.chat_epoch; identity=self.session_id
        user_text=text
        only_chat=text.startswith('/聊 ')
        if only_chat: text=text[3:].strip()
        input_emotion = keyword_reaction(text) if event == 'user' else 'none'
        if event == 'user':
            self.stop_emotion()
            self.pending_reaction = None; self.reaction_timer.stop()
            self.react(input_emotion)
        turn_started = time.monotonic()
        touched_at = self.interaction_stamp()
        body={**self.memory.context(text), 'text':text,'turnId':str(uuid.uuid4()),'sessionId':identity,
              'mode':self.mode,'project':self.project,'event':event,'reason':reason,'stream':True}
        self.chat_turn_id=body['turnId']
        def done(value):
            if epoch != self.chat_epoch or identity != self.session_id: return
            self.chatting=False; self.chat_reply=None; self.chat_turn_id=''
            reply=clean(value.get('reply',''),300)
            action=value.get('action','chat') if not only_chat and event=='user' else 'chat'
            if action in ('task','steer','new_task'):
                self.stop_emotion(); self.pending_reaction = None; self.reaction_timer.stop()
                self.send_task(user_text,'steer' if action=='steer' and self.busy else delivery,original=user_text,new=action=='new_task')
            elif action=='cancel':
                self.stop_emotion(); self.pending_reaction = None; self.reaction_timer.stop()
                self.cancel(); self.clear_input(user_text)
            else:
                self.report(reply)
                if event=='user':
                    self.memory.add_turn(user_text,reply); self.clear_input(user_text)
                else:
                    self.last_report_at=time.monotonic(); self.reported_revision=self.task_revision
        def failed(message):
            if epoch != self.chat_epoch: return
            self.chatting=False; self.chat_reply=None; self.chat_turn_id=''
            if event=='user': self.report(message + ' 这句话还留在输入框里。')
            elif self.mode=='dsh' and identity==self.session_id:
                fallback={'waiting':'主人，工作在等你确认或回答问题，请去 dsh 看一眼。',
                    'success':'这轮工作结束啦，具体结果在 dsh；要我帮你看看重点也可以哦。',
                    'error':'呜，工作遇到错误了，请去 dsh 看具体原因。', 'idle':'这轮工作已停止，详情在 dsh。'}
                if reason in ('waiting','success','error','idle'): self.report(fallback[reason])
        self.chat_reply=self.client.request('/chat',body,done,failed)
        # Start the reply first. Classification runs independently, only for
        # unmatched dialogue beyond known neutral questions; neither completion
        # waits for the other.
        if event == 'user' and input_emotion == 'none' and not is_neutral_chat(text):
            emotion_epoch = self.emotion_epoch
            def classified(value):
                if emotion_epoch != self.emotion_epoch or epoch != self.chat_epoch or identity != self.session_id: return
                self.emotion_reply = None
                if time.monotonic()-turn_started < 10 and self.interaction_stamp() == touched_at:
                    self.react(value.get('emotion','none'))
            def ignored(_):
                if emotion_epoch == self.emotion_epoch: self.emotion_reply = None
            self.emotion_reply = self.client.request('/emotion', {'text':text,'turnId':body['turnId'],'sessionId':identity}, classified, ignored)

    def send_task(self, text, mode='queue', original=None, new=False):
        if not text: return
        original = original or text
        if not self.connected:
            self.report('任务还没发出去：请先打开并接入 dsh，输入已经保留。'); return
        if self.sending or self.creating: return
        if self.mode!='dsh': self.set_mode('dsh')
        row=self.session_rows.get(self.session_id,{})
        if new or not self.session_id or not row or not self.same_project(row.get('cwd',''),self.project):
            self.new_session(lambda:self.send_task(text,mode,original), '蓝色大肥鱼 · '+text.replace('\n',' ')[:40]); return
        self.set_auto_follow(False)
        if self.window: self.window.auto.setChecked(False)
        identity=self.session_id
        self.sending = True
        if self.window:
            self.window.send_button.setEnabled(False)
        request_id = str(uuid.uuid4())
        def done(value):
            self.sending = False
            if self.window:
                self.window.send_button.setEnabled(True)
            if value.get('accepted') is not True:
                self.report('dsh 没有确认接收这条指令，输入已经保留。'); return
            self.clear_input(original)
            title=self.session_rows.get(identity,{}).get('title') or identity[:20]
            reply=('已经把新要求插进当前工作啦。' if mode=='steer' else '任务已经交给 dsh 啦，忙的时候会排队。')+'我会帮你留意进展。\ndsh 会话：'+title[:50]
            self.report(reply); self.memory.add_turn(original,reply)
        def failed(message):
            self.sending = False
            if self.window:
                self.window.send_button.setEnabled(True)
            self.report(message + '\n指令已保留在输入框中，请检查后再发送。')
        def submit(_=None):
            self.client.request('/prompt', {'sessionId':identity, 'requestId':request_id, 'text':text,
                                           'mode':mode, 'clientTimeZone':'Asia/Shanghai'}, done, failed)
        if row.get('workspaceId'): submit()
        else:
            def attached(value):
                self.session_rows.setdefault(identity,{}).update(value); submit()
            self.client.request('/attach',{'sessionId':identity},attached,failed)

    def cancel(self):
        if self.session_id and not self.creating:
            self.client.request('/cancel', {'sessionId':self.session_id}, lambda _: self.report('已经请求停止当前工作啦，等 dsh 确认结束。排队里的指令会保留。'), self.report)
        else: self.report('现在还没有绑定任务哦。')

    def local_command(self, text):
        self.pet._touch()
        self.open_window(); self.window.append('你', text)
        commands = {'喂饭':lambda: self.pet.interact('rice'), '投喂':lambda: self.pet.interact('rice'),
                    '睡觉':lambda: self.pet.set_state('sleepy'), '醒醒':lambda: self.pet.recall(),
                    '安静':lambda: self.pet.set_quiet(True), '活泼':lambda: self.pet.set_quiet(False),
                    '隐藏':self.pet.hide_pet, '散步':lambda: self.pet.do_action('walk', 0),
                    '游泳':lambda: self.pet.do_action('swim',0),
                    '停止游泳':lambda:self.pet._begin_swim_return() if self.pet.state in SWIM_STATES else None,
                    '写代码':lambda: self.pet.do_action('ds_code_eureka', 0),
                    '摸尾巴':lambda: self.pet.interact('tail'), '摸头':lambda: self.pet.do_action('petted', 2), '开心':lambda: self.pet.do_action('happy', 0)}
        command = text.strip().lstrip('/')
        if command in commands:
            commands[command](); self.window.append('蓝色大肥鱼', '收到～')
        elif command in ('dsh','连接dsh'):
            self.set_mode('dsh'); self.window.append('蓝色大肥鱼', '正在连接本机 dsh…')
        else:
            self.send(text)

    def progress_report(self):
        if self.busy and self.task_revision-self.reported_revision>=3 and time.monotonic()-self.last_report_at>=90:
            self.queue_report('progress',500)

    def queue_report(self, reason, delay=1500):
        if not self.reports_enabled or not self.session_id or self.mode!='dsh': return
        self.report_reason=reason; self.report_identity=self.session_id; self.report_timer.start(delay)

    def task_report(self):
        if not self.reports_enabled or self.report_identity!=self.session_id or not self.connected: return
        if self.chatting or self.sending or self.creating:
            self.report_timer.start(4000); return
        reason,self.report_reason=self.report_reason,''
        if reason: self.chat('',event='report',reason=reason)

    def task_status_received(self, status):
        if self.mode!='dsh' or status.get('sessionId')!=self.session_id or status.get('unavailable'): return
        if status.get('cursor',-1)<self.activity.cursor: return
        value=dict(status)
        # A live text stream is newer than the most recent durable step/start.
        if (value.get('running') is True and value.get('state')=='thinking' and self.activity.state=='replying'
                and time.monotonic()-self.last_stream_at<5 and value.get('cursor')==self.activity.cursor):
            value['state']='replying'
        # Reconnecting to an old completed task must not celebrate again.
        if value.get('state') in ('success','error') and not self.busy:
            value['state']='idle'
        previous=self.rendered_state
        if self.activity.reconcile(value):
            self.set_activity(self.activity.state)
            if previous in WORK_POOLS and self.activity.state in ('success','error','idle'):
                self.queue_report(self.activity.state)

    def frame_received(self, frame):
        kind = frame.get('type')
        if kind == 'bridge-error':
            self.report(frame.get('error', 'dsh 连接异常')); return
        if kind == 'snapshot':
            self.activity.snapshot(frame)
            self.set_activity(self.activity.state)
            return
        if kind == 'event':
            event = frame.get('event', {})
            state = self.activity.consume(event)
            if state is not None:
                self.display_event(event); self.set_activity(state)
                event_kind=event.get('type','')
                if event_kind=='turn/start': self.report_timer.stop(); self.report_reason=''
                if event_kind=='tool/result': self.task_revision+=1
                if event_kind=='approval/asked' or (event_kind=='tool/call' and state=='waiting'):
                    self.queue_report('waiting')
                elif event_kind in ('turn/end','agent/error'):
                    self.queue_report(state)
            return
        if kind == 'assistant-stream':
            self.last_stream_at=time.monotonic()
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
                    self.set_activity(self.activity.stream('text-delta'))
            elif value.get('type') == 'end' and self.window:
                self.window.live.clear(); self.window.live.hide()

    def display_event(self, event):
        kind = event.get('type')
        if kind == 'user/message' and event.get('data', {}).get('source', {}).get('kind', 'user') != 'user':
            return
        if kind == 'assistant/message':
            self.last_task_reply=message_text(event.get('data', {}))[-2400:]

    def can_animate(self):
        if self.pet.dragging or self.pet.menu_open or self.pet.press_pos is not None or not self.pet.isVisible():
            return False
        if self.pet.state in ('walk','trip')+SWIM_STATES:
            return False
        return self.pet.animation_origin in ('idle','automatic','work') or self.pet.state == 'idle' or (self.pet.state == 'expr' and self.pet.state_len >= 1e8)

    def interaction_stamp(self):
        return (self.pet.last_input, self.pet.input_revision)

    def react(self, emotion):
        """Bounded, local animation only: no model-controlled task operations."""
        if not isinstance(emotion, str) or emotion not in REACTIONS: return
        now = time.monotonic()
        if now-self.reaction_at < 1.8 or now-self.reaction_last.get(emotion, -1e12) < REACTIONS[emotion].cooldown:
            return
        self.pending_reaction = (emotion, now+6.0, self.interaction_stamp())
        self.reaction_tick()
        if self.pending_reaction: self.reaction_timer.start()

    def reaction_tick(self):
        pending = self.pending_reaction
        if not pending:
            self.reaction_timer.stop(); return
        emotion, expires, touched = pending
        now = time.monotonic()
        if now > expires or self.interaction_stamp() != touched:
            self.pending_reaction = None; self.reaction_timer.stop(); return
        if not self.can_animate() or self.pet.queue or self.pet.state in ('sleep', 'sleepy', 'wake', 'dangle', 'fall', 'land', 'walk', 'trip')+SWIM_STATES:
            return
        reaction = REACTIONS[emotion]
        self.pending_reaction = None; self.reaction_timer.stop()
        self.reaction_at = self.reaction_last[emotion] = now
        self.pet.last_input = now
        self.pet.animation_origin = 'conversation'
        if reaction.animation:
            self.pet.set_state(reaction.state, reaction.animation, length=reaction.length, origin='conversation')
        else:
            self.pet.start_behaviour(reaction.state, reaction.length)
        self.pet.bubble = None

    def set_activity(self,state):
        if self.activity.pending_approvals or self.activity.pending_questions: state='waiting'
        changed=state!=self.rendered_state
        if changed: self.thinking_played=False
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
        if state=='thinking':
            if self.thinking_played: return
            self.thinking_played=True
            self.clip_state=state;self.clip_deadline=0.0
            self.finished_timer.stop()
            self.pet.set_state('dsh_thinking_once',origin='work')
            return
        owns_clip=self.pet.state=='expr' and self.pet.state_len>=1e8
        if state==self.clip_state and now<self.clip_deadline and owns_clip: return
        animation=self.work_pools[state].choose(now)
        self.clip_state=state; self.clip_deadline=now+self.pet.rng.uniform(*WORK_ROTATION_GAP)
        if animation and (self.pet.anim.name!=animation or not owns_clip):
            self.finished_timer.stop(); self.pet.set_state('expr',animation,length=1e9,origin='work')

    def finish_animation(self):
        if not self.busy:
            self.activity.state='idle'; self.rendered_state='idle'
            if self.pet.animation_origin=='work' and self.pet.anim.name==self.outcome_animation:
                self.pet.set_state('idle')

    def shutdown(self):
        self.closed = True; self.stop_chat(); self.report_timer.stop(); self.progress_timer.stop()
        self.animation_timer.stop(); self.celebration_timer.stop(); self.finished_timer.stop()
        self.client.enable(False)
        if self.window: self.window.hide()
        if self.reply_bubble: self.reply_bubble.timer.stop(); self.reply_bubble.hide(); self.reply_bubble.deleteLater()
