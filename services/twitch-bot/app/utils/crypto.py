import base64
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

class CryptoUtils:
    def __init__(self, secret_key: str):
        self.key = secret_key.encode('utf-8')
        self.mode = AES.MODE_ECB

    def decrypt(self, encrypted_b64: str) -> str | None:
        if not encrypted_b64:
            return None

        try:
            encrypted_data = base64.b64decode(encrypted_b64)

            cipher = AES.new(self.key, self.mode)

            decrypted_data = unpad(cipher.decrypt(encrypted_data), AES.block_size)

            return decrypted_data.decode('utf-8')

        except Exception as e:
            print(f"Fehler bei der Entschlüsselung: {e}")
            return None

    def encrypt(self, plain_text: str) -> str | None:
        if not plain_text:
            return None

        cipher = AES.new(self.key, self.mode)

        padded_data = pad(plain_text.encode('utf-8'), AES.block_size)
        encrypted_data = cipher.encrypt(padded_data)

        return base64.b64encode(encrypted_data).decode('utf-8')