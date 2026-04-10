import os
import hmac
import json
import hashlib
from datetime import datetime
from typing import Optional

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from pg_db import init_pg_db, get_user_pg, create_or_update_user_pg, set_user_pro_pg, get_conn

app = FastAPI()

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_PLAN_CODE = os.getenv("PAYSTACK_PLAN_CODE")
PAYSTACK_CALLBACK_URL = os.getenv("PAYSTACK_CALLBACK_URL", "")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("CRYP_DB_PATH", os.path.join(BASE_DIR, "cryp.db"))

if not PAYSTACK_SECRET_KEY:
    raise RuntimeError("Missing PAYSTACK_SECRET_KEY")
if not PAYSTACK_PLAN_CODE:
    raise RuntimeError("Missing PAYSTACK_PLAN_CODE")

def update_user_payment_profile(
    telegram_user_id: int,
    email: Optional[str] = None,
    paystack_customer_code: Optional[str] = None,
    paystack_subscription_code: Optional[str] = None,
    paystack_email_token: Optional[str] = None,
    subscription_status: Optional[str] = None,
    current_period_end: Optional[str] = None,
    is_pro: Optional[bool] = None,
):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM users WHERE telegram_user_id = %s",
        (telegram_user_id,)
    )
    existing = cur.fetchone()

    if not existing:
        cur.execute(
            """
            INSERT INTO users (
                telegram_user_id, username, email, is_pro,
                paystack_customer_code, paystack_subscription_code,
                paystack_email_token, subscription_status,
                current_period_end
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                telegram_user_id,
                None,
                email,
                1 if is_pro else 0,
                paystack_customer_code,
                paystack_subscription_code,
                paystack_email_token,
                subscription_status,
                current_period_end,
            ),
        )
    else:
        fields = []
        values = []

        if email is not None:
            fields.append("email = %s")
            values.append(email)
        if paystack_customer_code is not None:
            fields.append("paystack_customer_code = %s")
            values.append(paystack_customer_code)
        if paystack_subscription_code is not None:
            fields.append("paystack_subscription_code = %s")
            values.append(paystack_subscription_code)
        if paystack_email_token is not None:
            fields.append("paystack_email_token = %s")
            values.append(paystack_email_token)
        if subscription_status is not None:
            fields.append("subscription_status = %s")
            values.append(subscription_status)
        if current_period_end is not None:
            fields.append("current_period_end = %s")
            values.append(current_period_end)
        if is_pro is not None:
            fields.append("is_pro = %s")
            values.append(1 if is_pro else 0)

        fields.append("updated_at = CURRENT_TIMESTAMP")
        values.append(telegram_user_id)

        cur.execute(
            f"UPDATE users SET {', '.join(fields)} WHERE telegram_user_id = %s",
            values,
        )

    conn.commit()
    conn.close()

def verify_paystack_signature(raw_body: bytes, signature: Optional[str]) -> bool:
    if not signature:
        return False

    computed = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512
    ).hexdigest()

    return hmac.compare_digest(computed, signature)


def initialize_checkout(email: str, telegram_user_id: int):
    url = "https://api.paystack.co/transaction/initialize"
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "email": email,
        "amount": 9900,
        "plan": PAYSTACK_PLAN_CODE,
        "callback_url": PAYSTACK_CALLBACK_URL,
        "metadata": {
            "telegram_user_id": telegram_user_id,
            "source": "cryp_bot"
        }
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


@app.on_event("startup")
def startup():
    init_pg_db()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/paystack/checkout")
def paystack_checkout(telegram_user_id: int, email: str):
    try:
        result = initialize_checkout(email=email, telegram_user_id=telegram_user_id)
        auth_url = result["data"]["authorization_url"]

        update_user_payment_profile(
            telegram_user_id=telegram_user_id,
            email=email
        )

        return RedirectResponse(auth_url)

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)}
        )


@app.post("/paystack/webhook")
async def paystack_webhook(
    request: Request,
    x_paystack_signature: Optional[str] = Header(default=None)
):
    raw_body = await request.body()

    if not verify_paystack_signature(raw_body, x_paystack_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = json.loads(raw_body.decode("utf-8"))
    event_type = event.get("event", "")
    data = event.get("data", {}) or {}

    metadata = data.get("metadata", {}) or {}
    customer = data.get("customer", {}) or {}
    subscription = data.get("subscription", {}) or {}

    telegram_user_id = metadata.get("telegram_user_id")
    if telegram_user_id is not None:
        try:
            telegram_user_id = int(telegram_user_id)
        except Exception:
            telegram_user_id = None

    customer_code = customer.get("customer_code")
    customer_email = customer.get("email")

    subscription_code = data.get("subscription_code") or subscription.get("subscription_code")
    email_token = data.get("email_token") or subscription.get("email_token")
    current_period_end = data.get("next_payment_date") or subscription.get("next_payment_date")
    status = data.get("status") or subscription.get("status")

    if event_type in {"charge.success", "invoice.update"} and telegram_user_id:
        update_user_payment_profile(
            telegram_user_id=telegram_user_id,
            email=customer_email,
            paystack_customer_code=customer_code,
            paystack_subscription_code=subscription_code,
            paystack_email_token=email_token,
            subscription_status=status or "active",
            current_period_end=current_period_end,
            is_pro=True,
        )

    elif event_type == "subscription.create" and telegram_user_id:
        update_user_payment_profile(
            telegram_user_id=telegram_user_id,
            email=customer_email,
            paystack_customer_code=customer_code,
            paystack_subscription_code=subscription_code,
            paystack_email_token=email_token,
            subscription_status=status or "active",
            current_period_end=current_period_end,
            is_pro=True,
        )

    elif event_type in {"invoice.payment_failed", "subscription.disable", "subscription.not_renew"} and telegram_user_id:
        update_user_payment_profile(
            telegram_user_id=telegram_user_id,
            subscription_status=status or "inactive",
            current_period_end=current_period_end,
            is_pro=False,
        )

    return {"ok": True}