from __future__ import annotations

from .base import BaseParser
from .implementations import GubkinParser, MireaParser, MpeiParser, ReaParser, UnsupportedParser
from .remaining import FaParser, MaiParser, MiitParser, MospolytechParser, RanepaParser, StankinParser

PARSERS: dict[str, BaseParser] = {
    "mirea": MireaParser(),
    "mpei": MpeiParser(),
    "rea": ReaParser(),
    "miit": MiitParser(),
    "stankin": StankinParser(),
    "mospolytech": MospolytechParser(),
    "fa": FaParser(),
    "ranepa": RanepaParser(),
    "mai": MaiParser(),
    "gubkin": GubkinParser(),
}


def get_parser(parser_name: str) -> BaseParser:
    return PARSERS.get(parser_name, UnsupportedParser())
