"""
Message Search
--------------
Full-text keyword search across every parsed chat, for an investigator
looking for a specific word or phrase rather than browsing chat by chat.
"""

import re
from dataclasses import dataclass
from .db_parser import MIN_TS


@dataclass
class SearchHit:
    chat_jid: str
    chat_name: str
    message: object   # the db_parser.Message this hit is in
    snippet: str      # a short excerpt around the match, for quick scanning


def search_messages(chats, keyword: str, case_sensitive: bool = False,
                     whole_word: bool = False) -> list[SearchHit]:
    """
    Searches Message.text across every chat for `keyword`. Returns one
    SearchHit per matching message (not per occurrence within a message),
    each carrying a short surrounding-context snippet.
    """
    if not keyword or not keyword.strip():
        return []

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.escape(keyword)
    if whole_word:
        pattern = rf"\b{pattern}\b"
    regex = re.compile(pattern, flags)

    hits = []
    for chat in chats:
        for msg in chat.messages:
            if not msg.text:
                continue
            match = regex.search(msg.text)
            if not match:
                continue
            start = max(0, match.start() - 30)
            end = min(len(msg.text), match.end() + 30)
            snippet = msg.text[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(msg.text):
                snippet = snippet + "..."
            hits.append(SearchHit(chat_jid=chat.jid, chat_name=chat.display_name,
                                   message=msg, snippet=snippet))

    # Most recent first — an investigator searching for a keyword is
    # usually most interested in when it was last said, not first.
    hits.sort(key=lambda h: h.message.timestamp or MIN_TS, reverse=True)
    return hits
