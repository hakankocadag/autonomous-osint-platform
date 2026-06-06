"""
Modular Async Text Preprocessing Pipeline for AI/NLP.

Designed to dynamically consume scraped HTML/text and clean it into
a structured, NLP-ready format using spaCy and BeautifulSoup.
"""

import asyncio
import re
import unicodedata
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Any

from bs4 import BeautifulSoup
from pydantic import BaseModel, HttpUrl, field_validator

# Try importing spacy, allow lazy loading or fallback gracefully if missing 
# during initial container spins.
try:
    import spacy
except ImportError:
    spacy = None  # type: ignore


logger = logging.getLogger(__name__)




class BaseModelWithUrl(BaseModel):
    """Base model containing common URL coercion."""
    
    @field_validator("url", mode="before", check_fields=False)
    @classmethod
    def coerce_url(cls, v: Any) -> str:
        """Coerce URL to string before Pydantic validates it as HttpUrl."""
        return str(v)


class TacticalPayload(BaseModelWithUrl):
    """Raw payload injected into the NLP Queue by the Swarm Orchestrator.
    
    Acts as the strict entry point contract.
    """
    source_name: str
    category: str
    headline: str
    url: HttpUrl
    content: str
    extracted_at: datetime


class ProcessedPayload(BaseModelWithUrl):
    """Fully processed payload ready for database ingestion or model training.
    
    Acts as the strict exit point contract.
    """
    source_name: str
    category: str
    headline: str
    url: HttpUrl
    original_length: int
    cleaned_content: str
    cleaned_length: int
    extracted_at: datetime
    processed_at: datetime




class BaseTextProcessor(ABC):
    """Abstract Base Class for all asynchronous text processors."""
    
    @abstractmethod
    async def process(self, text: str) -> str:
        """Process the text block and return the modified string.
        
        Args:
            text: Input string or HTML.
            
        Returns:
            Processed string.
        """
        pass


class BS4HTMLStripper(BaseTextProcessor):
    """Removes HTML and boilerplate tags using BeautifulSoup4 asynchronously."""
    
    def __init__(self, parser: str = "html.parser"):
        self.parser = parser

    def _strip_html(self, text: str) -> str:
        try:
            soup = BeautifulSoup(text, self.parser)
            # Remove base tags
            for element in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                element.extract()
                
            # Remove elements by common boilerplate class/id names
            boilerplate_keywords = ["nav", "menu", "footer", "header", "sidebar", "cookie", "promo", "advert"]
            for element in soup.find_all(attrs={"class": True}):
                classes = " ".join(element.get("class", [])).lower()
                if any(kw in classes for kw in boilerplate_keywords):
                    element.extract()
                    
            for element in soup.find_all(attrs={"id": True}):
                el_id = element.get("id", "").lower()
                if any(kw in el_id for kw in boilerplate_keywords):
                    element.extract()
                    
            return soup.get_text(separator="\n", strip=True)
        except Exception as e:
            logger.error(f"HTML Stripping failed: {e}")
            return text

    async def process(self, text: str) -> str:
        """Remove HTML tags safely by offloading to an OS thread."""
        return await asyncio.to_thread(self._strip_html, text)


class SpacyNoiseReduction(BaseTextProcessor):
    """Leverages spaCy to normalize text, hyper-optimized for max speed."""
    
    def __init__(self, model_name: str = "en_core_web_sm"):
        self.model_name = model_name
        self._nlp = None

    def _load_model(self) -> None:
        if spacy is None:
            logger.warning("spaCy is not installed. SpacyNoiseReduction will pass-through.")
            return

        if self._nlp is None:
            try:
                # We disable all the heavy lifting pipelines in spaCy (like named entity recognition)
                # because we only need basic, fast tokenization to clean up the text.
                self._nlp = spacy.load(
                    self.model_name,
                    disable=["ner", "parser", "lemmatizer", "textcat", "custom"]
                )
            except Exception as e:
                logger.warning(f"Failed to load spaCy model '{self.model_name}': {e}. Pass-through enabled.")
                self._nlp = "failed" # type: ignore

    def _execute_spacy(self, text: str) -> str:
        self._load_model()
        
        if self._nlp is None or self._nlp == "failed":
            return re.sub(r"\s+", " ", text).strip()
            
        try:
            doc = self._nlp(text[:100000])  # Limit to 100k chars to cap RAM usage
            tokens = [token.text for token in doc if not token.is_space]
            return " ".join(tokens)
        except Exception as e:
            logger.error(f"spaCy processing error: {e}")
            return text

    async def process(self, text: str) -> str:
        """Analyze text with spaCy fully off the main event loop thread."""
        return await asyncio.to_thread(self._execute_spacy, text)


class UnicodeNormalizer(BaseTextProcessor):
    """Normalizes Unicode characters to NFKC form asynchronously."""
    
    def __init__(self, form: str = "NFKC") -> None:
        self.form = form

    async def process(self, text: str) -> str:
        return unicodedata.normalize(self.form, text)


class DuplicateLineRemover(BaseTextProcessor):
    """Removes exact duplicate lines from the text."""
    
    async def process(self, text: str) -> str:
        lines = text.split("\n")
        seen: set[str] = set()
        unique_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped not in seen:
                seen.add(stripped)
                unique_lines.append(stripped)
        return "\n".join(unique_lines)


class TextPipeline:
    """Orchestrator that executes the processing pipeline."""
    
    def __init__(self, processors: List[BaseTextProcessor]) -> None:
        self.processors = processors

    async def execute(self, text: str) -> str:
        """Run the text through the established async pipeline."""
        if not text:
            return ""

        for proc in self.processors:
            text = await proc.process(text)
            if not text.strip():
                return ""
                
        return text




class NLPTacticalConsumer:
    """Consumer service that endlessly pulls payloads and normalizes them."""
    
    def __init__(self, pipeline: TextPipeline) -> None:
        """Initialize with a configured NLP pipeline."""
        self.pipeline = pipeline

    async def start(
        self, 
        inbox: asyncio.Queue[TacticalPayload], 
        outbox: asyncio.Queue[ProcessedPayload]
    ) -> None:
        """Begin the unified event loop consumer.
        
        Args:
            inbox: Shared asyncio queue where the Swarm produces data.
            outbox: Output queue (or could be external db writes).
        """
        logger.info("🟢 NLPTacticalConsumer initialized. Waiting for payloads...")
        
        try:
            while True:
                # Asynchronously wait for a new raw payload from the Swarm
                payload: TacticalPayload = await inbox.get()
                
                try:
                    original_length = len(payload.content)
                    
                    # Pass the dirty HTML/text block through our entire processing pipeline 
                    # to strip tags, remove duplicates, and normalize unicode.
                    cleaned_content = await self.pipeline.execute(payload.content)
                    
                    # Generate the strict ProcessedPayload output structure
                    processed_payload = ProcessedPayload(
                        source_name=payload.source_name,
                        category=payload.category,
                        headline=payload.headline,
                        url=payload.url,
                        original_length=original_length,
                        cleaned_content=cleaned_content,
                        cleaned_length=len(cleaned_content),
                        extracted_at=payload.extracted_at,
                        processed_at=datetime.utcnow()
                    )
                    
                    # Push downstream
                    await outbox.put(processed_payload)
                    logger.debug(f"✅ NLP processed: {payload.headline[:30]}")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to process payload from {payload.url}: {e}")
                finally:
                    # Signal consumer that item is done routing
                    inbox.task_done()
                    
        except asyncio.CancelledError:
            logger.info("🛑 NLPTacticalConsumer has been cancelled and is shutting down.")
            raise