"""xc_types.py — Core dataclasses and built-in type maps."""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Param:
    type_str: str
    name: str
    default: str = ''

    def __repr__(self):
        return f"{self.type_str} {self.name}"

    def signature_type(self):
        """Normalised type string used for prototype comparison."""
        return re.sub(r'\s+', ' ', self.type_str.strip())


@dataclass
class FunctionDef:
    return_type: str
    name: str
    params: list
    body: str
    struct_name: Optional[str] = None
    inherited_from: Optional[str] = None
    abstract: bool = False

    def prototype_key(self):
        """(return_type, name, (param_types...)) used to validate overrides."""
        return (
            re.sub(r'\s+', ' ', self.return_type.strip()),
            self.name,
            tuple(p.signature_type() for p in self.params),
        )

    def mangled_name(self):
        """Unique C name encoding struct + base name + param types."""
        parts = []
        if self.struct_name: parts.append(self.struct_name)
        parts.append(self.name)
        for p in self.params:
            tag = re.sub(r'[^a-zA-Z0-9_]', '_', p.type_str.strip())
            parts.append(tag)
        return "__".join(parts)


@dataclass
class GenericFunctionDef:
    """A template function defined with the 'generic' keyword."""
    return_type: str        # may contain type param names, e.g. "A"
    name: str
    type_params: list       # ordered list of type param names, e.g. ['A', 'B']
    params: list            # list of Param (types may reference type params)
    body: str               # raw body text (type params not yet substituted)

    def mangle(self, type_args: list) -> str:
        """Return the C function name for a concrete instantiation."""
        tags = [re.sub(r'[^a-zA-Z0-9_]', '_', t.strip()) for t in type_args]
        return self.name + '__' + '__'.join(tags)


@dataclass
class StructDef:
    name: str
    parent: Optional[str]
    fields: list
    methods: list
    is_proto: bool = False
    static_fields: list = field(default_factory=list)
    all_fields: list = field(default_factory=list)
    all_methods: list = field(default_factory=list)
    all_statics: list = field(default_factory=list)


INT_TYPE_MAP = {
    'int8':   'int8_t',
    'uint8':  'uint8_t',
    'int16':  'int16_t',
    'uint16': 'uint16_t',
    'int32':  'int32_t',
    'uint32': 'uint32_t',
    'int64':  'int64_t',
    'uint64': 'uint64_t',
}

CSTRING_MAP = {
    'char16': ('char', 17),
    'char32': ('char', 33),
    'char64': ('char', 65),
}


def replace_builtin_types(text: str) -> str:
    for src, dst in INT_TYPE_MAP.items():
        text = re.sub(rf'\b{src}\b', dst, text)
    return text
