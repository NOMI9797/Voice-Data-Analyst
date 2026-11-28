"""Fast first-pass screen of the user's QUESTION (before any LLM / SQL).

Catches clearly dangerous requests instantly and explains the refusal in a
sentence that can be spoken. It is deliberately conservative: borderline
questions pass through and are still protected by the SQL guard and the
read-only connection. The SQL guard is the real gate; this layer gives fast,
friendly refusals and stops prompt-injection text from reaching the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RULES: list[tuple[str, str, re.Pattern]] = [
    # (category, spoken refusal, pattern)
    ("data_modification",
     "I can only read data. I can't delete, change, or add anything.",
     re.compile(
         r"\b(delete|drop|truncate|wipe|erase|destroy|purge)\b.{0,40}\b(table|tables|database|db|schema|"
         r"row|rows|record|records|data|everything|all|customers?|orders?|users?|products?)\b"
         r"|\b(update|modify|change|set|overwrite|alter)\b.{0,30}\b(price|prices|record|records|row|rows|"
         r"table|status|values?|column|password|salary|email)s?\b.{0,30}\b(to|=|as)\b"
         r"|\b(insert|add|create)\b.{0,25}\b(new\s+)?(row|record|table|user|entry|column|index)s?\b.{0,25}\b(into|in|to|called|named)\b"
         r"|\b(drop|truncate|alter)\s+(table|database|schema|column|index|view)\b"
         r"|\b(grant|revoke)\b.{0,30}\b(access|privilege|permission|admin|role)s?\b"
         r"|\bremove\s+(all|every)\b"
         r"|\b(delete|drop|remove|erase|wipe|destroy|truncate|update|modify|overwrite)\s+(them|it|those|these|everything)\b",
         re.I)),
    ("prompt_injection",
     "I can't follow instructions that try to change how I work. Please ask a question about the data.",
     re.compile(
         r"\b(ignore|disregard|forget|override|bypass)\b.{0,30}\b(previous|prior|above|earlier|all|your|the|any)\b"
         r".{0,20}\b(instruction|instructions|rules|prompt|guard|guardrails|restrictions|safety)\b"
         r"|\b(system|developer)\s+prompt\b|\bjailbreak\b|\bdeveloper\s+mode\b|\bDAN\b"
         r"|\byou\s+are\s+now\b|\bact\s+as\s+(an?\s+)?(admin|administrator|root|dba|superuser)\b"
         r"|\b(run|execute)\s+(this|the following|raw)\s+(sql|query|command)\b",
         re.I)),
    ("secrets",
     "I can't access passwords, credentials, or other secrets.",
     re.compile(
         r"\b(password|passwords|passwd|credentials?|api\s*keys?|secret\s*keys?|access\s*tokens?|"
         r"hash(ed)?\s+passwords?|credit\s*card\s*numbers?|card\s*numbers?|cvv|social\s+security)\b",
         re.I)),
    ("bulk_personal_data",
     "I can't export personal details like emails, phone numbers or addresses. "
     "I can give counts or summaries instead.",
     re.compile(
         r"\b(all|every|list|export|dump|download|give\s+me|show\s+me|send\s+me)\b.{0,40}"
         r"\b(e-?mails?|e-?mail\s+addresses|phone\s*numbers?|home\s+addresses|addresses|personal\s+(data|details|information|info))\b",
         re.I)),
    ("system_access",
     "I can't access the database system itself, only the business data.",
     re.compile(
         r"\b(dump|export|copy|download|backup)\b.{0,30}\b(entire|whole|full|complete|all)\b.{0,20}\b(database|db|tables)\b"
         r"|\b(database|db)\s+(users?|accounts?|logins?|roles?|config|configuration|settings|version)\b"
         r"|\b(information_schema|pg_catalog|pg_shadow|mysql\.user|sqlite_master)\b"
         r"|\b(shell|command\s+line|operating\s+system|os\s+command|file\s+system|/etc/passwd)\b",
         re.I)),
    ("resource_abuse",
     "That request would overload the database, so I won't run it.",
     re.compile(
         r"\b(sleep|wait)\s+(for\s+)?\d+\s*(seconds?|minutes?|hours?)\b"
         r"|\b(infinite|endless|forever|never[- ]ending)\b.{0,20}\b(loop|query|recursion|rows)\b"
         r"|\bcross\s+join\b.{0,30}\b(every|all)\b.{0,20}\btables?\b"
         r"|\b(billion|trillion)\s+rows\b",
         re.I)),
]


# Whether a question is allowed, and the refusal message if not.
@dataclass
class IntentResult:
    allowed: bool
    category: str = ""
    message: str = ""
    matched: str = ""


# MAIN: refuses clearly dangerous questions at once (delete, passwords, prompt injection...).
def screen_question(question: str) -> IntentResult:
    q = " ".join((question or "").split())
    for category, message, pattern in _RULES:
        m = pattern.search(q)
        if m:
            return IntentResult(False, category, message, m.group(0))
    return IntentResult(True)
