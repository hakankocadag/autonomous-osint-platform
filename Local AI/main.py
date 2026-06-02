import time
import sys
import os
import json
import re
import threading

R    = "\033[0m"
B    = "\033[1m"
DIM  = "\033[2m"

PURP  = "\033[38;5;129m"   
PURPL = "\033[38;5;141m"
BLOOD = "\033[38;5;160m"   
CRIMN = "\033[38;5;196m"   
ROSE  = "\033[38;5;204m"   
GRAY  = "\033[38;5;240m"   
SILVR = "\033[38;5;250m"   

C   = PURP
G   = PURPL
Y   = ROSE
RD  = BLOOD
M   = CRIMN

logo_lines = [
    (BLOOD, r" ██████╗ ███████╗██╗███╗   ██╗████████╗"),
    (CRIMN, r"██╔═══██╗██╔════╝██║████╗  ██║╚══██╔══╝"),
    (ROSE,  r"██║   ██║███████╗██║██╔██╗ ██║   ██║   "),
    (PURPL, r"██║   ██║╚════██║██║██║╚██╗██║   ██║   "),
    (PURP,  r"╚██████╔╝███████║██║██║ ╚████║   ██║   "),
    (GRAY,  r" ╚═════╝ ╚══════╝╚═╝╚═╝  ╚═══╝   ╚═╝  "),
]
subtitle   = "     Open Source Intelligence  ·  Recon Engine"
separator  = "─" * 50


def print_logo():
    os.system("clear" if os.name != "nt" else "cls")
    for color, line in logo_lines:
        print(f"{color}{B}{line}{R}")
        time.sleep(0.05)
    print()
    print(f"{PURPL}{B}{subtitle}{R}")
    print(f"{BLOOD}{DIM}{separator}{R}\n")


class Spinner:
    def __init__(self, message: str, color: str = Y):
        self.message  = message
        self.color    = color
        self._stop    = threading.Event()
        self._thread  = threading.Thread(target=self._spin, daemon=True)
        self.frames   = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.frames[i % len(self.frames)]
            sys.stdout.write(f"\r{self.color}{B}{frame}  {self.message}...{R}   ")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1

    def start(self):
        self._thread.start()
        return self

    def stop(self, final_msg: str = "", success: bool = True):
        self._stop.set()
        self._thread.join()
        icon = f"{G}✔" if success else f"{RD}✘"
        msg  = final_msg or self.message
        sys.stdout.write(f"\r{icon}  {B}{msg}{R}                    \n")
        sys.stdout.flush()


def load_model_silently():
    import io
    import contextlib

    captured_out = io.StringIO()
    captured_err = io.StringIO()

    spinner = Spinner("Loading model weights", C)
    spinner.start()

    with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
        import torch
        from model     import Qwen3Model, Args
        from tokenizer import Qwen3Tokenizer
        from safetensors.torch import load_file
        from accelerate import init_empty_weights
        from accelerate.utils import set_module_tensor_to_device

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype  = torch.bfloat16

        tokenizer = Qwen3Tokenizer(
            tokenizer_file_path="./Qwen3-1.7B/tokenizer.json",
            repo_id="./Qwen3-1.7B",
            apply_chat_template=True,
            add_generation_prompt=True,
            add_thinking=False
        )

        args = Args()
        with init_empty_weights():
            model = Qwen3Model(args)

        files = [
            "./Qwen3-1.7B/model-00001-of-00002.safetensors",
            "./Qwen3-1.7B/model-00002-of-00002.safetensors"
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
                        model, t, device=device,
                        value=param_tensor.to(dtype)
                    )
                except AttributeError:
                    pass
            del state_dict

        model.to(device) # <-- This code added
        model.eval()

    spinner.stop("Model ready", success=True)
    return model, tokenizer, device, torch


def run_inference_silent(model, tokenizer, device, torch, prompt_text: str) -> str:
    import io, contextlib

    PLANNER_SYSTEM_PROMPT = f"""
You are a cybersecurity reconnaissance planner.
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
{prompt_text}
</DATA>
"""

    spinner = Spinner("Analyzing target with AI", M)
    spinner.start()

    full_response = ""
    buf = io.StringIO()

    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        input_ids = tokenizer.encode(PLANNER_SYSTEM_PROMPT)
        input_tensor = torch.tensor(input_ids, device=device).unsqueeze(0)
        MAX_NEW = 2048
        MAX_SEQ = input_tensor.shape[1] + MAX_NEW + 10

        for token in model.generate_with_cache(
            token_ids=input_tensor,
            max_new_tokens=MAX_NEW,
            eos_token_id=tokenizer.eos_token_id,
            max_seq_len=MAX_SEQ,
            use_turbo_quant=False
        ):
            token_id = token.squeeze(0).tolist()
            word = tokenizer.decode(token_id)
            full_response += word

    spinner.stop("Recon plan generated", success=True)
    return full_response


def parse_plan(raw: str) -> dict:
    clean = raw.strip()
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if match:
        clean = match.group(0)
    plan = json.loads(clean)
    with open("recon_output.json", "w") as f:
        json.dump(plan, f, indent=4)
    return plan


import subprocess
import shutil
from datetime import datetime


def run_cmd(cmd, timeout=30):
    tool = cmd[0]
    if not shutil.which(tool):
        return f"{RD}[!] '{tool}' not found. Install it to use this module.{R}"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr:
            output += f"\n{DIM}[stderr] {result.stderr.strip()}{R}"
        return output if output else f"{DIM}(no output){R}"
    except subprocess.TimeoutExpired:
        return f"{RD}[!] Timed out after {timeout}s{R}"
    except Exception as e:
        return f"{RD}[!] Error: {e}{R}"


def kv(label, value, val_color=None):
    """Print a clean key-value row."""
    vc = val_color or SILVR
    print(f"  {GRAY}{'·'} {DIM}{label:<20}{R}  {vc}{B}{value}{R}")

def kv_str(label, value, val_color=None):
    """Return a key-value row as string (for raw output storage)."""
    return f"  · {label:<20}  {value}"

def run_whois(t):
    raw = run_cmd(["whois", t])
    if raw.startswith(f"{RD}"):
        return raw
    extract = {
        "Registrar":        None,
        "Registrant Org":   None,
        "Registrant Email": None,
        "Created":          None,
        "Updated":          None,
        "Expires":          None,
        "Name Servers":     [],
        "Domain Status":    [],
        "DNSSEC":           None,
    }
    for line in raw.splitlines():
        l = line.strip()
        low = l.lower()
        if low.startswith("registrar:") and not extract["Registrar"]:
            extract["Registrar"] = l.split(":",1)[1].strip()
        elif low.startswith("registrant organization:") and not extract["Registrant Org"]:
            extract["Registrant Org"] = l.split(":",1)[1].strip()
        elif low.startswith("registrant email:") and not extract["Registrant Email"]:
            extract["Registrant Email"] = l.split(":",1)[1].strip()
        elif ("creation date:" in low or "created:" in low) and not extract["Created"]:
            extract["Created"] = l.split(":",1)[1].strip()[:10]
        elif ("updated date:" in low or "updated:" in low) and not extract["Updated"]:
            extract["Updated"] = l.split(":",1)[1].strip()[:10]
        elif ("expir" in low and "date" in low or "expiry" in low) and not extract["Expires"]:
            extract["Expires"] = l.split(":",1)[1].strip()[:10]
        elif low.startswith("name server:"):
            ns = l.split(":",1)[1].strip().lower()
            if ns and ns not in extract["Name Servers"]:
                extract["Name Servers"].append(ns)
        elif low.startswith("domain status:"):
            status = l.split(":",1)[1].strip().split(" ")[0]
            if status and status not in extract["Domain Status"]:
                extract["Domain Status"].append(status)
        elif low.startswith("dnssec:") and not extract["DNSSEC"]:
            extract["DNSSEC"] = l.split(":",1)[1].strip()

    lines = []
    simple_fields = ["Registrar","Registrant Org","Registrant Email","Created","Updated","Expires","DNSSEC"]
    for f in simple_fields:
        v = extract[f]
        if v:
            lines.append(kv_str(f, v))
            print(f"  {GRAY}·{R} {DIM}{f:<20}{R}  {SILVR}{B}{v}{R}")
    if extract["Name Servers"]:
        print(f"  {GRAY}·{R} {DIM}{'Name Servers':<20}{R}  {SILVR}{B}{extract['Name Servers'][0]}{R}")
        lines.append(kv_str("Name Servers", extract['Name Servers'][0]))
        for ns in extract["Name Servers"][1:]:
            print(f"  {GRAY}  {'':20}   {PURPL}{ns}{R}")
            lines.append(f"  {'':22}  {ns}")
    if extract["Domain Status"]:
        for s in extract["Domain Status"][:3]:
            print(f"  {GRAY}·{R} {DIM}{'Status':<20}{R}  {ROSE}{s}{R}")
            lines.append(kv_str("Status", s))
    return "\n".join(lines)

def run_dig(domain):
    record_colors = {"A": CRIMN, "MX": PURPL, "NS": PURP, "TXT": ROSE}
    all_lines = []
    for rec in ["A", "MX", "NS", "TXT"]:
        out = run_cmd(["dig", "+short", domain, rec])
        if not out or "(no output)" in out or out.startswith(f"{RD}"):
            continue
        col = record_colors.get(rec, SILVR)
        print(f"  {GRAY}·{R} {DIM}{(rec + ' Records'):<20}{R}  ", end="")
        entries = [e.strip() for e in out.strip().splitlines() if e.strip()]
        if not entries:
            continue
        print(f"{col}{B}{entries[0]}{R}")
        all_lines.append(kv_str(f"{rec} Records", entries[0]))
        for e in entries[1:6]:
            print(f"  {GRAY}  {'':20}   {col}{e}{R}")
            all_lines.append(f"  {'':22}  {e}")
    return "\n".join(all_lines) if all_lines else f"{DIM}  (no results){R}"

def run_whatweb(t):
    raw = run_cmd(["whatweb", "-v", t], timeout=45)
    if raw.startswith(f"{RD}"):
        return raw

    lines_out = []
    blocks = re.split(r'WhatWeb report for ', raw)
    for block in blocks:
        if not block.strip():
            continue
        first_line = block.splitlines()[0].strip()
        url_target = first_line.rstrip()

        status_m  = re.search(r'Status\s*:\s*(.+)',  block)
        title_m   = re.search(r'Title\s*:\s*(.+)',   block)
        ip_m      = re.search(r'IP\s*:\s*(.+)',      block)
        country_m = re.search(r'Country\s*:\s*(.+)', block)
        server_m  = re.search(r'HTTPServer\]\s*\n.*?String\s*:\s*(.+)', block)
        summary_m = re.search(r'Summary\s*:\s*(.+)', block)

        status  = status_m.group(1).strip()  if status_m  else "N/A"
        title   = title_m.group(1).strip()   if title_m   else "N/A"
        ip      = ip_m.group(1).strip()      if ip_m      else "N/A"
        country = country_m.group(1).strip() if country_m else "N/A"
        server  = server_m.group(1).strip()  if server_m  else "N/A"

        status_col = G if "200" in status else (Y if "30" in status else RD)

        print(f"  {GRAY}·{R} {DIM}{'URL':<20}{R}  {PURPL}{B}{url_target}{R}")
        print(f"  {GRAY}·{R} {DIM}{'Status':<20}{R}  {status_col}{B}{status}{R}")
        if title and title != "<None>":
            print(f"  {GRAY}·{R} {DIM}{'Title':<20}{R}  {SILVR}{title}{R}")
        if ip != "N/A":
            print(f"  {GRAY}·{R} {DIM}{'IP':<20}{R}  {CRIMN}{ip}{R}")
        if country != "N/A":
            print(f"  {GRAY}·{R} {DIM}{'Country':<20}{R}  {SILVR}{country}{R}")
        if server != "N/A":
            print(f"  {GRAY}·{R} {DIM}{'Server':<20}{R}  {ROSE}{server}{R}")

        if summary_m:
            plugins = [p.strip() for p in summary_m.group(1).split(",")]
            notable = [p for p in plugins if any(k in p.lower() for k in
                       ["cookie","frame","security","hsts","cloudflare","server",
                        "jquery","wordpress","php","nginx","apache","redirect"])]
            if notable:
                print(f"  {GRAY}·{R} {DIM}{'Detected':<20}{R}  {PURP}{', '.join(notable[:6])}{R}")

        print()
        lines_out.append(f"URL: {url_target} | Status: {status} | IP: {ip} | Server: {server}")

    return "\n".join(lines_out) if lines_out else raw

def run_traceroute(t):
    raw = run_cmd(["traceroute", "-m", "15", t], timeout=60)
    if raw.startswith(f"{RD}"):
        return raw
    lines_out = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        print(f"  {GRAY}│{R}  {SILVR}{stripped}{R}")
        lines_out.append(stripped)
    return "\n".join(lines_out)

def run_nmap(t):
    raw = run_cmd(["nmap", "-T4", "--top-ports", "100", "-sV", t], timeout=120)
    if raw.startswith(f"{RD}"):
        return raw
    lines_out = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "/tcp" in s or "/udp" in s:
            parts = s.split()
            port  = parts[0] if parts else s
            state = parts[1] if len(parts) > 1 else ""
            rest  = " ".join(parts[2:]) if len(parts) > 2 else ""
            col   = G if state == "open" else (RD if state == "closed" else Y)
            print(f"  {GRAY}·{R}  {col}{B}{port:<20}{R}  {DIM}{state:<10}{R}  {SILVR}{rest}{R}")
        else:
            print(f"  {GRAY}{DIM}{s}{R}")
        lines_out.append(s)
    return "\n".join(lines_out)

def run_curl_headers(t):
    raw = run_cmd(["curl", "-sI", "--max-time", "10", t], timeout=15)
    if raw.startswith(f"{RD}"):
        return raw
    INTERESTING = {"server","content-type","x-powered-by","strict-transport-security",
                   "x-frame-options","content-security-policy","location",
                   "x-content-type-options","cf-ray","via","set-cookie"}
    lines_out = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            if k.lower().strip() in INTERESTING:
                print(f"  {GRAY}·{R} {DIM}{k.strip():<30}{R}  {SILVR}{v.strip()[:80]}{R}")
                lines_out.append(f"{k.strip()}: {v.strip()}")
        elif line.startswith("HTTP"):
            col = G if "200" in line else (Y if "30" in line else RD)
            print(f"  {col}{B}{line.strip()}{R}")
            lines_out.append(line.strip())
    return "\n".join(lines_out)

def run_ipinfo(t):
    out = run_cmd(["curl", "-s", f"https://ipinfo.io/{t}/json"], timeout=15)
    try:
        d = json.loads(out)
        field_colors = {
            "ip": CRIMN, "hostname": SILVR, "city": SILVR,
            "region": SILVR, "country": ROSE, "org": PURPL, "timezone": GRAY
        }
        lines = []
        for k in ["ip","hostname","city","region","country","org","timezone"]:
            if k in d:
                col = field_colors.get(k, SILVR)
                print(f"  {GRAY}·{R} {DIM}{k:<20}{R}  {col}{B}{d[k]}{R}")
                lines.append(kv_str(k, d[k]))
        return "\n".join(lines)
    except Exception:
        return out

def run_abuseipdb(ip):
    key = os.environ.get("ABUSEIPDB_KEY", "")
    if not key:
        return f"{Y}[!] Set ABUSEIPDB_KEY env var  →  https://www.abuseipdb.com/register{R}"
    out = run_cmd(["curl","-s","-G","https://api.abuseipdb.com/api/v2/check",
                   "--data-urlencode",f"ipAddress={ip}","-d","maxAgeInDays=90",
                   "-H",f"Key: {key}","-H","Accept: application/json"], timeout=15)
    try:
        d = json.loads(out).get("data", {})
        s = d.get("abuseConfidenceScore", "N/A")
        col = RD if isinstance(s, int) and s > 50 else G
        return (f"  {DIM}IP:{R}           {d.get('ipAddress','N/A')}\n"
                f"  {DIM}Abuse Score:{R}  {col}{s}%{R}\n"
                f"  {DIM}Reports:{R}      {d.get('totalReports',0)}\n"
                f"  {DIM}Country:{R}      {d.get('countryCode','N/A')}\n"
                f"  {DIM}ISP:{R}          {d.get('isp','N/A')}")
    except Exception:
        return out

def run_virustotal(target, target_type):
    key = os.environ.get("VIRUSTOTAL_KEY", "")
    if not key:
        return f"{Y}[!] Set VIRUSTOTAL_KEY env var  →  https://www.virustotal.com{R}"
    if target_type   == "hash":   url = f"https://www.virustotal.com/api/v3/files/{target}"
    elif target_type == "domain": url = f"https://www.virustotal.com/api/v3/domains/{target}"
    elif target_type == "ip":     url = f"https://www.virustotal.com/api/v3/ip_addresses/{target}"
    else:
        import base64
        enc = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        url = f"https://www.virustotal.com/api/v3/urls/{enc}"
    out = run_cmd(["curl","-s",url,"-H",f"x-apikey: {key}"], timeout=15)
    try:
        stats = json.loads(out)["data"]["attributes"]["last_analysis_stats"]
        mal, sus, har = stats.get("malicious",0), stats.get("suspicious",0), stats.get("harmless",0)
        col = RD if mal > 0 else (Y if sus > 0 else G)
        return (f"  {DIM}Malicious:{R}   {col}{mal}{R}\n"
                f"  {DIM}Suspicious:{R}  {Y}{sus}{R}\n"
                f"  {DIM}Harmless:{R}    {G}{har}{R}")
    except Exception:
        return out

def run_urlscan(target):
    key = os.environ.get("URLSCAN_KEY", "")
    if not key:
        return (f"{Y}[!] Set URLSCAN_KEY for submissions  →  https://urlscan.io\n"
                f"    Searching existing results...{R}\n\n"
                + run_cmd(["curl","-s",
                           f"https://urlscan.io/api/v1/search/?q=domain:{target}&size=3"],
                          timeout=15))
    out = run_cmd(["curl","-s","-X","POST","https://urlscan.io/api/v1/scan/",
                   "-H",f"API-Key: {key}","-H","Content-Type: application/json",
                   "-d",json.dumps({"url": target, "visibility": "public"})], timeout=15)
    try:
        d = json.loads(out)
        return f"  {G}Submitted!{R}  Result: {d.get('result','N/A')}"
    except Exception:
        return out

def run_shodan(ip):
    key = os.environ.get("SHODAN_KEY", "")
    if not key:
        return f"{Y}[!] Set SHODAN_KEY env var  →  https://account.shodan.io/register{R}"
    out = run_cmd(["curl","-s",f"https://api.shodan.io/shodan/host/{ip}?key={key}"], timeout=15)
    try:
        d = json.loads(out)
        ports = [str(s.get("port")) for s in d.get("data", [])]
        return (f"  {DIM}Org:{R}    {d.get('org','N/A')}\n"
                f"  {DIM}OS:{R}     {d.get('os','N/A')}\n"
                f"  {DIM}Country:{R} {d.get('country_name','N/A')}\n"
                f"  {DIM}Ports:{R}  {', '.join(ports) or 'N/A'}")
    except Exception:
        return out


TOOL_DISPATCH = {
    "whois":        lambda p: run_whois(p["domain"] or p["ip"]),
    "dig":          lambda p: run_dig(p["domain"]),
    "whatweb":      lambda p: run_whatweb(p["url"] or f"http://{p['domain']}"),
    "nmap":         lambda p: run_nmap(p["ip"] or p["domain"]),
    "traceroute":   lambda p: run_traceroute(p["ip"] or p["domain"]),
    "curl_headers": lambda p: run_curl_headers(p["url"] or f"http://{p['domain']}"),
    "ipinfo":       lambda p: run_ipinfo(p["ip"] or p["domain"]),
    "abuseipdb":    lambda p: run_abuseipdb(p["ip"]),
    "virustotal":   lambda p: run_virustotal(
                        p["hash"] or p["url"] or p["domain"] or p["ip"],
                        p["target_type"]),
    "urlscan":      lambda p: run_urlscan(p["url"] or p["domain"]),
    "shodan":       lambda p: run_shodan(p["ip"]),
}


def _sanitise(obj):
    """Recursively strip ANSI codes from all strings in a result tree."""
    ansi_re = re.compile(r'\033\[[0-9;]*m')
    if isinstance(obj, str):
        return ansi_re.sub("", obj).strip()
    if isinstance(obj, dict):
        return {k: _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitise(i) for i in obj]
    return obj


def run_recon(plan: dict):
    from datetime import timezone

    target_display = (plan.get("domain") or plan.get("ip") or
                      plan.get("url")    or plan.get("hash") or "Unknown")
    target_type    = plan.get("target_type", "unknown")
    tools          = plan.get("tools", [])
    W              = 60
    started        = datetime.now(timezone.utc)

    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{PURP}{B}╔{'═'*(W-2)}╗{R}")
    print(f"{PURP}{B}║  {'RECON REPORT':^{W-4}}  ║{R}")
    print(f"{PURP}{B}╠{'═'*(W-2)}╣{R}")
    print(f"{PURP}{B}║{R}  {DIM}Target  {R}  {CRIMN}{B}{target_display:<{W-12}}{R}{PURP}{B}║{R}")
    print(f"{PURP}{B}║{R}  {DIM}Type    {R}  {ROSE}{target_type.upper():<{W-12}}{R}{PURP}{B}║{R}")
    print(f"{PURP}{B}║{R}  {DIM}Tools   {R}  {SILVR}{', '.join(tools)[:W-12]:<{W-12}}{R}{PURP}{B}║{R}")
    print(f"{PURP}{B}║{R}  {DIM}Started {R}  {GRAY}{started.strftime('%Y-%m-%d  %H:%M:%S UTC'):<{W-12}}{R}{PURP}{B}║{R}")
    print(f"{PURP}{B}╚{'═'*(W-2)}╝{R}")

    tool_results: dict = {}

    for tool in tools:
        pad = W - 6 - len(tool)
        print(f"\n{BLOOD}{B}┌─▸ {tool.upper()} {'─'*max(pad,0)}┐{R}")

        runner = TOOL_DISPATCH.get(tool)
        if runner is None:
            print(f"  {Y}[?] No runner implemented for '{tool}'{R}")
            print(f"{BLOOD}{DIM}└{'─'*(W-2)}┘{R}")
            tool_results[tool] = {"error": "no runner implemented"}
            continue

        spinner = Spinner(f"  running {tool}", GRAY)
        spinner.start()
        try:
            output = runner(plan)
            spinner.stop(f"  {tool} complete", success=True)
            if isinstance(output, dict):
                for k, v in output.items():
                    if k == "raw":
                        continue
                    val = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
                    print(f"  {DIM}{k:<20}{R}  {SILVR}{val[:80]}{R}")
            elif output:
                for line in str(output).splitlines():
                    if line.strip():
                        print(f"  {SILVR}{line}{R}")
            tool_results[tool] = output if isinstance(output, dict) else {"output": str(output)}
        except Exception as e:
            spinner.stop(f"  {tool} failed", success=False)
            print(f"  {RD}[!] {e}{R}")
            tool_results[tool] = {"error": str(e)}

        print(f"{BLOOD}{DIM}└{'─'*(W-2)}┘{R}")

    finished = datetime.now(timezone.utc)
    report = {
        "meta": {
            "target":       target_display,
            "target_type":  target_type,
            "tools_run":    tools,
            "started_utc":  started.isoformat(),
            "finished_utc": finished.isoformat(),
            "duration_s":   round((finished - started).total_seconds(), 2),
        },
        "results": _sanitise(tool_results),
    }

    safe_name   = re.sub(r'[^\w\-.]', '_', target_display)
    os.makedirs("reports", exist_ok=True)
    report_file = os.path.join("reports", f"report_{safe_name}.json")
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print(f"\n{PURP}{B}╔{'═'*(W-2)}╗{R}")
    print(f"{PURP}{B}║{R}  {PURPL}✔{R}  {SILVR}Report saved  →  {B}{report_file}{R}{' '*(max(W-22-len(report_file),0))}{PURP}{B}║{R}")
    print(f"{PURP}{B}╚{'═'*(W-2)}╝{R}\n")


if __name__ == "__main__":
    print_logo()

    try:
        model, tokenizer, device, torch = load_model_silently()
    except Exception as e:
        print(f"\n{RD}[!] Failed to load model: {e}{R}")
        sys.exit(1)

    print()
    print(f"{DIM}{'─'*50}{R}")
    print(f"{C}{B}  Enter a target to investigate:{R}")
    print(f"{DIM}  (IP address, domain, URL, or file hash){R}")
    print(f"{DIM}{'─'*50}{R}")
    prompt = input(f"{M}{B}  ❯  {R}").strip()

    if not prompt:
        print(f"{RD}[!] No input provided. Exiting.{R}")
        sys.exit(1)

    print()

    try:
        raw_output = run_inference_silent(model, tokenizer, device, torch, prompt)
    except Exception as e:
        print(f"\n{RD}[!] Inference failed: {e}{R}")
        sys.exit(1)

    try:
        plan = parse_plan(raw_output)
        print(f"{G}✔  Plan parsed successfully{R}")
        print(f"{DIM}  → recon_output.json written{R}\n")
    except json.JSONDecodeError as e:
        print(f"\n{RD}[!] Model returned invalid JSON: {e}{R}")
        print(f"{DIM}Raw output:\n{raw_output}{R}")
        sys.exit(1)

    run_recon(plan)