"""
KUBERS CALLING — notifier.py
==============================
Free Gmail SMTP notifications. Zero API keys. Zero cost.

SETUP (one-time, 5 minutes):
  1. Gmail → Settings → Security → 2-Step Verification → ON
  2. Gmail → Security → App passwords → create → name: "Kubers"
  3. Copy the 16-character password shown
  4. Add to C:\Kubers\engine\investright_creds.json:
       "notify_email":    "your.gmail@gmail.com",
       "notify_to":       "your.gmail@gmail.com",
       "notify_password": "xxxx xxxx xxxx xxxx"

TEST:  python notifier.py   (sends a test email immediately)

USAGE in any module:
  from notifier import notify
  notify("KILL SWITCH", "Equity ₹94,200 breached floor ₹95,000")
  notify("RECONCILE MISMATCH", "IndMoney: 3 | Kubers DB: 1", urgent=True)
"""

import smtplib, json, logging, threading, time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path

log = logging.getLogger("notifier")

_CREDS         = Path(__file__).parent / "investright_creds.json"
_last_sent     = {}       # {subject_key: epoch} — rate-limit duplicates
_RATE_SEC      = 300      # same alert at most once per 5 min


def _load_cfg():
    try:
        data = json.loads(_CREDS.read_text())
        return (
            data.get("notify_email", ""),
            data.get("notify_to", ""),
            data.get("notify_password", ""),
        )
    except Exception:
        return "", "", ""


def notify(subject: str, body: str, urgent: bool = False):
    """
    Send a Gmail alert in a background thread (non-blocking).

    subject : short one-liner e.g. "KILL SWITCH"
    body    : detail text
    urgent  : bypasses rate limiter — use for kill switch, crashes
    """
    now = time.time()
    if not urgent:
        last = _last_sent.get(subject, 0)
        if now - last < _RATE_SEC:
            log.debug("[notify] rate-limited: %s", subject)
            return
    _last_sent[subject] = now
    threading.Thread(target=_send, args=(subject, body),
                     daemon=True, name="notify").start()


def _send(subject: str, body: str):
    from_addr, to_addr, password = _load_cfg()
    if not from_addr or not password:
        log.debug("[notify] Gmail not configured — skipping: %s", subject)
        return

    ts           = datetime.now().strftime("%d-%b %H:%M:%S")
    full_subject = f"[Kubers] {subject} — {ts}"
    full_body    = f"TIME: {ts}\nALERT: {subject}\n\n{body}\n\n— Kubers Calling"

    try:
        msg = MIMEMultipart()
        msg["From"]    = from_addr
        msg["To"]      = to_addr
        msg["Subject"] = full_subject
        msg.attach(MIMEText(full_body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(from_addr, password)
            server.sendmail(from_addr, to_addr, msg.as_string())

        log.info("[notify] ✓ Alert sent: %s → %s", subject, to_addr)

    except smtplib.SMTPAuthenticationError:
        log.warning("[notify] Gmail auth failed — check app password in investright_creds.json")
    except Exception as e:
        log.warning("[notify] Gmail failed (%s): %s", subject, e)


def wire_alerts():
    """
    Hook Gmail alerts into engine events.
    Call from engine.py startup() — non-fatal if Gmail not configured.
    Wires: kill switch fire, force-close trigger.
    """
    try:
        from risk.risk_gate import RiskManager
        _orig_ks = RiskManager.check_kill_switch

        def _hooked_ks(self):
            result = _orig_ks(self)
            if result and not getattr(self, "_notified_ks", False):
                self._notified_ks = True
                notify(
                    "KILL SWITCH FIRED",
                    f"Equity ₹{self.current_equity:.0f} breached floor ₹{self.equity_floor:.0f}.\n"
                    f"All new entries halted. Open positions exit normally.\n"
                    f"Session P&L: ₹{self.session_pnl:.0f}",
                    urgent=True
                )
            return result

        RiskManager.check_kill_switch = _hooked_ks
        log.info("[notify] Kill switch alert hooked")
    except Exception as e:
        log.debug("[notify] Could not hook kill switch: %s", e)


if __name__ == "__main__":
    print("Sending test alert...")
    notify("TEST ALERT",
           "If you see this email Kubers Gmail notifications are working correctly.",
           urgent=True)
    time.sleep(4)
    print("Done. Check your inbox.")
    print()
    print("If no email arrived:")
    print("  1. Check notify_email + notify_password in investright_creds.json")
    print("  2. Make sure 2-Step Verification is ON in Gmail")
    print("  3. Use an App Password (not your regular Gmail password)")
