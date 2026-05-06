# OSINT Project 3

This project is an AI-powered OSINT system that collects, cleans, analyzes, and presents open-source intelligence data.

## Project Overview

The system processes collected OSINT data and prepares it for AI-based report generation. After the data is cleaned, the system can generate a structured intelligence report and convert the final report into an audio briefing.

## Main Features

- Data cleaning with NLP pipeline
- AI-powered intelligence report generation
- Structured JSON report output
- Text-to-Speech audio briefing
- Edge-TTS support for natural voice output
- gTTS fallback support
- Environment-based configuration

## Project Flow

```text
Raw Data
→ NLP Cleaning
→ Clean JSON
→ AI Intelligence Report
→ Final Briefing JSON
→ TTS Audio Briefing
→ briefing.mp3
```

## Main Files

```text
nlp.py               # Cleans and prepares raw text data
report_service.py    # Generates AI-powered intelligence report
tts_service.py       # Converts final report into audio briefing
test_swarm.py        # Main pipeline / test runner
requirements.txt     # Required Python packages
.env.example         # Example environment configuration
```

## Environment Setup

Create a `.env` file based on `.env.example`.

Example:

```env
AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_key_here
GEMINI_MODEL=gemini-flash-latest

ENABLE_PROVIDER_FALLBACK=false
FALLBACK_PROVIDERS=openai,claude

ENABLE_TTS=true
TTS_PROVIDER=edge
TTS_OUTPUT_FILE=briefing.mp3
TTS_VOICE=en-US-GuyNeural
TTS_RATE=-5%
TTS_PITCH=-2Hz
TTS_VOLUME=+0%
```

## Installation

Install the required packages:

```bash
pip install -r requirements.txt
```

## Usage

Run the main pipeline:

```bash
python test_swarm.py
```

Or test the TTS service separately:

```bash
python tts_service.py
```

## Audio Briefing

The TTS module reads the final intelligence report JSON and converts it into an MP3 audio briefing.

The default output file is:

```text
briefing.mp3
```

The project uses `edge-tts` for a more natural neural voice. If needed, `gTTS` can be used as a fallback.

## Security Note

The `.env` file is not included in this repository because it may contain private API keys.

Use `.env.example` as a template and create your own `.env` file locally.