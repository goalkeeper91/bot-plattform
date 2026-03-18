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
    secret_key = twitch_config.secret_key

    if not bot_id:
        print("❌ TWITCH_BOT_ID ist nicht gesetzt.")
        return

    print(f"🤖 Bot ID aus Config: {bot_id}")
    print(f"🔑 Secret Key (Start/Ende): {secret_key[:2]}...{secret_key[-2:]} (Länge: {len(secret_key)})")

    crypto = CryptoUtils(secret_key)

    print("\nWas möchtest du tun?")
    print("1: Neuen Bot-Token setzen")
    print("2: Aktuellen Status prüfen")
    print("3: Token für ANDEREN User setzen")

    choice = input("👉 Auswahl (1/2/3): ").strip()

    target_id = bot_id
    if choice == "3":
        target_id = input("👉 Twitch User ID eingeben: ").strip()

    if choice == "2":
        await check_status(db_config, target_id, crypto)
        return

    access_token = input("👉 Access Token eingeben: ").strip()
    refresh_token = input("👉 Refresh Token eingeben: ").strip()

    if not access_token or not refresh_token:
        print("❌ Token dürfen nicht leer sein.")
        return

    # Self-test encryption
    enc_access = crypto.encrypt(access_token)
    enc_refresh = crypto.encrypt(refresh_token)

    dec_test = crypto.decrypt(enc_access)
    if dec_test != access_token:
        print("❌ Interner Fehler: Verschlüsselung/Entschlüsselung Check fehlgeschlagen!")
        return
    print("✅ Crypto-Check OK.")

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
        # 1. Ensure user exists
        user_row = await conn.fetchrow("SELECT * FROM users WHERE twitch_id = $1", target_id)

        is_bot_val = True if target_id == bot_id else False
        
        if not user_row:
            print(f"ℹ️ User {target_id} existiert nicht. Erstelle neuen Eintrag...")
            await conn.execute("""
                INSERT INTO users (twitch_id, username, is_bot, created_at, updated_at)
                VALUES ($1, $2, $3, NOW(), NOW())
            """, target_id, f"user_{target_id}", is_bot_val)
        else:
            print(f"ℹ️ User {target_id} gefunden. Update...")
            if target_id == bot_id:
                 await conn.execute("UPDATE users SET is_bot = TRUE, updated_at = NOW() WHERE twitch_id = $1", target_id)

        # 2. Insert or Update token
        print("💾 Speichere Token...")
        await conn.execute("""
            INSERT INTO twitch_auth_tokens (twitch_user_id, access_token, refresh_token, created_at, updated_at)
            VALUES ($1, $2, $3, NOW(), NOW())
            ON CONFLICT (twitch_user_id) DO UPDATE 
            SET access_token = $2, 
                refresh_token = $3, 
                updated_at = NOW()
        """, target_id, enc_access, enc_refresh)

        print(f"✅ Token für {target_id} erfolgreich gespeichert!")
        print("🔄 Bitte starte den Bot-Service neu.")

    except Exception as e:
        print(f"❌ Fehler beim Speichern: {e}")
    finally:
        await conn.close()

async def check_status(db_config, target_id, crypto):
    print("⏳ Prüfe DB...")
    conn = await asyncpg.connect(**{k: getattr(db_config, k) for k in ['host', 'port', 'user', 'password', 'database']})
    try:
        row = await conn.fetchrow("""
            SELECT t.access_token, t.refresh_token, u.is_bot 
            FROM twitch_auth_tokens t
            JOIN users u ON t.twitch_user_id = u.twitch_id
            WHERE t.twitch_user_id = $1
        """, target_id)

        if not row:
            print(f"❌ Kein Eintrag für {target_id} gefunden.")
            return

        print(f"User is_bot: {row['is_bot']}")

        acc = crypto.decrypt(row['access_token'])
        if acc:
            print("✅ Access Token: Entschlüsselung erfolgreich!")
            print(f"   Token beginnt mit: {acc[:4]}...")
        else:
            print("❌ Access Token: Entschlüsselung FEHLGESCHLAGEN (Falscher Key?)")

    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
