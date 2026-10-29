"""Guarda segredos (token do GitHub, chaves de API) no Gerenciador de Credenciais do Windows.

Sem dependências: usa a API do Windows (advapi32) por ctypes. Fora do Windows, ou se a chamada
falhar, devolve None/False e o chamador mantém o comportamento antigo (arquivo de configurações).
"""
from __future__ import annotations

import sys
from typing import Protocol

SERVICE = "UsagiGameLauncher"
MAX_BLOB = 2500  # limite do Windows é 2560 bytes


class Backend(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> bool: ...
    def delete(self, name: str) -> bool: ...


class MemoryBackend:
    """Para testes e para quem não tem cofre do sistema."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.data.get(name)

    def set(self, name: str, value: str) -> bool:
        self.data[name] = value
        return True

    def delete(self, name: str) -> bool:
        self.data.pop(name, None)
        return True


class WindowsBackend:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ct, self.wt = ctypes, wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
            ]

        self.CREDENTIAL = CREDENTIAL
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        adv.CredReadW.restype = wintypes.BOOL
        adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        adv.CredWriteW.restype = wintypes.BOOL
        adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        adv.CredDeleteW.restype = wintypes.BOOL
        adv.CredFree.argtypes = [ctypes.c_void_p]
        self.adv = adv

    @staticmethod
    def _target(name: str) -> str:
        return f"{SERVICE}/{name}"

    def get(self, name: str) -> str | None:
        ct = self.ct
        ptr = ct.POINTER(self.CREDENTIAL)()
        if not self.adv.CredReadW(self._target(name), self.CRED_TYPE_GENERIC, 0, ct.byref(ptr)):
            return None
        try:
            cred = ptr.contents
            raw = ct.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            return raw.decode("utf-16-le")
        except (ValueError, OSError):
            return None
        finally:
            self.adv.CredFree(ptr)

    def set(self, name: str, value: str) -> bool:
        ct = self.ct
        blob = value.encode("utf-16-le")
        if len(blob) > MAX_BLOB:
            return False
        buf = (ct.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = self.CREDENTIAL()
        cred.Type = self.CRED_TYPE_GENERIC
        cred.TargetName = self._target(name)
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = ct.cast(buf, ct.POINTER(ct.c_ubyte))
        cred.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        cred.UserName = SERVICE
        return bool(self.adv.CredWriteW(ct.byref(cred), 0))

    def delete(self, name: str) -> bool:
        return bool(self.adv.CredDeleteW(self._target(name), self.CRED_TYPE_GENERIC, 0))


def default_backend() -> Backend | None:
    if sys.platform != "win32":
        return None
    try:
        return WindowsBackend()
    except Exception:  # sem advapi32 ou ctypes quebrado: cai para o arquivo
        return None
