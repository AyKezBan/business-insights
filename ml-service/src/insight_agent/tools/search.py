import math
import re
from collections import Counter

_WORD = re.compile(r"[a-zа-яё0-9]+(?:[.,][0-9]+)?", re.I)
_STOP = set("и в во на по с со к ко о об от до за из у не что это как для при а но или же ли бы то".split())
_ENDING = re.compile(
    r"(иями|ями|ами|ого|его|ому|ему|ыми|ими|ой|ей|ий|ый|ая|яя|ое|ее|ые|ие|ов|ев|ах|ях|ам|ям|ом|ем|а|я|о|е|и|ы|у|ю|ь)$"
)


def _stem(word: str) -> str:

    if not word.isalpha() or len(word) <= 4:
        return word
    return _ENDING.sub("", word)[:6]


def tokenize(text: str) -> list[str]:
    return [_stem(raw) for raw in _WORD.findall(text.lower().replace("ё", "е")) if raw not in _STOP]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(tokenize(d)) for d in docs]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df = Counter(term for tf in self.tfs for term in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        out = []
        for tf, length in zip(self.tfs, self.lens):
            s = 0.0
            for t in terms:
                if t not in tf:
                    continue
                f = tf[t]
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / (self.avg or 1)))
            out.append(s)
        return out
