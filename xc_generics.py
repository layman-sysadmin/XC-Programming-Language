"""xc_generics.py — Generic (template) function extraction and instantiation."""

import re

from xc_types import Param, GenericFunctionDef
from xc_utils import find_matching_brace, find_matching_paren, parse_params


def _generic_mangle(name: str, type_args: list) -> str:
    """Canonical mangled name for a (name, type_args) instantiation."""
    tags = [re.sub(r'[^a-zA-Z0-9_]', '_', t.strip()) for t in type_args]
    return name + '__' + '__'.join(tags)


def extract_generic_functions(source: str) -> tuple:
    """Remove all  generic <ret> <name><type A, ...>(params) { body }
    definitions from source.

    Returns:
        templates : dict  name -> list[GenericFunctionDef]
        cleaned   : str   source with generic definitions blanked out
    """
    generic_pat = re.compile(
        r'\bgeneric\b\s+'            # keyword
        r'([\w\s\*]+?)\s+'           # return type  (group 1)
        r'(\w+)\s*'                  # function name (group 2)
        r'<\s*([\w\s,]+?)\s*>\s*'    # <type A, type B, ...> (group 3)
        r'\(([^)]*)\)\s*'            # ( params )    (group 4)
        r'\{',                       # opening brace
        re.MULTILINE,
    )

    templates = {}
    removals  = []

    for m in generic_pat.finditer(source):
        ret_type   = m.group(1).strip()
        fname      = m.group(2).strip()
        tparam_raw = m.group(3).strip()
        params_raw = m.group(4).strip()

        # "type A, type B"  →  ['A', 'B']
        type_params = []
        for tp in tparam_raw.split(','):
            tp = tp.strip()
            if tp.startswith('type '):
                tp = tp[5:].strip()
            if tp:
                type_params.append(tp)

        if not type_params:
            raise SyntaxError(
                f"generic function '{fname}': no type parameters in <{tparam_raw}>"
            )

        params      = parse_params(params_raw)
        brace_start = m.end() - 1
        try:
            brace_end = find_matching_brace(source, brace_start)
        except ValueError:
            raise SyntaxError(f"generic function '{fname}': unmatched brace")

        body = source[brace_start + 1 : brace_end]
        gdef = GenericFunctionDef(
            return_type=ret_type,
            name=fname,
            type_params=type_params,
            params=params,
            body=body,
        )
        templates.setdefault(fname, []).append(gdef)
        removals.append((m.start(), brace_end + 1))

    # Blank out generic definitions (preserve newlines for error-message accuracy)
    out = list(source)
    for start, end in removals:
        for i in range(start, end):
            out[i] = '\n' if source[i] == '\n' else ' '

    return templates, ''.join(out)


def extract_specializations(source: str, templates: dict) -> tuple:
    """Remove all explicit specialization definitions from source.

    A specialization looks like a normal function definition whose name matches
    a known generic and whose angle-bracket type list contains only concrete
    types (no 'type' keyword):

        int function_name<char, char>(int arg1) { ... }

    The specialization is emitted immediately as a concrete C function with the
    mangled name  function_name__char__char .

    Returns:
        specialization_impls : dict  mangle_key -> C function text (pre-emitted)
        cleaned              : str   source with specialization defs blanked out
    """
    if not templates:
        return {}, source

    generic_names = set(templates.keys())

    spec_pat = re.compile(
        r'\b([\w\s\*]+?)\s+'          # return type  (group 1)
        r'(\w+)\s*'                   # function name (group 2)
        r'<\s*([\w\s\*,]+?)\s*>\s*'   # <concrete, types> — word chars only, no operators
        r'\(([^)]*)\)\s*'             # ( params )    (group 4)
        r'\{',                        # opening brace
        re.MULTILINE,
    )

    specialization_impls = {}
    removals = []

    for m in spec_pat.finditer(source):
        ret_type   = m.group(1).strip()
        fname      = m.group(2).strip()
        types_raw  = m.group(3).strip()
        params_raw = m.group(4).strip()

        if fname not in generic_names:
            continue

        # Skip if this was actually preceded by 'generic'
        prefix = source[max(0, m.start() - 8) : m.start()].strip()
        if prefix.endswith('generic'):
            continue

        type_args = [a.strip() for a in types_raw.split(',')]
        if any(a.startswith('type ') or not a for a in type_args):
            continue

        # Verify arity matches a known generic
        matching_generic = next(
            (gdef for gdef in templates[fname] if len(gdef.type_params) == len(type_args)),
            None
        )
        if matching_generic is None:
            continue

        mangled = _generic_mangle(fname, type_args)

        brace_start = m.end() - 1
        try:
            brace_end = find_matching_brace(source, brace_start)
        except ValueError:
            raise SyntaxError(f"specialization '{fname}<{types_raw}>': unmatched brace")

        spec_body = source[brace_start + 1 : brace_end]
        params    = parse_params(params_raw)
        param_str = ', '.join(f'{p.type_str} {p.name}' for p in params)

        func_text = (
            f'/* specialization: {fname}<{types_raw}> */\n'
            f'{ret_type} {mangled}({param_str}) {{\n'
            f'{spec_body}\n'
            f'}}\n'
        )
        # Last definition wins for duplicate specializations
        specialization_impls[mangled] = func_text
        removals.append((m.start(), brace_end + 1))

    out = list(source)
    for start, end in removals:
        for i in range(start, end):
            out[i] = '\n' if source[i] == '\n' else ' '

    return specialization_impls, ''.join(out)


def _substitute_type_params(text: str, type_params: list, type_args: list) -> str:
    """Replace each type-param name with its concrete type arg, whole-word only."""
    for param, arg in zip(type_params, type_args):
        text = re.sub(r'\b' + re.escape(param) + r'\b', arg.strip(), text)
    return text


def instantiate_generics(source: str, templates: dict,
                         specialization_impls: dict) -> tuple:
    """Scan source for  name<int, float>(...)  call sites.

    For each call:
      - If a pre-emitted specialization exists, rewrite the call without emitting.
      - Otherwise instantiate the template lazily (once per unique combo).

    Returns:
        generic_impls : str   all lazily-emitted function definitions
        rewritten     : str   source with call sites rewritten to mangled names
    """
    if not templates:
        return '', source

    # Type args must look like type names: word chars, spaces, stars, commas, no operators
    # This prevents matching 'a<0 or b<0>' as a generic call
    call_pat = re.compile(r'\b(\w+)\s*<\s*([\w\s\*,]+?)\s*>\s*\(')
    lazy_emitted : dict = {}

    result = []
    i = 0; n = len(source)

    while i < n:
        m = call_pat.search(source, i)
        if not m: result.append(source[i:]); break

        fname = m.group(1)
        if fname not in templates:
            result.append(source[i : m.end()]); i = m.end(); continue

        type_args = [a.strip() for a in m.group(2).split(',')]

        gdef = next(
            (c for c in templates[fname] if len(c.type_params) == len(type_args)),
            None
        )
        if gdef is None:
            result.append(source[i : m.end()]); i = m.end(); continue

        if any(not a for a in type_args):
            raise SyntaxError(f"generic call '{fname}<{m.group(2)}>': empty type argument")

        mangled = _generic_mangle(fname, type_args)

        if mangled not in specialization_impls and mangled not in lazy_emitted:
            sub = lambda t, _tp=gdef.type_params, _ta=type_args: _substitute_type_params(t, _tp, _ta)
            concrete_ret    = sub(gdef.return_type)
            concrete_params = [
                Param(type_str=sub(p.type_str), name=p.name, default=p.default)
                for p in gdef.params
            ]
            concrete_body = sub(gdef.body)
            param_str     = ', '.join(f'{p.type_str} {p.name}' for p in concrete_params)
            lazy_emitted[mangled] = (
                f'{concrete_ret} {mangled}({param_str}) {{\n'
                f'{concrete_body}\n'
                f'}}\n'
            )

        paren_open = m.end() - 1
        try:
            paren_close = find_matching_paren(source, paren_open)
        except ValueError:
            result.append(source[i : m.end()]); i = m.end(); continue

        args_str = source[paren_open + 1 : paren_close]
        result.append(source[i : m.start()])
        result.append(f'{mangled}({args_str})')
        i = paren_close + 1

    rewritten     = ''.join(result)
    generic_impls = '\n'.join(lazy_emitted.values())
    return generic_impls, rewritten
