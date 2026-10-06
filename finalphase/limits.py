"""Conservative input-token bounds and per-model dispatch limits."""
from __future__ import annotations

from dataclasses import dataclass

from .providers import Request


def input_token_bound(request: Request) -> int:
    # UTF-8 bytes bound text tokens, with request/framing overhead retained.
    # This never changes the scientific request or its output allowance.
    return len(request.to_json().encode("utf-8")) + 100


@dataclass(frozen=True)
class BatchLimits:
    max_input_tokens: int
    max_requests: int = 5000
    max_in_flight: int = 1

    def __post_init__(self) -> None:
        if any(type(value) is not int or value <= 0 for value in
               (self.max_input_tokens, self.max_requests, self.max_in_flight)):
            raise ValueError("batch limits must be positive integers")

    def waves(self, requests: list[Request]) -> list[list[Request]]:
        waves: list[list[Request]] = []
        wave: list[Request] = []
        tokens = 0
        for request in requests:
            bound = input_token_bound(request)
            if bound > self.max_input_tokens:
                raise ValueError(f"one request exceeds batch input-token capacity: {request.custom_id}")
            if wave and (tokens + bound > self.max_input_tokens or len(wave) >= self.max_requests):
                waves.append(wave)
                wave, tokens = [], 0
            wave.append(request)
            tokens += bound
        if wave:
            waves.append(wave)
        return waves
