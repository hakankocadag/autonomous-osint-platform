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
  "locations": [
    {
      "location": "",
      "news_titles": []
    }
  ],
  "keywords": [],
  "topics": [],
  "confidence_level": "",
  "key_judgments": [],
  "topics_analysis": [
    {
      "topic_name": "",
      "paragraphs": [""]
    }
  ]
}

FIELD RULES:
- "sources": Unique names of reporting entities. No URLs.
- "category": Main domain of the data.
- "locations": Array of objects mapping locations (geographic scope like "United States", "Europe", "Paris") to an array of news titles relevant to that location. If vague, leave empty.
- "keywords": Array of 5-10 concise keywords summarizing the whole dataset. Avoid generic words like "news", "report", "article".
- "topics": Array of topic headers that correspond to the sections discussed in the summary.
- "confidence_level": Allowed values are "Low", "Medium", or "High" based on clarity.
- "key_judgments": 2-4 short analytical bullet-style judgments grounded in the input. Do not make dramatic unsupported claims.

SUMMARY RULES:
1. Provide a structural analysis inside "topics_analysis". Create an object for each topic.
2. For each topic, write 2-3 detailed paragraphs analyzing the topic and store them as separate string elements in the "paragraphs" array. Do NOT output raw Markdown text blocks outside the JSON.
3. Synthesize the whole dataset; do not summarize each record separately.
4. Avoid robotic/academic phrases such as: "ignite global debate", "regulatory frameworks", "profound economic impacts", "critical juncture", "mandates close observation", "ramifications", "global landscape", "broader implications".
"""

def get_system_prompt(keyword: Optional[str] = None) -> str:
    prompt = SYSTEM_PROMPT
    if keyword and keyword.strip().lower() not in ["news", "all", "latest"]:
        prompt += f"\n\nCRITICAL DIRECTIVE: The user explicitly searched for the keyword/topic: '{keyword}'. You MUST aggressively filter out unrelated side-news from the provided text and strictly focus your intelligence brief, key judgments, and summaries ONLY on events, actions, and insights related to '{keyword}'. If a provided article does not contain information about '{keyword}', you must ignore it entirely."
    return prompt

import time

def _make_http_request_with_retry(url: str, headers: dict, payload: dict) -> dict:
    max_retries = 3
    backoff = [2, 5, 10]
    last_error = "Unknown error"
    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=300)
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

def _call_gemini(prompt: str, api_key: str, model_name: str = "gemini-flash-latest", keyword: Optional[str] = None) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    sys_prompt = get_system_prompt(keyword)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": sys_prompt + "\n\nData:\n" + prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    data = _make_http_request_with_retry(url, headers, payload)
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise Exception(f"Failed to parse Gemini response: {e}. Raw data: {data}")

def _call_openai(prompt: str, api_key: str, model_name: str = "gpt-4o-mini", keyword: Optional[str] = None) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    sys_prompt = get_system_prompt(keyword)
    payload = {
        "model": model_name,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": "Here is the scraped data:\n" + prompt}
        ]
    }
    data = _make_http_request_with_retry(url, headers, payload)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise Exception(f"Failed to parse OpenAI response: {e}. Raw data: {data}")

def _call_anthropic(prompt: str, api_key: str, model_name: str = "claude-3-5-sonnet-20240620", keyword: Optional[str] = None) -> str:
    url = "https://api.anthropic.com/v1/messages"
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    sys_prompt = get_system_prompt(keyword)
    payload = {
        "model": model_name,
        "max_tokens": 2048,
        "system": sys_prompt,
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
        "locations": [],
        "keywords": [],
        "topics": [],
        "confidence_level": "Low",
        "key_judgments": [],
        "summary": "System Error: The intelligence feed could not be synthesized due to a provider failure."
    }
    
    if not raw_response:
        return default_response
        
    json_match = re.search(r'```(?:json)?(.*?)```', raw_response, re.DOTALL)
    if json_match:
        raw_response = json_match.group(1).strip()
    else:
        start = raw_response.find('{')
        end = raw_response.rfind('}')
        if start != -1 and end != -1:
            raw_response = raw_response[start:end+1]
        
    try:
        parsed = json.loads(raw_response, strict=False)
        if not isinstance(parsed, dict):
            logger.error("Response is not a JSON object")
            return default_response
            
        topics_analysis = parsed.get("topics_analysis", [])
        references = parsed.get("references", [])
        
        summary_html = ""
        if isinstance(topics_analysis, list):
            for t in topics_analysis:
                if isinstance(t, dict):
                    topic_name = t.get('topic_name', 'Topic')
                    summary_html += f"### {topic_name}\n\n"
                    paras = t.get("paragraphs", [])
                    if isinstance(paras, list):
                        for p in paras:
                            summary_html += f"{p}\n\n"
        
        if references and isinstance(references, list):
            summary_html += "### References\n\n"
            for r in references:
                if isinstance(r, dict):
                    summary_html += f"&bull; **[{r.get('source', 'Source')}]**: {r.get('claim', '')}\n"

        validated = {
            "sources": parsed.get("sources", []),
            "category": parsed.get("category", "Mixed") or "Mixed",
            "locations": parsed.get("locations", []),
            "keywords": parsed.get("keywords", []),
            "topics": parsed.get("topics", []),
            "confidence_level": parsed.get("confidence_level", "Low") or "Low",
            "key_judgments": parsed.get("key_judgments", []),
            "summary": summary_html
        }
        
        if not isinstance(validated["sources"], list):
            validated["sources"] = []
            
        if not isinstance(validated["locations"], list):
            validated["locations"] = []
            
        if isinstance(validated["keywords"], str):
            validated["keywords"] = [k.strip() for k in validated["keywords"].split(",") if k.strip()]
        elif not isinstance(validated["keywords"], list):
            validated["keywords"] = []
            
        if isinstance(validated["topics"], str):
            validated["topics"] = [k.strip() for k in validated["topics"].split(",") if k.strip()]
        elif not isinstance(validated["topics"], list):
            validated["topics"] = []
            
        if isinstance(validated["key_judgments"], str):
            validated["key_judgments"] = [k.strip() for k in validated["key_judgments"].split("\n") if k.strip()]
        elif not isinstance(validated["key_judgments"], list):
            validated["key_judgments"] = []
            
        return validated
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from AI response: {e}\nRaw output: {raw_response[:200]}...")
        return default_response

def generate_summary_report(cleaned_data: Any, provider: str, api_key: str, model_name: Optional[str] = None, keyword: Optional[str] = None) -> Dict[str, Any]:
    """Main entrypoint to generate the intelligence report."""
    logger.info(f"Generating summary report using provider: {provider} for keyword: {keyword}")
    
    data_str = json.dumps(cleaned_data, ensure_ascii=False)
    
    def try_call(prov: str, key: str, mod: Optional[str]):
        p_key = prov.lower().strip()
        try:
            if p_key == "gemini":
                return _call_gemini(data_str, key, mod or "gemini-flash-latest", keyword), p_key, None
            elif p_key == "openai":
                return _call_openai(data_str, key, mod or "gpt-4o-mini", keyword), p_key, None
            elif p_key in ["anthropic", "claude"]:
                return _call_anthropic(data_str, key, mod or "claude-3-5-sonnet-20240620", keyword), "claude", None
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
            "locations": [],
            "keywords": [],
            "confidence_level": "None",
            "key_judgments": [],
            "summary": f"System Error: Intelligence synthesis failed. Details: {error_msg}"
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
            print("[Error] intelligence_report.json not found. Please run the scraper first to generate it.")
