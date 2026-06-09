import sys
import os
import socket
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, 
                             QHBoxLayout, QVBoxLayout, QPushButton, QTextEdit, QTextBrowser, 
                             QMessageBox, QDialog, QLabel, QLineEdit, QRadioButton, QButtonGroup,
                             QGraphicsDropShadowEffect, QCheckBox, QScrollArea)
from PyQt6.QtGui import QAction, QTextCursor, QTextBlockFormat, QTextCharFormat, QColor, QFont, QPalette, QActionGroup, QIcon
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QUrl, QObject, pyqtSlot, Qt, pyqtSignal, QThread, QTimer

from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
from qwen_agent import QwenAgentThread
import subprocess
import json
import re
from dotenv import load_dotenv

load_dotenv()

class NewsSourceDialog(QDialog):
    def __init__(self, parent=None, current_choice="all"):
        super().__init__(parent)
        self.setWindowTitle("Select Sources")
        self.setMinimumSize(400, 500)
        self.current_choice = current_choice
        self.selected_choice = current_choice
        
        layout = QVBoxLayout(self)
        
        info_label = QLabel("Select the news agencies you want to scan:")
        layout.addWidget(info_label)
        
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(scroll_content)
        
        self.checkboxes = []
        
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            nlp_dir = os.path.join(base_dir, "nlp")
            sys.path.insert(0, nlp_dir)
            from test_swarm import SOURCES
            sys.path.pop(0)
            
            selected_indices = []
            if self.current_choice != "all":
                selected_indices = [int(x.strip()) - 1 for x in self.current_choice.split(",") if x.strip().isdigit()]
                
            for i, source in enumerate(SOURCES):
                cb = QCheckBox(f"{source['name']} ({source['category']})")
                if self.current_choice == "all" or i in selected_indices:
                    cb.setChecked(True)
                self.checkboxes.append((i, cb))
                self.scroll_layout.addWidget(cb)
        except Exception as e:
            err_label = QLabel(f"Error loading sources: {e}")
            self.scroll_layout.addWidget(err_label)
            
        self.scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)
        
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self.select_all)
        deselect_all_btn = QPushButton("Deselect All")
        deselect_all_btn.clicked.connect(self.deselect_all)
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(deselect_all_btn)
        layout.addLayout(btn_layout)
        
        btn_box = QHBoxLayout()
        save_btn = QPushButton("Save Selection")
        save_btn.clicked.connect(self.save_selection)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_box.addStretch()
        btn_box.addWidget(save_btn)
        btn_box.addWidget(cancel_btn)
        layout.addLayout(btn_box)
        
    def select_all(self):
        for _, cb in self.checkboxes:
            cb.setChecked(True)
            
    def deselect_all(self):
        for _, cb in self.checkboxes:
            cb.setChecked(False)
            
    def save_selection(self):
        selected = [str(i + 1) for i, cb in self.checkboxes if cb.isChecked()]
        if len(selected) == len(self.checkboxes) or not selected:
            self.selected_choice = "all"
        else:
            self.selected_choice = ",".join(selected)
        self.accept()

class ReconWorker(QThread):
    finished = pyqtSignal(str, object)
    error = pyqtSignal(str)

    def __init__(self, local_ai_dir):
        super().__init__()
        self.local_ai_dir = local_ai_dir

    def run(self):
        try:
            result = subprocess.run([sys.executable, "run.py"], cwd=self.local_ai_dir, capture_output=True, text=True, encoding='utf-8', errors='replace')
            
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

    def __init__(self, nlp_dir, keyword, hours, choice="all"):
        super().__init__()
        self.nlp_dir = nlp_dir
        self.keyword = keyword
        self.hours = hours
        self.choice = choice

    def run(self):
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", "test_swarm.py", "--keyword", str(self.keyword), "--hours", str(self.hours), "--choice", str(self.choice)],
                cwd=self.nlp_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, encoding='utf-8', errors='replace'
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
                elif "🔎 Scanning:" in clean_line:
                    match = re.search(r"Scanning:\s*(.*)", clean_line)
                    if match:
                        self.progress.emit(f"Scanning source: {match.group(1)}")
                elif "📄 Found:" in clean_line:
                    match = re.search(r"Found:\s*(.*)", clean_line)
                    if match:
                        self.progress.emit(f"FOUND_ARTICLE:{match.group(1)}")
                elif "✅  Pushed" in clean_line:
                    match = re.search(r"Pushed \([^)]+\):\s*(.*)", clean_line)
                    if match:
                        title = match.group(1).strip()
                        self.progress.emit(f"Scraping: {title}")
                    else:
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

    def __init__(self, data, provider, api_key, keyword=None):
        super().__init__()
        self.data = data
        self.provider = provider
        self.api_key = api_key
        self.keyword = keyword

    def run(self):
        try:
            import sys
            import os
            base_dir = os.path.dirname(os.path.abspath(__file__))
            ai_tts_dir = os.path.join(base_dir, "ai-tts")
            if ai_tts_dir not in sys.path:
                sys.path.append(ai_tts_dir)
                
            from report_service import generate_summary_report

            summary_data = generate_summary_report(self.data, self.provider, self.api_key, keyword=self.keyword)
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
            base_dir = os.path.dirname(os.path.abspath(__file__))
            ai_tts_dir = os.path.join(base_dir, "ai-tts")
            if ai_tts_dir not in sys.path:
                sys.path.append(ai_tts_dir)
                
            from tts_service import generate_audio_briefing
            
            os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
            
            temp_json = os.path.join(base_dir, "temp_tts_report.json")
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
        msg = f"<div style='color: #555555; font-weight: bold; margin-bottom: 10px;'>System: Map Clicked at Lat {lat:.4f}, Lng {lng:.4f}</div>"
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

class ZoomableTextBrowser(QTextBrowser):
    def wheelEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoomIn(1)
            else:
                self.zoomOut(1)
        else:
            super().wheelEvent(event)

class NewsCollectorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OmniSense")
        self.setGeometry(100, 100, 1200, 800)
        
        # Setup Window Icon
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(os.path.join(base_dir, "icon.jpg")):
            self.setWindowIcon(QIcon(os.path.join(base_dir, "icon.jpg")))
        elif os.path.exists(os.path.join(base_dir, "icon.png")):
            self.setWindowIcon(QIcon(os.path.join(base_dir, "icon.png")))

        # Menu Bar Setup
        menubar = self.menuBar()
        
        self.clock_label = QLabel(self)
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.clock_label.setContentsMargins(0, 0, 15, 0)
        self.clock_label.setMinimumWidth(150)
        menubar.setCornerWidget(self.clock_label, Qt.Corner.TopRightCorner)
        
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        self.update_clock()
        
        configuration_menu = menubar.addMenu("Configuration")
        
        set_api_key_action = QAction("Set API Key", self)
        set_api_key_action.triggered.connect(self.open_token_dialog)
        configuration_menu.addAction(set_api_key_action)
        
        news_sources_action = QAction("Select Sources", self)
        news_sources_action.triggered.connect(self.open_news_source_dialog)
        configuration_menu.addAction(news_sources_action)
        
        self.news_choice = "all"

        theme_menu = menubar.addMenu("Theme")
        
        self.action_theme_system = QAction("System Default", self)
        self.action_theme_system.setCheckable(True)
        self.action_theme_system.triggered.connect(lambda: self.set_theme_preference("System"))
        
        self.action_theme_light = QAction("Light", self)
        self.action_theme_light.setCheckable(True)
        self.action_theme_light.triggered.connect(lambda: self.set_theme_preference("Light"))
        
        self.action_theme_dark = QAction("Dark", self)
        self.action_theme_dark.setCheckable(True)
        self.action_theme_dark.triggered.connect(lambda: self.set_theme_preference("Dark"))
        
        theme_group = QActionGroup(self)
        theme_group.addAction(self.action_theme_system)
        theme_group.addAction(self.action_theme_light)
        theme_group.addAction(self.action_theme_dark)
        
        theme_menu.addAction(self.action_theme_system)
        theme_menu.addAction(self.action_theme_light)
        theme_menu.addAction(self.action_theme_dark)

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

        # Map Setup (Full screen)
        self.map_view = QWebEngineView(central_widget)
        
        self.channel = QWebChannel()
        self.bridge = MapBridge(self)
        self.channel.registerObject("backend", self.bridge)
        self.map_view.page().setWebChannel(self.channel)

        html_path = os.path.abspath("map.html")
        self.map_view.setUrl(QUrl.fromLocalFile(html_path)) 
        
        map_layout = QVBoxLayout(central_widget)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.addWidget(self.map_view)

        # Floating Output Panel (Top Right)
        self.output_container = QWidget(central_widget)
        self.output_container.setObjectName("OutputContainer")
        
        output_shadow = QGraphicsDropShadowEffect()
        output_shadow.setBlurRadius(20)
        output_shadow.setXOffset(0)
        output_shadow.setYOffset(5)
        output_shadow.setColor(QColor(0, 0, 0, 30))
        self.output_container.setGraphicsEffect(output_shadow)

        output_layout = QVBoxLayout(self.output_container)
        output_layout.setContentsMargins(15, 15, 15, 15)
        
        self.ai_output = ZoomableTextBrowser() 
        self.ai_output.setOpenLinks(False) 
        self.ai_output.anchorClicked.connect(self.handle_link_click) 
        self.ai_output.setPlaceholderText("Loading Qwen model in background...")
        output_layout.addWidget(self.ai_output)

        # Floating Input Panel (Bottom Right)
        self.input_container = QWidget(central_widget)
        self.input_container.setStyleSheet("""
            QWidget#InputContainer {
                background-color: transparent;
                border: none;
            }
        """)
        self.input_container.setObjectName("InputContainer")

        input_layout = QVBoxLayout(self.input_container)
        input_layout.setContentsMargins(15, 15, 15, 15)
        input_layout.setSpacing(10)
        
        top_input_layout = QHBoxLayout()
        top_input_layout.setSpacing(10)
        top_input_layout.setContentsMargins(0, 0, 0, 0)
        
        self.ai_prompt = PromptTextEdit()
        self.ai_prompt.send_requested.connect(self.handle_prompt)
        self.ai_prompt.setPlaceholderText("Enter message...")
        self.ai_prompt.setMaximumHeight(60)
        
        self.send_button = QPushButton("Send")
        self.send_button.setMinimumHeight(60)
        self.send_button.clicked.connect(self.handle_prompt)
        
        top_input_layout.addWidget(self.ai_prompt)
        top_input_layout.addWidget(self.send_button)
        
        self.cyber_security_button = QPushButton("Cybersecurity Scan")
        self.cyber_security_button.setMinimumHeight(40)
        self.cyber_security_button.clicked.connect(self.start_cyber_security_scan)
        
        input_layout.addLayout(top_input_layout)
        input_layout.addWidget(self.cyber_security_button)
        
        self.load_theme_preference()
        self.apply_theme()
        app = QApplication.instance()
        if hasattr(app.styleHints(), 'colorSchemeChanged'):
            app.styleHints().colorSchemeChanged.connect(self.apply_theme)

    def update_clock(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        self.clock_label.setText(now.strftime("UTC %H:%M:%S"))

    def load_theme_preference(self):
        self.theme_preference = "System"
        try:
            if os.path.exists("ui_settings.json"):
                with open("ui_settings.json", "r") as f:
                    self.theme_preference = json.load(f).get("theme", "System")
        except:
            pass
        
        if self.theme_preference == "Light":
            self.action_theme_light.setChecked(True)
        elif self.theme_preference == "Dark":
            self.action_theme_dark.setChecked(True)
        else:
            self.action_theme_system.setChecked(True)

    def set_theme_preference(self, theme):
        self.theme_preference = theme
        try:
            with open("ui_settings.json", "w") as f:
                json.dump({"theme": theme}, f)
        except:
            pass
        self.apply_theme()

    def apply_theme(self):
        app = QApplication.instance()
        
        if getattr(self, 'theme_preference', 'System') == "Light":
            is_dark = False
        elif getattr(self, 'theme_preference', 'System') == "Dark":
            is_dark = True
        else:
            is_dark = False
            if hasattr(app.styleHints(), 'colorScheme'):
                is_dark = (app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
            else:
                is_dark = app.palette().color(QPalette.ColorRole.WindowText).lightness() > 128
            
        if is_dark:
            bg_color = "rgba(30, 30, 30, 0.85)"
            border_color = "rgba(80, 80, 80, 0.5)"
            text_color = "#E0E0E0"
            input_bg = "rgba(45, 45, 45, 0.9)"
            input_border = "rgba(100, 100, 100, 0.6)"
            input_focus = "rgba(150, 150, 150, 0.8)"
            btn_bg = "rgba(60, 60, 60, 0.95)"
            btn_text = "#E0E0E0"
            btn_hover = "rgba(80, 80, 80, 1)"
            btn_pressed = "rgba(45, 45, 45, 1)"
            
            scan_btn_bg = "rgba(45, 45, 45, 0.9)"
            scan_btn_hover = "rgba(60, 60, 60, 1)"
            scan_btn_pressed = "rgba(80, 80, 80, 0.9)"
        else:
            bg_color = "rgba(255, 255, 255, 0.85)"
            border_color = "rgba(255, 255, 255, 0.5)"
            text_color = "#3D3D3D"
            input_bg = "rgba(255, 255, 255, 0.9)"
            input_border = "rgba(200, 200, 200, 0.6)"
            input_focus = "rgba(150, 150, 150, 0.8)"
            btn_bg = "rgba(240, 240, 240, 0.95)"
            btn_text = "#2F2F2F"
            btn_hover = "rgba(255, 255, 255, 1)"
            btn_pressed = "rgba(220, 220, 220, 1)"
            
            scan_btn_bg = "rgba(255, 255, 255, 0.9)"
            scan_btn_hover = "#FFFFFF"
            scan_btn_pressed = "rgba(230, 230, 230, 0.9)"

        self.setStyleSheet(f"""
            QMenuBar {{
                background-color: {bg_color};
                color: {text_color};
                border-bottom: 1px solid {border_color};
            }}
            QMenuBar::item {{
                background-color: transparent;
                padding: 4px 10px;
            }}
            QMenuBar::item:selected {{
                background-color: {btn_hover};
            }}
            QMenu {{
                background-color: {bg_color};
                color: {text_color};
                border: 1px solid {border_color};
            }}
            QMenu::item:selected {{
                background-color: {btn_hover};
            }}
        """)
        
        if hasattr(self, 'clock_label'):
            self.clock_label.setStyleSheet(f"color: {text_color}; font-weight: 600; font-family: 'Inter', '-apple-system', 'Segoe UI', 'Roboto', sans-serif; font-size: 13px;")


        self.output_container.setStyleSheet(f"""
            QWidget#OutputContainer {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 12px;
            }}
        """)
        
        self.ai_output.setStyleSheet(f"""
            QTextBrowser {{
                background-color: transparent;
                color: {text_color};
                border: none;
                font-family: 'Inter', '-apple-system', 'Segoe UI', 'Roboto', sans-serif;
                font-size: 14px;
                line-height: 1.6;
            }}
        """)
        
        self.ai_prompt.setStyleSheet(f"""
            QTextEdit {{
                background-color: {input_bg};
                color: {text_color};
                border: 1px solid {input_border};
                border-radius: 8px;
                padding: 10px;
                font-family: 'Inter', '-apple-system', 'Segoe UI', 'Roboto', sans-serif;
                font-size: 14px;
            }}
            QTextEdit:focus {{
                border: 1px solid {input_focus};
                background-color: {input_bg};
            }}
        """)
        

        c = self.get_theme_colors()
        doc_css = f'''
        .sys {{ color: {c['sys']}; }}
        .header {{ color: {c['header']}; font-weight: 600; }}
        .text {{ color: {c['text']}; }}
        .color-you {{ color: {c['you']}; font-weight: 600; }}
        .color-qwen {{ color: {c['qwen']}; font-weight: 600; }}
        .color-system {{ color: {c['system']}; font-weight: 600; }}
        .color-collector {{ color: {c['collector']}; font-weight: 600; }}
        hr {{ border: 0; border-top: 1px solid {c['divider']}; margin-bottom: 15px; }}
        a.play-btn {{ color: {c['text']}; background-color: {c['btn_bg']}; border: 1px solid {c['divider']}; text-decoration: none; font-weight: 500; padding: 6px 12px; border-radius: 6px; }}
        table td.sys-td {{ color: {c['sys']}; width: 160px; padding: 4px; vertical-align: top; }}
        table td.text-td {{ color: {c['text']}; padding: 4px; }}
        '''
        self.ai_output.document().setDefaultStyleSheet(doc_css)
        
        # --- Dynamically re-color existing HTML history ---
        old_html = self.ai_output.toHtml()
        
        if is_dark:
            old_html = old_html.replace("#2d2d2d", "#D1D5DB").replace("#2D2D2D", "#D1D5DB")
            old_html = old_html.replace("#6b6b6b", "#8E8EA0").replace("#6B6B6B", "#8E8EA0")
            old_html = old_html.replace("#000000", "#FFFFFF")
            old_html = old_html.replace("#b8860b", "#FFD700").replace("#B8860B", "#FFD700")
            old_html = old_html.replace("#dc143c", "#FF6347").replace("#DC143C", "#FF6347")
            old_html = old_html.replace("rgba(0, 0, 0, 0.1)", "rgba(255, 255, 255, 0.1)")
            old_html = old_html.replace("rgba(0, 0, 0, 0.05)", "rgba(255, 255, 255, 0.1)")
        else:
            old_html = old_html.replace("#d1d5db", "#2D2D2D").replace("#D1D5DB", "#2D2D2D")
            old_html = old_html.replace("#8e8ea0", "#6B6B6B").replace("#8E8EA0", "#6B6B6B")
            old_html = old_html.replace("#ffffff", "#000000").replace("#FFFFFF", "#000000")
            old_html = old_html.replace("#ffd700", "#B8860B").replace("#FFD700", "#B8860B")
            old_html = old_html.replace("#ff6347", "#DC143C").replace("#FF6347", "#DC143C")
            old_html = old_html.replace("rgba(255, 255, 255, 0.1)", "rgba(0, 0, 0, 0.1)")
            
        scrollbar = self.ai_output.verticalScrollBar()
        scroll_val = scrollbar.value()
        
        self.ai_output.setHtml(old_html)
        scrollbar.setValue(scroll_val)
        
        self.send_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_bg};
                color: {btn_text};
                border: 1px solid {input_border};
                border-radius: 8px;
                padding: 0px 20px;
                font-family: 'Inter', '-apple-system', 'Segoe UI', 'Roboto', sans-serif;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; border: 1px solid {input_focus}; }}
            QPushButton:pressed {{ background-color: {btn_pressed}; }}
        """)
        
        self.cyber_security_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {scan_btn_bg};
                color: {text_color};
                border: 1px solid {input_border};
                border-radius: 8px;
                padding: 10px;
                font-family: 'Inter', '-apple-system', 'Segoe UI', 'Roboto', sans-serif;
                font-size: 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {scan_btn_hover}; border: 1px solid {input_focus}; }}
            QPushButton:pressed {{ background-color: {scan_btn_pressed}; }}
        """)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        
        margin_right = 20
        margin_top = 20
        margin_bottom = 50
        container_width = 450
        input_height = 140
        output_height = self.height() - margin_top - margin_bottom - input_height - 15
        
        if hasattr(self, 'output_container'):
            self.output_container.setGeometry(
                self.width() - container_width - margin_right,
                margin_top,
                container_width,
                output_height
            )
            
        if hasattr(self, 'input_container'):
            self.input_container.setGeometry(
                self.width() - container_width - margin_right,
                self.height() - margin_bottom - input_height,
                container_width,
                input_height
            )

    def _reset_block_format(self):
        cursor = self.ai_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        block_fmt = QTextBlockFormat()
        block_fmt.setBackground(QColor(0, 0, 0, 0))
        block_fmt.setTopMargin(0)
        block_fmt.setBottomMargin(0)
        block_fmt.setLeftMargin(0)
        block_fmt.setRightMargin(0)
        cursor.insertBlock(block_fmt)
        self.ai_output.setTextCursor(cursor)

    def get_theme_colors(self):
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QPalette
        from PyQt6.QtCore import Qt
        app = QApplication.instance()
        if getattr(self, 'theme_preference', 'System') == "Light":
            is_dark = False
        elif getattr(self, 'theme_preference', 'System') == "Dark":
            is_dark = True
        else:
            is_dark = False
            if hasattr(app.styleHints(), 'colorScheme'):
                is_dark = (app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
            else:
                is_dark = app.palette().color(QPalette.ColorRole.WindowText).lightness() > 128
                
        if is_dark:
            return {"text": "#D1D5DB", "sys": "#8E8EA0", "header": "#FFFFFF", "divider": "rgba(255, 255, 255, 0.1)", "btn_bg": "rgba(255, 255, 255, 0.1)", "you": "#FFFFFF", "qwen": "#FFD700", "system": "#FF6347", "collector": "#4DA6FF"}
        else:
            return {"text": "#2D2D2D", "sys": "#6B6B6B", "header": "#000000", "divider": "rgba(0, 0, 0, 0.1)", "btn_bg": "rgba(0, 0, 0, 0.05)", "you": "#000000", "qwen": "#B8860B", "system": "#DC143C", "collector": "#0066CC"}

    def _set_ai_block_format(self, title="Qwen Agent:"):
        cursor = self.ai_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        block_fmt = QTextBlockFormat()
        
        block_fmt.setBackground(QColor(0, 0, 0, 0))
        block_fmt.setTopMargin(15)
        block_fmt.setBottomMargin(15)
        block_fmt.setLeftMargin(0)
        block_fmt.setRightMargin(15)
        cursor.insertBlock(block_fmt)
        
        self.ai_output.setTextCursor(cursor)
        self.ai_output.insertHtml(f"<b class='color-qwen'>{title} </b>")
        
        cursor = self.ai_output.textCursor()
        char_fmt = QTextCharFormat()
        char_fmt.setFontWeight(QFont.Weight.Normal)
        cursor.setCharFormat(char_fmt)
        self.ai_output.setTextCursor(cursor)

    def handle_prompt(self):
        user_text = self.ai_prompt.toPlainText().strip()
        if not user_text:
            return

        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='text' style='margin-bottom:15px; padding: 10px 15px;'><b class='color-you'>You:</b><br><span>{user_text}</span></div>")
        self.ai_prompt.clear()

        self._set_ai_block_format("Qwen Agent:")
        self.qwen_agent.generate_async(user_text)

        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def start_cyber_security_scan(self):
        user_text = self.ai_prompt.toPlainText().strip()
        if not user_text:
            return

        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='text' style='margin-bottom:15px; padding: 10px 15px;'><b class='color-you'>You (Cyber Scan):</b><br><span>{user_text}</span></div>")
        self.ai_prompt.clear()

        self._set_ai_block_format("System:")
        
        cursor = self.ai_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("Generating scan plan...\n")
        self.ai_output.setTextCursor(cursor)

        self.qwen_agent.generate_async(user_text, is_recon=True)

        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_recon_ready(self, response):
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 5px; padding: 0 15px;'><b class='color-system'>System:</b> Scan plan generated!</div>")
        
        clean = response.strip()
        import re
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if match:
            clean = match.group(0)
        try:
            import json, os
            plan = json.loads(clean)
            
            base_dir = os.path.dirname(os.path.abspath(__file__))
            local_ai_dir = os.path.join(base_dir, "local-ai")
            recon_file = os.path.join(local_ai_dir, "recon_output.json")
            
            with open(recon_file, "w") as f:
                json.dump(plan, f, indent=4)
                
            self._reset_block_format()
            self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System:</b> Running reconnaissance tools... This may take a while.</div>")
            scrollbar = self.ai_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            
            self.recon_worker = ReconWorker(local_ai_dir)
            self.recon_worker.finished.connect(self.on_recon_finished)
            self.recon_worker.error.connect(self.on_recon_error)
            self.recon_worker.start()

        except json.JSONDecodeError as e:
            self._reset_block_format()
            self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System Error:</b> Failed to parse JSON plan: {e}</div><pre class='text' style='padding: 0 15px;'>{response}</pre>")
            scrollbar = self.ai_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def on_recon_finished(self, output, report_data):
        self._reset_block_format()
        c = self.get_theme_colors()
        if report_data:
            html = self.format_report_html(report_data, c)
            self.ai_output.insertHtml(html)
        else:
            self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System:</b> Reconnaissance complete:</div><pre class='text' style='padding: 0 15px;'>{output}</pre>")
        
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def format_report_html(self, data, c):
        meta = data.get("meta", {})
        results = data.get("results", {})
        
        target = meta.get("target", "Unknown")
        target_type = meta.get("target_type", "Unknown")
        
        html = f"""
        <div class="text" style="padding: 15px; margin-bottom: 15px;">
            <hr>
            <h2 class="header" style="margin-top: 10px; margin-bottom: 10px;">CYBERSECURITY REPORT: {target} ({target_type.upper()})</h2>
            <hr>
        """
        
        for tool, result in results.items():
            if "error" in result:
                html += f"<div style='margin-bottom: 10px;'><b style='class='header'>[+] {tool.upper()}</b> <span style='class='sys'>Failed: {result['error']}</span></div>"
                continue
            
            html += f"<div style='margin-bottom: 5px;'><b style='class='header'>[+] {tool.upper()}</b></div>"
            html += "<table style='margin-left: 15px; margin-bottom: 15px; border-collapse: collapse; width: 95%;' class='text'>"
            
            if tool == "whois" and "parsed" in result:
                for k, v in result["parsed"].items():
                    val = "<br>".join(v) if isinstance(v, list) else v
                    html += f"<tr><td class='sys' style='width: 160px; padding: 4px; vertical-align: top;'>{k.replace('_', ' ').title()}</td><td class='text' style='padding: 4px;'>{val}</td></tr>"
            
            elif tool == "dig":
                for rec_type, records in result.items():
                    val = "<br>".join(records)
                    html += f"<tr><td class='sys' style='width: 160px; padding: 4px; vertical-align: top;'>{rec_type} Records</td><td class='text' style='padding: 4px;'>{val}</td></tr>"
                    
            elif tool in ["ipinfo", "virustotal", "shodan"]:
                for k, v in result.items():
                    if k != "raw":
                        val = "<br>".join([str(x) for x in v]) if isinstance(v, list) else str(v)
                        html += f"<tr><td class='sys' style='width: 160px; padding: 4px; vertical-align: top;'>{k.replace('_', ' ').title()}</td><td class='text' style='padding: 4px;'>{val}</td></tr>"
            
            else:
                for k, v in result.items():
                    if k == "raw": continue
                    val = str(v).replace('\\n', '<br>')
                    html += f"<tr><td class='sys' style='width: 160px; padding: 4px; vertical-align: top;'>{k.replace('_', ' ').title()}</td><td class='text' style='padding: 4px;'>{val}</td></tr>"
            
            html += "</table>"
            
        html += "</div><br>"
        return html

    def on_recon_error(self, error):
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System Error:</b> Reconnaissance failed: {error}</div>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_model_loaded(self):
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 15px; padding: 0 15px;'><b class='color-system'>System:</b> Qwen model loaded successfully! Ready for instructions.</div>")
        self.ai_output.setPlaceholderText("Qwen model loaded. Awaiting instructions...")

    def on_token_ready(self, token):
        cursor = self.ai_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.ai_output.setTextCursor(cursor)
        self.ai_output.insertPlainText(token)
        
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
    def on_agent_response(self, response):
        clean = response.strip()
        import re, json
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                if data.get("intent") == "news" or "keyword" in data:
                    self.on_news_ready(response)
                    return
            except Exception:
                pass
                
        if clean.startswith("{") or clean.startswith("```"):
            cursor = self.ai_output.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.ai_output.setTextCursor(cursor)
            self.ai_output.insertPlainText(response)

        self.message_counter += 1
        msg_id = str(self.message_counter)
        self.message_store[msg_id] = response

        c = self.get_theme_colors()
        read_link = f"<div style='margin-top: 15px; margin-bottom: 5px; padding: 0 15px;'><a href='tts:{msg_id}' class='text' style='text-decoration: none; font-weight: 500; background-color: {c['btn_bg']}; padding: 6px 12px; border-radius: 6px; border: 1px solid {c['divider']};'>▶ Listen</a></div><br>"
        cursor = self.ai_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.ai_output.setTextCursor(cursor)
        self.ai_output.insertHtml(read_link)
        
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_agent_error(self, error_msg):
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 15px; padding: 0 15px;'><b style='font-weight: 600;'>Agent Error:</b> {error_msg}</div>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def handle_link_click(self, url):
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
                
            import hashlib, json, os
            content_str = json.dumps(data, sort_keys=True)
            content_hash = hashlib.md5(content_str.encode()).hexdigest()[:10]
                
            audio_path = os.path.abspath(os.path.join("voice", f"briefing_{msg_id}_{content_hash}.mp3"))
            os.makedirs("voice", exist_ok=True)
            
            if os.path.exists(audio_path):
                from PyQt6.QtCore import QUrl
                self.audio_player.setSource(QUrl.fromLocalFile(audio_path))
                self.audio_player.play()
            else:
                self._reset_block_format()
                c = self.get_theme_colors()
                self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'>System: Generating audio briefing for message {msg_id}...</div>")
                scrollbar = self.ai_output.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                
                self.tts_worker = TTSWorker(data, audio_path)
                self.tts_worker.finished.connect(self.on_tts_finished)
                self.tts_worker.error.connect(self.on_tts_error)
                self.tts_worker.start()

    def on_tts_finished(self, audio_path):
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'>System: Audio briefing ready. Playing...</div>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        from PyQt6.QtCore import QUrl
        self.audio_player.setSource(QUrl.fromLocalFile(audio_path))
        self.audio_player.play()

    def on_tts_error(self, error):
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System Error:</b> TTS generation failed: {error}</div>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def add_marker_to_map(self, lat, lng, text):
        js_code = f"addMarker({lat}, {lng}, '{text}');"
        self.map_view.page().runJavaScript(js_code)

    def open_news_source_dialog(self):
        dialog = NewsSourceDialog(self, getattr(self, 'news_choice', 'all'))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.news_choice = dialog.selected_choice

    def on_news_ready(self, response):
        clean = response.strip()
        import re, json, os
        match = re.search(r'\{.*\}', clean, re.DOTALL)
        if match:
            clean = match.group(0)
        try:
            plan = json.loads(clean)
            error = plan.get("error")
            c = self.get_theme_colors()
            if error:
                self._reset_block_format()
                self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b style='font-weight: 600;'>Agent Error:</b> {error}</div>")
                scrollbar = self.ai_output.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                return
            
            keyword = plan.get("keyword", "news")
            hours = plan.get("hours", 24)
            
            self._reset_block_format()
            self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System:</b> Request parsed. Topic: {keyword}, Hours: {hours}. Running collector...</div>")
            scrollbar = self.ai_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            
            base_dir = os.path.dirname(os.path.abspath(__file__))
            nlp_dir = os.path.join(base_dir, "nlp")
            
            self.news_worker = NewsWorker(nlp_dir, keyword, hours, getattr(self, 'news_choice', 'all'))
            self.news_worker.progress.connect(self.on_news_progress)
            self.news_worker.finished.connect(self.on_news_finished)
            self.news_worker.error.connect(self.on_news_error)
            self.news_worker.start()

        except json.JSONDecodeError as e:
            self._reset_block_format()
            c = self.get_theme_colors()
            self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System Error:</b> Failed to parse JSON plan: {e}</div><pre class='text' style='padding: 0 15px;'>{response}</pre>")
            scrollbar = self.ai_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def on_news_progress(self, msg):
        self._reset_block_format()
        c = self.get_theme_colors()
        if msg.startswith("FOUND_ARTICLE:"):
            title = msg.replace("FOUND_ARTICLE:", "", 1)
            self.ai_output.insertHtml(f"<div class='text' style='margin-bottom: 3px; padding: 0 15px; margin-left: 20px; color: {c['sys']}; font-size: 13px;'>└─ 📄 {title}</div>")
        else:
            self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 5px; padding: 0 15px;'><b class='color-collector'>Collector:</b> {msg}</div>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_news_finished(self, output, report_data):
        self._reset_block_format()
        c = self.get_theme_colors()
        html = f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System:</b> News collected successfully."
        if report_data:
            html += f"<br><br>Found {len(report_data)} articles.<br><br><b class='color-system'>System:</b> Summarizing news with AI..."
        html += "</div>"
        self.ai_output.insertHtml(html)
        if report_data:
            
            
            import os, json
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
                self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System Error:</b> {provider.capitalize()} API Key is missing. Please set it via Configuration > Set API Key.</div>")
                scrollbar = self.ai_output.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
                return
                
            kw = getattr(self.news_worker, 'keyword', None) if hasattr(self, 'news_worker') else None
            self.summary_worker = SummaryWorker(report_data, provider, api_key, keyword=kw)
            self.summary_worker.finished.connect(self.on_summary_finished)
            self.summary_worker.error.connect(self.on_summary_error)
            self.summary_worker.start()
        else:
            self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System Error:</b> No report data collected to summarize.</div>")
            
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_summary_finished(self, summary_data):
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System:</b> Summary generation complete.</div>")
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
        
        import re
        html_summary = re.sub(r'#+\s*(.*)', rf'<h3 style="color:{c["header"]}; margin-bottom: 5px; margin-top: 15px; font-weight: 600;">\1</h3><hr>', summary_text)
        html_summary = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', html_summary)
        html_summary = re.sub(r'<hr>\s+', '<hr>', html_summary)
        html_summary = html_summary.replace('\n', '<br>')
        
        sources = summary_data.get("sources", [])
        sources_html = ""
        if sources:
            sources_list = ", ".join(sources)
            sources_html = f"<div class='sys' style='margin-top: 20px; font-size: 0.9em; border-top: 1px solid {c['divider']}; padding-top: 10px;'><b>Sources:</b> {sources_list}</div>"

        html = f"""
        <div class='text' style="padding: 15px; margin-top: 10px; margin-bottom: 10px;">
            <hr style="margin-top: 5px; margin-bottom: 15px;">
            <h2 class=\'header\' style="margin-top: 0; margin-bottom: 10px;">OSINT REPORT</h2>
            <hr>
            {html_summary}
            {sources_html}
        </div>
        <div style='margin-top: 15px; margin-bottom: 25px; padding: 0 15px;'><a href='tts:{msg_id}' class='play-btn'>▶ Listen</a></div>
        """
        self._reset_block_format()
        self.ai_output.insertHtml(html)
        
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
            popup_text = popup_text.replace("'", "\\'")
            
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
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'>System Warning: Could not find map coordinates for location - {error_msg}</div>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_summary_error(self, error):
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System Error:</b> Summarization failed: {error}</div>")
        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_news_error(self, error):
        self._reset_block_format()
        c = self.get_theme_colors()
        self.ai_output.insertHtml(f"<div class='sys' style='margin-bottom: 10px; padding: 0 15px;'><b class='color-system'>System Error:</b> News collector failed: {error}</div>")
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