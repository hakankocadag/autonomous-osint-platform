import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, 
                             QHBoxLayout, QVBoxLayout, QPushButton, QTextEdit, QTextBrowser)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtCore import QUrl, QObject, pyqtSlot

# --- NEW IMPORTS FOR AUDIO ---
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

class MapBridge(QObject):
    def __init__(self, main_app):
        super().__init__()
        self.app = main_app

    @pyqtSlot(float, float)
    def receive_coordinates(self, lat, lng):
        msg = f"<font color='gray'><i>System: Map Clicked at Lat {lat:.4f}, Lng {lng:.4f}</i></font><br>"
        self.app.ai_output.append(msg)
        self.app.add_marker_to_map(lat, lng, "Selected Area")

class NewsCollectorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI News OSINT Collector")
        self.setGeometry(100, 100, 1200, 800)

        # --- AUDIO PLAYER SETUP ---
        self.audio_player = QMediaPlayer()
        self.audio_output_device = QAudioOutput()
        self.audio_player.setAudioOutput(self.audio_output_device)
        self.audio_output_device.setVolume(1.0) # Volume ranges from 0.0 to 1.0
        
        self.message_store = {} 
        self.message_counter = 0

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
        self.ai_output.setPlaceholderText("LLM initialization complete. Awaiting instructions...")
        
        input_layout = QHBoxLayout()
        
        self.ai_prompt = QTextEdit()
        self.ai_prompt.setPlaceholderText("Enter AI prompt here...")
        self.ai_prompt.setMaximumHeight(60)
        
        self.send_button = QPushButton("Send")
        self.send_button.setMinimumHeight(60)
        self.send_button.clicked.connect(self.handle_prompt)
        
        input_layout.addWidget(self.ai_prompt)
        input_layout.addWidget(self.send_button)
        
        self.action_button = QPushButton("Scan Area for News")
        self.action_button.clicked.connect(self.start_scan)

        right_panel.addWidget(self.ai_output)
        right_panel.addLayout(input_layout)
        right_panel.addWidget(self.action_button)

        main_layout.addLayout(right_panel, stretch=1)

    def handle_prompt(self):
        user_text = self.ai_prompt.toPlainText().strip()
        if not user_text:
            return

        self.ai_output.append(f"<b>You:</b> {user_text}<br>")
        self.ai_prompt.clear()

        simulated_response = "I have received your prompt. Currently, I am running in simulation mode. Once the backend is connected, I will analyze this against the OSINT database."
        
        self.message_counter += 1
        msg_id = str(self.message_counter)
        self.message_store[msg_id] = simulated_response

        # Kept the link format exactly the same
        read_link = f"&nbsp;<a href='tts:{msg_id}' style='text-decoration:none;'>[▶ Play Briefing]</a>"
        self.ai_output.append(f"<font color='#0055ff'><b>AI Agent:</b></font> {simulated_response}{read_link}<br><br>")

        scrollbar = self.ai_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def handle_link_click(self, url: QUrl):
        """Catches the link click and plays the MP3 file"""
        if url.scheme() == "tts":
            # Safely build the path to your audio file
            audio_path = os.path.abspath(os.path.join("voice", "briefing.mp3"))
            
            # Check if the file actually exists to prevent crashes
            if os.path.exists(audio_path):
                # Load the local file into the player and start it
                self.audio_player.setSource(QUrl.fromLocalFile(audio_path))
                self.audio_player.play()
            else:
                self.ai_output.append(f"<font color='red'><i>System Error: Audio file not found at {audio_path}</i></font><br>")

    def add_marker_to_map(self, lat, lng, text):
        js_code = f"addMarker({lat}, {lng}, '{text}');"
        self.map_view.page().runJavaScript(js_code)

    def start_scan(self):
        self.ai_output.append("<font color='orange'><b>System:</b> Scanning news - simulated API call...</font><br>")
        self.add_marker_to_map(38.72, 35.48, "Simulated News Alert")

if __name__ == "__main__":
    os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = "9999"
    
    app = QApplication(sys.argv)
    
    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpUserAgent("NewsCollectorApp/1.0 (OSINT Research Tool)")
    
    settings = profile.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    
    window = NewsCollectorApp()
    window.show()
    sys.exit(app.exec())