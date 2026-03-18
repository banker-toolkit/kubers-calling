"""
KUBERS CALLING — rca/rca_agent.py
===================================
Autonomous Root Cause Analysis agent.

Run standalone:
    python rca/rca_agent.py --type DIVERGENCE --tickers BANDHANBNK,IRB

Called programmatically:
    from rca.rca_agent import RCAAgent, make_incident
    agent = RCAAgent()
    result = agent.investigate(make_incident("DIVERGENCE", ["BANDHANBNK"], {}))
"""

import os, sys, json, sqlite3, logging, re
from datetime import datetime
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
log = logging.getLogger("rca_agent")

RCA_DIR    = os.path.dirname(os.path.abspath(__file__))
KB_PATH    = os.path.join(RCA_DIR, "knowledge_base.json")
INC_PATH   = os.path.join(RCA_DIR, "incident_log.json")
FIX_DIR    = os.path.join(RCA_DIR, "proposed_fixes")
KUBERS_DIR = os.path.dirname(RCA_DIR)

try:
    from config import DB_LIVE_PATH
except ImportError:
    DB_LIVE_PATH = os.path.join(KUBERS_DIR, "database", "kubers_live.db")

CLAUDE_API           = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL         = "claude-sonnet-4-20250514"
MAX_ROUNDS           = 5
CONFIDENCE_THRESHOLD = 0.90


def make_incident(incident_type, tickers, evidence):
    return {
        "id":            f"{incident_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "type":          incident_type,
        "tickers":       tickers,
        "timestamp":     datetime.now().isoformat(),
        "evidence":      evidence,
        "status":        "open",
        "rca":           None,
        "confidence":    0.0,
        "rounds":        [],
        "pattern_match": None,
        "proposed_fix":  None,
        "affected_file": None,
        "approved":      False,
    }


class RCAAgent:

    def __init__(self):
        self.kb        = self._load_kb()
        self.incidents = self._load_incidents()
        os.makedirs(FIX_DIR, exist_ok=True)

    # ── MAIN ──────────────────────────────────────────────────────────

    def investigate(self, incident):
        log.info("[rca] Investigating: %s", incident["id"])
        incident["status"] = "investigating"

        match = self._pattern_match(incident)
        if match:
            log.info("[rca] PATTERN MATCH: %s (%.0f%%)", match["id"], match["confidence"]*100)
            incident.update({
                "pattern_match": match["id"],
                "rca":           match["root_cause"],
                "confidence":    match["confidence"],
                "status":        "pattern_matched",
                "proposed_fix":  f"[FIXED {match.get('fix_date','?')}] {match.get('fix_applied','')}",
            })
            self._save_incident(incident)
            return incident

        log.info("[rca] No pattern match — gathering evidence for Claude interrogation")
        evidence = self._gather_evidence(incident)
        incident = self._interrogate(incident, evidence)
        self._save_incident(incident)
        if incident["confidence"] >= CONFIDENCE_THRESHOLD:
            self._add_to_kb(incident)
        return incident

    # ── PATTERN MATCH ─────────────────────────────────────────────────

    def _pattern_match(self, incident):
        ev      = incident.get("evidence", {})
        tickers = incident.get("tickers", [])
        today   = datetime.now().strftime("%Y-%m-%d")

        # Auto-detect signatures from DB for these tickers
        auto = {}
        try:
            conn = sqlite3.connect(DB_LIVE_PATH)
            conn.row_factory = sqlite3.Row
            if tickers:
                ph = ",".join("?"*len(tickers))
                rows = conn.execute(
                    f"SELECT exit_reason, exit_price, entry_price, hold_minutes, gross_pnl "
                    f"FROM trade_log WHERE ticker IN ({ph}) AND DATE(entry_time)=? "
                    f"ORDER BY entry_time DESC LIMIT 10", tickers+[today]
                ).fetchall()
                if rows:
                    r = rows[0]
                    auto["exit_reason"]                  = r["exit_reason"]
                    auto["exit_price_equals_entry_price"]= abs(float(r["exit_price"] or 0)-float(r["entry_price"] or 0))<0.01
                    auto["hold_minutes_lt"]              = float(r["hold_minutes"] or 99)
                    auto["gross_pnl"]                    = float(r["gross_pnl"] or 0)
            # Check broker vs DB divergence
            db_tickers = {row["ticker"] for row in conn.execute("SELECT ticker FROM positions").fetchall()}
            for t in tickers:
                auto["in_kubers_db_positions"]  = t in db_tickers
                auto["in_indmoney_positions"]   = True  # if we got here, it was flagged as open on broker
                auto["entry_narrative_empty"]   = False
                # check if any recent trade has blank narrative
                if t in db_tickers:
                    pass  # in DB, so not a ghost
                else:
                    auto["in_kubers_db_positions"] = False
            conn.close()
        except Exception as e:
            log.warning("[rca] pattern_match DB error: %s", e)

        # Merge auto-detected with manually provided evidence
        merged = {**auto, **ev}

        for p in self.kb.get("patterns", []):
            sig = p.get("signature", {})
            ok  = True
            for key, expected in sig.items():
                actual = merged.get(key, self._compute_sig_field(key, incident))
                if actual is None:
                    # Dont fail match on missing optional fields — skip field
                    continue
                if isinstance(expected, bool):
                    if bool(actual) != expected:
                        ok = False; break
                elif isinstance(expected, (int, float)):
                    # For hold_minutes_lt pattern: check if actual < expected
                    if key.endswith("_lt"):
                        if not float(actual) < expected:
                            ok = False; break
                    else:
                        if float(actual) != expected:
                            ok = False; break
                elif str(actual).upper() != str(expected).upper():
                    ok = False; break
            if ok:
                return p
        return None

    def _compute_sig_field(self, key, incident):
        tickers = incident.get("tickers", [])
        today   = datetime.now().strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(DB_LIVE_PATH)
            conn.row_factory = sqlite3.Row
            if key == "exit_price_equals_entry_price" and tickers:
                r = conn.execute(
                    "SELECT ABS(exit_price-entry_price)<0.01 eq FROM trade_log "
                    "WHERE ticker=? AND DATE(entry_time)=? ORDER BY entry_time DESC LIMIT 1",
                    (tickers[0], today)
                ).fetchone()
                conn.close()
                return bool(r["eq"]) if r else None
            if key == "in_kubers_db_positions" and tickers:
                r = conn.execute("SELECT COUNT(*) n FROM positions WHERE ticker=?", (tickers[0],)).fetchone()
                conn.close()
                return r["n"] > 0 if r else False
            conn.close()
        except Exception:
            pass
        return None

    # ── EVIDENCE ──────────────────────────────────────────────────────

    def _gather_evidence(self, incident):
        ev      = dict(incident.get("evidence", {}))
        tickers = incident.get("tickers", [])
        today   = datetime.now().strftime("%Y-%m-%d")

        try:
            conn = sqlite3.connect(DB_LIVE_PATH)
            conn.row_factory = sqlite3.Row
            ev["db_open_positions"] = [dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()]
            if tickers:
                ph = ",".join("?"*len(tickers))
                ev["db_recent_trades"] = [dict(r) for r in conn.execute(
                    f"SELECT ticker,direction,entry_price,exit_price,qty,hold_minutes,"
                    f"exit_reason,gross_pnl,net_pnl,entry_time,exit_time "
                    f"FROM trade_log WHERE ticker IN ({ph}) AND DATE(entry_time)=? "
                    f"ORDER BY entry_time DESC LIMIT 20", tickers+[today]
                ).fetchall()]
                ev["db_signals"] = [dict(r) for r in conn.execute(
                    f"SELECT ticker,direction,disposition,timestamp,limit_price,sl_price,entry_reason "
                    f"FROM signal_log WHERE ticker IN ({ph}) AND DATE(timestamp)=? "
                    f"ORDER BY timestamp DESC LIMIT 10", tickers+[today]
                ).fetchall()]
            ev["sl_hit_stats"] = dict(conn.execute(
                "SELECT COUNT(*) total, "
                "SUM(CASE WHEN ABS(exit_price-entry_price)<0.01 AND hold_minutes<2 THEN 1 ELSE 0 END) instant "
                "FROM trade_log WHERE exit_reason='SL_HIT' AND DATE(entry_time)=?", (today,)
            ).fetchone() or {})
            conn.close()
        except Exception as e:
            ev["db_error"] = str(e)

        ev["source_files"] = {}
        for rel in ["engine.py","execution/broker.py","risk/risk_gate.py",
                    "strategy/rule_strategy.py","strategy/shadow_book.py","data/feed.py","config.py"]:
            full = os.path.join(KUBERS_DIR, rel)
            try:    ev["source_files"][rel] = open(full).read()
            except: ev["source_files"][rel] = f"[NOT FOUND: {full}]"

        ev["system_facts"]    = self.kb.get("system_facts", {})
        ev["known_patterns"]  = [{"id":p["id"],"title":p["title"]} for p in self.kb.get("patterns",[])]
        return ev

    # ── INTERROGATION ─────────────────────────────────────────────────

    def _interrogate(self, incident, evidence):
        rounds     = []
        current    = None
        same_count = 0

        for rnum in range(1, MAX_ROUNDS + 1):
            log.info("[rca] Round %d/%d", rnum, MAX_ROUNDS)
            if rnum == 1:
                prompt = self._initial_prompt(incident, evidence)
            else:
                challenge = self._build_challenge(current, evidence, rounds)
                prompt    = self._challenge_prompt(incident, rounds, challenge)

            response = self._call_claude(prompt)
            if not response:
                break

            parsed = self._parse_response(response)
            rounds.append({"round": rnum, "response": response, **parsed})
            log.info("[rca] Round %d — conf=%.0f%% — %s",
                     rnum, parsed["confidence"]*100, parsed["hypothesis"][:80])

            if current and self._same_hyp(current["hypothesis"], parsed["hypothesis"]):
                same_count += 1
            else:
                same_count = 0
            current = parsed

            if parsed["confidence"] >= CONFIDENCE_THRESHOLD or same_count >= 2:
                log.info("[rca] Converged at round %d", rnum)
                break

        incident["rounds"] = rounds
        if rounds:
            f = rounds[-1]
            incident.update({
                "rca":           f["hypothesis"],
                "confidence":    f["confidence"],
                "proposed_fix":  f["proposed_fix"],
                "affected_file": f["affected_file"],
                "status":        "complete" if f["confidence"] >= CONFIDENCE_THRESHOLD else "low_confidence",
            })
            if f["confidence"] >= CONFIDENCE_THRESHOLD and f.get("proposed_fix"):
                self._write_fix(incident, f)
        return incident

    # ── PROMPTS ───────────────────────────────────────────────────────

    def _initial_prompt(self, incident, evidence):
        sources = ""
        for fname, content in evidence.get("source_files", {}).items():
            sources += f"\n\n=== {fname} ===\n{content}"
        return (
            f"You are the RCA agent for Kubers Calling, an NSE intraday trading system.\n\n"
            f"INCIDENT: {incident['type']} | Tickers: {incident['tickers']} | Time: {incident['timestamp']}\n"
            f"Evidence: {json.dumps(incident['evidence'], indent=2)}\n\n"
            f"SYSTEM FACTS (authoritative):\n{json.dumps(evidence.get('system_facts',{}), indent=2)}\n\n"
            f"KNOWN PATTERNS (already confirmed — do not re-investigate):\n{json.dumps(evidence.get('known_patterns',[]), indent=2)}\n\n"
            f"DB STATE:\n"
            f"Open positions: {json.dumps(evidence.get('db_open_positions',[]), indent=2)}\n"
            f"Recent trades: {json.dumps(evidence.get('db_recent_trades',[]), indent=2)}\n"
            f"SL_HIT stats: {json.dumps(evidence.get('sl_hit_stats',{}), indent=2)}\n"
            f"Signals: {json.dumps(evidence.get('db_signals',[]), indent=2)}\n\n"
            f"SOURCE CODE:{sources}\n\n"
            f"Identify the precise root cause. Respond in EXACTLY this format:\n"
            f"HYPOTHESIS: [one sentence, name exact file + function + line]\n"
            f"CONFIDENCE: [0.00 to 1.00]\n"
            f"AFFECTED_FILE: [path]\n"
            f"AFFECTED_FUNCTION: [name]\n"
            f"EVIDENCE_SUPPORTS: [2-3 specific data points]\n"
            f"EVIDENCE_AGAINST: [falsifying data or NONE]\n"
            f"PROPOSED_FIX: [exact before/after code change]\n"
            f"OPEN_QUESTIONS: [what would raise confidence or NONE]"
        )

    def _build_challenge(self, current, evidence, rounds):
        h      = current.get("hypothesis", "")
        trades = evidence.get("db_recent_trades", [])
        out    = []

        if "instant" in h.lower() or "immediately" in h.lower():
            real = [t for t in trades
                    if t.get("exit_reason") == "SL_HIT"
                    and abs(float(t.get("exit_price",0))-float(t.get("entry_price",0))) > 0.01]
            if real:
                out.append(
                    f"Your hypothesis predicts all SL_HITs are instant. "
                    f"But {len(real)} show exit_price != entry_price: "
                    f"{[(t['ticker'],t['exit_price'],t['entry_price']) for t in real[:2]]}. Reconcile."
                )

        if current.get("confidence", 0) < 0.85:
            out.append(
                f"Confidence {current.get('confidence',0):.0%} is below threshold. "
                f"Identify the EXACT line number causing the bug. If you cannot, refine your hypothesis."
            )

        last = rounds[-1].get("response","") if rounds else ""
        oq   = re.search(r"OPEN_QUESTIONS:(.*?)(?:\n[A-Z]|$)", last, re.DOTALL)
        if oq and oq.group(1).strip().upper() not in ("NONE",""):
            out.append(
                f"You listed open questions: {oq.group(1).strip()[:200]}. "
                f"All available evidence is provided. Answer using what you have."
            )

        if not out:
            out.append("Confirm hypothesis and raise confidence if evidence fully supports, or state what would change your mind.")

        return " | ".join(out)

    def _challenge_prompt(self, incident, rounds, challenge):
        last = rounds[-1]["response"] if rounds else ""
        return (
            f"Round {len(rounds)+1} interrogation for {incident['id']}.\n\n"
            f"Previous response:\n{last}\n\n"
            f"CHALLENGE: {challenge}\n\n"
            f"Respond in EXACTLY the same format:\n"
            f"HYPOTHESIS: [revised or reconfirmed]\nCONFIDENCE: [updated]\n"
            f"AFFECTED_FILE: [path]\nAFFECTED_FUNCTION: [name]\n"
            f"EVIDENCE_SUPPORTS: [updated]\nEVIDENCE_AGAINST: [updated]\n"
            f"PROPOSED_FIX: [exact change]\nOPEN_QUESTIONS: [remaining or NONE]"
        )

    # ── CLAUDE ────────────────────────────────────────────────────────

    def _call_claude(self, prompt):
        try:
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            r = requests.post(
                CLAUDE_API,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={"model": CLAUDE_MODEL, "max_tokens": 2000,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            if r.status_code == 200:
                return "".join(c.get("text","") for c in r.json().get("content",[]) if c.get("type")=="text")
            if r.status_code == 401:
                log.error("[rca] Claude 401 — set ANTHROPIC_API_KEY environment variable")
                log.error("[rca] Run: set ANTHROPIC_API_KEY=your_key_here")
            else:
                log.error("[rca] Claude HTTP %d", r.status_code)
        except Exception as e:
            log.error("[rca] Claude error: %s", e)
        return ""

    def _parse_response(self, text):
        def get(label):
            m = re.search(rf"{label}:\s*(.+?)(?:\n[A-Z_]+:|$)", text, re.DOTALL)
            return m.group(1).strip() if m else ""
        conf_str = get("CONFIDENCE")
        try:    conf = float(re.search(r"[\d.]+", conf_str).group())
        except: conf = 0.5
        return {"hypothesis": get("HYPOTHESIS"), "confidence": conf,
                "affected_file": get("AFFECTED_FILE"), "proposed_fix": get("PROPOSED_FIX"),
                "open_questions": get("OPEN_QUESTIONS"), "evidence_for": get("EVIDENCE_SUPPORTS")}

    def _same_hyp(self, a, b):
        wa = set(a.lower().split()); wb = set(b.lower().split())
        return len(wa & wb) / len(wa) > 0.70 if wa else False

    # ── FIX FILE ──────────────────────────────────────────────────────

    def _write_fix(self, incident, final):
        path = os.path.join(FIX_DIR, f"{incident['id']}.fix.txt")
        lines = [
            f"INCIDENT: {incident['id']}",
            f"DATE: {incident['timestamp']}",
            f"TICKERS: {incident['tickers']}",
            f"CONFIDENCE: {final['confidence']:.0%}",
            "",
            "ROOT CAUSE:",
            final["hypothesis"],
            "",
            f"AFFECTED FILE: {final['affected_file']}",
            f"AFFECTED FUNCTION: {final.get('affected_function','')}",
            "",
            "PROPOSED FIX:",
            final["proposed_fix"],
            "",
            "--- INTERROGATION TRANSCRIPT ---",
        ]
        for r in incident["rounds"]:
            lines += [f"\nROUND {r['round']}:", r["response"], "─"*60]
        with open(path, "w") as f:
            f.write("\n".join(lines))
        incident["fix_file"] = path
        log.info("[rca] Fix written: %s", path)

    # ── KB & INCIDENTS ────────────────────────────────────────────────

    def _load_kb(self):
        try:    return json.load(open(KB_PATH))
        except: return {"patterns": [], "system_facts": {}}

    def _load_incidents(self):
        try:    return json.load(open(INC_PATH))
        except: return []

    def _save_incident(self, incident):
        others = [i for i in self.incidents if i["id"] != incident["id"]]
        self.incidents = others + [incident]
        json.dump(self.incidents, open(INC_PATH,"w"), indent=2, default=str)

    def _add_to_kb(self, incident):
        if incident["id"] in {p["id"] for p in self.kb.get("patterns",[])}:
            return
        self.kb.setdefault("patterns",[]).append({
            "id":               incident["id"],
            "title":            (incident.get("rca") or "")[:80],
            "category":         "auto_discovered",
            "confirmed_date":   datetime.now().strftime("%Y-%m-%d"),
            "confidence":       incident["confidence"],
            "recurrence_count": 1,
            "signature":        {k:v for k,v in incident.get("evidence",{}).items()
                                 if not isinstance(v,(dict,list))},
            "root_cause":       incident.get("rca",""),
            "affected_file":    incident.get("affected_file",""),
            "fix_applied":      incident.get("proposed_fix",""),
            "fix_date":         None,
            "interrogation_rounds": len(incident.get("rounds",[])),
        })
        json.dump(self.kb, open(KB_PATH,"w"), indent=2)
        log.info("[rca] Added to knowledge base: %s", incident["id"])

    def approve_fix(self, incident_id):
        for inc in self.incidents:
            if inc["id"] == incident_id:
                inc.update({"approved": True, "approved_date": datetime.now().isoformat()})
                if inc.get("pattern_match"):
                    for p in self.kb.get("patterns",[]):
                        if p["id"] == inc["pattern_match"]:
                            p["recurrence_count"] = p.get("recurrence_count",1) + 1
                    json.dump(self.kb, open(KB_PATH,"w"), indent=2)
                self._save_incident(inc)
                return True
        return False

    def get_all_incidents(self):
        return sorted(self.incidents, key=lambda x: x.get("timestamp",""), reverse=True)

    def get_incident(self, incident_id):
        return next((i for i in self.incidents if i["id"]==incident_id), None)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser()
    p.add_argument("--type",    default="MANUAL")
    p.add_argument("--tickers", default="")
    p.add_argument("--approve", default="")
    args = p.parse_args()
    agent = RCAAgent()
    if args.approve:
        print("Approved:", agent.approve_fix(args.approve)); sys.exit(0)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    result  = agent.investigate(make_incident(args.type, tickers, {}))
    print(f"\n{'='*60}")
    print(f"STATUS:     {result['status']}")
    print(f"CONFIDENCE: {result['confidence']:.0%}")
    print(f"PATTERN:    {result.get('pattern_match') or 'NEW'}")
    print(f"\nROOT CAUSE:\n{result.get('rca','No conclusion')}")
    print(f"\nPROPOSED FIX:\n{result.get('proposed_fix','None')}")
    print("="*60)
