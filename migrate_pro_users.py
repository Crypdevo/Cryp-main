from db import init_db, create_or_update_user, set_user_pro


def migrate():
    init_db()

    try:
        with open("pro_users.txt", "r") as f:
            for line in f:
                line = line.strip()

                if not line:
                    continue

                user_id = int(line)

                create_or_update_user(user_id)
                set_user_pro(user_id, 1, subscription_status="manual_migrated")

        print("Migration complete.")

    except FileNotFoundError:
        print("pro_users.txt not found.")


if __name__ == "__main__":
    migrate()
