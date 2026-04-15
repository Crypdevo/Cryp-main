import os
import sqlite3
import psycopg
from psycopg.rows import dict_row

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "cryp.db")


def using_postgres():
    return bool(os.getenv("DATABASE_URL"))


def get_conn():
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        return psycopg.connect(database_url, row_factory=dict_row)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def adapt_query(query):
    """
    Convert Postgres-style %s placeholders to SQLite-style ? placeholders
    when running locally on SQLite.
    """
    if using_postgres():
        return query
    return query.replace("%s", "?")


def execute(cur, query, params=None):
    """
    Safe helper so the same code works for both Postgres and SQLite.
    """
    query = adapt_query(query)
    if params is None:
        cur.execute(query)
    else:
        cur.execute(query, params)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    if using_postgres():
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id BIGINT PRIMARY KEY,
            username TEXT,
            email TEXT,
            is_pro INTEGER DEFAULT 0,
            paystack_customer_code TEXT,
            paystack_subscription_code TEXT,
            paystack_email_token TEXT,
            subscription_status TEXT,
            current_period_end TEXT,
            lemon_customer_id TEXT,
            lemon_subscription_id TEXT,
            lemon_order_id TEXT,
            lemon_product_id TEXT,
            lemon_variant_id TEXT,
            pro_expires_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            reference TEXT UNIQUE,
            telegram_user_id BIGINT,
            amount INTEGER,
            currency TEXT,
            status TEXT,
            event_type TEXT,
            paid_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS crypto_payments (
            id SERIAL PRIMARY KEY,
            telegram_user_id BIGINT NOT NULL,
            telegram_username TEXT,
            network TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount_expected REAL NOT NULL,
            wallet_address TEXT NOT NULL,
            txid TEXT UNIQUE,
            status TEXT DEFAULT 'pending',
            plan_type TEXT DEFAULT 'monthly_pro',
            days_to_grant INTEGER DEFAULT 30,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TEXT,
            rejected_at TEXT,
            notes TEXT
        )
        """)

        try:
            cur.execute("ALTER TABLE users ADD COLUMN pro_expires_at TEXT")
        except Exception:
            pass
        
        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_customer_id TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_subscription_id TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_order_id TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_product_id TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_variant_id TEXT")
        except Exception:
            pass

    else:
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
            lemon_customer_id TEXT,
            lemon_subscription_id TEXT,
            lemon_order_id TEXT,
            lemon_product_id TEXT,
            lemon_variant_id TEXT,
            pro_expires_at TEXT,
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

        cur.execute("""
        CREATE TABLE IF NOT EXISTS crypto_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL,
            telegram_username TEXT,
            network TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount_expected REAL NOT NULL,
            wallet_address TEXT NOT NULL,
            txid TEXT UNIQUE,
            status TEXT DEFAULT 'pending',
            plan_type TEXT DEFAULT 'monthly_pro',
            days_to_grant INTEGER DEFAULT 30,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            approved_at TEXT,
            rejected_at TEXT,
            notes TEXT
        )
        """)

        try:
            cur.execute("ALTER TABLE users ADD COLUMN pro_expires_at TEXT")
        except Exception:
            pass
        
        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_customer_id TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_subscription_id TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_order_id TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_product_id TEXT")
        except Exception:
            pass

        try:
            cur.execute("ALTER TABLE users ADD COLUMN lemon_variant_id TEXT")
        except Exception:
            pass

    conn.commit()
    conn.close()


def create_or_update_user(telegram_user_id, username=None, email=None):
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        "SELECT telegram_user_id FROM users WHERE telegram_user_id = %s",
        (telegram_user_id,)
    )
    existing = cur.fetchone()

    if existing:
        execute(
            cur,
            """
            UPDATE users
            SET username = COALESCE(%s, username),
                email = COALESCE(%s, email),
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_user_id = %s
            """,
            (username, email, telegram_user_id)
        )
    else:
        execute(
            cur,
            """
            INSERT INTO users (telegram_user_id, username, email)
            VALUES (%s, %s, %s)
            """,
            (telegram_user_id, username, email)
        )

    conn.commit()
    conn.close()


def get_user(telegram_user_id):
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
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
    current_period_end=None,
    pro_expires_at=None
):
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        """
        UPDATE users
        SET is_pro = %s,
            subscription_status = COALESCE(%s, subscription_status),
            paystack_customer_code = COALESCE(%s, paystack_customer_code),
            paystack_subscription_code = COALESCE(%s, paystack_subscription_code),
            paystack_email_token = COALESCE(%s, paystack_email_token),
            current_period_end = COALESCE(%s, current_period_end),
            pro_expires_at = COALESCE(%s, pro_expires_at),
            updated_at = CURRENT_TIMESTAMP
        WHERE telegram_user_id = %s
        """,
        (
            is_pro,
            subscription_status,
            paystack_customer_code,
            paystack_subscription_code,
            paystack_email_token,
            current_period_end,
            pro_expires_at,
            telegram_user_id
        )
    )

    conn.commit()
    conn.close()
    
def create_crypto_payment(
    telegram_user_id,
    telegram_username,
    network,
    currency,
    amount_expected,
    wallet_address,
    txid
):
    conn = get_conn()
    cur = conn.cursor()

    if using_postgres():
        execute(
            cur,
            """
            INSERT INTO crypto_payments (
                telegram_user_id,
                telegram_username,
                network,
                currency,
                amount_expected,
                wallet_address,
                txid,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                telegram_user_id,
                telegram_username,
                network,
                currency,
                amount_expected,
                wallet_address,
                txid,
                "pending"
            )
        )
        row = cur.fetchone()
        payment_id = row["id"] if row else None
    else:
        execute(
            cur,
            """
            INSERT INTO crypto_payments (
                telegram_user_id,
                telegram_username,
                network,
                currency,
                amount_expected,
                wallet_address,
                txid,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                telegram_user_id,
                telegram_username,
                network,
                currency,
                amount_expected,
                wallet_address,
                txid,
                "pending"
            )
        )
        payment_id = cur.lastrowid

    conn.commit()
    conn.close()
    return payment_id


def get_pending_crypto_payments():
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        """
        SELECT *
        FROM crypto_payments
        WHERE status = %s
        ORDER BY created_at ASC
        """,
        ("pending",)
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def approve_crypto_payment(payment_id):
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        """
        UPDATE crypto_payments
        SET status = %s,
            approved_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        ("approved", payment_id)
    )

    conn.commit()
    conn.close()


def reject_crypto_payment(payment_id, notes=None):
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        """
        UPDATE crypto_payments
        SET status = %s,
            rejected_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP,
            notes = COALESCE(%s, notes)
        WHERE id = %s
        """,
        ("rejected", notes, payment_id)
    )

    conn.commit()
    conn.close() 
    
from datetime import datetime


def get_expired_pro_users():
    conn = get_conn()
    cur = conn.cursor()

    now_iso = datetime.utcnow().isoformat()

    execute(
        cur,
        """
        SELECT *
        FROM users
        WHERE is_pro = %s
          AND pro_expires_at IS NOT NULL
          AND pro_expires_at <= %s
        """,
        (1, now_iso)
    )
    rows = cur.fetchall()

    conn.close()
    return rows


def expire_user_pro(telegram_user_id):
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        """
        UPDATE users
        SET is_pro = %s,
            subscription_status = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE telegram_user_id = %s
        """,
        (0, "expired", telegram_user_id)
    )

    conn.commit()
    conn.close()  
    
def set_user_lemon(
    telegram_user_id,
    is_pro=None,
    subscription_status=None,
    lemon_customer_id=None,
    lemon_subscription_id=None,
    lemon_order_id=None,
    lemon_product_id=None,
    lemon_variant_id=None,
    current_period_end=None,
    pro_expires_at=None
):
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        """
        UPDATE users
        SET is_pro = COALESCE(%s, is_pro),
            subscription_status = COALESCE(%s, subscription_status),
            lemon_customer_id = COALESCE(%s, lemon_customer_id),
            lemon_subscription_id = COALESCE(%s, lemon_subscription_id),
            lemon_order_id = COALESCE(%s, lemon_order_id),
            lemon_product_id = COALESCE(%s, lemon_product_id),
            lemon_variant_id = COALESCE(%s, lemon_variant_id),
            current_period_end = COALESCE(%s, current_period_end),
            pro_expires_at = COALESCE(%s, pro_expires_at),
            updated_at = CURRENT_TIMESTAMP
        WHERE telegram_user_id = %s
        """,
        (
            is_pro,
            subscription_status,
            lemon_customer_id,
            lemon_subscription_id,
            lemon_order_id,
            lemon_product_id,
            lemon_variant_id,
            current_period_end,
            pro_expires_at,
            telegram_user_id
        )
    )

    conn.commit()
    conn.close()
    
def get_user_by_email(email):
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        "SELECT * FROM users WHERE email = %s",
        (email,)
    )
    row = cur.fetchone()

    conn.close()
    return row    

def init_alerts_table():
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            coin TEXT NOT NULL,
            condition TEXT NOT NULL,
            target REAL NOT NULL,
            premium INTEGER NOT NULL DEFAULT 0
        )
        """
        if not using_postgres()
        else
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            coin TEXT NOT NULL,
            condition TEXT NOT NULL,
            target DOUBLE PRECISION NOT NULL,
            premium INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    conn.commit()
    conn.close()


def get_all_alerts():
    conn = get_conn()
    cur = conn.cursor()

    execute(
        cur,
        """
        SELECT user_id, coin, condition, target, premium
        FROM alerts
        ORDER BY id ASC
        """
    )
    rows = cur.fetchall()
    conn.close()

    alerts = []
    for row in rows:
        alerts.append({
            "user_id": row["user_id"],
            "coin": row["coin"],
            "condition": row["condition"],
            "target": float(row["target"]),
            "premium": bool(row["premium"]),
        })

    return alerts


def replace_all_alerts(alerts):
    conn = get_conn()
    cur = conn.cursor()

    execute(cur, "DELETE FROM alerts")

    for alert in alerts:
        execute(
            cur,
            """
            INSERT INTO alerts (user_id, coin, condition, target, premium)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                alert["user_id"],
                alert["coin"],
                alert.get("condition", "above"),
                float(alert["target"]),
                1 if alert.get("premium", False) else 0,
            )
        )

    conn.commit()
    conn.close()         