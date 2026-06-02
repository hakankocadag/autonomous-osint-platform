import subprocess
import json
import sys
import os
import re
import shutil
from datetime import datetime, timezone

R    = "\033[0m"
B    = "\033[1m"
DIM  = "\033[2m"
C    = "\033[38;5;129m"
G    = "\033[38;5;141m"
Y    = "\033[38;5;204m"
RD   = "\033[38;5;160m"
M    = "\033[38;5;196m"
GRAY = "\033[38;5;240m"
SILV = "\033[38;5;250m"

ANSI_RE = re.compile(r'\033\[[0-9;]*m')

def _strip(text: str) -> str:
    """Remove ANSI escape codes."""
    return ANSI_RE.sub("", text).strip()



def banner(text: str, color: str = C):
    w = 62
    print(f"\n{color}{B}{'═' * w}{R}")
    print(f"{color}{B}  {text}{R}")
    print(f"{color}{B}{'═' * w}{R}")

def section(tool_name: str):
    pad = max(52 - len(tool_name), 0)
    print(f"\n{RD}{B}┌─▸ {tool_name.upper()} {'─' * pad}┐{R}")

def section_end():
    print(f"{RD}{DIM}└{'─' * 60}┘{R}")

def _kv(label: str, value: str, label_color: str = DIM, val_color: str = SILV):
    print(f"  {label_color}{label:<20}{R}  {val_color}{B}{value}{R}")



def run_cmd(cmd: list[str], timeout: int = 30) -> str:
    tool = cmd[0]
    if not shutil.which(tool):
        return f"ERROR: '{tool}' not found on this system."
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = result.stdout.strip()
        if result.returncode != 0 and result.stderr:
            out += f"\n[stderr] {result.stderr.strip()}"
        return out if out else "(no output)"
    except subprocess.TimeoutExpired:
        return f"ERROR: timed out after {timeout}s"
    except Exception as e:
        return f"ERROR: {e}"



def run_whois(target: str) -> dict:
    raw = run_cmd(["whois", target])
    parsed: dict = {}
    patterns = {
        "registrar":       r"(?i)registrar:\s*(.+)",
        "creation_date":   r"(?i)creation date:\s*(.+)",
        "expiry_date":     r"(?i)expir\w+ date:\s*(.+)",
        "updated_date":    r"(?i)updated date:\s*(.+)",
        "name_servers":    r"(?i)name server:\s*(.+)",
        "registrant_org":  r"(?i)registrant organization:\s*(.+)",
        "registrant_country": r"(?i)registrant country:\s*(.+)",
        "status":          r"(?i)domain status:\s*(.+)",
    }
    for key, pat in patterns.items():
        hits = re.findall(pat, raw)
        if hits:
            parsed[key] = [h.strip() for h in hits] if len(hits) > 1 else hits[0].strip()

    for k, v in parsed.items():
        val = ", ".join(v) if isinstance(v, list) else v
        _kv(k, val)
    if not parsed:
        print(f"  {DIM}{raw[:500]}{R}")

    return {"raw": raw, "parsed": parsed}


def run_dig(domain: str) -> dict:
    record_types = ["A", "MX", "NS", "TXT", "AAAA"]
    result: dict = {}
    for rtype in record_types:
        out = run_cmd(["dig", "+short", domain, rtype])
        if out and "(no output)" not in out and "ERROR" not in out:
            result[rtype] = [line.strip() for line in out.splitlines() if line.strip()]
            print(f"  {DIM}{rtype} records:{R}")
            for rec in result[rtype]:
                print(f"    {SILV}{rec}{R}")
    return result


def run_whatweb(target: str) -> dict:
    raw = run_cmd(["whatweb", "-v", target], timeout=45)
    parsed: dict = {"technologies": [], "server": None, "cms": None, "raw": raw}

    tech_hits = re.findall(r'\[([^\[\]]+)\]', raw)
    seen = set()
    for hit in tech_hits:
        for item in hit.split(","):
            t = item.strip().split("[")[0].strip()
            if t and t not in seen and len(t) > 1:
                seen.add(t)
                parsed["technologies"].append(t)

    for line in raw.splitlines():
        if re.search(r'(?i)apache|nginx|iis|caddy|lighttpd', line):
            m = re.search(r'(?i)(apache|nginx|iis|caddy|lighttpd)[^\s,\]]*', line)
            if m:
                parsed["server"] = m.group(0)
        if re.search(r'(?i)wordpress|joomla|drupal|magento', line):
            m = re.search(r'(?i)(wordpress|joomla|drupal|magento)[^\s,\]]*', line)
            if m:
                parsed["cms"] = m.group(0)

    _kv("server",       parsed["server"] or "unknown")
    _kv("cms",          parsed["cms"]    or "unknown")
    _kv("technologies", ", ".join(parsed["technologies"][:10]) or "none detected")
    return parsed


def run_nmap(target: str) -> dict:
    raw = run_cmd(["nmap", "-T4", "--top-ports", "100", "-sV", target], timeout=120)
    ports: list[dict] = []
    for line in raw.splitlines():
        m = re.match(
            r'(\d+)/(tcp|udp)\s+(open|closed|filtered)\s+(\S+)(?:\s+(.+))?',
            line.strip()
        )
        if m:
            entry = {
                "port":     int(m.group(1)),
                "protocol": m.group(2),
                "state":    m.group(3),
                "service":  m.group(4),
                "version":  (m.group(5) or "").strip() or None,
            }
            ports.append(entry)
            col = G if entry["state"] == "open" else GRAY
            _kv(
                f"{entry['port']}/{entry['protocol']}",
                f"{entry['state']}  {entry['service']}  {entry['version'] or ''}",
                val_color=col
            )
    if not ports:
        print(f"  {DIM}{raw[:600]}{R}")
    return {"ports": ports, "raw": raw}


def run_traceroute(target: str) -> dict:
    raw = run_cmd(["traceroute", "-m", "15", target], timeout=60)
    hops: list[dict] = []
    for line in raw.splitlines():
        m = re.match(r'\s*(\d+)\s+([\d.*]+(?:\s+[\d.*]+)*)\s*(.*)', line)
        if m:
            hop = {
                "hop": int(m.group(1)),
                "address": m.group(2).split()[0],
                "info": m.group(3).strip() or None,
            }
            hops.append(hop)
            _kv(f"hop {hop['hop']}", f"{hop['address']}  {hop['info'] or ''}")
    if not hops:
        print(f"  {DIM}{raw[:600]}{R}")
    return {"hops": hops, "raw": raw}


def run_curl_headers(url: str) -> dict:
    raw = run_cmd(["curl", "-sI", "--max-time", "10", url], timeout=15)
    headers: dict = {}
    status_code: int | None = None
    for i, line in enumerate(raw.splitlines()):
        if i == 0:
            m = re.match(r'HTTP/[\d.]+ (\d+)', line)
            if m:
                status_code = int(m.group(1))
                col = G if status_code < 400 else RD
                _kv("status", str(status_code), val_color=col)
        elif ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
            _kv(k.strip().lower(), v.strip())
    return {"status_code": status_code, "headers": headers}


def run_ipinfo(ip_or_domain: str) -> dict:
    raw = run_cmd(["curl", "-s", f"https://ipinfo.io/{ip_or_domain}/json"], timeout=15)
    try:
        data = json.loads(raw)
        fields = ["ip", "hostname", "city", "region", "country", "org", "timezone"]
        result = {k: data[k] for k in fields if k in data}
        for k, v in result.items():
            _kv(k, v)
        return result
    except Exception:
        print(f"  {DIM}{raw[:400]}{R}")
        return {"raw": raw}


def run_abuseipdb(ip: str) -> dict:
    api_key = os.environ.get("ABUSEIPDB_KEY", "")
    if not api_key:
        msg = "ABUSEIPDB_KEY not set — https://www.abuseipdb.com/register"
        print(f"  {Y}[!] {msg}{R}")
        return {"error": msg}
    raw = run_cmd([
        "curl", "-s", "-G", "https://api.abuseipdb.com/api/v2/check",
        "--data-urlencode", f"ipAddress={ip}",
        "-d", "maxAgeInDays=90",
        "-H", f"Key: {api_key}",
        "-H", "Accept: application/json"
    ], timeout=15)
    try:
        d = json.loads(raw).get("data", {})
        score = d.get("abuseConfidenceScore", None)
        col = RD if isinstance(score, int) and score > 50 else G
        result = {
            "ip":             d.get("ipAddress"),
            "abuse_score":    score,
            "total_reports":  d.get("totalReports"),
            "country":        d.get("countryCode"),
            "isp":            d.get("isp"),
            "is_whitelisted": d.get("isWhitelisted"),
        }
        _kv("ip",            result["ip"] or "N/A")
        _kv("abuse_score",   f"{score}%", val_color=col)
        _kv("total_reports", str(result["total_reports"]))
        _kv("country",       result["country"] or "N/A")
        _kv("isp",           result["isp"] or "N/A")
        return result
    except Exception as e:
        return {"error": str(e), "raw": raw}


def run_virustotal(target: str, target_type: str) -> dict:
    api_key = os.environ.get("VIRUSTOTAL_KEY", "")
    if not api_key:
        msg = "VIRUSTOTAL_KEY not set — https://www.virustotal.com"
        print(f"  {Y}[!] {msg}{R}")
        return {"error": msg}
    if target_type == "hash":
        url = f"https://www.virustotal.com/api/v3/files/{target}"
    elif target_type == "domain":
        url = f"https://www.virustotal.com/api/v3/domains/{target}"
    elif target_type == "ip":
        url = f"https://www.virustotal.com/api/v3/ip_addresses/{target}"
    else:
        import base64
        enc = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
        url = f"https://www.virustotal.com/api/v3/urls/{enc}"
    raw = run_cmd(["curl", "-s", url, "-H", f"x-apikey: {api_key}"], timeout=15)
    try:
        stats = json.loads(raw)["data"]["attributes"]["last_analysis_stats"]
        mal = stats.get("malicious", 0)
        sus = stats.get("suspicious", 0)
        har = stats.get("harmless", 0)
        und = stats.get("undetected", 0)
        col = RD if mal > 0 else (Y if sus > 0 else G)
        result = {
            "malicious":  mal,
            "suspicious": sus,
            "harmless":   har,
            "undetected": und,
        }
        _kv("malicious",  str(mal), val_color=col)
        _kv("suspicious", str(sus), val_color=Y if sus > 0 else SILV)
        _kv("harmless",   str(har), val_color=G)
        _kv("undetected", str(und))
        return result
    except Exception as e:
        return {"error": str(e), "raw": raw}


def run_urlscan(target: str) -> dict:
    api_key = os.environ.get("URLSCAN_KEY", "")
    if not api_key:
        msg = "URLSCAN_KEY not set — https://urlscan.io/user/signup"
        print(f"  {Y}[!] {msg}{R}")
        raw = run_cmd([
            "curl", "-s",
            f"https://urlscan.io/api/v1/search/?q=domain:{target}&size=3"
        ], timeout=15)
        try:
            data = json.loads(raw)
            results_list = data.get("results", [])
            summary = [
                {
                    "url":    r.get("page", {}).get("url"),
                    "domain": r.get("page", {}).get("domain"),
                    "ip":     r.get("page", {}).get("ip"),
                    "status": r.get("page", {}).get("status"),
                    "scan":   r.get("result"),
                }
                for r in results_list
            ]
            for s in summary:
                _kv("url", s["url"] or "N/A")
            return {"mode": "search", "results": summary}
        except Exception:
            return {"mode": "search", "raw": raw}
    raw = run_cmd([
        "curl", "-s", "-X", "POST", "https://urlscan.io/api/v1/scan/",
        "-H", f"API-Key: {api_key}",
        "-H", "Content-Type: application/json",
        "-d", json.dumps({"url": target, "visibility": "public"})
    ], timeout=15)
    try:
        d = json.loads(raw)
        result = {
            "mode":       "submitted",
            "result_url": d.get("result"),
            "uuid":       d.get("uuid"),
        }
        _kv("result_url", result["result_url"] or "N/A", val_color=G)
        return result
    except Exception as e:
        return {"mode": "submitted", "error": str(e), "raw": raw}


def run_shodan(ip: str) -> dict:
    api_key = os.environ.get("SHODAN_KEY", "")
    if not api_key:
        msg = "SHODAN_KEY not set — https://account.shodan.io/register"
        print(f"  {Y}[!] {msg}{R}")
        return {"error": msg}
    raw = run_cmd([
        "curl", "-s",
        f"https://api.shodan.io/shodan/host/{ip}?key={api_key}"
    ], timeout=15)
    try:
        d = json.loads(raw)
        ports = [s.get("port") for s in d.get("data", []) if s.get("port")]
        result = {
            "organization": d.get("org"),
            "os":           d.get("os"),
            "country":      d.get("country_name"),
            "open_ports":   ports,
            "hostnames":    d.get("hostnames", []),
            "tags":         d.get("tags", []),
        }
        _kv("organization", result["organization"] or "N/A")
        _kv("os",           result["os"] or "N/A")
        _kv("country",      result["country"] or "N/A")
        _kv("open_ports",   ", ".join(str(p) for p in ports) or "N/A")
        return result
    except Exception as e:
        return {"error": str(e), "raw": raw}


# ── Dispatch table ─────────────────────────────────────────────────────────────

TOOL_DISPATCH: dict = {
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
                        p["target_type"]
                    ),
    "urlscan":      lambda p: run_urlscan(p["url"] or p["domain"]),
    "shodan":       lambda p: run_shodan(p["ip"]),
}


# ── Main entry ─────────────────────────────────────────────────────────────────

def main():
    json_file = "recon_output.json"
    if not os.path.exists(json_file):
        print(f"{RD}[!] '{json_file}' not found. Run main.py first.{R}")
        sys.exit(1)

    with open(json_file) as f:
        plan = json.load(f)

    target   = (plan.get("domain") or plan.get("ip") or
                plan.get("url")    or plan.get("hash") or "Unknown")
    t_type   = plan.get("target_type", "unknown")
    tools    = plan.get("tools", [])
    started  = datetime.now(timezone.utc)

    banner(f"OSINT RECON  ·  {target}", C)
    _kv("target",      target, val_color=M)
    _kv("type",        t_type.upper(), val_color=Y)
    _kv("tools",       ", ".join(tools), val_color=SILV)
    _kv("started",     started.strftime("%Y-%m-%d  %H:%M:%S UTC"), val_color=GRAY)

    # ── Run every tool, collect structured results ────────────────────────────
    tool_results: dict = {}

    for tool in tools:
        section(tool)
        runner = TOOL_DISPATCH.get(tool)
        if runner is None:
            print(f"  {Y}[?] No runner for '{tool}'{R}")
            section_end()
            tool_results[tool] = {"error": "no runner implemented"}
            continue
        try:
            data = runner(plan)
            tool_results[tool] = data if isinstance(data, dict) else {"output": data}
        except Exception as e:
            print(f"  {RD}[!] {e}{R}")
            tool_results[tool] = {"error": str(e)}
        section_end()

    # ── Build the JSON report ─────────────────────────────────────────────────
    finished = datetime.now(timezone.utc)
    report = {
        "meta": {
            "target":       target,
            "target_type":  t_type,
            "tools_run":    tools,
            "started_utc":  started.isoformat(),
            "finished_utc": finished.isoformat(),
            "duration_s":   round((finished - started).total_seconds(), 2),
        },
        "results": tool_results,
    }

    # Sanitise: strip any leftover ANSI from string values (e.g. raw fields)
    def sanitise(obj):
        if isinstance(obj, str):
            return _strip(obj)
        if isinstance(obj, dict):
            return {k: sanitise(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitise(i) for i in obj]
        return obj

    report = sanitise(report)

    safe_name   = re.sub(r'[^\w\-.]', '_', target)
    report_file = f"reports/report_{safe_name}.json"
    
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    banner(f"Report saved  →  {report_file}", G)
    _kv("tools ran", str(len(tools)), val_color=G)
    _kv("duration",  f"{report['meta']['duration_s']}s", val_color=SILV)
    print()


if __name__ == "__main__":
    main()