"""Small language-specific lexer; never interpret literal contents as code."""

from __future__ import annotations

import keyword
import re

from secure_mcp.models import TokenType

LANGUAGE_KEYWORDS: dict[str, set[str]] = {
    "python": set(keyword.kwlist),
    "javascript": set(
        "break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof let new return super switch this throw try typeof var void while with yield async await true false null".split()
    ),
    "typescript": set(
        "abstract as asserts declare enum implements interface is keyof namespace never private protected public readonly satisfies type infer unknown any string number boolean".split()
    ),
    "go": set(
        "break default func interface select case defer go map struct chan else goto package switch const fallthrough if range type continue for import return var".split()
    ),
    "rust": set(
        "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while".split()
    ),
    "java": set(
        "abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null".split()
    ),
    "cpp": set(
        "alignas alignof asm auto bool break case catch char class concept const consteval constexpr constinit continue co_await co_return co_yield decltype default delete do double else enum explicit export extern false float for friend goto if inline int long mutable namespace new noexcept nullptr operator private protected public register reinterpret_cast requires return short signed sizeof static static_assert static_cast struct switch template this thread_local throw true try typedef typeid typename union unsigned using virtual void volatile wchar_t while".split()
    ),
    "sql": set(
        "select from where join left right inner outer on insert into values update set delete group by order having limit offset create table drop alter index as union and or not null is primary key foreign references distinct asc desc case when then else end exists in like between".split()
    ),
}
LANGUAGE_KEYWORDS["typescript"] |= LANGUAGE_KEYWORDS["javascript"]
LANGUAGE_KEYWORDS["c"] = LANGUAGE_KEYWORDS["cpp"]
# Compatibility export; classification always consults the selected language.
CODE_KEYWORDS = set().union(*LANGUAGE_KEYWORDS.values())
ALIASES = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "rs": "rust",
    "c++": "cpp",
}
NUMBER = re.compile(
    r"(?:0[xX][\da-fA-F_'\.]+(?:[pP][+-]?\d+)?|0[bB][01_']+|0[oO][0-7_']+|"
    r"(?:\d[\d_']*(?:\.\d[\d_']*)*|\.\d[\d_']*(?:\.\d[\d_']*)*)"
    r"(?:[eE][+-]?[\d_]+)?)[A-Za-z0-9_]*"
)
IDENTIFIER = re.compile(r"[^\W\d]\w*|_\w*", re.UNICODE)
STRING_START = re.compile(r"[rRuUbBfF]{0,2}(\"\"\"|'''|\"|')")
PREPROCESSOR_DIRECTIVES = {
    "include", "include_next", "define", "undef", "if", "ifdef", "ifndef",
    "elif", "elifdef", "elifndef", "else", "endif", "pragma", "error", "warning", "line",
}


def detect_language(code: str, language: str = "auto") -> str:
    language = ALIASES.get(language.lower(), language.lower())
    if language != "auto":
        if language not in LANGUAGE_KEYWORDS:
            raise ValueError(f"Unsupported code language: {language}")
        return language
    checks = [
        ("python", r"(?m)^\s*(?:async\s+def|def\s+\w+|from\s+[\w.]+\s+import)\b"),
        ("sql", r"(?im)^\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\b"),
        ("go", r"\b(?:package\s+\w+|func\s+\w+|var\s+\w+\s*=\s*`)"),
        ("rust", r"\b(?:fn|impl|unsafe|mod|crate)\b|\br#+\""),
        ("java", r"\b(?:synchronized|throws|public\s+(?:class|static|void))\b"),
        ("cpp", r"#\s*include|\b(?:std::|namespace|constexpr)|\bR\""),
        ("typescript", r"\b(?:interface|readonly|implements)\b"),
        ("javascript", r"\b(?:const|let|function|var)\b|=>"),
        ("javascript", r"(?m)^\s*(?://|/\*)"),
    ]
    return next(
        (name for name, pattern in checks if re.search(pattern, code)), "python"
    )


def quoted_end(code: str, start: int, quote: str, formatted: bool = False) -> int:
    """Walk escapes and f-string expression nesting, including same-quote strings."""
    i = start + len(quote)
    depth = 0
    while i < len(code):
        if code[i] == "\\":
            i += 2
            continue
        if formatted and code[i] == "{" and not code.startswith("{{", i):
            depth += 1
        elif formatted and depth and code[i] == "}":
            depth -= 1
        elif depth and code[i] in "\"'`":
            inner = code[i] * 3 if code.startswith(code[i] * 3, i) else code[i]
            i = quoted_end(code, i, inner)
            continue
        elif not depth and code.startswith(quote, i):
            # SQL doubled quotes are part of the same string.
            if len(quote) == 1 and code.startswith(quote * 2, i):
                i += 2
                continue
            return i + len(quote)
        if formatted and code.startswith("{{", i) and not depth:
            i += 2
        else:
            i += 1
    raise ValueError(
        "Unterminated string literal; refusing to return partially masked code."
    )


def literal_parts(value: str, subkind: str) -> tuple[str, str]:
    if subkind == "header":
        return "<", ">"
    if subkind == "diagnostic":
        return "", ""
    if subkind == "comment":
        prefix = next((p for p in ("//", "/*", "--", "#") if value.startswith(p)), "#")
        return prefix, "*/" if prefix == "/*" else ""
    if value.startswith("`"):
        return "`", "`"
    cpp = re.match(r'(?:u8|u|U|L)?R"([^ ()\\\t\r\n]{0,16})\(', value)
    if cpp:
        return cpp[0], ")" + cpp[1] + '"'
    rust = re.match(r'(?:br|r)(#+)"', value)
    if rust:
        return rust[0], '"' + rust[1]
    match = re.match(r'(?:u8|u|U|L|[rRuUbBfF]{0,2})("""|\'\'\'|"|\')', value)
    if not match:
        raise ValueError("Unsupported string literal")
    return match[0], match[1]


class CodeParser:
    @staticmethod
    def tokenize(code: str, language: str = "auto") -> list[tuple[str, TokenType, str]]:
        language = detect_language(code, language)
        tokens: list[tuple[str, TokenType, str]] = []
        i = 0
        directive = None
        expect_header = expect_pragma = False
        at_line_start = True
        while i < len(code):
            start = i
            c = code[i]
            kind = "syntax"
            token_type = TokenType.GRAMMAR
            directive_match = (
                re.match(r"#[ \t]*([A-Za-z_]\w*)", code[i:])
                if language in {"c", "cpp"} and c == "#" and at_line_start else None
            )
            comment = next(
                (
                    p
                    for p in ("/*", "//", "--", "#")
                    if code.startswith(p, i)
                    and (p != "--" or language == "sql")
                    and (p not in {"//", "/*"} or language != "python")
                    and (p != "#" or language == "python")
                ),
                None,
            )
            cpp = re.match(r'(?:u8|u|U|L)?R"([^ ()\\\t\r\n]{0,16})\(', code[i:])
            rust = re.match(r'(?:br|r)(#+)"', code[i:])
            string = STRING_START.match(code, i)
            prefixed = re.match(r'(?:u8|u|U|L)("|\')', code[i:])
            if c.isspace():
                i += 1
                while i < len(code) and code[i].isspace():
                    i += 1
                kind = "whitespace"
            elif directive_match and directive_match[1] in PREPROCESSOR_DIRECTIVES:
                i += len(directive_match[0])
                directive = directive_match[1]
                expect_header = directive in {"include", "include_next"}
                expect_pragma = directive == "pragma"
                kind = "keyword"
            elif directive in {"error", "warning"}:
                end = code.find("\n", i)
                i = end if end >= 0 else len(code)
                token_type, kind = TokenType.LITERAL, "diagnostic"
            elif expect_header and c == "<":
                end = code.find(">", i + 1)
                newline = code.find("\n", i + 1)
                if end < 0 or (newline >= 0 and newline < end):
                    raise ValueError("Unterminated include header")
                i = end + 1
                token_type, kind = TokenType.LITERAL, "header"
            elif comment:
                if comment == "/*":
                    end = code.find("*/", i + 2)
                    if end < 0:
                        raise ValueError("Unterminated block comment")
                    i = end + 2
                else:
                    end = code.find("\n", i)
                    i = end if end >= 0 else len(code)
                token_type, kind = TokenType.LITERAL, "comment"
            elif cpp or rust:
                match = cpp or rust
                assert match is not None
                suffix = ")" + match[1] + '"' if cpp else '"' + match[1]
                end = code.find(suffix, i + len(match[0]))
                if end < 0:
                    raise ValueError("Unterminated raw string literal")
                i = end + len(suffix)
                token_type, kind = TokenType.LITERAL, "string"
            elif c == "`":
                # A whole template is opaque: interpolation also contains private symbols.
                i = quoted_end(
                    code, i, "`", formatted=language in {"javascript", "typescript"}
                )
                token_type, kind = TokenType.LITERAL, "string"
            elif (
                language == "rust"
                and re.match(r"'[A-Za-z_]\w*(?!')", code[i:])
                and not re.match(r"'[^'\n]+'", code[i:])
            ):
                # Lifetime punctuation is structural; its name is processed next.
                i += 1
            elif string or prefixed:
                match = string or prefixed
                assert match is not None
                quote = match[1]
                qstart = i + len(match[0]) - len(quote)
                i = quoted_end(code, qstart, quote, "f" in match[0].lower())
                token_type, kind = TokenType.LITERAL, "string"
            elif c.isdigit() or (
                c == "." and i + 1 < len(code) and code[i + 1].isdigit()
            ):
                number = NUMBER.match(code, i)
                assert number is not None
                i = number.end()
                token_type, kind = TokenType.NUMBER, "number"
            else:
                identifier = IDENTIFIER.match(code, i)
                if identifier:
                    i = identifier.end()
                    word = identifier[0]
                    reserved = (
                        word.lower() in LANGUAGE_KEYWORDS[language]
                        if language == "sql"
                        else word in LANGUAGE_KEYWORDS[language]
                    )
                    if directive in {"if", "elif"} and word == "defined":
                        reserved = True
                    if expect_pragma and word == "once":
                        reserved = True
                    if language == "python" and word in {"match", "case"}:
                        line = code[code.rfind("\n", 0, start) + 1 :]
                        reserved = bool(re.match(r"\s*(?:match|case)\s+[^=\n]+:", line))
                    token_type, kind = (
                        (TokenType.GRAMMAR, "keyword")
                        if reserved
                        else (TokenType.IDENTIFIER, "identifier")
                    )
                else:
                    i += 1
            raw = code[start:i]
            # Only whitespace/comments can precede a directive on a logical
            # line. Preserve continuation context across escaped newlines.
            for newline_match in re.finditer("\n", raw):
                position = start + newline_match.start()
                previous = position - 1
                if previous >= 0 and code[previous] == "\r":
                    previous -= 1
                if previous < 0 or code[previous] != "\\":
                    directive = None
                    expect_header = expect_pragma = False
                    at_line_start = True
            if kind not in {"whitespace", "comment"}:
                at_line_start = False
                if not directive_match:
                    expect_header = expect_pragma = False
            tokens.append((raw, token_type, kind))
        return tokens
