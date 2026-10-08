import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path

ADMIN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
ITERATIONS = 600_000
MIN_PASSWORD = 8


class AdminStore:
    def __init__(self, path="/config/admins.json", bootstrap_password=""):
        self.path = Path(path)
        self.lock = threading.RLock()
        if self.path.exists():
            self._load()
        else:
            if len(bootstrap_password) < MIN_PASSWORD:
                raise ValueError(f"La contrasena inicial del administrador debe tener al menos {MIN_PASSWORD} caracteres")
            self.data = {"admin": self._hash(bootstrap_password)}
            self._save()

    @staticmethod
    def _validate_name(name):
        if not isinstance(name, str) or not ADMIN.fullmatch(name):
            raise ValueError("El administrador debe empezar por una letra minuscula y usar letras, numeros, _ o -")
        return name

    @staticmethod
    def _validate_password(password):
        if (not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= 256
                or "\n" in password or "\r" in password):
            raise ValueError(f"La contrasena del administrador debe tener entre {MIN_PASSWORD} y 256 caracteres")
        return password

    @staticmethod
    def _hash(password):
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
        return {"salt": salt.hex(), "hash": digest.hex(), "iterations": ITERATIONS}

    def _load(self):
        if self.path.stat().st_mode & 0o077:
            raise ValueError("El archivo de administradores no puede ser legible por el grupo u otros usuarios")
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not value:
            raise ValueError("El archivo de administradores no es valido")
        for name, record in value.items():
            self._validate_name(name)
            if (not isinstance(record, dict) or set(record) != {"salt", "hash", "iterations"}
                    or not isinstance(record["iterations"], int)):
                raise ValueError("El archivo de administradores no es valido")
            bytes.fromhex(record["salt"])
            bytes.fromhex(record["hash"])
        self.data = value

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".admins-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.data, stream, sort_keys=True)
                stream.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def names(self):
        with self.lock:
            return sorted(self.data)

    def verify(self, name, password):
        if not isinstance(password, str) or len(password) > 256:
            return False
        with self.lock:
            record = self.data.get(name) if isinstance(name, str) else None
        # An unknown name costs the same work as a wrong password, so the
        # answer does not tell which administrators exist. The hash is
        # computed outside the lock: it must not hold up the other requests.
        known = record or {"salt": "00" * 16, "hash": "", "iterations": ITERATIONS}
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(known["salt"]),
                                     known["iterations"])
        return bool(record) and hmac.compare_digest(digest.hex(), record["hash"])

    def add(self, name, password):
        with self.lock:
            name = self._validate_name(name)
            password = self._validate_password(password)
            if name in self.data:
                raise ValueError("Ese administrador ya existe")
            self.data[name] = self._hash(password)
            self._save()

    def change_password(self, name, password):
        with self.lock:
            name = self._validate_name(name)
            password = self._validate_password(password)
            if name not in self.data:
                raise ValueError("El administrador no existe")
            self.data[name] = self._hash(password)
            self._save()

    def delete(self, name):
        with self.lock:
            name = self._validate_name(name)
            if name not in self.data:
                raise ValueError("El administrador no existe")
            if len(self.data) == 1:
                raise ValueError("No se puede eliminar el ultimo administrador")
            del self.data[name]
            self._save()
