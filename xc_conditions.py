"""xc_conditions.py — Condition rewriting: and/or/not, chained comparisons, intervals."""

import re
import sys

from xc_utils import find_matching_paren

CMP_OPS = {'<', '>', '<=', '>=', '==', '!='}


def _extract_condition(source: str, keyword_pos: int) -> tuple:
    """Given the position of 'if'/'while'/'for', find the ( ... ) that follows."""
    i = keyword_pos
    while i < len(source) and (source[i].isalnum() or source[i] == '_'): i += 1
    while i < len(source) and source[i] in ' \t\n': i += 1
    if i >= len(source) or source[i] != '(': return None, None
    open_p  = i
    close_p = find_matching_paren(source, open_p)
    return open_p, close_p


def _split_on_and_or(expr: str) -> list:
    """Split expr on top-level 'and'/'or'/'xor'/'nor' keywords."""
    parts = []
    depth = 0; i = 0; current_start = 0; last_op = None
    n = len(expr)
    while i < n:
        c = expr[i]
        if c in ('(', '['): depth += 1; i += 1
        elif c in (')', ']'): depth -= 1; i += 1
        elif depth == 0:
            for kw in ('and', 'or', 'xor', 'nor'):
                klen = len(kw)
                if expr[i:i+klen] == kw:
                    before = expr[i-1] if i > 0 else ' '
                    after  = expr[i+klen] if i+klen < n else ' '
                    if not (before.isalnum() or before == '_') and \
                       not (after.isalnum()  or after  == '_'):
                        parts.append((last_op, expr[current_start:i].strip()))
                        last_op = kw; i += klen; current_start = i; break
            else:
                i += 1
        else:
            i += 1
    parts.append((last_op, expr[current_start:].strip()))
    return parts


def _tokenise_expr(expr: str):
    """Tokenise a simple comparison expression into (kind, value) pairs."""
    if '(' in expr or ')' in expr: return None
    if '&&' in expr or '||' in expr: return None
    tokens = []
    i = 0; n = len(expr)
    while i < n:
        if i + 1 < n and expr[i:i+2] == '->':
            tokens.append(('other', '->')); i += 2
        elif i + 1 < n and expr[i:i+2] in ('<=', '>=', '==', '!='):
            tokens.append(('op', expr[i:i+2])); i += 2
        elif expr[i] in '<>':
            tokens.append(('op', expr[i])); i += 1
        elif expr[i] in ' \t':
            j = i
            while j < n and expr[j] in ' \t': j += 1
            tokens.append(('ws', expr[i:j])); i = j
        elif expr[i].isalpha() or expr[i] == '_':
            j = i
            while j < n and (expr[j].isalnum() or expr[j] == '_'): j += 1
            tokens.append(('word', expr[i:j])); i = j
        elif expr[i].isdigit() or (expr[i] == '-' and i+1 < n and expr[i+1].isdigit()):
            j = i
            if expr[j] == '-': j += 1
            while j < n and (expr[j].isdigit() or expr[j] in '.eE+-fFuUlL'): j += 1
            tokens.append(('word', expr[i:j])); i = j
        else:
            tokens.append(('other', expr[i])); i += 1
    return tokens


def _rewrite_chained(expr: str) -> str:
    """Rewrite chained comparisons like  a < b < c  to  a < b && b < c ."""
    tokens = _tokenise_expr(expr)
    if tokens is None: return expr
    ops_positions = [i for i, t in enumerate(tokens) if t[0] == 'op' and t[1] in CMP_OPS]
    if len(ops_positions) < 2: return expr

    pairs = []
    for idx, op_pos in enumerate(ops_positions):
        left  = ''.join(t[1] for t in tokens[:op_pos if idx == 0 else ops_positions[idx-1]+1:op_pos]).strip()
        op    = tokens[op_pos][1]
        right = ''.join(t[1] for t in tokens[op_pos+1:(None if idx == len(ops_positions)-1 else ops_positions[idx+1])]).strip()
        pairs.append((left, op, right))

    def _flip_op(op):
        return {'<':'>','>':'<','<=':'>=','>=':'<=','==':'==','!=':'!='}.get(op, op)

    def _is_literal(s):
        return bool(re.match(r'^-?\d+(\.\d+)?([eE][+-]?\d+)?[fFuUlL]*$', s.strip()))

    expanded = [
        f'{right} {_flip_op(op)} {left}' if _is_literal(left) and not _is_literal(right)
        else f'{left} {op} {right}'
        for left, op, right in pairs
    ]
    return ' && '.join(expanded)


# ---------------------------------------------------------------------------
# Interval expressions
# ---------------------------------------------------------------------------

def _parse_endpoint(text: str, pos: int):
    """Parse one interval endpoint starting at text[pos]."""
    n = len(text)
    while pos < n and text[pos] in ' \t': pos += 1
    if pos >= n: return None, -1
    start = pos

    if text[pos] == '(':
        depth = 1; j = pos + 1
        while j < n and depth > 0:
            if text[j] == '(': depth += 1
            elif text[j] == ')': depth -= 1
            j += 1
        k = j
        while k < n and text[k] in ' \t': k += 1
        if k < n and (text[k].isalnum() or text[k] in '_-+'):
            pos = j
        else:
            return None, -1

    if pos < n and text[pos] in '+-': pos += 1
    if text[pos:pos+3] == 'INF': return text[start:pos+3].strip(), pos + 3

    if pos < n and (text[pos].isalnum() or text[pos] == '_'):
        while pos < n and (text[pos].isalnum() or text[pos] == '_'): pos += 1
        if pos < n and text[pos] == '.':
            pos += 1
            while pos < n and text[pos].isdigit(): pos += 1
        while pos < n and text[pos] in 'fFuUlL': pos += 1
        while pos + 1 < n and text[pos:pos+2] == '->':
            pos += 2
            while pos < n and text[pos] in ' \t': pos += 1
            if pos < n and (text[pos].isalnum() or text[pos] == '_'):
                while pos < n and (text[pos].isalnum() or text[pos] == '_'): pos += 1
            else:
                break
        while pos < n and text[pos] == '[':
            depth = 1; j = pos + 1
            while j < n and depth > 0:
                if text[j] == '[': depth += 1
                elif text[j] == ']': depth -= 1
                j += 1
            if depth == 0: pos = j
            else: break
        return text[start:pos].strip(), pos

    return None, -1


def _parse_interval_outer(text: str, start: int):
    """Parse  [ (lo, hi) U (lo2, hi2) U ... ]  at text[start]."""
    pos = start + 1; n = len(text)
    while pos < n and text[pos] in ' \t': pos += 1
    if pos >= n: return None, -1

    if text[pos] == 'R' and (pos + 1 >= n or not (text[pos+1].isalnum() or text[pos+1] == '_')):
        pos += 1
        while pos < n and text[pos] in ' \t': pos += 1
        if pos < n and text[pos] == ']': return [('R', None, None, None)], pos
        return None, -1

    intervals = []
    while True:
        while pos < n and text[pos] in ' \t': pos += 1
        if pos >= n: return None, -1
        if text[pos] not in '([': return None, -1

        open_b = text[pos]; pos += 1
        lo, pos = _parse_endpoint(text, pos)
        if lo is None: return None, -1
        while pos < n and text[pos] in ' \t': pos += 1
        if pos >= n or text[pos] != ',': return None, -1
        pos += 1
        hi, pos = _parse_endpoint(text, pos)
        if hi is None: return None, -1
        while pos < n and text[pos] in ' \t': pos += 1
        if pos >= n: return None, -1
        if text[pos] == ')':   close_b = ')'; pos += 1
        elif text[pos] == ']': close_b = ']'; pos += 1
        else: return None, -1

        intervals.append((open_b, lo, hi, close_b))
        while pos < n and text[pos] in ' \t': pos += 1
        if pos >= n: return None, -1
        if text[pos] == 'U':
            pos += 1; continue
        elif text[pos] == ']':
            return intervals, pos
        elif text[pos] in '([':
            raise SyntaxError(
                f"XC interval error: adjacent sub-intervals must be joined with 'U'. "
                f"Context: ...{text[max(0,pos-20):pos+10]!r}..."
            )
        else:
            return None, -1


def _validate_and_fix_interval(intervals: list, src_context: str) -> list:
    """Validate interval list and swap bounds if reversed."""
    INF_VALS = {'-INF', '+INF', 'INF'}
    fixed = []
    for open_b, lo, hi, close_b in intervals:
        if open_b == 'R':
            fixed.append((open_b, lo, hi, close_b)); continue
        lo_s = (lo or '').strip().lstrip('+')
        hi_s = (hi or '').strip().lstrip('+')

        def _is_num(s):
            # Strip C numeric suffixes before parsing
            cleaned = s.lstrip('-').rstrip('fFuUlL')
            try:
                float(cleaned)
                return True
            except ValueError:
                return False

        def _to_float(s):
            cleaned = s.rstrip('fFuUlL')
            val = float(cleaned.lstrip('-') if cleaned.startswith('-') else cleaned)
            return -val if cleaned.startswith('-') else val

        if lo_s not in INF_VALS and hi_s not in INF_VALS and _is_num(lo_s) and _is_num(hi_s):
            lo_val = _to_float(lo_s)
            hi_val = _to_float(hi_s)
            if lo_val > hi_val:
                print(
                    f"XC interval warning: bounds are swapped in interval "
                    f"{open_b}{lo}, {hi}{close_b} — implicitly fixing. "
                    f"Context: {src_context!r}",
                    file=sys.stderr
                )
                flipped_open  = '[' if close_b == ']' else '['
                flipped_close = ')' if open_b  == '(' else ')'
                fixed.append((flipped_open, hi, lo, flipped_close)); continue
        fixed.append((open_b, lo, hi, close_b))
    return fixed


def _intervals_to_c(var: str, intervals: list) -> str:
    INF_VALS = {'-INF', '+INF', 'INF'}
    parts = []
    for item in intervals:
        if item[0] == 'R': return '1'
        open_b, lo, hi, close_b = item
        lo_s = (lo or '').strip(); hi_s = (hi or '').strip()
        lo_op = '>=' if open_b == '[' else '>'
        hi_op = '<=' if close_b == ']' else '<'
        conds = []
        if lo_s not in INF_VALS: conds.append(f'{var} {lo_op} {lo_s}')
        if hi_s not in INF_VALS: conds.append(f'{var} {hi_op} {hi_s}')
        if not conds:       parts.append('1')
        elif len(conds) == 1: parts.append(conds[0])
        else:                parts.append(f'({conds[0]} && {conds[1]})')
    if not parts: return '1'
    return parts[0] if len(parts) == 1 else ' || '.join(parts)


def _preexpand_intervals(source: str) -> str:
    """Pre-pass: expand all  VARNAME == [INTERVAL_SPEC]  forms."""
    result = []; i = 0; n = len(source)
    pat = re.compile(
        r'(?:\(\s*\*\s*([A-Za-z_]\w*)\s*\)'
        r'|\b([A-Za-z_]\w*(?:\s*->\s*[A-Za-z_]\w*)*(?:\s*\[\s*[A-Za-z_0-9]+\s*\])?)'
        r')\s*==\s*(\[)'
    )
    while i < n:
        m = pat.search(source, i)
        if not m: result.append(source[i:]); break

        bracket_pos = m.start(3)
        rest_after = source[bracket_pos+1:].lstrip(' \t')
        if rest_after.startswith(']'):
            raise SyntaxError(
                f"XC interval error: empty interval 'x == []' is not allowed. "
                f"Use 'x == [R]' for all reals. "
                f"Context: {source[max(0,bracket_pos-20):bracket_pos+5]!r}"
            )

        try:
            intervals, close_pos = _parse_interval_outer(source, bracket_pos)
        except SyntaxError: raise

        if intervals is None:
            result.append(source[i:m.end()]); i = m.end(); continue

        var = f'(*{m.group(1)})' if m.group(1) is not None else m.group(2)
        ctx = source[max(0, m.start()-10):close_pos+2]
        intervals = _validate_and_fix_interval(
            [item for item in intervals if item[0] != 'R']
            if any(item[0] == 'R' for item in intervals) else intervals,
            ctx
        )
        result.append(source[i:m.start()])
        result.append(_intervals_to_c(var, intervals))
        i = close_pos + 1
    return ''.join(result)


def _rewrite_condition(cond: str) -> str:
    """Apply all condition rewrites to the expression inside the parens."""
    parts = _split_on_and_or(cond)
    result_parts = []
    for op, sub in parts:
        sub_stripped = sub.strip()
        if sub_stripped.startswith('not ') or sub_stripped.startswith('not('):
            rest = sub_stripped[3:].strip()
            # If rest is a parenthesised group, strip the outer parens and rewrite inside
            if rest.startswith('(') and rest.endswith(')'):
                inner = rest[1:-1]
                inner_rewritten = _rewrite_condition(inner)
                sub_rewritten = f'!({inner_rewritten})'
            else:
                rest = _preexpand_intervals(rest)
                rest = _rewrite_chained(rest)
                sub_rewritten = f'!({rest})'
        else:
            sub_rewritten = _preexpand_intervals(sub_stripped)
            sub_rewritten = _rewrite_chained(sub_rewritten)

        if op is None:   result_parts.append(sub_rewritten)
        elif op == 'and': result_parts.append(f' && {sub_rewritten}')
        elif op == 'or':  result_parts.append(f' || {sub_rewritten}')
        elif op == 'xor':
            left = ''.join(result_parts).strip(); result_parts.clear()
            result_parts.append(f'!({left}) != !({sub_rewritten})')
        elif op == 'nor':
            prev = ''.join(result_parts).strip(); result_parts.clear()
            result_parts.append(f'!(({prev}) || ({sub_rewritten}))')
    return ''.join(result_parts)


def rewrite_conditions(source: str) -> str:
    """Find all  if/while/for ( CONDITION )  in source and rewrite the condition."""
    source = _preexpand_intervals(source)
    keyword_pat  = re.compile(r'\b(if|while|for)\b')
    replacements = []

    for m in keyword_pat.finditer(source):
        open_p, close_p = _extract_condition(source, m.start())
        if open_p is None: continue
        inner = source[open_p + 1: close_p]

        if m.group(1) == 'for':
            parts = []; depth = 0; current = ''
            for ch in inner:
                if ch in '([': depth += 1
                elif ch in ')]': depth -= 1
                elif ch == ';' and depth == 0: parts.append(current); current = ''; continue
                current += ch
            parts.append(current)
            if len(parts) == 3:
                rewritten_cond = _rewrite_condition(parts[1])
                if rewritten_cond != parts[1]:
                    replacements.append((open_p, close_p,
                                         parts[0] + '; ' + rewritten_cond + '; ' + parts[2]))
        else:
            rewritten = _rewrite_condition(inner)
            if rewritten != inner: replacements.append((open_p, close_p, rewritten))

    out = source
    for open_p, close_p, rewritten in reversed(replacements):
        out = out[:open_p + 1] + rewritten + out[close_p:]
    return out
