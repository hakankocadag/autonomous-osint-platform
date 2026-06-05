import os
from PyQt6.QtCore import QThread, pyqtSignal

class QwenAgentThread(QThread):
    response_ready = pyqtSignal(str)
    token_ready = pyqtSignal(str)
    recon_ready = pyqtSignal(str)
    news_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    model_loaded = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.prompt = None
        self.is_recon = False
        self.is_news = False
        self.load_only = True
        self.model = None
        self.tokenizer = None
        self.device = None
        
    def run(self):
        try:
            import sys
            import torch
            import io
            import contextlib
            
            base_dir = os.path.dirname(os.path.abspath(__file__))
            local_ai_dir = os.path.join(base_dir, "local-ai")
            local_model_dir = os.path.join(local_ai_dir, "Qwen3-1.7B")
            if local_ai_dir not in sys.path:
                sys.path.append(local_ai_dir)
                
            from model import Qwen3Model, Args
            from tokenizer import Qwen3Tokenizer
            from safetensors.torch import load_file
            from accelerate import init_empty_weights
            from accelerate.utils import set_module_tensor_to_device
            
            if self.load_only:
                if self.model is None or self.tokenizer is None:
                    self.device = "cuda" if torch.cuda.is_available() else "cpu"
                    dtype  = torch.bfloat16
                    
                    self.tokenizer = Qwen3Tokenizer(
                        tokenizer_file_path=os.path.join(local_model_dir, "tokenizer.json"),
                        repo_id=local_model_dir,
                        apply_chat_template=True,
                        add_generation_prompt=True,
                        add_thinking=False
                    )
                    
                    
                    args = Args()
                    
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        with init_empty_weights():
                            self.model = Qwen3Model(args)
                            
                        files = [
                            os.path.join(local_model_dir, "model-00001-of-00002.safetensors"),
                            os.path.join(local_model_dir, "model-00002-of-00002.safetensors")
                        ]
                        
                        for f in files:
                            state_dict = load_file(f)
                            for param_name, param_tensor in state_dict.items():
                                t = param_name
                                t = t.replace("model.embed_tokens",         "tok_emb")
                                t = t.replace("model.layers",               "trf_blocks")
                                t = t.replace("self_attn",                  "attn")
                                t = t.replace("o_proj",                     "out_proj")
                                t = t.replace("input_layernorm",            "norm_1")
                                t = t.replace("post_attention_layernorm",   "norm_2")
                                t = t.replace("mlp",                        "ffn")
                                t = t.replace("gate_proj",                  "fc1")
                                t = t.replace("up_proj",                    "fc2")
                                t = t.replace("down_proj",                  "fc3")
                                if "norm" in t and t.endswith(".weight"):
                                    t = t.replace(".weight", ".scale")
                                t = t.replace("model.norm.scale", "final_norm.scale")
                                t = t.replace("lm_head",          "out_head")
                                try:
                                    set_module_tensor_to_device(
                                        self.model, t, device=self.device,
                                        value=param_tensor.to(dtype)
                                    )
                                except AttributeError:
                                    pass
                            del state_dict
                            
                        self.model.to(self.device)
                        self.model.eval()
                self.model_loaded.emit()
            else:
                if self.model is None or self.tokenizer is None:
                    self.error_occurred.emit("Model is not loaded yet!")
                    return
                
                final_prompt = self.prompt
                if self.is_recon:
                    final_prompt = f"""You are a cybersecurity reconnaissance planner.
Given a suspicious target, output ONLY a valid JSON object — no explanation, no markdown, no extra text.

Schema:
{{
  "target_type": "ip" | "domain" | "url" | "hash",
  "ip":     "<ip address or null>",
  "domain": "<domain or null>",
  "url":    "<full url or null>",
  "hash":   "<file hash or null>",
  "tools":  ["list", "of", "tool", "names"]
}}

Available tools:
  Terminal: nmap, whois, whatweb, dig, traceroute, curl_headers
  APIs:     virustotal, ipinfo, urlscan

Rules:
- For an IP:     use [nmap, whois, traceroute, abuseipdb, shodan, ipinfo]
- For a domain:  use [whois, dig, whatweb, virustotal, urlscan, ipinfo]
- For a URL:     use [whatweb, curl_headers, virustotal, urlscan]
- For a hash:    use [virustotal, abuseipdb]
- Extraction Strictness: Do NOT infer, guess, or append protocols (like http/https) unless they are explicitly written in the input text. If a target is just a domain, the url field MUST be null.
- Output ONLY the JSON. Nothing else.

<DATA>
{self.prompt}
</DATA>"""
                elif self.is_news:
                    final_prompt = f"""You are an assistant that extracts news query parameters.
Given the user's request, extract the topic/keyword and the hours to look back.
If no hours are provided, use 24.
If the prompt is invalid or doesn't ask for news, set "error" to a message like "I could not find the news" or "prompt wrong".
Output ONLY a valid JSON object — no explanation, no markdown, no extra text.

Schema:
{{
  "keyword": "<topic or keyword>",
  "hours": <number of hours, default 24>,
  "error": "<error message if invalid, or null>"
}}

<DATA>
{self.prompt}
</DATA>"""
                else:
                    final_prompt = f"""You are an advanced AI assistant with real-time news gathering capabilities.
Your job is to determine if the user's input requires searching the web for recent news, events, or updates.
If the user asks ANY question about recent news, current events, or asks for updates on a topic (even if phrased as a yes/no question like "are there any news about X?"), you MUST trigger the news gathering tool by outputting ONLY a JSON object with the following schema and nothing else:
{{
  "intent": "news",
  "keyword": "<topic or keyword extracted from the prompt>",
  "hours": <number of hours to look back, default 24>
}}
Do not answer the question directly if it's about recent news; always use the JSON format so the system can fetch the latest data.
Only if the user's request is a regular chat or a general knowledge question not requiring recent news, respond normally as a helpful AI assistant without using JSON.

<DATA>
{self.prompt}
</DATA>"""

                input_ids = self.tokenizer.encode(final_prompt)
                input_tensor = torch.tensor(input_ids, device=self.device).unsqueeze(0)
                MAX_NEW = 4096
                MAX_SEQ = input_tensor.shape[1] + MAX_NEW + 10
                
                full_response = ""
                
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    for token in self.model.generate_with_cache(
                        token_ids=input_tensor,
                        max_new_tokens=MAX_NEW,
                        eos_token_id=self.tokenizer.eos_token_id,
                        max_seq_len=MAX_SEQ,
                        use_turbo_quant=False
                    ):
                        token_id = token.squeeze(0).tolist()
                        word = self.tokenizer.decode(token_id)
                        full_response += word
                        
                        if not self.is_recon and not self.is_news:
                            stripped = full_response.strip()
                            if not stripped.startswith("{") and not stripped.startswith("```"):
                                self.token_ready.emit(word)
                        
                if self.is_recon:
                    self.recon_ready.emit(full_response)
                elif self.is_news:
                    self.news_ready.emit(full_response)
                else:
                    self.response_ready.emit(full_response)
        except Exception as e:
            self.error_occurred.emit(f"Qwen Agent Error: {str(e)}")
            
    def load_model_async(self):
        if self.isRunning():
            return
        self.load_only = True
        self.start()
        
    def generate_async(self, prompt, is_recon=False, is_news=False):
        if self.isRunning():
            self.error_occurred.emit("Agent is currently busy computing another response.")
            return
        self.prompt = prompt
        self.is_recon = is_recon
        self.is_news = is_news
        self.load_only = False
        self.start()
