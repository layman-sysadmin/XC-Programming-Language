# XC — C with Upgrades

XC is a transpiler that compiles a superset of C to standard **C99**. Every feature is a zero-cost abstraction — no runtime library, no garbage collector, no hidden allocations. The output is always human-readable C99 that compiles with any standard C compiler.

```
python3 transpiler.py source.xc output.c
```

---

## Quick Reference

| Feature | XC syntax |
|---|---|
| Fixed-width integers | `int8 int16 int32 int64 uint8 uint16 uint32 uint64` |
| Fixed-size strings | `char16 char32 char64` |
| Binary literals | `0b10110011` |
| Struct methods | `struct Foo { void bar() { } }` |
| Inheritance | `struct Child extends struct Parent { }` |
| Abstract struct | `proto struct Base { int f(); }` |
| Static fields | `static int32 count = 0;` inside struct |
| Heap allocation | `new TYPE` / `new TYPE[N]` / `delete ptr` |
| References | `int& r; *r = &x;` |
| Default parameters | `void f(int x, int y = 0)` |
| Function overloading | same name, different param types |
| Generic functions | `generic T max<type T>(T a, T b)` |
| Generic specialization | `int max<int>(int a, int b) { ... }` |
| Keyword operators | `and` `or` `not` `xor` `nor` |
| Chained comparisons | `0 < x < 100` |
| Interval conditions | `x == [(lo, hi)]` |
| String operations | `buf = "a" + s + "b"` `buf += s` `buf -= 4` |
| Uniform pointer decls | `int* a, b, c` → all three are pointers |
| Regex source rewrite | `#regex "pattern" "replacement"` |
| Header guards | `#start NAME` / `#end NAME` |

---

## 1 — Fixed-Width Integer Types

Short aliases that map directly to `stdint.h`. XC automatically adds `#include <stdint.h>` to the output.

```c
// XC
int8  a = -1;       uint8  b = 255;
int16 c = 30000;    uint16 d = 65535;
int32 x = 100;      uint32 y = 0xDEADBEEF;
int64 big = 9000000000LL;
```
```c
// C99 output
int8_t  a = -1;     uint8_t  b = 255;
int16_t c = 30000;  uint16_t d = 65535;
int32_t x = 100;    uint32_t y = 0xDEADBEEF;
int64_t big = 9000000000LL;
```

---

## 2 — Fixed-Size C-Strings

`char16`, `char32`, `char64` declare character arrays with the null terminator slot already counted. `char32` holds 32 usable characters.

```c
// XC
char16 name;
char32 title;
char64 message;
```
```c
// C99 output
char name[17];
char title[33];
char message[65];
```

---

## 3 — Binary Literals

```c
// XC
uint8  mask  = 0b11110000;
uint32 flags = 0b10101010u;
int    mixed = 0B00001111;
```
```c
// C99 output
uint8_t  mask  = 0xF0;
uint32_t flags = 0xAAu;
int      mixed = 0x0F;
```

Bit width is preserved: 4-bit patterns → 1 hex digit, 8-bit → 2 digits, etc.

---

## 4 — Struct Methods

Methods are defined inside the struct body. The transpiler emits them as top-level C functions with mangled names. Inside a method body, bare member names automatically become `self->member`. The keyword `this` also works.

```c
// XC
struct Point {
    int32 x;
    int32 y;
    int32 dist() { return x*x + y*y; }
    void  set(int32 nx, int32 ny) { x = nx; y = ny; }
};

struct Point p;
p.set(3, 4);
printf("%d\n", p.dist());   // 25
```
```c
// C99 output
struct Point { int32_t x; int32_t y; };

int32_t Point__dist(struct Point* self) {
    return self->x*self->x + self->y*self->y;
}
void Point__set__int32_t__int32_t(struct Point* self, int32_t nx, int32_t ny) {
    self->x = nx; self->y = ny;
}

Point__set__int32_t__int32_t(&p, 3, 4);
printf("%d\n", Point__dist(&p));
```

**Mangling rule:** `StructName__methodName__paramType1__paramType2`  
Methods with no parameters have no type suffix.

**Call syntax:** `.` and `->` both work and are selected automatically based on whether the variable is a value or a pointer.

---

## 5 — Struct Inheritance

`extends` prepends the parent's fields into the child struct. The parent's methods are inherited and may be overridden. Pointer upcasting is safe because the memory layout is compatible.

```c
// XC
struct Animal {
    int32 legs;
    void describe() { printf("legs=%d\n", legs); }
};

struct Dog extends struct Animal {
    int32 tricks;
    void describe() { printf("dog, legs=%d tricks=%d\n", legs, tricks); }
};

struct Dog d;
d.legs = 4; d.tricks = 3;
d.describe();                       // calls Dog version

struct Animal* a = (struct Animal*)&d;
a->describe();                      // calls Animal version — type determines dispatch
```

Typedef structs use `extends` without the `struct` keyword on the parent name:

```c
typedef struct Child extends Parent { ... } Child;
```

---

## 6 — Proto Structs (Abstract Base Types)

`proto struct` is a type contract. It is **completely erased** from the C99 output — no `sizeof`, no allocation. Concrete children must implement every abstract method (declared with `;` instead of a body). Concrete methods defined in a proto ARE inherited.

```c
// XC
proto struct Shape {
    float area();           // abstract — children must implement
    void  reset() { }      // concrete — inherited as-is
};

struct Circle extends struct Shape {
    float radius;
    float area() { return 3.14159f * radius * radius; }
};

struct Square extends struct Shape {
    float side;
    float area() { return side * side; }
};
```

Declaring a `proto struct` with an abstract method that has no implementation is a **compile-time error** in XC.

---

## 7 — Static Member Variables

Declared with `static` inside a struct body. The field is shared across all instances — one file-scope global per declared type. Static fields are **not stored** in the struct.

```c
// XC
struct Counter {
    int32 id;
    static int32 count = 0;
    void  inc()  { count = count + 1; }
    int32 get()  { return count; }
};

struct Counter a, b;
a.inc(); b.inc();
printf("%d\n", a.get());    // 2 — shared
```
```c
// C99 output (static hoisted to file scope)
static int32_t Counter__count = 0;
struct Counter { int32_t id; };
```

A child struct that re-declares the same static name gets its **own independent global**.

---

## 8 — Heap Allocation

```c
// XC
int32*       p   = new int32;
struct Foo*  f   = new struct Foo;
int32*       arr = new int32[64];

delete p;
delete f;
delete arr;
```
```c
// C99 output  (#include <stdlib.h> added automatically)
int32_t*     p   = (int32_t*)malloc(sizeof(int32_t));
struct Foo*  f   = (struct Foo*)malloc(sizeof(struct Foo));
int32_t*     arr = (int32_t*)malloc(sizeof(int32_t) * (64));

free(p);
free(f);
free(arr);
```

`delete` calls `free()` only. Nullifying the pointer afterward is the programmer's responsibility.

---

## 9 — Reference Variables

A reference (`TYPE& name`) is a pointer that auto-dereferences on every read and write. Rebinding a reference uses the explicit pointer syntax `*r = &x`. References are compatible with functions that expect a plain pointer.

```c
// XC
void scale(int32& x, int32 factor) {
    x = x * factor;     // auto-dereferenced — no * needed
}

void demo() {
    int32  val = 10;
    int32& r;
    *r = &val;          // bind r to val (explicit rebind syntax)
    scale(r, 3);        // val is now 30
}
```
```c
// C99 output
void scale(int32_t* x, int32_t factor) {
    (*x) = (*x) * factor;
}
void demo() {
    int32_t  val = 10;
    int32_t* r;
    r = &val;
    scale(r, 3);
}
```

---

## 10 — Default Parameters

Trailing parameters may have default values. XC generates forwarding stubs so every call variant has a distinct C name.

```c
// XC
void log(char32 msg, int32 level = 1, int32 color = 0) {
    // body
}

log("hello");           // level=1, color=0
log("hello", 2);        // level=2, color=0
log("hello", 2, 3);     // all explicit
```
```c
// C99 output — three distinct functions, no ambiguity
void log__char(char msg[33]) {
    log__char__int32_t__int32_t(msg, 1, 0);
}
void log__char__int32_t(char msg[33], int32_t level) {
    log__char__int32_t__int32_t(msg, level, 0);
}
void log__char__int32_t__int32_t(char msg[33], int32_t level, int32_t color) {
    // body
}
```

---

## 11 — Function Overloading

Multiple functions may share a name if their parameter types differ. The transpiler mangles each definition and rewrites all call sites.

```c
// XC
int32 add(int32 a, int32 b)              { return a + b; }
float add(float a, float b)              { return a + b; }
int32 add(int32 a, int32 b, int32 c)     { return a + b + c; }

add(1, 2);          // → add__int32_t__int32_t
add(1.0f, 2.0f);    // → add__float__float
add(1, 2, 3);       // → add__int32_t__int32_t__int32_t
```

---

## 12 — Generic Functions

Generic functions are parameterised by type. The keyword is `generic`; type parameters use the `type` prefix inside `< >`. A generic is not compiled until it is called — the transpiler instantiates a concrete C function for each unique combination of type arguments.

```c
// XC — generic definition
generic A max<type A>(A a, A b) {
    if (a > b) return a;
    return b;
}

// Instantiated automatically at each call site
int   m1 = max<int>(10, 7);        // emits max__int
float m2 = max<float>(3.14f, 1.0f); // emits max__float
```
```c
// C99 output (generated on demand)
int   max__int(int a, int b)     { if (a > b) return a; return b; }
float max__float(float a, float b) { if (a > b) return a; return b; }
```

Multiple type parameters are separated by commas:

```c
generic int pack<type A, type B>(A val1, B val2) {
    return (int)val1 + (int)val2;
}
int r = pack<float, int>(3.14f, 2);   // emits pack__float__int
```

### Generic Specializations

An explicit specialization replaces the generic body for one specific set of type arguments. It is compiled immediately (not lazily) and takes priority over the template.

```c
// XC — generic + one hand-written specialization
generic int compare<type A>(A a, A b) { return (a > b) - (a < b); }

// Specialization for char* (string comparison)
int compare<char*>(char* a, char* b) { return strcmp(a, b); }

// Calls
compare<int>(3, 5);         // uses generic template → compare__int
compare<char*>("x", "y");   // uses specialization   → compare__char_
```

---

## 13 — Keyword Operators

```c
// XC                           // C99 output
if (x > 0 and y > 0)  { }      // x > 0 && y > 0
if (done or failed)    { }      // done || failed
if (not ready)         { }      // !ready
if (a xor b)           { }      // !(a) != !(b)
if (a nor b)           { }      // !((a) || (b))
```

Keyword operators compose with each other:

```c
if (x > 0 and not done)    // x > 0 && !done
if (a or b and not c)      // a || (b && !c)
if (not (a or b))          // !(a || b)   — parens supported
```

---

## 14 — Chained Comparisons

Write comparisons the way they read in mathematics. The transpiler expands them to paired `&&` expressions.

```c
// XC                    // C99 output
0 < x < 100             x > 0 && x < 100
lo <= val <= hi         val >= lo && val <= hi
-50 < temp < 50         temp > -50 && temp < 50
a < b < c < d           a < b && b < c && c < d
```

Literals on the left side are flipped automatically: `5 > x` becomes `x < 5`.

---

## 15 — Interval Conditions

Test whether a variable falls inside a mathematical interval. Use `(` / `)` for open endpoints and `[` / `]` for closed endpoints.

```c
// XC                           // C99 output
x == [(0, 100)]                 x > 0 && x < 100        // open
x == [[0, 100]]                 x >= 0 && x <= 100       // closed
x == [[0, 100)]                 x >= 0 && x < 100        // half-open
x == [(-INF, 0]]                x <= 0                   // left-infinite
x == [[0, +INF)]                x >= 0                   // right-infinite
x == [R]                        1                        // always true
```

**Union** of intervals with `U`:

```c
// XC                                 // C99 output
x == [(-INF, 0] U (5, +INF)]         x <= 0 || x > 5
```

Swapped bounds (e.g. `[(100, 0)]`) produce a warning and are **automatically corrected**. An empty `[]` is a compile-time error. Adjacent intervals without `U` are a compile-time error.

---

## 16 — String Operations

Assignment, concatenation, and shrinkage are overloaded for `char` array targets (`char16`, `char32`, `char64`, or any `char name[N]`). The right-hand side may chain pieces with `+`.

```c
// XC
char32 buf;
char32 src;

buf = "Hello";              // assign literal
buf = src;                  // assign variable
buf = "Hi " + src + "!";   // chained concat
buf[4] = src;               // write starting at offset 4
buf += " world";            // append
buf -= 3;                   // shorten by 3 characters
```
```c
// C99 output
strncpy(buf, "Hello", 6);
strcpy(buf, src);
strncpy(buf, "Hi ", 3); strcat(buf, src); strcat(buf, "!");
strncpy(buf+(sizeof(char)*4), src, ...);
strcat(buf, " world");
{ int s = strlen(buf); buf[(s > 3) ? s-3 : 0] = '\0'; }
```

Plain `int` arrays, single `char` variables, and non-char expressions are not affected.

---

## 17 — Uniform Pointer Declarations

In standard C, `int* a, b, c` makes only `a` a pointer. XC applies the `*` to **all** names on the line.

```c
// XC                        // C99 output
int32* a, b, c;              int32_t* a; int32_t* b; int32_t* c;
char*  s1, s2;               char* s1; char* s2;
int*   p, q, r;              int* p; int* q; int* r;
```

To mix pointer and non-pointer on the same line, declare them on separate lines.

---

## 18 — Regex Source Rewrites

`#regex` applies a Python-style regex substitution to all following source text until `#endex`. Directives nest — each `#endex` closes the innermost open `#regex`.

```c
// XC
#regex "\bold\b" "prev"
#regex "\bMAGIC\b" "42"
int32 old_val = MAGIC;       // → int32_t prev_val = 42
#endex
int32 old_count = 0;         // MAGIC scope closed; "old"→"prev" still active
#endex
```
```c
// C99 output
int32_t prev_val   = 42;
int32_t prev_count = 0;
```

An orphan `#endex` with no matching `#regex` is a compile-time error.

---

## 19 — Header Guards

```c
// XC
#start MY_HEADER_H
// ... file contents ...
#end MY_HEADER_H
```
```c
// C99 output
#ifndef MY_HEADER_H
#define MY_HEADER_H
// ... file contents ...
#endif /* MY_HEADER_H */
```

---

## Name Mangling Reference

XC uses a consistent mangling scheme for all generated C function names.

| XC declaration | Mangled C name |
|---|---|
| `struct Foo { void bar() }` | `Foo__bar` |
| `struct Foo { void bar(int x) }` | `Foo__bar__int` |
| `struct Foo { void bar(int x, float y) }` | `Foo__bar__int__float` |
| `int add(int a, int b)` (overloaded) | `add__int__int` |
| `float add(float a, float b)` (overloaded) | `add__float__float` |
| `generic T f<type T>(T x)` called with `int` | `f__int` |
| `generic T f<type T, type U>(...)` called with `int, float` | `f__int__float` |

Static struct fields: `StructName__fieldName` (file-scope global).

---

## Running the Transpiler

```bash
python3 transpiler.py input.xc output.c
gcc -std=c99 -o program output.c
```

All `.py` files must be in the same directory. No external Python dependencies.

---

## What XC Does Not Do

- No garbage collection or reference counting
- No runtime type information
- `delete` does not null the pointer — that is your responsibility
- Generic type arguments are resolved at transpile time only — no runtime polymorphism
- No variadic generics (fixed arity per definition)
