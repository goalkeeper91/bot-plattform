import asyncio
import os
import sys
import asyncpg
from app.config import load_db_config, load_twitch_config
from app.utils.crypto import CryptoUtils

async def main():
    print("🔑 Twitch Bot Token Manager")
    print("---------------------------")
    
    try:
        db_config = load_db_config()
        twitch_config = load_twitch_config()
    except Exception as e:
        print(f"❌ Fehler beim Laden der Konfiguration: {e}")
        print("Stelle sicher, dass alle Umgebungsvariablen gesetzt sind (DB_*, TWITCH_*).")
        return

    bot_id = twitch_config.bot_id
    if not bot_id:
        print("❌ TWITCH_BOT_ID ist nicht gesetzt.")
        return

    print(f"🤖 Bot ID aus Config: {bot_id}")
    
    access_token = input("👉 Access Token eingeben: ").strip()
    refresh_token = input("👉 Refresh Token eingeben: ").strip()

    if not access_token or not refresh_token:
        print("❌ Token dürfen nicht leer sein.")
        return

    crypto = CryptoUtils(twitch_config.secret_key)
    
    enc_access = crypto.encrypt(access_token)
    enc_refresh = crypto.encrypt(refresh_token)

    if not enc_access or not enc_refresh:
        print("❌ Fehler bei der Verschlüsselung.")
        return

    print("⏳ Verbinde zur Datenbank...")
    try:
        conn = await asyncpg.connect(
            host=db_config.host,
            port=db_config.port,
            user=db_config.user,
            password=db_config.password,
            database=db_config.database
        )
    except Exception as e:
        print(f"❌ Datenbankverbindung fehlgeschlagen: {e}")
        return

    try:
        # 1. Ensure user exists in users table with is_bot=TRUE
        user_row = await conn.fetchrow("SELECT * FROM users WHERE twitch_id = $1", bot_id)
        
        if not user_row:
            print(f"ℹ️ User {bot_id} existiert nicht. Erstelle neuen Eintrag...")
            await conn.execute("""
                INSERT INTO users (twitch_id, username, is_bot, created_at, updated_at)
                VALUES ($1, $2, TRUE, NOW(), NOW())
            """, bot_id, f"bot_{bot_id}") # Placeholder username
        else:
            print(f"ℹ️ User {bot_id} gefunden. Aktualisiere is_bot Status...")
            await conn.execute("UPDATE users SET is_bot = TRUE, updated_at = NOW() WHERE twitch_id = $1", bot_id)

        # 2. Insert or Update token
        print("💾 Speichere Token...")
        await conn.execute("""
            INSERT INTO twitch_auth_tokens (twitch_user_id, access_token, refresh_token, created_at, updated_at)
            VALUES ($1, $2, $3, NOW(), NOW())
            ON CONFLICT (twitch_user_id) DO UPDATE 
            SET access_token = $2, 
                refresh_token = $3, 
                updated_at = NOW()
        """, bot_id, enc_access, enc_refresh)

        print("✅ Token erfolgreich gespeichert!")
        print("🔄 Bitte starte den Bot-Service neu, damit die Änderungen wirksam werden.")

    except Exception as e:
        print(f"❌ Fehler beim Speichern: {e}")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
