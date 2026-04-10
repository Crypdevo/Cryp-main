import os
from typing import Optional

import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL")


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_pg_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id BIGINT PRIMARY KEY,
            username TEXT,
            email TEXT,
            is_pro INTEGER DEFAULT 0,
            subscription_status TEXT,
            paystack_customer_code TEXT,
            paystack_subscription_code TEXT,
            paystack_email_token TEXT,
            current_period_end TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def get_user_pg(telegram_user_id: int) -> Optional[dict]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM users WHERE telegram_user_id = %s",
        (telegram_user_id,)
    )
    user = cur.fetchone()

    conn.close()
    return user


def create_or_update_user_pg(telegram_user_id: int, username: Optional[str] = None):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (telegram_user_id, username)
        VALUES (%s, %s)
        ON CONFLICT (telegram_user_id)
        DO UPDATE SET
            username = EXCLUDED.username,
            updated_at = CURRENT_TIMESTAMP
    """, (telegram_user_id, username))

    conn.commit()
    conn.close()


def set_user_pro_pg(
    telegram_user_id: int,
    is_pro: int,
    subscription_status: Optional[str] = None
):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (telegram_user_id, is_pro, subscription_status)
        VALUES (%s, %s, %s)
        ON CONFLICT (telegram_user_id)
        DO UPDATE SET
            is_pro = EXCLUDED.is_pro,
            subscription_status = EXCLUDED.subscription_status,
            updated_at = CURRENT_TIMESTAMP
    """, (telegram_user_id, is_pro, subscription_status))

    conn.commit()
    conn.close()