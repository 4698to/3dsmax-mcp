# MAXScript: Core Syntax Reference

## Language Fundamentals

- **Expression-based**: every construct yields a value, including `if`, `for`, blocks
- **Case-insensitive**: `Box`, `box`, `BOX` are identical identifiers
- **Type-free variables**: any variable can hold any type; type can change per assignment
- **Type-safe**: wrong operations on a value produce runtime errors, not silent corruption
- **1-indexed**: arrays, strings, and bit operations all start at index 1
- **Semicolons optional**: line breaks separate expressions; `;` separates on same line
- **Line continuation**: use `\` at end of line to continue on next line
- **Comments**: `-- single line comment` (no block comments)
- **Parentheses `()` are blocks**, not just grouping; top-level `()` creates a new scope

## Variables & Scope

```maxscript
-- Explicit declaration
global g = 10
local x = 1, y = 2, z = 3

-- Implicit: first use in a scope creates the variable
a = 42  -- global at top level, local inside (), fn, for, etc.

-- Explicit global access (bypass local shadowing)
::myGlobal = 99

-- Cascading assignment (assignment is an expression)
x = y = z = 0

-- Swap
swap a b
```

**Scope rules:**
- New scope created by: `()` at top level, `fn` body, `for` body, utility/rollout/plugin definitions, event handlers
- Inner block-expressions inside a scope do NOT create new scopes (only top-level `()` does)
- `for` loop variable is ALWAYS local to the loop, even if same name exists outside
- Implicitly declared variables go to the current scope context
- Variables in rollouts/utilities/plugins persist as "private globals" for the lifetime of the construct

**Gotcha**: implicit declaration + re-running scripts can cause a variable to be global on second run that was local on first run. Always declare `local`/`global` explicitly in production code.

## Data Types

### Numbers

```maxscript
42              -- Integer
3.14            -- Float
1.0e-6          -- Float (scientific)
0xFF            -- Integer (hex)
1.0d5           -- Double
100L            -- Integer64
```

Integer division truncates: `10 / 20 --> 0`. Mix in a float to get float division: `10.0 / 20 --> 0.5`.

Integer range: -2,147,483,648 to 2,147,483,647 (overflow is silent!). Float precision: ~6-7 significant digits.

```maxscript
abs -5          --> 5
mod 10 3        --> 1.0  (always float)
ceil 2.3        --> 3.0  (always float)
floor 2.7       --> 2.0  (always float)
pow 2 10        --> 1024.0
sqrt 16         --> 4.0
sin 90          --> 1.0  (degrees, not radians!)
random 1 100    --> random int between 1-100 inclusive
random 1.0 10.0 --> random float
bit.and 0xFF 0x0F  --> 15
bit.shift 1 4      --> 16 (left shift)
bit.shift 16 -4    --> 1  (right shift)
```

### Strings

```maxscript
s = "hello"
s.count             --> 5
s[1]                --> "h"  (1-indexed!)
s[2] = "E"          -- mutates in place
"foo" + "bar"       --> "foobar"
"AB" == "ab"        --> false  (string == is CASE-SENSITIVE)
stricmp "AB" "ab"   --> 0      (case-insensitive compare)
```

Escape sequences: `\"` `\n` `\r` `\t` `\\` `\%` `\xNN`

Verbatim strings (no escape processing):
```maxscript
path = @"C:\Users\temp\file.max"
```

Key string methods:
```maxscript
findString "hello world" "world"    --> 7
substring "abcdef" 2 3              --> "bcd"
substring "abcdef" 3 -1             --> "cdef"  (-1 = rest)
replace "abcdef" 3 2 "XY"          --> "abXYef"
filterString "a,b,,c" ","           --> #("a","b","c")
filterString "a,b,,c" "," splitEmptyTokens:true --> #("a","b","","c")
matchPattern "test1" pattern:"test?" --> true   (case-insensitive by default)
substituteString "foo bar" "bar" "baz" --> "foo baz"
toUpper "abc"                       --> "ABC"
toLower "ABC"                       --> "abc"
append s " world"                   -- mutates s in-place (memory efficient)
execute "2 + 2"                     --> 4  (scope is GLOBAL, not caller's scope)
```

### Names (Symbols)

```maxscript
#foo                    -- name literal
"hello" as name         --> #hello
#'FFD 4x4x4'           -- quoted name (allows spaces/special chars)
-- Underscore replaces space: #FFD_4x4x4 == #'FFD 4x4x4'
-- Name comparison is case-insensitive
```

### Conversion

```maxscript
42 as float        --> 42.0
3.7 as integer     --> 4
42 as string       --> "42"
"123" as integer   --> 123
"hello" as name    --> #hello
#foo as string     --> "foo"
```

## Operators & Precedence

Highest to lowest:

| Precedence | Operators |
|---|---|
| 1 (highest) | `operand`, unary `-` |
| 2 | function call |
| 3 | `as` (type conversion) |
| 4 | `^` (right-associative) |
| 5 | `*` `/` |
| 6 | `+` `-` |
| 7 | `==` `!=` `>` `<` `>=` `<=` |
| 8 | `not` |
| 9 | `and` |
| 10 (lowest) | `or` |

Compound assignment: `+=` `-=` `*=` `/=`

Logical operators: `and`, `or`, `not` (keywords, not symbols)

## Control Flow

### If

```maxscript
-- then/else form (yields a value)
x = if a > b then a else b

-- do form (no else, returns undefined if false; preferred in Listener)
if a > b do print a

-- Multi-line
if a > b then (
    print a
    doSomething()
) else (
    print b
)
```

### For Loop

```maxscript
-- Numeric range
for i = 1 to 10 do print i
for i = 1 to 10 by 2 do print i

-- Collection iteration
for obj in selection do print obj.name
for obj in $box* do print obj         -- path name patterns

-- Collect (builds and returns an array)
result = for i = 1 to 10 collect i * 2
--> #(2, 4, 6, 8, 10, 12, 14, 16, 18, 20)

-- Where filter
big = for obj in $* where obj.height > 50 collect obj

-- While clause (fast early termination, preferred over exit)
for i = 1 to 1000 while (someCondition) do doWork i

-- Index variables (3ds Max 2021+)
for val, idx, filtIdx = 1 to 20 where (mod val 5 == 0) do
    format "val:% idx:% filtIdx:%\n" val idx filtIdx

-- dontCollect: skip collecting an item without where
for i = 1 to 10 collect (if mod i 2 == 0 then i else dontCollect)
```

**Gotcha**: the `for` loop variable is always local to the loop, even if a same-named variable exists outside.

### While / Do-While

```maxscript
while x > 0 do ( print x; x -= 1 )

do (
    x = getNext()
) while x != undefined
```

### Case

```maxscript
-- With test value
result = case val of (
    1: "one"
    2: "two"
    3: (print "three"; "three")
    default: "other"
)

-- Without test value (boolean labels)
case of (
    (a > b): print "a wins"
    (b > c): print "b wins"
    default: print "c wins"
)
```

### Loop Control

```maxscript
-- exit (WARNING: very slow, uses try/catch internally!)
for i = 1 to 100 do (
    if badCondition do exit
)
-- exit with value
result = for i = 1 to 100 do (
    if found do exit with i
)

-- continue (skip iteration)
for i = 1 to 10 do (
    if i == 5 do continue
    print i  -- prints 1-4, 6-10
)

-- In collect loops, continue skips collecting that element
for i = 1 to 10 collect (if i == 5 do continue; i) --> #(1,2,3,4,6,7,8,9,10)
```

**Performance**: prefer the `while` clause on `for` loops over `exit`. `exit` and `continue` are slow.

### Return

```maxscript
fn findItem arr val = (
    for i = 1 to arr.count do
        if arr[i] == val do return i
    return 0  -- not found
)
```

`return` is very slow (uses exception internally). Prefer structuring code so the last expression is the return value.

## Block Expressions

```maxscript
-- Parentheses group expressions; value = last expression
z = (
    a = computeX()
    b = computeY()
    a + b          -- this is the block's return value
)
```

## Functions

```maxscript
-- Basic function
fn add a b = a + b

-- Equivalent long form
function add a b = a + b

-- Keyword parameters with defaults
fn makeBox w:10 h:20 d:30 = box width:w height:h length:d

-- Unsupplied keyword detection
fn greet name greeting: = (
    if greeting == unsupplied then
        format "Hello, %!\n" name
    else
        format "%, %!\n" greeting name
)

-- Multi-line body
fn process data scale:1.0 = (
    local result = #()
    for item in data do
        append result (item * scale)
    result  -- last expression = return value
)

-- Mapped function (auto-maps over collections)
mapped fn colorize obj clr:(color 255 0 0) = obj.wireColor = clr
colorize $box*  -- applies to all objects matching $box*

-- Functions are first-class values
fns = #(sin, cos, tan)
result = fns[1] 45  --> 0.707107

-- Recursive
fn factorial n = if n <= 1 then 1 else n * factorial (n - 1)
```

**Parameter passing**: by reference semantics for compound values. Assigning to a parameter variable is local, but mutating a compound value's properties affects the caller's copy. Use `copy` to avoid side effects.

## Structures

```maxscript
struct Person (
    name,
    age,
    height,
    greeting = "Hello",                   -- default value
    fn introduce = (
        format "%! I'm %, age %.\n" greeting name age
    )
)

-- Construction (positional or keyword)
p1 = Person "Alice" 30 165
p2 = Person name:"Bob" age:25
p3 = Person "Charlie" height:180          -- mix positional + keyword

-- Access
p1.name         --> "Alice"
p2.age = 26     -- set property
p1.introduce()  -- call member function

-- Introspection
classOf p1              --> Person
getPropNames p1         --> #(#name, #age, #height, #greeting, #introduce)
getProperty p1 #name    --> "Alice"

-- Copy (shallow by default)
p4 = copy p1
```

## Arrays

```maxscript
a = #(1, 2, 3)          -- literal
a = #()                  -- empty
a[1]                     --> 1  (1-INDEXED!)
a[4] = 99               -- auto-grows: #(1, 2, 3, 99)
a.count                  --> 4

-- Methods
append a 5               -- add to end, returns array
appendIfUnique a 5       -- add only if not present
insertItem 10 a 2        -- insert 10 at index 2
deleteItem a 3           -- remove element at index 3
findItem a 99            --> index or 0 if not found
sort a                   -- in-place ascending sort
join a #(6, 7)           -- append all elements of second array to first
makeUniqueArray #(1,2,2,3) --> #(1,2,3)

-- Collect pattern (very common)
positions = for obj in selection collect obj.pos

-- Custom sort
fn byHeight v1 v2 = (
    d = v1.height - v2.height
    if d < 0 then -1 else if d > 0 then 1 else 0
)
qsort myArray byHeight

-- Copy behavior (CRITICAL GOTCHA)
b = a       -- b references SAME array. b[1]=99 changes a too!
b = copy a #nomap   -- shallow copy (sub-arrays still shared)
b = deepCopy a      -- full recursive copy

-- Comparison (GOTCHA)
#(1,2,3) == #(1,2,3)       --> false (reference equality only!)
deepEqual #(1,2,3) #(1,2,3) --> true  (element-wise)

-- Convert to array
sel = selection as array
```

## Dictionaries

Available since 3ds Max 2017.1.

```maxscript
-- Constructors (key type: #name, #string, or #integer)
d = Dictionary #name          -- empty, name-keyed
d = Dictionary #("one",1) #("two",2)   -- string-keyed from arrays
d = Dictionary foo:1 bar:2              -- name-keyed from keywords

-- Access
d[#foo]              --> 1
d[#baz] = 42        -- add/set entry

-- Methods
HasDictValue d #foo          --> true
GetDictValue d #foo          --> 1
PutDictValue d #new 99       -- add entry
RemoveDictValue d #foo       -- remove entry
d.count                      --> number of entries
d.keys                       --> array of keys
d.type                       --> #name

-- Iteration
for pair in d do format "key:% val:%\n" pair.v1 pair.v2

-- Copy
d2 = copy d           -- shallow
d2 = deepCopy d       -- deep

-- Comparison
deepEqual d1 d2       --> true/false (== does NOT work on dicts)
```

## Error Handling

```maxscript
try (
    riskyOperation()
) catch (
    msg = getCurrentException()
    format "Error: %\n" msg
)

-- Re-throw (only inside catch)
try ( something() ) catch ( cleanup(); throw() )

-- Custom throw
throw "Something went wrong"
throw "Bad value" myValue

-- Error source info (inside catch, 3ds Max 2018+)
getErrorSourceFileName()
getErrorSourceFileLine()
```

## Common Gotchas & Quirks

1. **Arrays are 1-indexed**. `findItem` returns 0 (not -1) on failure.
2. **String `==` is case-sensitive**, but variable names and name values are case-insensitive.
3. **Integer division truncates**: `7/2 --> 3`. Use `7.0/2` for `3.5`.
4. **Trig functions use degrees**, not radians.
5. **`exit` and `return` are slow** (implemented via exceptions). Use `while` clause or structure code to avoid.
6. **Array/struct assignment is by reference**: `b = a` does NOT copy.
7. **`==` on arrays/dicts checks reference identity**, not contents. Use `deepEqual`.
8. **Implicit variable scoping is tricky**: always use `local`/`global` declarations in scripts.
9. **`\` in strings is an escape char**: use `\\` or verbatim `@"C:\path"` for file paths.
10. **`mod` always returns float**, even with integer inputs.
11. **`^` is exponentiation**, not XOR. Use `bit.xor` for bitwise XOR.
12. **`copy` on arrays is mapped by default** (copies elements, not array). Use `copy arr #nomap` for shallow array copy, or `deepCopy` for full recursive copy.
13. **`if...then` without `else`** in the Listener waits for more input. Use `if...do` for no-else conditionals in interactive mode.
14. **No `break` keyword**: use `exit` (slow) or `while` clause (fast) to leave loops early.
15. **Struct `copy` is shallow**: compound member values are shared between original and copy.

## Reserved Keywords

```
about and animate as at attributes by case catch collect continue
coordsys do else exit fn for from function global if in local
macroscript mapped max not of off on or parameters persistent
plugin rcmenu return rollout set struct then throw to tool try
undo utility when where while with
```
