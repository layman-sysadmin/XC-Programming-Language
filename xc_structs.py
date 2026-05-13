"""xc_structs.py — Struct parsing, inheritance resolution, and C code generation."""

import re
import sys
from copy import deepcopy

from xc_types import Param, FunctionDef, StructDef
from xc_utils import find_matching_brace, find_matching_paren, parse_params


def parse_struct_body(inner: str, struct_name: str):
    """Split a struct body into (fields, methods, static_fields)."""
    fields = []
    methods = []

    method_pattern = re.compile(
        r'([\w\s\*]+?)\s+(\w+)\s*\(([^)]*)\)\s*\{',
        re.MULTILINE
    )
    consumed_ranges = []

    for m in method_pattern.finditer(inner):
        func_name = m.group(2).strip()
        if func_name in ('if', 'for', 'while', 'switch', 'do', 'else'): continue
        brace_start = m.end() - 1
        try:
            brace_end = find_matching_brace(inner, brace_start)
        except ValueError:
            continue
        body = inner[brace_start + 1: brace_end]
        return_type = m.group(1).strip()
        func_name = m.group(2).strip()
        params = parse_params(m.group(3))
        methods.append(FunctionDef(
            return_type=return_type,
            name=func_name,
            params=params,
            body=body,
            struct_name=struct_name,
            abstract=False,
        ))
        consumed_ranges.append((m.start(), brace_end + 1))

    remaining = inner
    for start, end in sorted(consumed_ranges, reverse=True):
        remaining = remaining[:start] + ' ' * (end - start) + remaining[end:]

    abstract_pattern = re.compile(r'([\w\s\*]+?)\s+(\w+)\s*\(([^)]*)\)\s*;')
    abstract_ranges = []
    for m in abstract_pattern.finditer(remaining):
        return_type = m.group(1).strip()
        func_name = m.group(2).strip()
        if func_name in ('if', 'for', 'while', 'switch', 'do', 'else'): continue
        params = parse_params(m.group(3))
        methods.append(FunctionDef(
            return_type=return_type,
            name=func_name,
            params=params,
            body='',
            struct_name=struct_name,
            abstract=True,
        ))
        abstract_ranges.append((m.start(), m.end()))

    for start, end in sorted(abstract_ranges, reverse=True):
        remaining = remaining[:start] + ' ' * (end - start) + remaining[end:]

    static_fields = []
    regular_fields = []
    for line in remaining.split(';'):
        line = line.strip()
        if not line or re.match(r'^[\s{}]*$', line): continue
        tokens = line.split()
        if len(tokens) >= 2:
            if tokens[0] == 'static':
                rest = line[len('static'):].strip()
                eq_pos = rest.find('=')
                if eq_pos != -1:
                    init_val = rest[eq_pos+1:].strip()
                    rest = rest[:eq_pos].strip()
                else:
                    init_val = None
                rest_tokens = rest.split()
                if len(rest_tokens) >= 2:
                    varname = rest_tokens[-1].lstrip('*')
                    stars   = rest_tokens[-1][:len(rest_tokens[-1]) - len(varname)]
                    type_part = ' '.join(rest_tokens[:-1]) + stars
                    static_fields.append((type_part.strip(), varname, init_val))
            else:
                if ',' in line:
                    first_comma = line.index(',')
                    before_comma = line[:first_comma].strip()
                    bc_tokens = before_comma.split()
                    if len(bc_tokens) >= 2:
                        base_type = ' '.join(bc_tokens[:-1])
                        all_names_str = bc_tokens[-1] + ',' + line[first_comma+1:]
                        for decl in all_names_str.split(','):
                            decl = decl.strip()
                            if not decl: continue
                            name = decl.lstrip('*')
                            stars = decl[:len(decl) - len(name)]
                            regular_fields.append((base_type + stars, name))
                        continue
                name = tokens[-1].lstrip('*')
                stars = tokens[-1][:len(tokens[-1]) - len(name)]
                type_part = ' '.join(tokens[:-1]) + stars
                regular_fields.append((type_part.strip(), name))

    return regular_fields, methods, static_fields


def _find_enclosing_function(source: str, pos: int):
    """Given a position in source, return the start of the enclosing function,
    or None if pos is at file scope."""
    depth = 0
    i = pos - 1
    while i >= 0:
        c = source[i]
        if c == '}':
            depth += 1
        elif c == '{':
            if depth == 0:
                j = i - 1
                while j >= 0 and source[j] in ' \t\n': j -= 1
                if j >= 0 and source[j] == ')':
                    paren_depth = 1
                    j -= 1
                    while j >= 0 and paren_depth > 0:
                        if   source[j] == ')': paren_depth += 1
                        elif source[j] == '(': paren_depth -= 1
                        j -= 1
                    while j >= 0 and source[j] in ' \t\n': j -= 1
                    while j >= 0 and source[j] not in ';\n{}': j -= 1
                    func_start = j + 1
                    while func_start < pos and source[func_start] in ' \t\n':
                        func_start += 1
                    return func_start
                else:
                    return None
            else:
                depth -= 1
        i -= 1
    return None


def resolve_inheritance(structs: list):
    """Walk every StructDef and fill in all_fields / all_methods / all_statics."""
    struct_map = {s.name: s for s in structs}
    resolved = set()

    def resolve(sd: StructDef):
        if sd.name in resolved: return

        if sd.parent is None:
            sd.all_fields  = list(sd.fields)
            sd.all_methods = list(sd.methods)
            for mth in sd.all_methods:
                mth.struct_name = sd.name
            sd.all_statics = [(t, v, i, sd.name) for t, v, i in sd.static_fields]
            resolved.add(sd.name)
            return

        parent_name = sd.parent
        if parent_name not in struct_map:
            raise NameError(
                f"struct '{sd.name}' extends unknown struct '{parent_name}'"
            )

        parent = struct_map[parent_name]
        resolve(parent)

        sd.all_fields = list(parent.all_fields) + list(sd.fields)

        merged = {}
        for mth in parent.all_methods:
            copied = deepcopy(mth)
            copied.inherited_from = mth.struct_name
            merged[mth.name] = copied

        for own_mth in sd.methods:
            own_mth.struct_name = sd.name
            if own_mth.name in merged:
                parent_mth = merged[own_mth.name]

                own_ret    = re.sub(r'\s+', ' ', own_mth.return_type.strip())
                parent_ret = re.sub(r'\s+', ' ', parent_mth.return_type.strip())

                own_required        = [p for p in own_mth.params if not p.default]
                own_types_all       = tuple(p.signature_type() for p in own_mth.params)
                own_types_req       = tuple(p.signature_type() for p in own_required)
                parent_types        = tuple(p.signature_type() for p in parent_mth.params)
                extra_params        = own_mth.params[len(parent_mth.params):]
                extra_all_defaulted = all(p.default for p in extra_params)
                prefix_matches      = own_types_req == parent_types

                if own_ret != parent_ret or not prefix_matches or not extra_all_defaulted:
                    raise TypeError(
                        f"'{sd.name}.{own_mth.name}' overrides "
                        f"'{parent_name}.{own_mth.name}' but the signature differs.\n"
                        f"  Parent : {parent_ret} {parent_mth.name}({', '.join(parent_types)})\n"
                        f"  Child  : {own_ret} {own_mth.name}({', '.join(own_types_all)})\n"
                        f"  (Child may add trailing default params, but required params must match parent)"
                    )

                shared       = min(len(parent_mth.params), len(own_mth.params))
                own_names    = tuple(p.name for p in own_mth.params[:shared])
                parent_names = tuple(p.name for p in parent_mth.params[:shared])
                if own_names != parent_names:
                    mismatches = [
                        f"param {i+1}: '{pn}' -> '{cn}'"
                        for i, (pn, cn) in enumerate(zip(parent_names, own_names))
                        if pn != cn
                    ]
                    print(
                        f"Warning: '{sd.name}.{own_mth.name}' parameter names differ "
                        f"from '{parent_name}.{own_mth.name}': "
                        + ', '.join(mismatches),
                        file=sys.stderr
                    )

            merged[own_mth.name] = own_mth

        sd.all_methods = list(merged.values())

        own_static_names  = {v for _, v, _ in sd.static_fields}
        inherited_statics = [e for e in parent.all_statics if e[1] not in own_static_names]
        own_statics       = [(t, v, i, sd.name) for t, v, i in sd.static_fields]
        sd.all_statics    = inherited_statics + own_statics

        resolved.add(sd.name)

    for sd in structs:
        resolve(sd)


def build_c_struct(sd: StructDef, known_structs: set = None) -> str:
    """Emit a plain C struct with data fields only — no function pointers."""
    def qualify(t: str) -> str:
        if known_structs is None: return t
        t_stripped = t.strip()
        if t_stripped.startswith(('struct ', 'union ', 'enum ')):
            return t
        base = t_stripped.rstrip('* ').strip()
        if base in known_structs:
            return t_stripped.replace(base, f'struct {base}', 1)
        return t

    field_lines = '\n'.join(f'    {qualify(t)} {n};' for t, n in sd.all_fields)
    return f'struct {sd.name} {{\n{field_lines}\n}}'


def normalize_typedef_structs(source: str) -> str:
    """Normalize typedef-struct and bare-struct inheritance into canonical form."""
    typedef_names: set = set()
    plain_names:   set = set()

    for m in re.finditer(r'\btypedef\s+struct\s+(\w+)', source):
        typedef_names.add(m.group(1))
    for m in re.finditer(r'\bstruct\s+(\w+)\s*(?:\{|extends|;)', source):
        name = m.group(1)
        if name not in typedef_names: plain_names.add(name)

    def validate_extends(child_name: str, parent_name: str, has_struct_kw: bool):
        if parent_name in typedef_names:
            if has_struct_kw:
                raise SyntaxError(
                    f"XC syntax error: '{child_name} extends struct {parent_name}' — "
                    f"'{parent_name}' was declared with typedef, so write "
                    f"'extends {parent_name}' without the 'struct' keyword."
                )
        elif parent_name in plain_names:
            if not has_struct_kw:
                raise SyntaxError(
                    f"XC syntax error: '{child_name} extends {parent_name}' — "
                    f"'{parent_name}' is a plain struct declaration, so write "
                    f"'extends struct {parent_name}' with the 'struct' keyword."
                )

    def rewrite_bare_extends(text: str) -> str:
        pat = re.compile(
            r'\bstruct\s+(\w+)\s+extends\s+(struct\s+)?(\w+)\s*\{',
            re.MULTILINE
        )
        result = []
        i = 0
        for m in pat.finditer(text):
            pre = text[max(0, m.start()-20):m.start()].rstrip()
            if re.search(r'\btypedef\s*$', pre):
                result.append(text[i:m.end()]); i = m.end(); continue
            child      = m.group(1)
            has_struct = bool(m.group(2))
            parent     = m.group(3)
            validate_extends(child, parent, has_struct)
            result.append(text[i:m.start()])
            result.append(f'extend {parent} struct {child} {{')
            i = m.end()
        result.append(text[i:])
        return ''.join(result)

    source = rewrite_bare_extends(source)

    typedef_pat = re.compile(
        r'\btypedef\s+'
        r'(?:(proto)\s+)?'
        r'struct\s+(\w+)\s*'
        r'(?:extends\s+(struct\s+)?(\w+)\s*)?'
        r'\{',
        re.MULTILINE
    )

    result = []
    i = 0
    n = len(source)

    while i < n:
        m = typedef_pat.search(source, i)
        if not m:
            result.append(source[i:]); break

        is_proto    = bool(m.group(1))
        struct_name = m.group(2)
        has_struct  = bool(m.group(3))
        parent_name = m.group(4)
        brace_start = m.end() - 1

        if parent_name: validate_extends(struct_name, parent_name, has_struct)

        try:
            brace_end = find_matching_brace(source, brace_start)
        except ValueError:
            result.append(source[i:m.end()]); i = m.end(); continue

        after   = source[brace_end + 1:]
        alias_m = re.match(r'\s*(\w+)\s*;', after)
        alias_end = brace_end + 1 + alias_m.end() if alias_m else brace_end + 1

        result.append(source[i:m.start()])
        prefix = ''
        if is_proto:    prefix += 'proto '
        if parent_name: prefix += f'extend {parent_name} '
        result.append(f'{prefix}struct {struct_name} {{')
        result.append(source[m.end():brace_end + 1])
        result.append(';')
        i = alias_end

    return ''.join(result)


def extract_structs(source: str, typedef_origin: set = None):
    """Parse all struct definitions from source, resolve inheritance, emit C."""
    # Import here to avoid circular dependency with xc_methods
    from xc_methods import generate_method_impl

    structs = []
    replacements = []

    struct_pattern = re.compile(
        r'(?:(proto)\s+)?'
        r'(?:(?:extend|extends)\s+(?:struct\s+)?(\w+)\s+)?'
        r'struct\s+(\w+)\s*\{',
        re.MULTILINE
    )

    for m in struct_pattern.finditer(source):
        is_proto    = m.group(1) == 'proto'
        parent_name = m.group(2)
        struct_name = m.group(3)
        brace_start = m.end() - 1
        brace_end   = find_matching_brace(source, brace_start)
        inner       = source[brace_start + 1: brace_end]

        own_fields, own_methods, own_statics = parse_struct_body(inner, struct_name)

        if not is_proto:
            concrete_names = {mth.name for mth in own_methods if not mth.abstract}
            errors = [
                f"'{struct_name}.{mth.name}' is declared but never implemented "
                f"in '{struct_name}'. Either add an implementation or use 'proto struct'."
                for mth in own_methods if mth.abstract and mth.name not in concrete_names
            ]
            if errors: raise TypeError('\n'.join(errors))
            own_methods = [mth for mth in own_methods if not mth.abstract]

        structs.append(StructDef(
            name=struct_name,
            parent=parent_name,
            fields=own_fields,
            methods=own_methods,
            is_proto=is_proto,
            static_fields=own_statics,
        ))

        tail_match = re.match(r'\s*;', source[brace_end + 1:])
        end_pos = brace_end + 1 + (len(tail_match.group(0)) if tail_match else 0)
        replacements.append((m.start(), end_pos, struct_name))

    resolve_inheritance(structs)

    all_struct_names = {sd.name for sd in structs}
    if typedef_origin is None: typedef_origin = set()

    c_texts = {}
    for sd in structs:
        if sd.is_proto:
            c_texts[sd.name] = ''
        else:
            struct_decl  = build_c_struct(sd, all_struct_names) + ';\n'
            static_decls = []
            for type_str, varname, init_val, owner in sd.all_statics:
                if owner == sd.name:
                    mangled = f'{sd.name}__{varname}'
                    if init_val is not None:
                        static_decls.append(f'static {type_str} {mangled} = {init_val};')
                    else:
                        static_decls.append(f'static {type_str} {mangled};')
            static_block = '\n'.join(static_decls) + '\n' if static_decls else ''
            impl_lines = []
            _seen = set()
            _pn   = {s.name for s in structs if s.is_proto}
            for mth in sd.all_methods:
                if mth.abstract: continue
                mn = mth.mangled_name()
                if mn in _seen: continue
                if mth.struct_name == sd.name or mth.struct_name in _pn:
                    _seen.add(mn)
                    impl_lines.append(generate_method_impl(mth, sd, structs))
            td_alias = f'typedef struct {sd.name} {sd.name};\n' \
                       if sd.name in typedef_origin else ''
            parts = [static_block, struct_decl, td_alias] + impl_lines
            c_texts[sd.name] = '\n\n'.join(p for p in parts if p) + '\n'

    out    = source
    hoisted = []

    for (start, end, sname) in reversed(replacements):
        c_text               = c_texts[sname]
        enclosing_func_start = _find_enclosing_function(out, start)

        if enclosing_func_start is not None:
            sd = next((s for s in structs if s.name == sname), None)
            if sd and not sd.is_proto:
                struct_decl    = build_c_struct(sd, all_struct_names) + ';\n'
                typedef_alias  = f'typedef struct {sd.name} {sd.name};\n'
                impl_only_parts = []
                _s2  = set()
                _pn2 = {s.name for s in structs if s.is_proto}
                for mth in sd.all_methods:
                    if mth.abstract: continue
                    mn = mth.mangled_name()
                    if mn in _s2: continue
                    if mth.struct_name == sd.name or mth.struct_name in _pn2:
                        _s2.add(mn)
                        impl_only_parts.append(generate_method_impl(mth, sd, structs))
                impl_text = '\n\n'.join(impl_only_parts) + '\n\n' if impl_only_parts else ''
                hoisted.append((enclosing_func_start, struct_decl + '\n' + impl_text))
                out = out[:start] + typedef_alias + out[end:]
            else:
                out = out[:start] + c_text + out[end:]
        else:
            out = out[:start] + c_text + out[end:]

    _rd = {r: len(c_texts[s]) - (e - r) for r, e, s in replacements}
    _hm = {}
    for hp, it in hoisted:
        adj = hp + sum(d for r, d in _rd.items() if r < hp)
        _hm.setdefault(adj, []).append(it)
    for ap in sorted(_hm, reverse=True):
        out = out[:ap] + '\n' + '\n'.join(_hm[ap]) + out[ap:]

    return structs, out


def rewrite_static_accesses(source: str, structs: list) -> str:
    """Rewrite call-site accesses to static struct fields to their mangled names."""
    if not structs: return source

    static_map = {}
    for sd in structs:
        if sd.all_statics:
            static_map[sd.name] = {}
            for type_str, varname, init_val, owner in sd.all_statics:
                static_map[sd.name][varname] = f'{owner}__{varname}'

    if not static_map: return source

    struct_names = set(static_map.keys()) | {sd.name for sd in structs}
    scope = {}

    decl_pat = re.compile(r'\bstruct\s+(\w+)\s*(\*?)\s*(\w+)\s*(?:=\s*[^;]+)?;')
    for m in decl_pat.finditer(source):
        sname, is_ptr, varname = m.group(1), bool(m.group(2).strip()), m.group(3)
        if sname in struct_names: scope[varname] = (sname, is_ptr)

    C_KW = {'int','char','float','double','void','short','long','unsigned','signed',
             'struct','union','enum','return','if','else','while','for','do','switch',
             'case','break','continue','goto','sizeof','typedef','extern','static',
             'const','volatile','register','auto','inline'}
    typedef_decl_pat = re.compile(r'\b(\w+)\s*(\*?)\s*(\w+)\s*(?:=\s*[^;]+)?;')
    for m in typedef_decl_pat.finditer(source):
        sname, is_ptr, varname = m.group(1), bool(m.group(2).strip()), m.group(3)
        if sname in struct_names and sname not in C_KW and varname not in scope:
            scope[varname] = (sname, is_ptr)

    param_pat = re.compile(r'\b(\w+)\s*[*&]\s*(\w+)\s*(?=[,)])')
    for m in param_pat.finditer(source):
        sname, varname = m.group(1), m.group(2)
        if sname in struct_names and varname not in scope:
            scope[varname] = (sname, True)

    if not scope: return source

    access_pat = re.compile(r'(&?)\s*\b(\w+)\s*(->>?|\.)\s*(\w+)\b')

    def replace_static(m):
        addr_of = m.group(1)
        varname = m.group(2)
        op      = m.group(3)
        field   = m.group(4)
        if varname not in scope:                          return m.group(0)
        sname = scope[varname][0]
        if sname not in static_map:                       return m.group(0)
        if field not in static_map[sname]:                return m.group(0)
        return f'{addr_of}{static_map[sname][field]}'

    return access_pat.sub(replace_static, source)


def rewrite_struct_value_casts(source: str, structs: list) -> str:
    """Rewrite struct value-cast expressions to valid C99 pointer-dereference form."""
    if not structs: return source
    known = {sd.name for sd in structs}
    cast_pat = re.compile(
        r'\(\s*(?:struct\s+)?(\w+)\s*\)\s*(?!\*)(\w+)',
        re.MULTILINE
    )
    def replace_cast(m):
        type_name = m.group(1)
        expr      = m.group(2)
        if type_name not in known: return m.group(0)
        return f'*(struct {type_name}*)&({expr})'
    return cast_pat.sub(replace_cast, source)


def strip_stale_init_calls(source: str, structs: list) -> str:
    """Remove any calls to StructName_init(&var) that remain in the source."""
    for name in {sd.name for sd in structs}:
        pat = re.compile(
            r'^[ \t]*' + re.escape(name) + r'_init\s*\([^)]*\)\s*;[ \t]*',
            re.MULTILINE
        )
        source = pat.sub(lambda m: ' ' * len(m.group(0)), source)
    return source
