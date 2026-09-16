"""Deterministic content boundaries shared by context and output validation."""

import re
import unicodedata

# Domain terms named in the architecture; graph names extend this vocabulary.
FORBIDDEN_TECH = re.compile(
    r"(?<!\w)(?:ИИ|AI|LLMs?|MCP|GPT|Gemma|llama|docker(?:\.sock)?|seccomp|"
    r"namespaces?|prompt(?:s|ing)?|injection|embeddings?|tokeniz\w*|"
    r"нейросет\w*|промпт\w*|токениз\w*|эмбеддинг\w*|"
    r"искусственн\w*\s+интеллект\w*)(?!\w)",
    re.I,
)

CYCLE = re.compile(
    r"(?<!\w)(?:менстру\w*|месячные|месячных|месячными|пмс|овуля\w*|"
    r"лютеин\w*|фолликуляр\w*|эстроген\w*|прогестерон\w*|"
    r"гормон\w*|критическ\w*\s+дн\w*|женск\w*\s+дн\w*|"
    r"эти\s+дни|красн\w*\s+дн\w*\s+календар\w*|"
    r"menstrua\w*|ovulat\w*|luteal|follicular|pms|estrogen|progesterone|"
    r"hormon\w*|period\s+(?:cramps?|pain|started)|my\s+period|"
    r"(?:that|the)\s+time\s+of\s+the\s+month|aunt\s+flo)(?!\w)",
    re.I,
)


def normalized_text(text: str) -> str:
    return " ".join(
        "".join(
            char
            for char in unicodedata.normalize("NFKC", text)
            if unicodedata.category(char) != "Cf"
        )
        .casefold()
        .split()
    )


def technical_match(text: str, graph_terms: tuple[str, ...] = ()) -> str | None:
    text = normalized_text(text)
    if match := FORBIDDEN_TECH.search(text):
        return match[0]
    for term in graph_terms:
        term = normalized_text(term)
        if term and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text):
            return term
    return None
