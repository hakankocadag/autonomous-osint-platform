import sys
import os
import socket
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, 
                             QHBoxLayout, QVBoxLayout, QPushButton, QTextEdit, QTextBrowser, 
                             QMessageBox, QDialog, QLabel, QLineEdit, QRadioButton, QButtonGroup)
from PyQt6.QtGui import QAction
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QUrl, QObject, pyqtSlot, Qt, pyqtSignal, QThread

from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from qwen_agent import QwenAgentThread
import subprocess
import json
import re

class ReconWorker(QThread):
    finished = pyqtSignal(str, object)
    error = pyqtSignal(str)

    def __init__(self, local_ai_dir):
        super().__init__()
        self.local_ai_dir = local_ai_dir

    def run(self):
        try:
            result = subprocess.run([sys.executable, "run.py"], cwd=self.local_ai_dir, capture_output=True, text=True)
            
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            clean_output = ansi_escape.sub('', result.stdout)
            
            if result.stderr:
                clean_stderr = ansi_escape.sub('', result.stderr)
                clean_output += f"\n[stderr]\n{clean_stderr}"
                
            report_data = None
            recon_out_path = os.path.join(self.local_ai_dir, "recon_output.json")
            if os.path.exists(recon_out_path):
                with open(recon_out_path, "r", encoding="utf-8") as f:
                    plan = json.load(f)
                target = plan.get("domain") or plan.get("ip") or plan.get("url") or plan.get("hash") or "Unknown"
                safe_name = re.sub(r'[^\w\-.]', '_', target)
                report_file = os.path.join(self.local_ai_dir, "reports", f"report_{safe_name}.json")
                if os.path.exists(report_file):
                    with open(report_file, "r", encoding="utf-8") as f:
                        report_data = json.load(f)

            self.finished.emit(clean_output, report_data)
        except Exception as e:
            self.error.emit(str(e))


class NewsWorker(QThread):
    finished = pyqtSignal(str, object)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, nlp_dir, keyword, hours):
        super().__init__()
        self.nlp_dir = nlp_dir
        self.keyword = keyword
        self.hours = hours

    def run(self):
        try:
            process = subprocess.Popen(
                [sys.executable, "test_swarm.py", "--keyword", str(self.keyword), "--hours", str(self.hours), "--choice", "all"],
                cwd=self.nlp_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
            
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            full_output = ""
            
            for line in process.stdout:
                clean_line = ansi_escape.sub('', line)
                full_output += clean_line
                
                if "🚀 Phase 1" in clean_line:
                    self.progress.emit("Phase 1: Discovering sources...")
                elif "🔍 Discovered" in clean_line:
                    match = re.search(r"Discovered (\d+) unique", clean_line)
                    if match:
                        self.progress.emit(f"Found {match.group(1)} relevant articles. Extracting content...")
                elif "✅  Pushed" in clean_line:
                    self.progress.emit("Extracting article content...")
                elif "⏳ Waiting" in clean_line:
                    self.progress.emit("Processing articles with NLP...")
                elif "HARVEST COMPLETE" in clean_line:
                    self.progress.emit("News collection finished.")
            
            process.wait()
            
            if process.returncode != 0:
                self.error.emit(f"Collector exited with code {process.returncode}\n{full_output}")
                return
                
            report_data = None
            report_path = os.path.join(self.nlp_dir, "intelligence_report.json")
            if os.path.exists(report_path):
                with open(report_path, "r", encoding="utf-8") as f:
                    try:
                        report_data = json.load(f)
                    except json.JSONDecodeError:
                        pass
            
            self.finished.emit(full_output, report_data)
        except Exception as e:
            self.error.emit(str(e))


class SummaryWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, data, provider, api_key):
        super().__init__()
        self.data = data
        self.provider = provider
        self.api_key = api_key

    def run(self):
        try:
            import sys
            import os
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ai_tts_dir = os.path.join(base_dir, "ai-tts")
            if ai_tts_dir not in sys.path:
                sys.path.append(ai_tts_dir)
                
            from report_service import generate_summary_report

            summary_data = generate_summary_report(self.data, self.provider, self.api_key)
            self.finished.emit(summary_data)
        except Exception as e:
            self.error.emit(str(e))


class TTSWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, data, output_file):
        super().__init__()
        self.data = data
        self.output_file = output_file

    def run(self):
        try:
            import sys
            import os
            import json
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ai_tts_dir = os.path.join(base_dir, "ai-tts")
            if ai_tts_dir not in sys.path:
                sys.path.append(ai_tts_dir)
                
            from tts_service import generate_audio_briefing
            
            os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
            
            temp_json = os.path.join(base_dir, "client", "temp_tts_report.json")
            with open(temp_json, "w", encoding="utf-8") as f:
                json.dump(self.data, f)
                
            generate_audio_briefing(temp_json, self.output_file)
            self.finished.emit(self.output_file)
        except Exception as e:
            self.error.emit(str(e))


class MultiLocationWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, locations_data):
        super().__init__()
        self.locations_data = locations_data

    def run(self):
        try:
            import requests
            import time
            results = []
            headers = {"User-Agent": "NewsCollectorApp/1.0 (OSINT Research Tool)"}
            
            for loc_data in self.locations_data:
                loc_name = loc_data.get("location", "")
                if not loc_name or loc_name.lower() == "unknown":
                    continue
                
                url = "https://nominatim.openstreetmap.org/search"
                params = {"q": loc_name, "format": "json", "limit": 1}
                response = requests.get(url, params=params, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    if data and len(data) > 0:
                        lat = float(data[0]["lat"])
                        lng = float(data[0]["lon"])
                        results.append({
                            "location": loc_name,
                            "lat": lat,
                            "lng": lng,
                            "titles": loc_data.get("news_titles", [])
                        })
                time.sleep(1) # respect Nominatim rate limit
            
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

class PromptTextEdit(QTextEdit):
    send_requested = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.send_requested.emit()
        else:
            super().keyPressEvent(event)

class MapBridge(QObject):
    def __init__(self, main_app):
        super().__init__()
        self.app = main_app

    @pyqtSlot(float, float)
    def receive_coordinates(self, lat, lng):
        msg = f"<font color='gray'><i>System: Map Clicked at Lat {lat:.4f}, Lng {lng:.4f}</i></font><br>"
        self.app.ai_output.append(msg)
        self.app.add_marker_to_map(lat, lng, "Selected Area")

class TokenDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowType.Tool)
        self.setWindowFlag(Qt.WindowType.Popup)
        self.setWindowTitle("Set AI Provider & Token")
        layout = QVBoxLayout(self)
        
        self.provider_label = QLabel("Select Provider:")
        layout.addWidget(self.provider_label)
        
        self.provider_group = QButtonGroup(self)
        self.radio_layout = QHBoxLayout()
        
        self.radio_gemini = QRadioButton("Gemini")
        self.radio_openai = QRadioButton("OpenAI")
        self.radio_anthropic = QRadioButton("Anthropic")
        
        self.radio_layout.addWidget(self.radio_gemini)
        self.radio_layout.addWidget(self.radio_openai)
        self.radio_layout.addWidget(self.radio_anthropic)
        layout.addLayout(self.radio_layout)
        
        self.provider_group.addButton(self.radio_gemini, 1)
        self.provider_group.addButton(self.radio_openai, 2)
        self.provider_group.addButton(self.radio_anthropic, 3)
        self.radio_gemini.setChecked(True)
        
        self.label = QLabel("Enter API Token:")
        layout.addWidget(self.label)
        
        self.token_input = QLineEdit()
        if os.path.exists("ai_settings.json"):
            try:
                with open("ai_settings.json", "r") as f:
                    settings = json.load(f)
                    provider = settings.get("provider", "gemini")
                    token = settings.get("api_key", "")
                    
                    if provider == "openai":
                        self.radio_openai.setChecked(True)
                    elif provider == "anthropic":
                        self.radio_anthropic.setChecked(True)
                    else:
                        self.radio_gemini.setChecked(True)
                        
                    self.token_input.setText(token)
            except Exception:
                pass
        layout.addWidget(self.token_input)
        
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_and_close)
        layout.addWidget(self.save_button)
        
    def save_and_close(self):
        token = self.token_input.text().strip()
        
        if self.radio_openai.isChecked():
            provider = "openai"
        elif self.radio_anthropic.isChecked():
            provider = "anthropic"
        else:
            provider = "gemini"
            
        try:
            settings = {"provider": provider, "api_key": token}
            with open("ai_settings.json", "w") as f:
                json.dump(settings, f)
            QMessageBox.information(self, "Success", "Settings saved successfully.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to save settings: {e}")
        self.accept()

class NewsCollectorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI News OSINT Collector")
        self.setGeometry(100, 100, 1200, 800)

        # Menu Bar Setup
        menubar = self.menuBar()
        configuration_menu = menubar.addMenu("Configuration")
        
        set_api_key_action = QAction("Set API Key", self)
        set_api_key_action.triggered.connect(self.open_token_dialog)
        configuration_menu.addAction(set_api_key_action)

        # Audio Setup
        self.audio_player = QMediaPlayer()
        self.audio_output_device = QAudioOutput()
        self.audio_player.setAudioOutput(self.audio_output_device)
        self.audio_output_device.setVolume(1.0) 
        
        self.message_store = {} 
        self.message_counter = 0

        self.qwen_agent = QwenAgentThread()
        self.qwen_agent.model_loaded.connect(self.on_model_loaded)
        self.qwen_agent.token_ready.connect(self.on_token_ready)
        self.qwen_agent.response_ready.connect(self.on_agent_response)
        self.qwen_agent.recon_ready.connect(self.on_recon_ready)
        self.qwen_agent.news_ready.connect(self.on_news_ready)
        self.qwen_agent.error_occurred.connect(self.on_agent_error)
        self.qwen_agent.load_model_async()

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # Map Setup
        self.map_view = QWebEngineView()
        
        self.channel = QWebChannel()
        self.bridge = MapBridge(self)
        self.channel.registerObject("backend", self.bridge)
        self.map_view.page().setWebChannel(self.channel)

        html_path = os.path.abspath("map.html")
        self.map_view.setUrl(QUrl.fromLocalFile(html_path)) 
        
        main_layout.addWidget(self.map_view, stretch=2)

        # Right Panel Setup
        right_panel = QVBoxLayout()
        
        self.ai_output = QTextBrowser() 
        self.ai_output.setOpenLinks(False) 
        self.ai_output.anchorClicked.connect(self.handle_link_click) 
        self.ai_output.setPlaceholderText("Loading Qwen model in background...")
        
        input_layout = QHBoxLayout()
        
        self.ai_prompt = PromptTextEdit()
        self.ai_prompt.send_requested.connect(self.handle_prompt)
        self.ai_prompt.setPlaceholderText("Enter AI prompt here...")
        self.ai_prompt.setMaximumHeight(60)
        
        self.send_button = QPushButton("Send")
        self.send_button.setMinimumHeight(60)
        self.send_button.clicked.connect(self.handle_prompt)
        
        input_layout.addWidget(self.ai_prompt)
        input_layout.addWidget(self.send_button)
        
        right_panel.addWidget(self.ai_output)
        right_panel.addLayout(input_layout)

        self.cyber_security_button = QPushButton("Scan for Cyber Security")
        self.cyber_security_button.clicked.connect(self.start_cyber_security_scan)
        right_panel.addWidget(self.cyber_security_button)

        main_layout.addLayout(right_panel, stretch=1)

    def handle_prompt(self):
        user_text = self.ai_prompt.toPlainText().strip()
        if not user_text:
            return

        self.ai_output.append(f"<b>You:</b> {user_text}<br>")
        self.ai_prompt.clear()

        cursor = self.ai_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.ai_output.setTextCursor(cursor)
        self.ai_output.insertHtml("<font color='#0055ff'><b>Qwen Agent:</b></font> ")

        self.qwen_agent.generate_async(user_text)

        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def start_cyber_security_scan(self):
        user_text = self.ai_prompt.toPlainText().strip()
        if not user_text:
            return

        self.ai_output.append(f"<b>You (Cyber Scan):</b> {user_text}<br>")
        self.ai_prompt.clear()

        cursor = self.ai_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.ai_output.setTextCursor(cursor)
        self.ai_output.insertHtml("<font color='#0055ff'><b>System:</b></font> Generating scan plan...<br>")

        self.qwen_agent.generate_async(user_text, is_recon=True)

        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_recon_ready(self, response):
        self.ai_output.append(f"<font color='green'><b>System:</b> Scan plan generated!</font><br>")
        
        clean = response.strip()
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if match:
            clean = match.group(0)
        try:
            plan = json.loads(clean)
            
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            local_ai_dir = os.path.join(base_dir, "local-ai")
            recon_file = os.path.join(local_ai_dir, "recon_output.json")
            
            with open(recon_file, "w") as f:
                json.dump(plan, f, indent=4)
                
            self.ai_output.append("<font color='orange'><b>System:</b> Running reconnaissance tools... This may take a while.</font><br>")
            scrollbar = self.ai_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            
            self.recon_worker = ReconWorker(local_ai_dir)
            self.recon_worker.finished.connect(self.on_recon_finished)
            self.recon_worker.error.connect(self.on_recon_error)
            self.recon_worker.start()

        except json.JSONDecodeError as e:
            self.ai_output.append(f"<font color='red'><b>System Error:</b> Failed to parse JSON plan: {e}</font><br>")
            self.ai_output.append(f"<pre>{response}</pre><br>")
            scrollbar = self.ai_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def on_recon_finished(self, output, report_data):
        if report_data:
            html = self.format_report_html(report_data)
            self.ai_output.append(html)
        else:
            self.ai_output.append(f"<font color='green'><b>System:</b> Reconnaissance complete:</font><br><pre>{output}</pre><br>")
        
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def format_report_html(self, data):
        meta = data.get("meta", {})
        results = data.get("results", {})
        
        target = meta.get("target", "Unknown")
        target_type = meta.get("target_type", "Unknown")
        
        html = f"""
        <div style="background-color: #1e1e1e; padding: 15px; font-family: monospace; color: #d4d4d4; margin-top: 10px; margin-bottom: 10px;">
            <h2 style="color: #c586c0; margin-top: 0; margin-bottom: 10px;">RECON REPORT: {target} ({target_type.upper()})</h2>
            <hr style="border: 1px solid #444; margin-bottom: 15px;">
        """
        
        for tool, result in results.items():
            if "error" in result:
                html += f"<div style='margin-bottom: 10px;'><b style='color: #f44747;'>[+] {tool.upper()}</b> <span style='color: #f44747;'>Failed: {result['error']}</span></div>"
                continue
            
            html += f"<div style='margin-bottom: 5px;'><b style='color: #569cd6;'>[+] {tool.upper()}</b></div>"
            html += "<table style='margin-left: 15px; margin-bottom: 15px; border-collapse: collapse; width: 95%;'>"
            
            if tool == "whois" and "parsed" in result:
                for k, v in result["parsed"].items():
                    val = "<br>".join(v) if isinstance(v, list) else v
                    html += f"<tr><td style='color: #9cdcfe; width: 160px; padding: 4px; vertical-align: top;'>{k.replace('_', ' ').title()}</td><td style='color: #ce9178; padding: 4px;'>{val}</td></tr>"
            
            elif tool == "dig":
                for rec_type, records in result.items():
                    val = "<br>".join(records)
                    html += f"<tr><td style='color: #9cdcfe; width: 160px; padding: 4px; vertical-align: top;'>{rec_type} Records</td><td style='color: #ce9178; padding: 4px;'>{val}</td></tr>"
                    
            elif tool in ["ipinfo", "virustotal", "shodan"]:
                for k, v in result.items():
                    if k != "raw":
                        val = "<br>".join([str(x) for x in v]) if isinstance(v, list) else str(v)
                        html += f"<tr><td style='color: #9cdcfe; width: 160px; padding: 4px; vertical-align: top;'>{k.replace('_', ' ').title()}</td><td style='color: #ce9178; padding: 4px;'>{val}</td></tr>"
            
            else:
                for k, v in result.items():
                    if k == "raw": continue
                    val = str(v).replace('\\n', '<br>')
                    html += f"<tr><td style='color: #9cdcfe; width: 160px; padding: 4px; vertical-align: top;'>{k.replace('_', ' ').title()}</td><td style='color: #ce9178; padding: 4px;'>{val}</td></tr>"
            
            html += "</table>"
            
        html += "</div><br>"
        return html

    def on_recon_error(self, error):
        self.ai_output.append(f"<font color='red'><b>System Error:</b> Reconnaissance failed: {error}</font><br>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_model_loaded(self):
        self.ai_output.append("<font color='green'><i>System: Qwen model loaded successfully! Ready for instructions.</i></font><br>")
        self.ai_output.setPlaceholderText("Qwen model loaded. Awaiting instructions...")
    def on_token_ready(self, token):
        cursor = self.ai_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.ai_output.setTextCursor(cursor)
        self.ai_output.insertPlainText(token)
        
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
    def on_agent_response(self, response):
        clean = response.strip()
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if data.get("intent") == "news" or "keyword" in data:
                    # Pass the raw response to on_news_ready which handles parsing again
                    self.on_news_ready(response)
                    return
            except Exception:
                pass
                
        # If streaming was suppressed because it looked like JSON but it wasn't news intent
        if clean.startswith("{") or clean.startswith("```"):
            cursor = self.ai_output.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.ai_output.setTextCursor(cursor)
            self.ai_output.insertPlainText(response)

        self.message_counter += 1
        msg_id = str(self.message_counter)
        self.message_store[msg_id] = response

        read_link = f"&nbsp;<a href='tts:{msg_id}' style='text-decoration:none;'>[▶ Read]</a><br><br>"
        cursor = self.ai_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.ai_output.setTextCursor(cursor)
        self.ai_output.insertHtml(read_link)
        
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_agent_error(self, error_msg):
        self.ai_output.append(f"<font color='red'><b>Agent Error:</b> {error_msg}</font><br><br>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def handle_link_click(self, url: QUrl):
        if url.scheme() == "tts":
            if self.audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.audio_player.stop()
                return

            msg_id = url.path()
            if not msg_id:
                msg_id = url.toString().replace("tts:", "")
                
            data = self.message_store.get(msg_id)
            if not data:
                return
                
            if isinstance(data, str):
                data = {"summary": data}
                
            import hashlib
            content_str = json.dumps(data, sort_keys=True)
            content_hash = hashlib.md5(content_str.encode()).hexdigest()[:10]
                
            audio_path = os.path.abspath(os.path.join("voice", f"briefing_{msg_id}_{content_hash}.mp3"))
            os.makedirs("voice", exist_ok=True)
            
            if os.path.exists(audio_path):
                self.audio_player.setSource(QUrl.fromLocalFile(audio_path))
                self.audio_player.play()
            else:
                self.ai_output.append(f"<font color='orange'><i>System: Generating audio briefing for message {msg_id}...</i></font><br>")
                scrollbar = self.ai_output.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                
                self.tts_worker = TTSWorker(data, audio_path)
                self.tts_worker.finished.connect(self.on_tts_finished)
                self.tts_worker.error.connect(self.on_tts_error)
                self.tts_worker.start()

    def on_tts_finished(self, audio_path):
        self.ai_output.append("<font color='green'><i>System: Audio briefing ready. Playing...</i></font><br>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        self.audio_player.setSource(QUrl.fromLocalFile(audio_path))
        self.audio_player.play()

    def on_tts_error(self, error):
        self.ai_output.append(f"<font color='red'><i>System Error: TTS generation failed: {error}</i></font><br>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def add_marker_to_map(self, lat, lng, text):
        js_code = f"addMarker({lat}, {lng}, '{text}');"
        self.map_view.page().runJavaScript(js_code)


    def on_news_ready(self, response):
        clean = response.strip()
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if match:
            clean = match.group(0)
        try:
            plan = json.loads(clean)
            error = plan.get("error")
            if error:
                self.ai_output.append(f"<font color='red'><b>Agent:</b> {error}</font><br>")
                scrollbar = self.ai_output.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                return
            
            keyword = plan.get("keyword", "news")
            hours = plan.get("hours", 24)
            
            self.ai_output.append(f"<font color='green'><b>System:</b> Request parsed. Topic: {keyword}, Hours: {hours}. Running collector...</font><br>")
            scrollbar = self.ai_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            nlp_dir = os.path.join(base_dir, "nlp")
            
            self.news_worker = NewsWorker(nlp_dir, keyword, hours)
            self.news_worker.progress.connect(self.on_news_progress)
            self.news_worker.finished.connect(self.on_news_finished)
            self.news_worker.error.connect(self.on_news_error)
            self.news_worker.start()

        except json.JSONDecodeError as e:
            self.ai_output.append(f"<font color='red'><b>System Error:</b> Failed to parse JSON plan: {e}</font><br>")
            self.ai_output.append(f"<pre>{response}</pre><br>")
            scrollbar = self.ai_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def on_news_progress(self, msg):
        self.ai_output.append(f"<font color='gray'><i>Collector: {msg}</i></font><br>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_news_finished(self, output, report_data):
        self.ai_output.append("<font color='green'><b>System:</b> News collected successfully.</font><br>")
        if report_data:
            self.ai_output.append(f"<font color='green'>Found {len(report_data)} articles.</font><br>")
            self.ai_output.append("<font color='orange'><b>System:</b> Summarizing news with AI...</font><br>")
            
            token_path = "ai_settings.json"
            api_key = ""
            provider = "gemini"
            if os.path.exists(token_path):
                try:
                    with open(token_path, "r") as f:
                        settings = json.load(f)
                        api_key = settings.get("api_key", "").strip()
                        provider = settings.get("provider", "gemini").strip()
                except Exception:
                    pass
                    
            if not api_key:
                self.ai_output.append(f"<font color='red'><b>System Error:</b> {provider.capitalize()} API Key is missing. Please set it via Configuration > Set API Key.</font><br>")
                scrollbar = self.ai_output.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                return
                
            self.summary_worker = SummaryWorker(report_data, provider, api_key)
            self.summary_worker.finished.connect(self.on_summary_finished)
            self.summary_worker.error.connect(self.on_summary_error)
            self.summary_worker.start()
        else:
            self.ai_output.append("<font color='red'><b>System Error:</b> No report data collected to summarize.</font><br>")
            
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_summary_finished(self, summary_data):
        self.ai_output.append("<font color='green'><b>System:</b> Summary generation complete.</font><br>")
        print(summary_data)
        summary_text = summary_data.get("summary", "No summary available.")
        
        locations = summary_data.get("locations", [])
        if locations:
            self.multi_loc_worker = MultiLocationWorker(locations)
            self.multi_loc_worker.finished.connect(self.on_multi_location_found)
            self.multi_loc_worker.error.connect(self.on_location_error)
            self.multi_loc_worker.start()
        else:
            location = summary_data.get("location", "")
            if location and location.lower() != "unknown" and location.strip():
                self.multi_loc_worker = MultiLocationWorker([{"location": location, "news_titles": []}])
                self.multi_loc_worker.finished.connect(self.on_multi_location_found)
                self.multi_loc_worker.error.connect(self.on_location_error)
                self.multi_loc_worker.start()
            
        self.message_counter += 1
        msg_id = str(self.message_counter)
        self.message_store[msg_id] = summary_data
        
        html_summary = re.sub(r'#+\s*(.*)', r'<h3 style="color:#569cd6; margin-bottom: 5px; margin-top: 15px;">\1</h3>', summary_text)
        html_summary = html_summary.replace('\n', '<br>')
        
        sources = summary_data.get("sources", [])
        sources_html = ""
        if sources:
            sources_list = ", ".join(sources)
            sources_html = f"<div style='margin-top: 20px; color: #858585; font-size: 0.9em; font-style: italic; border-top: 1px dashed #555; padding-top: 10px;'><b>Sources:</b> {sources_list}</div>"

        html = f"""
        <div style="padding: 15px; color: #d4d4d4; margin-top: 10px; margin-bottom: 10px;">
            <h2 style="color: #c586c0; margin-top: 0; margin-bottom: 10px;">AI NEWS SUMMARY</h2>
            <hr style="border: 1px solid #444; margin-bottom: 15px;">
            <div style='color: #ce9178;'>{html_summary}</div>
            {sources_html}
        </div><br>
        &nbsp;<a href='tts:{msg_id}' style='text-decoration:none;'>[▶ Read]</a><br><br>
        """
        self.ai_output.append(html)
        
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_multi_location_found(self, results):
        if not results:
            return
            
        self.map_view.page().runJavaScript("clearMarkers();")
            
        for res in results:
            lat = res["lat"]
            lng = res["lng"]
            loc_name = res["location"]
            titles = res["titles"]
            
            titles_html = "<br>".join([f"• {t}" for t in titles])
            popup_text = f"<b>{loc_name}</b><br>{titles_html}" if titles else f"<b>{loc_name}</b>"
            # Escape single quotes for JS
            popup_text = popup_text.replace("'", "\\'")
            
            # self.ai_output.append(f"<font color='green'><i>System: Placed marker at {loc_name} (Lat: {lat:.4f}, Lng: {lng:.4f})</i></font><br>")
            self.add_marker_to_map(lat, lng, popup_text)
            
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        if results:
            first_lat = results[0]["lat"]
            first_lng = results[0]["lng"]
            zoom_level = 4 if len(results) > 1 else 6
            js_code = f"map.flyTo([{first_lat}, {first_lng}], {zoom_level});"
            self.map_view.page().runJavaScript(js_code)

    def on_location_error(self, error_msg):
        self.ai_output.append(f"<font color='orange'><i>System Warning: Could not find map coordinates for location - {error_msg}</i></font><br>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_summary_error(self, error):
        self.ai_output.append(f"<font color='red'><b>System Error:</b> Summarization failed: {error}</font><br>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_news_error(self, error):
        self.ai_output.append(f"<font color='red'><b>System Error:</b> News collector failed: {error}</font><br>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def open_token_dialog(self):
        dialog = TokenDialog(self)
        dialog.exec()

def check_internet_connection():
    """Returns True if connected to the internet, False otherwise."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError:
        pass
    return False

if __name__ == "__main__":
    os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = "9999"
    
    app = QApplication(sys.argv)
    
    if not check_internet_connection():
        error_dialog = QMessageBox()
        error_dialog.setIcon(QMessageBox.Icon.Critical)
        error_dialog.setWindowTitle("Network Error")
        error_dialog.setText("No internet connection detected.")
        error_dialog.setInformativeText("This OSINT application requires an active internet connection to load maps and fetch news data. The application will now close.")
        error_dialog.exec()
        
        sys.exit(0)
    
    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpUserAgent("NewsCollectorApp/1.0 (OSINT Research Tool)")
    
    settings = profile.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    
    window = NewsCollectorApp()
    window.show()
    sys.exit(app.exec())