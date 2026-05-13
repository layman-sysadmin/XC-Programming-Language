"""xc_misc.py — Miscellaneous source transforms: cstrings, new/delete,
overloading, default params, regex directives, header guards."""

import re
from collections import defaultdict

from xc_types import CSTRING_MAP
from xc_utils import find_matching_brace, find_matching_paren, parse_params


def lift_includes(source: str) -> tuple:
    """Extract all #include lines, return (source_without_includes, include_lines)."""
    includes = []
    kept     = []
    for line in source.split('\n'):
        if re.match(r'^\s*#\s*include\s*[<"]', line):
            includes.append(line)
        else:
            kept.append(line)
    return '\n'.join(kept), includes


def replace_cstring_declarations(source: str) -> str:
    """Replace char16/char32/char64 type names with char[N+1] array syntax."""
    for ctype, (base, size) in CSTRING_MAP.items():
        pattern = re.compile(rf'\b{ctype}\s+(\w+)(\s*=\s*[^;]+)?;')
        def replacer(m, base=base, size=size):
            return f'{base} {m.group(1)}[{size}]{m.group(2) or ""};'
        source = pattern.sub(replacer, source)
        source = re.compile(rf'\b{ctype}\s+(\w+)').sub(rf'{base} \1[{size}]', source)
    return source


def replace_new_delete(source: str) -> tuple:
    """Replace XC 'new' and 'delete' with C malloc/free equivalents."""
    stdlib_needed = False

    new_array_pat = re.compile(r'\bnew\s+([\w\s\*]+?)\s*\[\s*([^\]]+)\s*\]')
    def replace_new_array(m):
        nonlocal stdlib_needed; stdlib_needed = True
        return f'({m.group(1).strip()}*)malloc(sizeof({m.group(1).strip()}) * ({m.group(2).strip()}))'
    source = new_array_pat.sub(replace_new_array, source)

    new_pat = re.compile(r'\bnew\s+([\w\s\*]+?)(?=\s*[;,)\]])')
    def replace_new_single(m):
        nonlocal stdlib_needed; stdlib_needed = True
        return f'({m.group(1).strip()}*)malloc(sizeof({m.group(1).strip()}))'
    source = new_pat.sub(replace_new_single, source)

    delete_pat = re.compile(
        r'(^[ \t]*|(?<=;)\s*|(?<=\{)\s*)\bdelete\s+([\w\->\.\[\]]+)\s*;',
        re.MULTILINE
    )
    def replace_delete(m):
        nonlocal stdlib_needed; stdlib_needed = True
        return f'{m.group(1)}free({m.group(2).strip()});'
    source = delete_pat.sub(replace_delete, source)

    return source, stdlib_needed


def replace_header_guards(source: str) -> str:
    """Rewrite #start NAME / #end NAME to #ifndef NAME / #define NAME / #endif."""
    start_pat  = re.compile(r'^\s*#start\s+(\w+)\s*$', re.MULTILINE)
    end_pat    = re.compile(r'^\s*#end\s+(\w+)\s*$',   re.MULTILINE)
    directives = []
    for m in start_pat.finditer(source):
        directives.append(('start', m.group(1), m.start(), m.end()))
    for m in end_pat.finditer(source):
        directives.append(('end', m.group(1), m.start(), m.end()))
    directives.sort(key=lambda d: d[2])

    stack = []
    for kind, name, _, _ in directives:
        if kind == 'start': stack.append(name)
        else:
            if not stack: raise SyntaxError(f"#end {name} has no matching #start")
            top = stack.pop()
            if top != name: raise SyntaxError(f"#end {name} does not match #start {top}")
    if stack: raise SyntaxError(f"Unclosed #start for: {', '.join(stack)}")

    for kind, name, start, end in reversed(directives):
        replacement = (f'#ifndef {name}\n#define {name}'
                       if kind == 'start' else f'#endif /* {name} */')
        source = source[:start] + replacement + source[end:]
    return source


def _parse_regex_arg(s: str):
    """Parse one quoted or unquoted token from the start of string s."""
    s = s.strip()
    if not s: return '', ''
    if s[0] in ('"', "'"):
        q = s[0]; i = 1; buf = []
        while i < len(s):
            if s[i] == '\\' and i + 1 < len(s): buf.append(s[i:i+2]); i += 2
            elif s[i] == q:                       i += 1; break
            else:                                 buf.append(s[i]); i += 1
        return ''.join(buf), s[i:].strip()
    else:
        parts = s.split(None, 1)
        return parts[0], (parts[1] if len(parts) > 1 else '')


def apply_regex_directives(source: str) -> str:
    """Process all  #regex PATTERN REPLACEMENT  /  #endex  directive pairs."""
    regex_open_pat = re.compile(r'^[ \t]*#regex\b(.*)', re.MULTILINE)
    endex_pat      = re.compile(r'^[ \t]*#endex\b.*',   re.MULTILINE)

    events = []
    for m in regex_open_pat.finditer(source):
        events.append(('regex', m.start(), m.end(), m.group(1).strip()))
    for m in endex_pat.finditer(source):
        events.append(('endex', m.start(), m.end(), ''))
    events.sort(key=lambda e: e[1])

    depth = 0
    for kind, *_ in events:
        if kind == 'regex': depth += 1
        else:
            if depth == 0: raise SyntaxError("#endex with no matching #regex")
            depth -= 1

    regions = []
    stack   = []
    for kind, ev_start, ev_end, args in events:
        if kind == 'regex':
            pattern, rest = _parse_regex_arg(args)
            replacement, _ = _parse_regex_arg(rest)
            if not pattern: raise SyntaxError(f"#regex missing pattern: {args!r}")
            stack.append((ev_end, pattern, replacement))
        else:
            content_start, pattern, replacement = stack.pop()
            regions.append((content_start, ev_start, pattern, replacement))
    for content_start, pattern, replacement in stack:
        regions.append((content_start, len(source), pattern, replacement))

    blanked = list(source)
    for kind, ev_start, ev_end, _ in events:
        for i in range(ev_start, ev_end):
            blanked[i] = '\n' if source[i] == '\n' else ' '
    source = ''.join(blanked)

    offset = 0
    for region_start, region_end, pattern, replacement in sorted(regions):
        rs  = region_start + offset
        re_ = region_end   + offset
        segment = source[rs:re_]
        try:
            new_segment = re.sub(pattern, replacement, segment)
        except re.error as e:
            raise SyntaxError(f"#regex pattern error ({pattern!r}): {e}")
        source = source[:rs] + new_segment + source[re_:]
        offset += len(new_segment) - len(segment)

    return source


def expand_default_params(source: str) -> str:
    """Expand free functions with default parameter values into overload stubs."""
    func_pat   = re.compile(r'^([ \t]*)([\w\s\*]+?)\s+(\w+)\s*\(([^)]*)\)\s*\{', re.MULTILINE)
    struct_pat = re.compile(r'\bstruct\s+\w+\s*\{', re.MULTILINE)

    def _struct_ranges(src):
        ranges = set()
        for sm in struct_pat.finditer(src):
            try:
                be = find_matching_brace(src, sm.end() - 1)
                ranges.update(range(sm.start(), be + 1))
            except ValueError: pass
        return ranges

    struct_ranges = _struct_ranges(source)
    insertions    = []
    strip_defaults = []

    for m in func_pat.finditer(source):
        if m.start() in struct_ranges: continue
        indent = m.group(1); ret = m.group(2).strip()
        fname  = m.group(3).strip(); params_s = m.group(4)
        if fname in ('if', 'for', 'while', 'switch', 'do', 'else'): continue

        params   = parse_params(params_s)
        defaults = [(i, p) for i, p in enumerate(params) if p.default]
        if not defaults: continue

        first_default = defaults[0][0]
        for i in range(first_default, len(params)):
            if not params[i].default:
                raise SyntaxError(
                    f"Default parameter for '{fname}': non-default param "
                    f"'{params[i].name}' follows default param '{params[first_default].name}'. "
                    f"Only trailing parameters may have defaults."
                )

        stripped = ', '.join(f'{p.type_str} {p.name}'.strip() for p in params)
        strip_defaults.append((m, stripped, m.group(4)))

        full_tags     = [re.sub(r'[^a-zA-Z0-9_]', '_', p.type_str.strip()) for p in params]
        mangled_fname = fname + '__' + '_'.join(full_tags) if full_tags else fname + '__void'
        # Only use the mangled name if it already appears in source (e.g. it was
        # pre-mangled by a prior pass). Otherwise keep the plain name so the forward
        # declaration matches the actual definition that will be emitted later.
        if mangled_fname not in source:
            mangled_fname = fname

        overload_texts = []
        # Forward-declare the full function before the stubs so they can call it
        # regardless of source ordering.
        fwd_decl = f'{indent}{ret} {mangled_fname}({stripped});'
        overload_texts.append(fwd_decl)
        for cutoff in range(first_default, len(params)):
            short_params    = params[:cutoff]
            fill_args       = [p.name for p in short_params] + [p.default for p in params[cutoff:]]
            short_param_str = ', '.join(f'{p.type_str} {p.name}'.strip() for p in short_params)
            args_str        = ', '.join(fill_args)
            body_call       = (f'{mangled_fname}({args_str});' if ret == 'void'
                               else f'return {mangled_fname}({args_str});')
            overload_texts.append(f'{indent}{ret} {fname}({short_param_str}) {{ {body_call} }}')

        insertions.append((m.start(), '\n'.join(overload_texts) + '\n'))

    if not insertions and not strip_defaults: return source

    out = source
    for pos, text in sorted(insertions, reverse=True):
        out = out[:pos] + text + out[pos:]

    while True:
        changed       = False
        struct_ranges2 = _struct_ranges(out)
        for m2 in func_pat.finditer(out):
            if m2.start() in struct_ranges2: continue
            fname2 = m2.group(3).strip()
            if fname2 in ('if', 'for', 'while', 'switch', 'do', 'else'): continue
            params2   = parse_params(m2.group(4))
            if not any(p.default for p in params2): continue
            stripped2 = ', '.join(f'{p.type_str} {p.name}'.strip() for p in params2)
            if stripped2 == m2.group(4): continue
            old2 = m2.group(0)
            out  = out[:m2.start()] + old2.replace(m2.group(4), stripped2, 1) + out[m2.end():]
            changed = True; break
        if not changed: break

    return out


def mangle_overloaded_functions(source: str) -> str:
    """Detect overloaded free functions and mangle their names by parameter types."""
    func_def_pattern = re.compile(r'^([\w\s\*]+?)\s+(\w+)\s*\(([^)]*)\)\s*\{', re.MULTILINE)
    # Also match forward declarations (no body, ends with ;)
    func_fwd_pattern = re.compile(r'^([\w\s\*]+?)\s+(\w+)\s*\(([^)]*)\)\s*;', re.MULTILINE)
    name_groups = defaultdict(list)

    for m in func_def_pattern.finditer(source):
        fname = m.group(2).strip()
        if fname in ('if', 'for', 'while', 'switch', 'do'): continue
        name_groups[fname].append((m, m.group(1).strip(), m.group(3).strip(), 'def'))

    if not any(len(v) > 1 for v in name_groups.values()):
        return source  # nothing to mangle

    mangle_map      = {}
    def_replacements = {}

    for fname, occurrences in name_groups.items():
        if len(occurrences) <= 1: continue
        for m, ret, params_str, kind in occurrences:
            params  = parse_params(params_str)
            tags    = [re.sub(r'[^a-zA-Z0-9_]', '_', p.type_str.strip()) for p in params]
            mangled = fname + '__' + '_'.join(tags) if tags else fname + '__void'
            def_replacements[m.start()] = (m, mangled)
            mangle_map.setdefault(fname, []).append((params, mangled))

    if not def_replacements: return source

    out = source; offset = 0
    for pos in sorted(def_replacements):
        m, mangled   = def_replacements[pos]
        old          = m.group(0)
        new          = old.replace(m.group(2), mangled, 1)
        actual_pos   = pos + offset
        out          = out[:actual_pos] + new + out[actual_pos + len(old):]
        offset      += len(new) - len(old)

    # Also rename any forward declarations matching the same signatures
    for fname, variants in mangle_map.items():
        sig_to_mangled = {}
        for params, mangled in variants:
            key = tuple(p.signature_type() for p in params)
            sig_to_mangled[key] = mangled
        # Find forward decls for this fname
        fwd_pat = re.compile(r'\b' + re.escape(fname) + r'\s*\(([^)]*)\)\s*;')
        new_out = []; j = 0
        for fm in fwd_pat.finditer(out):
            params = parse_params(fm.group(1))
            key    = tuple(p.signature_type() for p in params)
            if key in sig_to_mangled:
                new_out.append(out[j:fm.start()])
                new_out.append(out[fm.start():fm.end()].replace(fname, sig_to_mangled[key], 1))
                j = fm.end()
        new_out.append(out[j:])
        out = ''.join(new_out)

    for fname, variants in mangle_map.items():
        # Map arg_count -> list of (params, mangled) — may be multiple for same arity
        count_to_variants: dict = {}
        for params, mangled in variants:
            count_to_variants.setdefault(len(params), []).append((params, mangled))

        def _pick_variant(arg_count, args_str, variants_for_count):
            """Pick the best mangled name for a call site.
            For unambiguous count, return directly.
            For same-count ambiguity, score each variant by how well param types
            match the argument literals/expressions."""
            if len(variants_for_count) == 1:
                return variants_for_count[0][1]

            # Split args
            raw_args = []
            depth = 0; cur = []
            for ch in (args_str or ''):
                if ch in ('(','['): depth += 1
                elif ch in (')',']'): depth -= 1
                if ch == ',' and depth == 0:
                    raw_args.append(''.join(cur).strip()); cur = []
                else:
                    cur.append(ch)
            if cur: raw_args.append(''.join(cur).strip())

            def _arg_is_float(arg):
                a = arg.strip()
                return bool(re.search(r'\d\.\d|\d[fF]$|^\d+\.\d*$', a))

            def _score(params, args):
                score = 0
                for p, a in zip(params, args):
                    pt = p.type_str.strip()
                    af = _arg_is_float(a)
                    if ('float' in pt or 'double' in pt) and af:
                        score += 2
                    elif ('float' not in pt and 'double' not in pt) and not af:
                        score += 2
                    elif af and ('float' in pt or 'double' in pt):
                        score += 1
                return score

            best_score = -1; best_mangled = variants_for_count[0][1]
            for params, mangled in variants_for_count:
                s = _score(params, raw_args)
                if s > best_score:
                    best_score = s; best_mangled = mangled
            return best_mangled

        i = 0
        while i < len(out):
            m = re.search(r'\b' + re.escape(fname) + r'\s*\(', out[i:])
            if not m: break
            abs_start  = i + m.start()
            paren_open = i + m.end() - 1
            line_start  = out.rfind('\n', 0, abs_start) + 1
            line_prefix = out[line_start:abs_start].strip()
            if bool(re.match(r'^[\w\s\*]+$', line_prefix)) and '{' not in out[line_start:abs_start]:
                i = abs_start + len(fname) + 1; continue
            try:
                paren_close = find_matching_paren(out, paren_open)
            except ValueError:
                i = abs_start + 1; continue
            args_str  = out[paren_open + 1: paren_close].strip()
            arg_count = 0
            if args_str:
                depth = 0; arg_count = 1
                for ch in args_str:
                    if ch in ('(', '['):   depth += 1
                    elif ch in (')', ']'): depth -= 1
                    elif ch == ',' and depth == 0: arg_count += 1
            if arg_count in count_to_variants:
                mangled = _pick_variant(arg_count, args_str, count_to_variants[arg_count])
                out = out[:abs_start] + mangled + out[abs_start + len(fname):]
                i   = abs_start + len(mangled) + 1
            else:
                i = abs_start + 1

    return out


def rewrite_cstring_ops(source: str) -> str:
    """Rewrite XC string-manipulation shorthand to C string library calls."""
    char_names: set = set()
    plain_decl = re.compile(r'\bchar\s+(\w+)\s*\[')
    for m in plain_decl.finditer(source):
        char_names.add(m.group(1))
    if not char_names: return source

    def _is_char_target(name: str) -> bool:
        bare = name.strip()
        if bare in char_names: return True
        m  = re.match(r'^\w+\s*->\s*(\w+)$', bare)
        if m and m.group(1) in char_names: return True
        m2 = re.match(r'^\w+\s*\.\s*(\w+)$', bare)
        return bool(m2 and m2.group(1) in char_names)

    def _str_lit_len(s: str) -> int:
        inner = s[1:-1]; count = 0; i = 0
        while i < len(inner):
            i += 2 if (inner[i] == '\\' and i + 1 < len(inner)) else 1
            count += 1
        return count

    STR_LIT  = r'"(?:[^"\\]|\\.)*"'
    INT_IDX  = r'(?:[0-9]+|\w+)'
    CHAR_TGT = r'(?:\w+\s*(?:->|\.)\s*)?\w+'

    assign_plain  = re.compile(rf'^(\s*)({CHAR_TGT})\s*=(?!=)\s*(.+?)\s*;(\s*)$')
    assign_offset = re.compile(rf'^(\s*)({CHAR_TGT})\s*\[({INT_IDX})\]\s*=(?!=)\s*(.+?)\s*;(\s*)$')
    concat_pat    = re.compile(rf'^(\s*)({CHAR_TGT})\s*\+=\s*(.+?)\s*;(\s*)$')
    shrink_pat    = re.compile(rf'^(\s*)({CHAR_TGT})\s*-=\s*(\w+)\s*;(\s*)$')

    def _split_plus_chain(rhs: str) -> list:
        pieces = []; current = ''; in_str = False; escape_next = False
        for ch in rhs:
            if escape_next: current += ch; escape_next = False
            elif ch == '\\' and in_str: current += ch; escape_next = True
            elif ch == '"': in_str = not in_str; current += ch
            elif ch == '+' and not in_str:
                piece = current.strip()
                if piece: pieces.append(piece)
                current = ''
            else: current += ch
        piece = current.strip()
        if piece: pieces.append(piece)
        return pieces if pieces else None

    def _is_valid_string_piece(piece: str) -> bool:
        if re.match(r'^' + STR_LIT + r'$', piece): return True
        if _is_char_target(piece): return True
        m2 = re.match(rf'^(\w+)\s*\[({INT_IDX})\]$', piece)
        if m2 and m2.group(1) in char_names: return True
        return bool(re.match(r'^\w+$', piece))

    def _emit_piece_as_copy(dest: str, piece: str, first: bool, indent: str) -> str:
        piece = piece.strip()
        lit_m = re.match(r'^' + STR_LIT + r'$', piece)
        off_m = re.match(rf'^(\w+)\s*\[({INT_IDX})\]$', piece)
        if first:
            if lit_m:
                slen = _str_lit_len(piece)
                return (f"{indent}{dest}[0]='\\0';" if slen == 0
                        else f'{indent}strncpy({dest},{piece},{slen+1});')
            elif off_m and off_m.group(1) in char_names:
                return f'{indent}strcpy({dest}, {off_m.group(1)}+(sizeof(char)*{off_m.group(2)}));'
            else:
                return f'{indent}strcpy({dest}, {piece});'
        else:
            if lit_m:
                return f'{indent}strcat({dest}, {piece});'
            elif off_m and off_m.group(1) in char_names:
                return f'{indent}strcat({dest}, {off_m.group(1)}+(sizeof(char)*{off_m.group(2)}));'
            else:
                return f'{indent}strcat({dest}, {piece});'

    def _ss(ln):
        if ln.count(';') <= 1: return [ln]
        ind = re.match(r'^(\s*)', ln).group(1)
        st = []; cu = []; ins = False; esc = False
        for ch in ln:
            if esc: cu.append(ch); esc = False; continue
            if ch == '\\' and ins: cu.append(ch); esc = True; continue
            if ch == '"': ins = not ins
            if ch == ';' and not ins: st.append(''.join(cu).strip()); cu = []
            else: cu.append(ch)
        if ''.join(cu).strip(): st.append(''.join(cu).strip())
        return [ind + s + ';' for s in st if s]

    lines = []
    for rl in source.split('\n'): lines.extend(_ss(rl))
    out = []

    for line in lines:
        rewritten = False

        m = assign_offset.match(line)
        if m and _is_char_target(m.group(2)):
            indent, tgt, idx, rhs, trail = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            dest   = f'{tgt}+(sizeof(char)*{idx})' if idx != '0' else tgt
            pieces = _split_plus_chain(rhs.strip())
            if pieces and all(_is_valid_string_piece(p) for p in pieces):
                stmts = [_emit_piece_as_copy(dest, pieces[0], True, indent)]
                for p in pieces[1:]: stmts.append(_emit_piece_as_copy(dest, p, False, indent))
                out.append('\n'.join(stmts) + trail); rewritten = True

        if not rewritten:
            m = assign_plain.match(line)
            if m and _is_char_target(m.group(2)):
                indent, tgt, rhs, trail = m.group(1), m.group(2), m.group(3), m.group(4)
                pieces = _split_plus_chain(rhs.strip())
                if pieces and all(_is_valid_string_piece(p) for p in pieces):
                    stmts = [_emit_piece_as_copy(tgt, pieces[0], True, indent)]
                    for p in pieces[1:]: stmts.append(_emit_piece_as_copy(tgt, p, False, indent))
                    out.append('\n'.join(stmts) + trail); rewritten = True

        if not rewritten:
            m = concat_pat.match(line)
            if m and _is_char_target(m.group(2)):
                indent, tgt, rhs, trail = m.group(1), m.group(2), m.group(3), m.group(4)
                pieces = _split_plus_chain(rhs.strip())
                if pieces and all(_is_valid_string_piece(p) for p in pieces):
                    stmts = [_emit_piece_as_copy(tgt, p, False, indent) for p in pieces]
                    out.append('\n'.join(stmts) + trail); rewritten = True

        if not rewritten:
            m = shrink_pat.match(line)
            if m and _is_char_target(m.group(2)):
                indent, tgt, n_expr, trail = m.group(1), m.group(2), m.group(3), m.group(4)
                out.append(
                    f'{indent}{{ int __xc_slen = strlen({tgt}); '
                    f'{tgt}[(__xc_slen > ({n_expr})) ? (__xc_slen - ({n_expr})) : 0] = \'\\0\'; }}{trail}'
                )
                rewritten = True

        if not rewritten: out.append(line)

    return '\n'.join(out)
