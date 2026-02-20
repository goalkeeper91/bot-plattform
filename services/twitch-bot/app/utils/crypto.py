import base64
from Crypto.Cipher import AES


class CryptoUtils:
    def __init__(self, secret_key: str):
        if len(secret_key) != 32:
            raise ValueError(f"Secret key muss 32 Zeichen lang sein, ist {len(secret_key)}")
        self.key = secret_key.encode('utf-8')

    def decrypt(self, encrypted_b64: str) -> str | None:
        if not encrypted_b64:
            return None

        try:
            data = base64.b64decode(encrypted_b64)

            nonce_size = 12
            tag_size = 16

            if len(data) < nonce_size + tag_size:
                return None

            nonce = data[:nonce_size]
            ciphertext_with_tag = data[nonce_size:]
            ciphertext = ciphertext_with_tag[:-tag_size]
            tag = ciphertext_with_tag[-tag_size:]

            cipher = AES.new(self.key, AES.MODE_GCM, nonce=nonce)

            decrypted_data = cipher.decrypt_and_verify(ciphertext, tag)

            return decrypted_data.decode('utf-8')

        except Exception as e:
            print(f"❌ Fehler bei der Entschlüsselung: {e}")
            return None

    def encrypt(self, plain_text: str) -> str | None:
        if not plain_text:
            return None

        try:
            cipher = AES.new(self.key, AES.MODE_GCM)

            ciphertext, tag = cipher.encrypt_and_digest(plain_text.encode('utf-8'))

            combined_data = cipher.nonce + ciphertext + tag

            return base64.b64encode(combined_data).decode('utf-8')

        except Exception as e:
            print(f"❌ Fehler bei der Verschlüsselung: {e}")
            return None