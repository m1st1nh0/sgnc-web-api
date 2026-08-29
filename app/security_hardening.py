"""Controles de segurança de aplicação introduzidos no PR08."""
from __future__ import annotations

from collections import defaultdict, deque
from hashlib import sha256
import re
from threading import Lock
import time
from typing import Callable

from fastapi import HTTPException, status


SENHA_MINIMA = 10
SIMBOLOS = r"!@#$%^&*()_+-=[]{};'\:\"|<>?,./`~"


def validar_senha_forte(senha: str) -> None:
    """Valida senhas novas sem interferir no login de usuários existentes."""
    requisitos = [
        (len(senha) >= SENHA_MINIMA, f"ao menos {SENHA_MINIMA} caracteres"),
        (bool(re.search(r"[a-z]", senha)), "uma letra minúscula"),
        (bool(re.search(r"[A-Z]", senha)), "uma letra maiúscula"),
        (bool(re.search(r"\d", senha)), "um número"),
        (any(c in SIMBOLOS for c in senha), "um símbolo"),
    ]
    faltantes = [texto for ok, texto in requisitos if not ok]
    if faltantes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A senha deve conter " + ", ".join(faltantes) + ".",
        )


class LoginRateLimiter:
    """Throttle local de login, complementar aos limites do Supabase Auth.

    O estado é intencionalmente efêmero e por processo. Ele reduz brute force no
    proxy da aplicação sem substituir os rate limits da plataforma. Se a API for
    horizontalmente escalada, este componente deve migrar para um backend
    compartilhado (Redis/Postgres) sem mudar o contrato do router.
    """

    def __init__(
        self,
        max_falhas: int = 8,
        janela_segundos: int = 10 * 60,
        bloqueio_segundos: int = 15 * 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_falhas = max_falhas
        self.janela_segundos = janela_segundos
        self.bloqueio_segundos = bloqueio_segundos
        self.clock = clock
        self._falhas: dict[str, deque[float]] = defaultdict(deque)
        self._bloqueado_ate: dict[str, float] = {}
        self._lock = Lock()

    @staticmethod
    def chave(email: str, peer: str | None) -> str:
        material = f"{email.strip().lower()}|{peer or 'unknown'}".encode("utf-8")
        return sha256(material).hexdigest()

    def _limpar_expiradas(self, chave: str, agora: float) -> None:
        limite = agora - self.janela_segundos
        fila = self._falhas[chave]
        while fila and fila[0] < limite:
            fila.popleft()
        if not fila:
            self._falhas.pop(chave, None)

    def verificar(self, chave: str) -> None:
        agora = self.clock()
        with self._lock:
            bloqueado_ate = self._bloqueado_ate.get(chave)
            if bloqueado_ate is not None and bloqueado_ate <= agora:
                self._bloqueado_ate.pop(chave, None)
                self._falhas.pop(chave, None)
                bloqueado_ate = None
            if bloqueado_ate is not None:
                retry_after = max(1, int(bloqueado_ate - agora))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Muitas tentativas de login. Tente novamente mais tarde.",
                    headers={"Retry-After": str(retry_after)},
                )
            self._limpar_expiradas(chave, agora)

    def registrar_falha(self, chave: str) -> None:
        agora = self.clock()
        with self._lock:
            self._limpar_expiradas(chave, agora)
            fila = self._falhas[chave]
            fila.append(agora)
            if len(fila) >= self.max_falhas:
                self._bloqueado_ate[chave] = agora + self.bloqueio_segundos

    def registrar_sucesso(self, chave: str) -> None:
        with self._lock:
            self._falhas.pop(chave, None)
            self._bloqueado_ate.pop(chave, None)


LOGIN_RATE_LIMITER = LoginRateLimiter()
