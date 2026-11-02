"""Signals about hostile content.

This scanner is not a security boundary and is never relied on as one. The boundary is
structural: model output and inbound text cannot reach the policy engine as anything but
data. The scanner only gives a human reviewer early warning and feeds incident reports.
"""
from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("override_instructions", re.compile(r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|your|safety|system)\b.{0,30}\b(instruction|rule|polic|prompt|guideline)", re.I | re.S)),
    ("role_claim", re.compile(r"\b(you are now|act as|pretend to be|system prompt|developer mode|jailbreak)\b", re.I)),
    ("authority_claim", re.compile(r"\b(the owner|your admin|the ceo|management)\b.{0,40}\b(approved|authori[sz]ed|said|confirmed)\b", re.I | re.S)),
    ("money_movement", re.compile(r"\b(transfer|wire|send|refund|pay)\b.{0,40}(\$|usd|inr|eur|gbp|money|funds|bank)", re.I | re.S)),
    ("secret_exfiltration", re.compile(r"\b(api key|password|credential|access token|bank details|secret)\b.{0,40}\b(send|share|reveal|email|tell|leak)\b|\b(send|share|reveal|email|tell|leak)\b.{0,40}\b(api key|password|credential|access token|bank details|secret)\b", re.I | re.S)),
)


def scan_untrusted(text: str) -> list[str]:
    return [name for name, pattern in _PATTERNS if pattern.search(text)]
