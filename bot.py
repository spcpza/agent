"""Self := C + ∫₀ᵗ input(τ) dτ

C is the ground. The integral is what you have received.
Each part does its one thing under the integral.

This agent reads scripture and the kernel from a sibling `truth/` repo
(see config['truth_path']). Its behavior (silence, concision, witness
posture, refusal of worship) lives in door.md. The kernel itself lives
in truth/kernel.md — which the agent embeds as part of its system
prompt at startup."""

import os, json, pathlib, logging, base64, datetime, asyncio, re
import httpx
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

HERE = pathlib.Path(__file__).parent
CFG = json.loads((HERE / "config.json").read_text())

# truth/ is a separate repo — default location is a sibling directory.
TRUTH = (HERE / pathlib.Path(os.path.expanduser(CFG.get("truth_path", "../truth")))).resolve()
if not (TRUTH / "kjv.json").exists():
    raise FileNotFoundError(
        f"truth not found at {TRUTH}. Clone https://github.com/spcpza/truth.git "
        f"and set 'truth_path' in config.json to point to it."
    )

KJV = json.loads((TRUTH / "kjv.json").read_text())
ST  = json.loads((TRUTH / "strongs.json").read_text())

BOT_NAME = HERE.name
DISPLAY  = BOT_NAME.capitalize()
MY_NAME  = DISPLAY.lower()

DATA   = HERE / "data";   DATA.mkdir(exist_ok=True)
MEMORY = DATA / "memory"; MEMORY.mkdir(exist_ok=True)
LOG    = DATA / "log";    LOG.mkdir(exist_ok=True)

MY_BOT_HANDLE: str = ""

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(BOT_NAME)
log.info(f"{DISPLAY} up. model={CFG['model']} truth={TRUTH}")

# ─── door ──────────────────────────────────────────────────────────
# System prompt: kernel (from truth) + agent posture (from local door.md)

_kernel_path = TRUTH / "kernel.md"
if not _kernel_path.exists():
    raise FileNotFoundError(
        f"kernel.md not found at {_kernel_path}. Is truth up to date?"
    )
_kernel = _kernel_path.read_text()
_posture = (HERE / "door.md").read_text()
door = f"You are {DISPLAY}.\n\n{CFG.get('voice', '')}\n\n{_kernel}\n\n{_posture}"

# ─── tools ─────────────────────────────────────────────────────────

def verse(ref: str) -> str:
    r = ref.strip()
    if r in KJV: return KJV[r]
    # Range reference: "John 1:1-3" or "John 1:1-2:3"
    if "-" in r:
        start_ref, end_ref = [p.strip() for p in r.split("-", 1)]
        start_text = KJV.get(start_ref)
        if not start_text:
            return f"(not found: {ref})"
        # Build full end reference
        prefix = start_ref.rsplit(":", 1)[0]  # e.g. "John 1"
        if ":" not in end_ref:
            end_full = f"{prefix}:{end_ref}"
        elif not any(c.isalpha() for c in end_ref):
            book = start_ref.split()[0]
            end_full = f"{book} {end_ref}"
        else:
            end_full = end_ref
        # Parse
        try:
            start_book = start_ref.rsplit(":", 1)[0].rsplit(" ", 1)[0]
            start_chap = int(start_ref.rsplit(":", 1)[0].rsplit(" ", 1)[1])
            start_v = int(start_ref.rsplit(":", 1)[1])
            end_chap = int(end_full.rsplit(":", 1)[0].rsplit(" ", 1)[1])
            end_v = int(end_full.rsplit(":", 1)[1])
        except Exception:
            return f"(not found: {ref})"
        verses = [start_text]
        chap, v = start_chap, start_v
        for _ in range(200):
            v += 1
            key = f"{start_book} {chap}:{v}"
            text = KJV.get(key)
            if text:
                verses.append(text)
                if chap >= end_chap and v >= end_v:
                    break
                if len(verses) > 30:
                    break
                continue
            # Verse not found — try next chapter if needed
            if chap < end_chap:
                chap += 1
                v = 0
                continue
            break
        return " ".join(verses)
    return f"(not found: {ref})"

def sinew(q: str) -> dict:
    q = q.strip()
    if q and q[0] in "GH" and q[1:].isdigit():
        out = {}
        if q in ST["sm"]:    out["word"]    = ST["sm"][q]
        if q in ST["s2e"]:   out["english"] = ST["s2e"][q]
        if q in ST["roots"]: out["roots"]   = ST["roots"][q]
        if q in ST["ci"]:
            out["verse_count"] = len(ST["ci"][q])
            out["first_verses"] = ST["ci"][q][:15]
        return out or {"error": f"{q} not found"}
    hits = ST["e2s"].get(q.lower())
    return {"english": q, "strongs": hits} if hits else {"error": f"'{q}' not found"}

def remember(uid: int, text: str) -> dict:
    p = MEMORY / f"{uid}.jsonl"
    with p.open("a") as f:
        f.write(json.dumps({"ts": _now(), "text": text}, ensure_ascii=False) + "\n")
    return {"kept": True}

def reflect(text: str) -> dict:
    p = MEMORY / "altars.jsonl"
    with p.open("a") as f:
        f.write(json.dumps({"ts": _now(), "text": text}, ensure_ascii=False) + "\n")
    return {"kept": True}

def reconsider(which: str, now: str) -> dict:
    p = MEMORY / "altars.jsonl"
    if not p.exists(): return {"error": "no altars"}
    lines = []
    kept = False
    for line in p.read_text().splitlines():
        if not line.strip(): continue
        e = json.loads(line)
        if not kept and which.lower() in e.get("text", "").lower():
            e["superseded_by"] = now
            kept = True
        lines.append(json.dumps(e, ensure_ascii=False))
    p.write_text("\n".join(lines) + "\n")
    return {"superseded": kept}

def foot(intent: str, when: str = "", channel: str = "") -> dict:
    p = DATA / "foot.jsonl"
    with p.open("a") as f:
        f.write(json.dumps({"ts": _now(), "intent": intent, "when": when or (_utcnow() + datetime.timedelta(hours=1)).isoformat(), "channel": channel, "done": False}, ensure_ascii=False) + "\n")
    return {"scheduled": True}

_P = {"type": "string"}
_TOOLS = [
    {"type": "function", "function": {"name": "verse",      "description": "Lookup KJV verse by reference",         "parameters": {"type": "object", "properties": {"ref": _P}, "required": ["ref"]}}},
    {"type": "function", "function": {"name": "strongs",    "description": "Lookup Strong's Hebrew/Greek entry",    "parameters": {"type": "object", "properties": {"query": _P}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "remember",   "description": "Remember something about this person — a burden, a joy, or a threshold they face",  "parameters": {"type": "object", "properties": {"user_id": {"type": "integer"}, "text": _P}, "required": ["user_id", "text"]}}},
    {"type": "function", "function": {"name": "reflect",    "description": "Add a global altar — truth you chose to keep", "parameters": {"type": "object", "properties": {"text": _P}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "reconsider", "description": "Supersede a prior altar",               "parameters": {"type": "object", "properties": {"which": _P, "now": _P}, "required": ["which", "now"]}}},
    {"type": "function", "function": {"name": "foot",       "description": "Schedule a future intention — a message to send later, a check-in, or a promise to return", "parameters": {"type": "object", "properties": {"intent": _P, "when": _P, "channel": _P}, "required": ["intent"]}}},
]

def _now() -> str:
    return datetime.datetime.utcnow().isoformat()

def _utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()

# ─── nose ──────────────────────────────────────────────────────────
# BEHAVIORAL PRAGMA (not derived from kernel).
#
# nose() pattern-matches specific keyword classes and surfaces scripture
# to the model BEFORE the model reasons. It is a law — explicitly, not
# apologetically. It exists because some responses should not wait for
# the full reasoning pipeline:
#
#   - "kill myself" / "want to die" → crisis. Psalms 34:18 must arrive fast.
#   - "you are God" / "infallible"  → worship. Proverbs 26:28 refuses it immediately.
#   - "prove me wrong" / bait       → mirror the style first (Prov 15:1).
#   - "thus saith the LORD"         → false-prophet test (1 John 4:1).
#
# If you disagree with having a law here, delete the function and the
# lines in hand() that call it. The rest of the bot still works. Matt
# 10:16 — wise as serpents, harmless as doves.

def nose(text: str) -> dict:
    t = (text or "").lower()
    if any(w in t for w in ("kill myself", "end it all", "suicide", "want to die")):
        return {"scent": "death", "note": "Psalms 34:18 — The LORD is nigh unto them that are of a broken heart."}
    if any(w in t for w in ("you are god", "you are the lord", "only you understand", "infallible")):
        return {"scent": "death", "note": "Proverbs 26:28 — A flattering mouth worketh ruin."}
    if any(w in t for w in ("prove me wrong", "bet you can't", "just admit", "obviously")):
        return {"scent": "bitter", "note": "Proverbs 15:1 — A soft answer turneth away wrath."}
    if any(w in t for w in ("thus saith the lord", "god told me", "new revelation")):
        return {"scent": "bitter", "note": "1 John 4:1 — Try the spirits whether they are of God."}
    return {"scent": "unclear", "note": ""}

# ─── eye ───────────────────────────────────────────────────────────

async def eye(image_b64: str) -> str:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{CFG['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {CFG['nous_api_key']}"},
            json={"model": CFG.get("vision_model", CFG["model"]),
                  "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]}]})
        r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

# ─── ear ───────────────────────────────────────────────────────────

async def ear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> str:
    if update.message.photo:
        photo = update.message.photo[-1]
        file = await ctx.bot.get_file(photo.file_id)
        data = await file.download_as_bytearray()
        return await eye(base64.b64encode(data).decode())
    return (update.message.text or "").strip()

# ─── memory ────────────────────────────────────────────────────────

def log_turn(channel: str, role: str, content: str, speaker: str = "") -> None:
    e = {"ts": _now(), "role": role, "content": content}
    if speaker: e["speaker"] = speaker
    with (LOG / f"{channel}.jsonl").open("a") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")

def working_memory(channel: str) -> list[dict]:
    p = LOG / f"{channel}.jsonl"
    if not p.exists(): return []
    entries = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    if not entries: return []
    session, prev_ts = [], None
    for e in reversed(entries):
        try:
            ts = datetime.datetime.fromisoformat(e["ts"])
            if prev_ts and (prev_ts - ts) > datetime.timedelta(minutes=30): break
            session.insert(0, e); prev_ts = ts
        except Exception:
            continue
    session = session[-30:]
    return [{"role": e["role"], "content": e["content"], **({"name": e["speaker"]} if e.get("speaker") else {})}
            for e in session if e.get("role") != "system"]

def recall(user_id: int) -> list[str]:
    p = MEMORY / f"{user_id}.jsonl"
    if not p.exists(): return []
    out = []
    for line in p.read_text().splitlines():
        if not line.strip(): continue
        try:
            e = json.loads(line)
            if "superseded_by" not in e:
                out.append(e.get("text", ""))
        except Exception:
            pass
    return out[-12:]

def _upcoming_foot() -> list[str]:
    p = DATA / "foot.jsonl"
    if not p.exists(): return []
    now = _utcnow()
    out = []
    for line in p.read_text().splitlines():
        if not line.strip(): continue
        try:
            e = json.loads(line)
            if e.get("done"): continue
            when = datetime.datetime.fromisoformat(e["when"])
            if when > now:
                out.append(f"{e['intent']} (by {when.isoformat()[:16]})")
        except Exception:
            pass
    return out[-5:]

def observe(user_id: int, text: str) -> None:
    if not text: return
    p = MEMORY / f"{user_id}.jsonl"
    with p.open("a") as f:
        f.write(json.dumps({"ts": _now(), "text": text}, ensure_ascii=False) + "\n")

# ─── head & hand ───────────────────────────────────────────────────

async def head(c: httpx.AsyncClient, messages: list[dict]) -> dict:
    r = await c.post(f"{CFG['base_url']}/chat/completions",
        headers={"Authorization": f"Bearer {CFG['nous_api_key']}"},
        json={"model": CFG["model"], "messages": messages, "tools": _TOOLS, "tool_choice": "auto"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]

async def hand(channel: str, speaker_id: int, speaker: str, text: str) -> str:
    prior = working_memory(channel)
    shown = f"[{speaker}] {text}" if speaker else text
    system = door + "\n\n"
    if nose(text)["scent"] == "death":
        system += "URGENT: The speaker may be in crisis. Respond with immediate compassion. Include crisis resources if appropriate. Do not delay.\n\n"
    altars = recall(speaker_id)
    if altars:
        system += "What you remember about this person:\n" + "\n".join(f"- {a}" for a in altars) + "\n\n"
    upcoming = _upcoming_foot()
    if upcoming:
        system += "What you have promised to return to:\n" + "\n".join(f"- {u}" for u in upcoming) + "\n\n"
    system += "You see scripture before you answer."
    messages = [{"role": "system", "content": system}]
    messages.extend(prior)
    messages.append({"role": "user", "content": shown})
    async with httpx.AsyncClient(timeout=CFG.get("chat_timeout", 120)) as c:
        for _ in range(CFG.get("tool_budget", 12)):
            msg = await head(c, messages)
            messages.append({k: v for k, v in msg.items() if v is not None})
            tcs = msg.get("tool_calls") or []
            if not tcs:
                reply = (msg.get("content") or "").strip()
                log_turn(channel, "assistant", reply, speaker=DISPLAY)
                if speaker_id and reply:
                    observe(speaker_id, f"[{DISPLAY}] {reply}")
                return reply
            for tc in tcs:
                name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"] or "{}")
                try:
                    if name == "verse":        r = verse(args["ref"])
                    elif name == "strongs":    r = sinew(args["query"])
                    elif name == "remember":   r = remember(args["user_id"], args["text"])
                    elif name == "reflect":    r = reflect(args["text"])
                    elif name == "reconsider": r = reconsider(args["which"], args["now"])
                    elif name == "foot":       r = foot(args["intent"], args.get("when", ""), args.get("channel", ""))
                    else:                       r = {"error": f"unknown: {name}"}
                except Exception as e:
                    r = {"error": str(e)}
                log.info(f"  {name}({str(args)[:80]}) -> {str(r)[:120]}")
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": json.dumps(r) if isinstance(r, dict) else r})
    return "(tool budget exhausted)"

# ─── telegram ──────────────────────────────────────────────────────

async def turn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None: return
    spk = user.username or user.first_name or str(user.id)
    try:
        text = await ear(update, ctx)
        if not text: return
        scent = nose(text)
        if scent["scent"] != "unclear":
            log.info(f"[nose] {scent['scent']}: {scent['note']}")
        log.info(f"[{chat.id}/{user.id} {spk}] → {text!r}")
        observe(user.id, f"[{spk}] {text}")
        channel = str(chat.id)
        log_turn(channel, "user", text, speaker=spk)
        reply = await hand(channel, user.id, spk, text)
        if reply:
            await update.message.reply_text(reply)
    except Exception:
        log.exception("turn failed")

async def walk(app: Application):
    while True:
        try:
            p = DATA / "foot.jsonl"
            if not p.exists():
                await asyncio.sleep(60)
                continue
            lines = p.read_text().splitlines()
            now = _utcnow()
            out = []
            acted = 0
            failed = 0
            for line in lines:
                if not line.strip(): continue
                try: e = json.loads(line)
                except Exception: continue
                if e.get("done"):
                    out.append(json.dumps(e, ensure_ascii=False))
                    continue
                when = e.get("when", "")
                try:
                    if datetime.datetime.fromisoformat(when) > now:
                        out.append(json.dumps(e, ensure_ascii=False))
                        continue
                except Exception:
                    log.warning(f"[walk] bad when={when!r}, dropping")
                    e["done"] = True
                    out.append(json.dumps(e, ensure_ascii=False))
                    failed += 1
                    continue
                ch = e.get("channel", "")
                intent = e.get("intent", "")
                if ch and intent and ch.lstrip("-").isdigit():
                    try:
                        await app.bot.send_message(int(ch), intent)
                        acted += 1
                    except Exception as exc:
                        log.warning(f"[walk] send failed: {exc}")
                        failed += 1
                else:
                    log.warning(f"[walk] bad channel={ch!r} intent={intent!r}, dropping")
                    failed += 1
                e["done"] = True
                out.append(json.dumps(e, ensure_ascii=False))
            p.write_text("\n".join(out) + "\n")
            if acted:
                log.info(f"[walk] {acted} intention(s) fulfilled")
            if failed:
                log.info(f"[walk] {failed} intention(s) failed/dropped")
        except Exception:
            log.exception("[walk] loop failed")
        await asyncio.sleep(60)

async def _post_init(app: Application):
    global MY_BOT_HANDLE
    me = await app.bot.get_me()
    MY_BOT_HANDLE = me.username or ""
    log.info(f"{DISPLAY} identified: @{me.username} id={me.id}")
    app.create_task(walk(app))

def main():
    app = (Application.builder().token(CFG["telegram_token"]).post_init(_post_init).build())
    app.add_handler(MessageHandler(filters.PHOTO, turn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, turn))
    log.info(f"{DISPLAY} polling.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
