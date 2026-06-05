"""
Autonomous Agent Swarm: OSINT Mass-Harvest Module
Role   : Autonomous Agent Swarm & Core Orchestration
Pattern: Producer-Consumer via asyncio.Queue
         Strategy pattern hook-point for engine routing (BS4 / Playwright)

We should use strict Python type hints and Google-style docstrings.
"""

import asyncio
import json
import logging
import os
import re
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import Optional

# Fix Windows terminal encoding for emojis
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from urllib.parse import urljoin, urlparse

from pydantic import BaseModel, HttpUrl, field_validator
from crawlee.crawlers import (
    PlaywrightCrawler, 
    BeautifulSoupCrawler,
)
from crawlee.proxy_configuration import ProxyConfiguration

from nlp import (
    TextPipeline, UnicodeNormalizer, BS4HTMLStripper, SpacyNoiseReduction,
    DuplicateLineRemover, NLPTacticalConsumer, TacticalPayload, ProcessedPayload
)
from report_service import generate_summary_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)




SOURCES: list[dict] = [

    {"name": "Reuters World",           "url": "https://www.reuters.com/world/",                             "category": "Wire"},
    {"name": "AP News",                 "url": "https://apnews.com/world-news",                              "category": "Wire"},
    {"name": "BBC World",               "url": "https://www.bbc.com/news/world",                             "category": "Mainstream"},
    {"name": "Al Jazeera",              "url": "https://www.aljazeera.com/news/",                            "category": "Mainstream"},
    {"name": "The Guardian",            "url": "https://www.theguardian.com/world",                          "category": "Mainstream"},


    {"name": "Bellingcat",              "url": "https://www.bellingcat.com/category/news/",                  "category": "Geopolitics"},
    {"name": "ISW (War Study)",         "url": "https://www.understandingwar.org/",                          "category": "Geopolitics"},
    {"name": "Foreign Policy",          "url": "https://foreignpolicy.com/category/latest/",                 "category": "Geopolitics"},
    {"name": "Defense News",            "url": "https://www.defensenews.com/global/",                        "category": "Defense"},
    

    {"name": "Bloomberg Markets",       "url": "https://www.bloomberg.com/markets",                          "category": "Finance"},
    {"name": "Financial Times",         "url": "https://www.ft.com/world",                                   "category": "Finance"},
    {"name": "Hacker News",             "url": "https://news.ycombinator.com/newest",                        "category": "Tech"},
    {"name": "Bleeping Computer",       "url": "https://www.bleepingcomputer.com/",                          "category": "Tech"},


    {"name": "Reddit WorldNews",        "url": "https://www.reddit.com/r/worldnews/new/",                    "category": "Aggregator"},
    {"name": "X / Twitter Search",      "url": "https://x.com/search?q=&src=typed_query&f=live",             "category": "Social"},
    {"name": "Liveuamap",               "url": "https://liveuamap.com/",                                     "category": "Aggregator"}
]

CATEGORIES: list[str] = sorted({s["category"] for s in SOURCES})




class DiscoveredLink(BaseModel):
    """A candidate URL found on an index/listing page."""
    source_name: str
    category: str
    headline: str
    url: HttpUrl
    discovered_at: datetime
    pub_time: Optional[datetime] = None

    @field_validator("url", mode="before", check_fields=False)
    @classmethod
    def coerce_url(cls, v: object) -> str:
        """Coerce URL to string before Pydantic validates it as HttpUrl."""
        return str(v)

    def age_hours(self) -> float:
        """Return approximate age in hours."""
        ref = self.pub_time or self.discovered_at
        return (datetime.now(timezone.utc) - ref).total_seconds() / 3600




def clean_text(text: str) -> str:
    """Remove excessive whitespace from a short string."""
    return re.sub(r"\s+", " ", text).strip()


def parse_relative_time(text: str) -> Optional[datetime]:
    """Parse relative timestamps locally."""
    now = datetime.now(timezone.utc)
    text = text.lower()
    patterns = [
        (r"(\d+)\s*s(?:ec|econds?)?(?:\s*ago)?",  "seconds"),
        (r"(\d+)\s*min(?:utes?)?(?:\s*ago)?",      "minutes"),
        (r"(\d+)\s*h(?:ours?)?(?:\s*ago)?",        "hours"),
        (r"(\d+)\s*d(?:ays?)?(?:\s*ago)?",         "days"),
    ]
    for pattern, unit in patterns:
        m = re.search(pattern, text)
        if m:
            return now - timedelta(**{unit: int(m.group(1))})
    return None




class ExtractionStrategy(ABC):
    """Strategy Interface for executing an extraction routine on a set of links."""
    
    @abstractmethod
    async def run(self, links: list[DiscoveredLink], queue: asyncio.Queue[TacticalPayload]) -> None:
        """Execute extraction and feed payloads into the NLP queue.
        
        Args:
            links: A chunk of DiscoveredLinks assigned to this strategy.
            queue: The NLP tactical consumer's incoming queue block.
        """
        pass


class PlaywrightExtractionStrategy(ExtractionStrategy):
    """A heavy, full-browser strategy for dynamic SPAs and Javascript sources."""

    async def run(self, links: list[DiscoveredLink], queue: asyncio.Queue[TacticalPayload]) -> None:
        if not links:
            return
            
        logger.info(f"🕸️ Playwright Strategy handling {len(links)} links")
        
        # Implement resilience and backoff configuration
        proxy_url = os.environ.get("PROXY_URL")
        proxy_config = ProxyConfiguration(proxy_urls=[proxy_url]) if proxy_url else None

        crawler = PlaywrightCrawler(
            max_requests_per_crawl=len(links) + 5,
            headless=True,
            max_request_retries=3,        # Retry on intermittent block
            request_handler_timeout=timedelta(seconds=20),
            proxy_configuration=proxy_config,
        )
        
        # We block images, CSS, and fonts here because we don't care how the page looks.
        # This makes the crawler run lightning fast and saves a ton of bandwidth!
        async def block_visual_assets(context) -> None:
            await context.page.route(
                "**/*", 
                lambda route: route.abort() if route.request.resource_type in ["image", "stylesheet", "font", "media"] else route.continue_()
            )
            
        crawler.pre_navigation_hook(block_visual_assets)

        url_map = {str(l.url): l for l in links}

        @crawler.router.default_handler
        async def handler(context) -> None:
            url = context.request.url
            meta = url_map.get(url)
            if not meta:
                return

            try:
                # Instead of running NLP here, we simply fetch raw context
                await context.page.wait_for_selector("body", timeout=15000)
                title = await context.page.title()
                content = await context.page.content()  # Raw HTML
                
                payload = TacticalPayload(
                    source_name=meta.source_name,
                    category=meta.category,
                    headline=title or meta.headline,
                    url=meta.url,
                    content=content,
                    extracted_at=datetime.now(timezone.utc)
                )
                
                # Push heavily unstructured data directly to the NLP pipeline
                await queue.put(payload)
                logger.info(f"✅  Pushed (Playwright): {meta.headline[:50]}")
            except Exception as e:
                logger.error(f"❌ Playwright extraction failed on {url}: {e}")

        await crawler.run([str(l.url) for l in links])


class BeautifulSoupExtractionStrategy(ExtractionStrategy):
    """A lightweight, high-speed strategy for static sites using BS4 & aiohttp."""

    async def run(self, links: list[DiscoveredLink], queue: asyncio.Queue[TacticalPayload]) -> None:
        if not links:
            return
            
        logger.info(f"🕷️ BeautifulSoup Strategy handling {len(links)} links")
        
        # Resiliency: Pool connections efficiently behind crawlee HTTP client abstraction
        proxy_url = os.environ.get("PROXY_URL")
        proxy_config = ProxyConfiguration(proxy_urls=[proxy_url]) if proxy_url else None

        crawler = BeautifulSoupCrawler(
            parser="html.parser",
            max_request_retries=3,
            proxy_configuration=proxy_config,
        )

        url_map = {str(l.url): l for l in links}

        @crawler.router.default_handler
        async def handler(context) -> None:
            url = context.request.url
            meta = url_map.get(url)
            if not meta:
                return

            try:
                # Raw extraction
                title = context.soup.title.string if context.soup.title else ""
                content = str(context.soup)  # Raw HTML body
                
                payload = TacticalPayload(
                    source_name=meta.source_name,
                    category=meta.category,
                    headline=title or meta.headline,
                    url=meta.url,
                    content=content,
                    extracted_at=datetime.now(timezone.utc)
                )
                
                await queue.put(payload)
                logger.info(f"✅  Pushed (BeautifulSoup): {meta.headline[:50]}")
            except Exception as e:
                logger.error(f"❌ BeautifulSoup extraction failed on {url}: {e}")

        await crawler.run([str(l.url) for l in links])


class StrategyRouter:
    """Routes specific URLs to their most efficient ExtractionStrategy."""
    
    def __init__(self):
        self.playwright_strategy = PlaywrightExtractionStrategy()
        self.bs4_strategy = BeautifulSoupExtractionStrategy()
        
    def dispatch(self, links: list[DiscoveredLink]) -> dict[ExtractionStrategy, list[DiscoveredLink]]:
        """Determine routing based on domain complexity / category heuristics."""
        routing: dict[ExtractionStrategy, list[DiscoveredLink]] = {
            self.playwright_strategy: [],
            self.bs4_strategy: []
        }
        
        for link in links:
            # If the site is heavily reliant on Javascript (like Reddit), we route it to Playwright 
            # so the browser can actually render the JS. Otherwise, we just use simple BS4 for speed.
            if link.category == "Aggregator" or "ycombinator" in str(link.url):
                routing[self.playwright_strategy].append(link)
            else:
                routing[self.bs4_strategy].append(link)
                
        return routing




async def main() -> None:
    # Initialize the Autonomous Consumer Process
    processors = [
        UnicodeNormalizer(),
        BS4HTMLStripper(),
        SpacyNoiseReduction(model_name="en_core_web_sm"),
        DuplicateLineRemover(),
    ]
    nlp_consumer = NLPTacticalConsumer(pipeline=TextPipeline(processors))
    
    # Producer Consumer queues (Capped with Backpressure to prevent Memory Bloat)
    nlp_inbox: asyncio.Queue[TacticalPayload] = asyncio.Queue(maxsize=50)
    nlp_outbox: asyncio.Queue[ProcessedPayload] = asyncio.Queue()
    
    # 1. Fire up the backend NLP loop (daemonized equivalent)
    consumer_task = asyncio.create_task(nlp_consumer.start(nlp_inbox, nlp_outbox))

    keyword = input("Enter topic: ").strip() or "AI"
    
    hours_input = input("Enter last how many hours: ").strip()
    try:
        hours = float(hours_input)
    except ValueError:
        hours = 24.0

    print("\nAvailable sources:")
    for i, s in enumerate(SOURCES, start=1):
        print(f"{i}. {s['name']}")
    print(f"{len(SOURCES) + 1}. Gather All")

    choice = input("Choose sources: ").strip()
    
    selected_sources = []
    if choice == str(len(SOURCES) + 1) or not choice:
        selected_sources = SOURCES
    else:
        for part in choice.split(","):
            try:
                idx = int(part.strip()) - 1
                if 0 <= idx < len(SOURCES):
                    selected_sources.append(SOURCES[idx])
            except ValueError:
                pass
                
    if not selected_sources:
        selected_sources = SOURCES

    logger.info(f"🚀 Phase 1 — Deep Discovery on: {keyword} (max {hours} hours old)")

    discovered: list[DiscoveredLink] = []
    
    # Re enable the Discovery engine to autonomously find links inside the source sites
    discovery_crawler = PlaywrightCrawler(
        max_requests_per_crawl=len(selected_sources) * 2,
        headless=True,
        max_request_retries=2
    )
    
    async def block_visuals(context):
        await context.page.route("**/*", lambda r: r.abort() if r.request.resource_type in ["image", "stylesheet", "font", "media"] else r.continue_())
    discovery_crawler.pre_navigation_hook(block_visuals)

    source_map = {s["url"]: s for s in selected_sources}

    @discovery_crawler.router.default_handler
    async def discover_handler(context):
        page_url = context.request.url
        source_meta = next((meta for seed, meta in source_map.items() if seed in page_url), None)
        if not source_meta: return

        try:
            await context.page.wait_for_selector("body", timeout=10000)
            links = await context.page.query_selector_all("a")
            for link in links:
                try:
                    headline = clean_text(await link.inner_text())
                    if not headline or len(headline) < 10:
                        continue
                        
                    # We use a word boundary regex here so we don't accidentally match substrings.
                    # For example, if the user's keyword is "ai", we don't want to match the word "Spain".
                    if not re.search(r'\b' + re.escape(keyword) + r'\b', headline, re.IGNORECASE):
                        continue
                        
                    href = await link.get_attribute("href")
                    if not href: continue
                    full_url = urljoin(page_url, href)
                    if not full_url.startswith("http"): continue

                    # Attempt Recency Extraction
                    pub_time = None
                    parent = await link.evaluate_handle("el => el.closest('article,li,div')")
                    if parent:
                        parent_text = clean_text(await parent.as_element().inner_text())
                        pub_time = parse_relative_time(parent_text)
                    
                    record = DiscoveredLink(
                        source_name=source_meta["name"],
                        category=source_meta["category"],
                        headline=headline[:120],
                        url=full_url,
                        discovered_at=datetime.now(timezone.utc),
                        pub_time=pub_time
                    )
                    
                    if record.age_hours() <= hours:
                        discovered.append(record)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Discovery timeout on {page_url}")

    await discovery_crawler.run([s["url"] for s in selected_sources])

    # Deduplicate links
    seen_urls = set()
    unique_discovered = []
    for d in discovered:
        clean_url = str(d.url).split("?")[0]
        if clean_url not in seen_urls:
            seen_urls.add(clean_url)
            unique_discovered.append(d)

    selected_links = unique_discovered
    logger.info(f"🔍 Discovered {len(selected_links)} unique target articles matching '{keyword}' under {hours} hours old.")

    # 2. Route links to correct Producer Strategy
    router = StrategyRouter()
    strategy_mapping = router.dispatch(selected_links)
    
    logger.info("🚀 Phase 2 Concurrent Strategy Crawl")
    
    # 3. Execute all Producers concurrently
    # Gather creates non blocking task streams for Playwright and BeautifulSoup models
    producer_tasks = []
    for strategy, chunk_links in strategy_mapping.items():
        if chunk_links:
            producer_tasks.append(
                asyncio.create_task(strategy.run(chunk_links, nlp_inbox))
            )
            
    await asyncio.gather(*producer_tasks)

    # 4. Await queue drain and map results
    logger.info("⏳ Waiting for NLP queue to digest all chunks...")
    await nlp_inbox.join()
    
    # Dump results using Pydantic v2 high speed Rust compiled serialization
    results = []
    while not nlp_outbox.empty():
        item = nlp_outbox.get_nowait()
        results.append(item.model_dump(mode="json"))

    output_file = "intelligence_report.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ HARVEST COMPLETE. Scraped {len(results)} finalized records.")
    
    # Cleanup task
    consumer_task.cancel()
    
    # AI Summarization Step
    print("\n------------------------------------------------")
    
    # Load environment variables from .env file using python-dotenv
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print("[Note] python-dotenv not installed. Falling back to native os.environ.")
        # Try to load .env manually if dotenv isn't installed
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as env_file:
                for line in env_file:
                    if "=" in line and not line.strip().startswith("#"):
                        key, val = line.strip().split("=", 1)
                        os.environ[key.strip()] = val.strip()

    # Determine Provider
    provider = os.environ.get("AI_PROVIDER", "").strip().lower()
    
    # Fallback to asking if provider is missing
    if not provider:
        try:
            print("\n[Missing Config] No 'AI_PROVIDER' found in .env file.")
            gen_ai = input("Would you like to generate an AI Intelligence Report? (y/n): ").strip().lower()
            if gen_ai == 'y':
                print("Select AI Provider:")
                print("1. Google Gemini (default)")
                print("2. OpenAI")
                print("3. Anthropic Claude")
                provider_choice = input("Enter choice (1-3): ").strip()
                provider_map = {"1": "gemini", "2": "openai", "3": "claude"}
                provider = provider_map.get(provider_choice, "gemini")
        except EOFError:
            pass

    if not provider:
        print("❌ Skipping AI generation. To enable, add 'AI_PROVIDER=gemini' (or openai/claude) to your .env file.")
    else:
        # Determine API Key based on provider
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
            print(f"❌ API key is missing. Please add '{env_var_name}=your_key_here' to your .env file.")
        else:
            print(f"Cleaned records loaded for AI report: {len(results)}")
            if len(results) == 0:
                print("No cleaned records found. Skipping AI report generation.")
            else:
                print(f"⏳ Generating intelligence report using {provider.capitalize()}...")
                generate_summary_report(cleaned_data=results, provider=provider, api_key=api_key)
                
                # Check if TTS should run
                enable_tts = os.environ.get("ENABLE_TTS", "false").strip().lower() == "true"
                if enable_tts:
                    try:
                        from tts_service import generate_audio_briefing
                        
                        # Find the generated json file
                        # We don't know the exact name if fallback occurred, so we check likely candidates
                        # prioritizing the selected provider
                        candidates = [
                            f"{provider}_summary_output.json",
                            "openai_summary_output.json",
                            "claude_summary_output.json",
                            "gemini_summary_output.json",
                            "ai_intelligence_report.json"
                        ]
                        
                        report_file = None
                        for candidate in candidates:
                            if os.path.exists(candidate):
                                report_file = candidate
                                break
                                
                        if report_file:
                            print(f"🔊 Executing TTS briefing from {report_file}...")
                            output_file = os.environ.get("TTS_OUTPUT_FILE", "briefing.mp3")
                            generate_audio_briefing(report_file, output_file)
                        else:
                            print("❌ Could not locate the generated AI report JSON for TTS.")
                            
                    except Exception as e:
                        logger.error(f"TTS pipeline integration failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())