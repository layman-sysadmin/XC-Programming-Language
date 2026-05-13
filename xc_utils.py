"""xc_utils.py — Low-level utilities shared across all other modules."""

import re
from xc_types import Param


# ---------------------------------------------------------------------------
# Brace / paren matching
# ---------------------------------------------------------------------------

def _find_matching(text, start, op, cl):
    d = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        # Skip string literals so  "{"  or  '{'  don't confuse matching
        if c == '"':
            i += 1
            while i < n:
                if text[i] == '\\': i += 2; continue
                if text[i] == '"':  i += 1; break
                i += 1
            continue
        if c == "'":
            i += 1
            while i < n:
                if text[i] == '\\': i += 2; continue
                if text[i] == "'":  i += 1; break
                i += 1
            continue
        if c == op:   d += 1
        elif c == cl:
            d -= 1
            if d == 0: return i
        i += 1
    raise ValueError(f'Unmatched {op!r} at {start}')


def find_matching_brace(text, start):
    return _find_matching(text, start, '{', '}')


def find_matching_paren(text, start):
    return _find_matching(text, start, '(', ')')


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------

def parse_params(param_str: str) -> list:
    param_str = param_str.strip()
    if not param_str or param_str == 'void': return []
    params = []
    depth = 0
    current = []
    for ch in param_str:
        if ch in ('(', '[', '<'):   depth += 1
        elif ch in (')', ']', '>'): depth -= 1
        if ch == ',' and depth == 0:
            params.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current: params.append(''.join(current).strip())

    result = []
    for p in params:
        p = p.strip()
        if not p: continue

        default = ''
        eq_pos = -1
        d = 0
        for i, ch in enumerate(p):
            if ch in ('(', '[', '<'): d += 1
            elif ch in (')', ']', '>'): d -= 1
            elif ch == '=' and d == 0:
                eq_pos = i
                break
        if eq_pos != -1:
            default = p[eq_pos + 1:].strip()
            p = p[:eq_pos].strip()

        tokens = p.split()
        if len(tokens) == 1:
            result.append(Param(type_str=tokens[0], name='', default=default))
        else:
            name = tokens[-1].lstrip('*')
            stars = tokens[-1][:len(tokens[-1]) - len(name)]
            type_part = ' '.join(tokens[:-1]) + stars
            result.append(Param(type_str=type_part.strip(), name=name, default=default))
    return result


# ---------------------------------------------------------------------------
# Tokeniser (used by references, implicit-member rewriting, etc.)
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> list:
    """Split text into (kind, value) tokens.
    kind is one of: 'str', 'char', 'word', 'ws', 'op'
    """
    tokens = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == '"':
            qt = c; kd = 'str'; j = i + 1
            while j < len(text):
                if text[j] == '\\': j += 2; continue
                if text[j] == qt:   j += 1; break
                j += 1
            tokens.append((kd, text[i:j])); i = j
        elif c == "'":
            j = i + 1
            while j < len(text):
                if text[j] == '\\': j += 2; continue
                if text[j] == "'":  j += 1; break
                j += 1
            tokens.append(('char', text[i:j])); i = j
        elif c.isalpha() or c == '_':
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] == '_'):
                j += 1
            tokens.append(('word', text[i:j])); i = j
        elif c in ' \t\n\r':
            j = i
            while j < len(text) and text[j] in ' \t\n\r':
                j += 1
            tokens.append(('ws', text[i:j])); i = j
        else:
            tokens.append(('op', c)); i += 1
    return tokens


def _tokens_to_str(tokens: list) -> str:
    return ''.join(v for _, v in tokens)


# ---------------------------------------------------------------------------
# Misc source transforms
# ---------------------------------------------------------------------------

def fix_pointer_declarations(source: str) -> str:
    """Rewrite multi-name pointer declarations so every name gets its own star.

    Handles both styles:
        int* p, q;      ->  int* p;\n int* q;
        int *p, *q;     ->  int *p;\n int *q;
        int* p, *q;     ->  int* p;\n int* q;   (mixed)
    Single-name declarations are left untouched.
    """
    # Style 1: star attached to type:  int* p, q, *r;
    type_star_pat = re.compile(
        r'(^[ \t]*|(?<=[;{])\s*)'
        r'([a-zA-Z_][\w\s]*?)'
        r'(\*+)'
        r'(?=\s+\w)'
        r'\s+'
        r'([^;{}\n]+?)'
        r'\s*;',
        re.MULTILINE
    )

    # Style 2: star attached to name:  int *p, *q;  or  int *p, q;
    name_star_pat = re.compile(
        r'(^[ \t]*|(?<=[;{])\s*)'
        r'([a-zA-Z_][\w\s]*?)\s+'   # base type (no star)
        r'(\*\w[\w\s,\*]*?)'         # first declarator starts with *
        r'\s*;',
        re.MULTILINE
    )

    def _split_declarators(decl_list: str):
        """Split 'p, *q, r' into ['p', '*q', 'r']."""
        parts = []
        depth = 0
        current = []
        for ch in decl_list:
            if ch in ('(', '['): depth += 1
            elif ch in (')', ']'): depth -= 1
            if ch == ',' and depth == 0:
                parts.append(''.join(current).strip())
                current = []
            else:
                current.append(ch)
        if current:
            parts.append(''.join(current).strip())
        return parts

    def expand_type_star(m: re.Match) -> str:
        indent    = m.group(1)
        base_type = m.group(2).strip()
        stars     = m.group(3)
        decl_list = m.group(4).strip()
        declarators = _split_declarators(decl_list)
        if len(declarators) <= 1:
            return m.group(0)
        # XC spec: ALL names on a 'Type* a, b, c' line become pointers.
        # This is an intentional improvement over C (where only the first gets the star).
        lines = []
        for d in declarators:
            d = d.strip()
            name = d.lstrip('*').strip()   # strip any redundant explicit stars
            lines.append(f'{indent}{base_type}{stars} {name};')
        return '\n'.join(lines)

    def expand_name_star(m: re.Match) -> str:
        indent    = m.group(1)
        base_type = m.group(2).strip()
        decl_list = m.group(3).strip()
        declarators = _split_declarators(decl_list)
        if len(declarators) <= 1:
            return m.group(0)
        lines = []
        for d in declarators:
            d = d.strip()
            if d.startswith('*'):
                # keep the star on the type side
                lines.append(f'{indent}{base_type}* {d.lstrip("*")};')
            else:
                lines.append(f'{indent}{base_type} {d};')
        return '\n'.join(lines)

    # Apply style-1 first (star on type)
    source = type_star_pat.sub(expand_type_star, source)
    # Apply style-2 (star on name) — only when there's a comma (multi-decl)
    source = name_star_pat.sub(expand_name_star, source)
    return source


def replace_binary_literals(source: str) -> str:
    """Replace 0b... binary literals with 0x... hex equivalents."""
    bin_pat = re.compile(r'\b0[bB]([01]+)([uUlLuU]*)\b')

    def convert(m: re.Match) -> str:
        digits  = m.group(1)
        suffix  = m.group(2)
        value   = int(digits, 2)
        bit_len = len(digits)
        if   bit_len <=  4: nibbles = 1
        elif bit_len <=  8: nibbles = 2
        elif bit_len <= 16: nibbles = 4
        elif bit_len <= 32: nibbles = 8
        else:               nibbles = (bit_len + 3) // 4
        return f'0x{format(value, f"0{nibbles}X")}{suffix}'

    return bin_pat.sub(convert, source)
