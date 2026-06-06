#  OmniSense: AI-Powered Autonomous OSINT Platform

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![PyQt6](https://img.shields.io/badge/PyQt6-UI-green)
![Playwright](https://img.shields.io/badge/Playwright-Scraping-red)
![PyTorch](https://img.shields.io/badge/PyTorch-Local_AI-orange)

**OmniSense** is an advanced software engineering project designed to act as an autonomous intelligence analyst. It automates the extraction, processing, analysis, and visualization of intelligence from publicly available digital sources. 

Built using advanced Python concepts such as **Asynchronous programming (asyncio)**, **Object-Oriented Design**, and **Custom AI architectures (PyTorch)**, it combines real-time data scraping with state-of-the-art NLP pipelines and an interactive desktop application.

---
<img width="1279" height="764" alt="Ekran görüntüsü 2026-06-06 134144" src="https://github.com/user-attachments/assets/91957ab4-504d-411e-990f-c6ab027118db" />


---

##  Core Features & Workflow

### 1.  Autonomous Data Collection (Agent Swarm)
- **Concurrent Web-Scraping Agents:** Gathers raw data based on user queries (e.g., geopolitical events, market trends) or cybersecurity targets.
- **Dual-Engine Scraping:** Uses `aiohttp` for fast static scraping and `Playwright` via `Crawlee` for dynamic, JavaScript-heavy sites.
- **Live Telemetry:** Streams real-time updates directly to the UI, providing a live tree-view of discovered articles and scanning targets.

### 2.  NLP Processing Pipeline
- Follows **SOLID principles** and the **Chain of Responsibility** pattern.
- Cleans and filters noisy HTML data, extracts meaningful signals, and performs text normalization using `spaCy` and `BeautifulSoup`.
- Operates asynchronously utilizing `asyncio.Queue` for fully non-blocking data consumption.

### 3.  AI Intelligence & Reconnaissance
- **Routing:** Dynamically routes processed data using the Strategy Design Pattern.
- **Inference Engines:** Uses a locally fine-tuned PyTorch LLM (e.g., QWEN / LLaMA) optimized with Quantization and FlashAttention, OR falls back to Cloud AI APIs (like Google Gemini).
- **Cybersecurity Recon:** Built-in module to generate automated cybersecurity attack surface and reconnaissance reports.

### 4.  Interactive UI & Output Delivery
- **Real-Time Terminal:** Built with **PyQt6**, the UI features a beautiful terminal-like streaming output with Dark/Light mode support and dynamic color styling.
- **Geospatial Mapping:** Integrates an interactive Folium map to plot geopolitical events or IP locations visually.
- **Audio Briefings:** Converts complex AI-generated text reports into professional-grade audio briefings using `edge-tts` and plays them directly inside the app.

---
<img width="1279" height="764" alt="Ekran görüntüsü 2026-06-06 134004" src="https://github.com/user-attachments/assets/0b17a167-5418-4320-b61a-b479ca322555" />

---

##  Team Architecture & Technical Specifications

| Member | Role & Responsibilities | Core Technologies |
| :--- | :--- | :--- |
| **Hakan Kocadağ** | **Autonomous Agent Swarm & Core Orchestration**<br>Built the `asyncio` swarm, connection pooling, proxy rotation, and producer-consumer data flow architectures. | `asyncio`, `Playwright`, `Crawlee` |
| **Ali Mojarrad Almanabad** | **Local AI Engineering**<br>Implemented LLM integration in pure PyTorch. Handles LoRA/PEFT fine-tuning, KV caching, and generating structured JSON/Markdown reports. | `PyTorch`, `LLaMA/Qwen` |
| **Yiğithan Karabel** | **UI & Cloud Integration**<br>Built the async API layers, handled Pydantic validations, Cloud AI integrations, and the core asynchronous desktop/UI architecture. | `PyQt6`, `FastAPI`, `Pydantic` |
| **Kenan Akyürek** | **NLP Pipeline & Audio Processing**<br>Constructed the Chain of Responsibility NLP filters, semantic text cleaning, and the high-fidelity Edge-TTS audio reporting pipeline. | `spaCy`, `BS4`, `edge-tts` |

---

##  Project Structure

```text
autonomous-osint-platform/
├── main.py                 # Core PyQt6 application and UI orchestrator
├── map.html                # Folium integration map asset
├── icon.jpg                # Application Favicon (OmniSense Eye)
├── nlp/                    
│   └── test_swarm.py       # Async scraper swarm and NLP extraction pipeline
├── local-ai/               # Local PyTorch models and inference scripts
├── ai-tts/                 # Text-To-Speech worker and audio processing logic
└── voice/                  # Exported audio briefing files
```

---

##  Standard Operating Procedures (SOPs)

### 3.1 Project Management (Trello Kanban)
- **Card Naming Format:** `[Name: DD.MM.YYYY] Task Description`
- **WIP Limit:** Max 2 tasks "In Progress" per person.
- **AI Prompt Log:** Store useful prompts. One prompt per card.

### 3.2 Version Control (Git Flow)
- **Rules:** No direct commits to `main`. The `main` branch must always remain stable.
- **Branching:**
  - Feature: `feature/description-name`
  - Bugfix: `fix/description-name`
- **Pull Requests:** Required for merging into `dev`. Must be reviewed by at least one teammate.
- **Dependencies:** Maintain a single `requirements.txt`.

### 3.3 AI-Assisted Development Guidelines
1. **Provide Context:** Always include relevant architecture details in prompts.
2. **Generate Small Components:** Prefer single classes or functions. Avoid massive auto-generated files.
3. **Enforce Code Quality:** Always request Type hints and Google-style docstrings.
4. **Review Everything:** Never blindly trust AI-generated code. Carefully inspect async logic, PyTorch operations, and External APIs.

---
<img width="1279" height="764" alt="Ekran görüntüsü 2026-06-06 133941" src="https://github.com/user-attachments/assets/918e7d09-c12b-42f0-a4f2-213d865ac282" />

---

##  Installation & Usage

1. **Clone the Repository:**
   ```bash
   git clone https://github.com/hakankocadag/autonomous-osint-platform.git
   cd autonomous-osint-platform
   ```
2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   playwright install
   ```
3. **Run OmniSense:**
   ```bash
   python main.py
   ```

*Note: Ensure you configure your API keys via the `Configuration > Set API Key` menu inside the application for Cloud AI capabilities, or ensure you have enough VRAM to run the local PyTorch models inside the `local-ai` module.*
