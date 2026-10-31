#!/usr/bin/env python3
"""Import-closure, vendor-provenance, dynamic-import and workflow-pin gate (rules mindep, liccmp, secvde, secsup).

Every import statement in this tree's Python resolves, by the search roots of the program that imports it, to the
standard library of the declared floor version, to a module file in this repository, or to a vendored package with
recorded provenance and licence; every reference to the dynamic-import machinery (an occurrence of a watched name
or import-system member) is one a reviewed row lists exactly, by its scope, text and ordinal there (no position),
with exactly one exemption: a reference in a def or lambda body itself whose name Python's own compile-time
scoping proves a plain local of that function, bound by no import statement and by no comprehension or generator
expression target there, needs no row, a binding rule, not a value proof (what such a local can still hold is
disclosed under dynamic-import);
every action reference in .github/workflows (and in every action.yml or action.yaml in the tree) has the form of a
full commit SHA; and every container image there is pinned by digest. A third-party or invented package name
therefore cannot enter through an import statement in a scanned file or as a string-literal name an importer call
of LITERAL_IMPORTERS passes in a form the gate reads (below), a watched-name occurrence cannot enter unless a
reviewed row lists that exact node or it is the one exemption above (a proven plain def or lambda local, read
in that function's own body, its residual disclosed under dynamic-import), and
vendored Python cannot enter without a recognised SPDX identifier whose licence text it carries.

Search roots. A file's search roots are its own directory (Python puts a script's directory first on
sys.path; this tree's programs insert it explicitly, since python3 -I leaves it out), except when that
directory is a package (it holds __init__.py, so it is not itself on sys.path); each extra root that
IMPORT_SEARCH_ROOTS records for that file; and, for a file under a _vendor directory, that _vendor
directory alone (a vendored package is imported by its own name from there). A module is present in a root
only as NAME.py or a regular package NAME/__init__.py among the walked files: a namespace directory never
resolves an absolute import, since an installed regular package of the same name precedes it.

Rules (each self-test vector is caught by exactly its rule, and passes with that rule removed):
  import-closure     an absolute import statement (an import or from statement anywhere in the file, lazy, guarded or
                     under TYPE_CHECKING included; and a string-literal name at an importer call, see dynamic-import)
                     resolves by its first dotted component to a module present in one of the file's search roots, or to
                     the standard library. A name present in none of them is a finding: a third-party or invented name.
                     A same-named file outside the file's search roots never resolves it. A relative import must name a
                     module or package that exists under its base directory (or the base must be a package). A value the
                     gate reads to decide an importer call is a finding when it cannot read it with certainty, never a
                     pass: an importer call passing a starred or double-starred argument; a string literal at its
                     module-name parameter that is not a dotted name of identifiers once the callee's own reading is
                     applied ('x.', 'x..y', 'x.0' and 'x-y' among them), or an f-string there with no placeholder; a
                     help request that, once stripped, pydoc's Helper reads as a listing rather than as a module name
                     (HELP_LISTINGS: keywords, symbols, topics, and modules, which imports every package it finds),
                     whatever module of that name the tree holds (a modules request with a search word,
                     help('modules x'), which imports them too, is refused as not a dotted name);
                     beside a string-literal name, a level that is not an integer literal (-0, +0, a computed level);
                     and, at the callee after the .__call__ normalisation, a call of a getattr passing a starred or
                     double-starred argument or a keyword, or a call through a getattr name or a subscript key written
                     as an f-string with no placeholder. A computed module name (any expression other than a literal,
                     an f-string with a placeholder among them) is not read: its site is reviewed (DISCLOSED RESIDUAL).
                     Each IMPORT_SEARCH_ROOTS row is held live: its file is scanned, its root lies beyond the file's own
                     directory, the file makes a sys.path.insert or sys.path.append call whose argument expression,
                     read in the closed set of shapes the Search-roots limitation lists, resolves to the row's root,
                     and at least one of its imports resolves through that root; a row that fails any of these is a
                     finding, and an insertion argument outside those shapes, where no read insertion resolves the
                     row's root, is refused (cannot evaluate), never a silent pass.
  dynamic-import     dynamic imports are not resolved (a literal name excepted, below); a reference to the
                     dynamic-import machinery is refused unless a reviewed row lists that exact node. The watched names
                     are DYNAMIC_IMPORT_NAMES (__import__, __builtins__, importlib, imp, runpy, zipimport, pkgutil,
                     pydoc, the loader, finder and runner names reachable from them, the import-system hooks __path__,
                     meta_path and path_hooks, and the builtin help and its module _sitebuiltins) and every undotted
                     name sys.modules gives a module of IMPORT_SYSTEM_MODULES (the frozen _frozen_importlib and
                     _frozen_importlib_external, and _imp, among them); the members are the last component of every
                     dotted such name and every function, class or module defined in those modules (_bootstrap,
                     _gcd_import, _find_and_load, BuiltinImporter, SourceLoader among them), less
                     INERT_IMPORT_SYSTEM_MEMBERS. Both sets are read from the modules on every run, never from a hand
                     list. A site is a reference node holding a watched name as an identifier anywhere in the syntax
                     tree (a name read or bound, an attribute, an imported module or alias, a definition, parameter or
                     keyword name; help excepted as a keyword argument's name, KEYWORD_EXEMPT_NAMES, and a name
                     read or bound as a plain local excepted, next), a member as an
                     attribute name, or either as a dotted component of a string or bytes literal that is a dotted
                     identifier chain (getattr(builtins, "__import__"), sys.modules["_frozen_importlib"]). A name
                     read or bound as a scoped variable (a read, an assignment, deletion, loop, with, except,
                     walrus or match target, or a parameter) is no site only under one exemption, of three
                     conditions. First, the reference sits in a def or lambda body itself: not in any
                     comprehension or generator expression, parameter or return annotation, type-parameter
                     scope, type alias's value or class body, at any depth (only another def or lambda below
                     one of those starts a body of its own). Second, Python's own compile-time scoping (the
                     standard library symtable over the same source; the walk that puts each reference in its
                     syntactic position is this gate's own, and the site vectors pin, shape by shape, that a
                     position it marks as a body resolves in that function's own table) reports the name in
                     that function's own table as a plain local bound by no import statement: a parameter, or
                     a name a statement of the body binds, not global, nonlocal or free. Third, no
                     comprehension or generator expression anywhere in that function binds the name as a
                     for-target: a conservative keep, not a condition the floor version's scoping needs
                     (an inlined comprehension's target leaks nowhere, so a body read outside every
                     comprehension still resolves to the def's own binding, and where only a comprehension
                     binds the name the table reads the body reference as global, kept by the second
                     condition); it can only over-report, and its vectors pin the kept sites. The exemption is a
                     binding rule, not a value proof: the binding is
                     local and fixed at compile time, so the name resolves to that variable and never to a
                     module-level or builtin binding; what the variable holds is not proven, and a local that
                     import machinery reaches another way is the accepted residual, watched only where the gate
                     watches that way (help bound as a parameter and then read or called is reported nowhere, and
                     so is a local bound from eval or another unwatched dynamic call; an import statement, an
                     importer call and every watched-name occurrence stay watched as this rule reads them).
                     Everything else stays a site, and no other scope shape is resolved at all. The exemption
                     no longer covers, and this gate therefore reports: a free (closure) read, however plainly
                     an enclosing def binds the name; every reference in a list, set or dict comprehension or
                     a generator expression (targets, body, conditions and every iterable, the first
                     included); every parameter and return annotation and a type alias's value (the floor
                     version evaluates them lazily, in annotation scopes); everything a type-parameter
                     scope holds (a type parameter list with its bounds, constraints and defaults, and a
                     generic class's bases and keywords: the floor version reads them against the
                     definition's type-parameter scope, where a class namespace can intervene); and a
                     generic def's defaults, kept as a stated conservative policy and not by the floor
                     version's scoping: it evaluates a def's defaults in the enclosing scope, generic or
                     not (def f[T](x=T) raises NameError, and def f[help](x=help) reads the enclosing
                     help, never the type parameter), so this keep can only over-report, and its own
                     vectors pin the kept sites.
                     A site as before: a module-scope or class-body binding (names there are looked
                     up at run time and can fall through to the globals or the builtins, so a conditional,
                     deleted or later binding proves nothing), a comprehension target, a global or nonlocal
                     declared name, a local an import
                     statement binds (import imp in the scope that reads imp), a name any scope imports under a
                     global or nonlocal declaration, a reference to no binding in the file (the builtin
                     __import__), every reference in a file with a * import, every ambiguity (two lambdas on one
                     line, a def named genexpr beside a generator expression on its line, a def named
                     listcomp beside a class-body annotation's comprehension on its line, a source symtable
                     cannot read), and a dunder
                     watched name (__import__, __builtins__, __loader__, __path__, __spec__) wherever it is
                     bound, since binding one rebinds the import machinery itself; and an import statement, a
                     def's or class's header, an attribute, a keyword's name, a type parameter and a literal are
                     never resolved, so each stays a site as before. The reference
                     node is an alias's import statement, a def or class by its header, a parameter, keyword or literal,
                     or the expression climbed through the attribute access, call and subscript the name is the target
                     of. A site is allowed only when a DYNAMIC_IMPORT_SITES row (file, enclosing scope, references,
                     reason) names its file and its enclosing scope and lists its reference exactly: its node's
                     unparsed text and its ordinal among the nodes of that text in that scope (1 for the first in
                     source order), as #ORDINAL: TEXT (ast.unparse's text, so layout, comments and quote style aside;
                     a def, class or except clause reads as def, class or except as and its name). No line or column
                     is part of it, so lines added or removed elsewhere in the file leave a row valid. The enclosing
                     scope is one lexical scope instance, <module> at module level, else the defs, classes and lambdas
                     around it joined by dots, each by its own name (a def's name, class and a class's name, or
                     <lambda>) followed by #K/N when its own enclosing scope holds N > 1 scopes of that name, K its
                     ordinal in source order among them (sibling lambdas in f, or two defs of one name under an if and
                     its else, are f.<lambda>#1/2 and f.<lambda>#2/2, or f#1/2 and f#2/2). A reference moved from one
                     such scope into its sibling is a finding, and adding or removing a sibling of that name changes N
                     and so the identity of every sibling of that name (each row there goes stale); only an edit that
                     keeps N (siblings reordered, or as many added as removed) can leave a moved reference on the
                     #K/N it held (DISCLOSED RESIDUAL). A comprehension or generator expression is not a scope here:
                     what it holds is listed under the scope around it (and nothing it holds is ever exempt,
                     above). An identity two scopes share would tell neither
                     apart, so it exits 2 (cannot evaluate) and is never merged. A definition's name, decorators,
                     defaults, annotations, bases, keywords and type parameters (their bounds, constraints and
                     defaults) belong to the scope that runs the definition, and only its parameters and body to
                     its own scope; a type alias is not a scope here, so its value belongs to the scope that runs
                     the type statement. An annotation, a type parameter's bound, constraints and default, and a
                     type alias's value belong there although the floor version evaluates each only when it is
                     read, in an annotation scope (one __annotate__ scope holds all the annotations of one
                     definition; under from __future__ import annotations an annotation reads as a string, not
                     evaluated even then), and a generic class's bases and keywords belong there although the
                     floor version evaluates them at once in the annotation scope of the class's type parameters
                     (DISCLOSED RESIDUAL). The annotation of an annotated assignment directly in a function body,
                     or of one whose target is not a simple name (C.x: T, d[k]: T, (x): T), is never evaluated
                     (one in a class body or a def nested in that function is), and a watched name there is
                     read like any other expression of the scope that holds the statement. Scope identities and ordinals give
                     each node its own key (file, scope, ordinal and text); a key that more than one node held would
                     allow none of them, listed or not (a backstop against a regression in that numbering: no parsed
                     tree reaches it, and the self-test injects it). Each row is held live: it
                     gives a reason, lists at least one reference of that form, repeats none, and every reference it
                     lists occurs there; a row that fails any of these is a finding. An importer call is a call that
                     reaches a LITERAL_IMPORTERS callee (__import__, import_module, _gcd_import, find_spec,
                     resolve_name, run_module, run_path, locate, safeimport, help and the import-system entry points
                     _find_and_load, iter_importers, _run_module_as_main and _get_module_details; doc, render_doc,
                     resolve and writedoc only through pydoc) by name, by attribute, by getattr with two or three
                     arguments, a literal name and no starred argument, by a subscript with a literal key (pydoc reached
                     through pydoc, vars(pydoc) or pydoc.__dict__), or, for help, by calling what pydoc.Helper(),
                     _Helper() or type(help) makes; a trailing .__call__ is transparent, at the call and at each
                     getattr, type, vars or helper-class callee inside it (getattr.__call__ reads as getattr). Its
                     module name is read by the callee's real signature: the first argument, or the keyword of that
                     parameter (name, fullname, mod_name, path, request or thing), and the level by its position or its
                     keyword where the callee takes one; help strips surrounding whitespace, resolve_name reads the
                     module before a module:object colon, and the relative importers import_module, find_spec and
                     resolve_name leave a name beginning with a dot unresolved: import_module and find_spec read it as
                     relative to their package argument, pkgutil.resolve_name has no package argument and refuses it
                     (ValueError), and importlib.util.resolve_name computes a name from it without importing anything. A
                     string-literal absolute name read there at level 0 is also an import record for import-closure,
                     whether its site is allowlisted or not, and a literal there that the gate cannot read is a finding
                     (import-closure, above). A relative request (an integer level above 0, or a relative importer's
                     name beginning with a dot) is not resolved, and run_path's argument is a file path and is not
                     resolved. No alias is resolved: an alias is itself a site.
  relative-escape    a relative import whose base directory lies outside the repository root, or, for a
                     vendored file, outside its own vendored package root.
  vendor-provenance  each _vendor directory: every NAME.provenance.toml there records [package] name,
                     version and purl (a pkg: URL ending in /NAME@VERSION), [license] spdx and vendored_path,
                     and [vendored_tree] root (one directory directly inside that _vendor directory, named for the
                     package: its name, case aside, with - and . read as _); spdx is one identifier from
                     SPDX_LICENCE_MARKERS, or several joined by AND or by OR (not both); the licence file lies
                     under that _vendor directory, is listed in the manifest, and its text contains every marker
                     sentence of every identifier named (case and whitespace aside); the sibling
                     NAME.manifest.sha256 exists, and every row's file exists with the recorded sha256; every file
                     under the root is listed; every .py file under the _vendor directory lies under a recorded
                     root; and an import that resolves into a _vendor directory resolves to a recorded root.
  action-pin         every `uses:` value is a local ./PATH that stays inside the repository once normalised, a
                     docker:// image pinned by @sha256:<64 hex>, or OWNER/REPO[/PATH]@<40 lowercase hex>; every
                     `image:` or `container:` scalar is pinned by @sha256:<64 hex> (a docker:// prefix allowed)
                     or, in an action file only, names a walked file relative to that file's directory (the
                     local-file exception: a Dockerfile the action builds). A tag, a branch, a short SHA or an
                     expression is a finding.
  disposition        each VENDORED_OPTIONAL_IMPORTS row (a third-party import inside byte-exact vendored code
                     that this tree does not edit) is live and true: the file is under a recorded vendored
                     root, the import occurs there, and either every occurrence sits in the body of a try
                     whose handler is bare or names ImportError, ModuleNotFoundError, Exception or
                     BaseException (guarded-optional), or no import statement anywhere else in the tree reaches
                     that module, a from-import's names included (unreached-module). A row that no longer
                     matches, or whose condition fails, is a finding. A dispositioned import is printed on
                     every run, never hidden.

Workflow and action files are read by a line classifier for a subset of YAML 1.2, not a full parser. A line is
accepted only as: blank; a comment; a block mapping key (plain, or quoted with no escape and no quote inside)
followed by nothing, a comment, a plain or quoted scalar, a flow sequence of scalars none of which holds a
colon, or a block scalar header (| or >, with an optional chomping indicator); a block sequence item (one dash)
holding such a key, such a scalar or flow sequence, or nothing; or block scalar content, whose extent follows
YAML's indentation rule. A key named uses (in any case) is checked wherever it sits, and so is a key named image
or container. Any other line cannot be classified and exits 2, so an unpinned action cannot hide behind syntax
the classifier does not read: an escaped, alias or complex key, an anchor, alias or tag, a flow mapping, a
multi-line flow or plain scalar (a line indented deeper than the key, or the dash, of the preceding line when
that line's value is an inline scalar, even a line that starts with a dash, since YAML reads it as a
continuation of that scalar), a document marker or directive, a second dash on one line, a block scalar as a
sequence item, a uses or image key without an inline scalar, a container key holding a block scalar or a flow
sequence, and a scalar on a structural line holding the word uses followed by a colon (a disclosed
over-rejection). A bare CR, a C0 or C1 control character other than tab, LF and the CR of a CRLF, U+2028, U+2029
and U+FEFF anywhere in the file also exit 2, since a YAML 1.1 reader breaks lines at some of them.

Fail closed (exit 2, cannot evaluate): the standard-library set is read from the interpreter
(sys.stdlib_module_names), never from a hand list, and the gate refuses to run if that attribute is absent,
empty or not a frozenset of nonempty names; it also refuses unless the interpreter's MAJOR.MINOR equals the
floor declared in .aiqt/core/python-floor.toml, since that set belongs to the running version. Also exit 2:
a .py file, workflow or action file, provenance record, manifest or licence text that is not a regular file
(a symlink, a FIFO), is unreadable, is not UTF-8 where text is required, or does not parse; a directory that
cannot be listed; a symlinked directory in the walk; a manifest row that is not one sha256sum row (64 hex,
two spaces, a path not listed before); an absent workflow directory; an absolute import whose first
component is present in two of the file's search roots, or in one of them and the standard library
(ambiguous: which file it reaches depends on sys.path order, which the gate does not model); and a workflow
or action line outside the accepted YAML subset (above).

  check_import_closure.py              scan this repository
  check_import_closure.py --self-test  fixture trees: every rule vector is red with its rule and green
                                       without it (one per watched name and import-system member, and one per
                                       importer, among them), every green vector exits 0, every cannot-evaluate
                                       vector exits 2 by its own refusal (a text of that refusal's message; each
                                       fails on a refusal not its own, and where the gate words one refusal
                                       alike for a class of inputs that text pins the class and the line, not
                                       the check that refused), every site vector finds exactly the
                                       sites (scope and reference) it names, every edit vector (rows the gate
                                       under test writes for one source, the fixture's own rows among them)
                                       passes that source unchanged and gives exactly its findings, each in the
                                       edited file, on the edited source, every mutant in MUTANTS (one check
                                       removed, with the backstop that would refuse first where one is named, or
                                       a line or a column put into the key) fails the vector it names by that
                                       vector's own failure (never by an exception that propagates from the
                                       mutant's load or run, SystemExit and KeyboardInterrupt among them; one
                                       raised in another thread or a destructor goes to its hook, outside this
                                       guarantee, as are os._exit, a fatal signal and a hang; by a
                                       refusal only where MUTANTS marks the mutant as caught by its backstop,
                                       and then only by that backstop's refusal; an edit vector only when the
                                       unchanged source passes under that mutant and every finding lies in the
                                       edited file), each run in _UNCREDITED_RUNS (an exception raised writing
                                       an edit vector's rows, scanning its unchanged source or scanning its
                                       edited source, one raised on a rule vector, a SystemExit(0) raised
                                       loading a mutant, scanning an edit vector's unchanged source or on a rule
                                       vector, a KeyboardInterrupt a mutant raises, a refusal no backstop is
                                       named for on a rule vector, a green vector, a cannot-evaluate vector (a
                                       refusal not its own) or a rule vector's rerun with its rule removed, and
                                       a backstop named for a refusal not its own or for a failure that is no
                                       refusal) credits no mutant, removing any one watched
                                       name, member or importer fails its vector, and the live tree passes. It
                                       exits 0 only by returning after its PASS line: a SystemExit that escapes
                                       it exits 1 (though a mutant that ends the process itself is not
                                       contained: os._exit(0) would end it with exit 0 and no PASS line, and a
                                       fatal signal or a hang stops or stalls it), and the user's own SIGINT
                                       (under Python's default handler) is not contained: one delivered in a
                                       mutant's run propagates, and the handler is restored on every path. The
                                       checks of those two run under any SIGINT handler this process can read
                                       and put back, also in a child launched with SIGINT ignored; where it
                                       cannot (a handler set outside Python, a thread other than the main one,
                                       or a restore that raises) the check cannot evaluate, which fails the
                                       self-test, never a skip
  check_import_closure.py --interrupt-leg
                                       those two checks alone (the child the self-test launches)

Exit convention: 0 clean; 1 a finding; 2 usage or cannot-evaluate.

DISCLOSED RESIDUAL. Dynamic imports: an allowlisted site is reviewed, not evaluated; the gate resolves only a
string-literal absolute name an importer call passes in a form it reads, and refuses a literal it cannot read (above),
so a computed name (a variable, a concatenation, a call, a subscript, an f-string with a placeholder) is never judged by
import-closure, and the unreached-module condition is static (an allowlisted site can still reach the module at runtime:
the vendored marko.helpers, an allowlisted site, loads an extension by a computed name). A relative request is not
resolved either: what it imports depends on its package argument or the caller's globals, which the gate does not read
(import_module('.x', 'pkg') and find_spec('.x', 'pkg') import pkg, and __import__('x', g, None, (), 1) imports from the
package g names), so its site is reviewed. A row allows one node, not what becomes of its value: a listed reference that
is not called in place (an importer returned, stored, passed or bound by an alias, or a lambda holding a call) lets
whatever later calls that value import without a site of its own, so such a row is reviewed for where the value goes,
and a finding says when a reference is not a call. A row names a reference by its scope (one lexical scope instance, a
same-named sibling told apart by #K/N, its ordinal among them and their count, above), its unparsed text and its ordinal
among the references of that text in that scope, not by its line or column. So an edit to its own text, a move into
another scope (a sibling lambda or a same-named def among them), a rename of a def or class around it or a change of one
into the other (over-rejections: its text is unchanged), a same-named sibling of a scope around it added or removed (an
over-rejection: N changes, so every row under a sibling of that name goes stale until it is re-reviewed) or reordered
(an over-rejection wherever that shifts that scope's #K), and a new reference of that text ahead of it in its scope (the
ordinals shift, and one is left unlisted) each make a row stale or a reference a finding until the row is rewritten (the
re-review the binding asks for); but a move within its scope (onto another line, into or out of a branch, a loop or a
try, between an annotation and a default of one definition, from a type alias's value or a type parameter's bound
into a statement beside it, between a generic class's bases or keywords and a statement beside the class, or into,
out of or between comprehensions and generator expressions), an edit elsewhere in
its scope (rebinding a name it is passed, say), a change of layout, comments or quote
style that ast.unparse does not keep, and an exchange among the references of one text in one scope (one removed and
another of that text added there in one change, or two swapped) leave every row valid, so none of them is re-reviewed: a
row reviews a node's text in its scope, not its place there. A comprehension or generator expression is not a scope
here, though Python gives each a nested scope of its own (on the floor version a list, set or dict comprehension is
inlined into the code of the function, class body or module around it and a generator expression is not, and both
keep their loop variables to themselves): what it holds is read in the def, class, lambda or module around it, so a
move between two of them in one def is a move within that def, which is wider than a move within one of Python's
scopes. An annotation, a type parameter's bound, constraints and default, and a type alias's value, which the floor
version evaluates only when it is read, in an annotation scope (one __annotate__ scope holds all the annotations of one
definition; under from __future__ import annotations an annotation reads as a string, not evaluated even then), and a
generic class's bases and keywords, which it evaluates at once in the annotation scope of the class's type
parameters, are read in the scope that runs the definition or the type statement, as a default is: a move between one
of them and the code beside it is a move within that scope, which is again wider than a move within one of Python's
scopes (a move between an annotation and a default of one definition among them: the annotation runs in __annotate__,
the default in the scope that runs the definition). The annotation of an annotated assignment directly in a function
body, or of one whose target is not a simple name (C.x: T, d[k]: T, (x): T), is never evaluated (one in a class body or
a def nested in that function is), yet a watched name there is read like any other expression of the scope that holds
the statement: a site, or in a def body the exemption's residual, though the name is never evaluated at all. A scope is
known by its name, its ordinal among same-named siblings and their count, not by its place or its contents, so an edit
that keeps the count of same-named siblings in a scope (siblings reordered or exchanged whole, or as many removed as
added in one change) reads the sibling now at each #K/N as the one that was there: a reference that ends in the sibling
now at the #K/N it held is not re-reviewed, whichever sibling it was written in (one sibling removed and another added
ahead of it, say, with the reference moved into the sibling that takes its ordinal).
A listed string literal, or one inside an f-string, climbs into the call it is directly an argument of, or the
subscript it is directly the key of, before the row records it, so turning that call or subscript into a called importer
is a finding. An expression built from a listed literal ('__import__'.strip(), a tuple holding it) is a value passed on:
the call around that expression is not climbed into, so it is part of the residual, with the introspection-only routes
below and whatever a listed reference's value is passed to or stored as. Importer calls: the reader covers the callee
names LITERAL_IMPORTERS lists, reached by the spellings above; it does not read every way to reach an import function.
Not read, so the name such a call passes is not resolved (the call is reviewed only as a site, where it names a watched
name or member, and through an alias only at the import that binds it): an import function the table does not list
(importlib._bootstrap._find_and_load_unlocked, pkgutil.get_data, the anchor functions of importlib.resources such as
files and read_text, pydoc._url_handler, and their kin); a pydoc helper reached through an owner spelled other than
pydoc, vars(pydoc) or pydoc.__dict__ (__import__('pydoc').doc, sys.modules['pydoc'].doc,
import_module('pydoc').render_doc); a help instance made other than by pydoc.Helper(), _Helper() or type(help)
(help.__class__()); an importer called unbound with its instance passed first (pydoc.Helper.help(h, name)), since the
module name is read at the callee's first argument or that parameter's keyword; and an importer bound to another name
and called through it (h = help), since the callee is read by its name, though the binding is itself a site when it
names a watched name. doc, render_doc, resolve and writedoc are read only through pydoc (this tree defines functions of
those names), so a call of one of those four after a from-import of pydoc is reviewed at that import (a site, since it
names pydoc) and not read; safeimport has no such owner, so an unqualified safeimport call is read. Over-rejections,
disclosed: an importer's name on an unrelated object (o.locate, o.help, o.find_spec with a third-party literal) is read
as an importer; importlib.util.resolve_name computes a name without importing it and is read as an importer;
iter_importers is read as an import of the name it is given, though it imports only the parent package of a dotted name,
and only once iterated (iter_importers('requests') imports nothing); help with a keyword, topic or symbol word that is a
dotted name (help('if'), help('MODULES'), help('j')) reads the word as a module name, a finding unless a module of that
name resolves, though help shows that topic and imports no module of that name; help of another symbol (help('+')) is
refused as a literal it cannot read; every other string that is not a dotted name of
identifiers ('' anywhere, '.x' at __import__) is refused whether or not the interpreter would import anything for it,
and so is a level that is a literal of another type (None, 0.0); and a starred or double-starred argument is refused
even when it is a literal the gate could expand. The members are read from IMPORT_SYSTEM_MODULES, not from every
importer: the members of importlib.resources and importlib.metadata are reached only through a watched importlib
reference, and a member's name used for a local object (a def, a parameter, a plain name) is not a site. Not seen: exec,
eval or compile of a string, whose source the gate does not read as Python; a watched name assembled at runtime from
pieces (a computed string handed to getattr, vars, globals or sys.modules); an import function reached through the
object graph without naming it (a function's __globals__, a frame, the garbage collector); and other standard-library
functions that import by name as a side effect (pickle loading a class reference, logging.config factories, unittest
name loaders, xml.sax.make_parser, among others), and run_module and _run_module_as_main of a
standard-library command-line module, read as an import of that module alone, though run as __main__
(run_module with run_name='__main__', or _run_module_as_main) its command-line code imports by sys.argv
(runpy.run_module('pydoc', run_name='__main__') imports the module sys.argv names): the watched set covers
the import system's own modules and help, not every importer by name in the standard library, and of the
import system's own functions only those LITERAL_IMPORTERS lists are read as importers. A native library
loaded through ctypes, and Python source handed to a subprocess or written to a file, are outside the
surface. Search roots: the own-directory root and the IMPORT_SEARCH_ROOTS rows are the gate's
model of sys.path; a row is held live by a sys.path.insert or sys.path.append call whose argument is one of a closed
set of shapes, the ones this tree's rows write, read by spelling and never evaluated: str() around a pathlib chain;
os.path.join(os.path.dirname(os.path.abspath(__file__)), LITERAL, ...), in exactly that nesting; or a module-level
name bound to one of those, a str() there around a chain that starts at no name (a str-valued name over a chain that
starts at a name, LIBS = str(ROOT / 'lib') with ROOT a name, is not read, and neither is the insertion of a name bound
to another name). A pathlib chain starts at Path(__file__).resolve() followed by .parent (1 to 17 times) or by one
.parents[N] (N an int literal, 0 to 16), at repo_root(), or, only inside a str() that is itself the insertion
argument, at a module-level name bound to a pathlib chain, which may itself start at one more such name, whose chain
starts at no name (at most two names, so a chain that reaches a third name is not read), and continues with / and a
LITERAL any number of times. Every LITERAL is one path component, [A-Za-z0-9_.-]+ and neither . nor .., so an
absolute, empty, separator-holding or trailing-slash component never occurs, and a shape that climbs above the
repository root is not read. A name is read only where the whole file binds it exactly once, by a plain module-level
NAME = value statement with one target, and nowhere else in any form (annotated, augmented, chained or destructuring
assignment, for, with, except, match or comprehension target, walrus, del, global, nonlocal, import, def, class,
parameter or type parameter, in any scope); Path is read only where the file's one binding of it is from pathlib
import Path, os only where it is import os, and repo_root only where it is from _gen_common import repo_root
(this tree's helper, read as the repository root by its contract, not executed);
__file__ and str only where the file binds them nowhere; and a star import anywhere in the file leaves every name
unread. Every other argument (a bare string literal, an unwrapped pathlib value, another os.path call or nesting) is
refused (cannot evaluate), naming the file, the row and the argument's spelling, where no read insertion resolves the
row's root. A string literal elsewhere in the file (a fixture path, a docstring) holds no row live. The reading is of
spelling, not execution: an insertion executed conditionally, in unreached code, or only after the import it serves
still counts, a name's binding need not precede the insertion that reads it, and a binding made at runtime (through
globals(), setattr or exec) is not seen;
and the gate does not evaluate the order of insertions (it refuses a name present in two places instead), it does not model an in-code
insertion of a path outside the tree (a site-packages directory, which can then shadow an in-repo name), and it does not
see a program launched with another path (PYTHONPATH, python3 -m from another directory, or python3 -I without the
own-directory insertion, under which a sibling import fails or reaches an installed module of that name). Third-party
code committed outside a _vendor directory and reached through a recorded root counts as in-repo, without provenance;
the rows are reviewed in this file. Surface: only .py files are scanned; Python launched from another suffix, other
languages, and install commands (pip, npm) written into scripts or documentation are outside it. The walk reads the
working tree, not the git index, so an untracked local file can change a local verdict (CI checks out clean). The walk
does not enter .git, .venv, venv or node_modules at the repository root (each run names the ones present); a directory
of those names deeper in the tree is walked. A *.pyc file inside a __pycache__ directory is not read. Vendored
non-Python files outside a recorded root (data fixtures) are outside the vendor-provenance surface. Licences: the gate
holds that a vendored package names recognised SPDX identifiers and that its licence text carries each identifier's
marker sentences; it does not prove the text complete or unmodified, judge licence compatibility, check the package
against a registry, or consult vulnerability advisories. A package whose import name differs from its distribution name
cannot be recorded (the root must be named for the package). Pins: the gate checks a reference's form; it does not ask
GitHub whether a 40-hex ref names a commit rather than a branch or tag of that name, or whether the commit is
trustworthy. The local-file exception accepts an action file's image naming a walked file (a Dockerfile); the base
images that file names (its FROM lines) are not checked. A key named image or container inside an action's with: inputs
is checked too (an over-rejection). The classifier follows YAML 1.2 for its subset, and a local uses path is confined by
normalising it; GitHub's own parser and runner were not run against the vectors.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_import_closure.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import contextlib
import hashlib
import importlib.util
import io
import os
import re
import shutil
import signal
import stat
import subprocess
import symtable
import tempfile
import tomllib
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOOR_REL = ".aiqt/core/python-floor.toml"
WORKFLOWS_REL = ".github/workflows"
# Not walked, each anchored at the repository root only (a directory of the same name deeper in the tree is
# walked); every run names the ones present.
SKIPPED_ROOT_NAMES = (".git", ".venv", "venv", "node_modules")
BYTECODE_DIR_NAME = "__pycache__"
VENDOR_DIR_NAME = "_vendor"
PROVENANCE_SUFFIX = ".provenance.toml"
MANIFEST_SUFFIX = ".manifest.sha256"
ACTION_FILE_NAMES = ("action.yml", "action.yaml")
FLOOR_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)")
MANIFEST_ROW_RE = re.compile(r"(?P<digest>[0-9a-f]{64})  (?P<path>\S(?:.*\S)?)")
COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
REMOTE_ACTION_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?@(?P<ref>[^@\s]+)")
DOCKER_PINNED_RE = re.compile(r"docker://[^@\s]+@sha256:[0-9a-f]{64}")
IMAGE_PINNED_RE = re.compile(r"(?:docker://)?[^@\s]+@sha256:[0-9a-f]{64}")
RULES = ("import-closure", "dynamic-import", "relative-escape", "vendor-provenance", "action-pin", "disposition")
IMPORT_ERRORS = ("ImportError", "ModuleNotFoundError", "Exception", "BaseException")

# The accepted YAML subset (see the docstring). Anything else on a structural line cannot be classified.
# U+2028, U+2029 and U+FEFF are written by code point, so no invisible character sits in this source.
YAML_FORBIDDEN_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f%s]|\r(?!\n)"
                               % "".join(map(chr, (0x2028, 0x2029, 0xFEFF))))
YAML_LINE_RE = re.compile(r"(?P<indent> *)(?P<dash>-(?: +|$))?(?P<body>.*)")
YAML_KEY_RE = re.compile(r"(?P<key>[A-Za-z0-9_][A-Za-z0-9_.\-/]*|'[A-Za-z0-9_.\-/ ]*'|\"[A-Za-z0-9_.\-/ ]*\")"
                         r" *:(?: +(?P<value>.*))?")
YAML_TAIL = r"(?:[ \t]+#.*)?"
YAML_BLOCK_RE = re.compile(r"[|>][+-]?" + YAML_TAIL)
YAML_SINGLE_RE = re.compile(r"'(?P<value>(?:[^']|'')*)'" + YAML_TAIL)
YAML_DOUBLE_RE = re.compile(r"\"(?P<value>[^\"\\]*)\"" + YAML_TAIL)
YAML_FLOW_ITEM = r"(?:[A-Za-z0-9_.\-/]+|'[^':\\]*'|\"[^\":\\]*\")"
YAML_FLOW_SEQ_RE = re.compile(r"\[ *(?:%s *(?:, *%s *)*)?\]" % (YAML_FLOW_ITEM, YAML_FLOW_ITEM) + YAML_TAIL)
YAML_PLAIN_START = "[]{},#&*!|>'\"%@`"
USES_KEYLIKE_RE = re.compile(r"(?i)(?<![A-Za-z0-9_-])uses['\"]?[ \t]*:")
PIN_KEYS = ("uses", "image", "container")

# SPDX licence identifiers the vendor-provenance rule accepts, each with the sentences its licence text must
# contain (compared case-insensitively with runs of whitespace collapsed).
SPDX_LICENCE_MARKERS = (
    ("0BSD", ("Permission to use, copy, modify, and/or distribute this software for any purpose with or "
              "without fee is hereby granted",)),
    ("Apache-2.0", ("Apache License", "Version 2.0, January 2004")),
    ("BSD-2-Clause", ("Redistribution and use in source and binary forms, with or without modification, are "
                      "permitted provided that the following conditions are met",)),
    ("BSD-3-Clause", ("Redistribution and use in source and binary forms, with or without modification, are "
                      "permitted provided that the following conditions are met", "Neither the name of")),
    ("ISC", ("this software for any purpose with or without fee is hereby granted, provided that the above "
             "copyright notice and this permission notice appear in all copies",)),
    ("MIT", ("Permission is hereby granted, free of charge, to any person obtaining a copy",)),
    ("MPL-2.0", ("Mozilla Public License Version 2.0",)),
    ("PSF-2.0", ("PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",)),
    ("Unlicense", ("This is free and unencumbered software released into the public domain",)),
    ("Zlib", ("This software is provided 'as-is', without any express or implied warranty",)),
)

# The dynamic-import rule's watched names (see the docstring). Written as one string so the table itself is no
# site: a watched name is a site only as an identifier or as a whole dotted string literal.
DYNAMIC_IMPORT_NAMES = frozenset("""
    __import__ __builtins__ __loader__ __path__ __spec__ importlib imp pkgutil pydoc runpy zipimport
    import_module find_spec find_loader load_module exec_module module_from_spec spec_from_file_location
    spec_from_loader SourceFileLoader SourcelessFileLoader ExtensionFileLoader PathFinder FileFinder zipimporter
    run_module run_path resolve_name get_loader get_importer iter_importers walk_packages extend_path locate
    meta_path path_hooks path_importer_cache help _sitebuiltins
""".split())
# Watched names that are no site as the name of a keyword argument: help= is argparse's option text, and a keyword
# argument's name binds a parameter of the function called, never the builtin.
KEYWORD_EXEMPT_NAMES = frozenset("""
    help
""".split())
# The import system's own modules. The watched set is read from them on every run (see the docstring): every
# name sys.modules gives one of them (the frozen _frozen_importlib and _frozen_importlib_external among them) is
# watched like a name in DYNAMIC_IMPORT_NAMES, and every function, class or module defined in them is watched as
# a member: as an attribute name or a string literal component, the positions a member is reached by.
IMPORT_SYSTEM_MODULES = tuple("""
    importlib importlib._bootstrap importlib._bootstrap_external importlib.abc importlib.machinery importlib.util
    _imp pkgutil runpy zipimport
""".split())
# Members of those modules that neither import nor load a module and whose names this tree uses for its own
# objects: (name, why it is not watched). Each must still be a member, or the gate cannot evaluate.
INERT_IMPORT_SYSTEM_MEMBERS = (
    ("abc", "the leaf name of importlib.abc is also the standard-library abc module; importlib.abc is reached "
     "only through a watched importlib reference, and its loader and finder classes are watched by name"),
    ("acquire_lock", "takes the import lock (_imp); it imports nothing"),
    ("lock_held", "reports whether the import lock is held (_imp); it imports nothing"),
    ("release_lock", "releases the import lock (_imp); it imports nothing"),
)
# The importers, each read by its real signature, written as one string so the table is no site: callee name, the
# keywords of its module-name parameter (its first), the position:keyword of its level parameter or -, what the
# parameter names (module; relative, a module or, beginning with a dot, a name that is not resolved: import_module
# and find_spec read it relative to their package argument, pkgutil.resolve_name refuses it and
# importlib.util.resolve_name imports nothing; or path, a file path, which is not resolved), and the module the
# callee must be reached through (pydoc's doc, render_doc, resolve and writedoc only as attributes of pydoc, since
# this tree defines functions of those names) or -. A string-literal absolute name an importer call passes at level
# 0 is resolved like an import statement wherever the call sits, an allowlisted site included; a starred or
# double-starred argument, and a literal name or level the gate cannot read, at any importer call is a finding (see
# the docstring). help and pydoc's helpers import the module a dotted name starts with; iter_importers imports only
# the parent package of a dotted name, once iterated, and is read like the others (a disclosed over-rejection).
LITERAL_IMPORTERS = tuple((row[0], tuple(row[1].split(",")), None if row[2] == "-" else
                           (int(row[2].split(":")[0]), row[2].split(":")[1]), row[3], None if row[4] == "-" else row[4])
                          for row in (line.split() for line in """
    __import__     name           4:level  module    -
    import_module  name           -        relative  -
    _gcd_import    name           2:level  module    -
    find_spec      name,fullname  -        relative  -
    resolve_name   name           -        relative  -
    run_module     mod_name       -        module  -
    run_path       path_name      -        path    -
    locate         path           -        module  -
    safeimport     path           -        module  -
    help           request        -        module  -
    _find_and_load      name           -   module  -
    iter_importers      fullname       -   module  -
    _run_module_as_main mod_name       -   module  -
    _get_module_details mod_name       -   module  -
    doc            thing          -        module  pydoc
    render_doc     thing          -        module  pydoc
    resolve        thing          -        module  pydoc
    writedoc       thing          -        module  pydoc
""".strip().splitlines()))
IMPORTERS = dict((row[0], row) for row in LITERAL_IMPORTERS)
# A call of an instance these classes make is a call of help: pydoc.Helper and the builtin help's own class.
HELPER_CLASSES = dict(pair.split(":") for pair in "Helper:help _Helper:help".split())
# The requests pydoc's Helper.help on the floor version reads, once stripped, as a listing rather than as a module
# name: keywords, symbols and topics list help's own topics, and modules imports every package it finds to list it.
# A help request equal to one of them names no module the gate can resolve, so it is refused (see the docstring).
HELP_LISTINGS = frozenset("keywords symbols topics modules".split())
DOTTED_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
REFERENCE_RE = re.compile(r"#[1-9][0-9]*: \S.*")
MODULE_SCOPE = "<module>"
SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

# The extra search roots of each program that imports across directories (beyond its own directory): each
# row is (repo-relative importing file, repo-relative root directory), a sys.path insertion that file makes.
# The import-closure rule holds every row live (see the docstring).
IMPORT_SEARCH_ROOTS = (
    (".github/check_cited_standards_contract.py", "opf/tools"),
    (".github/check_cited_standards_contract.py", "tools"),
    ("opf/tools/_commonmark_headings.py", "opf/tools/_vendor"),
    ("tools/check_byte_canon.py", "opf/tools"),
    ("tools/check_crosswalk.py", "opf/tools"),
    ("tools/check_manifest.py", "opf/tools"),
    ("tools/check_opf_declaration.py", "opf/tools"),
    ("tools/check_release_cut.py", "opf/tools"),
    ("tools/check_relied_exit_pipes.py", ".aiqt/core/hooks/scripts"),
    ("tools/check_versions.py", "opf/tools"),
    ("tools/doctor.py", "opf/tools"),
    ("tools/migrate.py", "opf/tools"),
    ("tools/orch_doctor.py", ".aiqt/core/hooks/scripts"),
    ("tools/orch_preflight.py", ".aiqt/core/hooks/scripts"),
    ("tools/pin.py", "opf/tools"),
    ("tools/selftest_aiqt_hooks.py", ".aiqt/core/hooks/scripts"),
    ("tools/selftest_git_fixture_env.py", "opf/tools"),
    ("tools/selftest_git_fixture_env_core.py", "opf/tools"),
    ("tools/selftest_orch_hooks.py", ".aiqt/core/hooks/scripts"),
)
# The closed set of insertion-argument shapes the gate reads (see the docstring): the one literal path component,
# the highest .parents index (17 directories up, at most), and the one binding each name a shape calls must have in
# the whole file, its exact import.
INSERT_PART_RE = re.compile(r"[A-Za-z0-9_.-]+")
MAX_PARENTS_INDEX = 16
# The most names a pathlib chain's start is followed through, each read as a name is (see the docstring): an
# insertion of str(NAME) where NAME = OTHER / LITERAL and OTHER is bound to Path(__file__).resolve().parents[N].
MAX_NAME_HOPS = 2
INSERT_IMPORTS = dict(os=("import", "os"), Path=("from", "pathlib", "Path"),
                      repo_root=("from", "_gen_common", "repo_root"))

# Third-party imports inside byte-exact vendored code (opf/tools/_vendor is not edited here; its provenance
# record states that no extra is vendored and no extension is loaded on the parse path). Each row is
# (repo-relative file, first dotted component, condition). The disposition rule holds each row live and its
# condition true on every run, and every dispositioned import is printed, so the hit stays visible to the
# owner of the vendored tree.
VENDORED_OPTIONAL_IMPORTS = (
    ("opf/tools/_vendor/marko/element.py", "objprint", "guarded-optional"),
    ("opf/tools/_vendor/marko/ext/codehilite.py", "pygments", "unreached-module"),
    ("opf/tools/_vendor/marko/ext/toc.py", "slugify", "guarded-optional"),
)
CONDITIONS = ("guarded-optional", "unreached-module")

# The reviewed dynamic-import sites: (repo-relative file, enclosing scope, the exact references allowed there, each
# #ORDINAL: TEXT with the reference's unparsed source and its ordinal among the references of that text in that
# scope, why they are legitimate). A scope is one lexical scope instance: a class reads as class NAME, and a scope
# whose enclosing scope holds N > 1 scopes of its name carries #K/N, K its ordinal among them in source order. Dynamic
# imports are not resolved; a reference no row lists is a finding, an edited reference or one moved into another scope
# (a sibling lambda or a same-named def among them) included, unless the same edit keeps N and leaves the reference on
# the #K/N it held (a disclosed residual; a move within its scope is not a finding either), and the dynamic-import rule
# holds every row live (see the docstring).
DYNAMIC_IMPORT_SITES = (
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_orch_sha256_hex",
     ("#1: __import__('hashlib').sha256(text.encode('utf-8')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_state_dir_from_registry",
     ("#1: __import__('hashlib').sha256(root.encode('utf-8', 'replace')).hexdigest()[:16]",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_orch_register_wake",
     ("#1: __import__('hashlib').sha256(prompt.encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "orch_ask_guard",
     ("#1: __import__('hashlib').sha256(text.encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "orch_dispatch_ledger",
     ("#1: __import__('hashlib').sha256((basis + _orch_now().isoformat()).encode('utf-8', "
      "'replace')).hexdigest()[:12]",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_blob_id",
     ("#1: __import__('hashlib').new(fmt)",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_worktree_entry",
     ("#1: __import__('errno')",
      "#1: __import__('hashlib').new(fmt)"),
     "inline __import__ of the standard-library module errno and hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_decide",
     ("#1: __import__('threading').Thread(target=_work, name='review-dispatch-pin', daemon=True)",),
     "inline __import__ of the standard-library module threading, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_binding_digest",
     ("#1: __import__('hashlib').sha256(blob.encode('ascii')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_rdp_record_path",
     ("#1: __import__('hashlib').sha256(json.dumps([identity, rel]).encode('utf-8', "
      "'surrogateescape'))",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "orch_prompt_stamp",
     ("#1: __import__('hashlib').sha256((prompt or '').encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_orch_chained_rows",
     ("#1: __import__('hashlib').sha256",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_orch_lease_hb_flock",
     ("#1: __import__('fcntl')",),
     "binds __import__ of the standard-library module fcntl, a string literal, to a local name: equivalent to a "
     "plain import fcntl in this function, not a dynamic load (fcntl is POSIX-only and no module-level import "
     "loads it)"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_commit_command_syntax",
     ("#1: _CommitSyntax('help', records, steps, commits, 'exact lone help form')",),
     "the string help names a commit-command form (its status), data, not the builtin help"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "_protected_line_fallback",
     ("#1: 'help'",),
     "the string help names a commit-command form (its status), data, not the builtin help"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", "protected_line",
     ("#1: 'help'",),
     "the string help names a commit-command form (its status), data, not the builtin help"),
    (".aiqt/core/hooks/scripts/aiqt_hooks.py", MODULE_SCOPE,
     ("#1: 'help'",
      "#2: 'help'",
      "#3: 'help'",
      "#4: 'help'",
      "#5: 'help'",
      "#6: 'help'",
      "#7: 'help'"),
     "the string help is a git subcommand name in this file's subcommand and option tables, data, not the builtin "
     "help"),
    (".preview/char-policy-write.py", "_self_test",
     ("#1: import importlib.util",
      "#1: '__builtins__'",
      "#2: '__builtins__'"),
     "vectors name __builtins__ as data for the rebinding check; imports importlib.util for the sibling load"),
    (".preview/char-policy-write.py", "_self_test.sibling_gate",
     ("#1: importlib.util.spec_from_file_location('_char_policy_sibling_gate', path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling gate it compares"),
    (".preview/char-policy-write.py", "_self_test.rebound",
     ("#1: '__builtins__'",),
     "the rebinding analysis watches stores to __builtins__; data, not an import"),
    (".preview/clock-inject.py", "_self_test.class T.test_r13_shared_lease_code_identical_to_stop_hook",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location('sts_sibling', sib)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    (".preview/future-stamp-write.py", "_self_test",
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    (".preview/future-stamp-write.py", "_self_test.class T.test_shared_grammar_identical_to_sibling",
     ("#1: importlib.util.spec_from_file_location('sts_sibling', sib)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    (".preview/future-stamp-write.py", "_self_test.class T.test_r13_code_quote_helpers_identical_to_sibling",
     ("#1: importlib.util.spec_from_file_location('sts_sibling13', sib)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    (".preview/record-remove-check.py", MODULE_SCOPE,
     ("#1: 'help'",
      "#2: 'help'",
      "#3: 'help'",
      "#4: 'help'"),
     "the string help is an option name in this file's option tables, data, not the builtin help"),
    (".preview/stamp-truth-stop.py", "_self_test",
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    (".preview/stamp-truth-stop.py", "_self_test.class T.test_r13_shared_lease_code_identical_to_clock_inject",
     ("#1: importlib.util.spec_from_file_location('ci_sibling', sib)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    (".preview/stamp-truth-stop.py", "_self_test.class T.test_shared_grammar_identical_to_sibling",
     ("#1: importlib.util.spec_from_file_location('fsw_sibling', sib)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "sibling hook it compares"),
    (".preview/ungated-record.py", MODULE_SCOPE,
     ("#1: 'help'",
      "#2: 'help'",
      "#3: 'help'"),
     "the string help is an option name in this file's option tables, data, not the builtin help"),
    ("opf/enforcement/claude/pretooluse_deny.py", MODULE_SCOPE,
     ("#1: 'help'",
      "#2: 'help'"),
     "the string help is a git subcommand name, data, not the builtin help"),
    ("opf/enforcement/claude/pretooluse_deny.py", "_git_runs_code",
     ("#1: 'help'",),
     "the string help is a git subcommand name, data, not the builtin help"),
    ("opf/tools/_opf_adopt_apply.py", "_load_sweep_report_slice",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location('_opf_sweep_report_slice_' + tag, str(path))",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads, by its path, a scratch file holding the sweep-reporting slice of this file's own "
     "_self_test_checks (spec_from_file_location, module_from_spec, exec_module)"),
    ("opf/tools/_opf_adopt_observe.py", "_cancellation_self_test",
     ("#1: builtins.__import__",
      "#1: patch.object(builtins, '__import__', no_lazy_import)"),
     "self-test saves and patches builtins.__import__ to prove no lazy import runs, then restores it"),
    ("opf/tools/_opf_check.py", "self_test",
     ("#1: imp={'imported:BI': 1}",
      "#2: imp={'imported:BI': 1}",
      "#3: imp={'imported:BI': 1}",
      "#4: imp={'imported:BI': 1}",
      "#5: imp={'imported:BI': 1}",
      "#6: imp={'imported:BI': 1}",
      "#7: imp={'imported:BI': 1}",
      "#8: imp={'imported:BI': 1}",
      "#9: imp={'imported:BI': 1}",
      "#10: imp={'imported:BI': 1}",
      "#11: imp={'imported:BI': 1}",
      "#12: imp={'imported:BI': 1}",
      "#13: imp={'imported:BI': 1}",
      "#14: imp={'imported:BI': 1}",
      "#1: imp={'imported:BI': 2}",
      "#2: imp={'imported:BI': 2}",
      "#3: imp={'imported:BI': 2}",
      "#4: imp={'imported:BI': 2}",
      "#5: imp={'imported:BI': 2}",
      "#6: imp={'imported:BI': 2}",
      "#7: imp={'imported:BI': 2}",
      "#8: imp={'imported:BI': 2}",
      "#9: imp={'imported:BI': 2}",
      "#10: imp={'imported:BI': 2}",
      "#1: imp={'imported:BI': 3}",
      "#2: imp={'imported:BI': 3}",
      "#1: imp={'imported:WL': 1}",
      "#1: imp={'imported:WL': 2}",
      "#2: imp={'imported:WL': 2}",
      "#1: imp={'imported:WL': 3}",
      "#2: imp={'imported:WL': 3}",
      "#3: imp={'imported:WL': 3}"),
     "imp is the keyword parameter of the self-test's own _i3_machine fixture builder (the imported-counter "
     "values it seeds), not the imp module"),
    ("opf/tools/_opf_check.py", "self_test._i3_ns_machine",
     ("#1: imp={'imported:' + ns: imp_high}",),
     "imp is the keyword parameter of the self-test's own _i3_machine fixture builder (the imported-counter "
     "values it seeds), not the imp module"),
    ("opf/tools/_opf_check.py", "self_test._i3_ovm",
     ("#1: imp={'imported:BI': 4}",),
     "imp is the keyword parameter of the self-test's own _i3_machine fixture builder (the imported-counter "
     "values it seeds), not the imp module"),
    ("opf/tools/_opf_check.py", "self_test.fn_date",
     ("#1: __import__('datetime').date(2026, 6, 1)",),
     "inline __import__ of the standard-library module datetime, a string literal"),
    ("opf/tools/_opf_emit.py", "_load_byte_canon_authority",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location('_opf_emit_byte_canon_authority', path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the byte-canon "
     "authority"),
    ("opf/tools/_opf_emit.py", "_fixture_preload",
     ("#1: import importlib",
      "#1: importlib.import_module(name)"),
     "imports the fixed standard-library names of _FIXTURE_GUARDIAN_MODULES before a fork"),
    ("opf/tools/_opf_emit.py", "_st_guardian_close_reuse",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location('_opf_emit_close_harness', "
      "Path(__file__).resolve().parent / '_journal.py')",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(_journal)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "close harness"),
    ("opf/tools/_opf_emit.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/_opf_init.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/_opf_init_operation.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/_opf_store.py", MODULE_SCOPE,
     ("#1: 'imp'",
      "#2: 'imp'"),
     "the string imp is a run-id prefix, not the imp module"),
    ("opf/tools/_opf_views.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/_vendor/marko/cli.py", MODULE_SCOPE,
     ("#1: import importlib",),
     "byte-exact vendored Marko: its command-line entry imports importlib"),
    ("opf/tools/_vendor/marko/cli.py", "import_class",
     ("#1: importlib.import_module(module)",),
     "byte-exact vendored Marko: its command-line entry imports a class named on its command line; not on this "
     "tree's parse path"),
    ("opf/tools/_vendor/marko/helpers.py", MODULE_SCOPE,
     ("#1: from importlib import import_module",),
     "byte-exact vendored Marko: imports import_module for load_extension"),
    ("opf/tools/_vendor/marko/helpers.py", "load_extension",
     ("#1: import_module(f'marko.ext.{name}')",
      "#1: import_module(name)"),
     "byte-exact vendored Marko: loads an extension by a computed name; this tree passes none (disclosed residual)"),
    ("opf/tools/check_opf_doctor.py", "_claude_hook_self_test",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location('_opf_claude_hook_classifier', hook)",
      "#1: importlib.util.module_from_spec(hook_spec)",
      "#1: hook_spec.loader.exec_module(hook_mod)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "Claude hook classifier"),
    ("opf/tools/check_opf_doctor.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/check_opf_drift.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/check_opf_homes.py", "_self_test_vectors",
     ("#1: 'imp'",
      "#2: 'imp'",
      "#3: 'imp'",
      "#4: 'imp'"),
     "the string imp is a run-id prefix in vectors, not the imp module"),
    ("opf/tools/check_opf_homes.py", "_self_test_vectors.<lambda>#12/175.<lambda>",
     ("#1: 'imp'",),
     "the string imp is a run-id prefix in vectors, not the imp module"),
    ("opf/tools/check_opf_homes.py", "_self_test_vectors.<lambda>#84/175.<lambda>",
     ("#1: 'imp'",),
     "the string imp is a run-id prefix in vectors, not the imp module"),
    ("opf/tools/check_opf_homes.py", "_self_test_vectors.<lambda>#97/175",
     ("#1: 'imp'",),
     "the string imp is a run-id prefix in vectors, not the imp module"),
    ("opf/tools/check_opf_homes.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/check_opf_init.py", "_suite_isolated",
     ("#1: opf._bootstrap()",),
     "calls opf.py's own _bootstrap function (the opf module of this tree), not importlib._bootstrap"),
    ("opf/tools/check_opf_init.py", "_suite_isolated",
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("opf/tools/check_opf_init.py", "_suite_isolated.load_mutant",
     ("#1: importlib.util.spec_from_file_location('opf_route_mutant_' + tag.replace('-', '_'), "
      "str(mutant_path))",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)",
      "#1: module._bootstrap()"),
     "self-test loads a scratch mutant of opf.py, written from this tree's opf.py source with one fixed insertion, "
     "by its path, and calls that mutant's own _bootstrap (opf.py's helper imports), not importlib._bootstrap"),
    ("opf/tools/check_opf_init.py", "_suite_isolated.load_plain",
     ("#1: importlib.util.spec_from_file_location('opf_boot_mutant_' + tag.replace('-', '_'), "
      "str(plain_path))",
      "#1: importlib.util.module_from_spec(plain_spec)",
      "#1: plain_spec.loader.exec_module(plain_module)"),
     "self-test loads a scratch mutant of opf.py, written from this tree's opf.py source with one fixed insertion, "
     "by its path"),
    ("opf/tools/check_opf_init.py", "_suite_isolated",
     ("#1: boot_module._bootstrap",),
     "passes a loaded boot mutant's own _bootstrap (opf.py's, a raise inserted before its first helper import) to "
     "fd_capture; not importlib._bootstrap"),
    ("opf/tools/check_opf_init_contract.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/check_opf_init_observe.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/check_opf_init_p0.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/check_opf_prompt_pack.py", "_load_sibling",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location(name, path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads a file by its path (spec_from_file_location, module_from_spec, exec_module): its callers pass "
     "this tree's _journal.py beside this file (the close harness) or, in the loaded-exit probe child, a scratch "
     "file the self-test wrote from one _LOADED_EXIT_CASES literal; no input chooses the path"),
    ("opf/tools/check_opf_prompt_pack.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("opf/tools/check_opf_record.py", "_self_test_isolated",
     ("#1: opf._bootstrap()",),
     "calls opf.py's own _bootstrap function (the opf module of this tree), not importlib._bootstrap"),
    ("opf/tools/check_opf_record.py", "t81_cross_kind_reservations",
     ("#1: 'imp'",
      "#2: 'imp'"),
     "the string imp is an import run-id prefix in reservation fixtures, not the imp module"),
    ("opf/tools/check_opf_record.py", "t81_cross_kind_reservations.<lambda>#5/6",
     ("#1: 'imp'",),
     "the string imp is an import run-id prefix in reservation fixtures, not the imp module"),
    ("opf/tools/opf.py", MODULE_SCOPE,
     ("#1: '__builtins__'",),
     "names __builtins__ as a namespace the entry check refuses stores through; data, not an import"),
    ("opf/tools/opf.py", "_self_test_entry_gap",
     ("#1: bindings_of('__builtins__')",),
     "the entry check reads bindings of __builtins__; not an import"),
    ("opf/tools/opf.py", "_import_body_findings",
     ("#1: '__builtins__'",
      "#1: bindings['__builtins__']"),
     "the namespace check reads bindings of __builtins__; not an import"),
    ("opf/tools/opf.py", "_import_namespace_findings",
     ("#1: getattr(func, '__builtins__', None)",),
     "compares a function's __builtins__ with the builtins dictionary; not an import"),
    ("opf/tools/opf.py", "_cli_self_test._import_leg",
     ("#1: probe.__builtins__",
      "#1: '__builtins__'"),
     "vectors bind __builtins__ to prove the namespace check refuses it; not an import"),
    ("opf/tools/opf.py", "_bootstrap_exit_findings",
     ("#1: sys.path_importer_cache.pop(tmp, None)",),
     "drops the importer-cache entry of the temporary directory the self-test put on sys.path for a stand-in "
     "_opf_worklog; it imports nothing"),
    ("opf/tools/opf.py", "_watchdog_closeout_vectors",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location('_opf_emit_closeout_vector', Path(__file__).resolve().parent / "
      "'_opf_emit.py' if source is None else source)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(emit)"),
     "self-test loads, by its path, a private copy of this tree's opf/tools/_opf_emit.py beside this file, or a "
     "scratch mutant of it written from that source with one fixed wait inserted; never entered in sys.modules"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_orch_sha256_hex",
     ("#1: __import__('hashlib').sha256(text.encode('utf-8')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_state_dir_from_registry",
     ("#1: __import__('hashlib').sha256(root.encode('utf-8', 'replace')).hexdigest()[:16]",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_orch_register_wake",
     ("#1: __import__('hashlib').sha256(prompt.encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "orch_ask_guard",
     ("#1: __import__('hashlib').sha256(text.encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "orch_dispatch_ledger",
     ("#1: __import__('hashlib').sha256((basis + _orch_now().isoformat()).encode('utf-8', "
      "'replace')).hexdigest()[:12]",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_blob_id",
     ("#1: __import__('hashlib').new(fmt)",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_worktree_entry",
     ("#1: __import__('errno')",
      "#1: __import__('hashlib').new(fmt)"),
     "inline __import__ of the standard-library module errno and hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_decide",
     ("#1: __import__('threading').Thread(target=_work, name='review-dispatch-pin', daemon=True)",),
     "inline __import__ of the standard-library module threading, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_binding_digest",
     ("#1: __import__('hashlib').sha256(blob.encode('ascii')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_rdp_record_path",
     ("#1: __import__('hashlib').sha256(json.dumps([identity, rel]).encode('utf-8', "
      "'surrogateescape'))",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "orch_prompt_stamp",
     ("#1: __import__('hashlib').sha256((prompt or '').encode('utf-8', 'replace')).hexdigest()",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_orch_chained_rows",
     ("#1: __import__('hashlib').sha256",),
     "inline __import__ of the standard-library module hashlib, a string literal"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_orch_lease_hb_flock",
     ("#1: __import__('fcntl')",),
     "binds __import__ of the standard-library module fcntl, a string literal, to a local name: equivalent to a "
     "plain import fcntl in this function, not a dynamic load (fcntl is POSIX-only and no module-level import "
     "loads it)"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_commit_command_syntax",
     ("#1: _CommitSyntax('help', records, steps, commits, 'exact lone help form')",),
     "the string help names a commit-command form (its status), data, not the builtin help"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "_protected_line_fallback",
     ("#1: 'help'",),
     "the string help names a commit-command form (its status), data, not the builtin help"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", "protected_line",
     ("#1: 'help'",),
     "the string help names a commit-command form (its status), data, not the builtin help"),
    ("plugin/aiqt-guardrails-hooks/hooks/scripts/aiqt_hooks.py", MODULE_SCOPE,
     ("#1: 'help'",
      "#2: 'help'",
      "#3: 'help'",
      "#4: 'help'",
      "#5: 'help'",
      "#6: 'help'",
      "#7: 'help'"),
     "the string help is a git subcommand name in this file's subcommand and option tables, data, not the builtin "
     "help"),
    ("tools/_selftest_exit_report.py", MODULE_SCOPE,
     ("#1: import importlib.machinery",
      "#1: import importlib.util"),
     "imports importlib.machinery and importlib.util for the runner loader"),
    ("tools/_selftest_exit_report.py", MODULE_SCOPE,
     ("#1: threading.Thread._bootstrap",),
     "threading's own Thread._bootstrap method, bound to run every thread; not importlib._bootstrap"),
    ("tools/_selftest_exit_report.py", "_record_subinterp_files",
     ("#1: importlib.util.find_spec(name)",),
     "find_spec locates the files of fixed standard-library extension modules without importing them"),
    ("tools/_selftest_exit_report.py", MODULE_SCOPE,
     ("#1: importlib.machinery.SourceFileLoader",),
     "subclasses SourceFileLoader to launch a registered runner from its source, as a direct launch does"),
    ("tools/_selftest_exit_report.py", "_bootstrap_main",
     ("#1: main.__loader__",
      "#1: loader.exec_module(main)"),
     "executes the registered runner through that loader as __main__"),
    ("tools/check_child_status_coverage.py", MODULE_SCOPE,
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_child_status_coverage.py", MODULE_SCOPE,
     ("#1: '__import__'",
      "#2: '__import__'",
      "#1: 'exec_module'",
      "#1: 'import_module'",
      "#1: 'load_module'",
      "#1: 'spec_from_file_location'",
      "#1: 'locate'",
      "#1: 'resolve_name'",
      "#1: 'run_module'",
      "#1: 'run_path'"),
     "the strings name the load calls the completeness scan counts (DYNAMIC_NAMES and DYNAMIC_ATTRS); each is "
     "compared against a call's name in the source it scans, never called or resolved"),
    ("tools/check_child_status_coverage.py", "_canonical_module",
     ("#1: importlib.util.spec_from_file_location('child_status_canonical', Path(root) / CANONICAL_REL)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): the "
     "canonical guard opf/tools/_child_status.py under the given root"),
    ("tools/check_child_status_coverage.py", "_vectors.<lambda>#107/116",
     ("#1: 'import_module'",),
     "the string is matched in a cannot-evaluate reason the classifier gives for a fixture's load call; never "
     "called or resolved"),
    ("tools/check_child_status_coverage.py", "_load_mutant",
     ("#1: importlib.util.spec_from_file_location('child_status_coverage_mutant_{}'.format(number), path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_dangerous_api.py", MODULE_SCOPE,
     ("#1: '_load'",),
     "the string names pickle's private _load in the lint's unsafe-loader table; it is compared against the "
     "names in the source it scans, never called or resolved"),
    ("tools/check_dangerous_api.py", MODULE_SCOPE,
     ("#1: 'runpy.run_path'",
      "#1: 'runpy.run_module'",
      "#1: 'runpy._run_code'",
      "#1: 'runpy._run_module_code'",
      "#1: 'exec_module'",
      "#1: 'load_module'",
      "#2: 'exec_module'",
      "#2: 'load_module'",
      "#1: 'importlib.import_module'",
      "#1: '__import__'",
      "#1: 'builtins.__import__'"),
     "the strings name code sinks, loader methods and importers in the lint's tables (CODE_SINKS, LOADER_METHODS, "
     "METHOD_SINKS, _IMPORTERS); each is compared against the names in the source it scans, never called or "
     "resolved"),
    ("tools/check_dangerous_api.py", MODULE_SCOPE,
     ("#1: '_GuardedMutant.__getattr__'",),
     "the string names a scope of tools/check_instruction_budget.py in the lint's ALLOWLIST; it is compared "
     "against the scopes of the source it scans, never called or resolved"),
    ("tools/check_entry_guard.py", MODULE_SCOPE,
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_entry_guard.py", "_load_mutant",
     ("#1: importlib.util.spec_from_file_location('entry_guard_mutant_{}'.format(number), path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_footer.py", "_close_vectors",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location('_footer_close_revert', path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(reverted)"),
     "self-test loads a scratch mutant of this file's _read_regular_page, written from its own source with the "
     "descriptor ownership reverted, by its path, over a copy of this module's globals"),
    ("tools/check_footer.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("tools/check_import_closure.py", "<module>",
     ("#1: import importlib.util",),
     "it imports importlib.util for _load_mutant"),
    ("tools/check_import_closure.py", "_literal_import_name",
     ("#1: 'help'",
      "#1: 'resolve_name'",
      "#2: 'help'",),
     "the importer names help and resolve_name, compared as string literals to read each importer's own argument spelling"),
    ("tools/check_import_closure.py", "watched_names",
     ("#1: importlib.import_module(name)",),
     "loads the fixed standard-library modules of IMPORT_SYSTEM_MODULES to read the watched set from them"),
    ("tools/check_import_closure.py", "_load_mutant",
     ("#1: importlib.util.spec_from_file_location('import_closure_mutant_%d' % number, path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)",),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_instruction_budget.py", "_mutant",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location('check_instruction_budget_mutant', path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(mutant)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_instruction_budget.py", "_install_fault_hooks",
     ("#1: _threads.Thread._bootstrap",),
     "reads the standard-library threading's own Thread._bootstrap (threading imported under a local alias) only "
     "to compare a low-level thread start's target with it by identity; never called, not importlib._bootstrap"),
    ("tools/check_internal_names.py", "_load_ratchet_mutant",
     ("#1: import importlib.util",
      "#1: importlib.util.spec_from_file_location('tracker_mutant_{}'.format(tag), str(path))",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads a scratch mutant of this file, written from its own source with one ratchet comparison "
     "disabled, by its path; the mutant imports only what this file imports, which this gate already checks"),
    ("tools/check_manifest.py", "_self_test_main_isolated",
     ("#1: __import__('subprocess').run(['git', '--version'], capture_output=True).returncode",),
     "inline __import__ of the standard-library module subprocess, a string literal"),
    ("tools/check_opf_declaration.py", MODULE_SCOPE,
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_opf_declaration.py", "_mutant",
     ("#1: importlib.util.spec_from_file_location('opf_declaration_mutant_{}'.format(number), "
      "home / '_opf_declaration.py')",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads a scratch mutant of opf/tools/_opf_declaration.py, written from that file's source with "
     "one revert, by its path"),
    ("tools/check_opf_declaration.py", "_copy_mutant",
     ("#1: importlib.util.spec_from_file_location(name, home / (name + '.py'))",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads a scratch mutant of this file or of opf/tools/opf.py, written from that file's own "
     "source with one revert, by its path"),
    ("tools/check_opf_declaration.py", "self_test",
     ("#1: opf._bootstrap()",),
     "calls opf.py's own _bootstrap function (the opf module of this tree), not importlib._bootstrap"),
    ("tools/check_opf_declaration.py", "self_test",
     ("#1: patch.object(here, '_load', missing_helper)",
      "#1: patch.object(unguarded, '_load', missing_helper)",
      "#1: '_load'",
      "#1: patch.object(unguarded, '_load', lambda: None)"),
     "the string _load names this gate's own _load helper, which the self-test replaces with a stand-in to "
     "show a missing helper module refused; not pickle's _load, never resolved as a loader"),
    ("tools/check_opf_declaration.py", "self_test.reach",
     ("#1: '_load'",
      "#2: '_load'",
      "#3: '_load'",
      "#4: '_load'"),
     "the string _load names this gate's own _load helper, which the self-test replaces for each site it raises "
     "at: with a stand-in that raises (#1, #2), or with lambda: None so that the raise comes from the _read_regular "
     "stand-in or the stand-in opf's _bootstrap instead (#3, #4); not pickle's _load, never resolved as a loader"),
    ("tools/check_opf_enforce_platforms.py", MODULE_SCOPE,
     ("#1: import importlib.util",),
     "imports importlib.util for load_hook"),
    ("tools/check_opf_enforce_platforms.py", "load_hook",
     ("#1: importlib.util.spec_from_file_location('_opf_enforce_platforms_hook', str(path))",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)",),
     "loads the in-tree Claude Code deny hook (HOOK_REL, or the self-test's copy of it) by its path under a private "
     "name to read its tables; the module is never entered in sys.modules and its main() is never called"),
    ("tools/check_overclaim.py", "_asset_closure_self_test",
     ("#2: 'imp.css'",
      "#1: 'imp.css'"),
     "the string imp.css is a fixture file name, not the imp module"),
    ("tools/check_python_floor.py", MODULE_SCOPE,
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_python_floor.py", "_rule_reverts",
     ("#1: importlib.util.spec_from_file_location('python_floor_copy', copy)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_python_floor.py", "_tomllib_by_name",
     ("#1: '__import__'",
      "#1: 'find_spec'",
      "#1: 'import_module'"),
     "the strings name loader callables the tomllib leg recognizes in the source it scans; each is compared "
     "against a call's name in that source, never called or resolved"),
    ("tools/check_python_floor.py", "_load_path",
     ("#1: importlib.util.spec_from_file_location(name, path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads, by a path its caller builds from ROOT, the in-repo tools/_gen_common.py and "
     "opf/enforcement/claude/pretooluse_deny.py and a fixture copy of .github/check_newtab_contract.py"),
    ("tools/check_python_host_pin.py", MODULE_SCOPE,
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_python_host_pin.py", "_copy",
     ("#1: importlib.util.spec_from_file_location(name, base / (name + '.py'))",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads, by its path under the self-test's temporary directory, a copy or mutant of this file "
     "written from its own source, or a copy of this tree's tools/check_python_floor.py or tools/check_ci_parity.py"),
    ("tools/check_release_delta.py", "_post_release_e2e",
     ("#1: import importlib.util as _ilu",
      "#1: _ilu.cache_from_source(str(src))",),
     "the R5 pyc fixture computes, with cache_from_source, where to plant a crafted .pyc that must never load; "
     "it imports nothing through importlib"),
    ("tools/check_relied_exit_pipes.py", MODULE_SCOPE,
     ("#1: 'help'",),
     "the string help is a bash builtin name in the unmodelled-builtin roster, data, not the builtin help"),
    ("tools/check_relied_exit_pipes.py", "self_test_main",
     ("#1: 'help'",),
     "the string help is a bash builtin name the self-test requires the command-line rule to refuse, data, not "
     "the builtin help"),
    ("tools/check_rule_placement.py", MODULE_SCOPE,
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_rule_placement.py", "_load_probe_mutant",
     ("#1: importlib.util.spec_from_file_location('standards_mutant_{}'.format(number), path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads, by its path, a scratch mutant of this tree's tools/_standards.py, written from its source "
     "with one guard reverted"),
    ("tools/check_rule_placement.py", "_load_placement_mutant",
     ("#1: importlib.util.spec_from_file_location('placement_mutant', path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(module)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/check_rule_placement.py", MODULE_SCOPE,
     ("#1: '_is_dir'",
      "#2: '_is_dir'"),
     "the string names this file's own _is_dir (stat.S_ISDIR), the module attribute two self-test mutants "
     "patch; not the import system's _is_dir"),
    ("tools/check_selftest_execution.py", MODULE_SCOPE,
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/check_selftest_execution.py", "self_test",
     ("#1: importlib.util.spec_from_file_location('_selftest_exit_report_probe', "
      "str(Path(__file__).resolve().parent / '_selftest_exit_report.py'))",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(probe)"),
     "self-test loads a file of this tree by its path (spec_from_file_location, module_from_spec, exec_module): "
     "tools/_selftest_exit_report.py as a probe"),
    ("tools/gen_crosswalk.py", "_fdopen_vectors",
     ("#1: import importlib.util",),
     "imports importlib.util for this file's loads by path, each reviewed in its own row"),
    ("tools/gen_crosswalk.py", "_fdopen_vectors.flipped",
     ("#1: importlib.util.spec_from_file_location('_crosswalk_fdopen_flip_' + flip_path.stem, flip_path)",
      "#1: importlib.util.module_from_spec(spec)",
      "#1: spec.loader.exec_module(reverted)"),
     "self-test loads a scratch copy or mutant of this file, written from its own source, by its path"),
    ("tools/migrate.py", "self_test",
     ("#1: import importlib.util",
      "#1: sys.meta_path.insert(0, nd_finder)",
      "#1: importlib.util.spec_from_file_location('_migrate_nested_missing', os.path.abspath(__file__))",
      "#1: importlib.util.module_from_spec(nd_spec)",
      "#1: nd_spec.loader.exec_module(importlib.util.module_from_spec(nd_spec))",
      "#1: sys.meta_path.remove(nd_finder)"),
     "self-test reloads this file by its path, once under a meta_path finder that fails tomllib, to prove the "
     "missing-dependency path"),
    ("tools/migrate.py", "self_test.class _NestedMissingFinder",
     ("#1: def find_spec",),
     "the self-test finder answers for tomllib only, with spec_from_loader"),
    ("tools/migrate.py", "self_test.class _NestedMissingFinder.find_spec",
     ("#1: importlib.util.spec_from_loader(name, self)",),
     "the self-test finder answers for tomllib only, with spec_from_loader"),
    ("tools/migrate.py", "self_test.class _NestedMissingFinder",
     ("#1: def exec_module",),
     "the self-test finder's loader method, which raises to simulate a missing dependency"),
    ("tools/pin.py", "self_test",
     ("#1: import importlib.util",
      "#1: sys.meta_path.insert(0, nd_finder)",
      "#1: importlib.util.spec_from_file_location('_pin_nested_missing', os.path.abspath(__file__))",
      "#1: importlib.util.module_from_spec(nd_spec)",
      "#1: nd_spec.loader.exec_module(importlib.util.module_from_spec(nd_spec))",
      "#1: sys.meta_path.remove(nd_finder)"),
     "self-test reloads this file by its path, once under a meta_path finder that fails tomllib, to prove the "
     "missing-dependency path"),
    ("tools/pin.py", "self_test.class _NestedMissingFinder",
     ("#1: def find_spec",),
     "the self-test finder answers for tomllib only, with spec_from_loader"),
    ("tools/pin.py", "self_test.class _NestedMissingFinder.find_spec",
     ("#1: importlib.util.spec_from_loader(name, self)",),
     "the self-test finder answers for tomllib only, with spec_from_loader"),
    ("tools/pin.py", "self_test.class _NestedMissingFinder",
     ("#1: def exec_module",),
     "the self-test finder's loader method, which raises to simulate a missing dependency"),
    ("tools/selftest_aiqt_hooks.py", "_deadline_fire_take_failures.class InTake.__getattr__",
     ("#1: super().__getattr__(attr)",),
     "the fault-injecting signal stand-in forwards attribute lookup to its base class _FaultySignal; not the "
     "import system's __getattr__"),
    ("tools/selftest_git_fixture_env.py", MODULE_SCOPE,
     ("#1: '__import__'",),
     "the string names a reflective builtin the close-probe oracle refuses (fail closed); it is compared against "
     "the probed child's own names, never called or resolved"),
    ("tools/selftest_orch_hooks.py", "_leasehb_cases",
     ("#1: __import__('fcntl')",),
     "binds __import__ of the standard-library module fcntl, a string literal, to a local name: equivalent to a "
     "plain import fcntl in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_cases",
     ("#1: __import__('time')",),
     "binds __import__ of the standard-library module time, a string literal, to a local name: equivalent to a "
     "plain import time as mm_time in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#1: __import__('errno')",),
     "binds __import__ of the standard-library module errno, a string literal, to a local name: equivalent to a "
     "plain import errno as errno_mod in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#2: __import__('errno')",),
     "binds __import__ of the standard-library module errno, a string literal, to a local name: equivalent to a "
     "plain import errno as errno_mod2 in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#1: __import__('fcntl')",),
     "binds __import__ of the standard-library module fcntl, a string literal, to a local name: equivalent to a "
     "plain import fcntl as fcntl7 in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#2: __import__('fcntl')",),
     "binds __import__ of the standard-library module fcntl, a string literal, to a local name: equivalent to a "
     "plain import fcntl as fcntl8 in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#3: __import__('fcntl')",),
     "binds __import__ of the standard-library module fcntl, a string literal, to a local name: equivalent to a "
     "plain import fcntl as fcntl9 in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#3: __import__('errno')",),
     "binds __import__ of the standard-library module errno, a string literal, to a local name: equivalent to a "
     "plain import errno as errno9d in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#4: __import__('errno')",),
     "binds __import__ of the standard-library module errno, a string literal, to a local name: equivalent to a "
     "plain import errno as errno9 in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#5: __import__('errno')",),
     "binds __import__ of the standard-library module errno, a string literal, to a local name: equivalent to a "
     "plain import errno as errno10 in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#4: __import__('fcntl')",),
     "binds __import__ of the standard-library module fcntl, a string literal, to a local name: equivalent to a "
     "plain import fcntl as fcntl9f in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#5: __import__('fcntl')",),
     "binds __import__ of the standard-library module fcntl, a string literal, to a local name: equivalent to a "
     "plain import fcntl as fcntl11 in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_fix2_cases",
     ("#6: __import__('errno')",),
     "binds __import__ of the standard-library module errno, a string literal, to a local name: equivalent to a "
     "plain import errno as errno11 in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_write_integrity_cases",
     ("#1: __import__('errno')",),
     "binds __import__ of the standard-library module errno, a string literal, to a local name: equivalent to a "
     "plain import errno as errno_wi in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_leasehb_status_and_parse_cases",
     ("#1: __import__('errno')",),
     "binds __import__ of the standard-library module errno, a string literal, to a local name: equivalent to a "
     "plain import errno as errno_sp in this function, not a dynamic load"),
    ("tools/selftest_orch_hooks.py", "_main_isolated",
     ("#1: import importlib",
      "#1: importlib.import_module('orch_doctor')",
      "#1: __import__('io').StringIO()"),
     "imports the in-tree orch_doctor by its literal name through a recorded search root; inline __import__ of the "
     "standard-library io"),
    ("tools/selftest_orch_hooks.py", "_rdp_scope_cases",
     ("#1: 'help'",),
     "the string help is a git subcommand in a test case, data, not the builtin help"),
)


class CannotEvaluate(Exception):
    """A fail-closed condition: the verdict cannot be computed (exit 2)."""


def stdlib_names(sys_module=sys):
    """The standard-library top-level names, from the interpreter only. Refuses if the source is missing
    or malformed, so a hand list never stands in for it."""
    names = getattr(sys_module, "stdlib_module_names", None)
    if not isinstance(names, frozenset) or not names or not all(isinstance(n, str) and n for n in names):
        raise CannotEvaluate("sys.stdlib_module_names is unavailable or malformed on this interpreter; the "
                             "standard-library set has no other source")
    return names


def _regular_bytes(path, rel):
    """The bytes of a regular file, refusing a symlink, FIFO or other non-regular file without opening it."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("%s: cannot stat: %s" % (rel, exc))
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("%s: not a regular file (a symlink or special file)" % rel)
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise CannotEvaluate("%s: unreadable: %s" % (rel, exc))


def _text(path, rel):
    try:
        return _regular_bytes(path, rel).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("%s: not UTF-8: %s" % (rel, exc))


def _toml(path, rel):
    try:
        return tomllib.loads(_text(path, rel))
    except (ValueError, RecursionError, MemoryError) as exc:
        # TOMLDecodeError is a ValueError; a deeply nested or oversized file raises the other two
        raise CannotEvaluate("%s: does not parse as TOML: %s" % (rel, exc))


def load_floor(root):
    data = _toml(Path(root) / FLOOR_REL, FLOOR_REL)
    floor = data.get("python-floor")
    match = FLOOR_RE.fullmatch(floor) if isinstance(floor, str) else None
    if match is None:
        raise CannotEvaluate("%s: python-floor is absent or not MAJOR.MINOR" % FLOOR_REL)
    return int(match.group(1)), int(match.group(2))


def check_interpreter(floor, version_info):
    running = tuple(version_info[:2])
    if running != tuple(floor):
        raise CannotEvaluate("the standard-library set comes from this interpreter (Python %d.%d), which is not "
                             "the declared floor (Python %d.%d); run the gate under Python %d.%d"
                             % (running + tuple(floor) + tuple(floor)))


def _walk(root):
    """Every file under root as (repo-relative posix path, Path), leaving out SKIPPED_ROOT_NAMES at the root
    only and *.pyc files in a __pycache__ directory; fail-closed on an unlistable directory or a symlinked
    directory (never silently skipped)."""
    def _raise(exc):
        raise CannotEvaluate("cannot list a directory: %s" % exc)
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise):
        top = Path(dirpath) == root
        dirnames[:] = sorted(d for d in dirnames if not (top and d in SKIPPED_ROOT_NAMES))
        for d in dirnames:
            if os.path.islink(os.path.join(dirpath, d)):
                raise CannotEvaluate("%s: a symlinked directory is not walked"
                                     % Path(dirpath, d).relative_to(root).as_posix())
        cache = Path(dirpath).name == BYTECODE_DIR_NAME
        for name in sorted(filenames):
            if (top and name in SKIPPED_ROOT_NAMES) or (cache and name.endswith(".pyc")):
                continue
            path = Path(dirpath) / name
            out.append((path.relative_to(root).as_posix(), path))
    return out


def _join(directory, name):
    return name if not directory else directory + "/" + name


def _dir_of(rel):
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _vendor_dir_of(rel):
    """The repo-relative _vendor directory a path lies under, or None."""
    parts = rel.split("/")[:-1]
    if VENDOR_DIR_NAME not in parts:
        return None
    return "/".join(parts[:parts.index(VENDOR_DIR_NAME) + 1])


def _module_present(file_set, directory, name, namespace=False):
    """True if NAME.py or a regular package NAME/__init__.py is a walked file in directory; with namespace,
    also a directory NAME holding a walked .py file (a relative import searches only its own package)."""
    if not name:
        return False
    if _join(directory, name + ".py") in file_set or _join(directory, name + "/__init__.py") in file_set:
        return True
    prefix = _join(directory, name) + "/"
    return namespace and any(f.startswith(prefix) and f.endswith(".py") and "/" not in f[len(prefix):]
                             for f in file_set)


def _catches_import_error(handler):
    kind = handler.type
    if kind is None:
        return True
    kinds = kind.elts if isinstance(kind, ast.Tuple) else [kind]
    return any(isinstance(k, ast.Name) and k.id in IMPORT_ERRORS for k in kinds)


def _transparent(func):
    """func with every trailing .__call__ removed: X.__call__ reaches whatever X reaches."""
    while isinstance(func, ast.Attribute) and func.attr == "__call__":
        func = func.value
    return func


def _placeholder_free(node):
    """True for an f-string with no replacement field: a literal the gate does not evaluate, so a finding wherever
    the gate must read a name (an f-string with a replacement field is a computed value, reviewed at its site)."""
    return isinstance(node, ast.JoinedStr) and not any(isinstance(v, ast.FormattedValue) for v in node.values)


def _callee_name(func):
    """The name a call reaches its function by, after the .__call__ normalisation (_transparent, here and so at
    the getattr, type, vars and helper-class callees inside it): a name, an attribute, getattr (two or three
    arguments, no keyword, no starred argument) with a literal name, a subscript with a literal key, or help for
    an instance a HELPER_CLASSES call or type(help) makes; None otherwise."""
    func = _transparent(func)
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call) and _callee_name(func.func) == "getattr" and len(func.args) in (2, 3) and \
            not func.keywords and not any(isinstance(a, ast.Starred) for a in func.args) and \
            isinstance(func.args[1], ast.Constant):
        return func.args[1].value
    if isinstance(func, ast.Call) and _callee_name(func.func) == "type" and len(func.args) == 1 and \
            not func.keywords and _callee_name(func.args[0]) in HELPER_CLASSES.values():
        return "Helper"
    if isinstance(func, ast.Call) and _callee_name(func.func) in HELPER_CLASSES:
        return HELPER_CLASSES[_callee_name(func.func)]
    if isinstance(func, ast.Subscript) and isinstance(func.slice, ast.Constant):
        return func.slice.value
    return None


def _owner_module(node):
    """The last dotted component of the namespace an importer is reached through: pydoc from pydoc, from
    vars(pydoc), or from pydoc.__dict__, so a subscript of a module's own namespace keeps the module's identity."""
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        node = node.value
    elif isinstance(node, ast.Call) and _callee_name(node.func) == "vars" and len(node.args) == 1 and \
            not node.keywords:
        node = node.args[0]
    return ast.unparse(node).rsplit(".", 1)[-1]


def _importer(func):
    """The LITERAL_IMPORTERS row a call's function reaches by its callee name, through its module when the row
    names one (pydoc.doc, getattr(pydoc, ...), vars(pydoc)['doc'], pydoc.__dict__['doc']), or None. A trailing
    .__call__ is transparent: X.__call__ reaches whatever X reaches."""
    func = _transparent(func)
    row = IMPORTERS.get(_callee_name(func))
    if row is None or row[4] is None:
        return row
    owner = func.value if isinstance(func, ast.Attribute) else \
        func.args[0] if isinstance(func, ast.Call) and _callee_name(func.func) == "getattr" else \
        func.value if isinstance(func, ast.Subscript) else None
    return row if owner is not None and _owner_module(owner) == row[4] else None


def _literal_import_name(call, importer):
    """(name, problem) for an importer call (a LITERAL_IMPORTERS row) with no starred argument. name is the
    string-literal absolute dotted name it passes as its module-name parameter (its first argument or that
    parameter's keyword), read at level 0, else None. problem, else None, says which value the gate needs is a
    literal it cannot read with certainty: an f-string with no placeholder; a string that is not a dotted name of
    identifiers once the callee's own reading is applied (help strips surrounding whitespace, resolve_name reads
    the module before a module:object colon, a relative importer's name may begin with a dot); a help request
    that is one of HELP_LISTINGS once stripped; or, beside a string literal, a level that is not an integer
    literal. A computed name (any other expression) is neither:
    its site is reviewed. A relative request (an integer level above 0, or a relative importer's name that begins
    with a dot) and a file path are not resolved."""
    callee, keywords, level, kind, _owner = importer
    name = call.args[0] if call.args else None
    depth = call.args[level[0]] if level is not None and len(call.args) > level[0] else None
    for keyword in call.keywords:
        if keyword.arg in keywords and name is None:
            name = keyword.value
        if level is not None and keyword.arg == level[1]:
            depth = keyword.value
    if kind == "path":
        return None, None
    if _placeholder_free(name):
        return None, "its module name as an f-string with no placeholder, which the gate does not evaluate"
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return None, None
    if depth is not None and not (isinstance(depth, ast.Constant) and isinstance(depth.value, int)):
        return None, "a literal module name at the level %s, which is not an integer literal" % ast.unparse(depth)
    if depth is not None and depth.value != 0:
        return None, None
    spelled = name.value.strip() if callee == "help" else \
        name.value.split(":", 1)[0] if callee == "resolve_name" else name.value
    if callee == "help" and spelled in HELP_LISTINGS:
        return None, "the help request %r, which pydoc's Helper reads as a listing, not a module name" % name.value
    if kind == "relative" and spelled.startswith("."):
        return None, None
    if not all(part.isidentifier() for part in spelled.split(".")):
        return None, "the literal module name %r, which is not a dotted name of identifiers" % name.value
    return spelled, None


def unreadable_calls(tree):
    """(line, message) for every call the gate cannot read statically, each a finding: an importer call passing
    a starred or double-starred argument (its module name cannot be read), or a literal module name or level it
    cannot read with certainty (_literal_import_name); and, at the callee after the same .__call__ normalisation the
    importer reader applies, a call of a getattr passing a starred or double-starred argument or a keyword (the
    function it calls cannot be read), or a call through a getattr name or a subscript key written as an f-string
    with no placeholder."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _transparent(node.func)
        importer = _importer(node.func)
        text = ast.unparse(node)
        if importer is not None:
            starred = any(isinstance(a, ast.Starred) for a in node.args)
            unpacked = any(k.arg is None for k in node.keywords)
            if starred or unpacked:
                out.append((node.lineno, node.col_offset, "importer call %s passes a starred or double-starred "
                            "argument, so the name it imports cannot be read statically: spell each argument out"
                            % text))
                continue
            problem = _literal_import_name(node, importer)[1]
            if problem is not None:
                out.append((node.lineno, node.col_offset, "importer call %s passes %s, so what it imports cannot "
                            "be read statically: pass the module's dotted name as a plain string literal at level 0"
                            % (text, problem)))
        elif isinstance(callee, ast.Call) and _callee_name(callee.func) == "getattr" and \
                (any(isinstance(a, ast.Starred) for a in callee.args) or callee.keywords):
            out.append((node.lineno, node.col_offset, "call of a getattr %s passes a starred or double-starred "
                        "argument or a keyword, so the function it calls cannot be read statically: spell each "
                        "argument out" % text))
        elif isinstance(callee, ast.Call) and _callee_name(callee.func) == "getattr" and len(callee.args) > 1 and \
                _placeholder_free(callee.args[1]) or isinstance(callee, ast.Subscript) and \
                _placeholder_free(callee.slice):
            out.append((node.lineno, node.col_offset, "call %s reaches its function by an f-string with no "
                        "placeholder, which the gate does not evaluate: write a plain string literal" % text))
    return [(line, message) for line, _column, message in sorted(out)]


def extract_imports(tree):
    """Import records (lineno, module, level, names, guarded) for every import statement in the file, and for
    every importer call (LITERAL_IMPORTERS) with a string-literal absolute name (resolved at an allowlisted site
    too)."""
    records = []

    def visit(node, guarded):
        if isinstance(node, ast.Import):
            for alias in node.names:
                records.append((node.lineno, alias.name, 0, (), guarded))
        elif isinstance(node, ast.ImportFrom):
            records.append((node.lineno, node.module or "", node.level,
                            tuple(a.name for a in node.names), guarded))
        elif isinstance(node, ast.Call) and _importer(node.func) is not None:
            name = _literal_import_name(node, _importer(node.func))[0]
            if name is not None:
                records.append((node.lineno, name, 0, (), guarded))
        if isinstance(node, (ast.Try, ast.TryStar)):
            body_guarded = guarded or any(_catches_import_error(h) for h in node.handlers)
            for child in node.body:
                visit(child, body_guarded)
            for child in node.handlers + node.orelse + node.finalbody:
                visit(child, guarded)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return records


def watched_names(modules=IMPORT_SYSTEM_MODULES, inert=INERT_IMPORT_SYSTEM_MEMBERS):
    """(names, members) the dynamic-import rule watches, read from the import system's modules themselves:
    names are DYNAMIC_IMPORT_NAMES and every undotted name sys.modules gives one of the modules; members are the
    last component of every dotted such name and every function, class or module defined in one of them, less
    the inert members. Fail-closed if a module does not load or an inert name is not a member."""
    loaded = []
    for name in modules:
        try:
            loaded.append(importlib.import_module(name))
        except ImportError as exc:
            raise CannotEvaluate("import-system module %s does not load (%s): the watched set has no other source"
                                 % (name, exc))
    aliases = set(k for k, v in list(sys.modules.items()) if any(v is m for m in loaded))
    members = set(a.rsplit(".", 1)[1] for a in aliases if "." in a)
    for module in loaded:
        for key, value in vars(module).items():
            if isinstance(value, (types.FunctionType, types.BuiltinFunctionType, type, types.ModuleType)) and \
                    (getattr(value, "__module__", None) in aliases or getattr(value, "__name__", None) in aliases):
                members.add(key)
    stale = sorted(n for n, _reason in inert if n not in members)
    if stale:
        raise CannotEvaluate("INERT_IMPORT_SYSTEM_MEMBERS names %s, which is not a member of the import system's "
                             "modules on this interpreter" % stale[0])
    names = DYNAMIC_IMPORT_NAMES | frozenset(a for a in aliases if "." not in a)
    return names, frozenset(members) - names - frozenset(n for n, _reason in inert)


def _scoped_children(node, scope, inner=None, body=False):
    """(child, identity scope, body position, descend) for each child of node. A def, class or lambda runs its
    body, and binds its parameters, in a scope of its own, inner (its identity, scope_identities); everything else
    it holds (decorators, defaults, annotations, bases, keywords, type parameters with their bounds, constraints
    and defaults) is read in the enclosing scope, where the definition runs, and that scope names its row. A
    parameter is a leaf there: its annotation is pushed apart, in the enclosing scope. A comprehension, a
    generator expression and a type alias are no scopes here, so what they hold is listed under the scope around
    them. The body position is the one bit the exemption reads (_kept): True only on the chain from a def's or
    lambda's body (its parameters among them) down through plain statements and expressions, and False from every
    construct the exemption does not enter, at any depth below it, until another def or lambda body (whose own
    table proves its own locals) starts a chain of its own: a list, set or dict comprehension and a generator
    expression (all of it, the first iterable included), a type parameter list with its bounds, constraints and
    defaults, every parameter and return annotation (the floor version evaluates them lazily, in annotation
    scopes), a type alias's value, a generic class's bases and keywords (the floor version reads them against
    the definition's type-parameter scope), a generic def's defaults (a stated conservative policy, not the
    floor version's scoping: it evaluates a def's defaults in the enclosing scope, generic or not, so the
    False position here can only over-report, and the generic-default vectors pin the kept sites), and a
    class body. A def's or class's decorators, a non-generic def's defaults and a non-generic class's bases
    and keywords run as plain expressions where the definition runs, so they keep the position they are read
    in; so does the annotation of an annotated assignment, which in a function body the floor version never
    evaluates at all."""
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return [(child, scope, False, True) for child in ast.iter_child_nodes(node)]
    if isinstance(node, ast.TypeAlias):
        out = [(node.name, scope, body, True)]
        out.extend((child, scope, False, True) for child in node.type_params)
        out.append((node.value, scope, False, True))
        return out
    if not isinstance(node, SCOPE_NODES):
        return [(child, scope, body, True) for child in ast.iter_child_nodes(node)]
    generic = bool(getattr(node, "type_params", None))
    out = []
    for field, value in ast.iter_fields(node):
        for child in value if isinstance(value, list) else (value,):
            if field == "body":
                out.append((child, inner, not isinstance(node, ast.ClassDef), True))
            elif field == "type_params":
                out.append((child, scope, False, True))
            elif field == "returns" and child is not None:
                out.append((child, scope, False, True))
            elif field in ("bases", "keywords"):
                out.append((child, scope, body and not generic, True))
            elif isinstance(child, ast.arguments):
                for arg in child.posonlyargs + child.args + [child.vararg] + child.kwonlyargs + [child.kwarg]:
                    if arg is not None:
                        out.append((arg, inner, True, False))
                        if arg.annotation is not None:
                            out.append((arg.annotation, scope, False, True))
                out.extend((d, scope, body and not generic, True)
                           for d in child.defaults + child.kw_defaults if d is not None)
            elif isinstance(child, ast.AST):
                out.append((child, scope, body, True))
    return out


def _scope_name(node):
    """A scope's own name: a def's name, class and a class's name, or <lambda>."""
    if isinstance(node, ast.Lambda):
        return "<lambda>"
    return ("class " if isinstance(node, ast.ClassDef) else "") + node.name


def scope_identities(tree):
    """{id(node): scope} for the module (<module>) and for each def, class and lambda in tree, one per lexical
    scope instance: the scopes around it and its own name (_scope_name) joined by dots, its own name followed by #K/N
    when its enclosing scope holds N > 1 scopes of that name (sibling lambdas, two defs of one name under an if and its
    else), K its ordinal in source order among them, so adding or removing a sibling of that name changes the identity
    of every sibling of that name. A scope's enclosing scope is the one that runs its definition (_scoped_children); a
    comprehension or generator expression is not a scope here. No line or column is part of it; the source order only
    numbers same-named siblings."""
    held = dict()
    stack = [(tree, tree, False, True)]
    while stack:
        node, scope, body, descend = stack.pop()
        if isinstance(node, SCOPE_NODES):
            held.setdefault(id(scope), []).append(node)
        if descend:
            stack.extend(_scoped_children(node, scope, node, body))
    identities = {id(tree): MODULE_SCOPE}
    order = [tree]
    for parent in order:
        inner = sorted(held.get(id(parent), ()), key=lambda n: (n.lineno, n.col_offset))
        counts, seen = dict(), dict()
        for node in inner:
            counts[_scope_name(node)] = counts.get(_scope_name(node), 0) + 1
        for node in inner:
            name = _scope_name(node)
            seen[name] = seen.get(name, 0) + 1
            if counts[name] > 1:
                name += "#%d/%d" % (seen[name], counts[name])
            identities[id(node)] = name if parent is tree else identities[id(parent)] + "." + name
            order.append(node)
    return identities


# The (node type, field) pairs that hold a watched name as a scoped variable: a read, a binding or a deletion
# the compile-time resolution below reads (_kept). An alias, a def's or class's name, an attribute, a keyword's
# name, a global or nonlocal declaration, a type parameter and a literal are not among them: none can be the
# occurrence of a plain local variable, so none is resolved and each stays a site.
RESOLVED_NAME_FIELDS = ((ast.Name, "id"), (ast.arg, "arg"), (ast.ExceptHandler, "name"), (ast.MatchAs, "name"),
                        (ast.MatchStar, "name"), (ast.MatchMapping, "rest"))


def _comprehension_bound(tree, identities):
    """{def or lambda identity: every name under a for-target of a comprehension or generator expression anywhere
    in that function, nested scopes included}: no reference to such a name is ever exempt anywhere in that
    function. A conservative keep, not a condition the floor version's scoping needs: every reference inside a
    comprehension or generator expression is already kept by its position (_scoped_children), an inlined
    comprehension's target leaks nowhere, so a body read outside them resolves to the function's own binding
    (the floor version inlines a list, set or dict comprehension into the function around it, and the
    function's own symbol for a target that shadows its local reads as a plain local there, the merged symbol
    carrying no comprehension flag), and where only a comprehension binds the name the table reads a body
    reference as global, kept by _plain_local. The keep can only over-report, and its vectors pin the kept
    sites; a name a target merely reads (a subscript's base or index) is swept in too, an over-keep, never an
    exemption."""
    out = dict()
    stack = [(tree, ())]
    while stack:
        node, held = stack.pop()
        if isinstance(node, SCOPE_NODES) and not isinstance(node, ast.ClassDef):
            held = held + (identities[id(node)],)
        if isinstance(node, ast.comprehension):
            for name in set(n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)):
                for identity in held:
                    out.setdefault(identity, set()).add(name)
        stack.extend((child, held) for child in ast.iter_child_nodes(node))
    return out


def _starred_import(tree):
    """True when the file holds a * import anywhere: the names a * import binds are read at run time, so every
    watched-name reference in such a file keeps its site."""
    return any(isinstance(node, ast.ImportFrom) and any(held.name == "*" for held in node.names)
               for node in ast.walk(tree))


def _scope_tables(tree, source, identities):
    """({def or lambda identity: its own compile-time symbol table}, tainted names): for every def and lambda of
    tree the one function-kind table that matches by name (a lambda as lambda) and line. The tables are Python's
    own scope analysis (the standard library symtable module over the same source); the walk that puts each
    reference in its syntactic position is this gate's own (_scoped_children), and the site vectors pin, shape by
    shape, that a position it marks as a def or lambda body resolves in that function's own table. A scope more
    than one table could match (two lambdas on one line; a def named genexpr beside a generator expression on
    its line, whose table is also function-kind and named genexpr; or a def named listcomp beside a class-body
    annotation's comprehension on its line, a comprehension in an annotation scope that can see a class body
    not being inlined, so its table too is function-kind and named listcomp) is left unmatched, and every
    reference in an unmatched scope keeps its site. A tainted name is one some scope binds by an import
    statement under a global
    or nonlocal declaration (run that import and an enclosing def's local, or the module's name, becomes the
    imported module), so no reference to it is ever suppressed. A source symtable cannot read gives no tables:
    every reference then keeps its site."""
    try:
        top = symtable.symtable(source, "<dynamic-import>", "exec")
    except (SyntaxError, ValueError):
        return dict(), frozenset()
    functions, tainted = dict(), set()
    stack = [top]
    while stack:
        table = stack.pop()
        for sym in table.get_symbols():
            if sym.is_imported() and not sym.is_local():
                tainted.add(sym.get_name())
        if table.get_type() == "function":
            functions.setdefault((table.get_name(), table.get_lineno()), []).append(table)
        stack.extend(table.get_children())
    out = dict()
    for node in ast.walk(tree):
        if isinstance(node, SCOPE_NODES) and not isinstance(node, ast.ClassDef):
            held = functions.get(("lambda" if isinstance(node, ast.Lambda) else node.name, node.lineno), ())
            if len(held) == 1:
                out[identities[id(node)]] = held[0]
    return out, frozenset(tainted)


def _plain_local(sym, tainted):
    """True when sym is a plain compile-time local of the def or lambda whose own table holds it: Python's own
    analysis binds it there (an assignment, deletion, annotation or other binding statement, or a parameter), no
    import statement binds it, and no import under a global or nonlocal declaration can rebind it (tainted). A
    free variable (a closure read, with a nonlocal declared name among them), a global (declared or implicit) and
    a name the table does not bind are not its plain locals: the reference keeps its site, whatever binds it."""
    if sym.get_name() in tainted:
        return False
    if sym.is_imported():
        return False
    if sym.is_free():
        return False
    return sym.is_local()


def _kept(name, scope, body, tables, tainted, comp_bound, starred):
    """True when the reference to the watched name stays a site: a dunder name (binding __import__,
    __builtins__ or __path__ rebinds the import machinery itself, so every occurrence is reviewed), any
    reference in a file with a * import, any reference whose syntactic position is not a def or lambda body
    itself (body, _scoped_children: a comprehension, a generator expression, an annotation, a type-parameter
    scope, a type alias's value, a generic def's defaults, a class body and the module level keep every site),
    a scope no symbol table
    matches (a source symtable cannot read, and every ambiguity, among them), a name a comprehension or
    generator expression anywhere in that function binds as a target (comp_bound), and a name its own table
    does not hold or _plain_local does not prove. False only for the one exemption, a binding rule and not a
    value proof: the reference sits in a def or lambda body whose own table proves the name that function's
    plain local, so the name resolves to that variable's binding, never to a module-level or builtin one. What
    the variable holds is not proven: such a local can still hold import machinery obtained another way (a
    parameter, an eval or another dynamic call), and that way is watched only where the gate watches it, an
    accepted residual the rule text discloses and the site vectors pin."""
    if name.startswith("__") and name.endswith("__"):
        return True
    if starred:
        return True
    if not body:
        return True
    table = tables.get(scope)
    if table is None:
        return True
    if name in comp_bound.get(scope, ()):
        return True
    try:
        sym = table.lookup(name)
    except KeyError:
        return True
    return not _plain_local(sym, tainted)


def _site_reference(node, parents):
    """The node a watched name is reviewed as: an alias's import statement, or the expression climbed through
    the attribute access, call and subscript the name is the target of. A literal (inside an f-string, that
    f-string) first climbs into the call it is directly an argument of (or whose keyword it is), or the subscript it
    is directly the key of, then climbs on like a name, so an edit that turns that call or subscript into a called
    importer is a finding (a def, class or parameter is its own node; an expression built from a literal is not)."""
    if isinstance(node, ast.alias):
        return parents[id(node)]
    if isinstance(node, ast.Constant):
        parent = parents.get(id(node))
        while isinstance(parent, (ast.FormattedValue, ast.JoinedStr)):
            node, parent = parent, parents.get(id(parent))
        if isinstance(parent, ast.keyword):
            parent = parents.get(id(parent))
            if isinstance(parent, ast.Call):
                node = parent
        elif isinstance(parent, ast.Call) and node in parent.args:
            node = parent
        elif isinstance(parent, ast.Subscript) and parent.slice is node:
            node = parent
    while True:
        parent = parents.get(id(node))
        if not (isinstance(parent, ast.Attribute) and parent.value is node or
                isinstance(parent, ast.Call) and parent.func is node or
                isinstance(parent, ast.Subscript) and parent.value is node):
            return node
        node = parent


def reference_text(node):
    """(first line, column, last line, end column, text) of a site's reference node: a def, class or except clause
    by its header (from its first character to its body's first, as def, class or except as and its name),
    anything else by its whole span and its unparsed source. Columns are ast's: UTF-8 bytes, from 0. The span only
    orders the references of one text in a scope and gives a finding its line; no row lists it."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler)):
        head = dict(((ast.FunctionDef, "def "), (ast.AsyncFunctionDef, "async def "), (ast.ClassDef, "class "),
                     (ast.ExceptHandler, "except as ")))[type(node)]
        return node.lineno, node.col_offset, node.body[0].lineno, node.body[0].col_offset, head + node.name
    return node.lineno, node.col_offset, node.end_lineno, node.end_col_offset, ast.unparse(node)


def reference_key(ordinal, text):
    """The reference a DYNAMIC_IMPORT_SITES row lists: #ORDINAL: TEXT, its node's unparsed text and its ordinal
    among the references of that text in its scope, in source order (no line or column)."""
    return "#%d: %s" % (ordinal, text)


def dynamic_sites(tree, source, names, members):
    """(reference, enclosing scope, watched names, called, line) for every dynamic-import site in the file: each
    reference node holding an occurrence of a watched name, as an identifier anywhere in the tree, or as a dotted
    component of a string or bytes literal that is a dotted identifier chain; a member counts as an attribute
    name or a literal component only. called is true when the reference is a call. No alias is resolved: an
    alias is itself a site, and whatever later uses the name it binds is not. line is the reference's first line,
    reported with a finding and no part of its key. The enclosing scope is its lexical scope instance
    (scope_identities); an identity two scopes share cannot tell them apart, so it is refused, never merged.
    A name read or bound as a scoped variable (RESOLVED_NAME_FIELDS) is no site only under the one exemption
    (_kept): the reference sits in a def or lambda body itself (_scoped_children's body position), that
    function's own symbol table proves the name its plain local (_plain_local), and no comprehension or
    generator expression anywhere in that function binds the name as a target (_comprehension_bound); the
    binding is then that variable's, never a module-level or builtin one (a binding rule, not a value proof).
    Every other occurrence stays a site: a free (closure) read, every reference in a comprehension, generator
    expression, parameter or return annotation, type-parameter scope or type alias's value (the annotation of
    an annotated assignment is no such position: in a function body the floor version never evaluates it, and
    a watched name there is read like any other expression of the scope that holds the statement, the rule
    text's stated exception), a generic def's defaults (a stated conservative policy: the floor version
    evaluates them in the enclosing scope, so the keep can only over-report), a module or class-body
    resolution, an import binding, a global or nonlocal declared name, a type-parameter binding, a dunder,
    every reference in a file with a * import, and every ambiguity."""
    parents = dict()
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    identities, holders = scope_identities(tree), dict()
    for identity in identities.values():
        holders[identity] = holders.get(identity, 0) + 1
    shared = sorted(identity for identity, count in holders.items() if count > 1)
    if shared:
        raise CannotEvaluate("scope %r names %d lexical scopes, so no row could tell them apart; the gate refuses "
                             "rather than merge them" % (shared[0], holders[shared[0]]))
    tables, tainted = _scope_tables(tree, source, identities)
    comp_bound, starred = _comprehension_bound(tree, identities), _starred_import(tree)
    found = dict()
    stack = [(tree, MODULE_SCOPE, False, True)]
    while stack:
        node, scope, body, descend = stack.pop()
        for field, value in ast.iter_fields(node):
            for item in value if isinstance(value, list) else (value,):
                if isinstance(item, bytes):
                    item = item.decode("latin-1")
                if not isinstance(item, str) or DOTTED_NAME_RE.fullmatch(item) is None:
                    continue
                member = isinstance(node, ast.Constant) or (isinstance(node, ast.Attribute) and field == "attr")
                hits = set(part for part in item.split(".") if part in names or (member and part in members))
                if isinstance(node, ast.keyword):
                    hits -= KEYWORD_EXEMPT_NAMES
                if hits and any(isinstance(node, kind) and field == named for kind, named in RESOLVED_NAME_FIELDS):
                    hits = set(hit for hit in hits if _kept(hit, scope, body, tables, tainted, comp_bound,
                                                            starred))
                if hits:
                    reference = _site_reference(node, parents)
                    found.setdefault(id(reference), (reference, scope, set()))[2].update(hits)
        if descend:
            stack.extend(_scoped_children(node, scope, identities.get(id(node)), body))
    out = []
    for reference, scope, hits in found.values():
        span = reference_text(reference)
        out.append((span[:4], scope, span[4], " ".join(sorted(hits)), isinstance(reference, ast.Call)))
    ordinals, sites = dict(), []
    for span, scope, text, hits, called in sorted(out):
        key = (scope, text)
        ordinals[key] = ordinals.get(key, 0) + 1
        sites.append((reference_key(ordinals[key], text), scope, hits, called, span[0]))
    return sites


def check_dynamic_sites(sites, allowlist):
    """dynamic-import findings: every site (rel, reference, scope, names, called, line) no allowlist row lists
    exactly (its file, its enclosing scope and its reference: ordinal and text), every site whose permission key
    (file, scope, ordinal and text) another site shares, listed or not (a backstop: scope identities and ordinals
    make every key dynamic_sites gives unique), and every row that gives no reason, lists no reference or a
    malformed one, repeats a reference, or lists a reference that does not occur there."""
    findings = []
    listed = dict()
    for rel, function, references, reason in allowlist:
        problem = None
        if not reason.strip():
            problem = "gives no reason"
        elif not references or not all(isinstance(r, str) and REFERENCE_RE.fullmatch(r) for r in references):
            problem = "lists no reference, or one not of the form #ORDINAL: TEXT (no line or column)"
        elif any((rel, function, r) in listed for r in references) or len(set(references)) != len(references):
            problem = "repeats a reference"
        if problem is not None:
            findings.append(("dynamic-import", rel, 0, "DYNAMIC_IMPORT_SITES row (%r, %r) %s" % (rel, function,
                                                                                               problem)))
            continue
        for reference in references:
            listed[(rel, function, reference)] = False
    nodes = dict()
    for rel, reference, function, _names, _called, _line in sites:
        nodes[(rel, function, reference)] = nodes.get((rel, function, reference), 0) + 1
    for rel, reference, function, names, called, line in sites:
        if nodes[(rel, function, reference)] > 1:
            listed[(rel, function, reference)] = True
            findings.append(("dynamic-import", rel, line,
                             "%s in %s: %d nodes share the permission key %r, so no row can name one of them and "
                             "none is allowed" % (names, function, nodes[(rel, function, reference)], reference)))
            continue
        if (rel, function, reference) in listed:
            listed[(rel, function, reference)] = True
            continue
        findings.append(("dynamic-import", rel, line,
                         "%s in %s is a dynamic-import site no DYNAMIC_IMPORT_SITES row lists (dynamic imports are "
                         "not resolved, and a row allows one exact node: review it and list %r under (%r, "
                         "%r) with a reason, or remove it)%s"
                         % (names, function, reference, rel, function,
                            "" if called else "; the reference is not called in place, so what it is returned, "
                            "stored or passed to runs without a site of its own")))
    for (rel, function, reference), used in sorted(listed.items()):
        if not used:
            findings.append(("dynamic-import", rel, 0, "DYNAMIC_IMPORT_SITES row (%r, %r) is stale: no reference %r "
                             "occurs there (an edited reference, or one whose scope or ordinal among the references "
                             "of its text there changed, is listed again, and reviewed again)"
                             % (rel, function, reference)))
    return findings


def _spdx_ids(expression):
    """The identifiers of an SPDX expression the gate accepts (one identifier from SPDX_LICENCE_MARKERS, or
    several joined by AND or by OR), or None."""
    words = expression.split(" ")
    ids, operators = words[0::2], set(words[1::2])
    known = dict(SPDX_LICENCE_MARKERS)
    if len(words) % 2 == 0 or len(operators) > 1 or not operators <= frozenset(("AND", "OR")):
        return None
    if not all(i in known for i in ids):
        return None
    return ids


def _normalised(text):
    return " ".join(text.split()).casefold()


def _load_vendor(root, files):
    """Validate every _vendor directory. Returns (records, findings): records maps each recorded root
    (repo-relative, trailing slash) to its top-level package name."""
    findings = []
    records = dict()
    file_set = set(rel for rel, _ in files)
    vendor_dirs = sorted(set(v for v in (_vendor_dir_of(rel) for rel in file_set) if v is not None))
    for vdir in vendor_dirs:
        record_files = sorted(rel for rel in file_set if rel.startswith(vdir + "/") and
                              rel.count("/") == vdir.count("/") + 1 and rel.endswith(PROVENANCE_SUFFIX))
        for rec_rel in record_files:
            data = _toml(Path(root) / rec_rel, rec_rel)
            tables = dict()
            for key in ("package", "license", "vendored_tree"):
                tables[key] = data.get(key) if isinstance(data.get(key), dict) else dict()
            missing = []
            for dotted in ("package.name", "package.version", "package.purl", "license.spdx",
                           "license.vendored_path", "vendored_tree.root"):
                table, key = dotted.split(".")
                value = tables[table].get(key)
                if not (isinstance(value, str) and value.strip()):
                    missing.append(dotted)
            rroot = tables["vendored_tree"].get("root")
            if not isinstance(rroot, str) or not rroot.endswith("/") or not rroot.startswith(vdir + "/") or \
                    rroot.count("/") != vdir.count("/") + 2 or ".." in rroot.split("/"):
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "vendored_tree.root %r is not one directory directly inside %s/" % (rroot, vdir)))
                continue
            # The claimed root resolves imports even when the record fails below, so a defective record is
            # judged by this rule alone, never also as unresolved imports.
            records[rroot] = rroot.rstrip("/").rsplit("/", 1)[1]
            if missing:
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "provenance record lacks %s (no identifiable licence or source)"
                                 % ", ".join(missing)))
                continue
            package = tables["package"]
            if re.sub(r"[-.]", "_", package["name"]).casefold() != records[rroot].casefold():
                findings.append(("vendor-provenance", rec_rel, 0, "vendored_tree.root %s is not named for package %r"
                                 % (rroot, package["name"])))
            purl_re = r"pkg:[a-z][a-z0-9.+-]*/(?:[^/@\s]+/)*%s@%s" % (re.escape(package["name"]),
                                                                      re.escape(package["version"]))
            if re.fullmatch(purl_re, package["purl"]) is None:
                findings.append(("vendor-provenance", rec_rel, 0, "package.purl %r does not name %s@%s"
                                 % (package["purl"], package["name"], package["version"])))
            ids = _spdx_ids(tables["license"]["spdx"])
            if ids is None:
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "license.spdx %r is not one identifier from SPDX_LICENCE_MARKERS, or several "
                                 "joined by AND or by OR" % tables["license"]["spdx"]))
            licence = tables["license"]["vendored_path"]
            man_rel = rec_rel[:-len(PROVENANCE_SUFFIX)] + MANIFEST_SUFFIX
            if man_rel not in file_set:
                findings.append(("vendor-provenance", rec_rel, 0, "per-file manifest %s is missing" % man_rel))
                continue
            rows = dict()
            for number, line in enumerate(_text(Path(root) / man_rel, man_rel).splitlines(), 1):
                match = MANIFEST_ROW_RE.fullmatch(line)
                if match is None or match.group("path") in rows:
                    raise CannotEvaluate("%s:%d: not one sha256sum row (64 hex, two spaces, a unique path)"
                                         % (man_rel, number))
                rows[match.group("path")] = match.group("digest")
            for path_rel, digest in sorted(rows.items()):
                if path_rel not in file_set:
                    findings.append(("vendor-provenance", man_rel, 0, "listed file %s is missing" % path_rel))
                elif hashlib.sha256(_regular_bytes(Path(root) / path_rel, path_rel)).hexdigest() != digest:
                    findings.append(("vendor-provenance", path_rel, 0,
                                     "bytes differ from the sha256 recorded in %s" % man_rel))
            if not licence.startswith(vdir + "/"):
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "licence text %s does not lie under %s/" % (licence, vdir)))
            elif licence not in rows:
                findings.append(("vendor-provenance", rec_rel, 0,
                                 "licence text %s is not listed in %s" % (licence, man_rel)))
            elif ids is not None and licence in file_set:
                text = _normalised(_text(Path(root) / licence, licence))
                for ident, markers in SPDX_LICENCE_MARKERS:
                    absent = [m for m in markers if _normalised(m) not in text]
                    if ident in ids and absent:
                        findings.append(("vendor-provenance", licence, 0,
                                         "licence text lacks the %s marker sentence %r" % (ident, absent[0])))
            for rel in sorted(file_set):
                if rel.startswith(rroot) and rel not in rows:
                    findings.append(("vendor-provenance", rel, 0,
                                     "vendored file has no recorded provenance (not listed in %s)" % man_rel))
        for rel in sorted(file_set):
            if rel.startswith(vdir + "/") and rel.endswith(".py") and not any(rel.startswith(r) for r in records):
                findings.append(("vendor-provenance", rel, 0,
                                 "vendored Python file lies under no recorded provenance root"))
    return records, findings


def _vendored_root_of(rel, records):
    for rroot in records:
        if rel.startswith(rroot):
            return rroot
    return None


def _dotted(rel, rroot):
    """The dotted module name of a file under a recorded vendored root (marko/ext/toc.py is marko.ext.toc)."""
    parts = rel[len(rroot.rstrip("/").rsplit("/", 1)[0]) + 1:].split("/")
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _relative_base(root, own, level):
    """The base directory of a relative import, as a repo-relative posix path ("" for the root; it may start
    with ..)."""
    base = own
    for _ in range(level - 1):
        base = base.parent
    rel = os.path.relpath(base, root).replace(os.sep, "/")
    return base, ("" if rel == "." else rel)


def _search_roots(rel, file_set, extra):
    """The ordered, distinct search roots of one file (see the docstring)."""
    vdir = _vendor_dir_of(rel)
    if vdir is not None:
        return [vdir]
    own = _dir_of(rel)
    roots = [] if _join(own, "__init__.py") in file_set else [own]
    for sroot in extra.get(rel, ()):
        if sroot not in roots:
            roots.append(sroot)
    return roots


def _file_bindings(tree):
    """Every binding of every name anywhere in the file, in any scope: name -> [binding], a binding being ("assign",
    value) for a module-level NAME = value statement with one plain target, ("import", module) for import MODULE,
    ("from", module, name) for from MODULE import NAME, and ("other",) for every other form (an annotated, augmented,
    chained or destructuring assignment, a for, with, except, match or comprehension target, a walrus, del, global,
    nonlocal, an aliased import, a def, class, parameter or type parameter name, an assignment in a nested block or
    scope). A star import binds names the gate cannot list: it is held under "*"."""
    bindings = dict()
    plain = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            plain.add(id(node.targets[0]))
            bindings.setdefault(node.targets[0].id, []).append(("assign", node.value))
    for node in ast.walk(tree):
        names = ()
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            if id(node) not in plain:
                names = (node.id,)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*" or alias.asname is not None:
                    bindings.setdefault(alias.asname or "*", []).append(("other",))
                elif isinstance(node, ast.Import):
                    bindings.setdefault(alias.name.split(".")[0], []).append(("import", alias.name))
                else:
                    bindings.setdefault(alias.name, []).append(("from", "." * node.level + (node.module or ""),
                                                                alias.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = (node.name,)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names = node.names
        elif isinstance(node, ast.arg):
            names = (node.arg,)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar, ast.TypeVar, ast.ParamSpec,
                               ast.TypeVarTuple)):
            names = (node.name,) if node.name else ()
        elif isinstance(node, ast.MatchMapping):
            names = (node.rest,) if node.rest else ()
        for name in names:
            bindings.setdefault(name, []).append(("other",))
    return bindings


def _bindings_of(bindings, name):
    """Every binding of name in the file (_file_bindings), or None where a star import may bind it unseen."""
    return None if "*" in bindings else bindings.get(name, [])


def _assigned(bindings, name):
    """The value of name where its one binding in the whole file is a plain module-level NAME = value, else None."""
    found = _bindings_of(bindings, name)
    return found[0][1] if found and len(found) == 1 and found[0][0] == "assign" else None


def _imported(bindings, name):
    """Whether name's one binding in the whole file is the import INSERT_IMPORTS gives it."""
    found = _bindings_of(bindings, name)
    return found is not None and len(found) == 1 and found[0] == INSERT_IMPORTS[name]


def _unbound(bindings, name):
    """Whether the file binds name nowhere (__file__, and the builtin str)."""
    return _bindings_of(bindings, name) == []


def _is_call(node, func, count):
    """Whether node calls exactly func (its spelling) with count positional arguments and no keyword."""
    return isinstance(node, ast.Call) and not node.keywords and len(node.args) == count and \
        ast.unparse(node.func) == func


def _insert_part(node):
    """The path component a string literal is when the gate reads it as one (INSERT_PART_RE, neither . nor ..),
    else None: an absolute, empty, separator-holding or trailing-slash component never occurs."""
    if isinstance(node, ast.Constant) and type(node.value) is str and node.value not in (".", "..") and \
            INSERT_PART_RE.fullmatch(node.value):
        return node.value
    return None


def _file_parents(node, rel, bindings):
    """The repo-relative components of Path(__file__).resolve() followed by .parent (1 to MAX_PARENTS_INDEX + 1
    times) or by one .parents[N] (N an int literal, 0 to MAX_PARENTS_INDEX), or None for any other shape and for a
    directory above the repository root; no index is expanded."""
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
        index = node.slice.value if isinstance(node.slice, ast.Constant) else None
        if type(index) is not int:
            return None
        up, node = index + 1, node.value.value
    else:
        up = 0
        while isinstance(node, ast.Attribute) and node.attr == "parent":
            up, node = up + 1, node.value
    if not 1 <= up <= MAX_PARENTS_INDEX + 1:
        return None
    if not (_is_call(node, "Path(__file__).resolve", 0) and _is_call(node.func.value, "Path", 1) and
            isinstance(node.func.value.args[0], ast.Name) and _imported(bindings, "Path") and
            _unbound(bindings, "__file__")):
        return None
    parts = rel.split("/")
    return None if up > len(parts) else parts[:len(parts) - up]


def _path_chain(node, rel, bindings, named):
    """The repo-relative components of a pathlib value in the read shapes (see the docstring), or None: a start
    (_file_parents; repo_root(), the repository root; or, where named (the count of names still to follow, at most
    MAX_NAME_HOPS), a name bound to a pathlib chain read with one name fewer, so a chain starting at no name once
    none are left) then / with a literal component (_insert_part) any number of times."""
    parts = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        part = _insert_part(node.right)
        if part is None:
            return None
        parts.insert(0, part)
        node = node.left
    if isinstance(node, ast.Name) and named:
        value = _assigned(bindings, node.id)
        start = None if value is None else _path_chain(value, rel, bindings, named - 1)
    elif _is_call(node, "repo_root", 0) and _imported(bindings, "repo_root"):
        start = []
    else:
        start = _file_parents(node, rel, bindings)
    return None if start is None else start + parts


def _os_path_join(node, rel, bindings):
    """The repo-relative components of os.path.join(os.path.dirname(os.path.abspath(__file__)), LITERAL, ...), the
    one os.path nesting read, or None."""
    if not (isinstance(node, ast.Call) and not node.keywords and len(node.args) > 1 and
            ast.unparse(node.func) == "os.path.join" and _imported(bindings, "os")):
        return None
    head = node.args[0]
    if not (_is_call(head, "os.path.dirname", 1) and _is_call(head.args[0], "os.path.abspath", 1) and
            ast.unparse(head.args[0].args[0]) == "__file__" and _unbound(bindings, "__file__")):
        return None
    parts = [_insert_part(part) for part in node.args[1:]]
    return None if None in parts else rel.split("/")[:-1] + parts


def _str_shape(node, rel, bindings, named=True):
    """The repo-relative components a str-valued insertion argument spells in the read shapes (see the docstring):
    str() around a pathlib chain (_path_chain), the os.path nesting (_os_path_join), or, where named, a name bound to
    one of them; None for any other expression, an unwrapped pathlib value among them. Never executed."""
    if isinstance(node, ast.Name) and named:
        value = _assigned(bindings, node.id)
        return None if value is None else _str_shape(value, rel, bindings, False)
    if _is_call(node, "str", 1) and _unbound(bindings, "str"):
        return _path_chain(node.args[0], rel, bindings, MAX_NAME_HOPS if named else 0)
    return _os_path_join(node, rel, bindings)


def _insertion_path(node, rel, bindings):
    """The repo-relative path ('.' the repository root) a sys.path insertion argument spells, or None (_str_shape)."""
    parts = _str_shape(node, rel, bindings)
    return None if parts is None else "/".join(parts) or "."


def _insertion_roots(tree, rel):
    """(roots, unread) over every sys.path.insert or sys.path.append call the file makes: roots the repo-relative
    paths of the argument expressions the gate reads (_insertion_path), unread the spelling of each it cannot."""
    bindings = _file_bindings(tree)
    roots, unread = set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                node.func.attr in ("insert", "append") and isinstance(node.func.value, ast.Attribute) and \
                node.func.value.attr == "path" and isinstance(node.func.value.value, ast.Name) and \
                node.func.value.value.id == "sys":
            shape = len(node.args) == (2 if node.func.attr == "insert" else 1) and not node.keywords
            target = _insertion_path(node.args[-1], rel, bindings) if shape else None
            if target is None:
                unread.append(ast.unparse(node.args[-1] if node.args else node))
            else:
                roots.add(target)
    return roots, unread


def _row_problem(rel, sroot, trees, used):
    """Why an IMPORT_SEARCH_ROOTS row is not live, or None; raises CannotEvaluate where an insertion argument
    the gate cannot read could be the one that holds the row live (see the docstring)."""
    if rel not in trees:
        return "names no scanned Python file"
    relpath = os.path.relpath(sroot, _dir_of(rel) or ".").replace(os.sep, "/")
    parts = [p for p in relpath.split("/") if p not in ("..", ".")]
    if not parts:
        return "the root is not beyond the file's own directory"
    roots, unread = _insertion_roots(trees[rel], rel)
    if not roots and not unread:
        return "the file makes no sys.path.insert or sys.path.append call"
    if unread and sroot not in roots:
        raise CannotEvaluate("%s: IMPORT_SEARCH_ROOTS row (%r, %r): the sys.path insertion argument %s is "
                             "outside the forms the gate reads, and no read insertion resolves to the root"
                             % (rel, rel, sroot, unread[0]))
    if roots and sroot not in roots:
        return "no sys.path.insert or sys.path.append argument resolves to the root (the file inserts %s)" \
            % ", ".join(sorted(roots))
    if (rel, sroot) not in used:
        return "stale: no import of the file resolves through the root"
    return None


def scan_python(root, files, stdlib, records, rules, search_roots, watched):
    """Parse every .py file and classify every import. Returns (findings, stats, by_file, sites)."""
    findings = []
    stats = dict(files=0, imports=0, stdlib=0, repo=0, vendored=0, dispositioned=0)
    root = Path(root)
    file_set = set(rel for rel, _ in files)
    py = [(rel, path) for rel, path in files if rel.endswith(".py")]
    extra = dict()
    for rel, sroot in search_roots:
        extra.setdefault(rel, []).append(sroot)
    by_file, trees, used, sites = dict(), dict(), set(), []
    for rel, path in py:
        source = _regular_bytes(path, rel)
        try:
            tree = ast.parse(source, rel)
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            raise CannotEvaluate("%s: does not parse: %s" % (rel, exc))
        trees[rel] = tree
        imports = extract_imports(tree)
        for lineno, message in unreadable_calls(tree):
            findings.append(("import-closure", rel, lineno, message))
        sites.extend((rel,) + site for site in dynamic_sites(tree, source, *watched))
        stats["files"] += 1
        by_file[rel] = imports
        vroot = _vendored_root_of(rel, records)
        roots = _search_roots(rel, file_set, extra)
        for lineno, module, level, names, _guarded in imports:
            stats["imports"] += 1
            if level:
                base, base_rel = _relative_base(root, path.parent, level)
                limit = vroot.rstrip("/") if vroot else None
                escaped = base_rel == ".." or base_rel.startswith("../") or \
                    (limit is not None and base_rel != limit and not base_rel.startswith(limit + "/"))
                if escaped:
                    if "relative-escape" in rules:
                        findings.append(("relative-escape", rel, lineno, "relative import (level %d) escapes %s"
                                         % (level, "its vendored root " + vroot if vroot else "the repository")))
                    continue
                if module:
                    ok = _module_present(file_set, base_rel, module.split(".")[0], namespace=True)
                else:
                    ok = _join(base_rel, "__init__.py") in file_set or \
                        all(_module_present(file_set, base_rel, n, namespace=True) for n in names)
                if not ok:
                    findings.append(("import-closure", rel, lineno,
                                     "relative import names no module under %s" % (base_rel or ".")))
                    continue
                stats["vendored" if vroot else "repo"] += 1
                continue
            top = module.split(".")[0]
            hits = [r for r in roots if _module_present(file_set, r, top)]
            if len(hits) + (top in stdlib) > 1:
                places = ["the standard library"] * (top in stdlib) + [r or "." for r in hits]
                raise CannotEvaluate("%s:%d: import %r is ambiguous: present in %s (which one it reaches depends "
                                     "on sys.path order)" % (rel, lineno, top, ", ".join(places)))
            if not hits:
                if top in stdlib:
                    stats["stdlib"] += 1
                else:
                    findings.append(("import-closure", rel, lineno,
                                     "import %r is not the standard library of the floor version, or a module in "
                                     "a search root of this file (%s); third-party or invented name"
                                     % (top, ", ".join(r or "." for r in roots) or "none")))
                continue
            used.add((rel, hits[0]))
            target = _join(hits[0], top)
            if _vendor_dir_of(target + "/") is None:
                stats["repo"] += 1
            elif target + "/" in records:
                stats["vendored"] += 1
            elif "vendor-provenance" in rules:
                findings.append(("vendor-provenance", rel, lineno,
                                 "import %r resolves to %s, which no provenance record covers" % (top, target)))
    for rel, sroot in search_roots:
        problem = _row_problem(rel, sroot, trees, used)
        if problem is not None:
            findings.append(("import-closure", rel, 0, "IMPORT_SEARCH_ROOTS row (%r, %r): %s"
                             % (rel, sroot, problem)))
    return findings, stats, by_file, sites


def _import_targets(rel, imports, records):
    """(lineno, dotted target) for every absolute or resolvable relative import in one file, each from-import
    name also expanded (from a.b import c gives a.b and a.b.c)."""
    rroot = _vendored_root_of(rel, records)
    out = []
    for lineno, module, level, names, _guarded in imports:
        if level:
            if rroot is None:
                continue
            package = _dotted(rel, rroot).split(".")
            if not rel.endswith("/__init__.py"):
                package = package[:-1]
            package = package[:len(package) - (level - 1)]
            target = ".".join(package + ([module] if module else []))
        else:
            target = module
        out.append((lineno, target))
        out.extend((lineno, target + "." + name) for name in names)
    return out


def apply_dispositions(findings, by_file, records, dispositions, rules, stats):
    """Suppress the import-closure findings each row covers; under the disposition rule, refuse a stale,
    misplaced or untrue row. Returns (findings, printed dispositioned lines)."""
    shown, extra = [], []
    covered = set()
    for rel, top, condition in dispositions:
        rroot = _vendored_root_of(rel, records)
        prefix = "import %r is " % top
        hits = [f for f in findings if f[0] == "import-closure" and f[1] == rel and f[3].startswith(prefix)]
        problem = None
        if condition not in CONDITIONS:
            problem = "unknown condition %r" % condition
        elif rroot is None:
            problem = "the file is not under a recorded vendored root"
        elif not hits:
            problem = "stale: no unresolved import of %r occurs there" % top
        elif condition == "guarded-optional":
            unguarded = [r for r in by_file.get(rel, ()) if r[1].split(".")[0] == top and not r[2] and not r[4]]
            if unguarded:
                problem = "line %d imports %r outside a try that catches ImportError" % (unguarded[0][0], top)
        else:
            dotted = _dotted(rel, rroot)
            for other, imports in sorted(by_file.items()):
                reached = [n for n, t in _import_targets(other, imports, records)
                           if other != rel and (t == dotted or t.startswith(dotted + "."))]
                if reached:
                    problem = "%s:%d imports %s, so the module is reached" % (other, reached[0], dotted)
                    break
        if problem is None or "disposition" not in rules:
            covered.update(hits)
            shown.extend("DISPOSITIONED [%s] %s:%d: %s" % (condition, f[1], f[2], f[3]) for f in hits)
        if problem is not None and "disposition" in rules:
            extra.append(("disposition", rel, 0, "VENDORED_OPTIONAL_IMPORTS row (%r, %r): %s"
                          % (rel, top, problem)))
    stats["dispositioned"] = len(covered)
    return [f for f in findings if f not in covered] + extra, shown


def _plain_scalar(text):
    """A YAML plain scalar with its comment cut, or None if text cannot be one on a single line."""
    end = re.search(r"[ \t]#", text)
    value = (text[:end.start()] if end else text).rstrip(" \t")
    if not value or value[0] in YAML_PLAIN_START or \
            (value[0] in "-?:" and (len(value) == 1 or value[1] in " \t")):
        return None
    if re.search(r":(?:[ \t]|$)", value):
        return None
    return value


def _yaml_value(text):
    """(kind, scalar) for the text after a key's colon or a sequence dash: ("none", None) for nothing or a
    comment, ("block", None) for a block scalar header, ("flow", text) for a flow sequence of scalars,
    ("scalar", decoded value) for a plain or quoted scalar; None outside the accepted subset."""
    if not text or text.startswith("#"):
        return "none", None
    if YAML_BLOCK_RE.fullmatch(text):
        return "block", None
    single = YAML_SINGLE_RE.fullmatch(text)
    if single is not None:
        return "scalar", single.group("value").replace("''", "'")
    double = YAML_DOUBLE_RE.fullmatch(text)
    if double is not None:
        return "scalar", double.group("value")
    if YAML_FLOW_SEQ_RE.fullmatch(text):
        return "flow", text
    plain = _plain_scalar(text)
    return None if plain is None else ("scalar", plain)


def yaml_pin_scalars(text, rel):
    """(line number, key, value) for every key in PIN_KEYS (any case) with its inline scalar, over a
    workflow or action file read by the accepted YAML subset; CannotEvaluate on any line outside it."""
    bad = YAML_FORBIDDEN_RE.search(text)
    if bad is not None:
        raise CannotEvaluate("%s:%d: character %r (a bare CR, a control character, U+2028, U+2029 or U+FEFF) "
                             "is outside the accepted YAML subset"
                             % (rel, text.count("\n", 0, bad.start()) + 1, bad.group()))
    out = []
    block = None
    scalar_column = None
    for number, line in enumerate(text.split("\n"), 1):
        line = line[:-1] if line.endswith("\r") else line
        if block is not None:
            if not line.strip(" \t"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            parent, content = block
            if content is None and indent > parent:
                block = (parent, indent)
                continue
            if content is not None and indent >= content:
                continue
            block = None
        if not line.strip(" \t") or line.lstrip(" ").startswith("#"):
            continue
        shape = YAML_LINE_RE.fullmatch(line)
        dash, body = shape.group("dash"), shape.group("body")
        column = len(shape.group("indent")) + len(dash or "")
        if scalar_column is not None and len(shape.group("indent")) > scalar_column:
            raise CannotEvaluate("%s:%d: a line indented under an inline scalar, which YAML reads as a continuation "
                                 "of that scalar (judged as written)" % (rel, number))
        scalar_column = None
        key = YAML_KEY_RE.fullmatch(body)
        if key is not None:
            name = key.group("key")
            name = name[1:-1] if name[0] in "'\"" else name
            kind = _yaml_value(key.group("value") or "")
        elif dash is not None:
            name, kind = None, _yaml_value(body)
            if kind is not None and kind[0] == "block":
                kind = None
        else:
            name, kind = None, None
        if kind is None:
            raise CannotEvaluate("%s:%d: a line outside the accepted YAML subset (judged as written)"
                                 % (rel, number))
        if kind[0] == "block":
            block = (column, None)
        elif kind[0] in ("scalar", "flow"):
            scalar_column = column if key is not None else len(shape.group("indent"))
        if kind[0] in ("scalar", "flow") and USES_KEYLIKE_RE.search(kind[1]):
            raise CannotEvaluate("%s:%d: a scalar holding uses followed by a colon (judged as written)"
                                 % (rel, number))
        if name is not None and name.lower() in PIN_KEYS:
            if kind[0] != "scalar" and not (name.lower() == "container" and kind[0] == "none"):
                raise CannotEvaluate("%s:%d: a %s key without an inline scalar value (a container key may hold a "
                                     "nested mapping)" % (rel, number, name))
            if kind[0] == "scalar":
                out.append((number, name.lower(), kind[1]))
    return out


def scan_workflows(root, files, rules):
    """Every uses, image and container key in .github/workflows/*.yml or *.yaml (any case) and in every
    action.yml or action.yaml."""
    findings = []
    counts = dict(uses=0, images=0)
    file_set = set(rel for rel, _ in files)
    depth = WORKFLOWS_REL.count("/") + 1
    targets = [(rel, path) for rel, path in files
               if (rel.startswith(WORKFLOWS_REL + "/") and rel.count("/") == depth
                   and rel.lower().endswith((".yml", ".yaml")))
               or rel.rsplit("/", 1)[-1].lower() in ACTION_FILE_NAMES]
    if not any(rel.startswith(WORKFLOWS_REL + "/") for rel, _ in targets):
        raise CannotEvaluate("%s holds no .yml or .yaml workflow (cannot evaluate the pin rule)" % WORKFLOWS_REL)
    for rel, path in targets:
        action_file = rel.rsplit("/", 1)[-1].lower() in ACTION_FILE_NAMES
        for number, key, value in yaml_pin_scalars(_text(path, rel), rel):
            if key == "uses":
                counts["uses"] += 1
                if value.startswith("./"):
                    local = os.path.normpath(value).replace(os.sep, "/")
                    pinned = local != ".." and not local.startswith("../")
                    what = "uses %r leaves the repository (a local path must stay inside it)" % value
                elif value.startswith("docker://"):
                    pinned = DOCKER_PINNED_RE.fullmatch(value) is not None
                    what = "uses %r is not pinned by @sha256 digest (a tag can move)" % value
                else:
                    remote = REMOTE_ACTION_RE.fullmatch(value)
                    pinned = remote is not None and COMMIT_SHA_RE.fullmatch(remote.group("ref")) is not None
                    what = "uses %r is not pinned to a full 40-hex commit SHA (a tag or branch can move)" % value
            else:
                counts["images"] += 1
                local = os.path.normpath(os.path.join(_dir_of(rel), value)).replace(os.sep, "/")
                pinned = IMAGE_PINNED_RE.fullmatch(value) is not None or \
                    (action_file and not value.startswith("/") and local in file_set)
                what = "%s %r is not pinned by @sha256 digest (a tag can move)" % (key, value)
            if not pinned and "action-pin" in rules:
                findings.append(("action-pin", rel, number, what))
    return findings, counts


def scan(root, rules=RULES, sys_module=sys, version_info=None, dispositions=VENDORED_OPTIONAL_IMPORTS,
         search_roots=IMPORT_SEARCH_ROOTS, allowlist=DYNAMIC_IMPORT_SITES, import_system=IMPORT_SYSTEM_MODULES,
         inert=INERT_IMPORT_SYSTEM_MEMBERS):
    """The whole verdict over root. Returns (findings, dispositioned lines, stats); raises CannotEvaluate."""
    stdlib = stdlib_names(sys_module)
    check_interpreter(load_floor(root), version_info if version_info is not None else sys.version_info)
    watched = watched_names(import_system, inert)
    files = _walk(Path(root))
    records, vendor_findings = _load_vendor(root, files)
    py_findings, stats, by_file, sites = scan_python(root, files, stdlib, records, rules, search_roots, watched)
    py_findings, shown = apply_dispositions(py_findings, by_file, records, dispositions, rules, stats)
    py_findings += check_dynamic_sites(sites, allowlist)
    stats.update(dynamic=len(sites), entries=len(allowlist), names=len(watched[0]), members=len(watched[1]))
    wf_findings, counts = scan_workflows(root, files, rules)
    stats.update(counts)
    stats["skipped"] = ", ".join(n for n in SKIPPED_ROOT_NAMES if os.path.lexists(Path(root) / n)) or "none"
    findings = [f for f in vendor_findings + py_findings + wf_findings if f[0] in rules]
    return sorted(set(findings)), shown, stats


def run(root):
    try:
        findings, shown, stats = scan(root)
    except CannotEvaluate as exc:
        print("IMPORT CLOSURE: CANNOT EVALUATE: %s" % exc, file=sys.stderr)
        return 2
    for line in shown:
        print(line)
    for rule, rel, lineno, message in findings:
        print("FINDING [%s] %s:%d: %s" % (rule, rel, lineno, message), file=sys.stderr)
    summary = ("%(files)d Python files, %(imports)d imports (%(stdlib)d standard library, %(repo)d in-repo, "
               "%(vendored)d vendored, %(dispositioned)d dispositioned), %(dynamic)d dynamic-import sites (references "
               "to %(names)d watched names and %(members)d import-system members; not resolved, a literal name "
               "excepted), each listed exactly by one of %(entries)d reviewed rows, %(uses)d action references, "
               "%(images)d container images; not walked at the root: %(skipped)s" % stats)
    if findings:
        print("IMPORT CLOSURE: FAILED: %d finding(s); %s" % (len(findings), summary), file=sys.stderr)
        return 1
    print("IMPORT CLOSURE: OK: %s" % summary)
    return 0


# ---------------------------------------------------------------- self-test

_PIN = "0123456789abcdef0123456789abcdef01234567"
_BS = "\\"
_VENDORED = (
    "lib/_vendor/pkg/__init__.py", "lib/_vendor/pkg/mod.py", "lib/_vendor/pkg/sub/__init__.py",
    "lib/_vendor/pkg/sub/leaf.py", "lib/_vendor/pkg/opt.py", "lib/_vendor/pkg/far.py",
    "lib/_vendor/licenses/pkg-MIT.txt",
)
_PROVENANCE = ('[package]\nname = "pkg"\nversion = "1.0"\npurl = "%s"\n'
               '[license]\nspdx = "%s"\nvendored_path = "lib/_vendor/licenses/pkg-MIT.txt"\n'
               '[vendored_tree]\nroot = "%s"\n')
_GOOD_PROVENANCE = _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT", "lib/_vendor/pkg/")
_MIT = ("MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this "
        "software, to deal in the Software without restriction.\n")
_A_PY = ("import os\nimport sys\nfrom pathlib import Path\n"
         "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))\n"
         "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib' / '_vendor'))\n"
         "import b\nimport importlib\nimportlib.import_module('json')\nfrom c import d\nimport pkg.mod\n"
         "__import__(os.environ['X'])\n")
_WORKFLOW = ("on:\n  push:\n    branches: [main]\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
             "      - uses: actions/checkout@%s # v4\n      - uses: './local'\n      - name: x\n"
             "        run: |\n          echo uses: here\n\n          - uses: actions/checkout@v4\n"
             "      - \"name\": y\n        run: echo ok\n    container:\n      image: node@sha256:%s\n"
             % (_PIN, "ab" * 32))


def _fixture(base, extra=(), drop=(), rehash=True):
    """A clean fixture tree under base, then the extra rows (rel, str or bytes) written over it and the drop
    rels left out. The vendored manifest hashes the final bytes unless rehash is false (then the clean
    bytes, so an edited vendored file mismatches)."""
    rows = dict((
        (FLOOR_REL, 'python-floor = "%d.%d"\n' % tuple(sys.version_info[:2])),
        ("tools/a.py", _A_PY),
        ("tools/b.py", "import sys\n"),
        ("lib/c.py", "d = 1\n"),
        ("lib/_vendor/pkg/__init__.py", "from . import mod\n"),
        ("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\n"),
        ("lib/_vendor/pkg/sub/__init__.py", ""),
        ("lib/_vendor/pkg/sub/leaf.py", "from .. import mod\n"),
        ("lib/_vendor/pkg/opt.py", "try:\n    import shinylib\nexcept ImportError:\n    shinylib = None\n"),
        ("lib/_vendor/pkg/far.py", "import heavylib\n"),
        ("lib/_vendor/licenses/pkg-MIT.txt", _MIT),
        ("lib/_vendor/pkg-1.0.provenance.toml", _GOOD_PROVENANCE),
        (WORKFLOWS_REL + "/q.yml", _WORKFLOW),
    ))
    clean = dict(rows)
    rows.update(extra)
    hashed = rows if rehash else clean
    manifest = "".join("%s  %s\n" % (hashlib.sha256(hashed[rel] if isinstance(hashed[rel], bytes) else
                                                    hashed[rel].encode("utf-8")).hexdigest(), rel)
                       for rel in _VENDORED)
    rows.setdefault("lib/_vendor/pkg-1.0.manifest.sha256", manifest)
    for rel, text in rows.items():
        if rel in drop:
            continue
        path = Path(base) / rel
        os.makedirs(path.parent, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return Path(base)


_FIXTURE_DISPOSITIONS = (
    ("lib/_vendor/pkg/opt.py", "shinylib", "guarded-optional"),
    ("lib/_vendor/pkg/far.py", "heavylib", "unreached-module"),
)
_FIXTURE_ROOTS = (("tools/a.py", "lib"), ("tools/a.py", "lib/_vendor"))
_T_ROOTS = _FIXTURE_ROOTS + (("tools/t.py", "lib/_vendor"),)
_FIXTURE_SITES = (("tools/a.py", MODULE_SCOPE, ("#1: import importlib",
                                                "#1: importlib.import_module('json')",
                                                "#1: __import__(os.environ['X'])"), "the fixture's own sites"),)
_T_PATH = "import os, sys\nfrom pathlib import Path\n"
_T_VENDOR_LINE = "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib' / '_vendor'))\n"
_T_VENDOR = _T_PATH + _T_VENDOR_LINE
_T_OWN = _T_PATH + "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
_T_PLANTED = "planted = ('lib', '_vendor', 'lib/_vendor')\n"
# A file 16 directories deep, whose .parents[16] is the repository root.
_T_DEEP = "/".join("d%d" % n for n in range(16)) + "/t.py"
_T_GEN_COMMON = ("tools/_gen_common.py", "from pathlib import Path\ndef repo_root():\n    return Path('.')\n")
_T_ELSEWHERE = ("tools/elsewhere.py", "from pathlib import Path\ndef repo_root():\n    return Path('elsewhere')\n")
# The same module also defining the names the from-import rebinding vectors import, so each is a real rebinding.
_T_ELSEWHERE_NAMES = (_T_ELSEWHERE[0], _T_ELSEWHERE[1] + "ROOT = Path('elsewhere')\nLIB = ROOT / 'lib'\n")
# A name bound once to a read shape, then (in the rebinding vectors) one statement between it and its insertion.
_T_NAMED = _T_PATH + "ROOT = str(Path(__file__).resolve().parents[1] / 'lib' / '_vendor')\n" \
    "%ssys.path.insert(0, ROOT)\nimport pkg\n"
# Two names bound to pathlib chains, the inner one (ROOT) then, in the rebinding vectors, rebound before the outer
# one is bound, so that the outer one's value follows the rebinding and not the first binding.
_T_TWO_NAMES = _T_PATH + "ROOT = Path(__file__).resolve().parents[1]\n%sLIB = ROOT / 'lib'\n" \
    "sys.path.insert(0, str(LIB / '_vendor'))\nimport pkg\n"
# The same two names, the outer one (LIB, read directly inside the inserted str()) then, in the rebinding vectors,
# rebound before the insertion, so that the insertion follows the rebinding and not the first binding.
_T_OUTER_NAME = _T_PATH + "ROOT = Path(__file__).resolve().parents[1]\nLIB = ROOT / 'lib'\n" \
    "%ssys.path.insert(0, str(LIB / '_vendor'))\nimport pkg\n"
# One name bound to a pathlib chain and read directly inside the inserted str(), then, in the rebinding vectors,
# rebound before the insertion.
_T_ONE_NAME = _T_PATH + "ROOT = Path(__file__).resolve().parents[1]\n" \
    "%ssys.path.insert(0, str(ROOT / 'lib' / '_vendor'))\nimport pkg\n"


def _unread_refusal(argument, rel="tools/t.py", sroot="lib/_vendor"):
    """The whole refusal of a row whose only insertion argument the gate does not read: the file, the row and the
    argument's spelling."""
    return ("%s: IMPORT_SEARCH_ROOTS row (%r, %r): the sys.path insertion argument %s is outside the forms the gate "
            "reads, and no read insertion resolves to the root" % (rel, rel, sroot, argument))


def _unread_vector(name, argument, extra=(), source=None, rel="tools/t.py"):
    """A cannot-evaluate vector: rel inserting argument (or holding source) beside import pkg, under the row (rel,
    lib/_vendor), refused by its whole message."""
    source = source if source is not None else _T_PATH + "sys.path.insert(0, %s)\nimport pkg\n" % argument
    return (name, ((rel, source),) + tuple(extra), (), dict(search_roots=_FIXTURE_ROOTS + ((rel, "lib/_vendor"),)),
            None, _unread_refusal(argument, rel))


_WF = WORKFLOWS_REL + "/p.yml"
_REC = "lib/_vendor/pkg-1.0.provenance.toml"
_MANIFEST = "lib/_vendor/pkg-1.0.manifest.sha256"
_LICENCE = "lib/_vendor/licenses/pkg-MIT.txt"
_MALFORMED = "not of the form #ORDINAL: TEXT"

# (name, rule, rel the finding must name, extra rows, dropped rels, scan keyword overrides, rehash). An extra
# row (_MANIFEST, None) appends a manifest row for a file that does not exist.
_RULE_VECTORS = (
    ("third-party import", "import-closure", "tools/t.py", (("tools/t.py", "import requests\n"),), (), {}, True),
    ("invented name in a lazy import", "import-closure", "tools/t.py",
     (("tools/t.py", "def f():\n    import reqeusts\n"),), (), {}, True),
    ("disconnected shadow: a same-named file in an unrelated directory", "import-closure", "tools/t.py",
     (("tools/t.py", "import requests\n"), ("unused/requests.py", "")), (), {}, True),
    ("disconnected shadow: a same-named package in a test directory", "import-closure", "tools/t.py",
     (("tools/t.py", "import requests\n"), ("tests/fixtures/requests/__init__.py", "x = 1\n")), (), {}, True),
    ("namespace directory beside the importer", "import-closure", "tools/t.py",
     (("tools/t.py", "import requests\n"), ("tools/requests/helper.py", "x = 1\n")), (), {}, True),
    ("sibling import from inside a package", "import-closure", "lib/pk/m.py",
     (("lib/pk/__init__.py", ""), ("lib/pk/m.py", "import n\n"), ("lib/pk/n.py", "")), (), {}, True),
    ("vendored file importing beside itself", "import-closure", "lib/_vendor/pkg/mod.py",
     (("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\nimport opt\n"),), (), {}, True),
    # A literal name at a dynamic-import call is resolved like an import statement, at an allowlisted site too.
    ("literal dynamic import of a third-party name at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.import_module('yaml')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import importlib", "#1: importlib.import_module('yaml')"),
                                       "r"),)), True),
    ("__import__ of a third-party name inside an allowlisted function", "import-closure", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('requests')",), "r"),)), True),
    ("__import__ level keyword 0 at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('requests', level=0)\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__('requests', level=0)",),
                                       "r"),)),
     True),
    ("__import__ fifth argument 0 at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('requests', None, None, (), 0)\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: __import__('requests', None, None, (), 0)",), "r"),)), True),
    ("_gcd_import of a third-party name at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib._bootstrap._gcd_import('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import importlib",
                                        "#1: importlib._bootstrap._gcd_import('requests')"),
                                       "r"),)), True),
    ("literal dynamic import of a third-party name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.import_module('json')\n"),), (), {}, True),
    ("aliased importlib", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib as il\nil.import_module('json')\n"),), (), {}, True),
    ("aliased import_module", "dynamic-import", "tools/t.py",
     (("tools/t.py", "from importlib import import_module as load\nload('requests')\n"),), (), {}, True),
    ("import_module name keyword", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.import_module(name='json')\n"),), (), {}, True),
    ("__import__ through builtins", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\nbuiltins.__import__('json')\n"),), (), {}, True),
    ("__import__ as an importlib attribute", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.__import__('json')\n"),), (), {}, True),
    ("find_spec through importlib.util", "dynamic-import", "tools/t.py",
     (("tools/t.py", "from importlib import util\nutil.find_spec('json')\n"),), (), {}, True),
    ("getattr with a literal name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\ngetattr(importlib, 'import_module')('json')\n"),), (), {}, True),
    # Each indirect dynamic import below (through an alias, a fromlist, a loader, a package path, or a name reached
    # through getattr, vars or sys.modules, among others) is a finding unless an allowlist row lists it.
    ("function-local alias beside a module-level __import__", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def unrelated():\n    from builtins import print as __import__\n__import__('json')\n"),),
     (), {}, True),
    ("__import__ fromlist keyword reaching an unreached module", "dynamic-import", "tools/t.py",
     (("tools/t.py", _T_VENDOR + "import pkg\n__import__('pkg', fromlist=['far'])\n"),), (),
     dict(search_roots=_T_ROOTS), True),
    ("__import__ positional fromlist reaching an unreached module", "dynamic-import", "tools/t.py",
     (("tools/t.py", _T_VENDOR + "import pkg\n__import__('pkg', None, None, ['far'])\n"),), (),
     dict(search_roots=_T_ROOTS), True),
    ("__import__ computed fromlist", "dynamic-import", "tools/t.py",
     (("tools/t.py", "__import__('json', fromlist=names)\n"),), (), {}, True),
    ("__import__ through __builtins__", "dynamic-import", "tools/t.py",
     (("tools/t.py", "__builtins__.__import__('json')\n"),), (), {}, True),
    ("PathFinder spec executed by its loader", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib.machinery, importlib.util\n"
       "s = importlib.machinery.PathFinder.find_spec('json')\nm = importlib.util.module_from_spec(s)\n"
       "s.loader.exec_module(m)\n"),), (), {}, True),
    ("importlib.resources by package name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib.resources\nimportlib.resources.files('requests')\n"),), (), {}, True),
    ("pydoc locate by name", "dynamic-import", "tools/t.py", (("tools/t.py", "import pydoc\npydoc.locate('json')\n"),),
     (), {}, True),
    ("importlib through sys.modules", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import sys\nsys.modules['importlib'].import_module('json')\n"),), (), {}, True),
    ("importlib bound to another name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\nm = importlib\nm.import_module('json')\n"),), (), {}, True),
    ("package __path__ extended outside the tree", "dynamic-import", "tools/pk/__init__.py",
     (("tools/pk/__init__.py", "__path__.append('/usr/lib/python3/dist-packages')\n"),
      ("tools/t.py", "import pk.yaml\n")), (), {}, True),
    ("pkgutil.extend_path on a package", "dynamic-import", "tools/pk/__init__.py",
     (("tools/pk/__init__.py", "from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n"),
      ("tools/t.py", "import pk.thing\n")), (), {}, True),
    ("getattr with a literal string name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, '__import__')('json')\n"),), (), {}, True),
    ("vars with a literal string key", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\nvars(builtins)['__import__']('json')\n"),), (), {}, True),
    ("getattr with a bytes literal name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, b'__import__'.decode())('requests')\n"),), (), {}, True),
    ("string naming an importlib submodule", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import sys\nsys.modules['importlib.util']\n"),), (), {}, True),
    # Reproductions that each passed while a row allowed its names throughout a function, or while the
    # import system's own entry points were not watched.
    ("default argument evaluated outside its function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f(x=__import__('json')):\n    return x\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "reviewed body of f"),)),
     True),
    ("decorator evaluated outside its function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "@((lambda x: lambda f: f)(__import__('json')))\ndef f():\n    pass\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "reviewed body of f"),)),
     True),
    ("attribute decorator evaluated outside its function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "@__import__('json').loads\ndef f():\n    pass\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json').loads",), "r"),)), True),
    ("class base evaluated outside its class", "dynamic-import", "tools/t.py",
     (("tools/t.py", "class C(__import__('json').JSONDecoder):\n    pass\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "class C", ("#1: __import__('json').JSONDecoder",), "r"),)),
     True),
    ("importer returned from a function with a reviewed call", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    __import__('json')\n    return __import__\n\ndef g():\n    return f()('json')\n\n"
       "result = g().__name__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "reviewed body of f"),)),
     True),
    ("lambda returned from a function with a reviewed call", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    __import__('json')\n    return lambda: __import__('json')\n\ng = f()\n"
       "result = g().__name__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "reviewed body of f"),)),
     True),
    ("_gcd_import through an allowlisted importlib", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib.util\nimportlib._bootstrap._gcd_import('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import importlib.util",), "r"),)), True),
    ("_frozen_importlib named directly", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import _frozen_importlib\n_frozen_importlib._gcd_import('json')\n"),), (), {}, True),
    ("frozen importlib module imported by its frozen name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import _frozen_importlib_external\n"),), (), {}, True),
    ("watched name _gcd_import, no inert member subtracted", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f(o):\n    return o._gcd_import\n"),), (), dict(inert=()), True),
    ("_frozen_importlib through sys.modules", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import sys\nsys.modules['_frozen_importlib']._gcd_import('json')\n"),), (), {}, True),
    ("_imp imported", "dynamic-import", "tools/t.py", (("tools/t.py", "import _imp\n"),), (), {}, True),
    # The allowlist: a row allows exact references (ordinal and text) in its own file and scope, and is held live.
    ("allowlisted reference repeated in another function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('json')\ndef g():\n    return __import__('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "r"),)), True),
    ("allowlist row naming a method by its bare name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "class C:\n    def g(self):\n        return __import__\ndef g():\n    pass\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "g", ("#1: __import__",), "r"),)), True),
    ("allowlist row naming a method by its bare name, no other scope of that name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "class C:\n    def g(self):\n        return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "g", ("#1: __import__",), "r"),)), True),
    ("module-level row for a site inside a function", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__",), "r"),)), True),
    # A reference is named by its text and its ordinal among the references of that text in its scope, never by its
    # line or column: an edit to its text, its scope, or the count of its text there is a finding, and a row still in
    # the old positional form is refused as malformed (expect: a finding must carry that refusal, not only the stale
    # row and unlisted site any unmatched reference gives).
    ("allowlisted reference's own text edited", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('csv')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "r"),)), True),
    ("a site added beside a listed one", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    __import__('json')\n    return __import__('csv')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "r"),)), True),
    ("enclosing function renamed", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def g():\n    return __import__('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "r"),)), True),
    ("identical reference added beside a listed one in its scope", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    __import__('json')\n    return __import__('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "r"),)), True),
    ("identical reference removed, its ordinal stale", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')", "#2: __import__('json')"),
                                       "r"),)), True),
    ("allowlist row in the positional form", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("2:11-2:29: __import__('json')",), "r"),),
          expect=_MALFORMED), True),
    ("allowlist row naming sibling lambdas by their shared name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "a = lambda x: __import__(x)\nb = lambda x: __import__(x)\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "<lambda>", ("#1: __import__(x)", "#2: __import__(x)"), "r"),)),
     True),
    ("allowlist row naming same-named defs by their shared name", "dynamic-import", "tools/t.py",
     (("tools/t.py", "if X:\n    def f(x):\n        return __import__(x)\nelse:\n    def f(x):\n"
                     "        return None\n"),),
     (), dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__(x)",), "r"),)), True),
    ("allowlisted line holding another expression", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('json').dumps\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "r"),)), True),
    ("stale allowlist row", "dynamic-import", "tools/t.py", (("tools/t.py", "def f():\n    return 1\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__",), "r"),)), True),
    ("allowlist row listing a reference that does not occur", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__", "#1: x.importlib"), "r"),)),
     True),
    ("allowlist row with no reason", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__",), " "),)), True),
    ("allowlist row listing no reference", "dynamic-import", "tools/t.py", (("tools/t.py", "x = 1\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", (), "r"),)), True),
    ("allowlist row with a malformed reference", "dynamic-import", "tools/t.py", (("tools/t.py", "x = 1\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("two: x",), "r"),), expect=_MALFORMED), True),
    ("allowlist row repeated", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__",), "r"),
                                      ("tools/t.py", "f", ("#1: __import__",), "s"))), True),
    # A permission names one node, not one line's text: two identical calls on one line need a reference each, two
    # nodes cannot share one permission key, and a row cannot list one reference twice.
    ("two identical calls on one line, one listed", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f(x):\n    __import__(x); x = 'requests'; __import__(x)\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__(x)",), "r"),)), True),
    ("two nodes sharing one permission key", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__('json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "r"),),
          special="twin-sites"), True),
    ("allowlist row listing one reference twice", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    return __import__\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__", "#1: __import__"), "r"),)),
     True),
    # A local named like a watched module suppresses nothing outside its own scope.
    ("a watched name bound only in an unrelated scope, used at module level", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    imp = 1\n    return imp\nx = imp\n"),), (), {}, True),
    # The exemption does not cover a closure read: a def's local read from a scope it holds is a
    # finding with no row, end to end.
    ("a closure read with no row, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f(record):\n    imp = record.get('import')\n    return lambda: imp.get('run')\n"),),
     (), {}, True),
    # A binding Python looks up at run time suppresses nothing, end to end: each reproduction below of such a
    # binding is a dynamic-import finding with no row.
    ("a conditional module binding, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "if False:\n    help = 1\nloader = help\nloader('json')\n"),), (), {}, True),
    ("an unexecuted global write, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f():\n    global help\n    help = 1\nx = help\n"),), (), {}, True),
    ("a deleted module binding, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "help = None\ndel help\ndef show(topic):\n    return help(topic)\n"),), (), {}, True),
    ("a module-level except target, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "try:\n    pass\nexcept OSError as help:\n    pass\ndef show(topic):\n"
                     "    return help(topic)\n"),), (), {}, True),
    ("a module binding after the def that reads it, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def show(t):\n    return help(t)\nhelp = print\n"),), (), {}, True),
    # Each of these is red against a gate that lets a binding Python looks up at run time suppress a site: the
    # references such a gate already kept are listed (import statements, a global declaration and, for the
    # inward barrier, the inner read), it reported none of the rest, and this gate reports every remaining
    # reference, one to three findings per vector, each without a row.
    ("a class-body read before its binding, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import pydoc\nclass C:\n    loader = pydoc.importfile\n    pydoc = None\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import pydoc",), "r"),)), True),
    ("a class-body read after an except target, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import pydoc\nclass C:\n    try:\n        pass\n    except OSError as pydoc:\n"
                     "        pass\n    f = pydoc.importfile\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import pydoc",), "r"),)), True),
    ("a global barrier outward, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\ndef outer():\n    importlib = 1\n    def middle():\n"
                     "        global importlib\n        def inner():\n            return importlib\n"
                     "        return inner\n    return middle()\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import importlib",), "r"),
                                      ("tools/t.py", "outer.middle", ("#1: global importlib",), "r"))), True),
    ("a global barrier inward, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "importlib = 0\ndef outer():\n    import importlib\n    def middle():\n"
                     "        global importlib\n        def inner():\n            return importlib\n"
                     "        return inner\n    return middle()\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "outer", ("#1: import importlib",), "r"),
                                      ("tools/t.py", "outer.middle.inner", ("#1: importlib",), "r"))), True),
    ("a lambda behind a global barrier, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import importlib\ndef outer():\n    importlib = 1\n    def middle():\n"
                     "        global importlib\n        return lambda: importlib\n    return middle()\n"),),
     (), dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import importlib",), "r"),
                                          ("tools/t.py", "outer.middle", ("#1: global importlib",), "r"))), True),
    ("a named binding beside a * import, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "help = 1\nfrom pydoc import *\nresult = help\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: from pydoc import *",), "r"),)), True),
    ("a comprehension target in a def, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "def f(rows):\n    return [imp for imp in rows if imp]\n"),), (), {}, True),
    ("a comprehension target in a class, end to end", "dynamic-import", "tools/t.py",
     (("tools/t.py", "class C:\n    xs = [imp for imp in (1,)]\n"),), (), {}, True),
    ("getattr with a default reaching __import__ at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, '__import__', None)('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: getattr(builtins, '__import__', None)('requests')",), "r"),)), True),
    ("getattr reaching __import__ at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, '__import__')('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: getattr(builtins, '__import__')('requests')",), "r"),)), True),
    ("subscript reaching __import__ at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\nvars(builtins)['__import__']('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: vars(builtins)['__import__']('requests')",), "r"),)), True),
    ("run_module mod_name keyword at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import runpy as r\nr.run_module(mod_name='requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import runpy as r",
                                                                    "#1: r.run_module(mod_name='requests')"),
                                       "r"),)), True),
    ("import_module name keyword at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib\nimportlib.import_module(name='requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import importlib",
                                        "#1: importlib.import_module(name='requests')"), "r"),)), True),
    ("pydoc.locate path keyword at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\npydoc.locate(path='requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import pydoc",
                                                                    "#1: pydoc.locate(path='requests')"),
                                       "r"),)), True),
    ("help of a third-party name at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "help('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help('requests')",), "r"),)), True),
    ("help request keyword at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "help(request='requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help(request='requests')",), "r"),)),
     True),
    ("help with no row", "dynamic-import", "tools/t.py", (("tools/t.py", "help('json')\n"),), (), {}, True),
    ("pydoc.Helper instance called at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\npydoc.Helper()('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import pydoc",
                                                                    "#1: pydoc.Helper()('requests')"),
                                       "r"),)), True),
    ("empty argument unpacking at an allowlisted importer call", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('requests', *())\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__('requests', *())",), "r"),)),
     True),
    ("starred module name at an allowlisted importer call", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__(*('requests',))\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__(*('requests',))",), "r"),)),
     True),
    ("double-starred module name at an allowlisted importer call", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__(**dict(name='requests'))\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: __import__(**dict(name='requests'))",), "r"),)), True),
    ("starred getattr callee at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, *('__import__',))('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: '__import__'",), "r"),)), True),
    ("relative import naming no module beside a non-package file", "import-closure", "tools/t.py",
     (("tools/t.py", "from . import nothere\n"),), (), {}, True),
    ("relative from-import of a missing module", "import-closure", "lib/_vendor/pkg/sub/leaf.py",
     (("lib/_vendor/pkg/sub/leaf.py", "from .. import mod\nfrom .gone import x\n"),), (), {}, True),
    ("search-root row with no sys.path call", "import-closure", "tools/t.py",
     (("tools/t.py", "import pkg\n_ = ('lib', '_vendor')\n"),), (), dict(search_roots=_T_ROOTS), True),
    ("search-root row whose root no insertion resolves to", "import-closure", "tools/t.py",
     (("tools/t.py", _T_PATH + "sys.path.append(str(Path(__file__).resolve().parents[1] / 'lib'))\nimport pkg\n"),),
     (), dict(search_roots=_T_ROOTS), True),
    ("search-root row beside an own-directory insertion and fixture literals", "import-closure", "tools/t.py",
     (("tools/t.py", _T_OWN + _T_PLANTED + "import pkg\n"),), (), dict(search_roots=_T_ROOTS), True),
    ("stale search-root row", "import-closure", "tools/t.py", (("tools/t.py", _T_VENDOR),), (),
     dict(search_roots=_T_ROOTS), True),
    ("search-root row naming no scanned file", "import-closure", "tools/t.py", (), (),
     dict(search_roots=_T_ROOTS), True),
    ("search-root row whose root is no directory", "import-closure", "tools/t.py",
     (("tools/t.py", _T_VENDOR.replace("'lib' / '_vendor'", "'gone'") + "import os\n"),), (),
     dict(search_roots=_FIXTURE_ROOTS + (("tools/t.py", "gone"),)), True),
    ("search-root row naming the file's own directory", "import-closure", "tools/t.py",
     (("tools/t.py", _T_OWN + "import b\n"),), (), dict(search_roots=_FIXTURE_ROOTS + (("tools/t.py", "tools"),)),
     True),
    ("Python under a nested venv directory", "import-closure", "tools/venv/evil.py",
     (("tools/venv/evil.py", "import requests\n"),), (), {}, True),
    ("relative import escaping the repo", "relative-escape", "tools/t.py",
     (("tools/t.py", "from ... import x\n"),), (), {}, True),
    ("relative import escaping its vendored root", "relative-escape", "lib/_vendor/pkg/sub/leaf.py",
     (("lib/_vendor/pkg/sub/leaf.py", "from ... import c\n"),), (), {}, True),
    ("unpinned action by tag", "action-pin", _WF, ((_WF, "steps:\n  - uses: actions/checkout@v4\n"),), (), {},
     True),
    ("unpinned action by short SHA", "action-pin", _WF,
     ((_WF, "steps:\n  - uses: \"actions/checkout@0123456\"\n"),), (), {}, True),
    ("unpinned action under a quoted key", "action-pin", _WF,
     ((_WF, "steps:\n  - 'uses': actions/checkout@v4\n"),), (), {}, True),
    ("unpinned action under a capitalised key", "action-pin", _WF,
     ((_WF, "steps:\n  - Uses: actions/checkout@v4\n"),), (), {}, True),
    ("unpinned action as a later key of an item", "action-pin", _WF,
     ((_WF, "steps:\n  - name: a\n    uses: actions/checkout@v4\n"),), (), {}, True),
    ("unpinned action after a block scalar ends", "action-pin", _WF,
     ((_WF, "steps:\n  - run: |\n      echo a\n  - uses: actions/checkout@v4\n"),), (), {}, True),
    ("unpinned action after an empty block scalar", "action-pin", _WF,
     ((_WF, "steps:\n  - run: >-\n    uses: actions/checkout@v4\n"),), (), {}, True),
    ("quoted uses value with inner whitespace", "action-pin", _WF,
     ((_WF, "steps:\n  - uses: 'actions/checkout@%s x'\n" % _PIN),), (), {}, True),
    ("unpinned docker image", "action-pin", WORKFLOWS_REL + "/p.YAML",
     ((WORKFLOWS_REL + "/p.YAML", "steps:\n  - uses: docker://alpine:3\n"),), (), {}, True),
    ("unpinned action in a local action file", "action-pin", "acts/x/action.yml",
     (("acts/x/action.yml", "runs:\n  steps:\n    - uses: actions/cache@main\n"),), (), {}, True),
    ("unpinned job container", "action-pin", _WF, ((_WF, "jobs:\n  t:\n    container: node:18\n"),), (), {},
     True),
    ("unpinned service image", "action-pin", _WF,
     ((_WF, "jobs:\n  t:\n    services:\n      db:\n        image: postgres:latest\n"),), (), {}, True),
    ("workflow image naming a walked file", "action-pin", _WF,
     ((_WF, "jobs:\n  t:\n    container: ../../tools/b.py\n"),), (), {}, True),
    ("action file image naming no walked file", "action-pin", "acts/y/action.yml",
     (("acts/y/action.yml", "runs:\n  using: docker\n  image: Dockerfile\n"),), (), {}, True),
    ("local uses path leaving the repository", "action-pin", _WF, ((_WF, "steps:\n  - uses: ./../../elsewhere\n"),),
     (), {}, True),
    ("vendored file missing provenance", "vendor-provenance", "lib/_vendor/pkg/extra.py",
     (("lib/_vendor/pkg/extra.py", "x = 1\n"),), (), {}, True),
    ("vendored package with no provenance record", "vendor-provenance", "tools/t.py",
     (("lib/_vendor/other/__init__.py", "x = 1\n"), ("tools/t.py", _T_VENDOR + "import other\n")), (),
     dict(search_roots=_T_ROOTS), True),
    ("vendored Python under no recorded root", "vendor-provenance", "lib/_vendor/stray.py",
     (("lib/_vendor/stray.py", "x = 1\n"),), (), {}, True),
    ("vendored record with no licence", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "", "lib/_vendor/pkg/")),), (), {}, True),
    ("vendored record with no version", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace('version = "1.0"\n', "")),), (), {}, True),
    ("vendored record with no purl", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace('purl = "pkg:pypi/pkg@1.0"\n', "")),), (), {}, True),
    ("vendored record with an empty version", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace("version = \"1.0\"", "version = \"\"").replace("pkg@1.0", "pkg@")),), (), {},
     True),
    ("purl naming another package", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/other@1.0", "MIT", "lib/_vendor/pkg/")),), (), {}, True),
    ("invalid SPDX identifier", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "not-a-license", "lib/_vendor/pkg/")),), (), {}, True),
    ("SPDX expression mixing AND and OR", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT AND ISC OR 0BSD", "lib/_vendor/pkg/")),), (), {}, True),
    ("SPDX expression with an unknown operator", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT WITH 0BSD", "lib/_vendor/pkg/")),), (), {}, True),
    ("SPDX expression ending in an operator", "vendor-provenance", _REC,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT AND", "lib/_vendor/pkg/")),), (), {}, True),
    ("empty licence text", "vendor-provenance", _LICENCE, ((_LICENCE, b""),), (), {}, True),
    ("licence text lacking a named licence", "vendor-provenance", _LICENCE,
     ((_REC, _PROVENANCE % ("pkg:pypi/pkg@1.0", "MIT OR Apache-2.0", "lib/_vendor/pkg/")),), (), {}, True),
    ("licence text not listed in the manifest", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace("licenses/pkg-MIT.txt", "licenses/absent.txt")),), (), {}, True),
    ("vendored root not named for its package", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace('name = "pkg"', 'name = "other"').replace("pypi/pkg@", "pypi/other@")),), (),
     {}, True),
    ("licence text outside its _vendor directory", "vendor-provenance", _REC,
     ((_REC, _GOOD_PROVENANCE.replace(_LICENCE, "COPYING")), ("COPYING", _MIT), ("+manifest", "COPYING")), (), {},
     True),
    ("vendored root nested too deep", "vendor-provenance", "lib/_vendor/other-1.0.provenance.toml",
     (("lib/_vendor/other-1.0.provenance.toml",
       _PROVENANCE.replace('name = "pkg"', 'name = "sub"') % ("pkg:pypi/sub@1.0", "MIT", "lib/_vendor/o/sub/")),
      ("lib/_vendor/other-1.0.manifest.sha256", "%s  %s\n" % (hashlib.sha256(_MIT.encode("utf-8")).hexdigest(),
                                                               _LICENCE))), (), {}, True),
    ("vendored bytes differ from the recorded digest", "vendor-provenance", "lib/_vendor/pkg/mod.py",
     (("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\nimport os\n"),), (), {}, False),
    ("listed vendored file missing", "vendor-provenance", _MANIFEST, ((_MANIFEST, None),), (), {}, True),
    ("listed file in a directory the walk does not enter", "vendor-provenance", _MANIFEST,
     (("node_modules/x.txt", "x\n"), ("+manifest", "node_modules/x.txt")), (), {}, True),
    ("vendored manifest missing", "vendor-provenance", _REC, (), (_MANIFEST,), {}, True),
    ("stale disposition row", "disposition", "lib/_vendor/pkg/mod.py", (), (),
     dict(dispositions=_FIXTURE_DISPOSITIONS + (("lib/_vendor/pkg/mod.py", "gone", "guarded-optional"),)), True),
    ("guarded-optional row over an unguarded import", "disposition", "lib/_vendor/pkg/opt.py",
     (("lib/_vendor/pkg/opt.py", "import shinylib\n"),), (), {}, True),
    ("guarded-optional row whose handler is not ImportError", "disposition", "lib/_vendor/pkg/opt.py",
     (("lib/_vendor/pkg/opt.py", "try:\n    import shinylib\nexcept ValueError:\n    shinylib = None\n"),), (),
     {}, True),
    ("unreached-module row whose module is reached", "disposition", "lib/_vendor/pkg/far.py",
     (("tools/t.py", _T_VENDOR + "from pkg import far\n"),), (), dict(search_roots=_T_ROOTS), True),
    ("import in a try's else is not guarded", "disposition", "lib/_vendor/pkg/opt.py",
     (("lib/_vendor/pkg/opt.py",
       "try:\n    import os\nexcept ImportError:\n    os = None\nelse:\n    import shinylib\n"),), (), {}, True),
    ("relative import reaching an unreached module", "disposition", "lib/_vendor/pkg/far.py",
     (("lib/_vendor/pkg/mod.py", "import json\nfrom .sub import leaf\nfrom . import far\n"),), (), {}, True),
    ("disposition row outside a vendored root", "disposition", "tools/t.py",
     (("tools/t.py", "try:\n    import shinylib\nexcept ImportError:\n    pass\n"),), (),
     dict(dispositions=_FIXTURE_DISPOSITIONS + (("tools/t.py", "shinylib", "guarded-optional"),)), True),
    ("disposition row with an unknown condition", "disposition", "lib/_vendor/pkg/far.py", (), (),
     dict(dispositions=(_FIXTURE_DISPOSITIONS[0], ("lib/_vendor/pkg/far.py", "heavylib", "optional"))), True),
    # Premise change. A listed literal climbs into the call or subscript around it, so an edit that
    # turns that call into a called importer goes stale; getattr with a starred argument is refused; and the
    # remaining spelled importer forms (help whitespace, resolve_name colon, pydoc subscript owner, .__call__,
    # type(help), and the import-system members _find_and_load, iter_importers, _run_module_as_main and
    # _get_module_details) are read.
    ("literal argument climbed into its call", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\npatch(builtins, '__import__', x)('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: '__import__'",), "r"),)), True),
    ("literal subscript key climbed into its subscript", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\nd['__import__'] = builtins\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: '__import__'",), "r"),)), True),
    ("starred getattr reaching an importer refused at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "getattr(*(x,), '__import__')(''.join(('a', 'b')))\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: getattr(*(x,), '__import__')(''.join(('a', 'b')))",), "r"),)), True),
    ("help of a whitespace-padded name at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "help(' requests ')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help(' requests ')",), "r"),)), True),
    ("resolve_name of a module-object name at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pkgutil\npkgutil.resolve_name('requests:thing')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pkgutil",
                                        "#1: pkgutil.resolve_name('requests:thing')"), "r"),)), True),
    ("doc through vars(pydoc) subscript at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\nvars(pydoc)['doc']('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pydoc", "#1: pydoc"), "r"),)), True),
    ("render_doc through pydoc dunder dict subscript at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\npydoc.__dict__['render_doc']('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pydoc",
                                        "#1: pydoc.__dict__['render_doc']('requests')"), "r"),)), True),
    ("importer reached through dunder call at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\nbuiltins.__import__.__call__('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: builtins.__import__.__call__('requests')",), "r"),)), True),
    ("pydoc.locate through dunder call at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\npydoc.locate.__call__('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pydoc",
                                        "#1: pydoc.locate.__call__('requests')"), "r"),)), True),
    ("help through type(help) at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "type(help)()('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help",), "r"),)), True),
    ("doc through getattr(pydoc) at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\ngetattr(pydoc, 'doc')('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pydoc", "#1: pydoc"), "r"),)), True),
    ("builtin help helper class instance at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "_Helper()('requests')\n"),), (), {}, True),
    ("find_spec name keyword at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import importlib.util\nimportlib.util.find_spec(name='requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import importlib.util",
                                        "#1: importlib.util.find_spec(name='requests')"), "r"),)), True),
    ("getattr callee with a keyword at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, name='__import__')('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: getattr(builtins, name='__import__')('requests')",), "r"),)), True),
    # What the gate cannot read is a finding. A literal module name or level it cannot read with certainty
    # is refused, at an allowlisted site too; each such vector names a standard-library module, so only the refusal
    # (never import-closure's resolution of a third-party name) can make it red. The getattr refusal applies
    # after the .__call__ normalisation, which also reads getattr.__call__ and pydoc.doc.__call__ (third-party
    # names, read), a getattr name or subscript key written as an f-string with no placeholder is refused, and a
    # literal inside an f-string climbs into the call around it.
    ("literal module name with an empty component at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('json.')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__('json.')",), "r"),)), True),
    ("literal module name with a non-identifier component at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('json.0')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__('json.0')",), "r"),)), True),
    ("pydoc.locate of a name with a leading dot at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\npydoc.locate('.json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pydoc", "#1: pydoc.locate('.json')"), "r"),)), True),
    ("help of a name with a trailing dot at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "help('json.')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help('json.')",), "r"),)), True),
    ("pydoc.locate of a name with a trailing dot at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\npydoc.locate('json.')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pydoc", "#1: pydoc.locate('json.')"), "r"),)), True),
    ("pydoc.render_doc of a name with a repeated dot at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\npydoc.render_doc('json..thing')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pydoc", "#1: pydoc.render_doc('json..thing')"),
                                       "r"),)), True),
    ("f-string module name at an allowlisted importer call", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__(f'json')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__(f'json')",), "r"),)), True),
    ("__import__ level keyword written -0 at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('json', level=-0)\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__('json', level=-0)",), "r"),)),
     True),
    ("__import__ fifth argument written +0 at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('json', None, None, (), +0)\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: __import__('json', None, None, (), +0)",), "r"),)), True),
    ("starred getattr through dunder call at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(*(builtins,), '__import__').__call__(''.join(('a', 'b')))\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: getattr(*(builtins,), '__import__').__call__(''.join(('a', 'b')))",),
                                       "r"),)), True),
    ("double-starred getattr through dunder call at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, '__import__', **{}).__call__(''.join(('a', 'b')))\n"),),
     (), dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                           ("#1: getattr(builtins, '__import__', **{}).__call__("
                                            "''.join(('a', 'b')))",), "r"),)), True),
    ("getattr with a keyword through dunder call at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, name='__import__').__call__(''.join(('a', 'b')))\n"),),
     (), dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                           ("#1: getattr(builtins, name='__import__').__call__("
                                            "''.join(('a', 'b')))",), "r"),)), True),
    ("starred getattr reached through its own dunder call at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr.__call__(*(builtins,), '__import__')(''.join(('a', 'b')))\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: getattr.__call__(*(builtins,), '__import__')(''.join(('a', 'b')))",),
                                       "r"),)), True),
    ("getattr through dunder call reaching __import__ at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr.__call__(builtins, '__import__')('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: getattr.__call__(builtins, '__import__')('requests')",), "r"),)),
     True),
    ("getattr name as an f-string at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, f'__import__')(''.join(('a', 'b')))\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: getattr(builtins, f'__import__')(''.join(('a', 'b')))",), "r"),)),
     True),
    ("subscript key as an f-string at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\nvars(builtins)[f'__import__'](''.join(('a', 'b')))\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: vars(builtins)[f'__import__'](''.join(('a', 'b')))",), "r"),)),
     True),
    ("__import__ level keyword written None at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "__import__('json', level=None)\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__('json', level=None)",),
                                       "r"),)), True),
    ("pydoc.doc through dunder call at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\npydoc.doc.__call__('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pydoc", "#1: pydoc.doc.__call__('requests')"), "r"),)),
     True),
    ("literal inside an f-string climbed into its call", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\npatch(builtins, f'__import__', x)('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: '__import__'",), "r"),)), True),
    # A help request pydoc's Helper reads as a listing (HELP_LISTINGS) is refused whatever module of that
    # name the tree holds, so each listing vector holds an in-tree module of its word; and the .__call__
    # normalisation at the vars owner, the type callee and the getattr owner, and the climb out of an f-string's
    # replacement field, are each pinned.
    ("help of the modules listing beside an in-tree modules module", "import-closure", "tools/t.py",
     (("tools/t.py", "help('modules')\n"), ("tools/modules.py", "x = 1\n")), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help('modules')",), "r"),)), True),
    ("help of the whitespace-padded modules listing beside an in-tree modules module", "import-closure",
     "tools/t.py", (("tools/t.py", "help(' modules ')\n"), ("tools/modules.py", "x = 1\n")), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help(' modules ')",), "r"),)), True),
    ("pydoc.Helper instance listing modules beside an in-tree modules module", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\npydoc.Helper()('modules')\n"), ("tools/modules.py", "x = 1\n")), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pydoc", "#1: pydoc.Helper()('modules')"), "r"),)),
     True),
    ("help through getattr listing modules beside an in-tree modules module", "import-closure", "tools/t.py",
     (("tools/t.py", "import builtins\ngetattr(builtins, 'help')('modules')\n"), ("tools/modules.py", "x = 1\n")),
     (), dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                           ("#1: getattr(builtins, 'help')('modules')",), "r"),)), True),
    ("help through type(help) listing modules beside an in-tree modules module", "import-closure", "tools/t.py",
     (("tools/t.py", "type(help)()('modules')\n"), ("tools/modules.py", "x = 1\n")), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help",), "r"),)), True),
    ("help of the keywords listing beside an in-tree keywords module", "import-closure", "tools/t.py",
     (("tools/t.py", "help('keywords')\n"), ("tools/keywords.py", "x = 1\n")), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help('keywords')",), "r"),)), True),
    ("help of the symbols listing beside an in-tree symbols module", "import-closure", "tools/t.py",
     (("tools/t.py", "help('symbols')\n"), ("tools/symbols.py", "x = 1\n")), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help('symbols')",), "r"),)), True),
    ("help of the topics listing beside an in-tree topics module", "import-closure", "tools/t.py",
     (("tools/t.py", "help('topics')\n"), ("tools/topics.py", "x = 1\n")), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help('topics')",), "r"),)), True),
    ("literal inside an f-string replacement field climbed into its call", "dynamic-import", "tools/t.py",
     (("tools/t.py", "import builtins\npatch(builtins, f'{\"__import__\"}', x)('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: '__import__'",), "r"),)), True),
    ("doc through vars dunder call subscript at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\nvars.__call__(pydoc)['doc']('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import pydoc", "#1: pydoc"),
                                       "r"),)), True),
    ("help through type dunder call at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "type.__call__(help)()('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help",), "r"),)), True),
    ("doc through getattr dunder call owner at an allowlisted site", "import-closure", "tools/t.py",
     (("tools/t.py", "import pydoc\ngetattr.__call__(pydoc, 'doc')('requests')\n"),), (),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import pydoc", "#1: pydoc"),
                                       "r"),)), True),
)

# (name, extra rows, scan keyword overrides): each must pass (exit 0); each pins a reading the gate must not widen.
_GREEN_VECTORS = (
    ("__import__ at a non-zero level keyword", (("tools/t.py", "__import__('requests', level=1)\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: __import__('requests', level=1)",),
                                       "r"),))),
    ("__import__ at a non-zero level argument", (("tools/t.py", "__import__('requests', None, None, (), 1)\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: __import__('requests', None, None, (), 1)",), "r"),))),
    ("import_module of a relative name",
     (("tools/t.py", "import importlib\nimportlib.import_module('.requests', 'pkg')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import importlib",
                                                                    "#1: importlib.import_module('.requests', "
                                                                    "'pkg')"), "r"),))),
    ("run_path of a file path", (("tools/t.py", "import runpy\nrunpy.run_path('requests')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: import runpy",
                                                                    "#1: runpy.run_path('requests')"), "r"),))),
    ("a local function named like a pydoc helper",
     (("tools/t.py", "def doc(*rows):\n    return rows\ndoc(*'ab')\ndoc('x')\n"),), dict()),
    ("help as a keyword argument's name", (("tools/t.py", "def f(**k):\n    return k\nf(help='x')\n"),), dict()),
    ("two identical calls on one line, each listed",
     (("tools/t.py", "def f(x):\n    __import__(x); x = 'json'; __import__(x)\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__(x)", "#2: __import__(x)"),
                                       "r"),))),
    ("plain literal names at importers, each listed",
     (("tools/t.py", "__import__('json', level=0)\nhelp(' json ')\nimport pydoc\npydoc.locate('json.decoder')\n"
                     "pydoc.render_doc('json')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: __import__('json', level=0)", "#1: help(' json ')",
                                        "#1: import pydoc", "#1: pydoc.locate('json.decoder')",
                                        "#1: pydoc.render_doc('json')"), "r"),))),
    ("help of a whitespace-padded standard-library name", (("tools/t.py", "help(' json ')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help(' json ')",), "r"),))),
    ("resolve_name of a standard-library module-object name",
     (("tools/t.py", "import pkgutil\npkgutil.resolve_name('json:loads')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import pkgutil", "#1: pkgutil.resolve_name('json:loads')"),
                                       "r"),))),
    ("import_module of an f-string with a placeholder (a computed name)",
     (("tools/t.py", "import importlib\nimportlib.import_module(f'json.{x}')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE,
                                       ("#1: import importlib", "#1: importlib.import_module(f'json.{x}')"),
                                       "r"),))),
    ("help of a standard-library name beside an in-tree modules module",
     (("tools/t.py", "help('json')\n"), ("tools/modules.py", "x = 1\n")),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", MODULE_SCOPE, ("#1: help('json')",), "r"),))),
    # No line or column is part of a row: unrelated lines added above every listed site, or a site moved within its
    # scope, leave every row valid, and identical references are told apart by their ordinal in their own scope.
    ("unrelated lines inserted above every listed site",
     (("tools/a.py", "\n# an unrelated comment\ndef unrelated():\n    return 1\n\n" + _A_PY),), dict()),
    ("allowlisted reference moved to another line in its scope",
     (("tools/t.py", "def f():\n    x = 1\n    return __import__('json')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "r"),))),
    ("identical references in one scope, each listed by its ordinal",
     (("tools/t.py", "def f():\n    __import__('json')\n    return __import__('json')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')", "#2: __import__('json')"),
                                       "r"),))),
    ("identical references in two scopes, each the first in its own",
     (("tools/t.py", "def f():\n    return __import__('json')\ndef g():\n    return __import__('json')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f", ("#1: __import__('json')",), "r"),
                                      ("tools/t.py", "g", ("#1: __import__('json')",), "r")))),
    # A watched name bound in the scope that reads it needs no row.
    ("a local bound in the scope that reads it, no row",
     (("tools/t.py", "def f(record):\n    imp = record.get('import')\n    return imp.get('run')\n"),), dict()),
    # A closure read is a site (the lambda reads f's local as a free variable), so it needs its row.
    ("a def's plain local read from a lambda it holds, its row reviewed",
     (("tools/t.py", "def f(record):\n    imp = record.get('import')\n    return lambda: imp.get('run')\n"),),
     dict(allowlist=_FIXTURE_SITES + (("tools/t.py", "f.<lambda>", ("#1: imp.get('run')",), "r"),))),
    # A row is live through the insertion that resolves to its root: the own-directory insertion beside it
    # changes nothing. The paired control of the own-directory vector: its fixture plus that insertion only.
    ("own-directory and recorded-root insertions together",
     (("tools/t.py", _T_OWN + _T_PLANTED + _T_VENDOR_LINE + "import pkg\n"),), dict(search_roots=_T_ROOTS)),
    # Positive controls of the closed set of insertion shapes, one per shape this tree's rows write, each paired
    # with the cannot-evaluate vectors that change it.
    ("insertion of a name bound once to a read shape", (("tools/t.py", _T_NAMED % ""),),
     dict(search_roots=_T_ROOTS)),
    ("insertion through a name bound once to a pathlib chain", (("tools/t.py", _T_ONE_NAME % ""),),
     dict(search_roots=_T_ROOTS)),
    # At the name bound: two names, the second bound to the chain the first starts at (one more is refused below).
    ("insertion through two names, each bound once to a pathlib chain",
     (("tools/t.py", _T_TWO_NAMES % ""),), dict(search_roots=_T_ROOTS)),
    ("insertion through repo_root imported from _gen_common",
     (("tools/t.py", _T_PATH + "from _gen_common import repo_root\n"
                     "sys.path.insert(0, str(repo_root() / 'lib' / '_vendor'))\nimport pkg\n"), _T_GEN_COMMON),
     dict(search_roots=_T_ROOTS)),
    ("insertion through the os.path nesting",
     (("tools/t.py", _T_PATH + "sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "
                     "'sub'))\nimport m\n"), ("tools/sub/m.py", "x = 1\n")),
     dict(search_roots=_FIXTURE_ROOTS + (("tools/t.py", "tools/sub"),))),
    ("insertion through .parents at the highest index read",
     ((_T_DEEP, _T_PATH + "sys.path.insert(0, str(Path(__file__).resolve().parents[16] / 'lib' / '_vendor'))\n"
                "import pkg\n"),), dict(search_roots=_FIXTURE_ROOTS + ((_T_DEEP, "lib/_vendor"),))),
)


def _name_vectors(names, members):
    """One rule vector per watched name and member, each its only site: removing it from the watched set must turn
    its vector green (the self-test removes each in turn)."""
    return tuple(("watched name " + name, "dynamic-import", "tools/t.py",
                  (("tools/t.py", "def f(o):\n    return o.%s\n" % name),), (), {}, True)
                 for name in sorted(names | members))


def _importer_vectors():
    """One import-closure vector per importer of a module name, a third-party name passed by the last keyword of
    its module-name parameter, on an object (through its module when the row names one), at a site allowlisted by
    its own references: removing the
    importer's row must turn its vector green (the self-test removes each in turn)."""
    out = []
    for callee, keywords, _level, kind, owner in LITERAL_IMPORTERS:
        if kind == "path":
            continue
        source = ("import %s\n%s" % (owner, owner) if owner else "o") + ".%s(%s='requests')\n" % (callee, keywords[-1])
        refs = tuple(key for key, _scope, _names, _called, _line in dynamic_sites(ast.parse(source), source,
                                                                          *watched_names()))
        out.append(("importer " + callee, "import-closure", "tools/t.py", (("tools/t.py", source),), (),
                    dict(allowlist=_FIXTURE_SITES + ((("tools/t.py", MODULE_SCOPE, refs, "r"),) if refs else ())),
                    True))
    return tuple(out)


# (name, source, the sites dynamic_sites must find, exactly, each as SCOPE | REFERENCE)
_SITE_VECTORS = (
    ("alias in a function, use at module level",
     "def unrelated():\n    from builtins import print as __import__\n__import__('json')\n",
     ("unrelated | #1: from builtins import print as __import__", "<module> | #1: __import__('json')")),
    ("submodule import and from-import", "import importlib.util as u\nfrom importlib import machinery\n",
     ("<module> | #1: import importlib.util as u", "<module> | #1: from importlib import machinery")),
    ("parameter and attribute in a nested class method",
     "class A:\n    class B:\n        def m(self, run_path=None):\n            return self.find_spec\n"
     "        x = run_path\n",
     ("class A.class B.m | #1: self.find_spec", "class A.class B | #1: run_path")),
    ("whole dotted string and bytes literals only", "x = 'importlib.util'\ny = 'use importlib'\nz = b'__import__'\n",
     ("<module> | #1: 'importlib.util'", "<module> | #1: b'__import__'")),
    ("global and keyword names", "def f():\n    global imp\n    g(run_module=1)\n",
     ("f | #1: global imp", "f | #1: run_module=1")),
    ("names outside the set", "import os\nos.path.join('a')\nimporter = 1\n", ()),
    ("async def scope", "async def f():\n    return __import__('json')\n", ("f | #1: __import__('json')",)),
    ("definition-time expressions in the enclosing scope",
     "class C(__import__('json').JSONDecoder):\n    @x.find_spec\n    def m(self, a: importlib = __import__):\n"
     "        return 1\n",
     ("<module> | #1: __import__('json').JSONDecoder", "class C | #1: x.find_spec", "class C | #1: importlib",
      "class C | #1: __import__")),
    ("a def's name in the enclosing scope", "class C:\n    def find_spec(self):\n        pass\n",
     ("class C | #1: def find_spec",)),
    ("lambda body in a scope of its own", "f = lambda: __import__('json')\n",
     ("<lambda> | #1: __import__('json')",)),
    ("one reference per attribute chain and call",
     "s = importlib.util.spec_from_file_location('x',\n    'y')\n",
     ("<module> | #1: importlib.util.spec_from_file_location('x', 'y')",)),
    ("subscript climbed into its reference", "x = __path__[0]\n", ("<module> | #1: __path__[0]",)),
    ("except clause by its header", "try:\n    pass\nexcept OSError as __import__:\n    pass\n",
     ("<module> | #1: except as __import__",)),
    ("import-system members as attributes and literals only",
     "def _gcd_import(_load):\n    return _gcd_import(_load)\nx = o._load\ny = '_gcd_import'\n",
     ("<module> | #1: o._load", "<module> | #1: '_gcd_import'")),
    ("identical references numbered in source order within each scope",
     "def f():\n    x = __import__\n    return __import__\ndef g():\n    return __import__\n",
     ("f | #1: __import__", "f | #2: __import__", "g | #1: __import__")),
    # Each lexical scope instance is its own scope: same-named siblings are numbered in source order within the scope
    # that runs their definitions, each with their count, and a name alone in its scope is not.
    ("sibling lambdas numbered in source order", "a = lambda: __import__('a')\nb = lambda: __import__('b')\n",
     ("<lambda>#1/2 | #1: __import__('a')", "<lambda>#2/2 | #1: __import__('b')")),
    ("identical references in sibling lambdas, each the first in its own",
     "a = lambda x: __import__(x)\nb = lambda x: __import__(x)\n",
     ("<lambda>#1/2 | #1: __import__(x)", "<lambda>#2/2 | #1: __import__(x)")),
    ("same-named defs numbered, a def of another name not",
     "if X:\n    def f():\n        return __import__\nelse:\n    def f():\n        return __import__\ndef g():\n"
     "    return __import__\n",
     ("f#1/2 | #1: __import__", "f#2/2 | #1: __import__", "g | #1: __import__")),
    ("lambdas in two defs, each alone in its own",
     "def f():\n    return lambda: __import__\ndef g():\n    return lambda: __import__\n",
     ("f.<lambda> | #1: __import__", "g.<lambda> | #1: __import__")),
    ("a default's lambda numbered in the scope that runs the definition",
     "def f(a=lambda: __import__):\n    return lambda: __import__\ng = lambda: __import__\n",
     ("<lambda>#1/2 | #1: __import__", "f.<lambda> | #1: __import__", "<lambda>#2/2 | #1: __import__")),
    # A comprehension or generator expression is not a scope for rows: what it holds is listed under the scope
    # around it (and nothing it holds is ever exempt: the comprehension and generator-expression vectors below). A
    # regression pin, not red on revert: the gate listed it this way before this boundary was disclosed, so it
    # fails only against a later change to that listing.
    ("comprehension and generator expression read in the def around them",
     "def f(a):\n    return [__import__(n) for n in a], (__import__(n) for n in a)\n",
     ("f | #1: __import__(n)", "f | #2: __import__(n)")),
    # Only a name Python's own compile-time scoping (symtable: _kept)
    # proves a plain local of a def or lambda, read in that function's own body, is suppressed. Each
    # function-local binding form suppresses its body occurrences; an import of the watched module, a global or
    # nonlocal declared name, a module-scope or class-body binding (a conditional, deleted, later or unexecuted
    # one included), a comprehension target, every reference in a file with a * import, an ambiguity and a
    # dunder name keep every site.
    ("a parameter of the def that reads it", "def f(imp):\n    return imp\n", ()),
    ("a loop target", "def f(rows):\n    for imp in rows:\n        rows.append(imp)\n", ()),
    ("a comprehension target and its reads in the def around them",
     "def f(rows):\n    return [imp for imp in rows if imp]\n",
     ("f | #1: imp", "f | #2: imp", "f | #3: imp")),
    ("a with target", "def f(c):\n    with c as imp:\n        return imp\n", ()),
    ("an except target", "def f():\n    try:\n        return 1\n    except OSError as imp:\n        return imp\n",
     ()),
    ("a walrus target", "def f(x):\n    if (imp := x):\n        return imp\n    return None\n", ()),
    ("a match capture", "def f(x):\n    match x:\n        case imp:\n            return imp\n    return None\n",
     ()),
    ("a deleted name", "def f(x):\n    imp = x\n    del imp\n    return None\n", ()),
    ("a nonlocal declaration naming an enclosing def's local",
     "def f():\n    imp = 1\n    def g():\n        nonlocal imp\n        imp = 2\n        return imp\n"
     "    return g\n", ("f.g | #1: nonlocal imp", "f.g | #1: imp", "f.g | #2: imp")),
    ("a def's name read at module level, the header and the read each a site",
     "def find_spec(x):\n    return x\ny = find_spec\n",
     ("<module> | #1: def find_spec", "<module> | #1: find_spec")),
    ("an import of the watched module in the scope that reads it",
     "def f():\n    import imp\n    return imp\ndef g():\n    imp = 1\n    return imp\n",
     ("f | #1: import imp", "f | #1: imp")),
    ("a global declaration naming a module-level import",
     "import imp\ndef f():\n    global imp\n    imp = f()\n    return imp\ndef g():\n    imp = 1\n"
     "    return imp\n",
     ("<module> | #1: import imp", "f | #1: global imp", "f | #1: imp", "f | #2: imp")),
    ("a global declaration reading past an enclosing def's local",
     "def outer():\n    imp = 1\n    def inner():\n        global imp\n        return imp\n",
     ("outer.inner | #1: global imp", "outer.inner | #1: imp")),
    ("a name bound only in an unrelated scope",
     "def f():\n    imp = 1\n    return imp\nx = imp\n", ("<module> | #1: imp",)),
    ("a class body's binding a site, and not read from the def it holds",
     "class C:\n    imp = 1\n    def m(self):\n        return imp\n",
     ("class C | #1: imp", "class C.m | #1: imp")),
    ("a dunder bound by assignment stays a site",
     "__path__ = ['x']\ndef f():\n    imp = 1\n    return imp\n", ("<module> | #1: __path__",)),
    ("a dunder bound in a def stays a site",
     "def f():\n    __path__ = ['x']\n    return __path__\n", ("f | #1: __path__", "f | #2: __path__")),
    # A binding Python looks up at run time proves nothing, so it keeps every site: a module
    # binding (conditional, deleted, later or unexecuted), a class-body binding, a * import's file, a global or
    # nonlocal declared name, a comprehension target and an ambiguity.
    ("a module binding under a false branch keeps the builtin's sites",
     "if False:\n    help = 1\nloader = help\nloader('json')\n",
     ("<module> | #1: help", "<module> | #2: help")),
    ("an unexecuted global write keeps the module read a site",
     "def f():\n    global help\n    help = 1\nx = help\n",
     ("f | #1: global help", "f | #1: help", "<module> | #1: help")),
    ("a deleted module binding keeps every site",
     "help = None\ndel help\ndef show(topic):\n    return help(topic)\n",
     ("<module> | #1: help", "<module> | #2: help", "show | #1: help(topic)")),
    ("an except target at module level keeps every site",
     "try:\n    pass\nexcept OSError as help:\n    pass\ndef show(topic):\n    return help(topic)\n",
     ("<module> | #1: except as help", "show | #1: help(topic)")),
    ("a module binding after the def that reads the name",
     "def show(t):\n    return help(t)\nhelp = print\n",
     ("show | #1: help(t)", "<module> | #1: help")),
    ("a class-body read before the class binds the name",
     "import pydoc\nclass C:\n    loader = pydoc.importfile\n    pydoc = None\n",
     ("<module> | #1: import pydoc", "class C | #1: pydoc.importfile", "class C | #1: pydoc")),
    ("a class-body read after an except target",
     "import pydoc\nclass C:\n    try:\n        pass\n    except OSError as pydoc:\n        pass\n"
     "    f = pydoc.importfile\n",
     ("<module> | #1: import pydoc", "class C | #1: except as pydoc", "class C | #1: pydoc.importfile")),
    ("a global declaration between an enclosing local and an inner read",
     "import importlib\ndef outer():\n    importlib = 1\n    def middle():\n        global importlib\n"
     "        def inner():\n            return importlib\n        return inner\n    return middle()\n",
     ("<module> | #1: import importlib", "outer.middle | #1: global importlib",
      "outer.middle.inner | #1: importlib")),
    ("a global declaration between a module binding and an inner read",
     "importlib = 0\ndef outer():\n    import importlib\n    def middle():\n        global importlib\n"
     "        def inner():\n            return importlib\n        return inner\n    return middle()\n",
     ("<module> | #1: importlib", "outer | #1: import importlib", "outer.middle | #1: global importlib",
      "outer.middle.inner | #1: importlib")),
    ("a lambda behind a global declaration",
     "import importlib\ndef outer():\n    importlib = 1\n    def middle():\n        global importlib\n"
     "        return lambda: importlib\n    return middle()\n",
     ("<module> | #1: import importlib", "outer.middle | #1: global importlib",
      "outer.middle.<lambda> | #1: importlib")),
    ("a named binding beside a * import",
     "help = 1\nfrom pydoc import *\nresult = help\n",
     ("<module> | #1: help", "<module> | #1: from pydoc import *", "<module> | #2: help")),
    ("a * import keeps a def's plain local a site",
     "from pydoc import *\ndef f():\n    imp = 1\n    return imp\n",
     ("<module> | #1: from pydoc import *", "f | #1: imp", "f | #2: imp")),
    ("a comprehension target in a class body",
     "class C:\n    xs = [imp for imp in (1,)]\n", ("class C | #1: imp", "class C | #2: imp")),
    ("a generator-expression target shadowing a def's local",
     "def f(rows):\n    imp = 1\n    return (x for imp in rows)\n", ("f | #1: imp", "f | #2: imp")),
    ("a def's import read from a lambda it holds",
     "def f():\n    import imp\n    return lambda: imp\n", ("f | #1: import imp", "f.<lambda> | #1: imp")),
    ("an import under a nonlocal declaration taints the enclosing local",
     "def f():\n    imp = 1\n    def g():\n        nonlocal imp\n        import imp\n    g()\n    return imp\n",
     ("f | #1: imp", "f.g | #1: nonlocal imp", "f.g | #1: import imp", "f | #2: imp")),
    ("two lambdas on one line, each scope unmatched",
     "pair = (lambda imp: imp, lambda imp: imp)\n",
     ("<lambda>#1/2 | #1: imp", "<lambda>#1/2 | #2: imp", "<lambda>#2/2 | #1: imp", "<lambda>#2/2 | #2: imp")),
    ("a match star target",
     "def f(x):\n    match x:\n        case [*imp]:\n            return imp\n    return None\n", ()),
    ("a match mapping rest target",
     "def f(x):\n    match x:\n        case {**imp}:\n            return imp\n    return None\n", ()),
    ("a deletion-only local", "def f():\n    del imp\n    return imp\n", ()),
    ("a free read through an intervening def",
     "def f():\n    imp = 1\n    def g():\n        def h():\n            return imp\n        return h\n"
     "    return g\n", ("f.g.h | #1: imp",)),
    ("a generator-expression read of a name no def binds",
     "def f(rows):\n    return (imp for r in rows)\n", ("f | #1: imp",)),
    # Nothing a generator expression holds is exempt (its first iterable included), and a target name
    # it binds keeps every reference in the whole function, the shadowed def local's own binding among them; a
    # free read inside one is a site like every closure read.
    ("a generator-expression target read in its body",
     "def f(rows):\n    help = 0\n    return (help for help in rows)\n",
     ("f | #1: help", "f | #2: help", "f | #3: help")),
    ("a generator-expression target called in its body",
     "def f(rows):\n    help = 0\n    return (help('json') for help in rows)\n",
     ("f | #1: help", "f | #1: help('json')", "f | #2: help")),
    ("a generator-expression target read in its condition",
     "def f(rows):\n    help = 0\n    return (x for help in rows if help)\n",
     ("f | #1: help", "f | #2: help", "f | #3: help")),
    ("a generator-expression free read of an enclosing local",
     "def f(rows):\n    imp = 1\n    return (imp for x in rows)\n", ("f | #1: imp",)),
    ("a generator expression's first iterable read, kept with the expression",
     "def f():\n    help = 0\n    return (x for x in help)\n", ("f | #1: help",)),
    ("a walrus target in a generator expression's condition",
     "def f(rows):\n    imp = 0\n    return (1 for x in rows if (imp := x))\n", ("f | #1: imp",)),
    ("nested generator expressions on one line, kept with the def around them",
     "def f(rows):\n    return ((imp for imp in r) for r in rows)\n", ("f | #1: imp", "f | #2: imp")),
    ("a def named genexpr beside a generator expression on its line",
     "def genexpr(rows): return (imp for r in rows)\n", ("genexpr | #1: imp",)),
    # Nothing a type-parameter scope holds is exempt (a bound, a generic class's base, a generic
    # def's annotation, a generic type alias's value), and neither is a def's body read of a name its own type
    # parameter binds (a free variable there, kept like every closure read).
    ("a type parameter shadowing an enclosing local, read in the def's body",
     "def outer():\n    help = 0\n    def inner[help]():\n        return help\n    return inner\n",
     ("outer | #1: help", "outer.inner | #1: help")),
    ("a generic class's base read in its type-parameter scope",
     "def outer():\n    help = 0\n    class C[help](help):\n        pass\n    return C\n",
     ("outer | #1: help", "outer | #2: help")),
    ("a generic type alias's value read in its type-parameter scope",
     "def outer():\n    help = 0\n    type Alias[help] = help\n    return Alias\n",
     ("outer | #1: help", "outer | #2: help")),
    ("a type parameter's bound read in its type-parameter scope",
     "def outer():\n    help = 0\n    def g[help, T: help]():\n        return T\n    return g\n",
     ("outer | #1: help", "outer | #2: help")),
    ("a generic def's annotation read in its type-parameter scope",
     "def outer():\n    help = 0\n    def g[help](x: help):\n        return x\n    return g\n",
     ("outer | #1: help", "outer | #2: help")),
    # A generic def's defaults stay sites as a stated conservative policy, not by the floor
    # version's scoping: it evaluates a def's defaults in the enclosing scope, generic or not (def f[T](x=T)
    # raises NameError, and def f[help](x=help) reads the enclosing help, never the type parameter), so the
    # keep can only over-report; these two default vectors pin the kept sites, and each catches the
    # generic-defaults widening mutant that drops only the not-generic condition on the defaults line. The
    # bound vector after them catches the widening of a type alias's type-parameter list.
    ("a generic def's positional default read where the def runs, kept as a site",
     "def outer():\n    help = 0\n    def g[T](x=help):\n        return x\n    return g\n",
     ("outer | #1: help",)),
    ("a generic def's keyword-only default read where the def runs, kept as a site",
     "def outer():\n    help = 0\n    def g[T](*, x=help):\n        return x\n    return g\n",
     ("outer | #1: help",)),
    ("a generic type alias's bound read in its type-parameter scope",
     "def outer():\n    help = 0\n    type A[T: help] = int\n    return A\n",
     ("outer | #1: help",)),
    # The exemption is a binding rule, not a value proof. A plain local can still hold import machinery
    # obtained another way (a parameter, an eval or another unwatched dynamic call), and the gate reports nothing
    # for it: the disclosed residual, pinned here. A source symtable rejects keeps every site.
    ("a parameter named help, read and called", "def f(help):\n    return help('json')\n", ()),
    ("a local bound from eval, read and called",
     "def f():\n    help = eval('he' + 'lp')\n    return help('json')\n", ()),
    ("a source symtable rejects keeps every site",
     "def f():\n    imp = 1\n    global imp\n    return imp\n",
     ("f | #1: imp", "f | #1: global imp", "f | #2: imp")),
    # A read inside a comprehension target keeps its site, and sweeps the name into the function's
    # comprehension-bound names (the shadowed local's own binding is then a site too); a name its scope's table
    # does not hold keeps its site (a function-body annotation is dropped from the table, never evaluated).
    ("a comprehension target's subscript read kept with it",
     "def f(rows, d):\n    imp = 1\n    return [x for d[imp] in rows]\n", ("f | #1: imp", "f | #2: imp")),
    ("an annotation read of a name no def binds",
     "def f():\n    x: imp = 1\n    return x\n", ("f | #1: imp",)),
    # A type-parameter or annotation scope directly in a class body reads the class
    # namespace first, so resolving past the class to an enclosing def's local suppressed wrongly; now nothing
    # in an annotation, a type-parameter scope or a type alias's value is exempt, in a class body or anywhere.
    ("a generic method's annotation in a class body, read there",
     "def outer():\n    help = 0\n    class C:\n        def m[T](self, x: help):\n            return x\n"
     "    return C\n", ("outer.class C | #1: help",)),
    ("a type parameter's bound in a class body, read there",
     "def outer():\n    help = 0\n    class C:\n        def m[T: help](self):\n            return self\n"
     "    return C\n", ("outer.class C | #1: help",)),
    ("a generic class's base in a class body, read there",
     "def outer():\n    help = 0\n    class C:\n        class D[T](help):\n            pass\n    return C\n",
     ("outer.class C | #1: help",)),
    ("a generic type alias's value in a class body, read there",
     "def outer():\n    help = 0\n    class C:\n        type A[T] = help\n    return C\n",
     ("outer.class C | #1: help",)),
    ("a generic def's return annotation in a class body, read there",
     "def outer():\n    help = 0\n    class C:\n        def m[T](self) -> help:\n            return self\n"
     "    return C\n", ("outer.class C | #1: help",)),
    ("a def's parameter annotation read where the def runs",
     "def outer():\n    help = 0\n    def g(x: help):\n        return x\n    return g\n",
     ("outer | #1: help",)),
    ("a def's return annotation read where the def runs",
     "def outer():\n    help = 0\n    def g() -> help:\n        return 1\n    return g\n",
     ("outer | #1: help",)),
    ("a type alias's value read where the type statement runs",
     "def outer():\n    help = 0\n    type A = help\n    return A\n", ("outer | #1: help",)),
    # The floor version inlines a list, set or dict comprehension into the def around
    # it, and the def's merged symbol for a shadowed target reads as a plain local, so the table alone proved
    # the wrong binding; now nothing a comprehension holds is exempt, and a name a comprehension binds as a
    # target is kept everywhere in that function, the shadowed binding and a later body read among them.
    ("a list comprehension's body read of a target shadowing a def's local",
     "def f(rows):\n    help = 0\n    return [help for help in rows]\n",
     ("f | #1: help", "f | #2: help", "f | #3: help")),
    ("a list comprehension's target called in its body, shadowing a def's local",
     "def f(rows):\n    help = 0\n    return [help('json') for help in rows]\n",
     ("f | #1: help", "f | #1: help('json')", "f | #2: help")),
    ("a set comprehension's condition read of a target shadowing a def's local",
     "def f(rows):\n    help = 0\n    return {x for help in rows if help}\n",
     ("f | #1: help", "f | #2: help", "f | #3: help")),
    ("a dict comprehension's target called in its key, shadowing a def's local",
     "def f(rows):\n    help = 0\n    return {help('json'): 1 for help in rows}\n",
     ("f | #1: help", "f | #1: help('json')", "f | #2: help")),
    ("a comprehension in an annotation, its target shadowing an enclosing local",
     "def outer(rows):\n    help = 0\n    def g(x: [help('j') for help in rows]):\n        return x\n"
     "    return g\n", ("outer | #1: help", "outer | #1: help('j')", "outer | #2: help")),
    ("a def-body read after a comprehension binds the name",
     "def f(rows):\n    help = 0\n    xs = [1 for help in rows]\n    return help('json')\n",
     ("f | #1: help", "f | #2: help", "f | #1: help('json')")),
    ("a list comprehension's read of an enclosing def's local",
     "def f(rows):\n    imp = 1\n    return [imp for x in rows]\n", ("f | #1: imp",)),
    # A closure read is a site; the exemption covers a reference in the one def or
    # lambda body whose own table binds the name, and nothing read across a scope boundary.
    ("a closure read of a def's plain local",
     "def f():\n    imp = 1\n    def g():\n        return imp\n    return g\n", ("f.g | #1: imp",)),
    ("a plain def local read and called, no site",
     "def f(x):\n    help = x\n    return help('json')\n", ()),
)

# (name, source before, source after, the lines of the findings the after source gives as tools/t.py, exactly, 0 for a
# row's own): the rows are the ones the gate under test writes for the before source and for the fixture's own
# tools/a.py, so each vector reads one edit the way that gate reads it. The before source must pass under those rows
# (the control), and every finding on the after source must be a dynamic-import finding in tools/t.py, or the vector
# fails without crediting a mutant (_UNCREDITED): a mutant that changes every key alike writes the rows with it, so it
# is caught only where the edit itself reads differently. A vector holds against this gate and fails against one
# whose scope identity merges distinct lexical scopes (the identity before scope instances were told apart), or, where
# the edit adds or removes a same-named sibling, against one whose identity leaves out the count of same-named
# siblings; the vectors after those say what else they fail against.
_MOVE_VECTORS = (
    ("reference moved into a sibling lambda",
     "a = lambda x: __import__(x)\nb = lambda x: None\n", "a = lambda x: None\nb = lambda x: __import__(x)\n", (0, 2)),
    ("reference moved into a same-named def",
     "if X:\n    def f(x):\n        return __import__(x)\nelse:\n    def f(x):\n        return None\n",
     "if X:\n    def f(x):\n        return None\nelse:\n    def f(x):\n        return __import__(x)\n", (0, 6)),
    ("identical reference added to an earlier sibling lambda, the later one still listed",
     "a = lambda x: None\nb = lambda x: __import__(x)\n", "a = lambda x: __import__(x)\nb = lambda x: __import__(x)\n",
     (1,)),
    ("class around a method turned into a def of its name",
     "class C:\n    def m(self):\n        return __import__(x)\n",
     "def C():\n    def m(self):\n        return __import__(x)\n", (0, 3)),
    # A same-named sibling added or removed changes the count, so a reference moved into the sibling that takes its
    # ordinal is a finding (and so is every row under a sibling of that name, an over-rejection).
    ("sibling lambda inserted ahead, reference moved into the one taking its ordinal",
     "a = lambda x: None\nb = lambda x: __import__(x)\n",
     "z = lambda x: None\na = lambda x: __import__(x)\nb = lambda x: None\n", (0, 2)),
    ("sibling lambda removed ahead, reference moved into the one taking its ordinal",
     "z = lambda: 0\na = lambda x: __import__(x)\nb = lambda x: None\n",
     "a = lambda x: None\nb = lambda x: __import__(x)\n", (0, 2)),
    ("sibling lambda holding the reference inserted ahead, the one listed emptied",
     "a = lambda x: __import__(x)\nb = lambda x: None\n",
     "c = lambda x: __import__(x)\na = lambda x: None\nb = lambda x: None\n", (0, 1)),
    ("same-named def removed ahead, reference moved into the one taking its ordinal",
     "if X:\n    def f(x):\n        return None\nelif Y:\n    def f(x):\n        return __import__(x)\nelse:\n"
     "    def f(x):\n        return None\n",
     "if Y:\n    def f(x):\n        return None\nelse:\n    def f(x):\n        return __import__(x)\n", (0, 6)),
    ("lambda removed elsewhere in a def, reference moved into the sibling taking its ordinal",
     "def v():\n    t = [lambda: 0]\n    u = lambda x: __import__(x)\n    w = lambda x: None\n",
     "def v():\n    u = lambda x: None\n    w = lambda x: __import__(x)\n", (0, 3)),
    ("same-named sibling added after a listed scope, its row stale (an over-rejection)",
     "a = lambda x: __import__(x)\nb = lambda x: None\n",
     "a = lambda x: __import__(x)\nb = lambda x: None\nc = lambda x: None\n", (0, 1)),
    # These hold against this gate and fail against a mutant (MUTANTS) that counts siblings of other names, or puts a
    # column or a line into the key.
    ("def of another name added beside a listed scope", "def f(x):\n    return __import__(x)\n",
     "def g(x):\n    return None\ndef f(x):\n    return __import__(x)\n", ()),
    ("reference moved within its lambda", "a = lambda x: (x, __import__(x))\nb = lambda x: None\n",
     "a = lambda x: (__import__(x), x)\nb = lambda x: None\n", ()),
    ("unrelated lines inserted above a listed site", "def f(x):\n    return __import__(x)\n",
     "def u():\n    return 1\n\n\ndef f(x):\n    return __import__(x)\n", ()),
    # Regression pins of a disclosed boundary, not red on revert (the gate read them this way before the boundary was
    # disclosed): a type alias's value and a type parameter's bound, though the floor version evaluates each only
    # when it is read, and a generic class's bases and keywords, though it evaluates them at once in the annotation
    # scope of the class's type parameters, are read in the scope that runs the type statement or the definition, so
    # a move from any of them into a statement beside it is a move within that scope. The second and the third also
    # fail against a mutant that reads what a definition holds in the definition's own scope (MUTANTS binds that
    # mutation to the second only).
    ("type alias value moved into a statement beside it", "type T = __import__(x)\n",
     "type T = int\nv = __import__(x)\n", ()),
    ("type-parameter bound moved into a statement beside it", "def f[U: __import__(x)](y):\n    return y\n",
     "def f[U: int](y):\n    return y\nv = __import__(x)\n", ()),
    ("generic class's base and keyword moved into statements beside the class",
     "class C[U](__import__(x), metaclass=__import__(y)):\n    pass\n",
     "class C[U]:\n    pass\nv = __import__(x)\nw = __import__(y)\n", ()),
    # Nothing a generator expression holds is exempt, the rows unchanged: an edit
    # that adds a read, call or condition naming the generator's target is a finding (the shadowed target was
    # listed, so a stale row or an unlisted reference discriminates).
    ("call added to a generator-expression target read, its rows unchanged",
     "def f(rows):\n    help = 0\n    return (help for help in rows)\n",
     "def f(rows):\n    help = 0\n    return (help('json') for help in rows)\n", (0, 3)),
    ("condition added to a generator expression, its rows unchanged",
     "def f(rows):\n    help = 0\n    return (x for help in rows)\n",
     "def f(rows):\n    help = 0\n    return (x for help in rows if help)\n", (3,)),
    ("target read added to a generator expression, its rows unchanged",
     "def f(rows):\n    help = 0\n    return (0 for help in rows)\n",
     "def f(rows):\n    help = 0\n    return (help for help in rows)\n", (3,)),
    # The class-namespace and inlined-comprehension reproductions, end to end: with the rows the gate
    # writes for the unchanged source, adding a call to the kept reference is a finding.
    ("call added to a class-body generic annotation read, its rows unchanged",
     "def outer():\n    help = 0\n    class C:\n        def m[T](self, x: help):\n            return x\n",
     "def outer():\n    help = 0\n    class C:\n        def m[T](self, x: help('json')):\n            return x\n",
     (0, 4)),
    ("call added to a comprehension target read, its rows unchanged",
     "def f(rows):\n    help = 0\n    return [help for help in rows]\n",
     "def f(rows):\n    help = 0\n    return [help('json') for help in rows]\n", (0, 3)),
)


class _NoStdlibSys:
    """A sys stand-in without stdlib_module_names: the standard-library source is unavailable."""


class _EmptyStdlibSys:
    stdlib_module_names = frozenset()


class _BlankStdlibSys:
    stdlib_module_names = frozenset(("os", ""))


class _ListStdlibSys:
    stdlib_module_names = ["os"]


# A TOML file holding this comment (valid TOML, so the file parses without the injection) makes the parse raise the
# error its vector's special names (_scan_rc): a real RecursionError or MemoryError from tomllib cannot be provoked
# safely and portably, so each is injected at the tomllib.loads call _toml makes.
_PARSE_FAILURE_MARK = "# the self-test's parser fails here\n"
_PARSE_FAILURES = {"toml-recursion": RecursionError, "toml-memory": MemoryError}


def _toml_parser():
    """tomllib.loads as bound when called: _scan_rc saves it before an injection and restores it in a finally."""
    return tomllib.loads


# (name, extra rows, dropped rels, scan keyword overrides, special, a text of the refusal's message: the vector holds
# only by that refusal, not by any exit 2)
_CANNOT_VECTORS = (
    ("standard-library source unavailable", (), (), dict(sys_module=_NoStdlibSys()), None,
     "sys.stdlib_module_names is unavailable or malformed"),
    ("standard-library source empty", (), (), dict(sys_module=_EmptyStdlibSys()), None,
     "sys.stdlib_module_names is unavailable or malformed"),
    ("standard-library source holding an empty name", (), (), dict(sys_module=_BlankStdlibSys()), None,
     "sys.stdlib_module_names is unavailable or malformed"),
    ("standard-library source not a frozenset", (), (), dict(sys_module=_ListStdlibSys()), None,
     "sys.stdlib_module_names is unavailable or malformed"),
    ("interpreter is not the floor version", (), (), dict(version_info=(sys.version_info[0], 99)), None,
     "(Python 3.99), which is not the declared floor"),
    ("floor source missing", (), (FLOOR_REL,), dict(), None, "python-floor.toml: cannot stat: "),
    ("floor not MAJOR.MINOR", ((FLOOR_REL, 'python-floor = "3"\n'),), (), dict(), None,
     "python-floor is absent or not MAJOR.MINOR"),
    ("unparseable Python file", (("tools/t.py", "def (:\n"),), (), dict(), None,
     "tools/t.py: does not parse: invalid syntax"),
    ("Python file that is not UTF-8", (("tools/t.py", b"x = '\xff'\n"),), (), dict(), None,
     "tools/t.py: does not parse: Non-UTF-8 code"),
    ("Python file with a NUL byte", (("tools/t.py", b"x = 1\x00\n"),), (), dict(), None,
     "tools/t.py: does not parse: source code string cannot contain null bytes"),
    ("unreadable Python file", (("tools/t.py", "x = 1\n"),), (), dict(), "unreadable-file", "tools/t.py: unreadable: "),
    ("symlinked Python file", (), (), dict(), "symlink", "tools/t.py: not a regular file"),
    ("FIFO named like a Python file", (), (), dict(), "fifo", "tools/t.py: not a regular file"),
    ("symlinked directory", (), (), dict(), "symlinked-dir", "tools/linked: a symlinked directory is not walked"),
    ("unlistable directory", (("tools/locked/x.txt", "x\n"),), (), dict(), "unlistable", "cannot list a directory: "),
    ("import present in two search roots", (("tools/c.py", "d = 2\n"),), (), dict(), None,
     "import 'c' is ambiguous: present in tools, lib"),
    ("in-repo module named like a standard-library module",
     (("tools/json.py", "x = 1\n"), ("tools/t.py", "import json\n")), (), dict(), None,
     "import 'json' is ambiguous: present in the standard library"),
    _unread_vector("search-root insertion argument outside the read forms", "os.environ['EXTRA_ROOT']"),
    # The closed set of insertion shapes: each shape below, outside it, is refused naming the file, the row and the
    # argument, never read as the root it seems to spell (Python evaluates each differently, or not at all).
    _unread_vector("os.path.join with an absolute component", "os.path.join('lib', '/_vendor')"),
    _unread_vector("absolute component on an unresolved pathlib chain",
                   "str(Path(__file__).parents[1] / 'lib' / '/_vendor')"),
    _unread_vector("absolute component on a read pathlib chain",
                   "str(Path(__file__).resolve().parents[1] / 'lib' / '/_vendor')"),
    _unread_vector("dot component on a read pathlib chain",
                   "str(Path(__file__).resolve().parents[1] / 'lib' / '.' / '_vendor')"),
    _unread_vector("os.path.dirname of a trailing-slash literal", "os.path.dirname('lib/_vendor/child/')"),
    _unread_vector("a string divided by a string", "'lib' / '_vendor'"),
    _unread_vector("resolve called on a string", "'lib/_vendor'.resolve()"),
    _unread_vector("unwrapped unresolved pathlib value", "Path(__file__).parents[1] / 'lib' / '_vendor'"),
    _unread_vector("unwrapped pathlib value", "Path(__file__).resolve().parents[1] / 'lib' / '_vendor'"),
    _unread_vector("bare string literal", "'lib/_vendor'"),
    _unread_vector(".parents index far past the highest read",
                   "str(Path(__file__).resolve().parents[9223372036854775807] / 'lib' / '_vendor')"),
    _unread_vector(".parents index one past the highest read",
                   "str(Path(__file__).resolve().parents[17] / 'lib' / '_vendor')", rel="d16/" + _T_DEEP),
    _unread_vector("name rebound by an annotated assignment", "ROOT",
                   source=_T_NAMED % "ROOT: str = 'elsewhere'\n"),
    _unread_vector("name rebound by an augmented assignment", "ROOT", source=_T_NAMED % "ROOT += '/elsewhere'\n"),
    _unread_vector("name rebound by a chained assignment", "ROOT", source=_T_NAMED % "ROOT = OTHER = 'elsewhere'\n"),
    _unread_vector("name rebound as a for target", "ROOT",
                   source=_T_NAMED % "for ROOT in ('elsewhere',):\n    pass\n"),
    _unread_vector("name deleted", "ROOT", source=_T_NAMED % "del ROOT\n"),
    _unread_vector("name rebound by import", "ROOT", (("tools/ROOT.py", ""),), source=_T_NAMED % "import ROOT\n"),
    _unread_vector("name rebound by a from-import", "ROOT", (_T_ELSEWHERE_NAMES,),
                   source=_T_NAMED % "from elsewhere import ROOT\n"),
    _unread_vector("name rebound as a with target", "ROOT",
                   source=_T_NAMED % "with open(__file__) as ROOT:\n    pass\n"),
    _unread_vector("name rebound by a walrus in a statement", "ROOT", source=_T_NAMED % "(ROOT := 'elsewhere')\n"),
    _unread_vector("name rebound by a walrus in an if test", "ROOT",
                   source=_T_NAMED % "if (ROOT := 'elsewhere'):\n    pass\n"),
    _unread_vector("name rebound by a destructuring assignment", "ROOT",
                   source=_T_NAMED % "ROOT, OTHER = 'elsewhere', 'other'\n"),
    _unread_vector("name rebound as an except target", "ROOT",
                   source=_T_NAMED % "try:\n    pass\nexcept OSError as ROOT:\n    pass\n"),
    _unread_vector("name rebound as a match capture", "ROOT",
                   source=_T_NAMED % "match 'elsewhere':\n    case ROOT:\n        pass\n"),
    _unread_vector("name rebound as a function parameter", "ROOT",
                   source=_T_NAMED % "def move(ROOT='elsewhere'):\n    return ROOT\n"),
    _unread_vector("name rebound as a lambda parameter", "ROOT",
                   source=_T_NAMED % "move = lambda ROOT='elsewhere': ROOT\n"),
    _unread_vector("name rebound as a comprehension variable", "ROOT",
                   source=_T_NAMED % "MOVED = [ROOT for ROOT in ('elsewhere',)]\n"),
    _unread_vector("name rebound in a class body", "ROOT", source=_T_NAMED % "class Moved:\n    ROOT = 'elsewhere'\n"),
    _unread_vector("name rebound as a type parameter", "ROOT", source=_T_NAMED % "class Moved[ROOT]:\n    pass\n"),
    _unread_vector("name rebound as a parameter specification", "ROOT",
                   source=_T_NAMED % "def move[**ROOT]():\n    pass\n"),
    _unread_vector("name rebound as a type variable tuple", "ROOT", source=_T_NAMED % "def move[*ROOT]():\n    pass\n"),
    _unread_vector("name rebound by a type alias", "ROOT", source=_T_NAMED % "type ROOT = str\n"),
    _unread_vector("name rebound as a match star capture", "ROOT",
                   source=_T_NAMED % "match ['elsewhere']:\n    case [*ROOT]:\n        pass\n"),
    _unread_vector("name rebound as a match mapping rest", "ROOT",
                   source=_T_NAMED % "match {'a': 'elsewhere'}:\n    case {**ROOT}:\n        pass\n"),
    _unread_vector("name rebound by an aliased import", "ROOT", (_T_ELSEWHERE,),
                   source=_T_NAMED % "import elsewhere as ROOT\n"),
    _unread_vector("name rebound by an aliased from-import", "ROOT", (_T_ELSEWHERE,),
                   source=_T_NAMED % "from elsewhere import repo_root as ROOT\n"),
    _unread_vector("name rebound by a starred destructuring assignment", "ROOT",
                   source=_T_NAMED % "*ROOT, OTHER = 'elsewhere', 'other'\n"),
    _unread_vector("name rebound by an async def", "ROOT", source=_T_NAMED % "async def ROOT():\n    pass\n"),
    _unread_vector("name rebound by a class", "ROOT", source=_T_NAMED % "class ROOT:\n    pass\n"),
    _unread_vector("name declared global in a function", "ROOT", source=_T_NAMED % "def move():\n    global ROOT\n"),
    # One name past the bound, and two names bound to chains that start at each other.
    _unread_vector("pathlib chain through one name past the highest read", "str(LIB)",
                   source=_T_PATH + "BASE = Path(__file__).resolve().parents[1]\nROOT = BASE / 'lib'\n"
                   "LIB = ROOT / '_vendor'\nsys.path.insert(0, str(LIB))\nimport pkg\n"),
    _unread_vector("names bound to pathlib chains that start at each other", "str(LIB / '_vendor')",
                   source=_T_PATH + "LIB = ROOT / 'lib'\nROOT = LIB / 'up'\n"
                   "sys.path.insert(0, str(LIB / '_vendor'))\nimport pkg\n"),
    # The inner name of two pathlib-chain names bound once and then rebound: the gate reads neither binding.
    _unread_vector("inner pathlib-chain name rebound later at module level", "str(LIB / '_vendor')",
                   source=_T_TWO_NAMES % "ROOT = Path('elsewhere')\n"),
    _unread_vector("inner pathlib-chain name rebound in a function through global", "str(LIB / '_vendor')",
                   source=_T_TWO_NAMES % "def move():\n    global ROOT\n    ROOT = Path('elsewhere')\nmove()\n"),
    _unread_vector("inner pathlib-chain name rebound in an if branch", "str(LIB / '_vendor')",
                   source=_T_TWO_NAMES % "if os.environ.get('ELSEWHERE'):\n    ROOT = Path('elsewhere')\n"),
    # The name read directly inside the inserted str(), the outer one of two or a single one, bound once and then
    # rebound: the gate reads neither binding.
    _unread_vector("outer pathlib-chain name rebound later at module level", "str(LIB / '_vendor')",
                   source=_T_OUTER_NAME % "LIB = Path('elsewhere')\n"),
    _unread_vector("outer pathlib-chain name rebound in a function through global", "str(LIB / '_vendor')",
                   source=_T_OUTER_NAME % "def move():\n    global LIB\n    LIB = Path('elsewhere')\nmove()\n"),
    _unread_vector("outer pathlib-chain name rebound in an if branch", "str(LIB / '_vendor')",
                   source=_T_OUTER_NAME % "if os.environ.get('ELSEWHERE'):\n    LIB = Path('elsewhere')\n"),
    _unread_vector("single pathlib-chain name rebound later at module level", "str(ROOT / 'lib' / '_vendor')",
                   source=_T_ONE_NAME % "ROOT = Path('elsewhere')\n"),
    _unread_vector("single pathlib-chain name rebound in a function through global", "str(ROOT / 'lib' / '_vendor')",
                   source=_T_ONE_NAME % "def move():\n    global ROOT\n    ROOT = Path('elsewhere')\nmove()\n"),
    _unread_vector("single pathlib-chain name rebound in an if branch", "str(ROOT / 'lib' / '_vendor')",
                   source=_T_ONE_NAME % "if os.environ.get('ELSEWHERE'):\n    ROOT = Path('elsewhere')\n"),
    # The same names rebound by an import of a module that resolves: the gate reads neither binding.
    _unread_vector("outer pathlib-chain name rebound by import", "str(LIB / '_vendor')", (("tools/LIB.py", ""),),
                   source=_T_OUTER_NAME % "import LIB\n"),
    _unread_vector("outer pathlib-chain name rebound by a from-import", "str(LIB / '_vendor')", (_T_ELSEWHERE_NAMES,),
                   source=_T_OUTER_NAME % "from elsewhere import LIB\n"),
    _unread_vector("single pathlib-chain name rebound by import", "str(ROOT / 'lib' / '_vendor')",
                   (("tools/ROOT.py", ""),), source=_T_ONE_NAME % "import ROOT\n"),
    _unread_vector("single pathlib-chain name rebound by a from-import", "str(ROOT / 'lib' / '_vendor')",
                   (_T_ELSEWHERE_NAMES,), source=_T_ONE_NAME % "from elsewhere import ROOT\n"),
    # A str-valued name over a chain that starts at a name: its chain is read with no name to follow.
    _unread_vector("str-valued name over a pathlib chain that starts at a name", "LIBS",
                   source=_T_PATH + "ROOT = Path(__file__).resolve().parents[1]\n"
                   "LIBS = str(ROOT / 'lib' / '_vendor')\nsys.path.insert(0, LIBS)\nimport pkg\n"),
    _unread_vector("repo_root defined in the file", "str(repo_root() / 'lib' / '_vendor')",
                   source=_T_PATH + "def repo_root():\n    return Path('elsewhere')\n"
                   "sys.path.insert(0, str(repo_root() / 'lib' / '_vendor'))\nimport pkg\n"),
    _unread_vector("repo_root import shadowed by a def", "str(repo_root() / 'lib' / '_vendor')", (_T_GEN_COMMON,),
                   source=_T_PATH + "from _gen_common import repo_root\n"
                   "def repo_root():\n    return Path('elsewhere')\n"
                   "sys.path.insert(0, str(repo_root() / 'lib' / '_vendor'))\nimport pkg\n"),
    _unread_vector("repo_root imported from another module", "str(repo_root() / 'lib' / '_vendor')", (_T_ELSEWHERE,),
                   source=_T_PATH + "from elsewhere import repo_root\n"
                   "sys.path.insert(0, str(repo_root() / 'lib' / '_vendor'))\nimport pkg\n"),
    _unread_vector("Path imported from another module", "str(Path(__file__).resolve().parents[1] / 'lib' / '_vendor')",
                   (("tools/elsewhere.py", "from pathlib import Path\n"),),
                   source="import os, sys\nfrom elsewhere import Path\n" + _T_VENDOR_LINE + "import pkg\n"),
    _unread_vector("star import beside a read insertion",
                   "str(Path(__file__).resolve().parents[1] / 'lib' / '_vendor')", (_T_ELSEWHERE,),
                   source=_T_PATH + "from elsewhere import *\n" + _T_VENDOR_LINE + "import pkg\n"),
    _unread_vector("__file__ rebound", "str(Path(__file__).resolve().parents[1] / 'lib' / '_vendor')",
                   source=_T_PATH + "__file__ = 'elsewhere/x/y.py'\n" + _T_VENDOR_LINE + "import pkg\n"),
    ("uses inside a flow mapping", ((_WF, "steps:\n  - {uses: a/b@v1}\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("uses value in a block scalar", ((_WF, "steps:\n  - uses: >\n      a/b@v1\n"),), (), dict(), None,
     "p.yml:2: a uses key without an inline scalar value"),
    ("uses key with its value on the next line", ((_WF, "steps:\n  - uses:\n      a/b@v1\n"),), (), dict(), None,
     "p.yml:2: a uses key without an inline scalar value"),
    ("escaped key in a workflow",
     ((_WF, "jobs:\n  x:\n    steps:\n      - \"u" + _BS + "u0073es\": actions/checkout@v4\n"),), (), dict(), None,
     "p.yml:4: a line outside the accepted YAML subset"),
    ("escaped key in an action file",
     (("acts/x/action.yml", "runs:\n  steps:\n    - \"" + _BS + "x75ses\": a/b@v1\n"),), (), dict(), None,
     "action.yml:3: a line outside the accepted YAML subset"),
    ("alias as a key", ((_WF, "x-k: &k \"" + _BS + "x75ses\"\nsteps:\n  - *k : actions/checkout@v4\n"),), (),
     dict(), None, "p.yml:1: a line outside the accepted YAML subset"),
    ("complex key", ((_WF, "steps:\n  - ? uses\n    : actions/checkout@v4\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("bare CR line break after a comment", ((_WF, "steps:\n  # pin\r  - uses: actions/checkout@v4\n"),), (),
     dict(), None, "p.yml:2: character '\\r'"),
    ("U+2028 after a comment", ((_WF, "steps:\n  # c" + chr(0x2028) + "  - uses: a/b@v1\n"),), (), dict(), None,
     "p.yml:2: character '\\u2028'"),
    ("anchored value", ((_WF, "steps:\n  - uses: &a actions/checkout@v4\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("flow sequence holding a mapping", ((_WF, "jobs:\n  t:\n    with: [image: node:18]\n"),), (), dict(), None,
     "p.yml:3: a line outside the accepted YAML subset"),
    ("uses key whose value is a flow sequence", ((_WF, "steps:\n  - uses: [a/b]\n"),), (), dict(), None,
     "p.yml:2: a uses key without an inline scalar value"),
    ("escaped uses value", ((_WF, "steps:\n  - uses: \"actions/checkout" + _BS + "x40v4\"\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("multi-line plain scalar", ((_WF, "steps:\n  - name: a\n      b\n"),), (), dict(), None,
     "p.yml:3: a line indented under an inline scalar"),
    ("bare scalar line", ((_WF, "steps: []\nplain text\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("plain scalar continued by a dash line",
     ((_WF, "jobs:\n  t:\n    container: node@sha256:%s\n      - x\n" % ("ab" * 32)),), (), dict(), None,
     "p.yml:4: a line indented under an inline scalar"),
    ("uses value continued by dash lines", ((_WF, "steps:\n  - uses: a/b@%s\n        -\n       - x\n" % _PIN),), (),
     dict(), None, "p.yml:3: a line indented under an inline scalar"),
    ("sequence item continued by a deeper dash line", ((_WF, "steps:\n  - a@v1\n    - x\n"),), (), dict(), None,
     "p.yml:3: a line indented under an inline scalar"),
    ("unterminated quoted scalar", ((_WF, "steps:\n  - name: 'a\n      b'\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("document marker", ((_WF, "---\nsteps: []\n"),), (), dict(), None,
     "p.yml:1: a line outside the accepted YAML subset"),
    ("nested sequence dash", ((_WF, "steps:\n  - - uses: actions/checkout@v4\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("block scalar as a sequence item", ((_WF, "steps:\n  - |\n    uses: a/b@v1\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("plain scalar holding a uses key", ((_WF, "steps:\n  - uses:actions/checkout@v4\n"),), (), dict(), None,
     "p.yml:2: a scalar holding uses followed by a colon"),
    ("plain value holding a colon and space", ((_WF, "steps:\n  - run: echo a: b\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("plain value starting with an indicator", ((_WF, "steps:\n  - run: @x\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("plain value that is a lone dash", ((_WF, "steps:\n  - run: -\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("tab-indented line", ((_WF, "steps:\n\t- uses: actions/checkout@v4\n"),), (), dict(), None,
     "p.yml:2: a line outside the accepted YAML subset"),
    ("image key with a nested value", ((_WF, "jobs:\n  t:\n    container:\n      image: node:18\n"
                                              "    services:\n      db:\n        image:\n"),), (), dict(), None,
     "p.yml:7: a image key without an inline scalar value"),
    ("workflow that is not UTF-8", ((_WF, b"steps: \xff\n"),), (), dict(), None, "p.yml: not UTF-8: "),
    ("malformed provenance record", ((_REC, "[package\n"),), (), dict(), None,
     "provenance.toml: does not parse as TOML"),
    ("provenance record whose parse raises RecursionError", ((_REC, _GOOD_PROVENANCE + _PARSE_FAILURE_MARK),), (),
     dict(), "toml-recursion", "provenance.toml: does not parse as TOML: injected RecursionError"),
    ("provenance record whose parse raises MemoryError", ((_REC, _GOOD_PROVENANCE + _PARSE_FAILURE_MARK),), (),
     dict(), "toml-memory", "provenance.toml: does not parse as TOML: injected MemoryError"),
    ("malformed manifest row", ((_MANIFEST, "abc lib/_vendor/pkg/mod.py\n"),), (), dict(), None,
     "manifest.sha256:1: not one sha256sum row"),
    ("duplicate manifest path", (), (), dict(), "duplicate-row", "manifest.sha256:8: not one sha256sum row"),
    ("licence text that is not UTF-8", ((_LICENCE, b"MIT \xff\n"),), (), dict(), None, "pkg-MIT.txt: not UTF-8: "),
    ("no workflow to evaluate", (), (WORKFLOWS_REL + "/q.yml",), dict(), None, "holds no .yml or .yaml workflow"),
    ("two lexical scopes given one identity", (("tools/t.py", "a = lambda: 1\nb = lambda: 2\n"),), (), dict(),
     "merged-scopes", "scope '<lambda>' names 2 lexical scopes"),
    ("import-system module that does not load", (), (),
     dict(import_system=IMPORT_SYSTEM_MODULES + ("no_such_import_system_module",)), None,
     "import-system module no_such_import_system_module does not load"),
    ("inert member that is no member", (), (),
     dict(inert=INERT_IMPORT_SYSTEM_MEMBERS + (("no_such_member", "r"),)), None,
     "INERT_IMPORT_SYSTEM_MEMBERS names no_such_member"),
)


def _vector_cases(tmp):
    """Build every vector's fixture tree once. Returns [(name, expected, rule, rel, base, kwargs, special)]:
    expected is 1 (a finding of rule at rel), 0 (clean), 2 (cannot evaluate; rule is then a text of the refusal's
    message), "sites" (base is then the source) or "move" (rule is then the finding lines, rel the before source,
    base the tree holding the after, kwargs["control"] the tree holding the before)."""
    cases = []
    for index, (name, rule, at, extra, drop, kwargs, rehash) in enumerate(_RULE_VECTORS + _importer_vectors() +
                                                                          _name_vectors(*watched_names())):
        base = _fixture(tmp / ("v%d" % index), tuple(row for row in extra if row[1] is not None and
                                                     not row[0].startswith("+")), drop, rehash)
        manifest = base / _MANIFEST
        if (_MANIFEST, None) in extra:
            manifest.write_bytes(manifest.read_bytes() + ("%s  lib/_vendor/pkg/gone.py\n" % ("0" * 64)).encode())
        for listed in (row[1] for row in extra if row[0] == "+manifest"):
            digest = hashlib.sha256((base / listed).read_bytes()).hexdigest()
            manifest.write_bytes(manifest.read_bytes() + ("%s  %s\n" % (digest, listed)).encode())
        cases.append((name, 1, rule, at, base, kwargs, None))
    for index, (name, extra, kwargs) in enumerate(_GREEN_VECTORS):
        cases.append((name, 0, None, None, _fixture(tmp / ("g%d" % index), extra), kwargs, None))
    for index, (name, extra, drop, kwargs, special, refusal) in enumerate(_CANNOT_VECTORS):
        base = _fixture(tmp / ("c%d" % index), extra, drop)
        if special == "symlink":
            os.symlink(base / "tools" / "b.py", base / "tools" / "t.py")
        elif special == "fifo":
            os.mkfifo(base / "tools" / "t.py")
        elif special == "symlinked-dir":
            os.symlink(base / "lib", base / "tools" / "linked")
        elif special == "duplicate-row":
            manifest = base / _MANIFEST
            manifest.write_bytes(manifest.read_bytes() + manifest.read_bytes().split(b"\n", 1)[0] + b"\n")
        cases.append((name, 2, refusal, None, base, kwargs, special))
    for name, source, sites in _SITE_VECTORS:
        cases.append((name, "sites", sites, None, source, dict(), None))
    for index, (name, before, after, lines) in enumerate(_MOVE_VECTORS):
        control = _fixture(tmp / ("m%dc" % index), (("tools/t.py", before),))
        cases.append((name, "move", lines, before, _fixture(tmp / ("m%d" % index), (("tools/t.py", after),)),
                      dict(control=control), None))
    return cases


def _scan_rc(module, base, rules=None, special=None, **kwargs):
    """(rc, findings) of module's scan of base: rc 2, with the refusal's message as the one entry, when it cannot
    evaluate; rc 3, naming it, when the parse error a parser-failure special injects escapes the gate under test."""
    kwargs.setdefault("dispositions", _FIXTURE_DISPOSITIONS)
    kwargs.setdefault("search_roots", _FIXTURE_ROOTS)
    kwargs.setdefault("allowlist", _FIXTURE_SITES)
    parser = _toml_parser()
    failure = _PARSE_FAILURES.get(special)
    injected = None if failure is None else failure("injected %s" % failure.__name__)

    def _failing(text, **parse_kwargs):
        if _PARSE_FAILURE_MARK in text:
            raise injected
        return parser(text, **parse_kwargs)
    original = os.scandir
    original_sites = module.dynamic_sites
    original_identities = module.__dict__.get("scope_identities")
    original_read = Path.__dict__.get("read_bytes")
    inherited_read = Path.read_bytes

    def _unreadable(path):
        if os.fspath(path).endswith(os.sep + "t.py"):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return inherited_read(path)

    def _locked(path="."):
        if os.fspath(path).endswith(os.sep + "locked"):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return original(path)
    # Each replacement is made inside the try whose finally restores it, so none is left in place by an exception
    # raised between the two.
    try:
        if special == "unlistable":
            os.scandir = _locked
        if special == "unreadable-file":
            Path.read_bytes = _unreadable
        if special == "twin-sites":
            module.dynamic_sites = lambda tree, *watched: original_sites(tree, *watched) * 2
        if special == "merged-scopes":
            module.scope_identities = lambda tree: dict((key, re.sub(r"#[0-9]+/[0-9]+", "", identity))
                                                        for key, identity in original_identities(tree).items())
        if injected is not None:
            tomllib.loads = _failing
        findings, _shown, _stats = module.scan(base, rules=module.RULES if rules is None else rules, **kwargs)
    except module.CannotEvaluate as exc:
        return 2, ["cannot evaluate: %s" % exc]
    except (RecursionError, MemoryError) as exc:
        # The injected error escaping (a handler that does not catch it) is that vector's own failure; any other
        # RecursionError or MemoryError propagates.
        if exc is not injected:
            raise
        return 3, ["the injected %s escaped the gate under test: %s" % (type(exc).__name__, exc)]
    finally:
        tomllib.loads = parser
        os.scandir = original
        module.dynamic_sites = original_sites
        if original_identities is not None:
            module.scope_identities = original_identities
        elif "scope_identities" in module.__dict__:
            del module.scope_identities
        if original_read is None:
            if "read_bytes" in Path.__dict__:
                del Path.read_bytes
        else:
            Path.read_bytes = original_read
    return (1 if findings else 0), findings


# One special for each replacement _scan_rc makes: os.scandir, Path.read_bytes, the gate's dynamic_sites and
# scope_identities, and tomllib.loads.
_SCAN_PATCHES = ("unlistable", "unreadable-file", "twin-sites", "merged-scopes", "toml-recursion")


class _AtBoundary(Exception):
    """Raised into _scan_rc by _scan_patch_check's tracer, as an interrupt could be, at the first line it runs with
    a replacement in place."""


def _scan_state(module):
    """Everything _scan_rc replaces for module (a scratch copy of this gate), each object as it is now."""
    return (os.scandir, Path.__dict__.get("read_bytes"), module.__dict__.get("dynamic_sites"),
            module.__dict__.get("scope_identities"), _toml_parser())


def _replaced(module, before):
    """The indexes, in _scan_state, of what is no longer the object before holds."""
    return [index for index, (now, was) in enumerate(zip(_scan_state(module), before)) if now is not was]


def _boundary_run(module, base, special):
    """The line of _scan_rc(module, base, special=special) at which _AtBoundary was raised, the first it runs with a
    replacement in place, or None when it made none (and so ran to its end)."""
    before = _scan_state(module)
    hit = []

    def _at_line(frame, event, _arg):
        if event == "line" and not hit and _replaced(module, before):
            hit.append(frame.f_lineno)
            raise _AtBoundary("line %d" % frame.f_lineno)
        return _at_line

    def _on_call(frame, _event, _arg):
        return _at_line if frame.f_code is _scan_rc.__code__ else None
    tracing = sys.gettrace()
    sys.settrace(_on_call)
    try:
        _scan_rc(module, base, special=special)
    except _AtBoundary:
        pass
    finally:
        sys.settrace(tracing)
    return hit[0] if hit else None


def _scan_patch_check(module, base):
    """The failures of _scan_rc's restore: for each special of _SCAN_PATCHES, _AtBoundary raised at the first line
    _scan_rc runs with that special's replacement in place (the line after its assignment) must leave nothing
    replaced. A replacement made before the try whose finally restores it stays in place; the check then puts it
    back itself, so the rest of the self-test runs unpatched."""
    failures = []
    names = ("os.scandir", "Path.read_bytes", "dynamic_sites", "scope_identities", "tomllib.loads")
    for special in _SCAN_PATCHES:
        before = _scan_state(module)
        line = _boundary_run(module, base, special)
        left = _replaced(module, before)
        if left:
            os.scandir = before[0]
            if before[1] is None:
                if "read_bytes" in Path.__dict__:
                    del Path.read_bytes
            else:
                Path.read_bytes = before[1]
            module.dynamic_sites = before[2]
            if before[3] is not None:
                module.scope_identities = before[3]
            elif "scope_identities" in module.__dict__:
                del module.scope_identities
            tomllib.loads = before[4]
        if line is None:
            failures.append("scan patch %s: _scan_rc made no replacement for it" % special)
        elif left:
            failures.append("scan patch %s: an exception at line %d of _scan_rc left %s replaced"
                            % (special, line, " and ".join(names[index] for index in left)))
    return failures


# The prefix of a problem that is not the vector's own failure (an edit vector's control failing, a finding outside
# the edited file, or an exception, SystemExit and KeyboardInterrupt among them, that propagates from the mutant's
# load or run): the self-test fails it against this gate and credits no mutant with it.
_UNCREDITED = "not credited: "
# The prefix of a refusal (exit 2) where the vector expects none: it credits only a mutant that MUTANTS marks as
# caught by that refusal, its backstop (_mutant_credit).
_REFUSED = "refused (exit 2): "
# The user's interrupt: the self-test's SIGINT handler (_interrupted) records it here before it raises
# KeyboardInterrupt, so _escaped re-raises it; a KeyboardInterrupt a mutant raises itself is recorded by nothing and
# fails that mutant like any other exception.
_INTERRUPTS = []


def _interrupted(signum, _frame):
    _INTERRUPTS.append(signum)
    raise KeyboardInterrupt


def _escaped(exc, step):
    """The _UNCREDITED problem for exc, any exception (SystemExit and KeyboardInterrupt included) the gate under
    test raised at step and let propagate (one routed to a hook, from another thread or a destructor, never
    reaches this); the user's interrupt (_INTERRUPTS) is re-raised instead."""
    if isinstance(exc, KeyboardInterrupt) and _INTERRUPTS:
        raise exc
    return _UNCREDITED + "the gate under test raised %s %s: %s" % (type(exc).__name__, step, exc)


def _written_rows(module, rel, source):
    """The DYNAMIC_IMPORT_SITES rows the gate under test (this gate or a mutant of it) writes for source at rel."""
    rows = dict()
    for key, scope, _names, _called, _line in module.dynamic_sites(ast.parse(source), source,
                                                                   *module.watched_names()):
        rows.setdefault(scope, []).append(key)
    return tuple((rel, scope, tuple(keys), "r") for scope, keys in rows.items())


def _case_failure(module, case):
    """None if the case holds against module (this gate or a mutant of it), else what went wrong (_UNCREDITED
    first when that is not the vector's own failure, _REFUSED first when the gate under test refuses where the
    vector expects no refusal). An exception an edit vector's run raises (SystemExit and KeyboardInterrupt among
    them), writing the rows or scanning either source, is _UNCREDITED (_escaped); any other vector's run lets it
    propagate, and _mutant_credit credits no mutant with it. A cannot-evaluate vector holds only by its own refusal,
    the one whose message holds the text rule gives; another refusal is _REFUSED, so with no backstop named in
    MUTANTS it credits no mutant (_mutant_credit)."""
    name, expected, rule, at, base, kwargs, special = case
    if expected == "sites":
        try:
            found = module.dynamic_sites(ast.parse(base), base, *module.watched_names())
        except module.CannotEvaluate as exc:
            return _REFUSED + "expected sites %r: %s" % (rule, exc)
        got = tuple("%s | %s" % (site[1], site[0]) for site in found)
        return None if got == rule else "expected sites %r, got %r" % (rule, got)
    if expected == "move":
        step = "writing the rows for the unchanged sources"
        try:
            allowlist = _written_rows(module, "tools/a.py", _A_PY) + _written_rows(module, "tools/t.py", at)
            step = "scanning the unchanged source"
            rc, findings = _scan_rc(module, kwargs["control"], allowlist=allowlist)
            if rc != 0:
                return _UNCREDITED + "the rows written for the unchanged source fail it, rc %d %r" % (rc, findings)
            step = "scanning the edited source"
            rc, findings = _scan_rc(module, base, allowlist=allowlist)
        except module.CannotEvaluate as exc:
            return _UNCREDITED + "the gate under test cannot write rows for the unchanged sources: %s" % exc
        except BaseException as exc:
            return _escaped(exc, step)
        if rc == 2 or any(f[:2] != ("dynamic-import", "tools/t.py") for f in findings):
            return _UNCREDITED + ("the edited source gives rc %d, or a finding outside the edited file's "
                                  "dynamic-import rows: %r" % (rc, findings))
        got = tuple(sorted(f[2] for f in findings))
        return None if got == rule else "expected findings at lines %r of tools/t.py, got %r" % (rule, findings)
    kwargs = dict(kwargs)
    special = kwargs.pop("special", special)
    expect = kwargs.pop("expect", None)
    rc, findings = _scan_rc(module, base, special=special, **dict(kwargs))
    if expected == 2:
        if rc != 2:
            return "expected cannot-evaluate (2) saying %r, got %d %r" % (rule, rc, findings)
        if rule in findings[0]:
            return None
        return _REFUSED + "expected cannot-evaluate (2) saying %r, got another refusal: %s" % (rule, findings[0])
    refused = _REFUSED if rc == 2 else ""
    if expected == 0:
        return None if rc == 0 else "%sexpected a clean scan (0), got %d %r" % (refused, rc, findings)
    if rc != 1 or not any(f[0] == rule and f[1] == at for f in findings):
        return "%sexpected a %s finding at %s, got rc %d %r" % (refused, rule, at, rc, findings)
    if expect is not None and not any(f[0] == rule and f[1] == at and expect in f[3] for f in findings):
        return "expected a %s finding at %s saying %r, got %r" % (rule, at, expect, findings)
    rc_off, findings_off = _scan_rc(module, base, rules=tuple(r for r in RULES if r != rule), special=special,
                                    **dict(kwargs))
    if rc_off != 0:
        return "%swith %s removed the vector must pass (it fails only by that rule), got rc %d %r" % (
            _REFUSED if rc_off == 2 else "", rule, rc_off, findings_off)
    return None


_SELF_TEST_MARKER = "\n# " + "-" * 64 + " self-test\n"
# _path_chain's name lookup, and that lookup with a branch ahead of it that reads the name directly inside the
# inserted str() at its first plain module-level binding; the lookup itself is left byte for byte, so the anchors of
# the other mutants on it stay found exactly once.
_STR_NAME_LOOKUP = "    if isinstance(node, ast.Name) and named:\n        value = _assigned(bindings, node.id)\n" \
    "        start = None if"
_STR_NAME_FIRST_BINDING = "    if isinstance(node, ast.Name) and named == MAX_NAME_HOPS:\n" \
    '        first = next((b[1] for b in _bindings_of(bindings, node.id) or () if b[0] == "assign"), None)\n' \
    "        start = None if first is None else _path_chain(first, rel, bindings, MAX_NAME_HOPS - 1)\n" \
    "    elif isinstance(node, ast.Name) and named:\n        value = _assigned(bindings, node.id)\n" \
    "        start = None if"
# That lookup with a branch ahead of it that drops the bindings of one import kind ("import" or "from", put in at %s)
# of the name read directly inside the inserted str() before testing that the name is bound once.
_STR_NAME_DROPPED = "    if isinstance(node, ast.Name) and named == MAX_NAME_HOPS:\n" \
    '        kept = [b for b in _bindings_of(bindings, node.id) or () if b[0] != %s]\n' \
    '        value = kept[0][1] if len(kept) == 1 and kept[0][0] == "assign" else None\n' \
    "        start = None if value is None else _path_chain(value, rel, bindings, named - 1)\n" \
    "    elif isinstance(node, ast.Name) and named:\n        value = _assigned(bindings, node.id)\n" \
    "        start = None if"
# _file_bindings' walk, and that walk with the targets of one binding form first marked as plain, so it records no
# binding for them.
_BINDING_WALK = "    for node in ast.walk(tree):\n        names = ()\n"
_IMPORT_BRANCH = "                elif isinstance(node, ast.Import):\n"
_HANDLER_NAMES = "        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar,"
_DEF_NAMES = "        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):\n"
_ALIAS_BRANCH = '                if alias.name == "*" or alias.asname is not None:\n'
# _file_bindings' parameter branch, and that branch skipping the parameters a walk marks as plain (_PARAMETERS_PLAIN,
# the parameters of the node classes put in at %s).
_ARG_BRANCH = "        elif isinstance(node, ast.arg):\n"
_ARG_BRANCH_UNPLAIN = "        elif isinstance(node, ast.arg) and id(node) not in plain:\n"
_PARAMETERS_PLAIN = "    plain |= {id(a) for n in ast.walk(tree) if isinstance(n, %s) for a in ast.walk(n.args) if " \
    "isinstance(a, ast.arg)}\n"

# Each mutant removes one check (or one fail-closed path), or puts back a widening or defect this gate replaces,
# from the gate half of this file's source, the part before the
# self-test: (mutant id, the exact source text, its replacement, the vector that must then fail, and, for a mutant only
# a refusal can catch, its backstop: that refusal, named, and a text of its message). A tuple of texts and a tuple of
# replacements remove a check together with the backstop that would refuse first, so the check's own vector catches it,
# not the backstop. The self-test loads every mutant as a scratch module and requires its named vector to fail by that
# vector's own failure (_mutant_credit): an exception that propagates from its load or run (SystemExit and
# KeyboardInterrupt among them) never credits it (one routed to a hook, from another thread or a destructor, and a
# mutant that ends the process itself or hangs, are outside the harness), a refusal (exit 2) credits it only
# through the backstop it names, and an edit vector
# (_MOVE_VECTORS) credits it only when the rows the mutant writes pass the unchanged source and every finding on the
# edited source is in the edited file (_UNCREDITED otherwise). An entry may repeat another's text and replacement,
# binding that mutation to a second vector.
MUTANTS = (
    # the walk and the inputs
    ("symlinked-dir-refused", "if os.path.islink(os.path.join(dirpath, d)):", "if False:", "symlinked directory"),
    ("unlistable-dir-refused", "os.walk(root, onerror=_raise)", "os.walk(root)", "unlistable directory"),
    ("skip-root-anchored", "if not (top and d in SKIPPED_ROOT_NAMES)", "if not (d in SKIPPED_ROOT_NAMES)",
     "Python under a nested venv directory"),
    ("unreadable-refused", 'raise CannotEvaluate("%s: unreadable: %s" % (rel, exc))', 'return b""',
     "unreadable Python file"),
    ("regular-file-only", "if not stat.S_ISREG(mode):", "if False:", "symlinked Python file"),
    ("utf8-refused", 'raise CannotEvaluate("%s: not UTF-8: %s" % (rel, exc))',
     'return _regular_bytes(path, rel).decode("utf-8", "replace")',
     "workflow that is not UTF-8"),
    ("toml-refused", 'raise CannotEvaluate("%s: does not parse as TOML: %s" % (rel, exc))', "return dict()",
     "malformed provenance record"),
    ("toml-recursion-refused", "except (ValueError, RecursionError, MemoryError) as exc:",
     "except tomllib.TOMLDecodeError as exc:", "provenance record whose parse raises RecursionError"),
    ("toml-memory-refused", "except (ValueError, RecursionError, MemoryError) as exc:",
     "except tomllib.TOMLDecodeError as exc:", "provenance record whose parse raises MemoryError"),
    ("toml-recursion-caught", "except (ValueError, RecursionError, MemoryError) as exc:",
     "except (ValueError, MemoryError) as exc:", "provenance record whose parse raises RecursionError"),
    ("toml-memory-caught", "except (ValueError, RecursionError, MemoryError) as exc:",
     "except (ValueError, RecursionError) as exc:", "provenance record whose parse raises MemoryError"),
    ("python-parse-refused", 'raise CannotEvaluate("%s: does not parse: %s" % (rel, exc))', "continue",
     "unparseable Python file"),
    ("stdlib-frozenset", "if not isinstance(names, frozenset) or not names or", "if not names or",
     "standard-library source not a frozenset"),
    ("stdlib-nonempty", "if not isinstance(names, frozenset) or not names or",
     "if not isinstance(names, frozenset) or",
     "standard-library source empty"),
    ("stdlib-names-valid", " or not all(isinstance(n, str) and n for n in names):", ":",
     "standard-library source holding an empty name"),
    ("floor-shape", 'raise CannotEvaluate("%s: python-floor is absent or not MAJOR.MINOR" % FLOOR_REL)',
     "return 3, 14", "floor not MAJOR.MINOR"),
    ("floor-equal", "if running != tuple(floor):", "if False:", "interpreter is not the floor version"),
    # search roots
    ("package-dir-not-a-root", 'roots = [] if _join(own, "__init__.py") in file_set else [own]', "roots = [own]",
     "sibling import from inside a package"),
    ("vendored-file-roots", "        return [vdir]\n", "        return [vdir, _dir_of(rel)]\n",
     "vendored file importing beside itself"),
    ("regular-module-only", "return namespace and any(", "return any(", "namespace directory beside the importer"),
    ("search-roots-only", "hits = [r for r in roots if _module_present(file_set, r, top)]",
     "hits = [r for r in sorted(set(_dir_of(f) for f in file_set)) if _module_present(file_set, r, top)][:1]",
     "disconnected shadow: a same-named file in an unrelated directory"),
    ("ambiguity-refused", "if len(hits) + (top in stdlib) > 1:", "if False:", "import present in two search roots"),
    ("stdlib-collision-refused", "if len(hits) + (top in stdlib) > 1:", "if len(hits) > 1:",
     "in-repo module named like a standard-library module"),
    ("unresolved-is-finding", "                if top in stdlib:\n                    stats[\"stdlib\"] += 1\n"
     "                else:\n", "                if True:\n                    stats[\"stdlib\"] += 1\n"
     "                else:\n", "third-party import"),
    ("row-file-scanned", 'return "names no scanned Python file"', "return None",
     "search-root row naming no scanned file"),
    ("row-beyond-own", "    if not parts:\n", "    if False:\n", "search-root row naming the file's own directory"),
    ("row-sys-path-call", "    if not roots and not unread:\n", "    if False:\n",
     "search-root row with no sys.path call"),
    ("row-argument-read", "    if unread and sroot not in roots:\n", "    if False and sroot not in roots:\n",
     "search-root insertion argument outside the read forms"),
    ("row-root-resolved", "    if roots and sroot not in roots:\n", "    if False:\n",
     "search-root row whose root no insertion resolves to"),
    ("row-root-resolved-own-directory", "    if roots and sroot not in roots:\n", "    if False:\n",
     "search-root row beside an own-directory insertion and fixture literals"),
    ("row-live", "    if (rel, sroot) not in used:\n", "    if False:\n", "stale search-root row"),
    # The closed set of insertion shapes: each check removed in turn reads a shape outside it, caught by its vector.
    # The refusal naming another argument than the one it read can be caught only by a refusal: its own, whose
    # whole message the vector requires.
    ("row-argument-named", "% (rel, rel, sroot, unread[0]))", "% (rel, rel, sroot, 'not-the-argument'))",
     "search-root insertion argument outside the read forms",
     ("the insertion-argument refusal itself (row-argument-read), naming another argument",
      "insertion argument not-the-argument is outside")),
    ("insert-part-charset", 'INSERT_PART_RE = re.compile(r"[A-Za-z0-9_.-]+")', 'INSERT_PART_RE = re.compile(r".+")',
     "absolute component on a read pathlib chain"),
    ("insert-part-dots", 'node.value not in (".", "..")', "True", "dot component on a read pathlib chain"),
    ("parents-bound", "if not 1 <= up <= MAX_PARENTS_INDEX + 1:", "if not 1 <= up:",
     ".parents index one past the highest read"),
    ("name-hops-bound", "_path_chain(value, rel, bindings, named - 1)", "_path_chain(value, rel, bindings, named)",
     "pathlib chain through one name past the highest read"),
    # A pathlib-chain name read at its first plain module-level binding, every other binding of it ignored: each
    # rebinding of the inner name of two is then read as the root it first spelled.
    ("chain-name-bound-once", "        value = _assigned(bindings, node.id)\n        start = None if",
     '        value = next((b[1] for b in _bindings_of(bindings, node.id) or () if b[0] == "assign"), None)\n'
     "        start = None if", "inner pathlib-chain name rebound later at module level"),
    ("chain-name-bound-once-global", "        value = _assigned(bindings, node.id)\n        start = None if",
     '        value = next((b[1] for b in _bindings_of(bindings, node.id) or () if b[0] == "assign"), None)\n'
     "        start = None if", "inner pathlib-chain name rebound in a function through global"),
    ("chain-name-bound-once-branch", "        value = _assigned(bindings, node.id)\n        start = None if",
     '        value = next((b[1] for b in _bindings_of(bindings, node.id) or () if b[0] == "assign"), None)\n'
     "        start = None if", "inner pathlib-chain name rebound in an if branch"),
    # The name read directly inside the inserted str() (named at MAX_NAME_HOPS) read at its first plain module-level
    # binding, every other binding ignored, in a branch ahead of the lookup: each rebinding of the outer name of two,
    # or of a single name, is then read as the root it first spelled.
    ("outer-chain-name-bound-once", _STR_NAME_LOOKUP, _STR_NAME_FIRST_BINDING,
     "outer pathlib-chain name rebound later at module level"),
    ("outer-chain-name-bound-once-global", _STR_NAME_LOOKUP, _STR_NAME_FIRST_BINDING,
     "outer pathlib-chain name rebound in a function through global"),
    ("outer-chain-name-bound-once-branch", _STR_NAME_LOOKUP, _STR_NAME_FIRST_BINDING,
     "outer pathlib-chain name rebound in an if branch"),
    ("single-chain-name-bound-once", _STR_NAME_LOOKUP, _STR_NAME_FIRST_BINDING,
     "single pathlib-chain name rebound later at module level"),
    ("single-chain-name-bound-once-global", _STR_NAME_LOOKUP, _STR_NAME_FIRST_BINDING,
     "single pathlib-chain name rebound in a function through global"),
    ("single-chain-name-bound-once-branch", _STR_NAME_LOOKUP, _STR_NAME_FIRST_BINDING,
     "single pathlib-chain name rebound in an if branch"),
    # The same name with its import (or its from-import) bindings dropped before the bound-once test: its rebinding
    # by that kind of import is then not seen. The outer and the single name take the same branch, so one vector
    # each binds a kind.
    ("outer-chain-name-import-seen", _STR_NAME_LOOKUP, _STR_NAME_DROPPED % '"import"',
     "outer pathlib-chain name rebound by import"),
    ("single-chain-name-from-import-seen", _STR_NAME_LOOKUP, _STR_NAME_DROPPED % '"from"',
     "single pathlib-chain name rebound by a from-import"),
    ("str-name-chain-unnamed", "MAX_NAME_HOPS if named else 0", "MAX_NAME_HOPS",
     "str-valued name over a pathlib chain that starts at a name"),
    ("str-wrapper-required", "    return _os_path_join(node, rel, bindings)\n",
     "    return _os_path_join(node, rel, bindings) or _path_chain(node, rel, bindings, named)\n",
     "unwrapped pathlib value"),
    ("binding-sole", 'found[0][1] if found and len(found) == 1 and found[0][0] == "assign"',
     'found[0][1] if found and found[0][0] == "assign"', "name rebound by an annotated assignment"),
    ("binding-other-forms", "            if id(node) not in plain:\n", "            if False:\n",
     "name rebound by an augmented assignment"),
    ("binding-defs", "        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):\n",
     "        elif False:\n", "repo_root import shadowed by a def"),
    # One binding form dropped from _file_bindings (an import or from-import of a name the insertion shapes never
    # import, a with, walrus or destructuring target, an except target, a match capture): a name it rebinds is then
    # read at its first binding.
    ("binding-imports", _IMPORT_BRANCH, "                elif isinstance(node, ast.Import) and "
     "alias.name not in INSERT_IMPORTS:\n                    pass\n" + _IMPORT_BRANCH, "name rebound by import"),
    ("binding-from-imports", _IMPORT_BRANCH, "                elif isinstance(node, ast.ImportFrom) and "
     "alias.name not in INSERT_IMPORTS:\n                    pass\n" + _IMPORT_BRANCH, "name rebound by a from-import"),
    ("binding-with-targets", _BINDING_WALK, "    plain |= {id(item.optional_vars) for n in ast.walk(tree) if "
     "isinstance(n, (ast.With, ast.AsyncWith)) for item in n.items}\n" + _BINDING_WALK,
     "name rebound as a with target"),
    ("binding-walrus-targets", _BINDING_WALK, "    plain |= {id(n.target) for n in ast.walk(tree) if "
     "isinstance(n, ast.NamedExpr)}\n" + _BINDING_WALK, "name rebound by a walrus in a statement"),
    ("binding-destructuring-targets", _BINDING_WALK, "    plain |= {id(e) for n in ast.walk(tree) if "
     "isinstance(n, ast.Assign) for t in n.targets if isinstance(t, (ast.Tuple, ast.List)) for e in t.elts}\n" +
     _BINDING_WALK, "name rebound by a destructuring assignment"),
    ("binding-except-targets", _HANDLER_NAMES, "        elif isinstance(node, (ast.MatchAs, ast.MatchStar,",
     "name rebound as an except target"),
    ("binding-match-captures", _HANDLER_NAMES, "        elif isinstance(node, (ast.ExceptHandler, ast.MatchStar,",
     "name rebound as a match capture"),
    # Every other binding branch of _file_bindings, or one binding form of it, dropped (_binding_branch_failures
    # requires a mutant here for each branch the gate half's _file_bindings has).
    ("binding-assign", '            bindings.setdefault(node.targets[0].id, []).append(("assign", node.value))\n', "",
     "insertion of a name bound once to a read shape",
     ("the refusal of an unread insertion", "outside the forms the gate reads")),
    ("binding-function-defs", _DEF_NAMES, "        elif isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef)):\n",
     "repo_root import shadowed by a def"),
    ("binding-async-defs", _DEF_NAMES, "        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):\n",
     "name rebound by an async def"),
    ("binding-classes", _DEF_NAMES, "        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):\n",
     "name rebound by a class"),
    ("binding-globals", "        elif isinstance(node, (ast.Global, ast.Nonlocal)):\n",
     "        elif isinstance(node, ast.Nonlocal):\n", "name declared global in a function"),
    ("binding-def-parameters", (_BINDING_WALK, _ARG_BRANCH),
     (_PARAMETERS_PLAIN % "(ast.FunctionDef, ast.AsyncFunctionDef)" + _BINDING_WALK, _ARG_BRANCH_UNPLAIN),
     "name rebound as a function parameter"),
    ("binding-lambda-parameters", (_BINDING_WALK, _ARG_BRANCH),
     (_PARAMETERS_PLAIN % "ast.Lambda" + _BINDING_WALK, _ARG_BRANCH_UNPLAIN), "name rebound as a lambda parameter"),
    ("binding-comprehension-variables", _BINDING_WALK, "    plain |= {id(e) for n in ast.walk(tree) if "
     "isinstance(n, ast.comprehension) for e in ast.walk(n.target)}\n" + _BINDING_WALK,
     "name rebound as a comprehension variable"),
    ("binding-class-body-names", _BINDING_WALK, "    plain |= {id(t) for n in ast.walk(tree) if "
     "isinstance(n, ast.ClassDef) for s in n.body if isinstance(s, ast.Assign) for t in s.targets}\n" + _BINDING_WALK,
     "name rebound in a class body"),
    ("binding-type-variables", "ast.MatchStar, ast.TypeVar, ast.ParamSpec,", "ast.MatchStar, ast.ParamSpec,",
     "name rebound as a type parameter"),
    ("binding-parameter-specifications", "ast.TypeVar, ast.ParamSpec,\n", "ast.TypeVar,\n",
     "name rebound as a parameter specification"),
    ("binding-type-variable-tuples", "                               ast.TypeVarTuple)):\n",
     "                               )):\n", "name rebound as a type variable tuple"),
    ("binding-type-aliases", _BINDING_WALK, "    plain |= {id(n.name) for n in ast.walk(tree) if "
     "isinstance(n, ast.TypeAlias)}\n" + _BINDING_WALK, "name rebound by a type alias"),
    ("binding-match-stars", "ast.MatchAs, ast.MatchStar, ast.TypeVar,", "ast.MatchAs, ast.TypeVar,",
     "name rebound as a match star capture"),
    ("binding-match-mapping-rests", "            names = (node.rest,) if node.rest else ()\n",
     "            names = ()\n", "name rebound as a match mapping rest"),
    ("binding-aliased-imports", _ALIAS_BRANCH,
     '                if alias.name == "*" or alias.asname is not None and isinstance(node, ast.ImportFrom):\n',
     "name rebound by an aliased import"),
    ("binding-aliased-from-imports", _ALIAS_BRANCH,
     '                if alias.name == "*" or alias.asname is not None and isinstance(node, ast.Import):\n',
     "name rebound by an aliased from-import"),
    ("binding-starred-targets", _BINDING_WALK, "    plain |= {id(n.value) for n in ast.walk(tree) if "
     "isinstance(n, ast.Starred)}\n" + _BINDING_WALK, "name rebound by a starred destructuring assignment"),
    ("import-identity", "found[0] == INSERT_IMPORTS[name]", "found[0][-1] == INSERT_IMPORTS[name][-1]",
     "repo_root imported from another module"),
    ("star-import-unread", 'return None if "*" in bindings else bindings.get(name, [])',
     "return bindings.get(name, [])", "star import beside a read insertion"),
    ("builtins-unbound", "return _bindings_of(bindings, name) == []", "return True", "__file__ rebound"),
    # relative imports
    ("relative-module-exists", "                if not ok:\n", "                if False:\n",
     "relative from-import of a missing module"),
    ("relative-escape-repo", 'escaped = base_rel == ".." or base_rel.startswith("../") or', "escaped = False or",
     "relative import escaping the repo"),
    ("relative-escape-vendored",
     "(limit is not None and base_rel != limit and not base_rel.startswith(limit + \"/\"))",
     "False", "relative import escaping its vendored root"),
    # dynamic imports
    ("site-identifiers", "if not isinstance(item, str) or DOTTED_NAME_RE.fullmatch(item) is None:",
     "if not isinstance(item, str) or not isinstance(node, ast.Constant) or DOTTED_NAME_RE.fullmatch(item) is None:",
     "function-local alias beside a module-level __import__"),
    ("site-string-literals", "if not isinstance(item, str) or DOTTED_NAME_RE.fullmatch(item) is None:",
     "if not isinstance(item, str) or isinstance(node, ast.Constant) or DOTTED_NAME_RE.fullmatch(item) is None:",
     "getattr with a literal string name"),
    ("site-bytes-literals", 'item = item.decode("latin-1")', "pass", "getattr with a bytes literal name"),
    ("site-dotted-parts", 'for part in item.split(".") if', "for part in (item,) if",
     "string naming an importlib submodule"),
    ("site-scope-entered", "    if not isinstance(node, SCOPE_NODES):\n", "    if True:\n",
     "module-level row for a site inside a function"),
    ("site-scope-async", "SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)",
     "SCOPE_NODES = (ast.FunctionDef, ast.ClassDef, ast.Lambda)", "async def scope"),
    ("site-scope-lambda", "SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)",
     "SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)", "lambda body in a scope of its own"),
    ("site-scope-nested", 'identities[id(node)] = name if parent is tree else identities[id(parent)] + "." + name',
     "identities[id(node)] = name", "allowlist row naming a method by its bare name, no other scope of that name"),
    # Each lexical scope instance is its own scope (same-named siblings numbered in source order in the
    # scope that runs their definitions, a class marked), an identity two scopes share is refused, and a malformed
    # reference is refused as malformed; each removed in turn and caught by its vector.
    # Caught by their backstop, the shared-identity refusal (scope-identity-refused): the numbering, or its ordinal,
    # removed alone gives same-named siblings one identity, which that refusal refuses before any row is read, and
    # leaves every other tree as it was, so no vector's own check can see it (an edit vector's unchanged source with
    # same-named siblings is refused too). The -unrefused pair below removes the refusal with it, and is caught by
    # the vectors of the numbering itself.
    ("scope-instances-numbered", "            if counts[name] > 1:\n", "            if False:\n",
     "sibling lambdas numbered in source order",
     ("the shared-identity refusal (scope-identity-refused)", "so no row could tell them apart")),
    ("scope-instances-counted", "seen[name] = seen.get(name, 0) + 1", "seen[name] = 1",
     "identical references in sibling lambdas, each the first in its own",
     ("the shared-identity refusal (scope-identity-refused)", "so no row could tell them apart")),
    ("scope-instances-source-order", "sorted(held.get(id(parent), ()), key=lambda n: (n.lineno, n.col_offset))",
     "held.get(id(parent), ())", "sibling lambdas numbered in source order"),
    ("scope-instances-enclosing", "held.setdefault(id(scope), []).append(node)",
     "held.setdefault(id(tree), []).append(node)", "lambdas in two defs, each alone in its own"),
    ("scope-class-marked", 'return ("class " if isinstance(node, ast.ClassDef) else "") + node.name',
     "return node.name", "class around a method turned into a def of its name"),
    ("scope-identity-refused", "    if shared:\n        raise CannotEvaluate(",
     "    if False:\n        raise CannotEvaluate(", "two lexical scopes given one identity"),
    # The numbering removed with the refusal that would otherwise catch it first: the move or site logic catches it.
    ("scope-instances-numbered-unrefused",
     ("            if counts[name] > 1:\n", "    if shared:\n        raise CannotEvaluate("),
     ("            if False:\n", "    if False:\n        raise CannotEvaluate("),
     "reference moved into a sibling lambda"),
    ("scope-instances-counted-unrefused",
     ("seen[name] = seen.get(name, 0) + 1", "    if shared:\n        raise CannotEvaluate("),
     ("seen[name] = 1", "    if False:\n        raise CannotEvaluate("),
     "identical references in sibling lambdas, each the first in its own"),
    # A numbered identity carries the count of its same-named siblings, and counts siblings of that name only.
    ("scope-instances-sibling-count", 'name += "#%d/%d" % (seen[name], counts[name])',
     'name += "#%d" % seen[name]', "sibling lambda inserted ahead, reference moved into the one taking its ordinal"),
    ("scope-instances-count-named", "counts[_scope_name(node)] = counts.get(_scope_name(node), 0) + 1",
     "counts = dict((_scope_name(n), len(inner)) for n in inner)", "def of another name added beside a listed scope"),
    ("row-reference-form", 'REFERENCE_RE = re.compile(r"#[1-9][0-9]*: \\S.*")', 'REFERENCE_RE = re.compile(r".+")',
     "allowlist row in the positional form"),
    ("row-reference-ordinal", 'REFERENCE_RE = re.compile(r"#[1-9][0-9]*: \\S.*")',
     'REFERENCE_RE = re.compile(r"\\S+: \\S.*")', "allowlist row with a malformed reference"),
    ("site-definition-outer",
     "            elif isinstance(child, ast.AST):\n                out.append((child, scope, body, True))",
     "            elif isinstance(child, ast.AST):\n                out.append((child, inner, body, True))",
     "decorator evaluated outside its function"),
    # The type parameter list read in the definition's own identity scope, not the enclosing one: the
    # type-parameter edit vector catches it.
    ("site-definition-outer-type-parameters",
     '            elif field == "type_params":\n                out.append((child, scope, False, True))',
     '            elif field == "type_params":\n                out.append((child, inner, False, True))',
     "type-parameter bound moved into a statement beside it"),
    ("site-defaults-outer", "out.extend((d, scope, body and not generic, True)",
     "out.extend((d, inner, body and not generic, True)",
     "default argument evaluated outside its function"),
    ("site-annotations-outer", "out.append((arg.annotation, scope, False, True))",
     "out.append((arg.annotation, inner, False, True))", "definition-time expressions in the enclosing scope"),
    ("site-parameters-inner", "out.append((arg, inner, True, False))", "out.append((arg, scope, True, False))",
     "parameter and attribute in a nested class method"),
    ("site-reference-climbed", "            return node\n        node = parent\n",
     "            return node\n        return node\n", "one reference per attribute chain and call"),
    ("site-alias-statement", "        return parents[id(node)]\n", "        return node\n",
     "submodule import and from-import"),
    ("site-members-watched",
     'member = isinstance(node, ast.Constant) or (isinstance(node, ast.Attribute) and field == "attr")',
     "member = False", "watched name _gcd_import"),
    ("site-members-positions",
     'member = isinstance(node, ast.Constant) or (isinstance(node, ast.Attribute) and field == "attr")',
     "member = True", "import-system members as attributes and literals only"),
    ("watched-module-aliases", "aliases = set(k for k, v in list(sys.modules.items()) if any(v is m for m in loaded))",
     "aliases = set(modules)", "frozen importlib module imported by its frozen name"),
    # With no inert member to subtract, the inert-member refusal (watched-inert-live) cannot refuse first, so the
    # member's own vector catches the derivation removed.
    ("watched-members-derived", "                members.add(key)\n", "                pass\n",
     "watched name _gcd_import, no inert member subtracted"),
    ("watched-modules-load",
     'raise CannotEvaluate("import-system module %s does not load (%s): the watched set has no other source"\n'
     '                                 % (name, exc))', "continue", "import-system module that does not load"),
    ("watched-inert-live", "    if stale:\n        raise CannotEvaluate(\"INERT_IMPORT_SYSTEM_MEMBERS",
     "    if False:\n        raise CannotEvaluate(\"INERT_IMPORT_SYSTEM_MEMBERS", "inert member that is no member"),
    ("literal-name-resolved", "                records.append((node.lineno, name, 0, (), guarded))\n",
     "                pass\n", "__import__ of a third-party name inside an allowlisted function"),
    ("literal-callee-attribute", "    if isinstance(func, ast.Attribute):\n        return func.attr\n",
     "    if False:\n        return func.attr\n",
     "literal dynamic import of a third-party name at an allowlisted site"),
    ("site-listed-exactly", "        if (rel, function, reference) in listed:\n",
     "        if any(r == rel and f == function for r, f, _x in listed):\n",
     "importer returned from a function with a reviewed call"),
    ("site-uncovered-finding", '        findings.append(("dynamic-import", rel, line,\n                         "%s in',
     '        (("dynamic-import", rel, line,\n                         "%s in',
     "function-local alias beside a module-level __import__"),
    ("sites-checked", "    py_findings += check_dynamic_sites(sites, allowlist)\n", "",
     "__import__ through __builtins__"),
    ("row-reason", "        if not reason.strip():\n", "        if False:\n", "allowlist row with no reason"),
    ("row-references",
     "        elif not references or not all(isinstance(r, str) and REFERENCE_RE.fullmatch(r) for r in "
     "references):\n", "        elif False:\n", "allowlist row listing no reference"),
    ("row-unique",
     "        elif any((rel, function, r) in listed for r in references) or len(set(references)) != "
     "len(references):\n", "        elif False:\n", "allowlist row repeated"),
    ("row-stale", "        if not used:\n", "        if False:\n", "stale allowlist row"),
    ("row-unique-within", " or len(set(references)) != len(references):\n", ":\n",
     "allowlist row listing one reference twice"),
    ("site-key-ordinal",
     "        if (rel, function, reference) in listed:\n            listed[(rel, function, reference)] = True\n",
     "        same = [k for k in listed if k[:2] == (rel, function) and "
     "k[2].partition(\": \")[2] == reference.partition(\": \")[2]]\n"
     "        if same:\n            listed[same[0]] = True\n",
     "two identical calls on one line, one listed"),
    ("site-key-ordinal-counted", 'return "#%d: %s" % (ordinal, text)', 'return "#%d: %s" % (1, text)',
     "two identical calls on one line, each listed"),
    ("site-key-ordinal-per-scope", "        key = (scope, text)\n", "        key = text\n",
     "identical references in two scopes, each the first in its own"),
    # Not a removal but the defect this identity replaces: a line number put back into the key fails the edit vector
    # that adds unrelated lines above a listed site (the fixture's rows are written with it, so they cannot catch it).
    ("site-key-position-free", "sites.append((reference_key(ordinals[key], text),",
     "sites.append((reference_key(ordinals[key], \"%d: %s\" % (span[0], text)),",
     "unrelated lines inserted above a listed site"),
    ("site-key-column-free", "sites.append((reference_key(ordinals[key], text),",
     "sites.append((reference_key(ordinals[key], \"%d: %s\" % (span[1], text)),", "reference moved within its lambda"),
    ("site-key-unique", "        if nodes[(rel, function, reference)] > 1:\n", "        if False:\n",
     "two nodes sharing one permission key"),
    ("site-reference-subscript", "                isinstance(parent, ast.Subscript) and parent.value is node):",
     "                False):", "subscript climbed into its reference"),
    ("site-except-header", "(ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler)):",
     "(ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):", "except clause by its header"),
    ("keyword-name-exempt", "                    hits -= KEYWORD_EXEMPT_NAMES\n", "                    pass\n",
     "help as a keyword argument's name"),
    # The local-binding exemption's checks removed one at a time, each caught by its own
    # vector.
    ("local-binding-dunder-kept", '    if name.startswith("__") and name.endswith("__"):\n        return True\n',
     '    if False:\n        return True\n',
     "a dunder bound in a def stays a site"),
    ("local-binding-star-kept", "    if starred:\n        return True\n", "    if False:\n        return True\n",
     "a * import keeps a def's plain local a site"),
    ("local-binding-body-position", "    if not body:\n        return True\n",
     "    if False:\n        return True\n",
     "a generator-expression free read of an enclosing local"),
    ("local-binding-unmatched-kept", "    table = tables.get(scope)\n    if table is None:\n        return True\n",
     "    table = tables.get(scope)\n    if table is None:\n        return False\n",
     "a source symtable rejects keeps every site"),
    ("local-binding-comp-bound-kept", "    if name in comp_bound.get(scope, ()):\n        return True\n",
     "    if False:\n        return True\n",
     "a def-body read after a comprehension binds the name"),
    ("local-binding-ambiguity-kept",
     '            if len(held) == 1:\n                out[identities[id(node)]] = held[0]\n',
     '            if held:\n                out[identities[id(node)]] = held[0]\n',
     "two lambdas on one line, each scope unmatched"),
    ("local-binding-missing-name-kept", "    except KeyError:\n        return True\n",
     "    except KeyError:\n        return False\n",
     "an annotation read of a name no def binds"),
    ("local-binding-import-kept", "    if sym.is_imported():\n        return False\n",
     "    if False:\n        return False\n",
     "an import of the watched module in the scope that reads it"),
    ("local-binding-taint-kept", "    if sym.get_name() in tainted:\n        return False\n",
     "    if False:\n        return False\n",
     "an import under a nonlocal declaration taints the enclosing local"),
    # Not removals but widenings, each put back one at a time and caught by its own
    # vector. What each widening mutant covers, exactly: free-widened a free variable; local-proven every name
    # regardless of compile-time locality; comprehension-widened what a list, set or dict comprehension or a
    # generator expression holds; type-params-widened a def's or class's type-parameter list;
    # alias-type-params-widened a type alias's type-parameter list; annotation-widened a parameter annotation;
    # returns-widened a return annotation; alias-value-widened a type alias's value;
    # generic-definition-widened a generic class's bases and keywords; and generic-defaults-widened with
    # generic-defaults-widened-kwonly (one mutation, bound to the positional and to the keyword-only default
    # vector) a generic def's defaults, the stated conservative policy. The class-body position has no
    # widening mutant: a class scope never matches a symbol table (_scope_tables), so
    # local-binding-unmatched-kept keeps its sites even widened, and no vector could tell that widening apart.
    ("local-binding-free-widened", "    if sym.is_free():\n        return False\n",
     "    if sym.is_free():\n        return True\n",
     "a closure read of a def's plain local"),
    ("local-binding-local-proven", "    return sym.is_local()\n", "    return True\n",
     "an unexecuted global write keeps the module read a site"),
    ("local-binding-comprehension-widened",
     "    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):\n"
     "        return [(child, scope, False, True) for child in ast.iter_child_nodes(node)]\n",
     "    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):\n"
     "        return [(child, scope, body, True) for child in ast.iter_child_nodes(node)]\n",
     "a list comprehension's read of an enclosing def's local"),
    ("local-binding-type-params-widened",
     '            elif field == "type_params":\n                out.append((child, scope, False, True))\n',
     '            elif field == "type_params":\n                out.append((child, scope, body, True))\n',
     "a type parameter's bound read in its type-parameter scope"),
    ("local-binding-annotation-widened",
     "                        if arg.annotation is not None:\n"
     "                            out.append((arg.annotation, scope, False, True))\n",
     "                        if arg.annotation is not None:\n"
     "                            out.append((arg.annotation, scope, body, True))\n",
     "a def's parameter annotation read where the def runs"),
    ("local-binding-returns-widened",
     '            elif field == "returns" and child is not None:\n'
     "                out.append((child, scope, False, True))\n",
     '            elif field == "returns" and child is not None:\n'
     "                out.append((child, scope, body, True))\n",
     "a def's return annotation read where the def runs"),
    ("local-binding-alias-value-widened", "    out.append((node.value, scope, False, True))\n",
     "    out.append((node.value, scope, body, True))\n",
     "a type alias's value read where the type statement runs"),
    ("local-binding-generic-definition-widened",
     "                out.append((child, scope, body and not generic, True))\n",
     "                out.append((child, scope, body, True))\n",
     "a generic class's base read in its type-parameter scope"),
    ("local-binding-alias-type-params-widened",
     "        out.extend((child, scope, False, True) for child in node.type_params)\n",
     "        out.extend((child, scope, body, True) for child in node.type_params)\n",
     "a generic type alias's bound read in its type-parameter scope"),
    ("local-binding-generic-defaults-widened",
     "                out.extend((d, scope, body and not generic, True)\n",
     "                out.extend((d, scope, body, True)\n",
     "a generic def's positional default read where the def runs, kept as a site"),
    ("local-binding-generic-defaults-widened-kwonly",
     "                out.extend((d, scope, body and not generic, True)\n",
     "                out.extend((d, scope, body, True)\n",
     "a generic def's keyword-only default read where the def runs, kept as a site"),
    # Each (node type, field) pair of RESOLVED_NAME_FIELDS removed in turn: its occurrences then stay sites, so
    # its own suppression vector fails.
    ("resolved-name-read", '(ast.Name, "id"), ', "", "a walrus target"),
    ("resolved-parameter", '(ast.arg, "arg"), ', "", "a parameter of the def that reads it"),
    ("resolved-except-target", '(ast.ExceptHandler, "name"), ', "", "an except target"),
    ("resolved-match-capture", '(ast.MatchAs, "name"),\n', "\n", "a match capture"),
    ("resolved-match-star", '(ast.MatchStar, "name"), ', "", "a match star target"),
    ("resolved-match-rest", ', (ast.MatchMapping, "rest"))', ")", "a match mapping rest target"),
    ("literal-callee-getattr", '_callee_name(func.func) == "getattr" and len(func.args) in (2, 3)',
     "False and len(func.args) in (2, 3)", "getattr reaching __import__ at an allowlisted site"),
    ("literal-callee-getattr-default", "len(func.args) in (2, 3)", "len(func.args) == 2",
     "getattr with a default reaching __import__ at an allowlisted site"),
    ("literal-callee-subscript",
     "    if isinstance(func, ast.Subscript) and isinstance(func.slice, ast.Constant):\n"
     "        return func.slice.value\n",
     "    if False:\n        return func.slice.value\n", "subscript reaching __import__ at an allowlisted site"),
    ("literal-callee-helper", "        return HELPER_CLASSES[_callee_name(func.func)]\n", "        return None\n",
     "pydoc.Helper instance called at an allowlisted site"),
    ("literal-name-keyword", "        if keyword.arg in keywords and name is None:\n", "        if False:\n",
     "run_module mod_name keyword at an allowlisted site"),
    ("literal-level-keyword",
     "        if level is not None and keyword.arg == level[1]:\n            depth = keyword.value\n",
     "        if False:\n            depth = keyword.value\n", "__import__ at a non-zero level keyword"),
    ("literal-level-positional",
     "    depth = call.args[level[0]] if level is not None and len(call.args) > level[0] else None\n",
     "    depth = None\n", "__import__ at a non-zero level argument"),
    ("literal-name-absolute", '    if not all(part.isidentifier() for part in spelled.split(".")):\n',
     "    if False:\n", "literal module name with an empty component at an allowlisted site"),
    ("literal-path-unresolved", '    if kind == "path":\n        return None, None\n',
     "    if False:\n        return None, None\n", "run_path of a file path"),
    ("importer-owner", "    if row is None or row[4] is None:\n", "    if True:\n",
     "a local function named like a pydoc helper"),
    ("unreadable-starred", "            starred = any(isinstance(a, ast.Starred) for a in node.args)\n",
     "            starred = False\n", "starred module name at an allowlisted importer call"),
    ("unreadable-double-starred", "            unpacked = any(k.arg is None for k in node.keywords)\n",
     "            unpacked = False\n", "double-starred module name at an allowlisted importer call"),
    ("unreadable-getattr", "(any(isinstance(a, ast.Starred) for a in callee.args) or callee.keywords)", "False",
     "starred getattr callee at an allowlisted site"),
    ("unreadable-checked", "        for lineno, message in unreadable_calls(tree):\n",
     "        for lineno, message in ():\n", "starred module name at an allowlisted importer call"),
    # workflow classification and pins
    ("yaml-forbidden-characters", "    if bad is not None:\n", "    if False:\n",
     "bare CR line break after a comment"),
    ("scalar-continuation-refused",
     'if scalar_column is not None and len(shape.group("indent")) > scalar_column:', "if False:",
     "plain scalar continued by a dash line"),
    ("item-continuation-column", 'scalar_column = column if key is not None else len(shape.group("indent"))',
     "scalar_column = column", "sequence item continued by a deeper dash line"),
    ("block-scalar-ends", "if content is not None and indent >= content:", "if content is not None:",
     "unpinned action after a block scalar ends"),
    ("block-scalar-empty", "if content is None and indent > parent:", "if content is None:",
     "unpinned action after an empty block scalar"),
    ("quoted-key-decoded", "name = name[1:-1] if name[0] in \"'\\\"\" else name", "name = name",
     "unpinned action under a quoted key"),
    ("key-any-case", "if name is not None and name.lower() in PIN_KEYS:", "if name is not None and name in PIN_KEYS:",
     "unpinned action under a capitalised key"),
    ("unclassified-line-refused", "            name, kind = None, None\n",
     "            name, kind = None, (\"none\", None)\n",
     "bare scalar line"),
    ("item-block-scalar-refused",
     "            if kind is not None and kind[0] == \"block\":\n                kind = None\n",
     "            if False:\n                kind = None\n", "block scalar as a sequence item"),
    ("uses-keylike-refused", 'if kind[0] in ("scalar", "flow") and USES_KEYLIKE_RE.search(kind[1]):', "if False:",
     "plain scalar holding a uses key"),
    ("pin-key-scalar-only", 'if kind[0] != "scalar" and not (', 'if kind[0] == "block" and not (',
     "uses key whose value is a flow sequence"),
    ("plain-indicator-start", "if not value or value[0] in YAML_PLAIN_START or \\", "if not value or \\",
     "plain value starting with an indicator"),
    ("plain-lone-dash", "(value[0] in \"-?:\" and (len(value) == 1 or value[1] in \" \\t\")):", "False:",
     "plain value that is a lone dash"),
    ("plain-colon-space", 'if re.search(r":(?:[ \\t]|$)", value):', "if False:",
     "plain value holding a colon and space"),
    ("quoted-key-no-escape", "|\\\"[A-Za-z0-9_.\\-/ ]*\\\")\"", "|\\\"[^\\\"]*\\\")\"", "escaped key in a workflow"),
    ("quoted-value-no-escape", "YAML_DOUBLE_RE = re.compile(r\"\\\"(?P<value>[^\\\"\\\\]*)\\\"\" + YAML_TAIL)",
     "YAML_DOUBLE_RE = re.compile(r\"\\\"(?P<value>[^\\\"]*)\\\"\" + YAML_TAIL)", "escaped uses value"),
    ("flow-item-no-colon", "YAML_FLOW_ITEM = r\"(?:[A-Za-z0-9_.\\-/]+|",
     "YAML_FLOW_ITEM = r\"(?:[A-Za-z0-9_.\\-/: ]+|",
     "flow sequence holding a mapping"),
    ("action-files-scanned", "               or rel.rsplit(\"/\", 1)[-1].lower() in ACTION_FILE_NAMES]",
     "               ]",
     "unpinned action in a local action file"),
    ("workflow-suffix-any-case", "and rel.lower().endswith((\".yml\", \".yaml\")))",
     "and rel.endswith((\".yml\", \".yaml\")))",
     "unpinned docker image"),
    ("workflow-present", "if not any(rel.startswith(WORKFLOWS_REL + \"/\") for rel, _ in targets):", "if False:",
     "no workflow to evaluate"),
    ("local-uses-confined", 'pinned = local != ".." and not local.startswith("../")', "pinned = True",
     "local uses path leaving the repository"),
    ("action-commit-sha",
     "pinned = remote is not None and COMMIT_SHA_RE.fullmatch(remote.group(\"ref\")) is not None",
     "pinned = remote is not None", "unpinned action by tag"),
    ("docker-digest", "pinned = DOCKER_PINNED_RE.fullmatch(value) is not None", "pinned = True",
     "unpinned docker image"),
    ("image-digest", "pinned = IMAGE_PINNED_RE.fullmatch(value) is not None or \\", "pinned = True or \\",
     "unpinned job container"),
    ("image-local-action-only", "(action_file and not value.startswith(\"/\") and local in file_set)",
     "(not value.startswith(\"/\") and local in file_set)", "workflow image naming a walked file"),
    ("image-local-walked", "(action_file and not value.startswith(\"/\") and local in file_set)",
     "(action_file and not value.startswith(\"/\"))", "action file image naming no walked file"),
    # vendor provenance
    # An empty version, not an absent one: the record is then read on without a KeyError, so the check's own vector
    # catches it.
    ("record-fields-required", '("package.name", "package.version", "package.purl", "license.spdx",',
     '("package.name", "license.spdx",', "vendored record with an empty version"),
    ("vendored-root-named", 'if re.sub(r"[-.]", "_", package["name"]).casefold() != records[rroot].casefold():',
     "if False:", "vendored root not named for its package"),
    ("licence-under-vendor", '            if not licence.startswith(vdir + "/"):\n', "            if False:\n",
     "licence text outside its _vendor directory"),
    ("record-root-shape", 'rroot.count("/") != vdir.count("/") + 2 or ".." in rroot.split("/"):', "False:",
     "vendored root nested too deep"),
    ("purl-names-package", 'if re.fullmatch(purl_re, package["purl"]) is None:', "if False:",
     "purl naming another package"),
    ("spdx-recognised", "            if ids is None:\n", "            if False:\n", "invalid SPDX identifier"),
    ("spdx-known-ids", "    if not all(i in known for i in ids):\n", "    if False:\n", "invalid SPDX identifier"),
    ("spdx-one-operator", " or len(operators) > 1 or", " or", "SPDX expression mixing AND and OR"),
    ("spdx-operator-names", ' or not operators <= frozenset(("AND", "OR")):', ":",
     "SPDX expression with an unknown operator"),
    ("spdx-odd-words", "if len(words) % 2 == 0 or", "if", "SPDX expression ending in an operator"),
    ("licence-markers", "if ident in ids and absent:", "if False:", "empty licence text"),
    ("licence-listed", "            elif licence not in rows:\n", "            elif False:\n",
     "licence text not listed in the manifest"),
    # Caught by its backstop, the refusal to read a file the walk did not list (_regular_bytes): with the check removed
    # the gate reads the absent manifest and refuses, and a manifest path the walk does not list is absent or not a
    # regular file, so the gate refuses to read it whatever the input.
    ("manifest-present", "            if man_rel not in file_set:\n", "            if False:\n",
     "vendored manifest missing",
     ("the refusal to read a file the walk did not list", "pkg-1.0.manifest.sha256: cannot stat: ")),
    ("manifest-unique-path", 'if match is None or match.group("path") in rows:', "if match is None:",
     "duplicate manifest path"),
    # A listed file the walk does not enter, not an absent one: the gate then reads it on without refusing, so the
    # check's own vector catches it.
    ("listed-file-present", "                if path_rel not in file_set:\n", "                if False:\n",
     "listed file in a directory the walk does not enter"),
    ("listed-file-digest", ".hexdigest() != digest:", ".hexdigest() != digest and False:",
     "vendored bytes differ from the recorded digest"),
    ("root-files-listed", "if rel.startswith(rroot) and rel not in rows:", "if False:",
     "vendored file missing provenance"),
    ("vendored-python-under-root",
     'if rel.startswith(vdir + "/") and rel.endswith(".py") and not any(rel.startswith(r) for r in records):',
     "if False:", "vendored Python under no recorded root"),
    ("vendor-import-recorded", 'elif target + "/" in records:', "elif True:",
     "vendored package with no provenance record"),
    # dispositions
    ("disposition-condition-known", "if condition not in CONDITIONS:", "if False:",
     "disposition row with an unknown condition"),
    ("disposition-under-root", "elif rroot is None:", "elif False:", "disposition row outside a vendored root"),
    ("disposition-live", "elif not hits:", "elif False:", "stale disposition row"),
    ("disposition-guarded", "            if unguarded:\n", "            if False:\n",
     "guarded-optional row over an unguarded import"),
    ("disposition-handler-type", "return any(isinstance(k, ast.Name) and k.id in IMPORT_ERRORS for k in kinds)",
     "return True", "guarded-optional row whose handler is not ImportError"),
    ("try-else-unguarded", "                visit(child, guarded)\n            return\n",
     "                visit(child, body_guarded)\n            return\n", "import in a try's else is not guarded"),
    ("relative-targets-vendored", "            if rroot is None:\n                continue\n",
     "            if True:\n                continue\n", "relative import reaching an unreached module"),
    ("disposition-unreached", "                if reached:\n", "                if False:\n",
     "unreached-module row whose module is reached"),
    # The premise-change checks (literal climbs into its call or subscript; getattr with a starred
    # argument refused; help whitespace, resolve_name colon, pydoc subscript owner, .__call__, type(help) and the
    # member importers read), each removed in turn and caught by its vector.
    ("site-literal-lifted", "    if isinstance(node, ast.Constant):\n        parent = parents.get(id(node))\n",
     "    if False:\n        parent = parents.get(id(node))\n", "literal argument climbed into its call"),
    ("callee-getattr-starred", "not func.keywords and not any(isinstance(a, ast.Starred) for a in func.args) and",
     "not func.keywords and True and", "starred getattr reaching an importer refused at an allowlisted site"),
    ("callee-type-helper",
     "    if isinstance(func, ast.Call) and _callee_name(func.func) == \"type\" and len(func.args) == 1 and \\\n"
     "            not func.keywords and _callee_name(func.args[0]) in HELPER_CLASSES.values():\n        return \"Helper\"\n",
     "    if False:\n        return \"Helper\"\n", "help through type(help) at an allowlisted site"),
    ("importer-dunder-call",
     "    while isinstance(func, ast.Attribute) and func.attr == \"__call__\":\n        func = func.value\n",
     "    while False:\n        func = func.value\n", "importer reached through dunder call at an allowlisted site"),
    ("importer-owner-subscript", "        func.value if isinstance(func, ast.Subscript) else None\n",
     "        None\n", "doc through vars(pydoc) subscript at an allowlisted site"),
    ("owner-vars",
     "    elif isinstance(node, ast.Call) and _callee_name(node.func) == \"vars\" and len(node.args) == 1 and \\\n"
     "            not node.keywords:\n        node = node.args[0]\n",
     "    elif False:\n        node = node.args[0]\n", "doc through vars(pydoc) subscript at an allowlisted site"),
    ("owner-dict", "    if isinstance(node, ast.Attribute) and node.attr == \"__dict__\":\n        node = node.value\n",
     "    if False:\n        node = node.value\n", "render_doc through pydoc dunder dict subscript at an allowlisted site"),
    ("importer-owner-getattr",
     "        func.args[0] if isinstance(func, ast.Call) and _callee_name(func.func) == \"getattr\" else \\\n",
     "        None if False else \\\n", "doc through getattr(pydoc) at an allowlisted site"),
    ("literal-help-strip", "    spelled = name.value.strip() if callee == \"help\" else \\\n",
     "    spelled = name.value if callee == \"help\" else \\\n",
     "help of a whitespace-padded standard-library name"),
    ("literal-resolve-colon", "        name.value.split(\":\", 1)[0] if callee == \"resolve_name\" else name.value\n",
     "        name.value if callee == \"resolve_name\" else name.value\n",
     "resolve_name of a standard-library module-object name"),
    ("importer-find-spec-name", "    find_spec      name,fullname  -        relative  -\n",
     "    find_spec      fullname       -        relative  -\n", "find_spec name keyword at an allowlisted site"),
    ("unreadable-getattr-keyword", " or callee.keywords)", " or False)",
     "getattr callee with a keyword at an allowlisted site"),
    ("helper-class-builtin",
     "HELPER_CLASSES = dict(pair.split(\":\") for pair in \"Helper:help _Helper:help\".split())",
     "HELPER_CLASSES = dict(pair.split(\":\") for pair in \"Helper:help\".split())",
     "builtin help helper class instance at an allowlisted site"),
    # What the gate cannot read is a finding (an unreadable literal name, f-string or level; the getattr
    # refusal after the .__call__ normalisation; an f-string getattr name or subscript key), a relative or computed
    # f-string request stays unresolved rather than refused, and a literal inside an f-string climbs; each removed
    # in turn and caught by its vector.
    ("unreadable-literal-checked", "            problem = _literal_import_name(node, importer)[1]\n",
     "            problem = None\n", "help of a name with a trailing dot at an allowlisted site"),
    ("literal-fstring-name",
     "        return None, \"its module name as an f-string with no placeholder, which the gate does not evaluate\"\n",
     "        return None, None\n", "f-string module name at an allowlisted importer call"),
    ("literal-level-unreadable",
     "        return None, \"a literal module name at the level %s, which is not an integer literal\" % "
     "ast.unparse(depth)\n",
     "        return None, None\n", "__import__ level keyword written -0 at an allowlisted site"),
    ("literal-level-integer",
     "    if depth is not None and not (isinstance(depth, ast.Constant) and isinstance(depth.value, int)):\n",
     "    if depth is not None and not isinstance(depth, ast.Constant):\n",
     "__import__ level keyword written None at an allowlisted site"),
    ("literal-relative-importers-only", '    if kind == "relative" and spelled.startswith("."):\n',
     '    if spelled.startswith("."):\n', "pydoc.locate of a name with a leading dot at an allowlisted site"),
    ("importer-owner-dunder-call", "    func = _transparent(func)\n    row = IMPORTERS.get(_callee_name(func))\n",
     "    row = IMPORTERS.get(_callee_name(func))\n", "pydoc.doc through dunder call at an allowlisted site"),
    ("literal-relative-unresolved", "    if kind == \"relative\" and spelled.startswith(\".\"):\n",
     "    if False:\n", "import_module of a relative name"),
    ("fstring-placeholder-computed",
     "    return isinstance(node, ast.JoinedStr) and not any(isinstance(v, ast.FormattedValue) for v in node.values)\n",
     "    return isinstance(node, ast.JoinedStr)\n",
     "import_module of an f-string with a placeholder (a computed name)"),
    ("unreadable-getattr-normalised", "        callee = _transparent(node.func)\n", "        callee = node.func\n",
     "starred getattr through dunder call at an allowlisted site"),
    ("callee-dunder-call", "    func = _transparent(func)\n    if isinstance(func, ast.Name):\n",
     "    if isinstance(func, ast.Name):\n", "getattr through dunder call reaching __import__ at an allowlisted site"),
    ("unreadable-fstring-getattr",
     "        elif isinstance(callee, ast.Call) and _callee_name(callee.func) == \"getattr\" and "
     "len(callee.args) > 1 and \\\n",
     "        elif False and \\\n", "getattr name as an f-string at an allowlisted site"),
    ("unreadable-fstring-subscript",
     "isinstance(callee, ast.Subscript) and \\\n                _placeholder_free(callee.slice):", "False:",
     "subscript key as an f-string at an allowlisted site"),
    ("site-fstring-climb",
     "        while isinstance(parent, (ast.FormattedValue, ast.JoinedStr)):\n",
     "        while False:\n", "literal inside an f-string climbed into its call"),
    # The help listing refusal (once per spelling) and each listing word; and the .__call__ normalisation
    # at the vars owner, the type callee and the getattr owner, and the climb out of an f-string's replacement
    # field; each removed in turn and caught by its vector.
    ("help-listing-refused",
     '    if callee == "help" and spelled in HELP_LISTINGS:\n', "    if False:\n",
     "help of the modules listing beside an in-tree modules module"),
    ("help-listing-refused-padded",
     '    if callee == "help" and spelled in HELP_LISTINGS:\n', "    if False:\n",
     "help of the whitespace-padded modules listing beside an in-tree modules module"),
    ("help-listing-refused-helper",
     '    if callee == "help" and spelled in HELP_LISTINGS:\n', "    if False:\n",
     "pydoc.Helper instance listing modules beside an in-tree modules module"),
    ("help-listing-refused-getattr",
     '    if callee == "help" and spelled in HELP_LISTINGS:\n', "    if False:\n",
     "help through getattr listing modules beside an in-tree modules module"),
    ("help-listing-refused-type",
     '    if callee == "help" and spelled in HELP_LISTINGS:\n', "    if False:\n",
     "help through type(help) listing modules beside an in-tree modules module"),
    ("help-listing-keywords", 'HELP_LISTINGS = frozenset("keywords symbols topics modules".split())',
     'HELP_LISTINGS = frozenset("symbols topics modules".split())',
     "help of the keywords listing beside an in-tree keywords module"),
    ("help-listing-symbols", 'HELP_LISTINGS = frozenset("keywords symbols topics modules".split())',
     'HELP_LISTINGS = frozenset("keywords topics modules".split())',
     "help of the symbols listing beside an in-tree symbols module"),
    ("help-listing-topics", 'HELP_LISTINGS = frozenset("keywords symbols topics modules".split())',
     'HELP_LISTINGS = frozenset("keywords symbols modules".split())',
     "help of the topics listing beside an in-tree topics module"),
    ("site-fstring-field-climb", "        while isinstance(parent, (ast.FormattedValue, ast.JoinedStr)):\n",
     "        while isinstance(parent, ast.JoinedStr):\n",
     "literal inside an f-string replacement field climbed into its call"),
    ("owner-vars-dunder-call",
     '    elif isinstance(node, ast.Call) and _callee_name(node.func) == "vars" and len(node.args) == 1 and \\\n',
     '    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "vars" and '
     'len(node.args) == 1 and \\\n',
     "doc through vars dunder call subscript at an allowlisted site"),
    ("callee-type-dunder-call",
     '    if isinstance(func, ast.Call) and _callee_name(func.func) == "type" and len(func.args) == 1 and \\\n',
     '    if isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and func.func.id == "type" and '
     'len(func.args) == 1 and \\\n',
     "help through type dunder call at an allowlisted site"),
    ("importer-owner-getattr-dunder-call",
     '        func.args[0] if isinstance(func, ast.Call) and _callee_name(func.func) == "getattr" else \\\n',
     '        func.args[0] if isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and '
     'func.func.id == "getattr" else \\\n',
     "doc through getattr dunder call owner at an allowlisted site"),
)

# Runs the credit rule (_mutant_credit) must refuse, each built like a MUTANTS entry, with a backstop or None and a
# text of the reason it must give: an exception raised writing an edit vector's rows (1 // 0 in the key), scanning
# its unchanged source (a raise as check_dynamic_sites starts) or scanning its edited source (a raise at an unlisted
# site); an exception on a rule vector (an absent version read on with its check removed); a SystemExit(0) raised
# while the mutant loads, scanning an edit vector's unchanged source, or on a rule vector, and a KeyboardInterrupt the
# mutant raises itself (each would otherwise end the self-test early, exit 0 included); a refusal for a mutant that
# names no backstop, on a rule vector, on a green vector and on a rule vector's rerun with its rule removed; and a
# backstop named for a refusal that is not its own, or for a failure that is no refusal. Each fails the self-test as a
# mutant.
_UNCREDITED_RUNS = (
    ("raise-writing-rows", "sites.append((reference_key(ordinals[key], text),",
     "sites.append((reference_key(ordinals[key], \"%d: %s\" % (1 // 0, text)),", "reference moved within its lambda",
     None, "raised ZeroDivisionError writing the rows"),
    ("raise-scanning-unchanged",
     "    malformed one, repeats a reference, or lists a reference that does not occur there.\"\"\"\n",
     "    malformed one, repeats a reference, or lists a reference that does not occur there.\"\"\"\n"
     "    raise RuntimeError(\"every input\")\n", "reference moved within its lambda", None,
     "raised RuntimeError scanning the unchanged source"),
    ("raise-scanning-edited",
     "        findings.append((\"dynamic-import\", rel, line,\n                         \"%s in",
     "        raise RuntimeError(\"an unlisted site\")\n        findings.append((\"dynamic-import\", rel, line,\n"
     "                         \"%s in", "reference moved into a sibling lambda", None,
     "raised RuntimeError scanning the edited source"),
    ("raise-on-rule-vector", "(\"package.name\", \"package.version\", \"package.purl\", \"license.spdx\",",
     "(\"package.name\", \"license.spdx\",", "vendored record with no version", None, "raised KeyError"),
    ("refused-no-backstop", "                members.add(key)\n", "                pass\n", "watched name _gcd_import",
     None, "MUTANTS names no backstop"),
    ("refused-not-its-backstop", "            if counts[name] > 1:\n", "            if False:\n",
     "sibling lambdas numbered in source order", ("the unreadable-file refusal (unreadable-refused)", ": unreadable: "),
     "not the refusal of its backstop"),
    ("exit-zero-loading", "\nSCOPE_NODES = ", "\nraise SystemExit(0)\nSCOPE_NODES = ",
     "reference moved within its lambda", None, "raised SystemExit loading the mutant: 0"),
    ("exit-zero-scanning-unchanged",
     "    malformed one, repeats a reference, or lists a reference that does not occur there.\"\"\"\n",
     "    malformed one, repeats a reference, or lists a reference that does not occur there.\"\"\"\n"
     "    raise SystemExit(0)\n", "reference moved within its lambda", None,
     "raised SystemExit scanning the unchanged source: 0"),
    ("exit-zero-on-rule-vector",
     "    malformed one, repeats a reference, or lists a reference that does not occur there.\"\"\"\n",
     "    malformed one, repeats a reference, or lists a reference that does not occur there.\"\"\"\n"
     "    raise SystemExit(0)\n", "watched name _gcd_import", None, "raised SystemExit on its vector: 0"),
    ("interrupt-scanning-unchanged",
     "    malformed one, repeats a reference, or lists a reference that does not occur there.\"\"\"\n",
     "    malformed one, repeats a reference, or lists a reference that does not occur there.\"\"\"\n"
     "    raise KeyboardInterrupt\n", "reference moved within its lambda", None,
     "raised KeyboardInterrupt scanning the unchanged source"),
    ("refused-green-vector", "    stdlib = stdlib_names(sys_module)\n",
     "    raise CannotEvaluate(\"every input\")\n    stdlib = stdlib_names(sys_module)\n",
     "unrelated lines inserted above every listed site", None,
     "MUTANTS names no backstop for this mutant: refused (exit 2): expected a clean scan"),
    ("refused-rule-removed", "    stdlib = stdlib_names(sys_module)\n",
     "    if \"dynamic-import\" not in rules:\n        raise CannotEvaluate(\"a rule removed\")\n"
     "    stdlib = stdlib_names(sys_module)\n", "watched name _gcd_import", None,
     "MUTANTS names no backstop for this mutant: refused (exit 2): with dynamic-import removed"),
    ("refused-cannot-evaluate-vector", "    stdlib = stdlib_names(sys_module)\n",
     "    raise CannotEvaluate(\"every input\")\n    stdlib = stdlib_names(sys_module)\n",
     "symlinked directory", None,
     "MUTANTS names no backstop for this mutant: refused (exit 2): expected cannot-evaluate (2)"),
    ("backstop-not-a-refusal", "if os.path.islink(os.path.join(dirpath, d)):", "if False:", "symlinked directory",
     ("a backstop named for a failure that is no refusal", "expected cannot-evaluate (2)"),
     "not the refusal of its backstop"),
)


# Each branch of _file_bindings that records a binding (_binding_branches names it), and the MUTANTS that drop it
# or one binding form it records.
_BINDING_BRANCH_MUTANTS = (
    ("ast.Assign", ("binding-assign",)),
    ("ast.Name: id(node) not in plain", ("binding-other-forms", "binding-with-targets", "binding-walrus-targets",
                                         "binding-destructuring-targets", "binding-comprehension-variables",
                                         "binding-class-body-names", "binding-type-aliases",
                                         "binding-starred-targets")),
    ("ast.Import|ast.ImportFrom: alias.name == '*' or alias.asname is not None",
     ("binding-aliased-imports", "binding-aliased-from-imports")),
    ("ast.Import", ("binding-imports",)),
    ("ast.Import|ast.ImportFrom: else", ("binding-from-imports",)),
    ("ast.FunctionDef", ("binding-function-defs",)),
    ("ast.AsyncFunctionDef", ("binding-async-defs",)),
    ("ast.ClassDef", ("binding-classes",)),
    ("ast.Global", ("binding-globals",)),
    ("ast.arg", ("binding-def-parameters", "binding-lambda-parameters")),
    ("ast.ExceptHandler", ("binding-except-targets",)),
    ("ast.MatchAs", ("binding-match-captures",)),
    ("ast.MatchStar", ("binding-match-stars",)),
    ("ast.TypeVar", ("binding-type-variables",)),
    ("ast.ParamSpec", ("binding-parameter-specifications",)),
    ("ast.TypeVarTuple", ("binding-type-variable-tuples",)),
    ("ast.MatchMapping", ("binding-match-mapping-rests",)),
)
# A binding branch no vector can catch a mutant of, with why.
_EQUIVALENT_BINDING_BRANCHES = (
    ("ast.Nonlocal", "a nonlocal name compiles only with a binding in an enclosing function, and another branch "
     "records that binding, so dropping the nonlocal one never leaves the name bound once"),
)


def _binding_records(stmts):
    """Whether stmts, outside any if statement nested in them, record a binding: set names, or call setdefault."""
    stack = list(stmts)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.If):
            continue
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "names" for t in node.targets):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault":
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def _arm_classes(test):
    """The node classes an if test names in its leading isinstance(node, ...), else ()."""
    first = test.values[0] if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And) else test
    if not (isinstance(first, ast.Call) and ast.unparse(first.func) == "isinstance" and len(first.args) == 2 and
            ast.unparse(first.args[0]) == "node"):
        return ()
    kinds = first.args[1]
    return tuple(ast.unparse(kind) for kind in (kinds.elts if isinstance(kinds, ast.Tuple) else (kinds,)))


def _bindings_function(gate_source):
    """_file_bindings' definition in gate_source, parsed."""
    return next(node for node in ast.walk(ast.parse(gate_source))
                if isinstance(node, ast.FunctionDef) and node.name == "_file_bindings")


def _binding_branches(gate_source):
    """Every branch of _file_bindings that records a binding, read from the gate half's own AST: one per node class
    the branch's leading isinstance(node, ...) names, else the classes of the branch it lies in and its test (else,
    for an else branch)."""
    found = []

    def visit(stmts, outer):
        for stmt in stmts:
            if not isinstance(stmt, ast.If):
                for field in ("body", "orelse", "finalbody", "handlers"):
                    visit(getattr(stmt, field, ()), outer)
                continue
            arm = stmt
            while True:
                classes = _arm_classes(arm.test)
                if _binding_records(arm.body):
                    found.extend(classes or ("%s: %s" % (outer, ast.unparse(arm.test)),))
                visit(arm.body, "|".join(classes) or outer)
                if len(arm.orelse) == 1 and isinstance(arm.orelse[0], ast.If):
                    arm = arm.orelse[0]
                    continue
                if _binding_records(arm.orelse):
                    found.append("%s: else" % outer)
                visit(arm.orelse, outer)
                break
    visit(_bindings_function(gate_source).body, "")
    return found


def _binding_branch_failures(gate_source, credited):
    """The failures of the binding-branch rule: every branch of gate_source's _file_bindings that records a binding
    (_binding_branches) has a row in _BINDING_BRANCH_MUTANTS, each of whose mutants edits _file_bindings and was
    credited by its vector in this run (credited, the ids), or a reason in _EQUIVALENT_BINDING_BRANCHES, never
    both; a row naming no such branch fails too."""
    branches = _binding_branches(gate_source)
    rows = dict(_BINDING_BRANCH_MUTANTS)
    equivalent = dict(_EQUIVALENT_BINDING_BRANCHES)
    entries = dict((entry[0], entry) for entry in MUTANTS)
    own = ast.dump(_bindings_function(gate_source))
    failures = []
    for branch in branches:
        if branch not in rows and branch not in equivalent:
            failures.append("binding branch %s of _file_bindings has no mutant registered against a vector" % branch)
        elif branch in rows and branch in equivalent:
            failures.append("binding branch %s has both mutants and a reason no mutant can be caught" % branch)
    for branch in sorted(set(rows) | set(equivalent)):
        if branch not in branches:
            failures.append("binding branch %s is listed, but _file_bindings has no such branch" % branch)
    for branch, mutants in _BINDING_BRANCH_MUTANTS:
        for mutant in mutants:
            if mutant not in credited:
                failures.append("binding branch %s: mutant %s was not credited by its vector" % (branch, mutant))
                continue
            _id, old, new = entries[mutant][:3]
            mutated = gate_source
            for text, replacement in (tuple(zip(old, new, strict=True)) if isinstance(old, tuple) else ((old, new),)):
                mutated = mutated.replace(text, replacement)
            if ast.dump(_bindings_function(mutated)) == own:
                failures.append("binding branch %s: mutant %s leaves _file_bindings unchanged" % (branch, mutant))
    return failures


def _load_mutant(source, directory, number):
    """Write one mutant module to the scratch directory and load it with importlib."""
    path = Path(directory) / ("import_closure_mutant_%d.py" % number)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("import_closure_mutant_%d" % number, path)
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def _mutant_of(gate_source, rest, old, new, directory, number):
    """(module, None) for this gate with old replaced by new in its gate half (each text of a tuple by its
    replacement), loaded as a scratch module, or (None, why not): an anchor not found exactly once, a mutant that
    does not compile, or one whose load raises an exception that propagates (SystemExit and KeyboardInterrupt
    among them, _escaped)."""
    pairs = tuple(zip(old, new, strict=True)) if isinstance(old, tuple) else ((old, new),)
    found = [gate_source.count(text) for text, _replacement in pairs]
    if found != [1] * len(pairs):
        return None, "anchor found %s times" % "/".join(map(str, found))
    for text, replacement in pairs:
        gate_source = gate_source.replace(text, replacement)
    try:
        return _load_mutant(gate_source + rest, directory, number), None
    except SyntaxError as exc:
        return None, "does not compile: %s" % exc
    except BaseException as exc:
        return None, _escaped(exc, "loading the mutant")


def _mutant_credit(module, case, backstop=None):
    """(credited, what) for a mutant of this gate (or a scratch copy with a check removed) run against the vector it
    names. Only the vector's own failure credits it: never an exception that propagates from the run (SystemExit
    and KeyboardInterrupt among them, _escaped), nor a problem _UNCREDITED begins, and a refusal (_REFUSED) only when
    backstop, the (refusal, a text of its message) MUTANTS marks the mutant with, is that refusal; a mutant so marked
    is credited by nothing else."""
    try:
        problem = _case_failure(module, case)
    except BaseException as exc:
        return False, _escaped(exc, "on its vector")
    if problem is None:
        return False, "still holds with the check removed"
    if problem.startswith(_UNCREDITED):
        return False, problem
    if backstop is None and problem.startswith(_REFUSED):
        return False, _UNCREDITED + "a refusal, and MUTANTS names no backstop for this mutant: " + problem
    if backstop is not None and not (problem.startswith(_REFUSED) and backstop[1] in problem):
        return False, _UNCREDITED + "not the refusal of its backstop %s: %s" % (backstop[0], problem)
    return True, problem


def _case_line(case):
    if case[1] == 1:
        return "RED %s -> %s; GREEN without it" % (case[0], case[2])
    if case[1] == 2:
        return "CANNOT-EVALUATE %s -> exit 2" % case[0]
    if case[1] == 0:
        return "GREEN %s -> exit 0" % case[0]
    if case[1] == "move":
        return "MOVE %s -> findings at lines %r" % (case[0], case[2])
    return "SITES %s -> %d found" % (case[0], len(case[2]))


_INTERRUPT_HELD = "INTERRUPT a SIGINT delivered in a mutant's run is recorded and propagates"
_SETUP_HELD = "SETUP a failing mkdtemp propagates and the SIGINT handler is still restored"


def _sigint_restorable():
    """(SIGINT's handler now, None) when this process can put that handler back (signal.signal works only in the
    main thread, and getsignal gives None for a handler not installed from Python), else (None, why not)."""
    handler = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, handler)
    except (TypeError, ValueError, OSError) as exc:
        return None, "SIGINT's handler %r cannot be put back: %s" % (handler, exc)
    return handler, None


def _put_back_sigint(check, before):
    """None once SIGINT's handler is before again, else check's cannot-evaluate failure naming why it is not."""
    try:
        signal.signal(signal.SIGINT, before)
    except (TypeError, ValueError, OSError) as exc:
        return "%s cannot evaluate: SIGINT's handler %r was not put back: %s" % (check, before, exc)
    return None


def _interrupt_check(copy, case):
    """None when the user's interrupt is not contained, else the failure. A SIGINT delivered (os.kill: real
    delivery, not a direct call of _interrupted) in a mutant's run is recorded by _interrupted and propagates
    rather than failing that mutant; a KeyboardInterrupt the check did not provoke (none sent, or more interrupts
    recorded than the one it sent) propagates on, so the check cannot swallow the user's own interrupt. The check
    installs _interrupted for its own run whatever SIGINT had (ignored, or another handler) and puts the original
    back on every path; where it cannot read that handler or put it back, the check cannot evaluate, a failure and
    never a skip."""
    before, problem = _sigint_restorable()
    if problem is not None:
        return "INTERRUPT cannot evaluate: " + problem
    own_sites = copy.dynamic_sites
    sent = []

    def _deliver(*_args):
        sent.append(len(_INTERRUPTS))
        os.kill(os.getpid(), signal.SIGINT)
        raise AssertionError("the SIGINT this check delivered to itself was not handled")
    try:
        signal.signal(signal.SIGINT, _interrupted)
        copy.dynamic_sites = _deliver
        credited, problem = _mutant_credit(copy, case)
        problem = "a SIGINT delivered in a mutant's run did not propagate: credited %r, %s" % (credited, problem)
    except KeyboardInterrupt:
        if not (sent and len(_INTERRUPTS) == sent[0] + 1):
            raise
        del _INTERRUPTS[sent[0]:]
        problem = None
    finally:
        copy.dynamic_sites = own_sites
        unrestored = _put_back_sigint("INTERRUPT", before)
    if unrestored is not None:
        return unrestored
    if problem is None:
        print(_INTERRUPT_HELD)
    return problem


def _setup_check(copy):
    """None when the self-test's restore runs even when setup fails, else the failure: with mkdtemp raising, the
    copy's self-test propagates that very exception (another, raised with mkdtemp never called or in place of
    mkdtemp's, fails the check) and puts Python's default SIGINT handler back. The check gives SIGINT that default
    handler for its own run whatever it had and puts the original back on every path; where it cannot read that
    handler or put it back, the check cannot evaluate, a failure and never a skip."""
    before, problem = _sigint_restorable()
    if problem is not None:
        return "SETUP cannot evaluate: " + problem
    own_mkdtemp = tempfile.mkdtemp
    injected = OSError("no scratch directory")

    def _no_tmp(*_args, **_kwargs):
        raise injected
    caught = None
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        sys.modules[copy.__name__] = copy
        tempfile.mkdtemp = _no_tmp
        try:
            copy.self_test_main()
        except Exception as exc:
            caught = exc
    finally:
        tempfile.mkdtemp = own_mkdtemp
        sys.modules.pop(copy.__name__, None)
        left = signal.getsignal(signal.SIGINT)
        unrestored = _put_back_sigint("SETUP", before)
    if unrestored is not None:
        return unrestored
    if caught is not injected:
        return "setup: the failing mkdtemp's own OSError did not propagate from the copy's self-test: %s" % (
            "it returned" if caught is None else "it raised another, %r" % caught)
    if left is not signal.default_int_handler:
        return "setup: a failing mkdtemp left the copy's SIGINT handler installed, not restored"
    print(_SETUP_HELD)
    return None


def _interrupt_leg(copy, case):
    """The failures of the INTERRUPT check (case is the vector its mutant run uses) and the SETUP check, on copy,
    a scratch copy of this gate."""
    return [failure for failure in (_interrupt_check(copy, case), _setup_check(copy)) if failure is not None]


def _raise_other_oserror():
    """A copy's self-test that raises an OSError of its own, mkdtemp never called."""
    raise OSError("not the failing mkdtemp's")


def _replace_mkdtemp_error():
    """A copy's self-test that calls mkdtemp and raises another OSError in place of mkdtemp's."""
    try:
        tempfile.mkdtemp()
    except OSError:
        raise OSError("in place of the failing mkdtemp's") from None


# Copies' self-tests that raise an OSError other than the failing mkdtemp's: the SETUP check must fail on each.
_OTHER_OSERRORS = (_raise_other_oserror, _replace_mkdtemp_error)


def _setup_other_error(copy):
    """The failures of SETUP's own-exception rule: with copy's self-test replaced by each of _OTHER_OSERRORS, the
    SETUP check must fail, naming that other OSError, never hold."""
    failures = []
    own_main = copy.self_test_main
    try:
        for other in _OTHER_OSERRORS:
            copy.self_test_main = other
            with contextlib.redirect_stdout(io.StringIO()):
                problem = _setup_check(copy)
            if problem is None or "it raised another, OSError(" not in problem:
                failures.append("SETUP with the copy's self-test %s: expected a failure naming its OSError, got %r"
                                % (other.__name__, problem))
    finally:
        copy.self_test_main = own_main
    return failures


def _sigint_blocked(check, args, how):
    """What check (the INTERRUPT or SETUP check) returns for args, or the exception it lets escape, when SIGINT's
    handler reads as None (how "read", as for one set outside Python) or putting it back raises OSError (how
    "restore"). SIGINT's handler and signal's functions are put back afterwards."""
    previous, problem = _sigint_restorable()
    if problem is not None:
        return problem
    own_signal, own_getsignal = signal.signal, signal.getsignal

    def _marked(signum, frame):
        return _interrupted(signum, frame)

    def _blocked(signum, handler):
        if signum == signal.SIGINT and handler is _marked and own_getsignal(signum) is not _marked:
            raise OSError("putting SIGINT's handler back is blocked")
        return own_signal(signum, handler)

    def _unread(signum):
        return None if signum == signal.SIGINT else own_getsignal(signum)
    try:
        own_signal(signal.SIGINT, _marked)
        if how == "restore":
            signal.signal = _blocked
        else:
            signal.getsignal = _unread
        try:
            return check(*args)
        except Exception as exc:
            return exc
    finally:
        signal.signal, signal.getsignal = own_signal, own_getsignal
        own_signal(signal.SIGINT, previous)


# How each SIGINT check must fail where SIGINT's handler cannot be read or put back (_sigint_blocked): its
# cannot-evaluate failure, with this text of why.
_UNRESTORABLE = (("read", "cannot be put back"), ("restore", "was not put back"))


def _unrestorable_check(copy, case):
    """The failures of the SIGINT checks' cannot-evaluate rule: each of INTERRUPT and SETUP, with SIGINT's handler
    not read or not put back (_UNRESTORABLE), must return its cannot-evaluate failure for that cause, never hold,
    fail another way or let the exception escape."""
    failures = []
    for label, check, args in (("INTERRUPT", _interrupt_check, (copy, case)), ("SETUP", _setup_check, (copy,))):
        for how, why in _UNRESTORABLE:
            with contextlib.redirect_stdout(io.StringIO()):
                got = _sigint_blocked(check, args, how)
            if not (isinstance(got, str) and got.startswith(label + " cannot evaluate: ") and why in got):
                failures.append("%s with SIGINT's handler blocked at %s: expected cannot evaluate (%s), got %r"
                                % (label, how, why, got))
    return failures


def _interrupt_leg_main():
    """--interrupt-leg: the INTERRUPT and SETUP checks alone, on a scratch copy of this gate; 0 only when both ran
    and held."""
    tmp = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="import-closure-"))
        case = dict((case[0], case) for case in _vector_cases(tmp))["reference moved within its lambda"]
        os.makedirs(tmp / "mutants")
        copy = _load_mutant(Path(__file__).read_text(encoding="utf-8"), tmp / "mutants", 0)
        failures = _interrupt_leg(copy, case)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
    for failure in failures:
        print("SELF-TEST FAIL: %s" % failure, file=sys.stderr)
    return 1 if failures else 0


def _ignore_sigint():
    """The child's SIGINT disposition from its start: ignored, as for a background job of a non-interactive
    shell."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _ignored_sigint_run(path, argv):
    """None when path, launched with argv as a child whose SIGINT is ignored from its start, exits 0 having passed
    the INTERRUPT and SETUP checks (both lines printed), else the failure: a child that does not run, a nonzero
    exit, or an exit 0 with either check not run (a PASS with the check skipped)."""
    command = "%s %s" % (path, " ".join(argv))
    try:
        child = subprocess.run([sys.executable, "-I", "-B", os.fspath(path)] + list(argv), stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=600, preexec_fn=_ignore_sigint)
    except (OSError, subprocess.SubprocessError) as exc:
        return "ignored SIGINT: %s did not run: %s" % (command, exc)
    if child.returncode != 0:
        said = (child.stderr.strip() or "no output").splitlines()[-1]
        return "ignored SIGINT: %s exited %d: %s" % (command, child.returncode, said)
    lines = child.stdout.splitlines()
    missing = [held.split(" ", 1)[0] for held in (_INTERRUPT_HELD, _SETUP_HELD) if held not in lines]
    if missing:
        return "ignored SIGINT: %s exited 0 without passing %s" % (command, " and ".join(missing))
    return None


def self_test_main():
    failures = []
    this = sys.modules[__name__]
    # The user's interrupt is recorded (_interrupted) only where SIGINT has Python's default handler in the main
    # thread; anywhere else a KeyboardInterrupt in a mutant's run is contained like any other exception. The
    # INTERRUPT check installs _interrupted for its own run whatever SIGINT had (_interrupt_check).
    handler = signal.getsignal(signal.SIGINT)
    installed = handler is signal.default_int_handler
    if installed:
        try:
            signal.signal(signal.SIGINT, _interrupted)
        except ValueError:
            installed = False
    # One try/finally from here puts the handler back whether the temporary-directory setup, the run or the
    # cleanup fails (and main checks that it was put back): a failing mkdtemp or rmtree never skips the restore.
    tmp = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="import-closure-"))
        clean = _fixture(tmp / "clean")
        rc, findings = _scan_rc(this, clean)
        if rc != 0:
            failures.append("clean fixture: expected 0, got %d %r" % (rc, findings))
        else:
            _f, shown, stats = scan(clean, dispositions=_FIXTURE_DISPOSITIONS, search_roots=_FIXTURE_ROOTS,
                                    allowlist=_FIXTURE_SITES)
            got = (len(shown), stats["dynamic"], stats["vendored"], stats["repo"], stats["uses"], stats["images"])
            if got != (2, 3, 4, 2, 2, 1):
                failures.append("clean fixture: dispositions or residual not reported: %r %r" % (shown, stats))
        print("GREEN clean fixture")
        cases = _vector_cases(tmp)
        for case in cases:
            problem = _case_failure(this, case)
            if problem is not None:
                failures.append("%s: %s" % (case[0], problem))
            print(_case_line(case))
        by_name = dict((case[0], case) for case in cases)
        gate_source, marker, rest = Path(__file__).read_text(encoding="utf-8").partition(_SELF_TEST_MARKER)
        mutant_dir = tmp / "mutants"
        os.makedirs(mutant_dir)
        backstops = 0
        credited_ids = set()
        for number, (mutant, old, new, vector, *backstop) in enumerate(MUTANTS, 1):
            backstop = backstop[0] if backstop else None
            if vector not in by_name:
                failures.append("mutant %s: vector %r unknown" % (mutant, vector))
                continue
            module, problem = _mutant_of(gate_source, marker + rest, old, new, mutant_dir, number)
            if module is None:
                failures.append("mutant %s: %s" % (mutant, problem))
                continue
            credited, problem = _mutant_credit(module, by_name[vector], backstop)
            if not credited:
                failures.append("mutant %s: vector %r %s" % (mutant, vector, problem))
            else:
                backstops += backstop is not None
                credited_ids.add(mutant)
                print("MUTANT %s caught by %s%s (%s)" % (mutant, vector, "" if backstop is None else
                                                         " through its backstop " + backstop[0], problem.split(",")[0]))
        # Each branch of _file_bindings that records a binding, read from the gate half itself, has a mutant
        # credited by its vector, or a reason none can be caught.
        unclosed = _binding_branch_failures(gate_source, credited_ids)
        failures.extend(unclosed)
        if not unclosed:
            print("BRANCHES each of _file_bindings' %d binding branches has a mutant caught by its vector (%d with a "
                  "reason none can be)" % (len(_binding_branches(gate_source)), len(_EQUIVALENT_BINDING_BRANCHES)))
        # Each run the credit rule must refuse is credited by none, for the reason it names.
        for number, (label, old, new, vector, backstop, reason) in enumerate(_UNCREDITED_RUNS, len(MUTANTS) + 1):
            module, problem = _mutant_of(gate_source, marker + rest, old, new, mutant_dir, number)
            if vector in by_name:
                credited = False
                if module is not None:
                    credited, problem = _mutant_credit(module, by_name[vector], backstop)
                if not credited and reason in problem:
                    print("UNCREDITED %s on %s (%s)" % (label, vector, problem.split(",")[0]))
                    continue
            failures.append("uncredited run %s: expected no credit saying %r on %r, got %s" % (label, reason, vector,
                                                                                              problem))
        # Each watched name and member removed in turn from a scratch copy: its own vector must then fail.
        copy = _load_mutant(gate_source + marker + rest, mutant_dir, 0)
        # Each cannot-evaluate vector holds only by its own refusal: with every scan refused for another reason,
        # it fails, and that refusal, with no backstop named, credits no mutant. The REFUSAL line prints only when
        # every vector held.
        own_scan = copy.scan

        def _refuse(*_args, **_kwargs):
            raise copy.CannotEvaluate("a refusal no vector names")
        copy.scan = _refuse
        refused_wrongly = 0
        for case in cases:
            if case[1] != 2:
                continue
            credited, problem = _mutant_credit(copy, case)
            if problem == "still holds with the check removed":
                refused_wrongly += 1
                failures.append("cannot-evaluate vector %s: holds on a refusal not its own" % case[0])
            elif credited or "MUTANTS names no backstop" not in problem:
                refused_wrongly += 1
                failures.append("cannot-evaluate vector %s: a refusal not its own was credited or misread: %s"
                                % (case[0], problem))
        copy.scan = own_scan
        if not refused_wrongly:
            print("REFUSAL each cannot-evaluate vector fails on a refusal not its own, crediting nothing")
        names, members = watched_names()
        for name in sorted(names | members):
            copy.watched_names = lambda *_args, _name=name: (names - {_name}, members - {_name})
            credited, problem = _mutant_credit(copy, by_name["watched name " + name])
            if not credited:
                failures.append("watched name %s: its vector, with the name removed, %s" % (name, problem))
            else:
                print("REMOVED %s caught by its vector" % name)
        copy.watched_names = watched_names
        # Each importer row removed in turn from the scratch copy: its own vector must then fail.
        for vector in _importer_vectors():
            callee = vector[0].split(" ", 1)[1]
            copy.IMPORTERS = dict((k, v) for k, v in IMPORTERS.items() if k != callee)
            credited, problem = _mutant_credit(copy, by_name[vector[0]])
            if not credited:
                failures.append("importer %s: its vector, with the row removed, %s" % (callee, problem))
            else:
                print("REMOVED importer %s caught by its vector" % callee)
        copy.IMPORTERS = IMPORTERS
        # Each replacement _scan_rc makes is put back by an exception raised at the line after it.
        unpatched = _scan_patch_check(copy, clean)
        failures.extend(unpatched)
        if not unpatched:
            print("SCAN-RESTORE each of _scan_rc's %d replacements is put back after an exception at the line after "
                  "it" % len(_SCAN_PATCHES))
        # The INTERRUPT and SETUP checks run under whatever handler SIGINT had at the start where this process can
        # put it back (_interrupt_leg), so neither is skipped where SIGINT was ignored; a child launched with SIGINT
        # ignored must run both and pass them.
        failures.extend(_interrupt_leg(copy, by_name["reference moved within its lambda"]))
        problem = _ignored_sigint_run(__file__, ["--interrupt-leg"])
        if problem is not None:
            failures.append(problem)
        else:
            print("IGNORED with SIGINT ignored from its start, a child still runs and passes INTERRUPT and SETUP")
        # SETUP holds only by the failing mkdtemp's own OSError, and each check cannot evaluate, a failure, where
        # SIGINT's handler cannot be read or put back.
        other_errors = _setup_other_error(copy)
        failures.extend(other_errors)
        if not other_errors:
            print("OTHER-OSERROR SETUP fails when the copy's self-test raises another OSError, with mkdtemp never "
                  "called or in place of its error")
        unrestorable = _unrestorable_check(copy, by_name["reference moved within its lambda"])
        failures.extend(unrestorable)
        if not unrestorable:
            print("UNRESTORABLE INTERRUPT and SETUP each cannot evaluate when SIGINT's handler cannot be read or put "
                  "back")

        # A SystemExit that escapes the self-test fails it (main), whatever its code, never ends it with that code.
        def _exit_zero():
            raise SystemExit(0)
        copy.self_test_main = _exit_zero
        with contextlib.redirect_stderr(io.StringIO()) as said:
            try:
                rc = copy.main(["--self-test"])
            except SystemExit as exc:
                rc = "SystemExit(%r)" % (exc.code,)
        if rc != 1 or "exited before its verdict" not in said.getvalue():
            failures.append("a SystemExit(0) escaping the self-test gave %r %r, not 1" % (rc, said.getvalue()))
        else:
            print("EXIT a SystemExit(0) escaping the self-test exits 1")
    finally:
        try:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)
        finally:
            if installed:
                signal.signal(signal.SIGINT, handler)
    if run(ROOT) != 0:
        failures.append("live: the real tree does not pass")
    if failures:
        for failure in failures:
            print("SELF-TEST FAIL: %s" % failure, file=sys.stderr)
        return 1
    names, members = watched_names()
    importers = len(_importer_vectors())
    print("SELF-TEST PASS: %d rule vectors red with their rule and green without it (%d of them one per watched "
          "name or import-system member, %d one per importer), %d green vectors exit 0, %d cannot-evaluate vectors "
          "exit 2 by their own refusal, %d dynamic-import site vectors hold, %d edit vectors (rows written for one "
          "source pass it unchanged, findings exact and in the edited file only on the edited one) hold, %d mutants "
          "(one check removed each, with its backstop where named, or a widening put back, or a line or a column put "
          "into the key) caught "
          "by their vectors, never through an exception that propagates from the load or the run, SystemExit and "
          "KeyboardInterrupt included (%d of them, as "
          "MUTANTS marks, only through their backstop's refusal), %d runs that raise, exit or refuse credited by "
          "none, %d watched names, %d members and %d importers each removed and caught, %d scan replacements put "
          "back after an exception at the line after each, INTERRUPT and SETUP held here and in a child with SIGINT "
          "ignored, SETUP failed on %d other OSErrors, each of the two cannot evaluate in the %d cases of SIGINT's "
          "handler not read or not put back, the live tree passes"
          % (len(_RULE_VECTORS) + len(names | members) + importers, len(names | members), importers,
             len(_GREEN_VECTORS), len(_CANNOT_VECTORS), len(_SITE_VECTORS), len(_MOVE_VECTORS), len(MUTANTS),
             backstops, len(_UNCREDITED_RUNS), len(names), len(members), importers, len(_SCAN_PATCHES),
             len(_OTHER_OSERRORS), len(_UNRESTORABLE)))
    return 0


def main(argv):
    if argv in (["--self-test"], ["--interrupt-leg"]):
        # The self-test puts the SIGINT handler back on every path (its restore is in a finally no setup or
        # cleanup failure skips); this checks that it did, so removing the restore turns the self-test red.
        before = signal.getsignal(signal.SIGINT)
        try:
            code = self_test_main() if argv == ["--self-test"] else _interrupt_leg_main()
        except SystemExit as exc:
            print("SELF-TEST FAIL: SystemExit(%r) exited before its verdict" % (exc.code,), file=sys.stderr)
            code = 1
        if signal.getsignal(signal.SIGINT) is not before:
            signal.signal(signal.SIGINT, before)
            print("SELF-TEST FAIL: the SIGINT handler was not restored after the self-test", file=sys.stderr)
            return 1
        return code
    if argv:
        print("usage: check_import_closure.py [--self-test | --interrupt-leg]", file=sys.stderr)
        return 2
    return run(ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
