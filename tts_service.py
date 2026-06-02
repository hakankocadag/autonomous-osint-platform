import json
import logging
import os
import requests
import re

logger = logging.getLogger(__name__)

def build_spoken_briefing(report: dict) -> str:
    category = report.get("category", "Unknown Category")
    locations_list = report.get("locations", [])
    if locations_list:
        loc_names = [loc.get("location", "Unknown") for loc in locations_list if loc.get("location")]
        location = ", ".join(loc_names) if loc_names else "Multiple Locations"
    else:
        location = report.get("location", "Unknown Location")
    confidence_level = report.get("confidence_level", "Unknown")
    
    key_judgments = report.get("key_judgments", [])
    summary = report.get("summary", "")
    
    # Strip "Sir," or "Sir, " from the beginning of the summary
    if summary.lower().startswith("sir,"):
        summary = summary[4:].strip()
    
    # Build judgments string using First, Second, Third
    judgments_text = ""
    if key_judgments:
        # Take only first 3
        top_judgments = key_judgments[:3]
        prefix_words = ["First,", "Second,", "Third,"]
        judgment_sentences = []
        for i, judgment in enumerate(top_judgments):
            # Clean up the judgment text
            clean_judgment = judgment.strip()
            if not clean_judgment.endswith("."):
                clean_judgment += "."
            judgment_sentences.append(f"{prefix_words[i]} {clean_judgment}")
        
        judgments_text = " ".join(judgment_sentences)
    else:
        judgments_text = "No specific judgments were made."
        
    script = (
        # f"This report focuses on {category}, with relevance to {location}. Confidence level is {confidence_level}.\n\n"
        f"This report focuses on {category}, with relevance to {location}.\n\n"
        f"Key judgments are as follows. {judgments_text}\n\n"
        f"{summary}\n\n"
        f"End of briefing."
    )
    
    # Pronunciation and natural pause improvements
    # Replace AI with artificial intelligence
    script = re.sub(r'\bAI\b', 'artificial intelligence', script)
    # Remove URLs
    script = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', script)
    # Remove Markdown headers and bold/italic formatting
    script = re.sub(r'[#*]', '', script)
    
    # Condense multiple spaces (leaving newlines intact)
    script = re.sub(r' +', ' ', script).strip()
    
    return script

def generate_audio_briefing(report_json_path: str, output_file: str = None) -> None:
    """
    Reads the finalized AI intelligence report JSON and converts it into a natural spoken briefing.
    Does not crash the pipeline if TTS fails.
    """
    try:
        # 1. Read the final JSON report
        if not os.path.exists(report_json_path):
            logger.error(f"TTS Error: Report file {report_json_path} not found.")
            return

        with open(report_json_path, "r", encoding="utf-8") as f:
            report_data = json.load(f)

        summary = report_data.get("summary", "")
        
        # 2. Check if the report is a fallback report
        if "AI report generation failed" in summary or not summary:
            logger.info("Skipping TTS because the intelligence report is a fallback output.")
            return

        # 3. Construct a natural spoken script
        script = build_spoken_briefing(report_data)

        logger.info("Generated TTS Script:")
        print("\n--- FINAL SPOKEN SCRIPT ---")
        print(script)
        print("---------------------------\n")

        # 4. Call TTS Provider
        provider = os.environ.get("TTS_PROVIDER", "gtts").strip().lower()
        if output_file is None:
            output_file = os.environ.get("TTS_OUTPUT_FILE", "briefing.mp3").strip()

        logger.info(f"Attempting to generate audio using TTS provider: {provider}")

        if provider == "openai":
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not api_key:
                logger.error("TTS Error: OPENAI_API_KEY is missing for OpenAI TTS.")
                return

            response = requests.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "tts-1",
                    "input": script,
                    "voice": "onyx"
                },
                timeout=30
            )

            if response.status_code == 200:
                with open(output_file, "wb") as f:
                    f.write(response.content)
                logger.info(f"Audio briefing successfully saved to {output_file}")
            else:
                logger.error(f"OpenAI TTS failed: {response.status_code} - {response.text}")

        elif provider == "edge":
            # Edge-TTS (Microsoft Neural Voices)
            voice = os.environ.get("TTS_VOICE", "en-US-GuyNeural").strip()
            rate = os.environ.get("TTS_RATE", "-5%").strip()
            pitch = os.environ.get("TTS_PITCH", "-2Hz").strip()
            volume = os.environ.get("TTS_VOLUME", "+0%").strip()
            
            edge_success = False
            try:
                import subprocess
                command = [
                    "edge-tts",
                    "--text", script,
                    "--voice", voice,
                    f"--rate={rate}",
                    f"--pitch={pitch}",
                    f"--volume={volume}",
                    "--write-media", output_file
                ]
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                logger.info(f"Audio briefing successfully saved to {output_file} using edge-tts.")
                edge_success = True
            except Exception as e:
                logger.error("edge-tts failed, falling back to gTTS.")
                
            if not edge_success:
                try:
                    from gtts import gTTS
                    tts = gTTS(text=script, lang='en', tld='com')
                    tts.save(output_file)
                    logger.info(f"Audio briefing successfully saved to {output_file} using Google TTS fallback.")
                except ImportError:
                    logger.error("TTS Error: 'gTTS' package is not installed. Please run 'pip install gTTS' for Google TTS.")

        elif provider == "gtts":
            # Direct gTTS (Google TTS)
            try:
                from gtts import gTTS
                tts = gTTS(text=script, lang='en', tld='com')
                tts.save(output_file)
                logger.info(f"Audio briefing successfully saved to {output_file} using Google TTS.")
            except ImportError:
                logger.error("TTS Error: 'gTTS' package is not installed. Please run 'pip install gTTS' for Google TTS.")
        else:
            logger.warning(f"Unsupported TTS provider: {provider}. Skipping audio generation.")

    except Exception as e:
        logger.error(f"TTS generation encountered an unexpected error: {e}")

