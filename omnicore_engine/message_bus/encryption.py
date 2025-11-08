# message_bus/encryption.py

from typing import Protocol, List
from cryptography.fernet import Fernet, MultiFernet


class EncryptionStrategy(Protocol):
    def encrypt(self, data: bytes) -> bytes:
        ...

    def decrypt(self, data: bytes) -> bytes:
        ...


class FernetEncryption:
    def __init__(self, keys: List[bytes]):  # Now takes a list of keys
        """
        Initializes FernetEncryption with one or more keys for key rotation.

        Args:
            keys (List[bytes]): A list of one or more URL-safe base64-encoded keys.
                                The first key in the list is the primary key for encryption.
        """
        if not keys or not all(keys):
            raise ValueError("At least one encryption key is required, and none can be empty.")
        
        fernets = [Fernet(key) for key in keys]
        self.multi_fernet = MultiFernet(fernets)

    def encrypt(self, data: bytes) -> bytes:
        """
        Encrypts data using the newest key in the key list.
        """
        return self.multi_fernet.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        """
        Decrypts data by trying all keys in the key list until one is successful.
        """
        return self.multi_fernet.decrypt(data)