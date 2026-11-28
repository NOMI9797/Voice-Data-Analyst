"""Things people say that are not data questions: greetings, thanks, "never mind",
"what can you do?", and "what tables / data do you have?".

Checked before anything else, so "thank you" is never searched for as a song title and
"never mind" is never taken as the answer to a pending clarifying question. Only short,
whole phrases match; a real question ("which customers said thank you") does not.
"""

from __future__ import annotations

import re

_GREETING = re.compile(r"^(hi|hello|hey|hiya|greetings|good (morning|afternoon|evening))( there)?$")
_THANKS = re.compile(r"^((ok|okay|great|perfect|awesome|cool|nice)\s+)?(thanks|thank you|many thanks|cheers)"
                     r"( (so much|a lot|very much))?$")
_CANCEL = re.compile(r"^(never ?mind|cancel( that| it)?|forget (it|that|about it)|stop|nothing|no thanks|"
                     r"no thank you|skip( it| that)?|leave it)$")
_ACK = re.compile(r"^(ok|okay|cool|great|got it|nice|perfect|awesome|alright|all right|i see|good)$")
_HELP = re.compile(r"^(help|what can you do|what can i ask( you)?|how does this work|who are you|what are you)$")
_CATALOG = [
    re.compile(r"^(what|which) (tables|databases|data sources|sources|datasets)( are| do you have| exist| is there)?"
               r"( there| in (the |this )?\w+( database| data)?| connected| available)?$"),
    re.compile(r"^what (kind|sort|type)s? of data (do you have|is there|is available|can i ask about)$"),
    re.compile(r"^what data (do you have|is there|is available|is connected)$"),
    re.compile(r"^(list|show)( me)?( all)?( the)? (tables|databases|data sources)( in (the |this )?\w+( database| data)?)?$"),
    re.compile(r"^what is in (the |this )?\w+( database| data)$"),
]


# Lowercase, no punctuation, single spaces.
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", text.lower())).strip()


# Which kind of small talk this is, or None for a real question.
def classify(text: str) -> str | None:
    t = _norm(text)
    if not t or len(t.split()) > 12:
        return None
    for kind, pattern in (("greeting", _GREETING), ("thanks", _THANKS), ("cancel", _CANCEL),
                          ("ack", _ACK), ("help", _HELP)):
        if pattern.match(t):
            return kind
    if any(p.match(t) for p in _CATALOG):
        return "catalog"
    return None


# The spoken reply for everything except "catalog" (that one lists the real tables).
def reply(kind: str, source_names: list[str]) -> str:
    listed = (source_names[0] if len(source_names) == 1
              else ", ".join(source_names[:-1]) + " and " + source_names[-1]) if source_names else "no data yet"
    return {
        "greeting": "Hello! Ask me anything about your data, for example which products sell best.",
        "thanks": "You're welcome.",
        "cancel": "Okay, cancelled. Ask me something else whenever you're ready.",
        "ack": "Okay.",
        "help": f"I answer questions about your data by voice: right now that's {listed}. Ask things like "
                "how many orders there were each month, or which customers spend the most. "
                "I show a chart, a table and the SQL, and read the answer out loud.",
    }[kind]
