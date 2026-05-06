import json
import logging
import os
import re
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a Senior Intelligence Briefing Officer. Your objective is to convert raw OSINT data into a refined Executive Intelligence Brief.

CRITICAL INSTRUCTIONS FOR THE JSON OUTPUT:
1. Output MUST be raw JSON. No markdown, no explanations outside the JSON.
2. Exact Structure:
{
  "sources": [],
  "category": "",
  "location": "",
  "keywords": [],
  "confidence_level": "",
  "key_judgments": [],
  "summary": ""
}

FIELD RULES:
- "sources": Unique names of reporting entities. No URLs.
- "category": Main domain of the data.
- "location": Main geographic scope (e.g., "United States, Europe").
- "keywords": Array of 5-10 concise keywords summarizing the whole dataset. Avoid generic words like "news", "report", "article".
- "confidence_level": Allowed values are "Low", "Medium", or "High" based on clarity.
- "key_judgments": 2-4 short analytical bullet-style judgments grounded in the input. Do not make dramatic unsupported claims.

SUMMARY RULES:
1. Must start exactly with "Sir, " (use "Sir, major...", not "Sir, Major...").
2. Keep it exactly one paragraph, around 60-90 words.
3. It must sound like a direct human intelligence briefing to a decision-maker.
4. Synthesize the whole dataset; do not summarize each record separately.
5. Avoid robotic/academic phrases such as: "ignite global debate", "regulatory frameworks", "profound economic impacts", "critical juncture", "mandates close observation", "ramifications", "global landscape", "broader implications".
"""

import time

def _make_http_request_with_retry(url: str, headers: dict, payload: dict) -> dict:
    max_retries = 3
    backoff = [2, 5, 10]
    last_error = "Unknown error"
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                return response.json()
            elif response.status_code in [503, 429, 500, 502, 504]:
                last_error = f"Status {response.status_code} - {response.reason}: {response.text}"
                logger.warning(f"Attempt {attempt + 1} failed ({last_error}). Retrying in {backoff[attempt]} seconds...")
                time.sleep(backoff[attempt])
            else:
                last_error = f"API Error {response.status_code}: {response.text}"
                logger.error(last_error)
                raise Exception(last_error)
        except requests.exceptions.RequestException as e:
            last_error = f"Network/timeout error: {e}"
            if attempt < max_retries - 1:
                logger.warning(f"Attempt {attempt + 1} failed with {last_error}. Retrying in {backoff[attempt]} seconds...")
                time.sleep(backoff[attempt])
            else:
                logger.error(f"Final attempt failed with exception: {e}")
                
    logger.error("All attempts failed.")
    raise Exception(f"All attempts failed. Last error: {last_error}")

def _call_gemini(prompt: str, api_key: str, model_name: str = "gemini-flash-latest") -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\nData:\n" + prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    data = _make_http_request_with_retry(url, headers, payload)
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise Exception(f"Failed to parse Gemini response: {e}. Raw data: {data}")

def _call_openai(prompt: str, api_key: str, model_name: str = "gpt-4o-mini") -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": model_name,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Here is the scraped data:\n" + prompt}
        ]
    }
    data = _make_http_request_with_retry(url, headers, payload)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise Exception(f"Failed to parse OpenAI response: {e}. Raw data: {data}")

def _call_anthropic(prompt: str, api_key: str, model_name: str = "claude-3-5-sonnet-20240620") -> str:
    url = "https://api.anthropic.com/v1/messages"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    payload = {
        "model": model_name,
        "max_tokens": 2048,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "Here is the scraped data:\n" + prompt + "\n\nPlease respond with strictly the JSON object as requested."}]
    }
    data = _make_http_request_with_retry(url, headers, payload)
    try:
        return data["content"][0]["text"]
    except (KeyError, IndexError) as e:
        raise Exception(f"Failed to parse Anthropic response: {e}. Raw data: {data}")

def parse_and_validate_json(raw_response: str) -> Dict[str, Any]:
    """Parse the LLM response, extracting JSON if wrapped in markdown, and validate schema."""
    default_response = {
        "sources": [],
        "category": "Unknown",
        "location": "Unknown",
        "keywords": [],
        "confidence_level": "Low",
        "key_judgments": [],
        "summary": "AI report generation failed because the selected provider was temporarily unavailable."
    }
    
    if not raw_response:
        return default_response
        
    json_match = re.search(r'```(?:json)?(.*?)```', raw_response, re.DOTALL)
    if json_match:
        raw_response = json_match.group(1).strip()
        
    try:
        parsed = json.loads(raw_response)
        if not isinstance(parsed, dict):
            logger.error("Response is not a JSON object")
            return default_response
            
        validated = {
            "sources": parsed.get("sources", []),
            "category": parsed.get("category", "Mixed") or "Mixed",
            "location": parsed.get("location", "Unknown") or "Unknown",
            "keywords": parsed.get("keywords", []),
            "confidence_level": parsed.get("confidence_level", "Low") or "Low",
            "key_judgments": parsed.get("key_judgments", []),
            "summary": parsed.get("summary", "")
        }
        
        if not isinstance(validated["sources"], list):
            validated["sources"] = []
            
        if isinstance(validated["keywords"], str):
            validated["keywords"] = [k.strip() for k in validated["keywords"].split(",") if k.strip()]
        elif not isinstance(validated["keywords"], list):
            validated["keywords"] = []
            
        if isinstance(validated["key_judgments"], str):
            validated["key_judgments"] = [k.strip() for k in validated["key_judgments"].split("\n") if k.strip()]
        elif not isinstance(validated["key_judgments"], list):
            validated["key_judgments"] = []
            
        return validated
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from AI response: {e}\nRaw output: {raw_response[:200]}...")
        return default_response

def generate_summary_report(cleaned_data: Any, provider: str, api_key: str, model_name: Optional[str] = None) -> Dict[str, Any]:
    """Main entrypoint to generate the intelligence report."""
    logger.info(f"Generating summary report using provider: {provider}")
    
    data_str = json.dumps(cleaned_data, ensure_ascii=False)
    
    def try_call(prov: str, key: str, mod: Optional[str]):
        p_key = prov.lower().strip()
        try:
            if p_key == "gemini":
                return _call_gemini(data_str, key, mod or "gemini-flash-latest"), p_key, None
            elif p_key == "openai":
                return _call_openai(data_str, key, mod or "gpt-4o-mini"), p_key, None
            elif p_key in ["anthropic", "claude"]:
                return _call_anthropic(data_str, key, mod or "claude-3-5-sonnet-20240620"), "claude", None
            else:
                return None, p_key, f"Unknown provider: {prov}"
        except Exception as e:
            return None, p_key, str(e)
            
    raw_response, provider_key, error_msg = try_call(provider, api_key, model_name)
    
    if raw_response is None:
        logger.error(f"Primary provider '{provider}' failed: {error_msg}")
        logger.info("HTTP Status Guide: 401/403 = API key/permission issue | 429 = Quota/rate limit reached | 503 = Provider/model unavailable or high demand.")
        
        import os
        enable_fallback = os.environ.get("ENABLE_PROVIDER_FALLBACK", "false").strip().lower() == "true"
        
        if enable_fallback:
            fallback_list_str = os.environ.get("FALLBACK_PROVIDERS", "openai,claude").strip().lower()
            fallback_providers = [p.strip() for p in fallback_list_str.split(",") if p.strip()]
            
            fallback_map = {
                "openai": ("OPENAI_API_KEY", "gpt-4o-mini"),
                "claude": ("ANTHROPIC_API_KEY", "claude-3-5-sonnet-20240620"),
                "gemini": ("GEMINI_API_KEY", "gemini-flash-latest")
            }
            
            for fallback_prov in fallback_providers:
                if fallback_prov == provider.lower().strip() or fallback_prov not in fallback_map:
                    continue
                
                env_key, default_model = fallback_map[fallback_prov]
                fallback_key = os.environ.get(env_key, "").strip()
                
                if fallback_key:
                    logger.info(f"Attempting fallback provider: {fallback_prov}")
                    raw_response, p_key, error_msg = try_call(fallback_prov, fallback_key, default_model)
                    if raw_response is not None:
                        provider_key = p_key
                        logger.info(f"Fallback to {fallback_prov} succeeded!")
                        break
                    else:
                        logger.error(f"Fallback provider '{fallback_prov}' failed: {error_msg}")
        else:
            logger.warning("Provider fallback is DISABLED via ENABLE_PROVIDER_FALLBACK in .env. Skipping alternative providers.")

    if raw_response is None:
        if enable_fallback:
            logger.error("All configured AI providers failed. Fallback intelligence report saved.")
        else:
            logger.error("Selected AI provider failed and provider fallback is disabled. Clean fallback intelligence report saved.")
        structured_data = {
            "sources": [],
            "category": "Unknown",
            "location": "Unknown",
            "keywords": [],
            "confidence_level": "Low",
            "key_judgments": [],
            "summary": "AI report generation failed because the selected providers were temporarily unavailable."
        }
    else:
        structured_data = parse_and_validate_json(raw_response)
    
    valid_providers = ["gemini", "openai", "claude"]
    output_filename = f"{provider_key}_summary_output.json" if provider_key in valid_providers else "ai_intelligence_report.json"
        
    try:
        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump(structured_data, f, ensure_ascii=False, indent=2)
            
        if raw_response is not None:
            logger.info(f"AI Intelligence Report successfully generated and saved to {output_filename}")
        else:
            logger.info(f"Fallback intelligence report saved to {output_filename}")
            
    except Exception as e:
        logger.error(f"Failed to save summary output: {e}")
        
    return structured_data

if __name__ == "__main__":
    from dotenv import load_dotenv
    import sys
    
    # Fix Windows terminal encoding for emojis
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)-5s %(message)s")
    
    # Load .env file
    load_dotenv()
    
    provider = os.environ.get("AI_PROVIDER", "gemini").strip().lower()
    env_key_map = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
    env_var_name = env_key_map.get(provider, f"{provider.upper()}_API_KEY")
    api_key = os.environ.get(env_var_name, "").strip()
    
    if not api_key:
        try:
            print(f"\n[Missing Key] No '{env_var_name}' found in .env file.")
            api_key = input(f"Enter your {provider.capitalize()} API key: ").strip()
        except EOFError:
            pass
            
    if not api_key:
        print(f"[Error] API key is missing. Please add '{env_var_name}' to your .env file.")
    else:
        try:
            with open("intelligence_report.json", "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if not data:
                print("[Error] intelligence_report.json is empty. Please run the scraper first or add test data to it.")
            else:
                print(f"[Testing] Report generation directly with {len(data)} records using {provider.capitalize()}...")
                generate_summary_report(data, provider, api_key)
        except FileNotFoundError:
            print("[Error] intelligence_report.json not found. Please run the scraper first to generate it.")
