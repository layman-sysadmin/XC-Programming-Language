"""xc_methods.py — Method body rewriting and method-call-site rewriting."""

import re

from xc_types import Param, FunctionDef
from xc_utils import (
    find_matching_brace, find_matching_paren,
    _tokenise, _tokens_to_str,
)


# ---------------------------------------------------------------------------
# Internal body transforms (used by generate_method_impl)
# ---------------------------------------------------------------------------

def _rewrite_implicit_members(body: str, member_names: set, param_names: set) -> str:
    """Rewrite bare member name references inside a method body to self->member."""
    if not member_names: return body

    tokens = _tokenise(body)
    n = len(tokens)

    shadow_stack = [set(param_names)]

    C_KEYWORDS = {
        'return', 'if', 'else', 'while', 'for', 'do', 'switch', 'case',
        'break', 'continue', 'goto', 'sizeof', 'typedef', 'extern',
        'static', 'auto', 'register', 'volatile', 'const', 'inline',
        'struct', 'union', 'enum', 'not', 'and', 'or',
    }

    local_decl_indices = {}
    i = 0
    while i < n:
        kind, val = tokens[i]
        if kind == 'word' and val in member_names:
            prev_idx = i - 1
            while prev_idx >= 0 and tokens[prev_idx][0] == 'ws': prev_idx -= 1
            next_idx = i + 1
            while next_idx < n and tokens[next_idx][0] == 'ws': next_idx += 1
            prev_tok = tokens[prev_idx] if prev_idx >= 0 else None
            next_tok = tokens[next_idx] if next_idx < n else None
            if (prev_tok and prev_tok[0] == 'word'
                    and prev_tok[1] not in C_KEYWORDS
                    and next_tok and next_tok[1] in (';', '=', ',')):
                local_decl_indices[i] = val
        i += 1

    out = []
    i = 0
    brace_depth = 0

    while i < n:
        kind, val = tokens[i]

        if kind == 'op' and val == '{':
            brace_depth += 1
            shadow_stack.append(set())
            out.append((kind, val)); i += 1; continue

        if kind == 'op' and val == '}':
            brace_depth -= 1
            if len(shadow_stack) > 1: shadow_stack.pop()
            out.append((kind, val)); i += 1; continue

        if i in local_decl_indices:
            shadow_stack[-1].add(local_decl_indices[i])
            out.append((kind, val)); i += 1; continue

        if kind == 'word' and val in member_names:
            shadowed = any(val in scope for scope in shadow_stack)
            if shadowed:
                out.append((kind, val)); i += 1; continue

            prev = None
            for t in reversed(out):
                if t[0] != 'ws': prev = t; break
            if prev and prev[1] in ('.', '->', '>'):
                out.append((kind, val)); i += 1; continue

            out.append(('word', f'self->{val}')); i += 1; continue

        out.append((kind, val)); i += 1

    return _tokens_to_str(out)


def _rewrite_self_method_calls(body: str, mth: FunctionDef, struct_def) -> str:
    """Inside a method body, rewrite bare calls to other methods of the same struct."""
    method_map = {m.name: m for m in struct_def.all_methods if not m.abstract}
    call_pat   = re.compile(r'\b(\w+)\s*\(')

    result = []
    i = 0
    n = len(body)

    while i < n:
        m = call_pat.search(body, i)
        if not m: result.append(body[i:]); break

        fname = m.group(1)
        if fname not in method_map:
            result.append(body[i:m.end()]); i = m.end(); continue

        prefix = body[i:m.start()].rstrip()
        if prefix and prefix[-1] in ('.', '>'):
            result.append(body[i:m.end()]); i = m.end(); continue

        if fname in ('if', 'for', 'while', 'switch', 'return'):
            result.append(body[i:m.end()]); i = m.end(); continue

        target_mth = method_map[fname]
        paren_open = m.end() - 1
        try:
            paren_close = find_matching_paren(body, paren_open)
        except ValueError:
            result.append(body[i:m.end()]); i = m.end(); continue

        args_str = body[paren_open + 1: paren_close].strip()

        user_arg_count = 0
        if args_str:
            depth = 0; user_arg_count = 1
            for ch in args_str:
                if ch in ('(', '['):   depth += 1
                elif ch in (')', ']'): depth -= 1
                elif ch == ',' and depth == 0: user_arg_count += 1

        total_params = len(target_mth.params)
        if user_arg_count < total_params:
            short_params = target_mth.params[:user_arg_count]
            if short_params:
                call_name = (target_mth.struct_name + '__' + target_mth.name + '__' +
                             '__'.join(re.sub(r'[^a-zA-Z0-9_]', '_', p.type_str.strip())
                                       for p in short_params))
            else:
                call_name = target_mth.struct_name + '__' + target_mth.name + '__void'
        else:
            call_name = target_mth.mangled_name()

        _sa = (f'(struct {target_mth.struct_name}*)self'
               if target_mth.struct_name != struct_def.name else 'self')
        new_call = f'{call_name}({_sa}, {args_str})' if args_str else f'{call_name}({_sa})'

        result.append(body[i:m.start()])
        result.append(new_call)
        i = paren_close + 1

    return ''.join(result)


def _extract_static_vars(body: str, struct_name: str, method_name: str) -> tuple:
    """Find all  static TYPE varname [= init];  declarations in body,
    hoist them to file scope with mangled names, and return (cleaned_body, decls)."""
    static_pat = re.compile(
        r'^([ \t]*)static\s+'
        r'([\w\s\*]+?)\s+'
        r'(\w+)'
        r'\s*(?:=\s*([^;]+?))?\s*;',
        re.MULTILINE
    )

    file_scope_decls = []
    renames = {}

    for m in static_pat.finditer(body):
        varname  = m.group(3)
        type_str = m.group(2).strip()
        init_val = m.group(4)
        mangled  = f'__xc_static_{struct_name}_{method_name}_{varname}'
        renames[varname] = mangled
        if init_val:
            file_scope_decls.append(f'static {type_str} {mangled} = {init_val.strip()};')
        else:
            file_scope_decls.append(f'static {type_str} {mangled};')

    if not renames: return body, []

    body = static_pat.sub(lambda m: m.group(1), body)
    for old, new in renames.items():
        body = re.sub(rf'\b{re.escape(old)}\b', new, body)

    return body, file_scope_decls


def _rewrite_static_member_access_body(body: str, struct_def, params: list) -> str:
    """Rewrite static field references inside a method body to their mangled names."""
    if not struct_def.all_statics: return body

    shadow = {p.name for p in params} | {'self'}
    local_decl_pat = re.compile(
        r'\b(?:int|char|float|double|void|short|long|unsigned|signed|\w+_t)\s+\*?\s*(\w+)\s*(?:=|;|\[|,)'
    )
    for m in local_decl_pat.finditer(body):
        shadow.add(m.group(1))

    for type_str, varname, init_val, owner in struct_def.all_statics:
        if varname in shadow: continue
        mangled = f'{owner}__{varname}'
        body = re.sub(r'\bself\s*->\s*' + re.escape(varname) + r'\b', mangled, body)
        body = re.sub(r'\(\s*\*\s*self\s*\)\s*\.\s*' + re.escape(varname) + r'\b', mangled, body)
        body = re.sub(r'(?<![>\.\w])' + re.escape(varname) + r'\b(?!\s*\()', mangled, body)

    return body


def _rewrite_static_member_access(body: str, struct_def) -> str:
    """Legacy wrapper — kept for compatibility."""
    return _rewrite_static_member_access_body(body, struct_def, [])


# ---------------------------------------------------------------------------
# generate_method_impl
# ---------------------------------------------------------------------------

def generate_method_impl(mth: FunctionDef, struct_def=None, all_structs=None) -> str:
    """Emit the top-level C function for a struct method."""
    # Deferred import to avoid circular dependency
    from xc_conditions import rewrite_conditions

    body = mth.body

    if all_structs:
        field_scope = {}
        if struct_def is not None:
            struct_names = {sd.name for sd in all_structs}
            for ftype, fname in struct_def.all_fields:
                clean_name = re.sub(r'\[.*\]', '', fname)
                m_type = re.match(r'(?:struct\s+)?(\w+)\s*\*?', ftype.strip())
                if m_type and m_type.group(1) in struct_names:
                    field_scope[clean_name] = (m_type.group(1), bool(re.search(r'\*', ftype)))
        body = rewrite_method_calls(body, all_structs, extra_scope=field_scope)

    if struct_def is not None:
        member_names = {re.sub(r'\[.*\]', '', name) for _, name in struct_def.all_fields}
        param_names  = {p.name for p in mth.params} | {'self'}
        body = _rewrite_implicit_members(body, member_names, param_names)

    if struct_def is not None and struct_def.all_statics:
        body = _rewrite_static_member_access_body(body, struct_def, mth.params)

    if struct_def is not None:
        body = _rewrite_self_method_calls(body, mth, struct_def)

    body = re.sub(r'\bthis\.(\w+)', r'self->\1', body)
    body = re.sub(r'\bthis->(\w+)', r'self->\1', body)

    body = rewrite_conditions(body)

    body, static_decls = _extract_static_vars(body, mth.struct_name, mth.name)

    all_params   = [Param(f'struct {mth.struct_name} *', 'self')] + mth.params
    stripped_str = ', '.join(f'{p.type_str} {p.name}' for p in all_params)

    full_def = (
        f'{mth.return_type} {mth.mangled_name()}({stripped_str}) {{\n'
        f'{body}\n'
        f'}}'
    )

    if static_decls:
        full_def = '\n'.join(static_decls) + '\n' + full_def

    defaulted = [(i, p) for i, p in enumerate(mth.params) if p.default]
    if not defaulted: return full_def

    full_fwd      = f'{mth.return_type} {mth.mangled_name()}({stripped_str});'
    first_default = defaulted[0][0]
    stubs = []
    for cutoff in range(first_default, len(mth.params)):
        short_user_params = mth.params[:cutoff]
        fill_args = (
            ['self'] +
            [p.name for p in short_user_params] +
            [p.default for p in mth.params[cutoff:]]
        )
        short_all_params = [Param(f'struct {mth.struct_name} *', 'self')] + short_user_params
        short_param_str  = ', '.join(f'{p.type_str} {p.name}' for p in short_all_params)
        args_str = ', '.join(fill_args)
        stub_body = (f'{mth.mangled_name()}({args_str});'
                     if mth.return_type == 'void'
                     else f'return {mth.mangled_name()}({args_str});')

        stub_name = (
            mth.struct_name + '__' + mth.name + '__' + '__'.join(
                re.sub(r'[^a-zA-Z0-9_]', '_', p.type_str.strip())
                for p in short_user_params
            ) if short_user_params
            else mth.struct_name + '__' + mth.name + '__void'
        )
        stubs.append(f'{mth.return_type} {stub_name}({short_param_str}) {{ {stub_body} }}')

    return '\n\n'.join([full_fwd] + stubs + [full_def])


# ---------------------------------------------------------------------------
# rewrite_method_calls — call-site rewriting
# ---------------------------------------------------------------------------

def rewrite_method_calls(source: str, structs: list, extra_scope: dict = None) -> str:
    """Rewrite struct method call syntax to direct C function calls."""
    struct_map  = {sd.name: sd for sd in structs}
    method_sets = {}
    for sd in structs:
        method_sets[sd.name] = {}
        for mth in sd.all_methods:
            if mth.abstract: continue
            required_count = sum(1 for p in mth.params if not p.default)
            total_count    = len(mth.params)
            method_sets[sd.name].setdefault(mth.name, []).append(
                (required_count, total_count, mth)
            )

    struct_name_set  = set(struct_map.keys())
    decl_pat         = re.compile(r'\bstruct\s+(\w+)\s*(\*?)\s*(\w+)\s*(?:=\s*[^;]+)?;')
    typedef_decl_pat = re.compile(r'\b(\w+)\s*(\*?)\s*(\w+)\s*(?:\[[^\]]*\])?\s*(?:=\s*[^;]+)?;')

    # Multi-variable declaration patterns (comma-separated names after the type)
    # e.g.  struct Foo a, b, *c;   or   Foo a, b;
    multi_struct_pat = re.compile(
        r'\bstruct\s+(\w+)\s+([\w\s,\*]+);'
    )
    multi_typedef_pat = re.compile(
        r'\b(\w+)\s+((?:\*?\w+\s*,\s*)*\*?\w+)\s*;'
    )

    call_pat = re.compile(
        r'\b(\w+)\s*(\.|->>?)\s*(\w+)\s*\('
        r'|'
        r'\b(\w+)\s*\[[^\]]*\]\s*(\.)\s*(\w+)\s*\('
        r'|'
        r'\b(\w+)\s*\[[^\]]*\]\s*(->)\s*(\w+)\s*\('
        r'|'
        r'\(\s*\*\s*(\w+)\s*\)\s*(\.)\s*(\w+)\s*\('
        r'|'
        r'\b(\w+)\s*\.\s*(\w+)\s*(\.)\s*(\w+)\s*\('
    )

    def rewrite_block(text: str, outer_scope: dict) -> str:
        scope = dict(outer_scope)
        sbp = set()
        for sm in re.finditer(r'\bstruct\s+\w+\s*\{', text):
            try:
                be = find_matching_brace(text, sm.end()-1)
                sbp.update(range(sm.start(), be+1))
            except ValueError: pass

        _fdef = re.compile(r'\b\w+\s*\([^)]*\)\s*\{', re.MULTILINE)
        _fsig = re.compile(r'\b(\w+)\s*[*&]\s*(\w+)\s*(?=[,)])')
        ppr = {}
        for _fm in _fdef.finditer(text):
            try: _fe = find_matching_brace(text, text.index('{', _fm.end()-1))
            except (ValueError, IndexError): continue
            for _pm in _fsig.finditer(_fm.group(0)):
                if _pm.group(1) in struct_map:
                    ppr.setdefault(_pm.group(2), []).append((_fm.start(), _fe))

        _CKW = {'int','char','float','double','void','short','long','unsigned','signed',
                'struct','union','enum','return','if','else','while','for','do','switch',
                'case','break','continue','goto','sizeof','typedef','extern','static',
                'const','volatile','register','auto','inline'}

        def _add_multi_decl(sn, names_str):
            """Parse 'a, *b, c' and add each name to scope."""
            for part in names_str.split(','):
                part = part.strip()
                if not part: continue
                is_ptr = part.startswith('*')
                name   = part.lstrip('*').strip()
                if re.match(r'^\w+$', name) and name not in scope:
                    scope[name] = (sn, is_ptr)

        # Single-var struct decl:  struct Foo *p;
        for m in decl_pat.finditer(text):
            if m.start() in sbp: continue
            sn, ip, vn = m.group(1), bool(m.group(2).strip()), m.group(3)
            if sn in struct_map: scope[vn] = (sn, ip)

        # Multi-var struct decl:  struct Foo a, *b, c;
        for m in re.finditer(r'\bstruct\s+(\w+)\s+((?:\*?\w+\s*,\s*)+\*?\w+)\s*;', text):
            if m.start() in sbp: continue
            sn = m.group(1)
            if sn in struct_map: _add_multi_decl(sn, m.group(2))

        # Single-var typedef decl:  Foo *p;
        for m in typedef_decl_pat.finditer(text):
            if m.start() in sbp: continue
            sn, ip, vn = m.group(1), bool(m.group(2).strip()), m.group(3)
            if sn in struct_name_set and vn not in scope and sn not in _CKW:
                scope[vn] = (sn, ip)

        # Multi-var typedef decl:  Foo a, *b;
        for m in re.finditer(r'\b(\w+)\s+((?:\*?\w+\s*,\s*)+\*?\w+)\s*;', text):
            if m.start() in sbp: continue
            sn = m.group(1)
            if sn in struct_name_set and sn not in _CKW:
                _add_multi_decl(sn, m.group(2))

        for m in _fsig.finditer(text):
            if m.group(1) in struct_map and m.group(2) not in scope:
                scope[m.group(2)] = (m.group(1), True)

        # Pre-pass: rewrite static method calls on the type name itself.
        # e.g.  Counter.getCount()  ->  Counter__getCount(NULL)
        # We scan for  TypeName.methodName(  where TypeName is a known struct
        # (not a variable in scope) and the method exists.
        static_call_pat = re.compile(r'\b(\w+)\.(\w+)\s*\(')
        def _rewrite_static_calls(txt):
            res = []; j = 0; nj = len(txt)
            while j < nj:
                sm = static_call_pat.search(txt, j)
                if not sm: res.append(txt[j:]); break
                sname  = sm.group(1)
                mname  = sm.group(2)
                # Only handle if sname is a known struct type AND not a variable in scope
                if sname not in struct_map or sname in scope:
                    res.append(txt[j:sm.end()]); j = sm.end(); continue
                if mname not in method_sets.get(sname, {}):
                    res.append(txt[j:sm.end()]); j = sm.end(); continue
                # Find the matching paren
                po = sm.end() - 1
                try: pc = find_matching_paren(txt, po)
                except ValueError:
                    res.append(txt[j:sm.end()]); j = sm.end(); continue
                args_str = txt[po+1:pc].strip()
                # Pick the method variant (static methods have 'self' but we pass NULL)
                variants = method_sets[sname][mname]
                mth = variants[0][2]
                mangled = mth.mangled_name()
                full_args = f'NULL, {args_str}' if args_str else 'NULL'
                res.append(txt[j:sm.start()])
                res.append(f'{mangled}({full_args})')
                j = pc + 1
            return ''.join(res)
        text = _rewrite_static_calls(text)

        result = []
        i = 0
        n = len(text)

        while i < n:
            m = call_pat.search(text, i)
            if not m: result.append(text[i:]); break

            if m.group(1) is not None:
                varname = m.group(1); op = m.group(2); methname = m.group(3)
                array_index = None; is_deref = False; field_name = None
            elif m.group(4) is not None:
                varname = m.group(4); op = m.group(5); methname = m.group(6)
                bracket_start = m.start() + text[m.start():].index('[')
                bracket_end   = text.index(']', bracket_start)
                array_index   = text[bracket_start:bracket_end + 1]
                is_deref = False; field_name = None
            elif m.group(7) is not None:
                varname = m.group(7); op = m.group(8); methname = m.group(9)
                bracket_start = m.start() + text[m.start():].index('[')
                bracket_end   = text.index(']', bracket_start)
                array_index   = text[bracket_start:bracket_end + 1]
                is_deref = False; field_name = None
            elif m.group(10) is not None:
                varname = m.group(10); op = m.group(11); methname = m.group(12)
                array_index = None; is_deref = True; field_name = None
            else:
                varname = m.group(13); field_name = m.group(14)
                op = m.group(15); methname = m.group(16)
                array_index = None; is_deref = False

            if field_name is not None:
                if varname not in scope or scope[varname][0] not in struct_map:
                    result.append(text[i:m.end()]); i = m.end(); continue
                outer_sd   = struct_map[scope[varname][0]]
                field_type = None
                for ftype, fname in outer_sd.all_fields:
                    clean_fname = re.sub(r'\[.*\]', '', fname)
                    if clean_fname == field_name:
                        field_type = re.sub(r'^struct\s+', '', ftype.strip()).strip()
                        break
                if field_type is None or field_type not in method_sets:
                    result.append(text[i:m.end()]); i = m.end(); continue
                if methname not in method_sets[field_type]:
                    result.append(text[i:m.end()]); i = m.end(); continue
                outer_is_ptr = scope[varname][1]
                self_arg     = (f'{varname}->{field_name}' if outer_is_ptr
                                else f'&{varname}.{field_name}')
                sname    = field_type
                variants = method_sets[sname][methname]
                is_deref = False; array_index = None
            else:
                if varname not in scope or scope[varname][0] not in method_sets:
                    result.append(text[i:m.end()]); i = m.end(); continue
                sname, is_ptr = scope[varname]
                if not is_ptr and varname in ppr:
                    if any(_fs <= m.start() <= _fe for _fs, _fe in ppr[varname]):
                        is_ptr = True
                if methname not in method_sets[sname]:
                    result.append(text[i:m.end()]); i = m.end(); continue
                variants = method_sets[sname][methname]

            paren_open = m.end() - 1
            try:
                paren_close = find_matching_paren(text, paren_open)
            except ValueError:
                result.append(text[i:m.end()]); i = m.end(); continue

            args_str = text[paren_open + 1: paren_close].strip()

            if field_name is not None:
                pass
            elif is_deref:
                self_arg = varname
            elif array_index is not None:
                self_arg = (f'{varname}{array_index}' if op == '->'
                            else f'&{varname}{array_index}')
            elif op == '->' or op == '->>' or is_ptr:
                self_arg = varname
            else:
                self_arg = f'&{varname}'

            stripped_args     = args_str
            _self_candidates  = [self_arg, self_arg.lstrip('&')]
            if is_deref or (not is_deref and field_name is None
                            and varname in scope and scope[varname][1]):
                _self_candidates += [f'&{varname}', varname]
            _self_candidates = list(dict.fromkeys(c for c in _self_candidates if c))
            for prefix in _self_candidates:
                pat = re.compile(r'^' + re.escape(prefix) + r'\s*(?:,\s*|$)')
                if pat.match(stripped_args):
                    stripped_args = pat.sub('', stripped_args).strip()
                    break

            user_arg_count = 0
            if stripped_args:
                depth = 0; user_arg_count = 1
                for ch in stripped_args:
                    if ch in ('(', '['):   depth += 1
                    elif ch in (')', ']'): depth -= 1
                    elif ch == ',' and depth == 0: user_arg_count += 1

            chosen_mth = None
            for req, total, mth_candidate in variants:
                if req <= user_arg_count <= total:
                    if chosen_mth is None or total < chosen_mth[0]:
                        chosen_mth = (total, mth_candidate)
            if chosen_mth is None:
                chosen_mth = (0, max(variants, key=lambda v: v[1])[2])
            mth = chosen_mth[1]

            if user_arg_count < len(mth.params):
                short_params = mth.params[:user_arg_count]
                stub_name = (
                    mth.struct_name + '__' + mth.name + '__' + '__'.join(
                        re.sub(r'[^a-zA-Z0-9_]', '_', p.type_str.strip())
                        for p in short_params
                    ) if short_params
                    else mth.struct_name + '__' + mth.name + '__void'
                )
                call_name = stub_name
            else:
                call_name = mth.mangled_name()

            if field_name is None and mth.struct_name != sname:
                self_arg = (
                    f'(struct {mth.struct_name}*){self_arg}'
                    if (self_arg.startswith('&') or self_arg.startswith('(') or is_ptr)
                    else f'(struct {mth.struct_name}*)&{self_arg}'
                )
            full_args = f'{self_arg}, {stripped_args}' if stripped_args else self_arg

            result.append(text[i:m.start()])
            result.append(f'{call_name}({full_args})')
            i = paren_close + 1

        return ''.join(result)

    def _rewrite_casts(text, ptr_mode):
        """Handle ((T*)expr)->meth() and ((T)expr).meth() forms."""
        hdr = (re.compile(r'\(\s*\((?:struct\s+)?(\w+)\s*\*\s*\)\s*') if ptr_mode
               else re.compile(r'\(\s*\((?:struct\s+)?(\w+)\s*\)\s*'))
        res = []; i = 0; n = len(text)
        while i < n:
            m = hdr.search(text, i)
            if not m: res.append(text[i:]); break
            tn = m.group(1)
            if not ptr_mode:
                hd = m.group(0); icp = m.start() + hd.rindex(')')
                if icp > 0 and text[icp-1] == '*':
                    res.append(text[i:m.end()]); i = m.end(); continue
            if tn not in struct_map or tn not in method_sets:
                res.append(text[i:m.end()]); i = m.end(); continue
            try: oc = find_matching_paren(text, m.start())
            except ValueError: res.append(text[i:m.end()]); i = m.end(); continue
            inner = text[m.end():oc].strip()
            k = oc + 1
            while k < n and text[k] in ' \t\n': k += 1
            if ptr_mode:
                if k + 1 >= n or text[k:k+2] != '->':
                    res.append(text[i:m.end()]); i = m.end(); continue
                k += 2
            else:
                if k >= n or text[k] != '.':
                    res.append(text[i:m.end()]); i = m.end(); continue
                k += 1
            while k < n and text[k] in ' \t\n': k += 1
            ms2 = k
            while k < n and (text[k].isalnum() or text[k] == '_'): k += 1
            mn = text[ms2:k]
            if not mn: res.append(text[i:m.end()]); i = m.end(); continue
            while k < n and text[k] in ' \t\n': k += 1
            if k >= n or text[k] != '(':
                res.append(text[i:m.end()]); i = m.end(); continue
            if mn not in method_sets.get(tn, {}):
                res.append(text[i:k+1]); i = k + 1; continue
            po = k
            try: pc = find_matching_paren(text, po)
            except ValueError: res.append(text[i:k+1]); i = k + 1; continue
            astr = text[po+1:pc].strip()
            dm = re.match(r'^\(\s*\*\s*(\w+)\s*\)$', inner)
            if ptr_mode:
                cl = inner.lstrip('&'); pl = dm.group(1) if dm else cl
                sa_strip = f'(struct {tn}*){dm.group(1) if dm else inner}'
                for pfx in (sa_strip, f'(struct {tn}*){inner}', f'(struct {tn}*)&{cl}',
                             f'({tn}*){inner}', f'({tn}*)&{cl}', inner, pl, f'&{pl}'):
                    if re.compile(r'^' + re.escape(pfx) + r'\s*(?:,\s*|$)').match(astr):
                        astr = re.sub(r'^' + re.escape(pfx) + r'\s*(?:,\s*|$)', '', astr).strip()
                        break
            else:
                pl = dm.group(1) if dm else inner.strip('&')
                for pfx in (f'(struct {tn}*){dm.group(1) if dm else inner}', f'&{pl}', pl):
                    if re.compile(r'^' + re.escape(pfx) + r'\s*(?:,\s*|$)').match(astr):
                        astr = re.sub(r'^' + re.escape(pfx) + r'\s*(?:,\s*|$)', '', astr).strip()
                        break
            vrs = method_sets[tn][mn]; ua = 0
            if astr:
                d = 0; ua = 1
                for ch in astr:
                    if ch in '([': d += 1
                    elif ch in ')]': d -= 1
                    elif ch == ',' and d == 0: ua += 1
            mth  = min(vrs, key=lambda v: abs(v[1]-ua))[2]
            own  = mth.struct_name
            if ptr_mode:
                real_sa = (f'(struct {own}*){dm.group(1)}' if dm
                           else f'(struct {own}*){inner}')
            else:
                if dm: real_sa = f'(struct {own}*){dm.group(1)}'
                elif inner.startswith('(') and inner.endswith(')'): real_sa = f'(struct {own}*){inner}'
                else: real_sa = f'(struct {own}*)&{inner}'
            sh = mth.params[:ua]
            cn = (mth.mangled_name() if ua >= len(mth.params) else
                  own + '__' + mth.name + (
                      '__' + '__'.join(re.sub(r'[^a-zA-Z0-9_]', '_', p.type_str.strip()) for p in sh)
                      if sh else '__void'
                  ))
            res.append(text[i:m.start()])
            res.append(f'{cn}({real_sa},{astr})' if astr else f'{cn}({real_sa})')
            i = pc + 1
        return ''.join(res)

    source = _rewrite_casts(source, True)
    source = _rewrite_casts(source, False)
    return rewrite_block(source, extra_scope or {})
