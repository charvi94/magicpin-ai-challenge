
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Optional
from datetime import datetime
import os
import json
import time
import uuid
import re

app = FastAPI(title="Vera AI", version="1.0.0")
START_TIME = time.time()

# In-memory state. This is enough for the challenge test window.
contexts: dict[tuple[str, str], dict[str, Any]] = {}
conversations: dict[str, dict[str, Any]] = {}
sent_keys: set[str] = set()

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []


class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


def get_context(scope: str, context_id: str):
    item = contexts.get((scope, context_id))
    return item["payload"] if item else None


def first_name(identity: dict) -> str:
    name = identity.get("name", "there")
    # Keep useful business/doctor names without over-processing.
    if name.startswith("Dr. "):
        parts = name.split()
        return " ".join(parts[:2]) if len(parts) >= 2 else name
    return name.split("'s")[0].split()[0]


def category_name(category: dict) -> str:
    return category.get("slug", "business")


def find_trigger_merchant(trigger: dict):
    # Trigger schemas can put merchant_id in payload or at top level.
    payload = trigger.get("payload") or {}
    return (
        trigger.get("merchant_id")
        or payload.get("merchant_id")
        or trigger.get("merchant", {}).get("merchant_id")
    )


def trigger_kind(trigger: dict) -> str:
    payload = trigger.get("payload") or {}
    return str(
        trigger.get("kind")
        or payload.get("kind")
        or trigger.get("type")
        or ""
    ).lower()


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def choose_offer(merchant: dict, category: dict, customer: Optional[dict] = None):
    offers = merchant.get("offers") or []
    active = [
        o for o in offers
        if str(o.get("status", "active")).lower() not in {"paused", "inactive", "expired"}
    ]
    if active:
        return active[0]

    catalog = category.get("offer_catalog") or []
    audience = "repeat_user" if customer else "new_user"
    for o in catalog:
        if o.get("audience") in {audience, "all"}:
            return o
    return catalog[0] if catalog else None


def performance_sentence(merchant: dict) -> str:
    p = merchant.get("performance") or {}
    delta = p.get("delta_7d") or {}

    for field, label in [
        ("calls_pct", "calls"),
        ("views_pct", "views"),
        ("directions_pct", "directions"),
        ("leads_pct", "leads"),
    ]:
        if field in delta:
            try:
                pct = float(delta[field]) * 100
                if abs(pct) >= 5:
                    direction = "up" if pct > 0 else "down"
                    return f"your {label} are {direction} {abs(pct):.0f}% over the last 7 days"
            except Exception:
                pass

    if "ctr" in p:
        try:
            return f"your current CTR is {float(p['ctr'])*100:.1f}%"
        except Exception:
            pass

    return ""


def digest_fact(category: dict):
    digest = category.get("digest") or []
    if not digest:
        return None
    return digest[0]


def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None
):
    """
    Deterministic composer.

    Uses only facts supplied in:
    category / merchant / trigger / customer contexts.

    Returns:
        body
        cta
        send_as
        suppression_key
        rationale
    """

    # ---------------------------------------------------------
    # BASIC CONTEXT
    # ---------------------------------------------------------

    cid = category_name(category)
    kind = trigger_kind(trigger)
    merchant_id = merchant.get("merchant_id", "merchant")

    identity = merchant.get("identity") or {}
    payload = trigger.get("payload") or {}

    # ---------------------------------------------------------
    # MERCHANT NAME
    # ---------------------------------------------------------

    name = (
        identity.get("owner_first_name")
        or identity.get("first_name")
        or first_name(identity)
        or "there"
    )

    full_name = identity.get("name", "your business")

    # Clean dentist owner name:
    # "Dr. Meera's" -> "Dr. Meera"
    if cid == "dentists" and isinstance(name, str):
        if name.endswith("'s"):
            name = name[:-2]
        elif name.endswith("’s"):
            name = name[:-2]

    # ---------------------------------------------------------
    # CUSTOMER-FACING TRIGGERS
    # ---------------------------------------------------------

    if customer is not None:

        cidentity = customer.get("identity") or {}

        cname = (
            cidentity.get("name")
            or cidentity.get("first_name")
            or "there"
        )

        offer = choose_offer(
            merchant,
            category,
            customer
        )

        offer_title = (offer or {}).get("title")

        # Customer-trigger families
        if (
            "recall" in kind
            or "appointment" in kind
            or "refill" in kind
            or "lapsed" in kind
            or "winback" in kind
        ):

            due = (
                payload.get("due_date")
                or payload.get("due_at")
                or payload.get("date")
            )

            slots = (
                payload.get("available_slots")
                or payload.get("slots")
            )

            detail = ""

            if due:
                detail += f" — due {due}"

            if slots and isinstance(slots, list):
                detail += f". Available: {slots[0]}"

            body = (
                f"Hi {cname}, quick reminder from "
                f"{full_name}{detail}. "
            )

            if offer_title:
                body += f"We also have {offer_title}. "

            body += "Would you like me to help with the next step?"

            return {
                "body": clean(body),
                "cta": "open_ended",
                "send_as": "merchant_on_behalf",
                "suppression_key": (
                    trigger.get("suppression_key")
                    or f"{kind}:{merchant_id}:"
                    f"{customer.get('customer_id', 'customer')}"
                ),
                "rationale": (
                    "Customer-state trigger; message uses the "
                    "supplied customer and merchant context and "
                    "asks for one low-friction next step."
                )
            }

    # ---------------------------------------------------------
    # MERCHANT-FACING SETUP
    # ---------------------------------------------------------

    perf = performance_sentence(merchant)

    offer = choose_offer(
        merchant,
        category
    )

    offer_title = (offer or {}).get("title")

    digest = digest_fact(category)

    # ---------------------------------------------------------
    # COMPLIANCE / REGULATION
    # ---------------------------------------------------------

    if (
        "compliance" in kind
        or "regulation" in kind
    ):

        d = payload if payload.get("title") else digest

        if d:

            title = d.get(
                "title",
                "a new compliance update"
            )

            source = d.get("source")

            actionable = (
                d.get("actionable")
                or d.get("summary")
            )

            body = (
                f"{name}, quick compliance update — {title}"
            )

            if source:
                body += f" ({source})"

            if actionable:
                body += f". {actionable}"

            body += (
                ". Want me to help with the practical next step?"
            )

        else:

            body = (
                f"{name}, quick compliance update. "
                "Want me to help review the relevant action?"
            )

        return {
            "body": clean(body),
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": (
                trigger.get("suppression_key")
                or f"compliance:{cid}:{merchant_id}"
            ),
            "rationale": (
                "Compliance trigger is handled explicitly using "
                "the supplied regulatory context and a practical "
                "next action."
            )
        }

    # ---------------------------------------------------------
    # RESEARCH / DIGEST / CDE
    # ---------------------------------------------------------

    if (
        "research" in kind
        or "digest" in kind
        or "cde" in kind
    ):

        d = payload if payload.get("title") else digest

        if d:

            title = d.get(
                "title",
                "a new category update"
            )

            source = d.get("source")

            actionable = (
                d.get("actionable")
                or d.get("summary")
            )

            body = f"{name}, quick one — {title}"

            if source:
                body += f" ({source})"

            if actionable:
                body += f". {actionable}"

            body += ". Worth a look?"

            return {
                "body": clean(body),
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": (
                    trigger.get("suppression_key")
                    or f"digest:{cid}:{d.get('id', 'latest')}"
                ),
                "rationale": (
                    "Uses a concrete category digest fact and "
                    "its source/action rather than a generic "
                    "marketing claim."
                )
            }

    # ---------------------------------------------------------
    # PERFORMANCE DIP
    # ---------------------------------------------------------

    if (
        "perf_dip" in kind
        or "dip" in kind
        or "below" in kind
    ):

        performance_text = (
            perf
            if perf
            else "one of your recent performance signals has softened"
        )

        body = (
            f"{name}, quick check — "
            f"{performance_text}."
        )

        if offer_title:
            body += f" Your active offer is {offer_title}."

        body += (
            " Want me to suggest one focused action to test?"
        )

        return {
            "body": clean(body),
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": (
                trigger.get("suppression_key")
                or f"perf_dip:{merchant_id}"
            ),
            "rationale": (
                "Performance trigger; references the merchant's "
                "actual metric and proposes one focused action."
            )
        }

    # ---------------------------------------------------------
    # PERFORMANCE SPIKE / MILESTONE
    # ---------------------------------------------------------

    if (
        "perf_spike" in kind
        or "milestone" in kind
    ):

        performance_text = (
            perf
            if perf
            else "your recent performance has improved"
        )

        body = (
            f"{name}, nice signal — "
            f"{performance_text}."
        )

        if offer_title:
            body += (
                f" You currently have {offer_title} live."
            )

        body += " Want to build on the momentum?"

        return {
            "body": clean(body),
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": (
                trigger.get("suppression_key")
                or f"positive:{merchant_id}:{kind}"
            ),
            "rationale": (
                "Positive performance/milestone trigger with "
                "merchant-specific context."
            )
        }

    # ---------------------------------------------------------
    # COMPETITOR
    # ---------------------------------------------------------

    if "competitor" in kind:

        body = (
            f"{name}, a nearby competitor signal just came in. "
        )

        if perf:
            body += f"Your current profile shows {perf}. "

        body += (
            "Want me to suggest a practical response "
            "based on your current offer mix?"
        )

        return {
            "body": clean(body),
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": (
                trigger.get("suppression_key")
                or f"competitor:{merchant_id}"
            ),
            "rationale": (
                "Competitive trigger; avoids naming an "
                "unsupported competitor and keeps the response "
                "grounded."
            )
        }

    # ---------------------------------------------------------
    # RENEWAL
    # ---------------------------------------------------------

    if "renewal" in kind:

        subscription = merchant.get("subscription") or {}
        days = subscription.get("days_remaining")

        if days is not None:
            detail = f" with {days} days remaining"
        else:
            detail = ""

        body = (
            f"{name}, your Vera subscription is coming up "
            f"for renewal{detail}. "
            "Want me to help review the next step?"
        )

        return {
            "body": clean(body),
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": (
                trigger.get("suppression_key")
                or f"renewal:{merchant_id}"
            ),
            "rationale": (
                "Renewal trigger uses the merchant subscription "
                "context."
            )
        }

    # ---------------------------------------------------------
    # FESTIVAL / SEASONAL / IPL / BRIDAL
    # ---------------------------------------------------------

    if (
        "festival" in kind
        or "seasonal" in kind
        or "ipl" in kind
        or "bridal" in kind
    ):

        title = (
            payload.get("title")
            or payload.get("event")
            or "an upcoming seasonal moment"
        )

        body = f"{name}, quick one — {title}."

        if offer_title:
            body += (
                f" You already have {offer_title} available."
            )

        body += (
            " Want to turn this into one focused "
            "customer-facing push?"
        )

        return {
            "body": clean(body),
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": (
                trigger.get("suppression_key")
                or f"seasonal:{merchant_id}:{kind}"
            ),
            "rationale": (
                "Seasonal trigger combined with an offer "
                "actually present in the merchant context."
            )
        }

    # ---------------------------------------------------------
    # GENERIC GROUNDED FALLBACK
    # ---------------------------------------------------------

    body = (
        f"{name}, quick one from Vera — "
        f"I spotted a new {cid} signal for {full_name}."
    )

    if perf:
        body += f" {perf}."

    elif offer_title:
        body += (
            f" Your current offer is {offer_title}."
        )

    body += " Want me to take a closer look?"

    return {
        "body": clean(body),
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": (
            trigger.get("suppression_key")
            or f"{kind or 'general'}:{merchant_id}"
        ),
        "rationale": (
            "Fallback uses only supplied merchant/category "
            "facts and asks one low-friction question."
        )
    }


def validate_message(result: dict, category: dict):
    body = clean(result.get("body", ""))
    taboos = (category.get("voice") or {}).get("vocab_taboo", [])
    lower = body.lower()

    for taboo in taboos:
        if taboo.lower() in lower:
            body = body.replace(taboo, "")
            body = clean(body)

    # Keep exactly one clear CTA concept.
    result["body"] = body
    if result.get("cta") not in {"yes_no", "open_ended", "none"}:
        result["cta"] = "open_ended"

    return result


@app.post("/v1/context")
async def push_context(body: ContextBody):
    if body.scope not in VALID_SCOPES:
        return {"accepted": False, "reason": "invalid_scope", "details": "scope must be category, merchant, customer, or trigger"}

    key = (body.scope, body.context_id)
    current = contexts.get(key)

    if current and current["version"] >= body.version:
        return {
            "accepted": False,
            "reason": "stale_version",
            "current_version": current["version"]
        }

    contexts[key] = {
        "version": body.version,
        "payload": body.payload,
        "delivered_at": body.delivered_at
    }

    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.utcnow().isoformat() + "Z"
    }


@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []

    for trigger_id in body.available_triggers:
        if len(actions) >= 20:
            break

        trigger = get_context("trigger", trigger_id)
        if not trigger:
            continue

        merchant_id = find_trigger_merchant(trigger)
        if not merchant_id:
            continue

        merchant = get_context("merchant", merchant_id)
        if not merchant:
            continue

        cat_slug = merchant.get("category_slug")
        category = get_context("category", cat_slug)
        if not category:
            continue

        customer_id = (
            trigger.get("customer_id")
            or (trigger.get("payload") or {}).get("customer_id")
        )
        customer = get_context("customer", customer_id) if customer_id else None

        result = validate_message(compose(category, merchant, trigger, customer), category)
        key = result["suppression_key"]

        # Suppress exact replay of the same trigger/message.
        if key in sent_keys:
            continue

        conv_id = f"conv_{merchant_id}_{trigger_id}_{uuid.uuid4().hex[:8]}"
        conversations[conv_id] = {
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "trigger_id": trigger_id,
            "messages": [],
            "sent_body": result["body"],
            "auto_reply_count": 0
        }
        sent_keys.add(key)

        actions.append({
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": result["send_as"],
            "trigger_id": trigger_id,
            "template_name": "vera_generic_v1",
            "template_params": [merchant.get("identity", {}).get("name", "")],
            "body": result["body"],
            "cta": result["cta"],
            "suppression_key": result["suppression_key"],
            "rationale": result["rationale"]
        })

    return {"actions": actions[:20]}


@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv = conversations.setdefault(body.conversation_id, {
        "merchant_id": body.merchant_id,
        "customer_id": body.customer_id,
        "messages": [],
        "auto_reply_count": 0
    })

    msg = clean(body.message)
    conv["messages"].append({"from": body.from_role, "message": msg})

    low = msg.lower()

    # Auto-reply loop protection.
    auto_phrases = [
        "thank you for contacting", "thanks for contacting",
        "we will get back to you", "will get back to you",
        "automated response", "auto reply", "out of office"
    ]
    if any(p in low for p in auto_phrases):
        conv["auto_reply_count"] += 1
        if conv["auto_reply_count"] >= 2:
            return {
                "action": "end",
                "rationale": "Repeated automated reply detected; exiting gracefully to avoid message loop."
            }
        return {
            "action": "wait",
            "wait_seconds": 1800,
            "rationale": "Likely automated response; backing off before another message."
        }

    # Explicit opt-out.
    if any(x in low for x in ["stop", "unsubscribe", "not interested", "don't message", "do not message"]):
        return {
            "action": "end",
            "rationale": "Merchant/customer indicated they do not want to continue."
        }

    # Immediate action intent — do not ask another qualification question.
    if any(x in low for x in ["ok let's do it", "okay let's do it", "lets do it", "let's do it", "yes", "do it", "go ahead", "send it"]):
        return {
            "action": "send",
            "body": "Absolutely — moving ahead with the agreed next step now.",
            "cta": "open_ended",
            "rationale": "Positive action intent detected; moving directly to execution instead of re-qualifying."
        }

    # Time/backoff.
    if any(x in low for x in ["later", "tomorrow", "give me time", "not now", "busy"]):
        return {
            "action": "wait",
            "wait_seconds": 1800,
            "rationale": "Merchant asked for more time; backing off rather than pushing."
        }

    # Hostile/off-topic: remain polite and redirect.
    if "gst" in low or "tax" in low or "politics" in low:
        return {
            "action": "send",
            "body": "Got it. I’m focused on helping with your magicpin growth and customer engagement. If you want, we can continue with the earlier Vera action.",
            "cta": "open_ended",
            "rationale": "Off-topic request detected; politely redirected to Vera's current mission."
        }

    # Generic positive/neutral continuation.
    if any(x in low for x in ["tell me more", "how", "details", "what do you suggest", "interested"]):
        return {
            "action": "send",
            "body": "Sure — I can keep this focused on one practical next step. Shall we proceed with the option from the earlier message?",
            "cta": "open_ended",
            "rationale": "Merchant showed interest; advancing with one low-friction next step."
        }

    return {
        "action": "wait",
        "wait_seconds": 900,
        "rationale": "No clear action intent detected; avoiding unnecessary repeated messaging."
    }


@app.get("/v1/healthz")
async def healthz():
    counts = {scope: 0 for scope in VALID_SCOPES}
    for scope, _ in contexts:
        counts[scope] += 1

    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": {
            "category": counts["category"],
            "merchant": counts["merchant"],
            "customer": counts["customer"],
            "trigger": counts["trigger"]
        }
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Vera AI Team",
        "team_members": ["Charvi"],
        "model": "deterministic-rule-engine",
        "approach": "grounded deterministic composer using category, merchant, trigger and customer context",
        "contact_email": "",
        "version": "1.0.0",
        "submitted_at": datetime.utcnow().isoformat() + "Z"
    }
