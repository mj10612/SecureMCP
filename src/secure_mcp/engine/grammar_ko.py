"""Korean grammar, agglutinative particle (조사) separation, and token classification engine."""

from __future__ import annotations

import re
from typing import Optional, Set, Tuple
from secure_mcp.models import TokenType


# Korean closed-class connective and grammatical functional words (접속사, 기능부사, 지시대명사 등)
KOREAN_FUNCTION_WORDS: Set[str] = {
    # 접속사 / 연결어
    "그리고", "그러나", "그런데", "하지만", "따라서", "그러므로", "또한", "및",
    "또는", "혹은", "게다가", "더구나", "결국", "즉", "그러면", "그럼", "그래서",
    "비록", "반면에", "한편", "오히려", "요컨대",

    # 지시대명사 / 관형사
    "이", "그", "저", "이것", "그것", "저것", "여기", "거기", "저기",
    "이곳", "그곳", "저곳", "이때", "그때", "저때", "이런", "그런", "저런",
    "모든", "어떤", "각", "각각", "매", "몇", "여러", "온갖", "무슨",

    # 기능 부사 (정도/빈도/양태)
    "매우", "아주", "너무", "더", "덜", "가장", "제일", "이미", "벌써",
    "아직", "항상", "늘", "자주", "가끔", "때때로", "전혀", "결코", "오직",
    "단지", "다만", "함께", "같이", "서로", "스스로", "직접", "특히", "주로",
    "참", "정말", "진짜", "반드시", "꼭", "아마", "혹시", "어쩌면", "과연",

    # 보조용언 / 기본 어미 표현
    "있다", "없다", "계시다", "않다", "못하다", "되다", "하다",
    "있으며", "없으며", "있고", "없고", "있지만", "없지만",
}

# Sorted by length in descending order for greedy longest-match stripping
KOREAN_JOSA_LIST: list[str] = sorted([
    # 복합 조사 / 장문 조사
    "으로부터", "로부터", "한테서부터", "에서부터", "에게서는", "에게서", "에게도", "에게만",
    "한테서", "한테는", "한테도", "한테만", "에서는", "에서도", "에서만",
    "으로서", "로서", "으로써", "로써", "으로는", "로는", "으로도", "로도", "으로만", "로만",
    "와는", "과는", "와도", "과도", "와의", "과의", "에는", "에도", "에만",
    "까지는", "까지도", "부터는", "부터도", "이라도", "라도", "이나마", "나마",
    "이야말로", "야말로", "조차도", "마저도", "뿐만아니라",
    "에게", "한테", "에서", "으로", "보다", "처럼", "만큼", "까지", "부터", "이라",
    "라도", "나마", "조차", "마저", "대로", "따라", "에게", "한테", "께서", "이며",
    "이나", "이란", "이야", "치고", "마는", "하구",

    # 단일 조사
    "은", "는", "이", "가", "을", "를", "에", "의", "로", "도", "만", "와", "과",
    "야", "여", "랑", "서", "께", "엔", "론", "선"
], key=len, reverse=True)

# Copula / Predicate endings attached to nouns (서술격조사 / 서술어미)
KOREAN_COPULA_LIST: list[str] = sorted([
    "이었습니다", "였습니다", "이었습니다", "이었습니다만", "였습니다만",
    "입니다", "였습니다", "입니다만", "이었다", "였다", "이고", "이며",
    "이면", "이라서", "이지만", "이라", "이다", "인", "일"
], key=len, reverse=True)

# Plural suffixes
KOREAN_PLURAL = "들"


def is_hangul_char(ch: str) -> bool:
    """Return True if character is a Korean Hangul syllable."""
    return 0xAC00 <= ord(ch) <= 0xD7A3


def is_hangul_string(s: str) -> bool:
    """Return True if string contains Korean Hangul characters."""
    return any(is_hangul_char(c) for c in s)


def has_batchim(ch: str) -> bool:
    """Return True if the Korean Hangul syllable has a final consonant (받침)."""
    if not is_hangul_char(ch):
        return False
    return (ord(ch) - 0xAC00) % 28 != 0


class KoreanGrammarEngine:
    """Decomposes Korean agglutinative word forms into content stem and grammatical particle/suffix."""

    @staticmethod
    def is_function_word(word: str) -> bool:
        """Check if entire token is a closed-class grammatical functional word."""
        clean = word.strip(".,!?;:\"'()[]{}~`")
        return clean in KOREAN_FUNCTION_WORDS

    @classmethod
    def decompose_token(cls, token: str) -> Tuple[str, str, TokenType]:
        """Decompose an agglutinative Korean token into (stem, grammatical_suffix, token_type).

        Example:
            '삼성전자가' -> ('삼성전자', '가', TokenType.ENTITY)
            '고객들에게' -> ('고객', '들에게', TokenType.NOUN)
            '결과입니다' -> ('결과', '입니다', TokenType.NOUN)
            '그리고'     -> ('그리고', '', TokenType.GRAMMAR)
        """
        clean = token.strip()
        if not clean:
            return token, "", TokenType.GRAMMAR

        # If it's a known standalone function word
        if cls.is_function_word(clean):
            return clean, "", TokenType.GRAMMAR

        # Numbers with Korean units (e.g. 100만, 500원, 30개)
        num_match = re.match(r'^(\d+(?:,\d{3})*(?:\.\d+)?)(원|개|명|달러|건|회|년|월|일|시|분|초|%|만|억|조)?(.*)$', clean)
        if num_match:
            val, unit, rest = num_match.groups()
            return val + (unit or ""), rest or "", TokenType.NUMBER

        # Check for plural suffix + josa (e.g., 고객들에게 -> stem: 고객, suffix: 들에게)
        best_stem = clean
        best_suffix = ""

        # Check copula endings first (e.g. 병원입니다 -> 병원 + 입니다)
        for copula in KOREAN_COPULA_LIST:
            if clean.endswith(copula) and len(clean) > len(copula):
                stem_candidate = clean[:-len(copula)]
                # Ensure stem is substantive
                if len(stem_candidate) >= 1:
                    best_stem = stem_candidate
                    best_suffix = copula
                    break

        # If not copula, check plural + josa
        if not best_suffix:
            # Check plural prefix in josa (e.g. 들에게)
            for josa in KOREAN_JOSA_LIST:
                plural_josa = KOREAN_PLURAL + josa
                if clean.endswith(plural_josa) and len(clean) > len(plural_josa):
                    best_stem = clean[:-len(plural_josa)]
                    best_suffix = plural_josa
                    break

        # If not plural+josa, check standard josa
        if not best_suffix:
            for josa in KOREAN_JOSA_LIST:
                if clean.endswith(josa) and len(clean) > len(josa):
                    candidate = clean[:-len(josa)]
                    # Heuristic: Avoid stripping if stem is empty or single jamo
                    if len(candidate) >= 1 and is_hangul_string(candidate):
                        best_stem = candidate
                        best_suffix = josa
                        break

        # Just plural suffix (고객들 -> 고객 + 들)
        if not best_suffix and clean.endswith(KOREAN_PLURAL) and len(clean) > len(KOREAN_PLURAL):
            best_stem = clean[:-len(KOREAN_PLURAL)]
            best_suffix = KOREAN_PLURAL

        # Determine token type
        # Check if stem looks like entity (company/org/person/capitalized)
        if re.search(r'(전자|통신|증권|생명|건설|병원|대학|학회|재단|협회|포털|네이버|카카오|삼성|엘지|현대|SK|KT|LG)', best_stem, re.IGNORECASE):
            token_type = TokenType.ENTITY
        elif any(c.isupper() for c in best_stem):
            token_type = TokenType.ENTITY
        else:
            token_type = TokenType.NOUN

        return best_stem, best_suffix, token_type
