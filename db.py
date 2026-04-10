import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "cryp.db")


import psycopg
from psycopg.rows import dict_row

def get_conn():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        return psycopg.connect(database_url, row_factory=dict_row)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_user_id INTEGER PRIMARY KEY,
        username TEXT,
        email TEXT,
        is_pro INTEGER DEFAULT 0,
        paystack_customer_code TEXT,
        paystack_subscription_code TEXT,
        paystack_email_token TEXT,
        subscription_status TEXT,
        current_period_end TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reference TEXT UNIQUE,
        telegram_user_id INTEGER,
        amount INTEGER,
        currency TEXT,
        status TEXT,
        event_type TEXT,
        paid_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

def create_or_update_user(telegram_user_id, username=None, email=None):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT telegram_user_id FROM users WHERE telegram_user_id = %s",
        (telegram_user_id,)
    )
    existing = cur.fetchone()

    if existing:
        cur.execute("""
            UPDATE users
            SET username = COALESCE(%s, username),
                email = COALESCE(%s, email),
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_user_id = %s
        """, (username, email, telegram_user_id))
    else:
        cur.execute("""
            INSERT INTO users (telegram_user_id, username, email)
            VALUES (%s, %s, %s)
        """, (telegram_user_id, username, email))

    conn.commit()
    conn.close()


def get_user(telegram_user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT * FROM users WHERE telegram_user_id = %s",
        (telegram_user_id,)
    )
    row = cur.fetchone()

    conn.close()
    return row


def set_user_pro(
    telegram_user_id,
    is_pro,
    subscription_status=None,
    paystack_customer_code=None,
    paystack_subscription_code=None,
    paystack_email_token=None,
    current_period_end=None
):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET is_pro = %s,
            subscription_status = COALESCE(%s, subscription_status),
            paystack_customer_code = COALESCE(%s, paystack_customer_code),
            paystack_subscription_code = COALESCE(%s, paystack_subscription_code),
            paystack_email_token = COALESCE(%s, paystack_email_token),
            current_period_end = COALESCE(%s, current_period_end),
            updated_at = CURRENT_TIMESTAMP
        WHERE telegram_user_id = %s
    """, (
        is_pro,
        subscription_status,
        paystack_customer_code,
        paystack_subscription_code,
        paystack_email_token,
        current_period_end,
        telegram_user_id
    ))

    conn.commit()
    conn.close()