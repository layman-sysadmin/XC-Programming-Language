"""xc_references.py — Transform C++ style references (TYPE& name) to C pointers."""

import re

from xc_utils import (
    find_matching_brace, find_matching_paren,
    _tokenise, _tokens_to_str, parse_params,
)


def _find_ref_declarations(tokens: list, known_struct_names: set = None) -> set:
    """Scan token stream for  TYPE& name  reference declarations."""
    C_TYPES = {
        'int','char','float','double','void','short','long',
        'unsigned','signed','struct','union','enum',
        'int8_t','uint8_t','int16_t','uint16_t',
        'int32_t','uint32_t','int64_t','uint64_t',
        'int8','uint8','int16','uint16','int32','uint32','int64','uint64',
        'size_t','ptrdiff_t','ssize_t','bool',
    }

    def _is_type(word):
        return (word in C_TYPES or word.endswith('_t')
                or (known_struct_names and word in known_struct_names))

    refs = set()
    i = 0; n = len(tokens)
    while i < n:
        kind, val = tokens[i]
        if kind == 'word' and _is_type(val):
            j = i + 1
            while j < n and tokens[j][0] == 'ws': j += 1
            if val in ('struct', 'union', 'enum'):
                if j < n and tokens[j][0] == 'word':
                    j += 1
                    while j < n and tokens[j][0] == 'ws': j += 1
            while j < n and tokens[j] == ('op', '*'):
                j += 1
                while j < n and tokens[j][0] == 'ws': j += 1
            if j < n and tokens[j] == ('op', '&'):
                k = j + 1
                while k < n and tokens[k][0] == 'ws': k += 1
                if k < n and tokens[k][0] == 'word':
                    var_name = tokens[k][1]
                    if var_name not in ('NULL','sizeof','return','if','while','for','else','switch'):
                        refs.add(var_name)
        i += 1
    return refs


def _rewrite_refs_in_body(tokens: list, ref_names: set,
                           known_struct_names: set = None) -> list:
    """Rewrite all usages of reference variables in a token stream to pointer form."""
    if not ref_names: return tokens

    out = []
    i = 0; n = len(tokens)

    while i < n:
        kind, val = tokens[i]

        if kind == 'word':
            j = i + 1
            while j < n and tokens[j][0] == 'ws': j += 1
            if val in ('struct', 'union', 'enum') and j < n and tokens[j][0] == 'word':
                tag_j    = j
                tag_name = tokens[tag_j][1]
                j = tag_j + 1
                while j < n and tokens[j][0] == 'ws': j += 1
                while j < n and tokens[j] == ('op', '*'):
                    j += 1
                    while j < n and tokens[j][0] == 'ws': j += 1
                if j < n and tokens[j] == ('op', '&'):
                    k = j + 1
                    while k < n and tokens[k][0] == 'ws': k += 1
                    if k < n and tokens[k][0] == 'word' and tokens[k][1] in ref_names:
                        for t in tokens[i:tag_j+1]: out.append(t)
                        for t in tokens[tag_j+1:j]: out.append(t)
                        out.append(('op', '*'))
                        for t in tokens[j+1:k]: out.append(t)
                        out.append(tokens[k])
                        i = k + 1
                        ii = i
                        while ii < n and tokens[ii][0] == 'ws': ii += 1
                        if ii < n and tokens[ii] == ('op', '='):
                            ii2 = ii + 1
                            while ii2 < n and tokens[ii2][0] == 'ws': ii2 += 1
                            for t in tokens[i:ii+1]: out.append(t)
                            i = ii + 1
                            rhs_start_str = ''.join(t[1] for t in tokens[ii2:ii2+4])
                            if not rhs_start_str.startswith(f'({val} {tag_name}'):
                                out.append(('other', f' ({val} {tag_name}*)'))
                        continue
                j = i + 1
                while j < n and tokens[j][0] == 'ws': j += 1

            _KW = {'return','if','else','while','for','do','switch','case',
                   'break','continue','goto','sizeof','not','and','or'}
            if val not in _KW and j < n and tokens[j] == ('op', '&'):
                k = j + 1
                while k < n and tokens[k][0] == 'ws': k += 1
                if k < n and tokens[k][0] == 'word' and tokens[k][1] in ref_names:
                    out.append((kind, val))
                    for t in tokens[i+1:j]: out.append(t)
                    out.append(('op', '*'))
                    for t in tokens[j+1:k]: out.append(t)
                    out.append(tokens[k])
                    i = k + 1
                    if known_struct_names and val in known_struct_names:
                        ii = i
                        while ii < n and tokens[ii][0] == 'ws': ii += 1
                        if ii < n and tokens[ii] == ('op', '='):
                            ii2 = ii + 1
                            while ii2 < n and tokens[ii2][0] == 'ws': ii2 += 1
                            rhs_str = ''.join(t[1] for t in tokens[ii2:ii2+5])
                            if not rhs_str.startswith(f'(struct {val}'):
                                for t in tokens[i:ii+1]: out.append(t)
                                i = ii + 1
                                out.append(('other', f' (struct {val}*)'))
                    continue

        if kind == 'op' and val == '&':
            j = i + 1
            while j < n and tokens[j][0] == 'ws': j += 1
            if j < n and tokens[j][0] == 'word' and tokens[j][1] in ref_names:
                _pnw = next((t for t in reversed(out) if t[0] != 'ws'), None)
                if _pnw and _pnw[0] == 'word' and _pnw[1] == 'return':
                    for t in tokens[i+1:j]: out.append(t)
                    out.append(tokens[j]); i = j + 1; continue

        if kind == 'op' and val == '*':
            j = i + 1
            while j < n and tokens[j][0] == 'ws': j += 1
            if j < n and tokens[j][0] == 'word' and tokens[j][1] in ref_names:
                _pnw2 = next((t for t in reversed(out) if t[0] != 'ws'), None)
                if _pnw2 and _pnw2[0] == 'word' and _pnw2[1] == 'return':
                    for t in tokens[i+1:j]: out.append(t)
                    out.append(tokens[j]); i = j + 1; continue
                k = j + 1
                while k < n and tokens[k][0] == 'ws': k += 1
                if (k < n and tokens[k] == ('op', '=')
                        and (k+1 >= n or tokens[k+1] != ('op', '='))):
                    rhs = k + 1
                    while rhs < n and tokens[rhs][0] == 'ws': rhs += 1
                    if rhs < n and tokens[rhs] == ('op', '&'):
                        for t in tokens[i+1:j]: out.append(t)
                        out.append(tokens[j]); i = j + 1; continue

        if kind == 'word' and val in ref_names:
            prev = None; prev2 = None
            for t in reversed(out):
                if t[0] != 'ws':
                    if prev is None:  prev = t
                    elif prev2 is None: prev2 = t; break
            # Look ahead: is the next non-ws token '&'? (e.g. "return &ref" — keep as-is)
            next_nws = None
            ni = i + 1
            while ni < n and tokens[ni][0] == 'ws': ni += 1
            if ni < n: next_nws = tokens[ni]

            suppress = (
                (prev and prev[1] in ('.', '&', '*', ',')) or
                (prev and prev[1] == '>' and prev2 and prev2[1] == '-') or
                # "return &ref" — the & will be handled by the op=='&' branch; don't touch name
                (prev and prev[0] == 'word' and prev[1] == 'return'
                 and next_nws and next_nws[1] == '&') or
                (prev and prev[1] == '(' and prev2 and prev2[0] == 'word'
                 and prev2[1] not in {'if','while','for','switch','return'})
            )
            if suppress:
                out.append((kind, val))
            else:
                out.append(('op', '(')); out.append(('op', '*'))
                out.append((kind, val)); out.append(('op', ')'))
            i += 1; continue

        out.append((kind, val)); i += 1

    return out


def replace_references(source: str, known_struct_names: set = None) -> str:
    """Main entry point: transform all reference (TYPE& name) usages to pointer form."""
    func_pat = re.compile(r'([\w\s\*&]+?)\s+(\w+)\s*\(([^)]*)\)\s*\{', re.MULTILINE)
    rewrites = []

    for m in func_pat.finditer(source):
        fname = m.group(2).strip()
        if fname in ('if', 'for', 'while', 'switch', 'do', 'else'): continue
        brace_open = m.end() - 1
        try:
            brace_close = find_matching_brace(source, brace_open)
        except ValueError:
            continue

        params_str  = m.group(3)
        body        = source[brace_open + 1: brace_close]

        param_tokens = _tokenise(params_str)
        ref_names    = _find_ref_declarations(param_tokens, known_struct_names)
        body_tokens  = _tokenise(body)
        ref_names   |= _find_ref_declarations(body_tokens, known_struct_names)
        if not ref_names: continue

        new_params = _tokens_to_str(_rewrite_refs_in_body(param_tokens, ref_names, known_struct_names))
        new_body   = _tokens_to_str(_rewrite_refs_in_body(body_tokens, ref_names, known_struct_names))
        rpi = [i for i, p in enumerate(parse_params(params_str)) if p.name in ref_names]
        rewrites.append((m.start(), m.end(), brace_close, m.group(0),
                         new_params, new_body, m.group(1).strip(), fname, rpi))

    rcm = {}
    for e in rewrites:
        for ix in e[8]:
            if ix not in rcm.get(e[7], []): rcm.setdefault(e[7], []).append(ix)

    out = source
    for (ss, se, bc, osig, np2, nb, ret, fn, _) in reversed(rewrites):
        orig_sig    = osig
        new_sig     = orig_sig.replace(
            orig_sig[orig_sig.index('(') + 1: orig_sig.rindex(')')], np2
        )
        replacement = new_sig + nb + '}'
        out = out[:ss] + replacement + out[bc + 1:]

    # Global-scope reference declarations
    global_tokens    = _tokenise(out)
    ref_names_global = _find_ref_declarations(global_tokens, known_struct_names)
    if ref_names_global:
        result_tokens = []
        i = 0; n = len(global_tokens)
        while i < n:
            kind, val = global_tokens[i]
            if kind == 'word':
                j = i + 1
                while j < n and global_tokens[j][0] == 'ws': j += 1
                if j < n and global_tokens[j] == ('op', '&'):
                    k = j + 1
                    while k < n and global_tokens[k][0] == 'ws': k += 1
                    if (k < n and global_tokens[k][0] == 'word'
                            and global_tokens[k][1] in ref_names_global):
                        result_tokens.append((kind, val))
                        for t in global_tokens[i+1:j]: result_tokens.append(t)
                        result_tokens.append(('op', '*'))
                        for t in global_tokens[j+1:k]: result_tokens.append(t)
                        result_tokens.append(global_tokens[k])
                        i = k + 1; continue
            result_tokens.append((kind, val)); i += 1
        out = _tokens_to_str(result_tokens)

    if rcm:
        _fbp = re.compile(r'\b\w+\s*\([^)]*\)\s*\{', re.MULTILINE)
        _pdp = re.compile(r'\b\w[\w\s]*\*\s*(\w+)\b')
        _fr  = []
        for _fm in _fbp.finditer(out):
            try:
                _fe = find_matching_brace(out, out.index('{', _fm.end()-1))
                _fr.append((_fm.start(), _fe, {x.group(1) for x in _pdp.finditer(_fm.group(0))}))
            except (ValueError, IndexError): pass

        def _ppa(pos): return next((pn for fs, fe, pn in _fr if fs <= pos <= fe), set())

        _csp = re.compile(r'\b(\w+)\s*\(')
        _res = []; _i = 0; _n = len(out)
        while _i < _n:
            _cm = _csp.search(out, _i)
            if not _cm: _res.append(out[_i:]); break
            _fn = _cm.group(1)
            if _fn not in rcm: _res.append(out[_i:_cm.end()]); _i = _cm.end(); continue
            _ls  = out.rfind('\n', 0, _cm.start()) + 1
            _pre = out[_ls:_cm.start()].strip()
            if re.match(r'^[\w\s\*]+$', _pre) and '{' not in out[_ls:_cm.start()]:
                _res.append(out[_i:_cm.end()]); _i = _cm.end(); continue
            _po = _cm.end() - 1
            try: _pc = find_matching_paren(out, _po)
            except ValueError: _res.append(out[_i:_cm.end()]); _i = _cm.end(); continue
            _raw = out[_po+1:_pc]; _args = []; _d = 0; _cur = []
            for _ch in _raw:
                if _ch in '([': _d += 1
                elif _ch in ')]': _d -= 1
                if _ch == ',' and _d == 0: _args.append(''.join(_cur)); _cur = []
                else: _cur.append(_ch)
            if _cur: _args.append(''.join(_cur))
            _lp  = _ppa(_cm.start()); _mod = False
            for _ix in rcm[_fn]:
                if _ix >= len(_args): continue
                _arg = _args[_ix].strip()
                if _arg.startswith('&') or _arg.startswith('('): continue
                _dm = re.match(r'^\*(\w+)$', _arg)
                if _dm: _args[_ix] = ' ' + _dm.group(1); _mod = True
                elif re.match(r'^\w+$', _arg) and _arg not in _lp:
                    _args[_ix] = ' &' + _arg; _mod = True
            if _mod: _res.extend([out[_i:_po+1], ', '.join(_args), ')']); _i = _pc + 1
            else: _res.append(out[_i:_pc+1]); _i = _pc + 1
        out = ''.join(_res)

    return out
