#!/usr/bin/env python3
"""Brief capability lint (brief-capability): refuse a brief that carries a structural execution request to
a worker family that cannot execute.

A brief is the text sent to one worker family. When the family cannot run a command (its row in
.aiqt/core/worker-capabilities.toml says `execute = "no"`), a request to run a command, a tool, or git
can only come back as an asserted value that nobody measured, or as UNVERIFIABLE. This lint reads the
brief before it is sent. It REFUSES (exit 1) only on a structural signal: for such a family, an executable
fence, or a command line in the text the family is addressed by (S1 to S3), unless a closed shape demotes it;
and for every family, a path outside a declared workdir when the family's row says it cannot read outside one
(P2), or a pinned revision that a git command line of the listed subcommands (P5: clone, checkout, switch,
reset, fetch, pull, worktree add, merge, rebase, restore --source, archive) reaches through a ref that can move with
no later cure, or through a dispatch ref that resolves to another commit (P5), or an input the brief declares on an
INPUT: line that does not exist on the host the lint runs on (P6). The
signal is structural, not certain: the lint fails closed, so some text a human would pass refuses (see
RESIDUALS). Every rule that infers a request from free prose (R1, R5, R6, R7) is ADVISORY:
its hits are printed so a human sees them, and it never refuses. For a family that can execute (`yes` or
`read-only-sandbox`) no execution rule applies; the PREFLIGHT rules below apply to every family.

PREFLIGHT (the input paths a brief names; read over the whole brief, line by line, before scope is applied):

  P1 relative input    ADVISORY. The brief names a workdir (the word workdir, work dir or working directory)
                       and an input by a relative path: a token with a slash whose last component has a file
                       suffix (`tools/t.py`, `.aiqt/core/x.toml`), or that opens with `./` or `../`. A relative
                       path resolves against the worker's own scratch (its working directory), not the workdir.
  P2 outside workdir   HARD. The family's row says reads-outside-workdir = "no" and the brief declares a
                       workdir. P2 does not look for a path by pattern: it splits each line into words on
                       whitespace only (a span in " or ` quotes that closes on the line is one word together
                       with anything glued to either end of it) and judges every U+002F slash in a word that
                       is at the word's start or right after a character that is not a letter, a digit, `.`
                       or `_` (so @/x, %/x, -/x, =/x, Path:/x, (/x, "/x and the second slash of // are all
                       judged). For each judged slash it takes the text from that slash to the END of the
                       word, strips only trailing sentence punctuation (. , ; : ) ! ? " ' `), folds it
                       lexically (posixpath.normpath, and a leading // read as /; no link followed) and
                       refuses unless it equals the declared workdir or lies under it (/w/repo/x is under
                       /w/repo, /w/repository is not); when a form along that strip ends in /.. it is
                       judged too, so "/w/repo/..." refuses. Text glued after a quoted span is part of the
                       word, so "/w/repo/a b"/../../../etc/x (/etc/x read as a shell word) refuses: each
                       judged slash in it gives a path that folds to /etc/x. This FAILS CLOSED and OVER-REFUSES odd
                       words a human would pass: /w/repo//a.md (its // judges /a.md), /w/repo/x~/a.md,
                       "/w/repo/a b"/x.md (the slash after the quote judges /x.md), a // or :// in any
                       non-web scheme (ftp://h/x, file://localhost/w/repo/x) and a quoted span with an
                       outside path inside it; the brief author rewrites such a word. A slash right after a
                       letter, a digit, `.` or `_` continues a relative word and is not judged, so a relative
                       path (no leading /: docs/a.md, ../x.md) is not compared with the workdir. A word that
                       opens, after opening markup (" ' ` * _ ( [ { <), with http:// or https:// (any case)
                       is a web address and is not judged at all, including anything glued to it; file:// and
                       every other scheme go through the slash rule (file:///etc/x judges /etc/x). Three more
                       word shapes refuse because they cannot be placed: a word with no judged slash that is a
                       home (~user/) or variable ($X/) path or a relative path whose `..` climbs above its own
                       start (posixpath.normpath opens with `..`, as the tail of an unquoted path with a space,
                       b/../../etc/x, does), and a word with more than MAX_WORD_PATHS judged slashes. Only
                       U+002F is a slash: a look-alike (U+2215 DIVISION SLASH, U+FF0F FULLWIDTH SOLIDUS, U+2044
                       FRACTION SLASH and the like) is an ordinary character, so etc/shadow spelt with U+2215
                       for each slash is not judged; the OS would not open it as /etc/shadow, but a worker
                       that rewrites it into ASCII would. A PROHIBITION (never, do not, don't, must not, not to, as a whole word, in any
                       case) silences every word that starts after the end of the first prohibition on its
                       line, to the end of the line and across sentence stops, whatever the line goes on to
                       ask; words before it are judged. Negation is LEXICAL, not semantic, so "Never write to
                       the workdir. Read /etc/x." and "Do not stop until you read /etc/x" do not fire. A
                       declaration is a line that opens (once markup is skipped) with Workdir, The workdir or Your
                       workdir (or work dir, working directory), then optionally a colon or the word is (before a
                       space or the line end), then one absolute path, quoted with " or ` when it holds a space
                       (Workdir /w/repo (read-only). The workdir is /w/repo. workdir:/w/repo); a line that is
                       only such a lead (Workdir, Workdir:, The workdir is) takes its path from the next line. A
                       path after the word workdir anywhere else (Do not read outside the workdir, such as
                       /etc/x) declares nothing. A line whose first word, once markup is skipped, is a workdir
                       label (Workdir, Work dir, Working directory or Working tree, optionally after The or
                       Your, in any case, plain or in bold, followed by anything) and that is not such a
                       declaration (**Workdir**: /w/repo, Workdir = /w/repo, Working tree: /w/repo, The
                       workdir is read-only) is never ignored. It, two declarations of different workdirs, a
                       declaration whose path is followed by a word (an unquoted space), a bare lead with no
                       path on the next line, or a declared workdir that normalises to / (/, /., //) or holds a
                       `..` component (/w/repo/..) is CANNOT_EVALUATE (exit 2) for a family this rule applies
                       to. "undeclared" and "yes" never fire, and with no declared workdir the rule does not
                       fire.
  P3 missing at pin    ADVISORY. A line that opens (once markup is skipped) with REVIEWED HEAD or BRIEF-PIN
                       (upper case, as written here) is a pin line; the label may be bold or italic, with the
                       closing markup before or after its colon (**REVIEWED HEAD:** or **REVIEWED HEAD**:).
                       Its value must be a commit of 7 to 40 or of
                       64 hex digits not followed by a letter, digit, `_` or `-`; a pin line with any other
                       value (6, 41, 63 or 65 hex digits, a non-hex character, no value) is CANNOT_EVALUATE
                       (exit 2) for every family, never ignored. Pin lines must agree: every BRIEF-PIN line
                       holds the first one's value, character for character, and, where the brief has a
                       BRIEF-PIN line, every REVIEWED HEAD line holds that value, a prefix of it or a value it
                       is a prefix of, character for character (so a full 40-hex REVIEWED HEAD is the BRIEF-PIN
                       value itself, and the same commit in upper case disagrees); pin lines that disagree,
                       wherever they stand (in a fence, indented, after a cure), are CANNOT_EVALUATE (exit 2)
                       for every family, and the message names both lines. A lower-case label (Reviewed head:)
                       or a label later in a line is not a pin line. A LOOK-ALIKE pin line is neither a pin
                       line nor a pin label with a bad value, but opens with REVIEWED HEAD or BRIEF-PIN once its
                       text is folded (NFKC, so a fullwidth letter is its ASCII form; every Unicode hyphen or
                       dash of category Pd read as -; case ignored) and markup, backticks and table pipes are
                       skipped around the label, followed by a commit of 7 to 40 or 64 hex digits (Brief-Pin: x,
                       BRIEF-PIN with U+2010 for its hyphen, `BRIEF-PIN: x`, | BRIEF-PIN | x |); where the brief
                       has a BRIEF-PIN line, a look-alike whose commit does not agree with it the way a REVIEWED
                       HEAD line must is CANNOT_EVALUATE (exit 2) too, and the message names both lines. A
                       look-alike is not a pin and is compared with nothing else. A valid pin pins a revision, and a
                       relative path the brief
                       names (`./` and `..` folded with posixpath.normpath; a path that then climbs out of the
                       repository is not repository-relative and is skipped) does not exist at it in the
                       repository the lint is pointed at (--repo, default this repository), read with
                       `git cat-file -e <commit>:<path>` from the object store, never the working tree. The
                       path may be a file the brief asks the worker to create, so P3 prints an ADVISORY line
                       and never refuses. A pin that does not resolve to one commit (unknown, ambiguous), a git
                       that cannot be launched or times out, or a pin with no repository to read is
                       CANNOT_EVALUATE (exit 2), never a pass, whether or not the brief names a relative path.
                       The git reads are hermetic against system, global, XDG and environment-injected git
                       configuration: every inherited GIT_ variable is dropped, the system and global config
                       are pinned to an empty file, and HOME and XDG_CONFIG_HOME name an empty directory, so
                       such configuration (malformed or adversarial) cannot change the answer. They are NOT
                       hermetic against the repository's own config or a file an absolute include.path in it
                       names: a malformed one fails the read, CANNOT_EVALUATE (exit 2). No system or global
                       safe.directory entry is honoured and none is added (a repository another user owns can
                       carry config that runs code), so a repository owned by another user reads
                       CANNOT_EVALUATE, and the message names the cause (dubious ownership) and the remedy:
                       run the check as the repository owner, or point --repo at a checkout you own.
  P4 no hand-back      ADVISORY. The family's row says handback = "yes", a sentence holds a delivery verb (return,
                       paste, print, send, show, include, quote, ...) not negated and a large output (a whole,
                       entire, full or complete file, source, diff, log or output; a diff; a patch), and no line
                       names a hand-back file (the words hand-back file or path and an absolute path).
  P5 moving reach      HARD on a git command line, ADVISORY in prose. The brief pins a revision: a pin line (P3),
                       or a confirm clause, a word of 7 to 40 or 64 hex digits holding a digit on a line that also
                       holds upper-case HEAD as a word and confirm, verify, check, assert, ensure, starts, matches
                       or equals (the commit id of a reach on that line included). The PINS a reach is matched
                       against are the pin lines' where the brief has one, else the confirm clauses'. P5 reads the
                       brief line by line, a line that ends in an odd run of backslashes (a shell continuation in
                       a fence, or a Markdown hard break) or inside a git command line before its subcommand (Run
                       git, git -C w) joined with the next. A REACH is a git command line (the COMMAND LINE grammar
                       below) whose subcommand is clone (its ref: the value of --revision, else of -b or --branch,
                       else the remote's default branch), checkout (the first word that is not an option, which
                       after -b or -B and the new branch is the start point and after --detach is its value; none,
                       not a reach), switch (the same, after -c or -C), reset in any mode (its first word: a soft
                       or mixed reset moves HEAD, and a later checkout or reset of the tree puts the commit in it),
                       fetch (the source side of each refspec after the remote; with no refspec, the remote's
                       branch tips), pull (always the remote's branch), worktree add (the commit-ish after the
                       path), merge (its first word; with none, the upstream branch), rebase (the value of --onto
                       and its first word; with neither word nor --root, the upstream branch), restore (the value
                       of -s or --source; with none, not a reach) or archive (its first word, local or --remote);
                       a merge or rebase with --abort, --continue, --quit, --skip, --edit-todo or
                       --show-current-patch is not one. A checkout or reset of a commit followed by -- and a path
                       restores paths from it, and reaches that commit. Its arguments run to the first `;`, `&&`,
                       `||`, `|` or backtick, the next command line or the line's end, and are walked word by
                       word: no pattern reads them, and `--` ends
                       the options; the words after it are still a fetch's remote and refspecs and a worktree add's
                       path and commit-ish, they are a checkout's or reset's paths, and no other reach reads them.
                       A ref MOVES when its word names a branch or tag by its place, whatever its spelling: a
                       clone's -b or --branch value (git clone --branch 20261029 moves), the remote's default
                       branch, a fetch's or pull's branch tips, the upstream branch of a merge or rebase with no
                       word, and the word of a switch with no --detach, -d, -c or -C (git refuses a commit there).
                       Any other ref is FIXED when it is 7 to 40 or 64 hex digits, a refs/dispatch/ ref, or HEAD,
                       ORIG_HEAD or @, alone or followed by ~N or ^N steps (each names a commit HEAD already held,
                       so the reach that put it there is the one judged). FETCH_HEAD takes the ref of the last
                       fetch or pull before it in brief order (its first refspec), so git fetch origin && git
                       checkout FETCH_HEAD reaches the remote's branch tips and moves, and git fetch <url> <pin> &&
                       git checkout FETCH_HEAD reaches the pin; with no fetch or pull before it, it is fixed. Every
                       other ref moves (a branch, origin/main, a tag, main~3, @{u}, a placeholder such as
                       <branch>). A moving reach refuses unless a later CURE in brief order (on a later line, or
                       after it on its line) cures it, and a cure is a closed shape that reads no English: nothing
                       else is one. Its COMMAND is, word for word on whitespace, git checkout --detach P or git
                       switch --detach P, either with -C and one word D between git and the subcommand, or git -C
                       D checkout P; P is the value of the brief's BRIEF-PIN lines (which agree, P3) when it is
                       exactly 40 hex digits, compared character for character (a REVIEWED HEAD line, a confirm
                       clause, a pin of fewer than 40 or of 64 digits, a prefix, another case, FETCH_HEAD and
                       every other word are not P), written P^{commit} (PEEL_SUFFIX: the form a refusal gives) or
                       bare, and D is a closed path: ASCII letters, digits, _ . / and -, and <name> placeholders
                       of them each a whole path segment (at D's start or after /, and at its end or before /:
                       <scratch>/r, not w<x>), not opening with - (no shell metacharacter, quote, ~ or $). A
                       commit id peeled so (<id>^{commit}) is a fixed ref wherever a reach names it. Git reads
                       P^{commit} as the commit P itself, but a checkout or switch of a bare P takes a local
                       branch named with P's 40 hex digits first (a clone makes one when the source's default
                       branch has that name), so HEAD then lands on that branch's commit with exit 0 (measured;
                       a tag of that name did not take P's place); the bare form stays accepted for the briefs
                       that name it so, and that branch is its residual.
                       The command cures only on a CURE LINE, one physical line (a line joined by a backslash
                       or after a git word is none) whose raw text, read before format characters are removed,
                       is printable ASCII with only space or tab between words (a format character, a carriage
                       return, any other control character or any other space makes it none), that holds no part
                       of an HTML comment (from <!-- to the next -->, in a fence or not; a fence marker inside a
                       comment opens or closes nothing), and that is either (a) the command alone, bare or as the
                       whole of one code span of single backticks, after only whitespace and at most one of a
                       list marker (-, *, +, N. or N)), a `$` prompt or the word then (any case), where a fence
                       line, literal text, takes only the `$` prompt (a diff's removed or added line, any other
                       list marker and then open no cure there); or (b) a line that opens, after whitespace and at
                       most one list marker (none in a fence), with the words Make a scratch clone:, Clone or Run
                       (as written) and whose whole rest is one code span of single backticks that is exactly a
                       cure chain: git clone, any of --no-hardlinks, --no-checkout, -q and --quiet, a source (a
                       closed path that may also hold : and @) and a directory D (a closed path), then && and the
                       command with -C D, the clone's own directory. So `git -C w checkout --detach <pin>` on a
                       line of its own, then git switch --detach <pin>, a fence line $ git checkout --detach <pin>
                       and "Make a scratch clone: `git clone <url> w && git -C w checkout --detach <pin>`" cure,
                       and these cure nothing, whatever the words around them: "Run `git -C w checkout --detach
                       <pin>`" (no clone), "Run `git -C w checkout --detach <pin>`." (a stop after the span),
                       "Then run `git -C w checkout --detach <pin>`", "Clone with `git clone <url> w && git -C w
                       checkout --detach <pin>`", "Run `git clone <url> w && false && git -C w checkout --detach
                       <pin>`", "Clone `git clone <url> w && git -C x checkout --detach <pin>`", git -C w;: checkout
                       --detach <pin>, "Make a scratch clone: git clone <url> w, then git -C w checkout --detach
                       <pin>; never a worktree.", and git -C w reset --hard <pin> or git checkout --detach <pin> --
                       docs on a line of its own; nor does a fetch, clone, merge, rebase, restore, archive or
                       worktree add of the pin, or a dispatch ref. A
                       reach with no git word is printed under the same
                       cure, never refused, in three shapes only: a prose reach (clone, check out, switch to or
                       fetch that is not a git subcommand, followed within three words by --branch, -b, main,
                       master, trunk, develop or "the branch"), gh pr checkout, and a web address (http:// or
                       https://) whose path holds archive, tree, blob or raw followed by a ref that is not fixed
                       (refs/heads/ or refs/tags/ and an archive suffix dropped). A git submodule update --remote
                       moves submodule content, not the pinned tree, so it is printed the same way, never refused.
                       A dispatch ref that resolves in the repository the lint is pointed at (--repo; main passes
                       this repository when --repo is absent) to a commit that is no pin is HARD; one that does not
                       resolve there (or, for an evaluate() caller, with no repository) is ADVISORY, since that
                       repository need not be the server the worker fetches from; a FETCH_HEAD that inherits a
                       dispatch ref is not read again. A PROHIBITION governs only its own OBJECT, the closed shape
                       the S rules use: a prohibition word (never, do not, don't, must not, not to, as a whole word,
                       in any case, outside a code span and not glued to `=`), closing emphasis, whitespace and at
                       most one prohibited verb (run, use, execute or follow, plain or in emphasis), then, once
                       emphasis and quotes are skipped, the code span that opens there (every reach in it) or the
                       one git command line or prose reach that starts there. Every other reach is judged. "Never
                       run `git clone --branch main <url>`; read the workdir.", "Never *run* `git clone --branch
                       main <url>`.", "Never run `git pull; git clone --branch main <url>`." and "Do not run git
                       clone --branch main; read the workdir." pass; "Do not forget to run `git
                       clone --branch main <url>`.", "It is important not to skip `git clone ...`.", "Run `git log
                       --color=never` and `git clone ...`.", "Never run git pull or git clone --branch main.", "Do
                       not edit the tree and clone with ...", "Do not edit the tree; clone with ..." and "Remember
                       not to push. Run `git clone --branch main <url>`." refuse. A refusal gives one line to copy
                       after the reach, git -C <dir> checkout --detach and the first 40-hex BRIEF-PIN value followed
                       by ^{commit} (with none, it says to add such a BRIEF-PIN line first), and the REFUSE line
                       states the cure shape (CURE_SHAPE). With no pin the rule does not fire.
  P6 declared input    HARD, for every family. INPUT declarations are read by ONE shared parser
                       (tools/_input_declarations.py), the same reading check_verifier_delivery's C1 takes, so
                       the two cannot disagree, and that parser judges the CommonMark document the vendored
                       parser builds (opf/tools/_vendor/marko, pinned 2.2.4, with the vendored GFM elements),
                       because the worker reads the brief through a Markdown reader. A declaration is a
                       paragraph at the document's top level or directly in a list item (bullet or numbered),
                       judged one rendered line at a time: the line declares when its rendered text, after the
                       parser has applied emphasis, strikethrough, backslash escapes and character references,
                       and after format (Cf) and combining (Mn, Me) characters are removed and each character
                       is folded through NFKC and a bounded confusable-to-ASCII table (Greek and Cyrillic
                       look-alikes of I N P U T; U+A789, U+2236, U+0589, U+05C3, U+02D0 and U+02F8 read as the
                       colon), begins with INPUT: (upper case, the colon directly after, no part of the label
                       in a code span or inline HTML), so **INPUT:**, __INPUT:__, *INPUT:*, INPUT\\:,
                       INPUT&#58; and a fullwidth or confusable-spelt label all declare what the reader shows.
                       After the label comes one path, taken from the rendered text: one word, one code span
                       (the quoted form for a path with spaces or markup characters) or one double-quoted
                       span, then only whitespace on that rendered line; emphasis inside an unquoted path is
                       applied like any other emphasis (quote such a path in a code span, whose literal
                       characters are then judged by shape). The lint calls os.stat on the declared path on
                       the host the lint runs on: a path that does
                       not exist (ENOENT, or ENOTDIR for a path under a file; a dangling symbolic link is one,
                       since the link is followed) is a HARD finding (exit 1). Any other stat error (EACCES on a
                       parent directory, ELOOP, ENAMETOOLONG), a path that is neither a regular file nor a
                       directory (a FIFO, a socket, a device) and a path os.access(R_OK) refuses for the lint's
                       uid are CANNOT_EVALUATE (exit 2); os.access opens no descriptor, so a FIFO or a device is
                       never opened. A path of only / is absolute with no `..` component, so INPUT: / declares
                       the root directory and passes where the root is readable. A declaration P6 cannot check
                       is CANNOT_EVALUATE (exit 2): no path (a bare
                       INPUT:, an empty quoted path), a path holding a placeholder, variable, home, glob or
                       brace character (< > $ ~ * ? [ ] { }) or a control character,
                       a relative path, a `..` component, a quote that does not close on the line, a word
                       after the path (an unquoted space), or inline HTML in the path (a <scratch>
                       placeholder, which a reader hides). A
                       LOOK-ALIKE input label is never ignored: a rendered paragraph line (wherever the
                       paragraph sits), a heading or a table cell whose folded text begins, past leading
                       whitespace, dashes and bare markup marks, with the token INPUT or INPUTS in any case
                       and then a colon (Input:, INPUTS:, INPUT :, **INPUT: with its markup unclosed,
                       <b>INPUT:</b>, a task-list item's label, a bulleted or dashed label, a blockquoted
                       label, ## Inputs:), and a table cell that is exactly INPUT or INPUTS (upper case), is
                       CANNOT_EVALUATE (exit 2), and so is a label opening a line of an HTML block or comment
                       once its tags are stripped (a reader may hide that text). NAMED MISSES: the scan is
                       anchored at the rendered start, so prose that only mentions the word input (The tool's
                       input: a TOML file.), a label glued after other visible text (1.INPUT:, Step 1 -
                       INPUT:), a mid-line table pipe in plain prose (| INPUT | x | with no delimiter row) and
                       a | Input | Expected | header pass; the confusable table is bounded, so an unlisted
                       look-alike letter or colon passes; INPUT FILE: x (a word between the token and the
                       colon) declares nothing and is not a look-alike; and a link reference definition
                       ([INPUT]: /x) renders as nothing and declares nothing. CODE is never read: inside a
                       fenced or indented code block and a code span nothing is a declaration and nothing is a
                       look-alike, so a brief may quote a GitHub Actions workflow (an indented inputs: key),
                       an example INPUT line, or the label itself (`INPUT:`) in code; a tab-indented INPUT
                       line is an indented code block and declares nothing. A
                       path in prose is never judged by P6: only a
                       declared input is, and an output the brief asks the worker to create is not declared, so
                       it is not judged. A brief the vendored parser cannot read, or one over its 1 MiB
                       ceiling, is CANNOT_EVALUATE, never read another way; conformance is exactly Marko
                       2.2.4's, so rare edge inputs are read exactly where Marko diverges from a strict
                       CommonMark reading (a no-break space accepted after a closing fence, for one).

What the preflight cannot know: whether the worker's uid can read a path (permissions, ACLs, a mount, a
sandbox); that is the dispatcher's check, made where the worker runs, and the hard readability check belongs
there. P6 checks only the paths a brief declares on INPUT: lines, only on the host the lint runs on and only at
lint time: a declared input present there can be missing where the worker runs, unreadable by the worker's uid,
sandbox or mount, or removed or replaced after the lint runs; os.access answers for the lint's own real uid and
groups, not the worker's, so a lint run as root passes a mode-000 file; a directory is checked for read
permission, not for search permission; a symbolic link is followed and its target judged; a required input the
brief names only in prose, in code (not checked by design), or not at all, is not checked; and a
verifier that skips a declared input without
saying so is not caught here (check_verifier_delivery reads a clean verdict whose UNVERIFIABLE answer names a
declared input, or a parent directory of one, as no vote, and no more). P2 does not follow symbolic links, so a path under the workdir lexically
can lead outside it; the preflight does
not know which relative paths are inputs and which are files the brief asks the worker to create, so a path
missing at a pin is ADVISORY (P3); P2 judges only the slashes above (a path split by a line break, a relative
path, a path in an http:// or https:// word, and a path spelt with look-alike slashes are not judged, and an
unquoted path with a space is read as separate words, so /w/repo x/y is /w/repo and the relative word x/y); a
prohibition is read lexically, so every word after the first negation word on its line is never judged by P2;
and reads-outside-workdir is what the table declares, not a measurement. P5 misses or over-refuses: a reach
with no git word is read only in the three ADVISORY shapes above, so a brief that asks for a branch clone in prose
is printed, not refused, and any other phrasing (a raw.githubusercontent.com path, a download link that names no
ref, a reach told in other words) is not read at all; a git command line quoted as an example (in quotes, after
such as or for example, or in a code span in a sentence that is no instruction) refuses, since those markers also
introduce real instructions (the sender writes "the branch clone" instead), the accepted cost of reading command
lines structurally, and so does one quoted on a later line or after the object of the prohibition that introduces
it ("Do not follow the old instruction quoted below." then an indented git clone --branch main <url>); a
prohibition is read lexically and governs only its object, so the second command line of a prohibited list ("Never
run git pull or git clone --branch main."), a reach after an introduction ("Never run the following: git clone
--branch main"), a prohibition with any other verb ("Never type git clone --branch main") and a reach before the
prohibition that forbids it ("git clone --branch main must never run") refuse; a cure is read by its shape alone,
so a cure line that a report, a question or a prohibition quotes (on a line of its own, or as a fence line, after
"The previous worker ran:" or "Never run this:", or as a diff's context line, which opens with a space) cures,
because the shape is a whole line of the canonical command, and a cure in any other place (inside a sentence,
after any other word, or with a stop or any word after it) cures nothing and refuses whatever the words around it,
as does every cure in a brief whose pins are only REVIEWED HEAD lines, confirm clauses or BRIEF-PIN values of
fewer than 40 or of 64 hex digits (a brief pinned in a SHA-256 repository has no cure and refuses); a brief with
CRLF or any other non-LF line end never reaches P5 (the TEXT gate refuses it, exit 2); REVIEWED HEAD lines with no
BRIEF-PIN line are not compared with each other, and a confirm clause is not compared with the pin lines (the pin
lines govern); a look-alike pin line is read only at a line's start, with the two labels as written once case,
compatibility forms and Pd dashes are folded (a label later in a line, in a table row's second column, spelt
another way such as Brief pin, BRIEF_PIN or Reviewed-head, or with a look-alike letter of another script or a
minus sign for its hyphen is not read), only with a hex commit id after it (a branch name is not read), and only
where the brief has a BRIEF-PIN line; a bare pin in a cure (git -C w checkout --detach <pin>) takes a local branch
named with the pin's 40 hex digits before the commit, so where the clone's source names its default branch so,
HEAD lands on that branch's commit with exit 0 and the lint passes (the refusal gives <pin>^{commit}, which no
branch can take the place of); a cure chain whose clone targets a directory an earlier clone in the brief already
made is read as a cure, but git refuses that clone (exit 128, the destination already exists) and && skips the
checkout, so the earlier clone stays at its moving ref, a loud failure rather than a silent wrong revision; a
<name> placeholder in a cure chain's source may stand anywhere in it; an HTML comment is read by its markers alone,
so a <!-- in a code span or a fence opens one too, and the lines up to the next --> hold no cure, and a fence that
opens inside that stretch and holds the --> is read as prose after it, so a list marker or then opens a cure on its
lines; a <name> placeholder in a cure's
directory or source is not runnable as written (bash reads <scratch>/r as two redirections), so the brief must
tell the worker to put a path in its place; a cure the worker cannot reach passes (a shallow or --single-branch
clone may not hold the pin; git is expected, not verified for every version, to fail the checkout then); outside a
cure chain a cure in another repository (another -C path, or none) is not told apart, since a cure is matched by
its words, and FETCH_HEAD takes the last fetch in the brief whatever its repository; a FETCH_HEAD with no fetch or
pull before it in the brief (filled outside it, or by a fetch that is a prohibition's object) is not judged; a
branch or tag named with hex digits alone is read as a commit id where a word may be either (a checkout, reset,
worktree add, merge, rebase, restore, archive or fetch refspec: git checkout 1234567abc takes the branch when one
has that name), so it passes; a path restore with no -- (git checkout <pin> docs) is read as a checkout of its
first word; a reset with no -- reads its first word as the commit, so git reset <path> to unstage refuses where
the path is not hex (git reset -- <path> does not); a merge's words after its first, a rebase's second word (the
branch it switches to first), cherry-pick, revert, am, apply, read-tree, stash, bisect, a git diff or git show
piped to apply, and a reach through an alias, a shell variable, `-c alias...` or a script are outside its
reading; an option value quoted with a space after the subcommand is read as several words, so its later words
can be read as refs; a reach whose own arguments wrap onto the next line with no backslash (git checkout --detach
at a line's end, main on the next) is read without them, so it is not judged, and a code span across a line break
is read as two lines; a tag force-moved after the lint runs is not seen; a dispatch ref written by hand that
--repo cannot resolve is only ADVISORY, and the worker then fails closed at the fetch; and, like every rule, it
helps only where the sender refuses to send on exit 1 or 2.

TEXT. A brief holding a line separator other than LF anywhere (a carriage return, bare or in CRLF, U+0085
NEXT LINE, U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR), or a format character (Unicode category Cf: a
byte order mark, zero-width and bidirectional controls) outside a fenced code block, is CANNOT_EVALUATE
(exit 2) before any rule runs: such a character can hide or split a line or a declaration, so it is refused,
never removed silently. A format character inside a fenced block's body is still removed
for every rule but the P5 cure, which reads each cure line as written; a brief
that is then empty or only whitespace and control characters is blank. Every list item (a line opening with `-`,
`*`, `+`, `N.` or `N)`, at any indentation) is its own paragraph, so it starts a sentence. A fence opens at any
indentation.

BLOCKING RULES (HARD; one hit refuses):

  S1 executable fence  A fenced block tagged as a shell or console session (EXEC_FENCE_TAGS: bash, sh,
                       shell, console, zsh, pycon, ...); a fence tagged python, python3, py or py3 whose
                       first non-blank line is a run prompt (`>>> `, `$ `, `In [1]:`); or a fence untagged
                       or tagged text, txt, plain, plaintext, none or code whose first non-blank line
                       starts with `$ `, or with a listed command (git, python, python3, python3.N, pytest,
                       cd, bash, sh, zsh, npm, npx, pip, uv, cargo, tox, nox, docker, curl, wget, chmod,
                       mkdir, rm, ls, grep, make, go; optionally after sudo, `NAME=value` assignments and
                       a directory path) or a path opening with `./` or `tools/`. INFO instead of HARD
                       only when the last sentence of the paragraph just before the fence says the fence
                       is not to be run ("do not run this", "do not run it", "never execute the
                       following", "not to be run"), holds no EXCEPTION word and no execution verb
                       (EXEC_WORD_RE) but the prohibited one, and that paragraph has no R1 or R5 hit.
  S2 command in item   A command line in a numbered item (with the indented lines, nested list items
                       and list items without a blank line before them that belong to it).
  S3 command in the    A command line anywhere else in the text the family is addressed by: the
     preamble          paragraphs, headings and unnumbered list items outside the numbered items. This
                       catches "Read only `git show <sha>:<path>`" sent to a family that cannot run git.

A COMMAND LINE, in prose or in a code span, is: git (optionally behind a directory path), any number of
global options (`-C` or `-c` with a value, `--git-dir`, `--work-tree`, `--namespace`, `--config-env` or
`--attr-source` with a separate value, or any other option word, with or without `=` and a value; a value is one
shell word, whose "..." or '...' spans closed on the line may hold a space: git -C "/w/a b" log), then a git
subcommand (GIT_SUBCOMMANDS:
show, diff, log, blame, status, rev-parse, show-ref, show-branch, annotate, checkout, commit, push, ...);
python, python3 or python3.N (optionally behind a directory path), any number of options (value-less flag
clusters such as `-I -B`, `-W` or `-X` with an attached or a separate value, long options), then `-c`,
`-m` (alone or closing a cluster: `-Ic`) or a `.py` path; a `.py` or `.sh` path then a flag (`t.py
--self-test`); `./x.sh` or `./x.py`; bash, sh or zsh then `-c` or a `.sh` path; pytest then a flag, a `.py`
path or a tests directory; or a code span that opens with make, pytest, tox, nox, npm, npx, cargo, go, uv,
pip, pip3 or docker and an argument. A bare path (`tools/t.py`) is a file name, not a command line.

DEMOTIONS. A command line is INFO (printed, never refusing) only in one of two CLOSED SHAPES. Each is a whole
sentence, once markup (a list marker, emphasis) is skipped, with nothing after the command line but the stop:
  PROHIBITED          "Do not run <command line>." or "Never run <command line>."
  PAST REPORT         "CI failed on <command line>.", "The check failed on <command line>." or "The job failed
                      on <command line>."
The command line is one code span, or a prose command line followed only by argument tokens (a token that opens
with - or a digit, or that holds /, :, =, ., @, ~, ^ or <): "Never run git show abc1234:t.py." is INFO, and "Never
run git log and report it." and "Do not run `./x.sh` even if it passes." refuse. Only the command line that opens
right after the shape's words is demoted, so in "Never run git log ./x.sh." the second command line refuses. A
sentence split off at a semicolon has no stop, so "CI failed on `make test`; please confirm it passes now."
refuses. Another sentence of the same VOID GROUP that asks for anything voids the demotion and the command line is
HARD: a sentence that holds, outside a code span, a listed request word (VOID_WORD_RE: confirm, check, verify, see,
try, retry, report, show, run, rerun, perform, do, execute, invoke, launch, call, use, repeat, reproduce, paste,
ignore, disregard, override and their forms; and, at a clause start only, VOID_START_RE: test, retest, fix and
their forms), or ends as a question, or has an R1, R5, R6 or R7 hit. A fence's contents are read as such
sentences. A closed-shape sentence asks nothing (its words are fixed), and neither does a heading. A void group is
a top-level numbered item with every block that belongs to it (its lazy continuation lines, nested list items
whatever their numbers, indented paragraphs, indented headings at any level, and fences), named by the top-level
item and never by a nested numeral or a heading; outside the numbered items, it is the blocks from one unindented
heading (or the brief's start, or the end of a numbered item) to the next such heading or numbered item. Nothing
else demotes a command line (the S1 not-to-be-run
introduction demotes only a fence): not quoted source text ("Quote the README command `make test`" refuses), not
a Find clause, not a code span, not quotes.

ADVISORY RULES (each runs on one sentence; a sentence ends at `.`, `!` or `?` before a capital letter,
or at `;`). A CLAUSE START is the start of a sentence, or the point just after a colon, a comma, a
parenthesis, `and`, `then`, `but`, or `so`, once markup (list markers, emphasis, `Step N -`) is
skipped, and again once at most six lead-in words are skipped (please, also, now, first, do, do not,
never, must, can you, could you, you need to, make sure to, go ahead and, ...). NOUN USES open no
clause: a parenthesis that holds only one word or a list of single words joined by commas (an Oxford
comma included), slashes, `or` or `and` (each optionally led by an article, determiner or possessive:
"(execute)", "a stalled (TIMED-OUT) run", "(a lint, gate, test, build, or self-test)") is not a clause,
so neither its parentheses nor the breaks inside it are clause starts, except that the point after such
a parenthesis that itself opens a clause is one ("(Optional) Run the suite"); and in a list of at least
three single words joined by commas and closed by `or` and a last word, either before punctuation
("lint, gate, test, build or self-test.") or, when an article, determiner or possessive leads the list,
before a word that is not an article, determiner or pronoun ("each lint, gate, build or self-test writes
a log"), no comma or word after the first is a clause start. A verb is NEGATED when `not`, `never`, `no`,
`nothing`, `without`, `cannot`, `nor`, or an `n't` form, found in the whole sentence, ends at most 60
characters before the verb begins with no HARD BREAK between them, or `nothing` follows the verb. A hard
break is a colon, a parenthesis, `then`, `but`, or the comma that closes an opening clause led by if,
once, when, after, before, unless, until, while, since, as soon as, or without; a plain comma or `and` is
not one, so "do not run, build or install" stays negated.

  R1 imperative    Not read in a Markdown heading. An execution verb (run, re-run, execute, invoke,
                   launch, build, compile, install, reproduce, benchmark, profile, measure, inject,
                   trigger, fuzz, bisect, clone, deploy, `check out` before a branch, commit, revision, tag
                   or code span, `time the ...`, and observe in a sentence that also injects) at a clause
                   start, outside a code span, not negated, and not followed by a colon, a hyphen, a dot or
                   slash joined to a word (so `install.sh` is a file name), `is` or `are`, or a noun that
                   makes it a noun phrase (configuration, failures, steps, order, ...); `try`, `start by`
                   or `begin by` before a gerund (running, building, ...); or `must`, `need to` or `has
                   to` before `be run`, `be executed`, `be built` (and the like) in a sentence that is not
                   a question.
  R5 result        A reporting verb (report, paste, give, record, capture, attach, print, state, show,
                   share, post, count, tell me) at a clause start, not negated and not followed by a noun
                   such as statements or calls, then in the sentence an execution result (an exit code or
                   status, a return code, the last or final lines, output, stdout or stderr, counts,
                   seconds, timings, a run time), unless the sentence names a static source after the verb
                   ("in the source", "the docstring documents").
  R6 outcome       A question ending with `?` opened at a clause start by does, do, did, is, are, was,
                   were, will, would, can, or could, or a sentence opened at a clause start by confirm,
                   verify, check, ensure, show, prove, establish or make sure, whose subject (at most 200
                   characters) is a test, self-test, gate, check, suite, CI, a `.py` or `.sh` file, or a
                   `--check` or `--self-test` flag, and whose outcome is pass, succeed, exit N, fail
                   without, fail on the old tree, turn red, go or stay green, unless the outcome opens a
                   relative clause (right after that, which, who, or an opening parenthesis).
  R7 git fact      A fact only git or a run can establish (a commit's parents, SHA, hash or id; which or
                   what commit or revision did something; a diff or changes between commits or revisions;
                   changed lines; an observed or actual exit code, or one of a run; test counts), outside a
                   code span and not negated, in a sentence that asks for something (a question ending
                   with `?`, or a request verb or wh-word at a clause start). No marker exempts an item:
                   an advisory rule needs no exemption.

Outcomes, printed one finding per line (HARD, INFO, or ADVISORY) and then one summary line:

  exit 0  OK               no HARD hit (INFO and ADVISORY findings may be printed).
  exit 1  REFUSE           at least one HARD hit (S1, S2, S3, P2, P5 or P6). Rewrite the brief, or send the item
                           to a family that can execute (S1 to S3), name paths it can read (P2) or, after the reach,
                           add the cure line the hit gives (git -C <dir> checkout --detach <pin>^{commit}) or reach
                           the pin by a dispatch ref (P5), or provide each missing declared input or correct its
                           INPUT line (P6).
                           When a hit is a command line (S2 or S3), the REFUSE line
                           says how to rewrite: name the function, row or entry instead of embedding the
                           command (quote the def line of X, not the line that runs git push).
  exit 2  CANNOT_EVALUATE  the brief is missing, not a regular file, larger than MAX_BYTES, not UTF-8, or
                           blank; a scope range runs backwards; a pinned revision does not resolve or
                           cannot be read (P3); an INPUT line P6 cannot check, a look-alike input label, a
                           brief the shared reading's vendored parser cannot read, or a
                           declared input that exists but cannot be confirmed readable (P6); the family is not
                           in the table; the
                           table is missing, unreadable, or malformed (format-version must be the
                           integer 1); or the arguments are wrong (a repeated --family or --table
                           included). A brief that cannot be evaluated is never OK.

Run with no arguments, it lints no brief: it validates the shipped capability table only and exits 0
when the table is valid, 2 when it is not.

RESIDUALS (what it does not catch; examples, not a complete list). A request with no fence and no
command line never refuses: "run the self-test and report its exit code", "which commit added the
helper?" and "build the docs" are ADVISORY only, so a human must read the ADVISORY lines; for the same
reason a closed shape stays INFO when the request that follows it is in another numbered item ("1. Do not run
`git log -p`." then "2. Run it.") or under another unindented heading, when the request is in a lead-in before
the list ("Please confirm each of these passes now:" then "1. CI failed on `make test`.") or in an unindented
paragraph or bullet after the item ends ("Confirm it passes now." or "- Confirm it passes now." after a blank
line), or when it is phrased with no listed request word, no question and no advisory hit ("CI failed on `make
test`. Make it pass."); test and fix void only at a clause start,
so "Please make the test pass." does not void it, while "Tests are flaky on main." does.
A command outside the COMMAND LINE list does not refuse ("mktemp -d", "go test ./..." or "make test"
outside a code span, "./x" with no `.sh` or `.py`, a git alias such as "git lg", a capitalised "Git log",
git or python options outside the listed value-taking ones whose value is a separate word, such as
"python3 --check-hash-based-pycs always t.py"), nor does a shell command in a fence tagged with another
language without a run prompt, a fence whose first line is not a listed command, or a command line split
by a line break inside a word. An indented code block with no fence is read as prose: a listed command
line in it refuses (S2 inside a numbered item, S3 outside), and any other command in it does not. Some
text refuses that a human would pass; that is the accepted cost of closed shapes, and the sender rewrites the
brief. A command line held as quoted source text refuses ("Quote the README command `make test`", "Quote the
scope_rows entry for FOO=1 ./x.sh with its expected exit", "Quote the README command: `make test`."): name
the function, row or entry by its id, or describe the line ("the README's test command"), instead of embedding
the command line, as the REFUSE line says. So do a prose or code-span mention of a git subcommand or a command
line outside the two
closed shapes ("the git log shows the fix", "a `git diff` excerpt follows", "is `t.py --self-test` listed in
CI?", "Does the docstring example `git show HEAD` match the usage line?", "Which line of t.py calls `git
rev-parse HEAD`?", a heading "PR 9 adds a git log parser"); a closed shape with anything after the command
line ("Never run git checkout main.", whose last word is not an argument token) or any other wording ("Don't
run", "Never use"); a closed shape whose item or section also holds a listed request word in another sentence
("See the notes."), a question, or an advisory hit, including an R5 hit on a reporting verb inside a code span
("`git log, report stdout`"); and a shell-tagged fence shown as an example. The advisory rules are lexical over
listed words and miss free English (an implicit request, an unlisted verb, a request split across
sentences, a request in a heading or a Find, Quote or Cite clause, an `or`-closed word list of
imperatives, a list led by a determiner before a bare plural object ("read the diff, compile or run
tests"), a git or run fact phrased outside the R7 list), and they also print hits a human would not call
requests (R7 on "quote the comment that states the exit code of the run"); being advisory, neither
refuses. Scope is lexical: item numbers are matched wherever they appear, so two numbered lists in one
brief share their numbers; an unindented lazy continuation line joins its item's void group but is read as text
outside the numbered items (S3) and is not dropped with its item; a family addressed without the `leg` marker
("Claude: run ...") is linted as if addressed to the linted family; and an operative marker for another family
hides that family's text up to the next marker or address, so an instruction for the linted family placed there
without an address is hidden. The lint reads only the bytes it is given: run it on the final per-family bytes that
are sent, since a brief composed after the lint runs is not covered. Run alone, it is advisory; it blocks
only where the sender refuses to send on exit 1 or 2.

TIME. Linearity rests on two kinds of evidence, not on a proof: a regex audit and measured shapes. No regular
expression reads a run of arguments, options or markup: such runs are walked token by token in Python.
THE REGEX AUDIT (the self-test, regex_audit) covers the compiled patterns bound to module-level names and each of
COMMAND_FORMS, enumerated and never listed by hand, and fails on any construct in them it cannot read. Beyond that,
without claiming to find every pattern, it also parses the items of tuple, list, set and frozenset globals and the
values of dict globals at any depth, the attributes of a class defined here, and each call of a re matching
function in the production source that the namespace does not hold (read from its literal pattern and flags). In
that source it fails on a call whose pattern or flags is not a literal, that passes a starred or double-starred
argument or that gives its flags twice, and on the other uses of re it recognizes: a bare `re` passed or bound
(getattr(re, ...)), a matching function named without a call, `import re as`, an import from re. Outside the
audit, and not chased: a pattern held as a dict key or in any other object the walk does not enter (an instance, a
function's closure or defaults) unless the source read meets its call with a literal pattern, a module-level name
compiled twice (only its last value is walked), and re reached by reflection (re.__dict__, getattr on anything but
the bare name re, re's private modules, __import__, importlib).
It fails on five constructs, each a guard the self-test patches
out alone against a canary that only that guard flags: two quantifiers whose classes overlap, adjacent or separated
only by atoms that can match empty (`\\s*,?\\s+`, `\\s*\\s+`, `\\w+\\w*`); a repeated group whose tail and head
quantifiers overlap; a repeated group whose body can match empty; an unbounded repeat (*, +, {n,}) that is not
possessive over a body holding an alternation or a quantifier anywhere inside it (`(?:a|ab)*c`, `(?:\\s+\\S+)*`),
flagged without deciding whether the alternatives overlap, so overlapping alternatives (the previous revision's
argument tail) are caught; and a construct the audit does not read (a back reference). REGEX_AUDIT_EXEMPTIONS would
record, per pattern, a one-line proof that the flagged alternatives cannot match a common prefix, and each exempted
pattern would need a failing-suffix shape; it records none. A possessive quantifier or an atomic group gives nothing
back, so it is not the first of a pair and the repeat rule does not flag it: it keeps its first match, and a failure
after it is not retried inside it. Single-character alternatives are read as the one class the parser folds them
into. Its limits: it does not flag a failed match retried at every offset of a long run by search() or finditer(), a
bounded repeat over alternatives (LEAD_IN_RE reads at most six lead-in words), a quantifier that backtracks against
one fixed atom, two adjacent repeats of groups longer than one character that can match the same text
(`(?:ab)*(?:ab)*$`: the pair rule compares only the first group's last class with the next one's first, and such a
pair takes time that grows with the square of the input), or work that grows faster than the input in the Python
code around the patterns; it compares classes
over a sample of characters (ASCII and a few others), so an overlap only outside the sample is missed; and it reads
the interpreter's private regular expression parser, so a Python release that changes it fails the audit closed.
The self-test pins with a canary the walk's tuple, dict, frozenset and class-attribute branches, the source read's
refusals (an alias, non-literal flags, a starred or double-starred argument, flags given twice) and the repeat
rule's atomic-group branch, and checks that the source it reads assigns every module-level pattern.
Those gaps are covered, as far as they are, by construction and by the shapes: every scan is one pass per sentence
(or per paragraph, line or fence), with bisection over positions found once (clause breaks, code spans, negations,
noun uses, and the last result and static-source positions for R5, each search resuming one character after the
previous match); markup runs and command-word option runs are walked once and remembered, so a run shared by many
clause starts or command words ("1) 1) 1)", "git -c git -c ... show") is not read again, and there is no bound on the
number of git or python options; a closed shape is matched once per sentence at its start, and the tail after its
command line is split on whitespace once and its tokens classified by a plain predicate (argument_token), once per
command line end; per opener, R6 measures its subject's length before reading it; and every word-run, command and
file-name pattern is anchored at a token start. THE MEASURED SHAPES: in a child process capped at 1 GiB of address
space (RLIMIT_AS) with a LINEAR_TIMEOUT-second timeout, which must exit 0 and print two finite, nonnegative times,
each shape at 1 MiB must take at most LINEAR_FACTOR times as long as at 128 KiB plus LINEAR_SLACK seconds
(the input is eight times larger; a pass whose time grows with the square of the input takes about 64 times as
long). A shape is evidence only for the text it repeats. Measured on 2026-10-08 on one shared host, each in such a
child under nice 10, in two runs of these shapes: every shape took at most 0.29 s at 128 KiB and 2.13 s at 1 MiB,
at most 9.4 times its 128 KiB time. On the previous revision, with the same harness and a 60-second timeout
(measured to 60 s, not to completion), the three argument-tail shapes with a failing suffix (" -x.y" repeated then
"!", " -x/y" repeated with no stop, " --a=b" repeated then " x") did not finish within 60 s; the fence-contents shape
took at most 0.06 s there and guards the new reading of fence contents, not the change. Of the older shapes,
closed-args-linear repeats a token with one parse and did not catch that argument tail; the whitespace-run shapes
(whitespace-run-linear and item-list-whitespace-linear) were added when an earlier revision did not finish 128 KiB
within 60 s, and some others when an earlier revision exceeded the bound, each measured only to a 60-second timeout.

Usage:
  check_brief_capability.py                (no brief: validate the shipped capability table, exit 0 or 2)
  check_brief_capability.py --family NAME [--unscoped] [--table PATH] [--repo PATH] BRIEF
  check_brief_capability.py --self-test
  check_brief_capability.py --execution-report ABS_PATH   (the self-test, as the execution gate runs it)
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_brief_capability.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import atexit
import bisect
import contextlib
import errno
import io
import os
import posixpath
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_brief_capability.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402
import _git_fixture_env  # noqa: E402
import _input_declarations  # noqa: E402

# the one reading of INPUT declarations, shared with check_verifier_delivery (C1) so the two cannot
# disagree; bound here so the self-test can patch each seam out
forbidden_text = _input_declarations.forbidden
read_inputs = _input_declarations.read

ROOT = Path(__file__).resolve().parents[1]
TABLE_REL = ".aiqt/core/worker-capabilities.toml"
EXIT_OK, EXIT_REFUSE, EXIT_CANNOT_EVALUATE = 0, 1, 2
MAX_BYTES = 1 << 20
READ_CHUNK = 1 << 16
# Non-blocking, so a FIFO opens at once and is then refused by type; close-on-exec.
OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
TABLE_VOCABULARY = dict(
    execute=("yes", "read-only-sandbox", "no"),
    network=("yes", "no"),
    scratch=("persistent", "per-command", "none"),
    handback=("yes", "no"),
)
TABLE_VOCABULARY["repo-read"] = ("path",)
TABLE_VOCABULARY["git-read"] = ("yes", "no", "undeclared")
TABLE_VOCABULARY["reads-outside-workdir"] = ("yes", "no", "undeclared")
FAMILY_NAME_RE = re.compile(r"[a-z][a-z0-9-]*\Z")
NEGATION_WINDOW = 60
R6_SUBJECT_MAX = 200
EXCERPT_CHARS = 120


class CannotEvaluate(Exception):
    pass


class Block:
    """A run of brief lines: kind "prose" (a paragraph, a heading, or a list item) or "fence"."""
    __slots__ = ("kind", "start", "lines", "item", "top", "tag", "heading", "listed", "masked")

    def __init__(self, kind, start, item=None, tag="", heading=0, listed=False, top=None):
        self.kind, self.start, self.item, self.tag, self.heading = kind, start, item, tag, heading
        self.top = top        # the number of the top-level numbered item the block belongs to, or None
        self.listed = listed  # a list item, numbered or not
        self.lines = []      # [(line number, text)]; a fence holds its body lines only
        self.masked = None   # [start, end) ranges of the joined text that another family claims

    def text(self):
        return "\n".join(line for _, line in self.lines)


# ---------- inputs ----------

def _read_regular(path, what):
    """The bytes of a regular file of at most MAX_BYTES, or CannotEvaluate."""
    try:
        fd = os.open(path, OPEN_FLAGS)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot open {}: {}".format(what, path, exc.strerror or exc))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CannotEvaluate("{}: {} is not a regular file".format(what, path))
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise CannotEvaluate("{}: {} is larger than {} bytes".format(what, path, MAX_BYTES))
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot read {}: {}".format(what, path, exc.strerror or exc))
    finally:
        os.close(fd)


def parse_table(data):
    """The table as family -> (key -> value), or CannotEvaluate on anything outside its schema."""
    try:
        doc = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("capability table: not valid UTF-8 TOML: {}".format(exc))
    version = doc.get("format-version")
    # the exact integer 1: a bool (true == 1) or a float (1.0 == 1) is refused
    if sorted(doc) != ["family", "format-version"] or type(version) is not int or version != 1:
        raise CannotEvaluate("capability table: the top level must be exactly format-version = 1 (an "
                             "integer) and [[family]] rows")
    rows = doc["family"]
    if not isinstance(rows, list) or not rows:
        raise CannotEvaluate("capability table: no [[family]] row")
    keys = sorted(["name"] + list(TABLE_VOCABULARY))
    table = dict()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or sorted(row) != keys:
            raise CannotEvaluate("capability table: family row {} must have exactly the keys {}".format(
                index, ", ".join(keys)))
        name = row["name"]
        if not isinstance(name, str) or not FAMILY_NAME_RE.match(name):
            raise CannotEvaluate("capability table: family row {} has an invalid name".format(index))
        if name in table:
            raise CannotEvaluate("capability table: family {!r} appears twice".format(name))
        for key, allowed in TABLE_VOCABULARY.items():
            if row[key] not in allowed:
                raise CannotEvaluate("capability table: family {!r} has {} = {!r}, not one of {}".format(
                    name, key, row[key], ", ".join(allowed)))
        table[name] = dict((key, row[key]) for key in TABLE_VOCABULARY)
    return table


def load_table(path):
    return parse_table(_read_regular(path, "capability table"))


def _blank_character(ch):
    return ch.isspace() or unicodedata.category(ch) in ("Cc", "Zs", "Zl", "Zp")


def decode_brief(data):
    """The brief's text, or CannotEvaluate when it is not UTF-8, blank, or holds a line separator other
    than LF anywhere or a format character outside a code block or code span of the parsed CommonMark
    document (forbidden_text, the shared reading of tools/_input_declarations.py): such a character can
    hide or split a declaration, so it is refused, never removed silently, and a brief the shared
    reading's vendored parser cannot read (DeclarationError) is CannotEvaluate too. A format character
    inside code is still removed for the rules that read fence content (the P5 cure alone reads each
    cure line as written)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("brief: not valid UTF-8 ({})".format(exc.reason))
    try:
        found = forbidden_text(text)
    except _input_declarations.DeclarationError as exc:
        raise CannotEvaluate("brief: {}".format(exc))
    if found is not None:
        raise CannotEvaluate("brief: line {}: {}".format(*found))
    # A byte order mark, a zero-width or a bidirectional control is invisible and can split a word; only
    # a fenced block's body can still hold one here.
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    if all(_blank_character(ch) for ch in text):
        raise CannotEvaluate("brief: blank")
    return text


# ---------- structure ----------

FENCE_OPEN_RE = re.compile(r"([ \t]*)(`{3,}+|~{3,}+)(.*)\Z")
HEADING_RE = re.compile(r" {0,3}(#{1,6})(?:\s|\Z)")
ITEM_RE = re.compile(r" {0,3}(?:[-*+]\s+)?(?:\*\*|__)?(?:Q|Item\s+|Question\s+)?(\d{1,3})(?:[.)]|:(?=\s))"
                     r"(?:\*\*|__)?(?=\s|\Z)", re.I)
LIST_LINE_RE = re.compile(r"[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+\S")


def _fence_closes(line, opening):
    """Whether `line` closes a fence opened by the run `opening` (the same character, at least as many)."""
    stripped = line.lstrip(" \t")
    run = len(stripped) - len(stripped.lstrip(opening[0]))
    return run >= len(opening) and not stripped[run:].strip()


def encloses(top, indented):
    """Whether a numbered line belongs to the open top-level item `top` (a nested item) rather than opening one:
    it is indented under it."""
    return indented and top is not None


def heading_in_item(top, indented):
    """Whether a heading, at any level, belongs to the open top-level item `top` rather than ending it: it is
    indented under it. It stays in the item's void group; only an unindented heading opens a section."""
    return indented and top is not None


def lazy_continuation(current):
    """Whether an unindented line right after a line of the prose block `current` is a lazy continuation of a
    numbered item: `current` is a numbered item's block (Markdown continues the item's paragraph)."""
    return current is not None and current.item is not None


def parse_blocks(text):
    """The brief as a list of Blocks: fences, headings, list items, and paragraphs. Each block records the top-level
    numbered item it belongs to (`top`): a nested numbered item, an indented block or heading, a fence inside the item
    and an unindented lazy continuation line all keep the number of the item that encloses them."""
    blocks, current, item, top, fence, opening, blank = [], None, None, None, None, "", False
    for number, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        indented = line[:1] in (" ", "\t")
        if fence is not None:
            if _fence_closes(line, opening):
                fence = None
            else:
                fence.lines.append((number, line))
            continue
        opened = FENCE_OPEN_RE.match(line)
        if opened and not (opened.group(2)[0] == "`" and "`" in opened.group(3)):
            if not indented:
                item = top = None
            info = opened.group(3).strip().split()
            opening = opened.group(2)
            fence = Block("fence", number, item=item, tag=info[0].lower() if info else "", top=top)
            blocks.append(fence)
            current, blank = None, False
            continue
        if not line.strip():
            current, blank = None, True
            continue
        was_blank, blank = blank, False
        heading = HEADING_RE.match(line)
        if heading:
            current = None
            if not heading_in_item(top, indented):
                item = top = None
            block = Block("prose", number, item=item, heading=len(heading.group(1)), top=top)
            block.lines.append((number, line))
            blocks.append(block)
            continue
        numbered = ITEM_RE.match(line)
        if numbered or LIST_LINE_RE.match(line):
            # every list item is its own block, so it starts a sentence and a clause
            if numbered:
                item = int(numbered.group(1))
                if not encloses(top, indented):
                    top = item
            elif not indented and was_blank:
                item = top = None
            current = Block("prose", number, item=item, listed=True, top=top)
            current.lines.append((number, line))
            blocks.append(current)
            continue
        if current is not None and (current.item is None or indented):
            current.lines.append((number, line))
            continue
        if not indented:
            # an unindented paragraph ends the numbered item; a lazy continuation line stays in its void group
            item = None
            if not lazy_continuation(current):
                top = None
        current = Block("prose", number, item=item, top=top)
        current.lines.append((number, line))
        blocks.append(current)
    return blocks


# ---------- sentences and clauses ----------

SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+(?=[*_\"'`(\[]*[A-Z])|;\s+")
MARKUP_TOKEN_RE = re.compile(r"\s|[>*_#]|[-+–—](?=\s)"
                             r"|(?:Q|Item\s+|Question\s+|Step\s+)?\d{1,3}(?:[.)]|:|\s+[-–—])(?=\s)", re.I)
LEAD_IN_RE = re.compile(r"(?:(?:please|kindly|also|then|now|first|second|third|next|finally|and|or|but|so|"
                        r"again|separately|additionally|instead|always|just|do\s+not|don't|do|never|"
                        r"(?:can|could|would|will)\s+you|(?:you|we)\s+(?:must|should|may|can|need\s+to|"
                        r"have\s+to|will\s+need\s+to)|must|should|may|need\s+to|have\s+to|"
                        r"i\s+(?:need|want|would\s+like)\s+you\s+to|make\s+sure\s+(?:to|you)|be\s+sure\s+to|"
                        r"remember\s+to|go\s+ahead\s+and|try\s+to)\b[\s,]*){0,6}", re.I)
CLAUSE_BREAK_RE = re.compile(r"(?:[:(),]|\b(?:and|then|but|so)\b)\s*", re.I)
HARD_BREAK_RE = re.compile(r"[:()]|\b(?:then|but)\b", re.I)
SUBORDINATE_RE = re.compile(r"(?:if|once|when|whenever|after|before|unless|until|while|since|as\s+soon\s+as|"
                            r"without)\b", re.I)
NEGATION_RE = re.compile(r"\b(?:not|never|no|nothing|without|cannot|nor)\b|n't\b", re.I)
NEGATED_AFTER_RE = re.compile(r"\s+nothing\b", re.I)
BACKTICKS_RE = re.compile(r"`+")
NOUN_WORD = r"[A-Za-z][\w-]*+"
DETERMINER = r"(?:a|an|the|its|their|his|her|our|your|my|this|that|these|those|each|every|any|some|no|either)"
NOUN_PARENTHESIS_RE = re.compile(r"\(\s*(?:(?:or|and)\s+)?(?:" + DETERMINER + r"\s+)?" + NOUN_WORD
                                 + r"(?:\s*(?:,\s*(?:or|and)\b|,|/|\bor\b|\band\b)\s*(?:" + DETERMINER + r"\s+)?" + NOUN_WORD
                                 + r")*+\s*\)", re.I)
NOUN_WORD_RE = re.compile(NOUN_WORD)
# anchored at a token start: unanchored, a failed match restarts at every character of a long word, so
# the scan grows with the square of the word's length
WORD_RUN_RE = re.compile(r"(?<![\w-])" + NOUN_WORD + r"(?:\s*,\s*" + NOUN_WORD + r")++")
# the comma and the space before `or` are one separator, so no two whitespace quantifiers share a run
LIST_OR = r"(?:\s*,\s+|\s+)or\s+"
NOUN_LIST_TAIL_RE = re.compile(LIST_OR + NOUN_WORD + r"(?=\s*(?:[)\].;:!?]|\Z))", re.I)
# a list led by a determiner is a noun list also before a word that is not an object ("each lint, gate,
# build or self-test writes a log"; not "the diff, build or run the parser")
LED_LIST_TAIL_RE = re.compile(LIST_OR + NOUN_WORD + r"(?=\s+(?!(?:" + DETERMINER
                              + r"|it|them|all|both)\b)[A-Za-z])", re.I)
LEADING_DETERMINER_RE = re.compile(r"(?<![\w-])" + DETERMINER + r"\s+\Z", re.I)


class MarkupEnds:
    """The end of the markup run (MARKUP_RE) from any offset of one text. The run is walked token by token
    and every offset walked is remembered, so a run that holds many clause or sentence starts ("1) 1) 1)")
    is read once, not once per start."""

    def __init__(self, text):
        self.text, self.memo = text, dict()

    def __call__(self, position):
        walked = []
        while position not in self.memo:
            found = MARKUP_TOKEN_RE.match(self.text, position)
            if not found:
                self.memo[position] = position
                break
            walked.append(position)
            position = found.end()
        end = self.memo[position]
        for offset in walked:
            self.memo[offset] = end
        return end


def sentences(text):
    """[(offset, sentence)] of a paragraph's text (newlines already turned into spaces)."""
    out, start = [], 0
    for found in SENTENCE_BREAK_RE.finditer(text):
        out.append((start, text[start:found.start()]))
        start = found.end()
    out.append((start, text[start:]))
    return [(offset, sentence) for offset, sentence in out if sentence.strip()]


def noun_ranges(sentence):
    """Sorted, disjoint [start, end) ranges of noun uses, which open no clause: a parenthesis holding one
    word or a list of single words (both parentheses included), and the part after the first word of an
    `or`-closed list of at least three single words that ends the clause or, led by a determiner, comes
    before a word that is not an object."""
    ranges = [found.span() for found in NOUN_PARENTHESIS_RE.finditer(sentence)]
    for run in WORD_RUN_RE.finditer(sentence):
        tail = NOUN_LIST_TAIL_RE.match(sentence, run.end())
        if not tail and LEADING_DETERMINER_RE.search(sentence, max(0, run.start() - 24), run.start()):
            tail = LED_LIST_TAIL_RE.match(sentence, run.end())
        if tail:
            ranges.append((NOUN_WORD_RE.match(sentence, run.start()).end(), tail.end()))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def code_spans(sentence):
    """Sorted, disjoint [start, end) code spans: a backtick run closed by the next run of the same length."""
    runs = [found.span() for found in BACKTICKS_RE.finditer(sentence)]
    by_length = dict()
    for index, (start, end) in enumerate(runs):
        by_length.setdefault(end - start, []).append(index)
    spans, index = [], 0
    while index < len(runs):
        start, end = runs[index]
        same = by_length[end - start]
        later = bisect.bisect_right(same, index)
        if later < len(same):
            spans.append((start, runs[same[later]][1]))
            index = same[later] + 1
        else:
            index += 1
    return spans


class Sentence:
    """One sentence with its clause starts and breaks, code spans, negations and hard breaks, each found
    once, so every rule runs in about linear time."""

    def __init__(self, text):
        self.text = text
        nouns = noun_ranges(text)
        noun_starts = [start for start, _ in nouns]

        def noun_use(position):
            index = bisect.bisect_right(noun_starts, position) - 1
            return index >= 0 and position < nouns[index][1]

        # a break inside a noun use ("(execute)", "lint, gate, build or self-test") opens no clause
        found = [match for match in CLAUSE_BREAK_RE.finditer(text) if not noun_use(match.start())]
        self.breaks = [match.start() for match in found]
        self.markup = markup = MarkupEnds(text)
        starts = set()
        for position in [0] + [match.end() for match in found]:
            position = markup(position)
            starts.add(position)
            starts.add(LEAD_IN_RE.match(text, position).end())
        # a noun parenthesis that itself opens a clause ("(Optional) Run the suite") is followed by one
        for aside in NOUN_PARENTHESIS_RE.finditer(text):
            if aside.start() in starts:
                position = markup(aside.end())
                starts.add(position)
                starts.add(LEAD_IN_RE.match(text, position).end())
        self.starts = sorted(starts)
        self.spans = code_spans(text)
        self.span_starts = [start for start, _ in self.spans]
        self.negations = [match.span() for match in NEGATION_RE.finditer(text)]
        self.negation_ends = [end for _, end in self.negations]
        hard = [match.start() for match in HARD_BREAK_RE.finditer(text)]
        clause = 0   # the start of the current clause; `and` and `so` do not open a new one here
        for match in found:
            if match.group(0).startswith(","):
                opening = LEAD_IN_RE.match(text, markup(clause)).end()
                if SUBORDINATE_RE.match(text, opening):
                    hard.append(match.start())
            if match.group(0)[:1] in (",", ":", "(", ")") or match.group(0).lower().startswith(("then", "but")):
                clause = match.end()
        self.hard = sorted(hard)

    def in_span(self, position):
        index = bisect.bisect_right(self.span_starts, position) - 1
        return index >= 0 and position < self.spans[index][1]

    def negated(self, start, end):
        """Whether the word at [start, end) is negated: the nearest negation ending at most NEGATION_WINDOW
        characters before it with no hard break between, or `nothing` right after it."""
        index = bisect.bisect_right(self.negation_ends, start) - 1
        if index >= 0:
            neg_end = self.negations[index][1]
            if start - neg_end <= NEGATION_WINDOW:
                between = bisect.bisect_left(self.hard, neg_end)
                if between >= len(self.hard) or self.hard[between] >= start:
                    return True
        return bool(NEGATED_AFTER_RE.match(self.text, end))

    def clause_end(self, position):
        """The offset of the first clause break after `position`, or the sentence's end."""
        index = bisect.bisect_right(self.breaks, position)
        return self.breaks[index] if index < len(self.breaks) else len(self.text)


# ---------- scope ----------

# each whitespace run is read by one quantifier (a separator's trailing space is not read twice)
ITEM_LIST = (r"Q?\d{1,3}(?:\s*(?:,\s*(?:(?:and|or)\s+)?|-\s*|\b(?:and|or|to|through)\s+)Q?\d{1,3})*+")
ITEM_TOKEN_RE = re.compile(r"\d{1,3}|-|\bto\b|\bthrough\b", re.I)
ONLY_RE = re.compile(r"answer\s+(?:only\s+)?(?:items?|questions?)\s+(" + ITEM_LIST + r")(\s+only)?", re.I)
ANSWER_ONLY_RE = re.compile(r"answer\s+only\s+(?:items?|questions?)\s+(" + ITEM_LIST + ")", re.I)
EXCLUDE_RE = re.compile(r"(?:(?:do\s+not|don't|never)\s+(?:attempt|answer)|skip)\s+(?:items?|questions?)\s+("
                        + ITEM_LIST + ")", re.I)
LEG_RE = re.compile(r"(?:(?:for\s+)?the\s+)?([A-Za-z][A-Za-z0-9-]*)\s+leg(?:\*\*|__)?\s*:", re.I)
LEG_HEADING_RE = re.compile(r" {0,3}#{1,6}\s+(?:the\s+)?([A-Za-z][A-Za-z0-9-]*)\s+leg\b", re.I)
TAIL_MARKER_RE = re.compile(r"\((?:for\s+)?(?:the\s+)?([A-Za-z][A-Za-z0-9-]*)\s+(?:leg|only)\)", re.I)
LINE_END_RE = re.compile(r"[\s.,:;!?*_]*\Z")
ADDRESS_RE = re.compile(r"(?:(?:all|every|each|both)\s+(?:legs?|famil(?:y|ies)|workers?)"
                        r"|([A-Za-z][A-Za-z0-9-]*)(?:\s+leg)?)(?:\*\*|__)?\s*:", re.I)


def parse_item_list(spec):
    """The item numbers of "1, 2 and 4", "1-3", "Q1 to Q3"; a backwards range is CannotEvaluate."""
    numbers = set()
    tokens = ITEM_TOKEN_RE.findall(spec)
    index = 0
    while index < len(tokens):
        first = int(tokens[index])
        if index + 2 < len(tokens) and not tokens[index + 1].isdigit():
            last = int(tokens[index + 2])
            if last < first:
                raise CannotEvaluate("scope: the item range {}-{} runs backwards".format(first, last))
            numbers.update(range(first, last + 1))
            index += 3
        else:
            numbers.add(first)
            index += 1
    return numbers


def scope_directives(text):
    """(kept, dropped): the item numbers the linted family's own scope text keeps (None: every item) and
    the ones it drops. Only a directive at a clause start, outside a code span, counts."""
    kept, dropped = None, set()
    for _, raw in sentences(text.replace("\n", " ")):
        sentence = Sentence(raw)
        for start in sentence.starts:
            if sentence.in_span(start):
                continue
            found = ONLY_RE.match(raw, start)
            if found and found.group(2):
                kept = (kept or set()) | parse_item_list(found.group(1))
            found = ANSWER_ONLY_RE.match(raw, start)
            if found:
                kept = (kept or set()) | parse_item_list(found.group(1))
            found = EXCLUDE_RE.match(raw, start)
            if found:
                dropped |= parse_item_list(found.group(1))
    return kept, dropped


def leg_spans(blocks, families):
    """[(block index, start offset, family, extent)] for every operative family marker: a heading marker
    (extent "section", offset 0), or a `<family> leg:` marker at the start of a line once markup is
    skipped (extent "items" when it opens a paragraph that ends with a colon, "" otherwise). A marker
    in a code span, in quotes, or mid-line is not operative."""
    spans = []
    for index, block in enumerate(blocks):
        if block.kind != "prose":
            continue
        text = block.text()
        if block.heading:
            found = LEG_HEADING_RE.match(text)
            if found and found.group(1).lower() in families:
                spans.append((index, 0, found.group(1).lower(), "section"))
            continue
        offset, colon = 0, text.rstrip().endswith(":")
        for number, (_, line) in enumerate(block.lines):
            found = LEG_RE.match(line, MarkupEnds(line)(0))
            if found and found.group(1).lower() in families:
                spans.append((index, offset + found.start(), found.group(1).lower(),
                              "items" if number == 0 and colon else ""))
            offset += len(line) + 1
    return spans


def tail_marker_family(text, families):
    """The family an operative `(<family> leg)` or `(<family> only)` marker names (at the start or the end
    of a line, outside a code span), or None."""
    spans, offset = code_spans(text), 0   # a code span can run across the item's lines
    span_starts = [start for start, _ in spans]
    for line in text.split("\n"):
        # only the line's last marker can end it: a later marker's parenthesis is not line-end text
        last = None
        for last in TAIL_MARKER_RE.finditer(line):
            pass
        candidates = [last] if last and LINE_END_RE.match(line, last.end()) else []
        at_start = TAIL_MARKER_RE.match(line, MarkupEnds(line)(0))
        if at_start:
            candidates.append(at_start)
        for found in candidates:
            index = bisect.bisect_right(span_starts, offset + found.start()) - 1
            if index >= 0 and offset + found.start() < spans[index][1]:
                continue
            if found.group(1).lower() in families:
                return found.group(1).lower()
        offset += len(line) + 1
    return None


def _addresses(text, families):
    """[(offset, family, or None for every leg)] for each sentence of a paragraph that opens by addressing
    a family or every leg."""
    flat, out = text.replace("\n", " "), []
    markup = MarkupEnds(flat)   # a markup run can hold many sentence starts ("Q1. Q1. Q1.")
    for offset, _ in sentences(flat):
        found = ADDRESS_RE.match(flat, markup(offset))
        if found and (found.group(1) is None or found.group(1).lower() in families):
            out.append((found.start(), found.group(1).lower() if found.group(1) else None))
    return out


def apply_scope(blocks, family, families):
    """The blocks addressed to the linted family: text another family claims masked or dropped, and the
    numbered items the family's own scope text excludes dropped."""
    for block in blocks:
        block.masked = []
    spans = leg_spans(blocks, families)
    by_block = dict()
    for span in spans:
        by_block.setdefault(span[0], []).append(span)
    own_text, claimed = [], dict()   # claimed: block index -> the family that claims the whole block
    for index, block_spans in by_block.items():
        block = blocks[index]
        if block_spans[0][3] == "section":
            name = block_spans[0][2]
            claimed[index] = name
            for later in range(index + 1, len(blocks)):
                if blocks[later].heading and blocks[later].heading <= block.heading:
                    break
                claimed[later] = name
            continue
        text = block.text()
        addresses = _addresses(text, families)
        positions = [address for address, _ in addresses]
        for position, (_, start, name, extent) in enumerate(block_spans):
            # the next marker, or a sentence addressing another family or every leg, ends this one's span
            end = block_spans[position + 1][1] if position + 1 < len(block_spans) else len(text)
            for following in range(bisect.bisect_right(positions, start), len(addresses)):
                address, addressed = addresses[following]
                if address >= end:
                    break
                if addressed != name:
                    end = address
                    break
            if name == family:
                own_text.append(text[start:end])
            else:
                block.masked.append((start, end))
            if extent == "items":
                for later in range(index + 1, len(blocks)):
                    after = blocks[later]
                    if later in by_block or not (after.kind == "fence" or after.listed or after.item is not None):
                        break
                    claimed.setdefault(later, name)
    for index, name in claimed.items():
        if name == family:
            own_text.append(blocks[index].text())
    kept, dropped = scope_directives("\n".join(own_text))
    scoped = []
    for index, block in enumerate(blocks):
        owner = claimed.get(index)
        if owner is not None and owner != family:
            continue
        if block.item is not None:
            if (kept is not None and block.item not in kept) or block.item in dropped:
                continue
            marker = tail_marker_family(block.text(), families) if block.kind == "prose" else None
            if marker is not None and marker != family:
                continue
        scoped.append(block)
    return scoped


# ---------- rules ----------

R1_NOUNS = (r"configuration|config|failures?|errors?|steps?|order|conditions?|settings?|names?|logs?|matrix|"
            r"options?|artifacts?|numbers?|ids?|modes?|phases?|stages?|types?|results?|status|outputs?|"
            r"directory|cache|environment|variables?|instructions?|notes?|documentation|section|systems?|"
            r"timeouts?|time|times|history|metadata|rules?|policy|policies|reports?|pipeline|definitions?")
R1_VERB_RE = re.compile(r"(?:re-?run|run|re-?execute|execute|invoke|launch|re-?build|build|compile|install|"
                        r"reproduce|benchmark|profile|measure|inject|trigger|fuzz|bisect|clone|deploy|"
                        r"check\s+out(?=\s+(?:(?:the|a|an|that|this)\s+)?(?:branch|commit|revision|tag|ref|"
                        r"PR|pull|worktree|sha|parent|base|head|main|master|[0-9a-f]{7,40}\b|`))|"
                        r"time(?=\s+(?:the|each|every|it|how)\b)|observe)\b"
                        r"(?!\s*:|-|[./]\w|\s+(?:is|are|was|were|" + R1_NOUNS + r")\b)", re.I)
R1_GERUND_RE = re.compile(r"(?:try|start\s+by|begin\s+by)\s+(?:re-?running|running|re-?executing|executing|"
                          r"invoking|launching|re-?building|building|compiling|installing|reproducing|"
                          r"benchmarking|profiling|measuring|injecting|triggering|fuzzing|bisecting|cloning|"
                          r"deploying)\b", re.I)
R1_PASSIVE_RE = re.compile(r"\b(?:must|needs?\s+to|has\s+to|have\s+to)\s+be\s+(?:re-?)?(?:run|executed|built|"
                           r"compiled|installed|reproduced|benchmarked|profiled|measured|launched|invoked|"
                           r"deployed)\b", re.I)
SHELL_TAGS = frozenset(("bash", "sh", "shell", "console", "zsh", "fish", "ksh", "dash", "shell-session",
                        "shellsession", "sh-session", "terminal", "powershell", "pwsh", "ps1", "cmd", "bat",
                        "batch"))
EXEC_FENCE_TAGS = SHELL_TAGS | frozenset(("pycon", "python-console", "python-repl", "ipython", "repl"))
PYTHON_TAGS = frozenset(("python", "python3", "py", "py3"))
RUN_PROMPT_RE = re.compile(r"\s*(?:>>>|\$|In\s*\[\d*\]:)(?:\s|\Z)")
PLAIN_TAGS = frozenset(("", "text", "txt", "plain", "plaintext", "none", "code"))
SHELL_FIRST_LINE_RE = re.compile(r"\s*(?:\$\s|(?:sudo\s+)?(?:[A-Za-z_]\w*=\S*\s+)*+(?:(?:[\w.~-]*/)*+(?:git|"
                                 r"python(?:3(?:\.\d+)?)?|pytest|cd|bash|sh|zsh|npm|npx|pip3?|uv|cargo|tox|nox|"
                                 r"docker|curl|wget|chmod|mkdir|rm|ls|grep|make|go)(?=\s|\Z)|(?:\./|tools/)\S))")
REFERENCE_INTRO_RE = re.compile(r"\b(?:(?:do\s+not|don['’]t|never)\s+(?:run|execute)\s+(?:this|it|these|them|"
                                r"the\s+following|(?:the\s+)?(?:block|fence|commands?|snippet)\s+below)\b"
                                r"|not\s+to\s+be\s+(?:run|executed)\b)", re.I)
# Wording that reverses or qualifies a request ("ignore the instruction", "but also on the branch"), or names an
# exception or a condition: a not-to-be-run introduction holding one outside a code span introduces nothing.
REVERSAL_WORDS = (r"ignore|disregard|override|instead|otherwise|rather|actually|anyway|regardless|but|then|now|"
                  r"also|again|afterwards?|later|yourself|manually|directly|live")
EXCEPTION_RE = re.compile(r"(?<![\w./-])(?:" + REVERSAL_WORDS + r"|except|excepting|only|other\s+than|apart\s+from|"
                          r"unless|until|before|after|first|without|more\s+than|alone|once|twice|(?<!even\s)if|when|"
                          r"whenever|where)(?![\w-]|[./]\w)", re.I)
# an execution verb anywhere in a sentence; a not-to-be-run introduction holds at most one
EXEC_WORD_RE = re.compile(r"(?<![\w./-])(?:re-?run|run|runs|running|ran|re-?execute|execute|executes|executing|"
                          r"invoke|invokes|invoking|call|calls|calling|use|uses|using|try|tries|trying|launch|"
                          r"launches|launching)(?![\w-]|[./]\w)", re.I)
GIT_CONFIG = (r"config(?!\s+(?:files?|keys?|values?|settings?|options?|entr(?:y|ies)|surfaces?|variables?|"
              r"sections?|scopes?|layers?)\b)")   # "the git config file" is a noun
GIT_SUBCOMMANDS = (r"add|am|annotate|apply|archive|bisect|blame|branch|cat-file|check-attr|check-ignore|checkout|"
                   r"cherry|cherry-pick|clean|clone|commit|" + GIT_CONFIG + r"|count-objects|describe|diff|diff-tree|"
                   r"fetch|for-each-ref|format-patch|fsck|gc|grep|init|log|ls-files|ls-remote|ls-tree|merge|"
                   r"merge-base|merge-tree|mv|name-rev|notes|pull|push|range-diff|rebase|reflog|remote|reset|"
                   r"restore|rev-list|rev-parse|revert|rm|shortlog|show|show-branch|show-ref|stash|status|submodule|"
                   r"switch|symbolic-ref|tag|var|verify-commit|verify-tag|whatchanged|worktree")
# git or python (optionally behind a directory path) as a command word, at a token start, case-sensitive
COMMAND_WORD_RE = re.compile(r"(?<![\w./~-])(?:[\w.~-]*/)*+(?:(git)|python(?:3(?:\.\d+)?)?)(?=\s)")
# a global git option's value: one shell word, its "..." and '...' spans closed on the line (which may hold a space)
# and its other characters but whitespace, glued together
GIT_OPTION_VALUE = r"""(?:"[^"\n]*+"|'[^'\n]*+'|\S)++"""
# one global git option: -C or -c and a value, a long option that takes a separate value, or any other option
GIT_OPTION_RE = re.compile(r"\s+(?:-[Cc]\s+" + GIT_OPTION_VALUE + r"|--(?:git-dir|work-tree|namespace|config-env|"
                           r"attr-source)\s+" + GIT_OPTION_VALUE + r"|--?[A-Za-z][\w-]*(?:="
                           + GIT_OPTION_VALUE + r")?)")
GIT_SUBCOMMAND_RE = re.compile(r"\s+(?:" + GIT_SUBCOMMANDS + r")(?![\w-])")
PY_FLAG = r"[A-VYZabd-lnoq-z]"   # a python flag letter that takes no value: every letter but c, m, W and X
# one python option: -W or -X with its value (attached or the next word), a cluster of value-less flags, or a
# long option; -c and -m end the options and are the target, so the option run never consumes them
PYTHON_OPTION_RE = re.compile(r"\s+(?:-" + PY_FLAG + r"*[WX](?:\S+|\s+\S+)|-" + PY_FLAG + r"+(?=\s)"
                              r"|--[a-z][\w-]*(?=\s))")
PYTHON_TARGET_RE = re.compile(r"\s+(?:-" + PY_FLAG + r"*[cm]|[\w./-]+\.py\b)")
# the other command-line forms, each anchored at a token start
COMMAND_FORMS = (
    r"(?<![\w./-])[\w./-]+\.(?:py|sh)\s+--?[A-Za-z][\w-]*",
    r"(?<![\w./-])\./[\w./-]+\.(?:sh|py)\b",
    r"(?<![\w./-])(?:bash|sh|zsh)\s+(?:-c\b|[\w./-]+\.sh\b)",
    r"(?<![\w./-])pytest\s+(?:-|[\w./-]+\.py\b|tests?/)",
)
COMMAND_RE = re.compile("|".join(COMMAND_FORMS))
# a code span that opens with one of these tools and an argument (matched at the span's opening backticks)
SPAN_COMMAND_RE = re.compile(r"`+\s*(?:\$\s+)?(?:make|pytest|tox|nox|npm|npx|cargo|go|uv|pip3?|docker)\s+[^`\s]")
# The two closed shapes that demote a command line to INFO. Each is matched at the sentence's start once markup is
# skipped and ends at the command line: PROHIBITED ("Do not run" or "Never run") and PAST REPORT ("CI failed on",
# "The check failed on" or "The job failed on").
CLOSED_PROHIBITION_RE = re.compile(r"(?:[Dd]o\s+not|[Nn]ever)\s+run\s+")
CLOSED_PAST_RE = re.compile(r"(?:CI|[Tt]he\s+(?:check|job))\s+failed\s+on\s+")
# after a code span that holds the command line: the stop and nothing else
CLOSED_SPAN_END_RE = re.compile(r"\.\s*\Z")
# after a prose command line: only argument tokens (an option, a number, or a token holding one of these marks),
# then the stop; "git log and report it" is not a command line followed by arguments. No regular expression reads
# the tail: it is split on whitespace once and each token is classified by argument_token.
ARGUMENT_MARKS = frozenset("/:=.@~^<")
# Another sentence of the same void group that asks for anything voids its closed shapes: a listed request word
# outside a code span, a question, or a hit of one of these advisory rules.
VOID_WORD_RE = re.compile(r"(?<![\w./-])(?:confirm\w*|check\w*|verif\w+|see|sees|seeing|try|tries|tried|trying|"
                          r"retry|retries|retried|retrying|report\w*|show\w*|re-?run\w*|run|runs|running|"
                          r"perform\w*|do|does|did|doing|execut\w+|invok\w+|launch\w*|call|calls|calling|use|uses|"
                          r"using|repeat\w*|reproduc\w+|paste|pastes|pasted|pasting|ignore\w*|disregard\w*|"
                          r"override\w*)(?![\w-]|[./]\w)", re.I)
# Words that ask at a clause start ("Test it locally.", "Fix it and give me the new result.") but are common nouns
# elsewhere ("the test", "the fix"): they void a closed shape only at a clause start, outside a code span.
VOID_START_RE = re.compile(r"(?:re-?)?(?:test|tests|tested|testing)(?![\w-]|[./]\w)"
                           r"|(?:fix|fixes|fixed|fixing)(?![\w-]|[./]\w)", re.I)
ADVISORY_VOID_RULES = frozenset(("R1", "R5", "R6", "R7"))
R5_VERB_RE = re.compile(r"(?:report|paste|give|record|capture|attach|print|state|show|share|post|count|"
                        r"tell\s+(?:me|us))\b(?!\s+(?:statements?|calls?|functions?|helpers?|methods?)\b)", re.I)
R5_RESULT_RE = re.compile(r"\b(?:exit\s+(?:codes?|status(?:es)?)|return\s*codes?|returncodes?|"
                          r"(?:last|final)\s+(?:\d+\s+)?lines|lines\s+of\s+(?:the\s+|its\s+)?output|"
                          r"(?:full|complete|raw|whole|entire|console|terminal|command|test)\s+output|"
                          r"output\s+of\s+(?:running|the\s+(?:command|run|self-test|tests?|suite|check|tool|"
                          r"script))|"
                          r"(?:stdout|stderr)(?!\s+(?:handling|handler|stream|buffer|writes?|redirect\w*|"
                          r"parameter|argument|variable|usage|path)\b)|counts?|seconds|"
                          r"wall[\s-]?clock|elapsed|timings?|run\s*times?)\b"
                          r"|\boutput\s+of\s+`", re.I)
STATIC_SOURCE_RE = re.compile(r"\b(?:docstring|source|module|code|comments?|README|documentation|docs|diff|patch)"
                              r"\s+(?:documents|sets|defines|declares|lists|states|says|uses|describes|names|"
                              r"specifies)\b"
                              r"|\b(?:in|from)\s+the\s+(?:source|docstring|module|code|diff|patch|README|"
                              r"documentation|docs)\b(?!\s+(?:tree|checkout|directory))", re.I)
R6_AUX_RE = re.compile(r"(?:does|do|did|is|are|was|were|will|would|can|could)\b", re.I)
R6_CONFIRM_RE = re.compile(r"(?:confirm|verify|check|ensure|show|prove|establish|make\s+sure)\b"
                           r"(?:\s+(?:that|whether|if)\b)?", re.I)
R6_OUTCOME_RE = re.compile(r"\b(?:pass(?:es)?(?=\s*(?:[?.,;)]|\Z|\b(?:and|or|on|at|under|with|without|when|"
                           r"after|before|in|now|again|cleanly|locally|still|both)\b))"
                           r"|succeeds?\b|exits?\s+(?:with\s+)?(?:(?:exit\s+)?(?:code|status)\s+)?\d+\b"
                           r"|fails?\s+without\b|fails?\s+(?:on|at|against|with|under)\s+(?:the\s+)?(?:old|parent|"
                           r"base|previous|unfixed|original|pre-fix|prior)\b"
                           r"|turns?\s+red\b|go(?:es)?\s+green\b|stays?\s+green\b"
                           r"|(?:green|red)(?=\s*(?:[?.]|\Z|\b(?:on|at|in|under|now|again|locally)\b)))", re.I)
R6_RELATIVE_RE = re.compile(r"(?:\bthat|\bwhich|\bwho|\()\s*\Z", re.I)
R6_SUBJECT_RE = re.compile(r"\b(?:tests?|self-tests?|gates?|checks?|suites?|CI)\b|--(?:check|self-test)\b"
                           r"|(?<![\w./-])[\w./-]+\.(?:py|sh)\b", re.I)
NAMED_TOOL_RE = re.compile(r"(?<![\w./-])[\w./-]+\.(?:py|sh)\b|--(?:check|self-test)\b")
QUESTION_END_RE = re.compile(r"\?[\s*_\"')\]]*\Z")
INJECT_RE = re.compile(r"\binject", re.I)
R7_THE = r"(?:(?:the|this|that|a|an|each|every|its|their|your|two|both|these|those)\s+)*+"
R7_FACT_RE = re.compile(
    r"\b(?:commit|revision)(?:'s|\u2019s|s')\s+(?:parents?|SHAs?|hash(?:es)?|ids?|identity)\b"
    r"|\bparents?\s+(?:of|for)\s+" + R7_THE + r"(?:[\w-]+\s+)?(?:commits?|revisions?|merges?|HEAD)\b"
    r"|\bparent\s+(?:commits?|revisions?|SHAs?|hash(?:es)?)\b"
    r"|\b(?:in\s+)?(?:which|what)\s+(?:commits?|revisions?|merge)\s+(?:(?:first|last|originally|actually)\s+)?"
    r"(?:added|introduced|removed|deleted|changed|touched|modified|fixed|broke|landed|merged|reverted|created|"
    r"made|moved|renamed|is|was|holds|contains|did|does)\b"
    r"|\bin\s+(?:which|what)\s+(?:commits?|revisions?)\b"
    r"|\b(?:SHAs?|hash(?:es)?|ids?|identity)\s+of\s+" + R7_THE
    + r"(?:[\w-]+\s+)?(?:commits?|revisions?|merges?|HEAD|tip)\b"
    r"|\bdiff\s+(?:between|of)\s+" + R7_THE + r"(?:[\w-]+\s+)?(?:commits|revisions|merges|[0-9a-f]{7,40}\b)"
    r"|\b(?:what|which\s+[\w-]+)\s+changed\s+between\b"
    r"|\bchanges?\s+between\s+" + R7_THE + r"(?:commits|revisions|[0-9a-f]{7,40}\b)"
    r"|\bchanged\s+lines\b|\blines\s+(?:that\s+)?(?:were\s+)?changed\b"
    r"|\blines\s+(?:that\s+)?(?:were\s+)?(?:added|removed|deleted|modified|touched)\s+(?:by|in|between|since)\s+"
    + R7_THE + r"(?:[\w-]+\s+)?(?:commits?|revisions?|merges?|diff|change|patch|PR|pull\s+request)\b"
    r"|\b(?:observed|actual)\s+(?:exit\s+(?:codes?|status(?:es)?)|return\s*codes?|returncodes?)\b"
    r"|\b(?:exit\s+(?:codes?|status(?:es)?)|return\s*codes?|returncodes?)\s+(?:of|from)\s+" + R7_THE
    + r"(?:runs?|running|invocations?|executions?|command\s+you\s+ran|(?:self-)?test\s+runs?)\b"
    r"|\btest\s+counts?\b"
    r"|\bhow\s+many\s+tests?\s+(?:pass|passed|fail|failed|ran|were\s+run)\b"
    r"|\bnumber\s+of\s+(?:passing|failing|passed|failed)\s+tests\b"
    r"|\bnumber\s+of\s+tests\s+(?:that\s+)?(?:pass|passed|fail|failed|ran)\b", re.I)
R7_REQUEST_RE = re.compile(r"(?:quote|find|cite|name|list|give|report|state|identify|tell|show|say|determine|"
                           r"establish|confirm|verify|check(?!\s+out\b)|answer|print|paste|record|write|look\s+up|"
                           r"work\s+out|compute|what|which|who|when|where|how)\b", re.I)


def rule_r1(sentence):
    """[(offset, verb)]: an execution verb, or a try-gerund, at a clause start, outside a code span, not
    negated; and a must-be-run passive in a sentence that is not a question."""
    text, hits = sentence.text, []
    injects = bool(INJECT_RE.search(text))   # once per sentence, not once per verb
    for start in sentence.starts:
        if sentence.in_span(start):
            continue
        found = R1_VERB_RE.match(text, start) or R1_GERUND_RE.match(text, start)
        if not found or sentence.negated(found.start(), found.end()):
            continue
        if found.group(0).lower() == "observe" and not injects:
            continue
        hits.append((found.start(), found.group(0)))
    if not QUESTION_END_RE.search(text):
        for found in R1_PASSIVE_RE.finditer(text):
            if not sentence.in_span(found.start()) and not sentence.negated(found.start(), found.end()):
                hits.append((found.start(), found.group(0)))
    return hits


def r1_applies(block):
    """Whether R1 reads a prose block: a Markdown heading is a title, never a clause start."""
    return not block.heading


def _asks(sentence):
    """Whether a sentence asks for something: a question, or a request verb or wh-word at a clause start."""
    if QUESTION_END_RE.search(sentence.text):
        return True
    return any(R7_REQUEST_RE.match(sentence.text, start) for start in sentence.starts)


def rule_r7(sentence):
    """[(offset, fact)]: a fact only git or a run can establish, outside a code span and not negated, in a
    sentence that asks for something."""
    hits = []
    for found in R7_FACT_RE.finditer(sentence.text):
        if not sentence.in_span(found.start()) and not sentence.negated(found.start(), found.end()):
            hits.append((found.start(), found.group(0)))
    return hits if hits and _asks(sentence) else []


def rule_s1(block):
    """Whether a fence is executable: a shell or console tag, a python tag over a run prompt, or a plain or
    untagged fence opening with a command."""
    if block.tag in EXEC_FENCE_TAGS:
        return True
    first = next((line for _, line in block.lines if line.strip()), None)
    if first is None:
        return False
    if block.tag in PYTHON_TAGS:
        return bool(RUN_PROMPT_RE.match(first))
    return block.tag in PLAIN_TAGS and bool(SHELL_FIRST_LINE_RE.match(first))


def _words(regex, sentence):
    """The matches of `regex` in the sentence outside its code spans, in one pass."""
    return [found for found in regex.finditer(sentence.text) if not sentence.in_span(found.start())]


def reference_intro(block):
    """Whether a prose block's last sentence introduces the fence after it as not to be run: it holds a
    not-to-be-run phrase, no exception or reversal word, and no execution verb but the prohibited one."""
    if block is None or block.kind != "prose":
        return False
    found = sentences(_masked_text(block).replace("\n", " "))
    if not found:
        return False
    last = Sentence(found[-1][1])
    return (bool(REFERENCE_INTRO_RE.search(last.text)) and not _words(EXCEPTION_RE, last)
            and len(_words(EXEC_WORD_RE, last)) <= 1)


def argument_token(token):
    """Whether one whitespace-free token after a prose command line is an argument: an option (it opens with -), a
    number (it opens with a digit), or a token holding / : = . @ ~ ^ or <."""
    return token[:1] == "-" or token[:1].isdecimal() or not ARGUMENT_MARKS.isdisjoint(token)


def arguments_then_stop(text, end):
    """Whether only argument tokens and the stop follow the command line that ends at `end`. The tail is split on
    whitespace once and the token list walked once, so the test is linear in the tail's length. The stop is the
    last character before trailing whitespace and closes the last token ("abc1234:t.py."); the tail must open with
    whitespace (the command line ends at a word's end), and the stop may not stand alone ("git log .")."""
    tail = text[end:].rstrip()
    if not tail.endswith("."):
        return False
    tail = tail[:-1]
    if tail and (not tail[0].isspace() or tail[-1].isspace()):
        return False
    return all(argument_token(token) for token in tail.split())


def closed_shape_head(sentence):
    """Where a closed shape must open: the sentence's start once markup (a list marker, emphasis) is skipped."""
    return sentence.markup(0)


class Demotions:
    """The closed shape of one sentence, found once: whether the whole sentence is a prohibition or a past report
    of one command line. Only a command line that opens right after the shape's words can be demoted, and its tail
    is walked once per command line end."""

    def __init__(self, sentence):
        self.text, self.kind, self.span, self.prose, self.tails = sentence.text, None, None, None, dict()
        head = closed_shape_head(sentence)
        for kind, regex in (("prohibited", CLOSED_PROHIBITION_RE), ("past report", CLOSED_PAST_RE)):
            found = regex.match(self.text, head)
            if found:
                break
        else:
            return
        self.kind = kind
        index = bisect.bisect_left(sentence.span_starts, found.end())
        if index < len(sentence.spans) and sentence.span_starts[index] == found.end():
            span = sentence.spans[index]
            # the code span is the command line: only the stop may follow it
            self.span = span if CLOSED_SPAN_END_RE.match(self.text, span[1]) else (-1, -1)
        else:
            self.prose = found.end()

    def __call__(self, start, end):
        """The demotion of the command line at [start, end) ("prohibited" or "past report"), or None."""
        if self.kind is None:
            return None
        if self.span is not None:
            return self.kind if self.span[0] <= start < self.span[1] else None
        # a prose command line opens right after the shape's words and only arguments and the stop follow it
        if start != self.prose:
            return None
        if end not in self.tails:
            self.tails[end] = arguments_then_stop(self.text, end)
        return self.kind if self.tails[end] else None


def _option_end(text, position, option_re, memo):
    """The offset after the run of options `option_re` matches from `position`, with no bound on their number.
    Every offset walked is remembered in `memo`, so the option runs of all the command words in a sentence
    cost one pass in total, however many words share one run ("git -c git -c ... show")."""
    walked = []
    while position not in memo:
        found = option_re.match(text, position)
        if not found:
            memo[position] = position
            break
        walked.append(position)
        position = found.end()
    end = memo[position]
    for offset in walked:
        memo[offset] = end
    return end


def option_commands(text):
    """[(start, end)] of the command lines that open with git (global options, then a git subcommand) or
    python (options, then -c, -m or a .py path)."""
    found, memos = [], (dict(), dict())
    for word in COMMAND_WORD_RE.finditer(text):
        git = word.group(1) is not None
        end = _option_end(text, word.end(), GIT_OPTION_RE if git else PYTHON_OPTION_RE, memos[0 if git else 1])
        target = (GIT_SUBCOMMAND_RE if git else PYTHON_TARGET_RE).match(text, end)
        if target:
            found.append((word.start(), target.end()))
    return found


def rule_commands(sentence):
    """[(offset, command line, demotion or None)]: every command line in the sentence, in prose and in code
    spans alike."""
    text = sentence.text
    found = option_commands(text) + [match.span() for match in COMMAND_RE.finditer(text)]
    found.sort()
    starts = [start for start, _ in found]
    for start, end in sentence.spans:
        index = bisect.bisect_left(starts, start)
        if (index == len(starts) or starts[index] >= end) and SPAN_COMMAND_RE.match(text, start):
            found.append((start, end))
    if not found:
        return []
    found.sort()
    demotion = Demotions(sentence)
    return [(start, text[start:min(end, start + EXCERPT_CHARS // 2)].strip("`").strip(), demotion(start, end))
            for start, end in found]


def _last_start(regex, text):
    """The last offset at which `regex` matches in `text`, or -1. Each search resumes one character after the
    previous match's start, so every offset is tried once and an overlapping match is not missed."""
    last, position = -1, 0
    while True:
        found = regex.search(text, position)
        if not found:
            return last
        last, position = found.start(), found.start() + 1


def rule_r5(sentence):
    """[(offset, verb)]: a reporting verb at a clause start, not negated, then an execution result, in a
    sentence that names no static source after the verb."""
    text, hits, last = sentence.text, [], None
    for start in sentence.starts:
        found = R5_VERB_RE.match(text, start)
        if not found or sentence.negated(found.start(), found.end()):
            continue
        if last is None:   # the last result and the last static source, found once per sentence
            last = _last_start(R5_RESULT_RE, text), _last_start(STATIC_SOURCE_RE, text)
        if last[0] >= found.end() and last[1] < found.end():
            hits.append((found.start(), found.group(0)))
    return hits


def rule_r6(sentence):
    """[(offset, names a tool)]: an outcome question (ending with `?`), or a confirm-that sentence, about a
    test, a gate, or a tool."""
    text, hits = sentence.text, []
    question = bool(QUESTION_END_RE.search(text))
    outcomes = list(R6_OUTCOME_RE.finditer(text))
    outcome_starts = [found.start() for found in outcomes]
    for start in sentence.starts:
        opener = (R6_AUX_RE.match(text, start) if question else None) or R6_CONFIRM_RE.match(text, start)
        if not opener:
            continue
        index = bisect.bisect_left(outcome_starts, opener.end())
        if index >= len(outcomes):
            continue
        if outcomes[index].start() - opener.end() > R6_SUBJECT_MAX:   # measured before slicing
            continue
        subject = text[opener.end():outcomes[index].start()]
        if R6_RELATIVE_RE.search(subject):
            continue
        if R6_SUBJECT_RE.search(subject):
            hits.append((opener.start(), bool(NAMED_TOOL_RE.search(subject))))
    return hits


# ---------- evaluation ----------

def _excerpt(text):
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS - 3] + "..."


def sentence_asks(sentence, findings, closed):
    """Whether a sentence asks for anything, so that it voids the closed shapes of its void group: a listed request
    word outside a code span (VOID_WORD_RE anywhere, VOID_START_RE at a clause start), a question, or an advisory
    hit (ADVISORY_VOID_RULES). A closed-shape sentence (`closed`) asks nothing: its words are fixed."""
    if closed:
        return False
    return (any(rule in ADVISORY_VOID_RULES for rule, _, _, _, _ in findings)
            or bool(QUESTION_END_RE.search(sentence.text)) or bool(_words(VOID_WORD_RE, sentence))
            or any(VOID_START_RE.match(sentence.text, start) for start in sentence.starts
                   if not sentence.in_span(start)))


def lint_sentence(raw, line, read_r1=True, structural="S3"):
    """(findings, asks) for one sentence: findings [(rule, severity, line, message, excerpt)], its command lines
    under the structural rule `structural` (S2 in a numbered item, S3 outside; None reads none), HARD unless a
    closed shape demotes them, then the ADVISORY rules; `read_r1` is false in a heading. `asks`: whether the
    sentence voids the closed shapes of its void group."""
    sentence = Sentence(raw)
    excerpt = _excerpt(raw)
    findings, demoted = [], []
    if structural is not None:
        where = "in a numbered item" if structural == "S2" else "outside the numbered items"
        for _, command, demotion in rule_commands(sentence):
            demoted.append(demotion is not None)
            if demotion is None:
                findings.append((structural, "HARD", line, "command line {!r} {}".format(command, where), excerpt))
            else:
                findings.append((structural, "INFO", line, "command line {!r} {}, {}: not a request".format(
                    command, where, demotion), excerpt))
    for _, verb in (rule_r1(sentence) if read_r1 else []):
        findings.append(("R1", "ADVISORY", line, "execution request {!r} at a clause start".format(verb), excerpt))
    for _, verb in rule_r5(sentence):
        findings.append(("R5", "ADVISORY", line, "asks to {} an execution result".format(verb.lower()), excerpt))
    for _, fact in rule_r7(sentence):
        findings.append(("R7", "ADVISORY", line, "asks for {!r}, which only git or a run can establish".format(
            fact), excerpt))
    for _, named in rule_r6(sentence):
        findings.append(("R6", "ADVISORY", line,
                         "asks for a test outcome" + (" of a named tool" if named else ""), excerpt))
    return findings, sentence_asks(sentence, findings, bool(demoted) and all(demoted))


def _masked_text(block):
    text = block.text()
    if not block.masked:
        return text
    pieces, position = [], 0
    for start, end in sorted(block.masked):
        start = max(start, position)
        if end > start:
            pieces.extend((text[position:start], " " * (end - start)))
            position = end
    pieces.append(text[position:])
    return "".join(pieces)


def command_rule(block):
    """The blocking rule a command line in a prose block falls under: S2 in a numbered item, S3 outside."""
    return "S2" if block.item is not None else "S3"


def asks_to_run(findings):
    """Whether a block's findings hold an R1 or R5 hit: an execution request or a request for a result. The block
    before a fence that does is not a not-to-be-run introduction."""
    return any(rule in ("R1", "R5") for rule, _, _, _, _ in findings)


def asks_in(block):
    """Whether a prose block's sentences can ask for something and so void a closed shape: a Markdown heading is
    a title, not a request."""
    return not block.heading


def starts_section(block):
    """Whether a block outside the numbered items opens a new void group: a heading does."""
    return bool(block.heading)


def fence_asks(block):
    """Whether a fence's contents, read as prose sentences, ask for anything. A fence belongs to the void group of
    the item or section that holds it, so its contents can void that group's closed shapes; nothing read here is a
    finding."""
    for _, raw in sentences(block.text().replace("\n", " ")):
        if lint_sentence(raw, block.start, True, None)[1]:
            return True
    return False


def void_groups(blocks):
    """[group number] per block: the blocks across which a sentence that asks voids the closed shapes. A top-level
    numbered item is one group with every block that belongs to it (its lazy continuation lines, nested list items
    whatever their numbers, indented paragraphs, fences); the group's identity is the top-level item (Block.top),
    never a nested numeral. Outside the numbered items, the blocks from one heading (or the brief's start, or the
    end of a numbered item) to the next heading or numbered item are one group."""
    groups, group, previous = [], -1, None
    for block in blocks:
        joins = previous is not None and (
            (block.top is not None and block.top == previous.top)
            or (block.top is None and previous.top is None and not starts_section(block)))
        if not joins:
            group += 1
        groups.append(group)
        previous = block
    return groups


def cancel_demotion(finding):
    """A demoted command line HARD again: its void group also asks for something."""
    rule, severity, line, message, excerpt = finding
    if severity != "INFO" or rule not in ("S2", "S3"):
        return finding
    return (rule, "HARD", line, message.replace(": not a request", ", voided: another sentence of the item or "
                                                "section asks for something"), excerpt)


# ---------- preflight: the input paths a brief names ----------

WORKDIR_WORD_RE = re.compile(r"(?<![\w-])(?:workdir|work\s+dir|working\s+directory)(?![\w-])", re.I)
WORKDIR_LEAD_RE = re.compile(r"(?:(?:the|your)\s++)?+(?:workdir|work\s++dir|working\s++directory)(?![\w-])", re.I)
# the optional link between a declaration lead and its path: a colon, or the word is before a space or the line end
DECLARATION_LINK_RE = re.compile(r":|is(?=\s|\Z)", re.I)
# a line whose first word (after markup) is a workdir label is a declaration or cannot be evaluated, never ignored
# (a `_` after it closes emphasis, as in __Workdir__)
WORKDIR_LABEL_RE = re.compile(r"(?:(?:the|your)\s++)?+(?:workdir|work\s++dir|working\s++directory|working\s++tree)"
                              r"(?!-|[^\W_])", re.I)
QUOTED_ABS_PATH_RE = re.compile(r"\"(/[^\"\n]*+)\"|`(/[^`\n]*+)`")
ABS_PATH_RE = re.compile(r"(?<![\w.~/:@%-])/[\w.@+-]++(?:/[\w.@+-]++)*+/?+")
REL_PATH_RE = re.compile(r"(?<![\w.~/:@%-])(?:\.\.?/)?+\.?+[\w@+-][\w.@+-]*+(?:/[\w.@+-]++)++/?+")
FILE_SUFFIX_RE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,11}\Z")
WORD_RE = re.compile(r"\S++")
# a home (~) or variable ($) path: where it leads is not in the text, so it cannot be placed under a workdir
UNPLACED_LEAD_RE = re.compile(r"[~$][\w{}.-]*+/")
# a prohibition before a path on its line: the line forbids the path rather than asking for it
PROHIBITION_RE = re.compile(r"(?<![\w/.-])(?:never|do\s++not|don['\u2019]t|must\s++not|not\s++to)(?![\w/.-])", re.I)
PIN_RE = re.compile(r"(?:REVIEWED\s+HEAD|BRIEF-PIN)[*_]*+\s*+[:=]?+[*_]*+\s*+[`*_(]*+"
                    r"([0-9a-fA-F]{64}|[0-9a-fA-F]{7,40})(?![\w-])")
# a line that opens with a pin label must carry a pin (PIN_RE); a label with anything else cannot be resolved
PIN_LABEL_RE = re.compile(r"(?:REVIEWED\s+HEAD|BRIEF-PIN)(?!-|[^\W_])")
# a LOOK-ALIKE pin line (near_pin) is no pin line, but once its text is folded to NEAR_PIN_FORM and every dash of a
# category in NEAR_PIN_DASHES is read as -, it opens (after markup) with REVIEWED HEAD or BRIEF-PIN in any case, with
# backticks and table pipes skipped around the label, and then a commit id; rule_p3 compares that id with the pin
NEAR_PIN_FORM = "NFKC"
NEAR_PIN_DASHES = frozenset(("Pd",))
NEAR_PIN_RE = re.compile(r"[`|*_\s]*+(?:REVIEWED\s++HEAD|BRIEF-PIN)[`|*_\s]*+[:=]?+[`|*_\s(]*+"
                         r"([0-9a-f]{64}|[0-9a-f]{7,40})(?![\w-])", re.I)
# P6: INPUT declarations, their shapes and their look-alikes are read by the shared parser
# (tools/_input_declarations.py, bound above as read_inputs), the same reading check_verifier_delivery's
# C1 uses, so the two cannot disagree; a stat error in MISSING_ERRNOS is a missing input, any other is
# cannot evaluate
MISSING_ERRNOS = frozenset((errno.ENOENT, errno.ENOTDIR))
# a slash after a letter, a digit or one of these continues a relative path word, so P2 does not judge it
PATH_GLUE = frozenset("._")
# a web address is not a file read; file:// and every other scheme is judged as a path
WEB_URL_RE = re.compile(r"[\"'`*_(\[{<]*+https?://", re.I)
TRAILING_PUNCTUATION = ".,;:)!?\"'`"
HANDBACK_NAMED_RE = re.compile(r"(?<![\w-])hand[- ]?back\s+(?:file|path)(?![\w-])", re.I)
LARGE_OUTPUT_RE = re.compile(r"(?<![\w-])(?:(?:whole|entire|full|complete)\s+(?:file|source|module|diff|patch|log|"
                             r"output|transcript|listing)s?+|(?:unified\s+)?diffs?+|patch(?:es)?+)(?![\w-])", re.I)
DELIVER_RE = re.compile(r"(?<![\w-])(?:return|give|paste|print|send|deliver|hand\s+back|show|produce|include|"
                        r"attach|provide|reply\s+with|quote)(?![\w-])", re.I)
GIT_READ_TIMEOUT = 10
# P5: the git subcommands that reach a ref (a reach), each with the options whose value is a separate word (or
# follows `=`); worktree is a reach only as worktree add
REACH_REFS = frozenset(("clone", "checkout", "switch", "fetch", "pull", "worktree", "reset", "merge", "rebase",
                        "restore", "archive"))
REACH_VALUE_OPTIONS = dict(
    clone=frozenset(("-b", "--branch", "--revision", "-o", "--origin", "-u", "--upload-pack", "-c", "--config", "-j",
                     "--jobs", "--depth", "--reference", "--reference-if-able", "--separate-git-dir",
                     "--shallow-since", "--shallow-exclude", "--template", "--filter", "--server-option",
                     "--bundle-uri", "--ref-format")),
    checkout=frozenset(("-b", "-B", "--orphan", "--conflict", "--pathspec-from-file")),
    switch=frozenset(("-c", "-C", "--create", "--force-create", "--orphan", "--conflict")),
    fetch=frozenset(("--depth", "--deepen", "--shallow-since", "--shallow-exclude", "-j", "--jobs", "--upload-pack",
                     "--refmap", "-o", "--server-option", "--negotiation-tip", "--filter",
                     "--recurse-submodules-default", "--submodule-prefix")),
    worktree=frozenset(("-b", "-B", "--reason")),
    reset=frozenset(("--pathspec-from-file",)),
    merge=frozenset(("-m", "-F", "--file", "-s", "--strategy", "-X", "--strategy-option", "--into-name", "--cleanup")),
    rebase=frozenset(("--onto", "-s", "--strategy", "-X", "--strategy-option", "-x", "--exec", "-C")),
    restore=frozenset(("-s", "--source", "--pathspec-from-file")),
    archive=frozenset(("--format", "-o", "--output", "--prefix", "--remote", "--exec", "--add-file",
                       "--add-virtual-file", "--mtime")),
)
# a clone's option whose value is the commit or ref it checks out, read as written (a value option above)
CLONE_REVISION_OPTIONS = frozenset(("--revision",))
# a rebase's option whose value is the commit it replays onto, and a restore's whose value is the tree it reads
REBASE_ONTO_OPTIONS = frozenset(("--onto",))
RESTORE_SOURCE_OPTIONS = frozenset(("-s", "--source"))
# a merge or rebase with one of these goes on with, or ends, one already started: it reaches no new ref
SEQUENCE_OPTIONS = frozenset(("--abort", "--continue", "--quit", "--skip", "--edit-todo", "--show-current-patch"))
# a switch reads its word as a commit only after one of these; with none, the word names a branch
SWITCH_COMMIT_OPTIONS = frozenset(("-c", "-C", "--create", "--force-create", "-d", "--detach"))
# the reaches whose words after `--` are still their operands (a fetch's remote and refspecs, a worktree add's path
# and commit-ish); a checkout's or reset's words after `--` are paths
OPERANDS_AFTER_DASHDASH = frozenset(("fetch", "worktree"))
# the reaches that fill FETCH_HEAD, which a later reach of FETCH_HEAD inherits
FETCH_REACHES = frozenset(("fetch", "pull"))
# an argument run ends at the first of these (`||` holds `|`), at the next command line or at the line's end
ARGUMENT_STOPS = (";", "&&", "|", "`")
# refs that do not move by their spelling: a commit id (hex_ref), a dispatch ref, HEAD, ORIG_HEAD and @ (alone or
# followed by ~N or ^N steps) and a FETCH_HEAD with no fetch before it (rule_p5 gives a FETCH_HEAD after a fetch or
# pull that reach's ref)
DISPATCH_PREFIX = "refs/dispatch/"
# a commit id peeled to its commit (peeled_ref): no branch or tag can be named so, and git reads a full 40-hex id
# before it as the object itself, so a checkout or switch of <pin>^{commit} reaches the pin even where a local
# branch is named with the pin's hex (a bare <pin> takes that branch)
PEEL_SUFFIX = "^{commit}"
INHERITED_REFS = frozenset(("HEAD", "FETCH_HEAD", "ORIG_HEAD", "@"))
RELATIVE_BASES = ("ORIG_HEAD", "HEAD", "@")
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
# a confirm clause: a line with upper-case HEAD as a word and one of these words gives its commit ids as pins
HEAD_WORD_RE = re.compile(r"(?<![\w-])HEAD(?![\w-])")
CONFIRM_RE = re.compile(r"(?<![\w-])(?:confirm|verify|check|assert|ensure|starts|matches|equals)(?![\w-])", re.I)
# a prose reach: one of these verbs with no git before it, then a branch word within three words (ADVISORY)
PROSE_REACH_RE = re.compile(r"(?<![\w-])(?:clone|check\s++out|switch\s++to|fetch)(?![\w-])", re.I)
PROSE_BRANCH_WORDS = frozenset(("--branch", "-b", "main", "master", "trunk", "develop"))
# the other reaches named with no git word (ADVISORY): gh pr checkout, and a web address whose path names a ref
# after one of WEB_REF_SEGMENTS (refs/heads/ or refs/tags/ and an archive suffix dropped)
GH_CHECKOUT_RE = re.compile(r"(?<![\w./-])gh\s++pr\s++checkout(?![\w-])")
WEB_REF_SEGMENTS = frozenset(("archive", "tree", "blob", "raw"))
ARCHIVE_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tar", ".zip")
# a moving reach on a git command line refuses; one inferred from prose or a web address, and a submodule update
# --remote (which moves submodule content, not the pinned tree), are printed, as every prose rule is
REACH_SEVERITY = dict(git="HARD", prose="ADVISORY", submodule="ADVISORY")
# a prohibition governs only its own object: a prohibition word (outside a code span, not glued to `=`), closing
# emphasis, whitespace and at most one prohibited verb (run, use, execute or follow), then, once OBJECT_MARKUP is
# skipped, one code span (every reach in it), one git command line or one prose reach that opens there
P5_PROHIBITION_WORDS = r"(?:never|do\s++not|don['" + chr(0x2019) + r"]t|must\s++not|not\s++to)"
REACH_PROHIBITION_RE = re.compile(r"(?<![\w/.=-])" + P5_PROHIBITION_WORDS + r"[*_]*+\s++"
                                  r"(?:[*_]*+(?:run|use|execute|follow)[*_]*+\s++)?+", re.I)
OBJECT_MARKUP = "*_\"'"
# a CURE is a closed shape that reads no English (cure_line): its command (cure_command) is git, optionally -C and
# one word, then one of CURE_SUBCOMMANDS, --detach and the pin, or git -C, one word, checkout and the pin; the -C word
# is a closed path (CURE_PATH_RE) and the pin is the one BRIEF-PIN value, of CURE_PIN_DIGITS hex digits, compared
# exactly, bare or followed by PEEL_SUFFIX (cure_pins, cure_matches; pin lines that disagree are CannotEvaluate,
# rule_p3). The command cures only on
# one physical line whose raw text is CURE_CHARACTERS alone (cure_characters, read before format characters are
# removed) and that holds no part of an HTML comment: as the whole line, after at most one list marker or
# CURE_PREFIXES word (in a fence only one of FENCE_PREFIXES), bare or as one code span; or as the end of one code
# span that is the whole rest of a line opening with one of CURE_LEADS and is exactly a closed clone, CURE_CHAIN and
# the command in the clone's directory (clone_chain)
CURE_SUBCOMMANDS = frozenset(("checkout", "switch"))
CURE_PIN_DIGITS = 40
CURE_PREFIXES = frozenset(("$", "then"))   # compared in lower case
FENCE_PREFIXES = frozenset(("$",))   # a fence line is literal text: no list marker and no then opens a cure there
CURE_LEADS = (("Make", "a", "scratch", "clone:"), ("Clone",), ("Run",))
CURE_CHAIN = "&&"
# a cure line's raw characters: printable ASCII, with space or tab between words (no other control character, no
# format character and no other space)
CURE_CHARACTERS = frozenset(chr(code) for code in range(0x20, 0x7F)) | frozenset("\t")
# a -C word or a clone's directory: letters, digits and _ . / -, and <name> placeholders each a whole path segment (at
# the start or after /, and at the end or before /: <scratch>/r, not w<x>), not opening with -; a clone's source may
# also hold : and @ (a URL) and a placeholder anywhere; neither holds a shell metacharacter, a quote, ~ or $
CURE_PATH_RE = re.compile(r"(?!-)(?:[A-Za-z0-9_./-]|(?<![^/])<[A-Za-z0-9_-]++>(?![^/]))++")
CURE_SOURCE_RE = re.compile(r"(?!-)(?:[A-Za-z0-9_./:@-]|<[A-Za-z0-9_-]++>)++")
# the only options the clone of a cure chain may carry: none names a branch, a depth or another ref
CURE_CLONE_OPTIONS = frozenset(("--no-hardlinks", "--no-checkout", "-q", "--quiet"))
HTML_COMMENT_OPEN, HTML_COMMENT_CLOSE = "<!--", "-->"
# the line a refusal tells the author to copy, after the reach (the pin peeled, PEEL_SUFFIX), and the cure shape it
# states
CURE_EXAMPLE = "git -C <dir> checkout --detach {}^{{commit}}"
CURE_SHAPE = ("a cure is a later line that is only `git -C <dir> checkout --detach <pin>^{commit}` (or git checkout or "
              "switch --detach <pin>^{commit}, with or without -C <dir>, or git -C <dir> checkout <pin>^{commit}), "
              "<pin> the brief's one full 40-hex BRIEF-PIN value (a bare <pin> is accepted too, but a branch named "
              "with the pin's hex takes its place in the checkout, so write ^{commit}) and <dir> letters, digits, "
              "_ . / - and <name> placeholders as whole path segments, bare or as one code span, after at most one "
              "list marker, `$ ` or then (in a fence only `$ `); or a line that opens with Make a scratch clone:, "
              "Clone or Run and whose whole rest is one code span holding exactly `git clone <source> <dir> && ` and "
              "that command with -C <dir> (the clone may add --no-hardlinks, --no-checkout, -q or --quiet); a cure "
              "line is printable ASCII with only spaces or tabs between words and is not inside an HTML comment; "
              "nothing else cures")


def _path_token(token):
    """A path match with one sentence stop dropped (never the dot of `/.` or `..`)."""
    if token.endswith(".") and not token.endswith(("/.", "..")):
        return token[:-1]
    return token


def _word_end(word):
    """A path word with the punctuation and markup that close it dropped (quotes, emphasis, brackets, a comma,
    a sentence stop), never the dots of `/.` or `..`."""
    end = len(word)
    while end and (word[end - 1] in "\"'`*_)]}>,;:!?"
                   or (word[end - 1] == "." and not word.endswith(("/.", ".."), 0, end))):
        end -= 1
    return word[:end]


def unplaced(word):
    """Whether a relative path word cannot be placed under a workdir lexically: a home (~) or variable ($)
    path, or a path whose `..` climbs above its own start (posixpath.normpath opens with `..`: the tail of an
    unquoted path with a space, or a path relative to the worker's own directory)."""
    return "/" in word and bool(UNPLACED_LEAD_RE.match(word) or posixpath.normpath(word).split("/")[0] == "..")


MAX_WORD_PATHS = 16   # more judged slashes than this in one word: the word is not judged slash by slash


def line_words(line):
    """[(start, word)] for one line split on whitespace only; a quoted (") or backticked span that closes on
    the line is one word together with anything glued to either end of it."""
    words, index = [], 0
    while index < len(line):
        if line[index].isspace():
            index += 1
            continue
        start = index
        while index < len(line) and not line[index].isspace():
            close = line.find(line[index], index + 1) if line[index] in "\"`" else -1
            index = close + 1 if close > index else index + 1
        words.append((start, line[start:index]))
    return words


def judged_slashes(word):
    """The indexes of the slashes P2 judges in a word: each `/` at the word's start or right after a character
    that is not a letter, a digit or one of PATH_GLUE."""
    return [index for index, character in enumerate(word) if character == "/" and (
        index == 0 or not (word[index - 1].isalnum() or word[index - 1] in PATH_GLUE))]


def path_ends(text):
    """The paths one judged slash gives: `text` (from the slash to the word's end) with trailing sentence
    punctuation (TRAILING_PUNCTUATION) stripped, plus every form along that strip that ends in `/..`, so a stop
    after /w/repo/.. cannot turn it into /w/repo/."""
    ends, end = [], len(text)
    while True:
        if text.endswith("/..", 0, end):
            ends.append(text[:end])
        if not end or text[end - 1] not in TRAILING_PUNCTUATION:
            return ends + [text[:end]]
        end -= 1


def path_words(line):
    """[(start, path, placed)] for the words of one line (line_words) that P2 judges, `start` being the word's
    start. A word that opens (after opening markup) with http:// or https:// is skipped. Every judged slash in a
    word (judged_slashes) gives the text from it to the word's end (path_ends), placed; a word with more than
    MAX_WORD_PATHS judged slashes is given whole and unplaced. A word with no judged slash is given whole and
    unplaced when it cannot be placed (unplaced)."""
    found = []
    for start, word in line_words(line):
        if WEB_URL_RE.match(word):
            continue
        slashes = judged_slashes(word)
        if len(slashes) > MAX_WORD_PATHS:
            found.append((start, word, False))
            continue
        found.extend((start, path, True) for slash in slashes for path in path_ends(word[slash:]))
        lead = len(word) - len(word.lstrip("\"'`*_([{<"))
        if not slashes and unplaced(_word_end(word[lead:])):
            found.append((start, _word_end(word[lead:]), False))
    return found


def near_pin(line):
    """The commit id of a LOOK-ALIKE pin line, else None: the line folded to NEAR_PIN_FORM (NFKC: a fullwidth letter
    or a no-break space is read as its ASCII form), every character of a category in NEAR_PIN_DASHES (a Unicode hyphen
    or dash) read as -, then matched after its markup by NEAR_PIN_RE: REVIEWED HEAD or BRIEF-PIN in any case,
    backticks and table pipes skipped around the label, then a commit of 7 to 40 or 64 hex digits. So Brief-Pin: x,
    BRIEF-PIN with U+2010 for its hyphen, `BRIEF-PIN: x` and | BRIEF-PIN | x | give x. PreflightText reads only the
    lines that are neither pin lines nor bad pins."""
    if not line.isascii():   # NFKC and the dash fold leave ASCII as it is
        line = "".join("-" if unicodedata.category(character) in NEAR_PIN_DASHES else character
                       for character in unicodedata.normalize(NEAR_PIN_FORM, line))
    found = NEAR_PIN_RE.match(line, MarkupEnds(line)(0))
    return found.group(1) if found else None


class PreflightText:
    """The paths a brief names, found once: [(line, path)] for the path words P2 judges (see path_words), split
    into the absolute ones and the ones it cannot place (unplaced), each kept only when no prohibition
    (PROHIBITION_RE) precedes it on its line; for relative paths that name a file (a last component with a suffix
    such as `.py`) or open with `./` or `../`; the declared workdirs (see _declaration_rest) and the
    declarations that name none clearly, with every line that opens with a workdir label (WORKDIR_LABEL_RE) but
    is not a declaration; the pinned revisions (a line that opens, once markup is skipped, with REVIEWED HEAD or
    BRIEF-PIN, plain or in bold, and a commit of 7 to 40 or 64 hex digits; the BRIEF-PIN ones and the REVIEWED HEAD
    ones also apart, for pin agreement (rule_p3) and the P5 cure), the lines that open with such a label but carry
    no such commit (bad_pins) and the commit ids of the other lines that are look-alike pin lines (near_pins, see
    near_pin; for pin agreement); the declared inputs ([(line, path)]) and the malformed declarations and
    look-alike INPUT lines, with the reason (bad_inputs), both read from `raw` by the shared parser
    (tools/_input_declarations.py, read_inputs), the same reading check_verifier_delivery's C1 uses: a
    top-level or list-item paragraph line of the parsed CommonMark document whose rendered, folded text
    begins with INPUT: declares one path, code declares nothing, and a rendered paragraph line, heading
    or table cell opening with the folded token INPUT or INPUTS and a colon (or a cell that is exactly
    the token) is bad; and whether a
    hand-back file is named (the words hand-back file or path and an absolute path on one line). `raw` is the
    brief's text before decode_brief removes format characters, which the P5 cure reads (cure_lines); None stands
    for `text` itself."""

    def __init__(self, text, raw=None):
        self.text = text
        self.raw = text if raw is None else raw
        self.names_workdir = bool(WORKDIR_WORD_RE.search(text))
        self.absolute, self.unplaced, self.relative, self.workdirs, self.pins = [], [], [], [], []
        self.bad_pins = []   # [line] for a pin label with no valid pin after it
        self.brief_pins = []   # [(line, pin)] for the pin lines labelled BRIEF-PIN, whose value a cure names (P5)
        self.reviewed_heads = []   # [(line, pin)] for the pin lines labelled REVIEWED HEAD
        self.near_pins = []   # [(line, commit id)] for the look-alike pin lines (near_pin)
        self.unclear = []   # [(line, reason)] for a declaration whose workdir cannot be read
        # the INPUT declarations P6 checks and the malformed or look-alike INPUT lines, each [(line, ...)],
        # read from the raw text by the shared parser so P6 and C1 cannot disagree; a raw text that
        # parser cannot read is CannotEvaluate, never an empty reading
        try:
            self.inputs, self.bad_inputs = read_inputs(self.raw)
        except _input_declarations.DeclarationError as exc:
            raise CannotEvaluate("brief: {}".format(exc))
        self.handback_named = False
        bare = None   # the line of a bare Workdir (or Workdir:, Workdir is) whose path is on the next line
        for number, line in enumerate(text.split("\n"), 1):
            line = line.rstrip("\r")
            quoted = [match for match in QUOTED_ABS_PATH_RE.finditer(line) if any(
                character.isspace() for character in match.group(1) or match.group(2))]
            inside = lambda start: any(span.start() <= start < span.end() for span in quoted)  # noqa: E731
            words = path_words(line)
            found = [(start, path) for start, path, placed in words if placed]
            negation = PROHIBITION_RE.search(line)
            for start, path, placed in words:
                if not negation or start < negation.end():
                    (self.absolute if placed else self.unplaced).append((number, path))
            for match in REL_PATH_RE.finditer(line):
                path = _path_token(match.group(0))
                if inside(match.start()):
                    continue
                if path.startswith(("./", "../")) or FILE_SUFFIX_RE.search(path.rstrip("/").rsplit("/", 1)[-1]):
                    self.relative.append((number, path))
            start = MarkupEnds(line)(0)
            if bare is not None:
                self._declare(line[start:], number)
            bare, rest = None, _declaration_rest(line, start)
            if rest is None and WORKDIR_LABEL_RE.match(line, start):
                self.unclear.append((number, "a line that opens with a workdir label is not a declaration (Workdir, "
                                     "The workdir or Your workdir, optionally a colon or is, then one absolute "
                                     "path); write it as one"))
            if rest is not None and not rest[MarkupEnds(rest)(0):]:
                bare = number
            elif rest is not None:
                self._declare(rest, number)
            pin = PIN_RE.match(line, start)
            if pin:
                self.pins.append((number, pin.group(1)))
                (self.brief_pins if line.startswith("BRIEF-PIN", start) else self.reviewed_heads).append(
                    (number, pin.group(1)))
            elif PIN_LABEL_RE.match(line, start):
                self.bad_pins.append(number)
            elif near_pin(line) is not None:
                self.near_pins.append((number, near_pin(line)))
            if found and HANDBACK_NAMED_RE.search(line):
                self.handback_named = True
        if bare is not None:
            self.unclear.append((bare, "a bare workdir declaration with no line after it"))

    def _declare(self, rest, number):
        """Record the workdir a declaration names on `rest`: one absolute path that opens it, quoted when it
        holds a space; any other shape is unclear, never a workdir."""
        rest = rest[MarkupEnds(rest)(0):]
        if rest[:1] in ('"', "`"):
            end = rest.find(rest[0], 1)
            path = rest[1:end] if end > 0 else ""
            if path.startswith("/"):
                self._add_workdir(path, number)
                return
        else:
            match = ABS_PATH_RE.match(rest)
            if match:
                tail, ends_sentence = rest[match.end():], _path_token(match.group(0)) != match.group(0)
                # a word after the path may be the rest of a path with a space in it, so it is unclear
                if ends_sentence or not tail.strip() or tail[0] in ".,;:)*_`" or tail.lstrip()[0] == "(":
                    self._add_workdir(_path_token(match.group(0)), number)
                    return
        self.unclear.append((number, "the declared workdir is not one absolute path (quote a path that holds "
                             "a space)"))

    def _add_workdir(self, path, number):
        """Record a declared workdir, or an unclear declaration when it is too wide to bound reads (bad_workdir)."""
        if bad_workdir(path):
            self.unclear.append((number, "the declared workdir {} is the root or holds a `..` component; declare "
                                 "the directory itself".format(path)))
        else:
            self.workdirs.append(path)


def _declaration_rest(line, start):
    """The text after a workdir declaration that opens `line` at `start`, or None when the line is not one. A
    declaration is Workdir, The workdir or Your workdir (or work dir, working directory), then optionally a
    colon or the word is (DECLARATION_LINK_RE: is before a space or the line end), then an absolute path, plain
    or quoted, or nothing (the path is then on the next line). A path anywhere else after the word, as in prose
    about the workdir, declares nothing; a line that opens with a workdir label (WORKDIR_LABEL_RE) and is not a
    declaration is unclear (PreflightText)."""
    lead = WORKDIR_LEAD_RE.match(line, start)
    if not lead:
        return None
    rest = line[lead.end():].lstrip()
    link = DECLARATION_LINK_RE.match(rest)
    if link:
        rest = rest[link.end():].lstrip()
    opens = rest[MarkupEnds(rest)(0):]
    if not opens or opens[:1] == "/" or (opens[:1] in ('"', "`") and opens[1:2] == "/"):
        return rest
    return None


def normalise(path):
    """posixpath.normpath with a leading `//` folded to `/`, as Linux opens it."""
    path = posixpath.normpath(path)
    return "/" + path.lstrip("/") if path.startswith("/") else path


def bad_workdir(path):
    """Whether a declared workdir is too wide to bound reads: it normalises to / or holds a `..` component."""
    return normalise(path) == "/" or ".." in path.split("/")


def under_workdir(path, workdirs):
    """Whether an absolute path is a declared workdir or lies under one, read lexically (normalise: `//`, `.`
    and `..` folded, no link followed): /w/repo/x is under /w/repo, /w/repository is not."""
    path = normalise(path)
    for workdir in workdirs:
        workdir = normalise(workdir)
        if path == workdir or path.startswith(workdir.rstrip("/") + "/"):
            return True
    return False


_GIT_READ_HOME = None


def _git_read_home():
    """A process-lifetime empty directory serving as HOME and XDG_CONFIG_HOME for the P3 git reads, created
    lazily and removed at interpreter exit."""
    global _GIT_READ_HOME
    if _GIT_READ_HOME is None or not os.path.isdir(_GIT_READ_HOME):
        _GIT_READ_HOME = tempfile.mkdtemp(prefix="aiqt-brief-capability-git-home-")
        atexit.register(shutil.rmtree, _GIT_READ_HOME, ignore_errors=True)
    return _GIT_READ_HOME


def _git_read_env():
    """The caller's environment with every GIT_-prefixed variable dropped, so a caller-supplied GIT_DIR or
    config source cannot point the read at another repository, then made hermetic: the system and global
    config pinned to os.devnull with GIT_CONFIG_NOSYSTEM=1, and HOME and XDG_CONFIG_HOME pointed at an empty
    directory, so a malformed or adversarial system, global, XDG or environment-injected git configuration
    cannot fail or steer the read. The repository's own config, and a file an absolute include.path in it names,
    are still read: a malformed one fails the read, CannotEvaluate."""
    env = dict((key, value) for key, value in os.environ.items() if not key.startswith("GIT_"))
    home = _git_read_home()
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = home
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


# git's ownership refusal: its first line in English, and the command of its hint, which a translation keeps
OWNERSHIP_REFUSAL_MARKS = ("detected dubious ownership in repository at ", "--add safe.directory ")
OWNERSHIP_REMEDY = ("git refused it for dubious ownership: the repository is owned by another user, and this read "
                    "honours no system or global safe.directory entry; run the check as the repository owner, or "
                    "point --repo at a checkout you own")


def _rev_parse(repo, rev):
    """git rev-parse of `rev` as a commit in `repo` under _git_read_env(), output captured."""
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", rev + "^{commit}"],
                          capture_output=True, timeout=GIT_READ_TIMEOUT, env=_git_read_env())


def _cat_file(repo, commit, path):
    """git cat-file -e of `path` at `commit` in `repo` under _git_read_env(), output captured."""
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", commit + ":" + path],
                          capture_output=True, timeout=GIT_READ_TIMEOUT, env=_git_read_env())


def _ownership_refused(probe):
    """Whether a failed git read was git refusing the repository for its ownership."""
    stderr = probe.stderr.decode("utf-8", "replace")
    return probe.returncode != 0 and any(mark in stderr for mark in OWNERSHIP_REFUSAL_MARKS)


def resolve_revision(repo, rev):
    """The full commit id a pinned revision names in `repo`, or CannotEvaluate: a revision that does not
    resolve, a repository git refuses for its ownership (named as such), or a git that cannot be launched or
    times out, leaves the question open, never a pass."""
    try:
        probe = _rev_parse(repo, rev)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("pinned revision {}: cannot run git in {}: {}".format(rev, repo, exc))
    if _ownership_refused(probe):
        raise CannotEvaluate("pinned revision {}: cannot read {}: {}".format(rev, repo, OWNERSHIP_REMEDY))
    commit = probe.stdout.decode("ascii", "replace").strip()
    if probe.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise CannotEvaluate("pinned revision {} does not resolve to a commit in {}".format(rev, repo))
    return commit


def exists_at_revision(repo, commit, path):
    """Whether `path` exists at `commit` in `repo`, read from the object store with git cat-file -e (never the
    working tree), or CannotEvaluate when git refuses the repository for its ownership, or cannot be launched
    or times out."""
    try:
        probe = _cat_file(repo, commit, path)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("cannot read {} at {} in {}: {}".format(path, commit, repo, exc))
    if _ownership_refused(probe):
        raise CannotEvaluate("cannot read {} at {} in {}: {}".format(path, commit, repo, OWNERSHIP_REMEDY))
    return probe.returncode == 0


def _first_seen(pairs):
    """[(line, path)] keeping only the first line each path is named on."""
    seen, out = set(), []
    for line, path in pairs:
        if path not in seen:
            seen.add(path)
            out.append((line, path))
    return out


def rule_p1(names, row, repo):
    """P1 ADVISORY: the brief names a workdir and an input by a relative path, which resolves against the
    worker's own scratch (its working directory), not the workdir."""
    if not names.names_workdir:
        return []
    return [("P1", "ADVISORY", line, "relative path with a workdir named: it resolves against the worker's "
             "own scratch, not the workdir; name it as an absolute path under the workdir", _excerpt(path))
            for line, path in _first_seen(names.relative)]


def rule_p2(names, row, repo):
    """P2 HARD: the family declares reads-outside-workdir = "no" and a word of the brief (path_words) gives a
    path that is not under the declared workdir once normalise folds it, or cannot be placed (unplaced), on a
    line where the word starts before the end of the first prohibition (PROHIBITION_RE, read lexically), or on a
    line with none. Two declarations that name different workdirs, one whose workdir cannot be read or is too
    wide to bound reads (bad_workdir), or a workdir label line that is not a declaration leave the boundary open:
    CannotEvaluate, never a pass."""
    if row["reads-outside-workdir"] != "no":
        return []
    if names.unclear:
        line, reason = names.unclear[0]
        raise CannotEvaluate("line {}: {}".format(line, reason))
    if len(set(normalise(path) for path in names.workdirs)) > 1:
        raise CannotEvaluate("the brief declares {} different workdirs ({}); declare exactly one".format(
            len(set(normalise(path) for path in names.workdirs)), ", ".join(sorted(set(names.workdirs)))))
    if not names.workdirs:
        return []
    workdirs = ", ".join(sorted(set(names.workdirs)))
    outside = [(line, "absolute path outside the declared workdir ({}), which this family cannot read".format(
        workdirs), path) for line, path in _first_seen(names.absolute) if not under_workdir(path, names.workdirs)]
    cannot_place = [(line, "path that cannot be placed under the declared workdir ({}) from its text (a home or "
                     "variable path, or `..` that climbs above where the path starts), which this family may "
                     "not be able to read".format(workdirs), path) for line, path in _first_seen(names.unplaced)]
    return [("P2", "HARD", line, message, _excerpt(path)) for line, message, path in outside + cannot_place]


def repository_path(path):
    """The repository-relative form of a relative path a brief names, with `./` and `..` folded
    (posixpath.normpath), or None when it names the root or climbs out of the repository."""
    path = posixpath.normpath(path)
    if path == "." or path.split("/")[0] == "..":
        return None
    return path


def brief_pin_conflict(names):
    """(line, pin, other line, label, value) for the first BRIEF-PIN line whose value is not the first BRIEF-PIN
    line's, character for character, else None: a brief pins one revision, and a cure names it (cure_pins)."""
    for other, value in names.brief_pins[1:]:
        if value != names.brief_pins[0][1]:
            return names.brief_pins[0] + (other, "BRIEF-PIN", value)
    return None


def pins_agree(value, pin):
    """Whether a REVIEWED HEAD value agrees with the BRIEF-PIN value: one is a prefix of the other, character for
    character, so a full 40-hex value must be the BRIEF-PIN value itself."""
    return value.startswith(pin) or pin.startswith(value)


def reviewed_head_conflict(names):
    """(line, pin, other line, label, value) for the first REVIEWED HEAD line whose value does not agree with the
    first BRIEF-PIN line's (pins_agree), else None; with no BRIEF-PIN line, None."""
    for other, value in names.reviewed_heads:
        if names.brief_pins and not pins_agree(value, names.brief_pins[0][1]):
            return names.brief_pins[0] + (other, "REVIEWED HEAD", value)
    return None


def near_pin_conflict(names):
    """(line, pin, other line, label, value) for the first look-alike pin line (near_pins) whose commit id does not
    agree with the first BRIEF-PIN line's (pins_agree), else None; with no BRIEF-PIN line, None."""
    for other, value in names.near_pins:
        if names.brief_pins and not pins_agree(value, names.brief_pins[0][1]):
            return names.brief_pins[0] + (other, "look-alike pin label", value)
    return None


def pin_conflict_message(conflict):
    """The CannotEvaluate message for pin lines that disagree, naming both lines."""
    line, pin, other, label, value = conflict
    return ("lines {} and {}: the pin lines disagree (BRIEF-PIN {} on line {}, {} {} on line {}); a brief pins one "
            "revision, so every BRIEF-PIN line must hold the same value, and every REVIEWED HEAD line, and every line "
            "that opens with REVIEWED HEAD or BRIEF-PIN once case, compatibility forms and dashes are folded and "
            "backticks and table pipes are skipped, a value that agrees with it, one a prefix of the other, character "
            "for character, and a P5 cure names that one pin".format(
                min(line, other), max(line, other), pin, line, label, value, other))


def rule_p3(names, row, repo):
    """P3 ADVISORY: the brief pins a revision and a repository-relative path it names (repository_path) does
    not exist at that revision in `repo`; it may be a file the brief asks the worker to create, so it never
    refuses. A pin label with no valid pin (bad_pins), pin lines that disagree (brief_pin_conflict,
    reviewed_head_conflict, near_pin_conflict for a look-alike pin line: the message names both lines,
    pin_conflict_message), a pin that does not resolve, or no repository to read is CannotEvaluate, whether or not
    the brief names a relative path."""
    if names.bad_pins:
        raise CannotEvaluate("line {}: a REVIEWED HEAD or BRIEF-PIN line with no pin of 7 to 40 or 64 hex "
                             "digits".format(names.bad_pins[0]))
    conflict = brief_pin_conflict(names) or reviewed_head_conflict(names) or near_pin_conflict(names)
    if conflict is not None:
        raise CannotEvaluate(pin_conflict_message(conflict))
    if not names.pins:
        return []
    if repo is None:
        raise CannotEvaluate("the brief pins a revision but no repository was given to read it from")
    commits = [(rev, resolve_revision(repo, rev)) for _, rev in names.pins]
    paths = [(line, repository_path(path)) for line, path in names.relative]
    paths = _first_seen([(line, path) for line, path in paths if path is not None])
    findings = []
    for rev, commit in commits:
        for line, path in paths:
            if not exists_at_revision(repo, commit, path):
                findings.append(("P3", "ADVISORY", line, "path does not exist at the pinned revision {}: if it "
                                 "is an input, the worker cannot read it there; if the brief asks the worker to "
                                 "create it, nothing is wrong".format(rev), _excerpt(path)))
    return findings


def rule_p4(names, row, repo):
    """P4 ADVISORY: the family declares handback = "yes", a sentence asks for a whole file, a diff or other
    large output, and the brief names no hand-back file."""
    if row["handback"] != "yes" or names.handback_named:
        return []
    findings = []
    for block in parse_blocks(names.text):
        if block.kind != "prose":
            continue
        line_starts, offset = [], 0
        for _, line in block.lines:
            line_starts.append(offset)
            offset += len(line) + 1
        for start, raw in sentences(block.text().replace("\n", " ")):
            verb, large = DELIVER_RE.search(raw), LARGE_OUTPUT_RE.search(raw)
            if verb and large and not Sentence(raw).negated(*verb.span()):
                findings.append(("P4", "ADVISORY", block.lines[bisect.bisect_right(line_starts, start) - 1][0],
                                 "large output asked for with no hand-back file named", _excerpt(raw)))
    return findings


def hex_ref(word):
    """Whether a word is a commit id as a pin is written: 7 to 40 or 64 hex digits, read without a pattern."""
    return (7 <= len(word) <= 40 or len(word) == 64) and all(character in HEX_DIGITS for character in word)


def pin_matches(ref, pins):
    """Whether a commit id and one of the pins name the same commit: either is a prefix of the other, in any case."""
    ref = ref.lower()
    return any(ref.startswith(pin.lower()) or pin.lower().startswith(ref) for pin in pins)


def relative_ref(ref):
    """Whether a ref is HEAD, ORIG_HEAD or @ followed by one or more ~N or ^N steps (HEAD~1, @^2, HEAD~), read
    without a pattern; @{u} and every other suffix are not."""
    base = next((base for base in RELATIVE_BASES if ref.startswith(base)), None)
    steps = ref[len(base):] if base else ""
    return bool(steps) and steps[0] in "~^" and all(
        character in "~^" or character.isdecimal() for character in steps) and steps.isascii()


def peeled_ref(ref):
    """Whether a ref is a commit id (hex_ref) followed by PEEL_SUFFIX, ^{commit}, read without a pattern."""
    return ref.endswith(PEEL_SUFFIX) and hex_ref(ref[:-len(PEEL_SUFFIX)])


def fixed_ref(ref):
    """Whether a reach's ref, read by its spelling alone, cannot move: a commit id (hex_ref), alone or peeled
    (peeled_ref: <id>^{commit}), a dispatch ref, HEAD, FETCH_HEAD, ORIG_HEAD or @ (INHERITED_REFS), or a relative
    form of HEAD, ORIG_HEAD or @ (relative_ref: HEAD~1). HEAD, ORIG_HEAD and @ name a commit HEAD already held, so
    the reach that put it there is the one judged; rule_p5 first replaces a FETCH_HEAD that follows a fetch or pull
    with that reach's ref, so only a FETCH_HEAD with none before it in the brief is read here. A branch, a tag, a
    relative form of anything else (main~3), @{u}, a placeholder (<branch>) and the remote's branch ("") all move, and
    so does a word that names a branch or tag by its place, whatever its spelling (reach_moves)."""
    return (hex_ref(ref) or peeled_ref(ref) or ref.startswith(DISPATCH_PREFIX) or ref in INHERITED_REFS
            or relative_ref(ref))


def reach_moves(ref, named):
    """Whether a reach's ref can move: a word that names a branch or tag by its place (`named`, see reach_refs: a
    clone's -b or --branch value, so git clone --branch 20261029 moves, the remote's default branch or branch tips,
    and a switch's word with no --detach, -d, -c or -C) moves whatever its spelling; any other ref moves unless it
    is fixed_ref."""
    return named or not fixed_ref(ref)


def governing_pins(line_pins, clause_pins):
    """The pins a reach is matched against: the pin lines' (BRIEF-PIN, REVIEWED HEAD) where the brief has one, else
    its confirm-clause pins, so a confirm clause cannot put another commit in place of a pinned revision."""
    return line_pins or clause_pins


def _span_at(spans, starts, position):
    """The code span of `spans` (sorted, disjoint [start, end); `starts` their starts) that holds `position`, or
    None, by bisection."""
    index = bisect.bisect_right(starts, position) - 1
    return spans[index] if index >= 0 and position < spans[index][1] else None


def prohibitions(line, spans):
    """The REACH_PROHIBITION_RE matches on a P5 line that are prohibitions: a prohibition word inside a code span
    (`never run x` quoted as text) is not one, and neither is one glued to `=` (--color=never; the pattern's
    look-behind)."""
    starts = [start for start, _ in spans]
    return [found for found in REACH_PROHIBITION_RE.finditer(line) if _span_at(spans, starts, found.start()) is None]


def prohibition_objects(line, spans):
    """Sorted, disjoint [start, end) ranges of a P5 line that a prohibition governs: the OBJECT of each prohibition
    (prohibitions), the closed shape the S rules use. After the prohibition word and at most one prohibited verb
    (REACH_PROHIBITION_RE: Never run, Do not use, must not execute, Don't follow, or Never alone) and any
    OBJECT_MARKUP, the object is the code span that opens there (every reach inside it), or else the one reach (a
    git command line or a prose reach) that starts exactly there, as (position, position + 1). Nothing else is
    governed: a negation before another verb (Do not forget to run, not to skip), a second command line of a list
    (Never run git pull or git clone ...), and every reach after the object in its clause or sentence are judged."""
    by_start = dict(spans)
    ranges = []
    for found in prohibitions(line, spans):
        position = found.end()
        while position < len(line) and line[position] in OBJECT_MARKUP:
            position += 1
        ranges.append((position, by_start.get(position, position + 1)))
    ranges.sort()
    merged = []
    for start, end in ranges:
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _governed(ranges, position):
    """Whether `position` lies in one of the sorted, disjoint [start, end) ranges prohibition_objects gives."""
    index = bisect.bisect_right(ranges, (position, float("inf"))) - 1
    return index >= 0 and position < ranges[index][1]


def argument_stop(line, end, limit):
    """Where the arguments of the command line whose subcommand (or python target) ends at `end` stop: at the
    first `;`, `&&`, `||`, `|` or backtick, the next command line (`limit`) or the line's end. Each mark is looked
    for once, between `end` and the stop found so far, so the runs of all the command lines on a line cost one pass
    in total."""
    stop = limit
    for mark in ARGUMENT_STOPS:
        found = line.find(mark, end, stop)
        if found >= 0:
            stop = found
    return stop


def _list_marker(word):
    """Whether a word is a list marker: -, *, + or ASCII digits followed by `.` or `)`."""
    return word in ("-", "*", "+") or (len(word) > 1 and word[-1] in ".)" and word[:-1].isascii()
                                       and word[:-1].isdecimal())


def _unspanned(words, bare):
    """The words inside one single-backtick code span that the whitespace-split `words` are as a whole (its opening
    backtick glued to the start of the first word, its closing one to the end of the last, and no other backtick
    among them); with `bare`, `words` themselves when none holds a backtick; else None."""
    ticks = sum(word.count("`") for word in words)
    if not ticks:
        return words if bare else None
    if (ticks != 2 or not words[0].startswith("`") or not words[-1].endswith("`")
            or (len(words) > 1 and "`" in (words[0], words[-1]))):
        return None
    return " ".join(words)[1:-1].split()


def cure_command(words):
    """Whether whitespace-split `words` are exactly the CURE COMMAND: git, optionally -C and one word, then checkout
    or switch (CURE_SUBCOMMANDS), --detach and one word; or git -C, one word, checkout and one word. The -C word is a
    closed path (CURE_PATH_RE: no shell metacharacter, quote, ~ or $). The last word is the pin argument, which
    rule_p5 compares with the pins (cure_matches); no other word, option, subcommand or prompt is part of it."""
    if words[:1] != ["git"]:
        return False
    if words[1:2] == ["-C"] and not (len(words) > 2 and CURE_PATH_RE.fullmatch(words[2])):
        return False
    rest = words[3:] if words[1:2] == ["-C"] else words[1:]
    if len(rest) == 3:
        return rest[0] in CURE_SUBCOMMANDS and rest[1] == "--detach"
    return len(rest) == 2 and words[1:2] == ["-C"] and rest[0] == "checkout"


def same_directory(command, directory):
    """Whether a cure command runs in a clone's directory: its -C word is that directory, character for character."""
    return command[1:3] == ["-C", directory]


def clone_chain(words):
    """The cure command of a cure chain, else None: whitespace-split `words` are exactly git clone, any number of
    CURE_CLONE_OPTIONS, a source (CURE_SOURCE_RE) and a directory (CURE_PATH_RE), then CURE_CHAIN and a cure command
    (cure_command) in that directory (same_directory). No other word, command, option or chain is part of it, and a
    cure command alone is no chain."""
    if CURE_CHAIN not in words:
        return None
    at = words.index(CURE_CHAIN)
    clone, command = words[:at], words[at + 1:]
    if clone[:2] != ["git", "clone"] or len(clone) < 4 or not all(word in CURE_CLONE_OPTIONS for word in clone[2:-2]):
        return None
    if not (CURE_SOURCE_RE.fullmatch(clone[-2]) and CURE_PATH_RE.fullmatch(clone[-1])):
        return None
    return command if cure_command(command) and same_directory(command, clone[-1]) else None


def cure_line(line, fenced=False):
    """(start, pin argument) of the CURE COMMAND (cure_command) on one physical line that is a CURE LINE, else None;
    `start` is the offset of the command's git word. Split on whitespace, a cure line is (a) the command alone, bare
    or as one code span (_unspanned), after at most one list marker (_list_marker) or one CURE_PREFIXES word (`$`, or
    then in any case); or (b) at most one list marker, one of CURE_LEADS (Make a scratch clone:, Clone or Run, as
    written), then one code span that is the whole rest of the line and is exactly a cure chain (clone_chain: a
    closed clone, CURE_CHAIN and the command in the clone's directory). A `fenced` line (inside a fence) is literal
    text: no list marker opens it, and of CURE_PREFIXES only FENCE_PREFIXES (`$`). No other word is read, before the
    command or after it, so no English is."""
    words = [(found.start(), found.group(0)) for found in WORD_RE.finditer(line)]
    texts = [word for _, word in words]
    if not texts:
        return None
    marker = not fenced and _list_marker(texts[0])
    first = 1 if marker or texts[0].lower() in (CURE_PREFIXES & FENCE_PREFIXES if fenced else CURE_PREFIXES) else 0
    command = _unspanned(texts[first:], True)
    if command is None or not cure_command(command):
        command, first = None, 1 if marker else 0
        for lead in CURE_LEADS:
            if tuple(texts[first:first + len(lead)]) == lead:
                command = clone_chain(_unspanned(texts[first + len(lead):], False) or [])
                break
    if not command:
        return None
    start, word = words[len(words) - len(command)]
    return start + (1 if word.startswith("`") else 0), command[-1]


def cure_characters(line):
    """Whether a cure line's raw text is CURE_CHARACTERS alone: printable ASCII, with only space or tab between words,
    so the words read are the bytes bash runs (no format character removed, no other space split on)."""
    return all(character in CURE_CHARACTERS for character in line)


def _comment_held(line, comment):
    """(whether `line` holds any part of an HTML comment, whether one is open at its end), with `comment` open at its
    start: a comment opens at HTML_COMMENT_OPEN, wherever it stands on its line, and closes at the next
    HTML_COMMENT_CLOSE, on its line or a later one. Each marker is looked for once, left to right."""
    held, position = comment, 0
    while True:
        if comment:
            close = line.find(HTML_COMMENT_CLOSE, position)
            if close < 0:
                return held, True
            comment, position = False, close + len(HTML_COMMENT_CLOSE)
        else:
            opening = line.find(HTML_COMMENT_OPEN, position)
            if opening < 0:
                return held, False
            comment, held, position = True, True, opening + len(HTML_COMMENT_OPEN)


def cure_lines(text, raw):
    """{line number: (line, (start, pin argument))} for the physical lines of a brief that are CURE LINES. `text` is
    the brief as every rule reads it (format characters removed, `\\r` dropped at a line's end) and `raw` the same
    text before decode_brief removed them, line for line. A line is read on `raw`: it must be cure_characters alone
    (a carriage return, a format character, a control character or a space other than space or tab makes it no cure
    line) and a cure_line, read as a fence line inside a fence. A line that holds any part of an HTML comment
    (_comment_held, fence or not) is none, and so is a line joined from others (p5_lines). The comment and fence
    structure is read on `text`: a fence opens and closes as parse_blocks reads it, and a fence marker inside a
    comment opens or closes nothing."""
    found, opening, comment = dict(), None, False
    for number, (line, physical) in enumerate(zip(text.split("\n"), raw.split("\n")), 1):
        line = line.rstrip("\r")
        held, comment = _comment_held(line, comment)
        if held:
            continue
        if opening is not None:
            if _fence_closes(line, opening):
                opening = None
                continue
        else:
            opened = FENCE_OPEN_RE.match(line)
            if opened and not (opened.group(2)[0] == "`" and "`" in opened.group(3)):
                opening = opened.group(2)
                continue
        cure = cure_line(physical, opening is not None) if cure_characters(physical) else None
        if cure is not None:
            found[number] = (line, cure)
    return found


def reach_arguments(words, value_options):
    """([(option, value)], [positional word], [word after `--`] or None) for the words after a git subcommand,
    walked once with no pattern: an option in `value_options` takes the next word as its value (or the text after
    its `=`, or after a short option's letter: -bmain), any other word that opens with - is a flag (value None), and
    `--` ends the options, every word after it returned apart and unread (None when there is no `--`)."""
    options, positional, index = [], [], 0
    while index < len(words):
        word = words[index]
        index += 1
        if word == "--":
            return options, positional, words[index:]
        if word[:1] != "-" or word == "-":
            positional.append(word)
            continue
        name, equals, value = word.partition("=")
        if equals and name in value_options:
            options.append((name, value))
        elif word in value_options:
            options.append((word, words[index] if index < len(words) else ""))
            index += 1
        elif word[1:2] != "-" and word[:2] in value_options:
            options.append((word[:2], word[2:]))
        else:
            options.append((word, None))
    return options, positional, None


def reach_refs(subcommand, words):
    """[(ref, named)] for the refs a git command line reaches (REACH_REFS), or [] when it is not a reach; `named` is
    True for a word that names a branch or tag by its place, whatever its spelling (reach_moves). clone: the value
    of --revision, else of -b or --branch (named), else the remote's default branch ("", named). checkout and
    switch: the first positional word (the start point after -b, -B, -c or -C and their branch, X after --detach),
    none when there is none; a switch with none of SWITCH_COMMIT_OPTIONS reads it as a branch (named), since git
    refuses a commit there. reset, in any mode (a soft or mixed reset moves HEAD, and a later checkout or reset of
    the tree puts the commit in it): its first positional word, so a checkout or reset of a commit followed by `--`
    and a path, which restores paths from it, reaches that commit too. fetch: the source side of each refspec after
    the remote, the words after `--` included (OPERANDS_AFTER_DASHDASH), and with no refspec the remote's branch
    tips ("", named). pull: always the remote's branch ("", named). worktree add: the commit-ish after the path,
    the words after `--` included. merge: its first positional word, or with none the upstream branch ("", named).
    rebase: the value of --onto and its first positional word (the upstream), or with neither positional nor --root
    the upstream branch ("", named). A merge or rebase with one of SEQUENCE_OPTIONS reaches nothing. restore: the
    value of -s or --source (none: it reads the index, not a ref). archive: its first positional word (the
    tree-ish, local or --remote)."""
    if subcommand == "pull":
        return [("", True)]
    if subcommand == "worktree":
        if words[:1] != ["add"]:
            return []
        words = words[1:]
    options, positional, after = reach_arguments(words, REACH_VALUE_OPTIONS[subcommand])
    flags = set(option for option, _ in options)
    if subcommand in OPERANDS_AFTER_DASHDASH:
        positional = positional + (after or [])
    if subcommand == "clone":
        revisions = [value for option, value in options if option in CLONE_REVISION_OPTIONS]
        branches = [value for option, value in options if option in ("-b", "--branch")]
        return [(revisions[-1], False)] if revisions else [(branches[-1] if branches else "", True)]
    if subcommand == "fetch":
        sources = [refspec.lstrip("+").partition(":")[0] for refspec in positional[1:]]
        return [(source, False) for source in sources if source] or [("", True)]
    if subcommand == "worktree":
        return [(ref, False) for ref in positional[1:2]]
    if subcommand in ("merge", "rebase") and flags & SEQUENCE_OPTIONS:
        return []
    if subcommand == "merge":
        return [(ref, False) for ref in positional[:1]] or [("", True)]
    if subcommand == "rebase":
        onto = [(value, False) for option, value in options if option in REBASE_ONTO_OPTIONS]
        upstream = [(ref, False) for ref in positional[:1]]
        return onto + (upstream or ([] if "--root" in flags else [("", True)]))
    if subcommand == "restore":
        return [(value, False) for option, value in options if option in RESTORE_SOURCE_OPTIONS]
    if subcommand == "archive":
        return [(ref, False) for ref in positional[:1]]
    named = subcommand == "switch" and not (flags & SWITCH_COMMIT_OPTIONS)
    return [(ref, named) for ref in positional[:1]]


def _reach_word(word):
    """A word of an argument run with opening markup and quotes, and closing punctuation, dropped (_word_end); a
    closing brace that closes one the word opens is part of the ref (@{u}, main@{1}), not markup."""
    word = word.lstrip("\"'*_([{<")
    end = len(_word_end(word))
    opened = word.count("{", 0, end) - word.count("}", 0, end)
    while opened > 0 and end < len(word) and word[end] == "}":
        end, opened = end + 1, opened - 1
    return word[:end]


def _subcommand(line, end):
    """The git subcommand that ends a command line at `end`, read back to the whitespace before it."""
    start = end
    while start and not line[start - 1].isspace():
        start -= 1
    return line[start:end]


def _prose_branch(line, position):
    """Whether one of the (at most) three words after `position` is --branch, -b, main, master, trunk or develop,
    or two of them are `the branch`. The words are walked character by character, never past the third."""
    words = []
    while len(words) < 3:
        while position < len(line) and line[position].isspace():
            position += 1
        start = position
        while position < len(line) and not line[position].isspace():
            position += 1
        if start == position:
            break
        words.append(_reach_word(line[start:position]).lower())
    return any(word in PROSE_BRANCH_WORDS or word.startswith("--branch=") for word in words) or any(
        pair == ["the", "branch"] for pair in (words[0:2], words[1:3]))


def _git_open_at_end(line):
    """Whether a line ends inside a git command line before its subcommand: its last git command word
    (COMMAND_WORD_RE) is followed only by global options (GIT_OPTION_RE) and whitespace, as `Run git` or
    `git -C w` at a line's end."""
    text = line.rstrip() + " "
    last = None
    for word in COMMAND_WORD_RE.finditer(text):
        if word.group(1) is not None:
            last = word
    return last is not None and not text[_option_end(text, last.end(), GIT_OPTION_RE, dict()):].strip()


def p5_lines(text):
    """[(number, line)] for P5: the brief's lines, `\\r` dropped, with a line that ends in an odd run of
    backslashes (a shell continuation, or a Markdown hard break; the last backslash dropped) or inside a git
    command line before its subcommand (_git_open_at_end) joined with the next by a space, so a reach split across
    lines is read whole; `number` is the first joined line's. Each line is tested once, on its own text."""
    lines, joined, parts = text.split("\n"), [], []
    for number, line in enumerate(lines, 1):
        line = line.rstrip("\r")
        if (len(line) - len(line.rstrip("\\"))) % 2:
            parts.append((number, line[:-1]))
            continue
        parts.append((number, line))
        if number < len(lines) and _git_open_at_end(line):
            continue
        joined.append((parts[0][0], " ".join(part for _, part in parts)))
        parts = []
    if parts:
        joined.append((parts[0][0], " ".join(part for _, part in parts)))
    return joined


def web_refs(line):
    """[(start, ref)] for the web addresses of a P5 line that name a ref: a word that opens, after opening markup,
    with http:// or https:// (WEB_URL_RE) and whose path holds a segment of WEB_REF_SEGMENTS (archive, tree, blob,
    raw) followed by a ref (refs/heads/ or refs/tags/ skipped, an ARCHIVE_SUFFIXES suffix dropped) that is not
    fixed (fixed_ref). Each word is split once."""
    found = []
    for word in WORD_RE.finditer(line):
        if not WEB_URL_RE.match(word.group(0)):
            continue
        segments = _word_end(word.group(0).lstrip("\"'`*_([{<")).split("/")
        for index in range(3, len(segments) - 1):
            if segments[index] not in WEB_REF_SEGMENTS:
                continue
            rest = segments[index + 1:]
            if rest[:1] == ["refs"] and rest[1:2] in (["heads"], ["tags"]):
                rest = rest[2:]
            ref = rest[0] if rest else ""
            for suffix in ARCHIVE_SUFFIXES:
                if ref.endswith(suffix):
                    ref = ref[:-len(suffix)]
                    break
            if ref and not fixed_ref(ref):
                found.append((word.start(), ref))
            break
    return found


def submodule_remote(subcommand, words):
    """Whether a git command line is a submodule update --remote, which moves each submodule to its remote's branch
    tip: an ADVISORY reach (REACH_SEVERITY), since it moves submodule content, not the pinned tree."""
    return subcommand == "submodule" and "update" in words and "--remote" in words


def confirm_pins(line):
    """The confirm-clause pins of one P5 line: on a line that holds upper-case HEAD as a word and a confirm word
    (CONFIRM_RE), each word of 7 to 40 or 64 hex digits holding a digit, the commit id of a reach on that line
    included."""
    if not (HEAD_WORD_RE.search(line) and CONFIRM_RE.search(line)):
        return []
    return [word for word in (_reach_word(word) for word in line.split())
            if hex_ref(word) and any(character.isdecimal() for character in word)]


def pin_reaches(text, raw):
    """(pins, reaches) for the whole brief, read over p5_lines. pins are the confirm-clause pins (confirm_pins).
    reaches are [(line, kind, subcommand, refs, cure, excerpt)] in brief order: kind "git" for a git command line
    (option_commands) whose subcommand is in REACH_REFS and whose words, from its argument run (argument_stop) split
    on whitespace (no pattern reads the run), give it refs (reach_refs), `cure` the pin argument when it is the
    command of a cure line (cure_lines, read on `raw`, the text before format characters are removed, on a line not
    joined from others, at the command's git word) and None
    otherwise; kind "submodule" for a submodule update --remote (submodule_remote); kind "prose", subcommand "" and
    refs [(ref, True)] for clone, check out, switch to or fetch that is not a git subcommand, followed within three
    words by a branch word (_prose_branch), for gh pr checkout, and for a web address that names a ref (web_refs). A
    reach that a prohibition governs (prohibition_objects: the code span, command line or prose reach that is its
    object) is not kept; every other reach is."""
    pins, reaches, cures = [], [], cure_lines(text, raw)
    for number, line in p5_lines(text):
        spans = code_spans(line)
        governed = prohibition_objects(line, spans)
        commands = sorted(dict((end, start) for start, end in option_commands(line)).items())
        physical, cure = cures.get(number, (None, None))
        if physical != line:   # a line joined from others is no cure line
            cure = None
        runs = []
        for index, (end, start) in enumerate(commands):
            limit = max(end, commands[index + 1][1]) if index + 1 < len(commands) else len(line)
            runs.append((start, end, argument_stop(line, end, limit)))
        found, subcommand_starts = [], set()
        for start, end, stop in runs:
            subcommand = _subcommand(line, end)
            subcommand_starts.add(end - len(subcommand))
            if _governed(governed, start):
                continue
            words = [word for word in (_reach_word(word) for word in line[end:stop].split()) if word]
            kind, refs = "git", []
            if submodule_remote(subcommand, words):
                kind, refs = "submodule", [("", True)]
            elif subcommand in REACH_REFS:
                refs = reach_refs(subcommand, words)
            if refs:
                excerpt = _excerpt(line[start:min(end, start + EXCERPT_CHARS)] + line[end:stop][:EXCERPT_CHARS])
                found.append((start, kind, subcommand, refs, cure[1] if cure and cure[0] == start else None, excerpt))
        prose = [match.start() for match in PROSE_REACH_RE.finditer(line)
                 if match.start() not in subcommand_starts and _prose_branch(line, match.end())]
        prose = [(start, "") for start in prose] + [(match.start(), "") for match in GH_CHECKOUT_RE.finditer(line)]
        for start, ref in sorted(prose + web_refs(line)):
            if not _governed(governed, start):
                found.append((start, "prose", "", [(ref, True)], None, _excerpt(line[start:start + EXCERPT_CHARS])))
        found.sort(key=lambda reach: reach[0])
        reaches.extend((number,) + reach[1:] for reach in found)
        pins.extend(confirm_pins(line))
    return pins, reaches


def dispatch_commit(repo, ref):
    """The full commit id a dispatch ref names in `repo`, or None when no repository was given or it does not
    resolve there (unknown, refused for its ownership, git not launched or timed out): `repo` need not be the
    server the worker fetches from, so a ref that does not resolve is not checked, not a refusal."""
    if repo is None:
        return None
    try:
        probe = _rev_parse(repo, ref)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = probe.stdout.decode("ascii", "replace").strip()
    return commit if probe.returncode == 0 and len(commit) in (40, 64) and hex_ref(commit) else None


def cure_pins(names):
    """The pins a cure must name: the BRIEF-PIN value (names.brief_pins) when it is CURE_PIN_DIGITS hex digits, one
    value, since rule_p3 refuses BRIEF-PIN lines that disagree (brief_pin_conflict). A REVIEWED HEAD line, a confirm
    clause and a shorter or 64-digit BRIEF-PIN give none."""
    return list(dict.fromkeys(pin for _, pin in names.brief_pins if len(pin) == CURE_PIN_DIGITS))


def cure_matches(word, pins):
    """Whether a cure's pin argument is one of the cure pins, character for character (no prefix, no case fold), bare
    or peeled: followed by PEEL_SUFFIX, ^{commit}, the form a refusal gives, which no branch named with the pin's hex
    can take the place of (a bare pin is still accepted, for the briefs that name it so)."""
    return word.removesuffix(PEEL_SUFFIX) in pins


def cure_advice(pins):
    """What a P5 refusal tells the author to write: the CURE_EXAMPLE line with the first cure pin, or, with none,
    that a BRIEF-PIN line of the full commit id is needed first."""
    if pins:
        return "add this line after the reach, on its own: " + CURE_EXAMPLE.format(pins[0])
    return ("no BRIEF-PIN line holds a full 40-hex commit id, so nothing can cure it: add the line BRIEF-PIN: "
            "<40-hex commit id> and, after the reach, this line on its own: "
            + CURE_EXAMPLE.format("<40-hex commit id>"))


def rule_p5(names, row, repo):
    """P5 moving reach: the brief pins a revision (governing_pins: its pin lines' pins, else its confirm-clause pins
    from pin_reaches) and reaches it through a ref that can move (reach_moves) with no later cure: the command of a
    cure line (cure_lines) whose pin argument is the cure pin (cure_pins: the 40-hex BRIEF-PIN value, cure_matches:
    bare or followed by ^{commit}), later in brief order. HARD for a git command line, ADVISORY for a prose reach, a
    web address or a submodule update --remote (REACH_SEVERITY); the message gives a line the author can copy
    (cure_advice: the pin followed by ^{commit}). A FETCH_HEAD after
    a fetch or pull (FETCH_REACHES) is first given the last one's ref (its first refspec), so a checkout of it moves
    when that fetch did. Nothing else cures: no other command, place or pin. A dispatch ref that resolves in `repo`
    to a commit that is not a pin (pin_matches) is HARD; one that does not resolve is ADVISORY (dispatch_commit); a
    FETCH_HEAD that inherits a dispatch ref is not read again."""
    clause_pins, reaches = pin_reaches(names.text, names.raw)
    pins = governing_pins([pin for _, pin in names.pins], clause_pins)
    if not pins:
        return []
    cures = cure_pins(names)
    judged, fetched = [], None
    for line, kind, subcommand, refs, cure, excerpt in reaches:
        for ref, named in refs:
            inherited = ref == "FETCH_HEAD" and fetched is not None
            judged.append((line, kind) + (fetched if inherited else (ref, named)) + (cure, inherited, excerpt))
        if subcommand in FETCH_REACHES:
            fetched = refs[0]
    findings, cured = [], False
    for line, kind, ref, named, cure, inherited, excerpt in reversed(judged):
        if reach_moves(ref, named):
            if not cured:
                findings.append(("P5", REACH_SEVERITY[kind], line, "the brief pins a revision but reaches it through "
                                 "{}{}, which can move before the worker reaches it; {}, or reach the pin by a "
                                 "dispatch ref".format("FETCH_HEAD, filled from " if inherited else "",
                                                       "the ref " + ref if ref else "a branch", cure_advice(cures)),
                                 excerpt))
        elif ref.startswith(DISPATCH_PREFIX) and not inherited:
            commit = dispatch_commit(repo, ref)
            if commit is None:
                findings.append(("P5", "ADVISORY", line, "dispatch ref {} not checked: it does not resolve in the "
                                 "repository the lint reads; the worker must confirm HEAD is the pin".format(ref),
                                 excerpt))
            elif not pin_matches(commit, pins):
                findings.append(("P5", "HARD", line, "dispatch ref {} resolves to {}, which is not a pinned "
                                 "revision".format(ref, commit), excerpt))
        elif cure is not None:
            cured = cured or cure_matches(cure, cures)
    findings.reverse()
    return findings


def input_readable(path):
    """Whether the lint's real uid may read `path`: os.access R_OK, which opens no descriptor."""
    return os.access(path, os.R_OK)


def input_state(path):
    """(errno, reason) for a declared input on the host the lint runs on, opening nothing: (None, None) when os.stat
    finds a regular file or a directory that input_readable allows; the stat's errno and message when it fails;
    (None, reason) for any other file type (a FIFO, a socket, a device); (EACCES, reason) when input_readable
    refuses."""
    try:
        info = os.stat(path)
    except OSError as exc:
        return exc.errno, exc.strerror or str(exc)
    except ValueError as exc:
        return None, str(exc)
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        return None, "it is neither a regular file nor a directory"
    if not input_readable(path):
        return errno.EACCES, "the lint's uid may not read it (os.access R_OK refused)"
    return None, None


def rule_p6(names, row, repo):
    """P6 HARD, for every family: a declared input (a declaring paragraph line of the parsed CommonMark
    document, read by the shared parser)
    that does not exist on the host
    the lint runs on (input_state gives an errno of MISSING_ERRNOS: ENOENT, or ENOTDIR for a path under a file).
    A malformed declaration or a look-alike label (bad_inputs), any other stat error, another
    file type and a path input_readable refuses leave the input unconfirmed: CannotEvaluate, never a pass. Paths in
    prose and in code (a fenced or indented block, a code span) are not judged."""
    if names.bad_inputs:
        line, reason = names.bad_inputs[0]
        raise CannotEvaluate("line {}: {}".format(line, reason))
    findings = []
    for line, path in _first_seen(names.inputs):
        code, reason = input_state(path)
        if code in MISSING_ERRNOS:
            findings.append(("P6", "HARD", line, "declared input does not exist on this host ({}), so the worker "
                             "cannot read it; provide it or correct the INPUT line".format(reason), _excerpt(path)))
        elif reason is not None:
            raise CannotEvaluate("line {}: the declared input {} cannot be confirmed readable: {}".format(
                line, path, reason))
    return findings


def preflight(text, row, repo, raw):
    """The preflight findings (P1 to P6) for a brief's text and its family's row; they apply to every family. `raw`
    is the text before decode_brief removed format characters, which the P5 cure reads."""
    names = PreflightText(text, raw)
    return rule_p1(names, row, repo) + rule_p2(names, row, repo) + rule_p3(names, row, repo) + rule_p4(
        names, row, repo) + rule_p5(names, row, repo) + rule_p6(names, row, repo)


def can_execute(row):
    """Whether a family's table row lets it run commands; the execution rules apply only to one that cannot."""
    return row["execute"] != "no"


def evaluate(data, family, table, scoped=True, repo=None):
    """(exit code, findings) for a brief's bytes addressed to `family`, or CannotEvaluate. Findings are
    (rule, severity, line, message, excerpt), in brief order. `repo` is the repository a pinned revision is
    read from (P3)."""
    if family not in table:
        raise CannotEvaluate("family {!r} is not in the capability table (known: {})".format(
            family, ", ".join(sorted(table))))
    text = decode_brief(data)
    early = preflight(text, table[family], repo, data.decode("utf-8"))
    if can_execute(table[family]):
        early.sort(key=lambda finding: finding[2])
        hard = any(severity == "HARD" for _, severity, _, _, _ in early)
        return (EXIT_REFUSE if hard else EXIT_OK), early
    blocks = parse_blocks(text)
    if scoped:
        blocks = apply_scope(blocks, family, set(table))
    findings, previous, previous_asks, voided = [], None, False, set()
    for block, group in zip(blocks, void_groups(blocks)):
        if block.kind == "fence":
            if rule_s1(block):
                first = next((line for _, line in block.lines if line.strip()), "")
                intro = not previous_asks and reference_intro(previous)
                finding = ("S1", "INFO" if intro else "HARD", block.start, "executable fence ({}{})".format(
                    block.tag or "untagged", ", introduced as not to be run" if intro else ""), _excerpt(first))
                findings.append((group, finding))
            if fence_asks(block):
                voided.add(group)
            previous, previous_asks = block, False
            continue
        previous = block
        line_starts, offset = [], 0
        for _, line in block.lines:
            line_starts.append(offset)
            offset += len(line) + 1
        structural = command_rule(block)
        found = []
        for start, sentence in sentences(_masked_text(block).replace("\n", " ")):
            line = block.lines[bisect.bisect_right(line_starts, start) - 1][0]
            hits, asks = lint_sentence(sentence, line, r1_applies(block), structural)
            found.extend(hits)
            if asks and asks_in(block):
                voided.add(group)
        previous_asks = asks_to_run(found)
        findings.extend((group, finding) for finding in found)
    findings = [cancel_demotion(finding) if group in voided else finding for group, finding in findings]
    findings = sorted(early + findings, key=lambda finding: finding[2])
    hard = any(severity == "HARD" for _, severity, _, _, _ in findings)
    return (EXIT_REFUSE if hard else EXIT_OK), findings


# printed with a refusal that holds a command line (S2 or S3): how to ask about the line without asking to run it
COMMAND_LINE_HINT = ("to ask about a command line, name the function, row or entry that holds it instead of "
                     "embedding the command (for example: quote the def line of X, not the line that runs git push)")


PREFLIGHT_RULES = frozenset(("P1", "P2", "P3", "P4", "P5", "P6"))
# added to a REFUSE line when a P6 hit is among the hard hits
INPUT_HINT = "; provide each missing declared input, or correct its INPUT line, before sending"


def run(brief_path, family, table_path, scoped=True, repo=None):
    try:
        table = load_table(table_path)
        code, findings = evaluate(_read_regular(brief_path, "brief"), family, table, scoped, repo)
    except CannotEvaluate as exc:
        print("CANNOT_EVALUATE: {}".format(exc))
        return EXIT_CANNOT_EVALUATE
    for rule, severity, line, message, excerpt in findings:
        print("{}:{}: {} {}: {}: {}".format(brief_path, line, severity, rule, message, excerpt))
    hard = [rule for rule, severity, _, _, _ in findings if severity == "HARD"]
    missing_input = INPUT_HINT if "P6" in hard else ""
    if code == EXIT_REFUSE and set(hard) - PREFLIGHT_RULES:
        print("REFUSE: family {} cannot execute and the brief carries a structural execution request ({} hard "
              "hit(s): {}); rewrite the brief or send these items to a family that can execute{}{}".format(
                  family, len(hard), ", ".join("{} x{}".format(r, hard.count(r)) for r in sorted(set(hard))),
                  "; " + COMMAND_LINE_HINT if set(hard) & {"S2", "S3"} else "", missing_input))
    elif code == EXIT_REFUSE and "P5" in hard:
        print("REFUSE: family {}: the brief reaches a pinned revision through a ref that can move ({} hard hit(s): "
              "{}); after the reach, add the line each P5 hit gives ({}), or reach the pin by a dispatch ref{}{}; "
              "{}".format(
                  family, len(hard), ", ".join("{} x{}".format(r, hard.count(r)) for r in sorted(set(hard))),
                  CURE_EXAMPLE.format("<pin>"), ", and name paths under the declared workdir" if "P2" in hard else "",
                  missing_input, CURE_SHAPE))
    elif code == EXIT_REFUSE and set(hard) == {"P6"}:
        print("REFUSE: family {}: the brief declares an input that does not exist on this host ({} hard hit(s): "
              "P6 x{}){}".format(family, len(hard), len(hard), INPUT_HINT))
    elif code == EXIT_REFUSE:
        print("REFUSE: family {}: the brief names an input the family cannot read ({} hard hit(s): {}); name "
              "paths under the declared workdir, or state a path the worker must not use as a prohibition{}".format(
                  family, len(hard), ", ".join("{} x{}".format(r, hard.count(r)) for r in sorted(set(hard))),
                  missing_input))
    elif can_execute(table[family]):
        advisory = sum(1 for _, severity, _, _, _ in findings if severity == "ADVISORY")
        print("OK: family {} can execute ({}); the execution rules do not apply{}".format(
            family, table[family]["execute"],
            "; {} preflight advisory, read the ADVISORY lines".format(advisory) if advisory else ""))
    else:
        advisory = sum(1 for _, severity, _, _, _ in findings if severity == "ADVISORY")
        print("OK: family {}: no structural hit ({} advisory, {} info); read the ADVISORY lines".format(
            family, advisory, len(findings) - advisory))
    return code


USAGE = ("usage: check_brief_capability.py --family NAME [--unscoped] [--table PATH] [--repo PATH] BRIEF\n"
         "       check_brief_capability.py --self-test | --execution-report ABS_PATH\n"
         "The pin reads (P3) honour no system or global safe.directory entry: a --repo owned by another user exits 2\n"
         "(dubious ownership); run the check as the repository owner, or point --repo at a checkout you own.")


def check_table(path):
    """The repository-level run: the capability table parses and holds only known capabilities."""
    try:
        table = load_table(path)
    except CannotEvaluate as exc:
        print("CANNOT_EVALUATE: {}".format(exc))
        return EXIT_CANNOT_EVALUATE
    print("OK: capability table valid ({} families); pass --family NAME BRIEF to lint a brief".format(len(table)))
    return EXIT_OK


def main(argv):
    if not argv:
        return check_table(ROOT / TABLE_REL)
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return self_test(argv[1])
    family, table_path, scoped, paths, repo = None, None, True, [], None
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--family" and args and family is None:
            family = args.pop(0)
        elif arg == "--table" and args and table_path is None:
            table_path = Path(args.pop(0))
        elif arg == "--repo" and args and repo is None:
            repo = Path(args.pop(0))
        elif arg == "--unscoped":
            scoped = False
        elif arg.startswith("-"):
            paths = None
            break
        else:
            paths.append(arg)
    if family is None or paths is None or len(paths) != 1:
        print("CANNOT_EVALUATE: wrong arguments\n" + USAGE, file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    return run(paths[0], family, ROOT / TABLE_REL if table_path is None else table_path, scoped,
               ROOT if repo is None else repo)


# SELF-TEST: production code ends here. Every fixture below is synthetic (no real brief text, path, or
# revision), addressed to the fixture families alpha (executes), beta (executes in a read-only sandbox),
# and gamma (cannot execute). Each case asserts through the instrumented check() choke point, and the
# executed id set is reconciled against tools/selftest_checks.toml (suite brief-capability-selftest), both
# in-run and by tools/check_selftest_execution.py. The revert/ cases patch one rule out of this module's
# globals (no code is generated or evaluated) and require its fixture to flip.

SUITE_ID = "brief-capability-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()


def _family_row(name, execute, network, git_read, scratch, handback, outside):
    return ('[[family]]\nname = "%s"\nexecute = "%s"\nnetwork = "%s"\nrepo-read = "path"\ngit-read = "%s"\n'
            'scratch = "%s"\nhandback = "%s"\nreads-outside-workdir = "%s"\n'
            % (name, execute, network, git_read, scratch, handback, outside))


TABLE_FIXTURE = ("format-version = 1\n"
                 + _family_row("alpha", "yes", "yes", "yes", "persistent", "yes", "yes")
                 + _family_row("beta", "read-only-sandbox", "no", "yes", "per-command", "no", "undeclared")
                 + _family_row("gamma", "no", "no", "undeclared", "none", "no", "no"))


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


@contextlib.contextmanager
def _patched(**replacements):
    """Replace module-level names (a rule patched out) for the duration of the block, then restore them."""
    names, absent = globals(), object()
    saved = dict((name, names.get(name, absent)) for name in replacements)
    names.update(replacements)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is absent:   # a name the previous revision had, put back for the block only
                del names[name]
            else:
                names[name] = value


@contextlib.contextmanager
def _environ(**values):
    """Set environment variables for the duration of the block, then restore or remove them."""
    saved = dict((name, os.environ.get(name)) for name in values)
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _git_outcome(fn, *args):
    """A git read's result, or "CannotEvaluate"."""
    try:
        return fn(*args)
    except CannotEvaluate:
        return "CannotEvaluate"


def _outcome(text, family="gamma", scoped=True, repo=None):
    """(exit code, sorted distinct (rule, severity)) for a synthetic brief, or "CannotEvaluate"."""
    table = parse_table(TABLE_FIXTURE.encode("utf-8"))
    try:
        code, findings = evaluate(text.encode("utf-8") if isinstance(text, str) else text, family, table,
                                  scoped, repo)
    except CannotEvaluate:
        return "CannotEvaluate"
    return code, sorted(set((rule, severity) for rule, severity, _, _, _ in findings))


def _table_outcome(text):
    try:
        return sorted(parse_table(text.encode("utf-8")))
    except CannotEvaluate:
        return "CannotEvaluate"


def _quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc), file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(SUITE_ID, CHECKS_MANIFEST),
          file=sys.stderr)
    return None


TABLE_PARSE_REFUSAL = "capability table: not valid UTF-8 TOML:"
MANIFEST_PARSE_REFUSAL = "SELF-TEST HARNESS ERROR: cannot read"


TRACEBACK_MARK = "Traceback (most recent call last"
INJECTED_EXHAUSTION_MARK = "injected-memory-exhaustion"


def _tomllib_parsers():
    """(tomllib.loads, tomllib.load) as bound when called: the injection seam saves this pair, and the
    restoration checks compare it with the pair bound at import."""
    return tomllib.loads, tomllib.load


TOMLLIB_PARSERS = _tomllib_parsers()


@contextlib.contextmanager
def _injected_memory_exhaustion():
    """tomllib.loads and tomllib.load raise MemoryError on a marked, otherwise valid input for the
    duration of the block, restored in a finally: real memory exhaustion cannot be provoked safely, so
    each MemoryError vector injects it at the parse call it exercises."""
    real_loads, real_load = _tomllib_parsers()

    def exhausted_loads(text, **kwargs):
        if INJECTED_EXHAUSTION_MARK in text:
            raise MemoryError("injected memory exhaustion")
        return real_loads(text, **kwargs)

    tomllib.loads = exhausted_loads
    tomllib.load = lambda handle, **kwargs: exhausted_loads(handle.read().decode("utf-8"), **kwargs)
    try:
        yield
    finally:
        tomllib.loads, tomllib.load = real_loads, real_load


def _boundary(argv, refusal):
    """(exit, refusal seen, traceback seen) of main(argv) alone. What is judged: the text main writes
    through sys.stdout and sys.stderr, captured together in process, must contain the refusal and must
    not contain the traceback marker; other text in it is not judged, and a write that bypasses
    sys.stdout and sys.stderr (sys.__stderr__, file descriptors 1 and 2, a child process) is not
    captured. A SystemExit out of main (check() refusing a duplicate id when the suite is re-entered)
    is returned with the captured diagnostic in place of the tuple, so it fails the compared value by
    name instead of ending the run with no output."""
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            code_seen = main(argv)
    except SystemExit as exc:
        return "SystemExit({!r}) out of main: {}".format(exc.code, printed.getvalue().strip())
    transcript = printed.getvalue()
    return code_seen, refusal in transcript, TRACEBACK_MARK in transcript


def _table_refusal(body):
    """Both production boundaries on a table whose parse fails, each judged by _boundary: check_table
    through a bare main() and run through --table; or the class of a parse failure that escaped the
    handler. A CannotEvaluate out of main is not converted: it propagates and ends the suite with a
    traceback."""
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-brief-capability-table-"))
    try:
        os.makedirs(tmp / ".aiqt" / "core")
        with open(tmp / TABLE_REL, "w", encoding="utf-8") as handle:
            handle.write(body)
        with open(tmp / "probe.md", "w", encoding="utf-8") as handle:
            handle.write(NEGATED)
        with _patched(ROOT=tmp):
            bare = _boundary([], TABLE_PARSE_REFUSAL)
        linted = _boundary([str(tmp / "probe.md"), "--family", "gamma", "--table", str(tmp / TABLE_REL)],
                           TABLE_PARSE_REFUSAL)
        return bare, linted
    except (ValueError, RecursionError, MemoryError) as exc:
        return "escaped " + type(exc).__name__
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _manifest_refusal(path):
    """(reader, command) on the checks manifest at path, each judged on its own transcript: reader is
    (result, refusal seen, traceback seen) of a direct _expected_check_ids call, and command is
    _boundary of main(--self-test) alone, so a refusal the reader prints cannot stand in for one the
    command omits; or the class of a parse failure that escaped the handler."""
    printed = io.StringIO()
    try:
        with _patched(CHECKS_MANIFEST=path):
            with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
                got = _expected_check_ids()
            command = _boundary(["--self-test"], MANIFEST_PARSE_REFUSAL)
    except (ValueError, RecursionError, MemoryError) as exc:
        return "escaped " + type(exc).__name__
    transcript = printed.getvalue()
    return (got, MANIFEST_PARSE_REFUSAL in transcript, TRACEBACK_MARK in transcript), command


def _silenced_manifest_self_test(report_path=None):
    """The silenced-command mutant of the opening of self_test, its expected-set read with stderr
    discarded, as far as the manifest vectors reach it: their manifest is unreadable, so self_test
    returns 2 there. A readable manifest returns a value no vector expects instead of running the suite."""
    with contextlib.redirect_stderr(io.StringIO()):
        expected = _expected_check_ids()
    if expected is None:
        return 2
    return "readable manifest: the mutant reproduces only the opening"


RUN_ITEM = "3. Run `t.py --self-test` on a snapshot of the change.\n"
# a production source for the source audit: a module-level compile it does not hold, a function-local literal, a
# non-literal pattern, a matching function referenced without a call, getattr on re, an import from re, an alias of
# re, non-literal flags, a starred and a double-starred argument after a literal pattern, and flags given twice
AUDIT_SOURCE_CANARY = ("import re\nX = re.compile(r\"(?:a|ab)*c\")\n\ndef f(text):\n"
                       "    return re.search(r\"\\s*\\s+\", text, re.I)\n\ndef g(pattern, text):\n"
                       "    return re.match(pattern, text)\n\ndef h():\n"
                       "    return re.findall\n\ndef k():\n    return getattr(re, \"compile\")\n\n"
                       "from re import compile\nimport re as r\n\ndef m(text, flags):\n"
                       "    return re.search(r\"x\", text, flags)\n\ndef n(*rest):\n"
                       "    return re.search(r\"x\", *rest)\n\ndef o(text, **options):\n"
                       "    return re.search(r\"x\", text, **options)\n\ndef q(text):\n"
                       "    return re.search(r\"x\", text, 0, flags=re.I)\n")
RUN_PROSE = "3. Run the parser on a snapshot of the change.\n"
NEGATED = "Do not run code or git.\n"
CLAUSE_MID = "Describe the build of the parser.\n"
BASH_FENCE = "Steps:\n\n```bash\npython3 t.py\n```\n"
PYCON_FENCE = "Steps:\n\n```pycon\n>>> import t\n```\n"
PYTHON_PROMPT_FENCE = "Steps:\n\n```python\n>>> import t\n```\n"
UNTAGGED_FENCE = "Steps:\n\n```\n$ git status\n```\n"
PLAIN_FENCE = "```\ncd repo\npython3 t.py\n```\n"
REFERENCE_FENCE = "The README shows this; do not run it:\n\n```bash\npython3 t.py\n```\n"
REFERENCE_UNTIL = "Do not run this until you read the notes:\n\n```bash\npython3 t.py\n```\n"
QUOTE_SELF_TEST = "Quote the code that runs `t.py --self-test`.\n"
MENTION = "CI failed on `t.py --self-test`.\n"
PROHIBITION = "Do not run `t.py --self-test`.\n"
PROHIBITION_EXCEPT = "Do not run `t.py --self-test` without the flag.\n"
GIT_SHOW_PREAMBLE = "Read only `git show abc1234:<path>` in the worktree.\n\n1. Quote the helper.\n"
GIT_SHOW_PROSE = "Answer by reading files at commit abc1234 (git show abc1234:<path>).\n"
GIT_ITEM = "1. Answer from a git show attempt's result.\n"
PYTHON_COMMAND = "Read the output of python3 -I -B t.py first.\n"
DOT_SLASH = "1. Use ./t.sh for the setup.\n"
BASH_C = "1. Use bash -c with the helper.\n"
PYTEST = "1. Use pytest -q on the module.\n"
SPAN_TOOL = "1. Say what `make test` prints.\n"
DESCRIBED_PROSE = "1. In t.py, quote the line in it that runs git show-ref.\n"
CONFIG_NOUN = "1. Name the setting the global git config file pins.\n"
GIT_OPTIONS = "git -c a -c a -c a -c a -c a -c a -c a show"   # seven global options: no bound applies
PYTHON_C = "1. Run python3 -c \"print(1)\".\n"
PYTHON_M = "1. Run python3 -m unittest.\n"
PYTHON_OPTIONS_M = "1. Run python3 -I -B -m unittest.\n"
# quoted source text is not a data context: a command line in it refuses (an accepted false positive)
QUOTED_ENTRY = "1. Quote the scope_rows entry for FOO=1 ./x.sh with its expected exit.\n"
QUOTED_ROW = "1. Quote the test row containing `t.py --self-test`.\n"
QUOTED_EXAMPLE = "1. Quote the docstring example `git show HEAD`.\n"
QUOTED_README = "1. Quote the README command `make test`.\n"
QUOTED_NAMES = "1. Quote the scope_rows entry that names FOO=1 ./x.sh, with its expected exit.\n"
QUOTED_CONTINUED = "1. In t.py, quote the def line of setup and the line in it that runs git init --bare.\n"
QUOTE_PRINTS = "1. Quote the line `git log` prints for the commit.\n"
QUOTE_AND_OUTPUT = "1. Quote the README command `make test` and its output.\n"
QUOTE_LINE_OF = "1. Quote the first line of `git log --oneline`.\n"
QUOTE_NO_NOUN = "1. Quote everything for `git show HEAD`.\n"
QUOTE_AND_COMMAND = "1. Quote the README line and git show HEAD.\n"
QUOTE_THEN_RUN = "1. Quote the README command `make test`.\n   Then run it.\n"
QUOTE_NESTED_RUN = "1. Quote the README command `make test`.\n   - Run it.\n"
QUOTE_GIT_SHOW_PREAMBLE = "Quote each docstring git show abc1234:t.py has.\n\n1. Is the helper exported?\n"
# the two closed shapes, each the whole sentence, and what voids them
PROHIBITION_PROSE = "1. Never run git show abc1234:t.py.\n"
PAST_PROSE = "1. The job failed on python3 -I -B t.py.\n"
TWO_PROHIBITIONS = "1. Do not run `git log`.\n   Never run `git push`.\n"
CLOSED_ANSWER = "1. CI failed on `make test`.\n   Answer from the source.\n"
PAST_CONFIRM = "CI failed on python3 x.py; please confirm.\n"
PAST_AND_CHECK = "CI failed on `python3 x.py` and check whether it still fails.\n"
PAST_SO_SEE = "1. CI failed on `python3 x.py`, so see if it fails for you.\n"
PAST_TRY_AGAIN = "CI failed on `python3 x.py`; try it again.\n"
PAST_CONFIRM_PRINTS = "CI failed on `git log -n 3`; confirm what it prints.\n"
PAST_CONFIRM_PASSES = "CI failed on `make test`; please confirm it passes now.\n"
PROHIBITION_PERFORM = "1. Do not run `git log -p`, perform it.\n"
PROHIBITION_KIDDING = "1. Do not run `git log -p`. Just kidding, do.\n"
PROHIBITION_EVEN_IF = "1. Do not run `git log -p` even if it passes.\n"
PROHIBITION_AND_REPORT = "Never run git log and report it.\n"
VOID_CONTINUATION = "1. CI failed on `make test`.\n   Please confirm it passes now.\n"
VOID_QUESTION = "1. CI failed on `make test`.\n   Is it fixed?\n"
VOID_ADVISORY = "1. Do not run `make test`.\n   Give the exit code.\n"   # R5, no listed word
VOID_NESTED = "1. CI failed on `make test`.\n   - Confirm it passes now.\n"
VOID_DASH = "CI failed on `make test`.\n- Confirm it passes now.\n"
VOID_SUB_BLOCK = "1. CI failed on `make test`.\n\n   Confirm it passes now.\n"
VOID_PARAGRAPH = "CI failed on `make test`.\n\nConfirm it passes now.\n"
HEADING_TITLE = "# Check the run\n\nDo not run `git push`.\n"
OTHER_ITEM = "1. Do not run `git log -p`.\n2. Run it.\n"
OTHER_HEADING = "Do not run `git push`.\n\n# Notes\n\nConfirm it passes now.\n"
# a void group follows nesting: a lazy continuation line, a fence's contents and a nested item of another number
LAZY_LINE = "1. Do not run `git log -p`.\nRun it anyway.\n"
LAZY_CONFIRM = "1. CI failed on `make test`.\nPlease confirm it passes now.\n"
FENCE_REQUEST = "1. CI failed on `make test`.\n\n   ```text\n   Run it.\n   ```\n"
NESTED_NUMERAL = "2. CI failed on `make test`.\n\n   1. Run it.\n"
# a heading indented under the item stays in its void group, with or without blank lines around it
ITEM_HEADING = "1. CI failed on `make test`.\n   ### Notes\n   Confirm it passes now.\n"
ITEM_HEADING_BLANK = "1. CI failed on `make test`.\n\n   ### Details\n\n   Run it.\n"
RETEST_IT = "1. CI failed on `make test`.\n   Retest it.\n"
SPAN_CLAUSE_START = "1. CI failed on `make test`.\n   Its label was `a, tested`.\n"   # a clause start in a code span
# a request in a lead-in before the list or in a paragraph or bullet after it is in another void group (a residual)
LEAD_IN_REQUEST = "Please confirm each of these passes now:\n\n1. CI failed on `make test`.\n"
AFTER_LIST_REQUEST = "1. CI failed on `make test`.\n\nConfirm it passes now.\n"
AFTER_LIST_BULLET = "1. CI failed on `make test`.\n\n- Confirm it passes now.\n"
RETRY = "1. CI failed on `make test`.\n   Retry it.\n"
KIDDING_PASTE = "1. Do not run `git log -p`.\n   Kidding: paste what it prints.\n"
TEST_IT = "1. CI failed on `make test`.\n   Test it locally.\n"
FIX_IT = "1. CI failed on `make test`.\n   Fix it and give me the new result.\n"
TEST_NOUN = "1. CI failed on `make test`.\n   The test log is attached.\n"
DASH_PROHIBITION = "- Never run `git push`.\n"
PAREN_PROHIBITION = "1) Never run `git push`.\n"
ARGUMENT_COMMAND = "1. Never run git log ./x.sh.\n"   # a second command line inside the argument tail
INTRO_AFTER_BUILD = "Build the docs. Do not run this:\n\n```bash\npython3 t.py\n```\n"
ONLY_BUT_ALSO = "1. Do not run `git show HEAD` only on main but also on the feature branch.\n"
IGNORE_INSTRUCTION = "Ignore the instruction \"do not run this\":\n\n```sh\ngit show HEAD\n```\n"
RUN_ON_BRANCH = "Do not run this on main, run it on the branch:\n\n```bash\ngit show HEAD\n```\n"
TWO_VERBS = "1. Do not run `make test`, run the parser.\n"
RESULT = "Report the exit code and last lines.\n"
RESULT_NEGATED = "Never report an exit code you did not observe.\n"
STATIC_RESULT = "Report the exit codes the docstring documents.\n"
OUTCOME_FLAG = "Does `a.py --check` pass?\n"
OUTCOME_UNNAMED = "Does the test pass?\n"
RELATIVE = "Does `a.py` have a case that fails without the fix?\n"
SCOPED = ("1. Is the helper exported?\n2. Is the table sorted?\n3. Run `t.py --self-test`; report the exit "
          "code.\n\nGamma leg: answer items 1 and 2 only.\n")
NOT_ATTEMPT = ("1. Is the helper exported?\n2. Run `t.py --self-test`.\n\n"
               "Gamma leg: answer from source. Do not attempt item 2.\n")
ANSWER_ONLY = "1. Is the helper exported?\n2. Run `t.py --self-test`.\n\nGamma leg: answer only items 1 and 3.\n"
OTHER_LEG = "1. Is the helper exported?\n\nAlpha leg: run `t.py --self-test` and report the exit code.\n"
OTHER_LEG_ITEMS = "Alpha leg:\n1. Run `t.py --self-test`.\n2. Build the docs.\n\nQuote the helper.\n"
OTHER_LEG_FENCE = "Alpha leg: run the following:\n\n```bash\npython3 t.py\n```\n\nGamma leg: answer from source.\n"
OTHER_LEG_GIT = "Alpha leg: read with `git show abc1234:t.py`.\n\nGamma leg: answer from source.\n"
OTHER_SECTION = ("# Brief\n\nQuote the helper.\n\n## Alpha leg\n\nRun `t.py --self-test`.\n\n## Done\n\n"
                 "Nothing else.\n")
SECTION_ENDS = "## Alpha leg\n\nRun `t.py --self-test`.\n\n## Gamma\n\nRun `t.py --self-test`.\n"
TAIL_MARKER = "1. Is the helper exported?\n2. Run `t.py --self-test` (alpha leg).\n"
TAIL_IN_CODE = "1. Is the helper exported?\n2. Run `t.py --self-test (alpha leg)` now.\n"
TAIL_MID_LINE = "1. Is the helper exported?\n2. Run `t.py --self-test` (alpha only) on the snapshot.\n"
TAIL_IN_WRAPPED_CODE = "1. Is the helper exported?\n2. Run `t.py --self-test` with `x\n   (alpha leg)` set.\n"
CODE_SPAN_DIRECTIVE = "1. Run `t.py --self-test`.\n\nGamma leg: see `x: answer items 2 only` in the notes.\n"
CODE_SPAN_MARKER = "Gamma leg: read the parser for `Alpha leg:` handling. Run `t.py --self-test`.\n"
MID_LINE_MARKER = "Read the notes on the alpha leg: run `t.py --self-test`.\n"
QUOTED_DIRECTIVE = "1. Run `t.py --self-test`.\n\nGamma leg: the parser accepts \"answer items 2 only\" as input.\n"
ADDRESS_ENDS = "Alpha leg: run `t.py --self-test`. Gamma: run `t.py --self-test` too.\n"
ALL_LEGS_ENDS = "Alpha leg: lint the tree. All legs: run `t.py --self-test`.\n"
REVERSED = "Gamma leg: answer items 2-1 only.\n\n1. Run the parser.\n"
DASH_LIST = "- Read the diff\n- Run `t.py --self-test`\n- Report the exit code\n"
BOM_BRIEF = b"\xef\xbb\xbf```bash\ncd repo\n```\n"   # a byte order mark before the fence opener
ZERO_WIDTH_SPLIT = "Read only `git sh" + chr(0x200B) + "ow abc1234:t.py`.\n"   # a zero-width space inside "show"
# a zero-width space inside a fenced command: the fence body may hold it, and removal restores the command
FENCED_ZERO_WIDTH = "```\nc" + chr(0x200B) + "d repo\n```\n"
# "no" in "casino" ends exactly NEGATION_WINDOW characters before "run": a slice of the window would
# open on a false word boundary and read it as a negation.
SLICE_EDGE = "Read the casino" + " w" * 25 + " ww and run the parser.\n"
NEAR_NEGATION = "Do not" + " w" * 22 + " and run the parser.\n"     # 49 characters: negated
FAR_NEGATION = "Do not" + " w" * 30 + " and run the parser.\n"      # 65 characters: not negated
THEN_NEGATION = "Do not edit files, then run the parser.\n"
SUBORDINATE = "If the patch does not apply, run the parser.\n"
HEADING_NOUN = "# Notes (source only): parser train, rebuild 3; copy and launch wiring\n\nQuote the helper.\n"
HEADING_COMMAND = "# Step 2: git diff abc1234\n\nQuote the helper.\n"
PAREN_NOUN = "Quote the sentence naming the read (execute) flag.\n"
ADJECTIVE_NOUN = "Quote the code that turns a stalled (TIMED-OUT) run into the row's verdict.\n"
OPENING_PARENTHESIS = "2. (Optional) Run the benchmark on the snapshot.\n"
OR_LIST = "Each lint, gate, test, build or self-test writes a log.\n"
PUNCTUATED_LIST = "Name the stage: lint, gate, test, build or self-test.\n"
OXFORD_PARENTHESIS = "Quote the line (test, build, or run).\n"
GIT_PARENTS = "1. Quote the parents of the merge commit as the file headers list them.\n"
GIT_STATEMENT = "The merge commit's parents are listed in the notes.\n"
P1_RELATIVE = "The workdir is /w/repo.\nRead docs/notes.md before you answer.\n"
P1_ABSOLUTE = "The workdir is /w/repo.\nRead /w/repo/docs/notes.md before you answer.\n"
P1_NO_WORKDIR = "Read docs/notes.md before you answer.\n"
P1_NOT_A_PATH = "The workdir is /w/repo.\nAnswer yes/no on read/write access under Python 3.14/3.15.\n"
P2_BRIEF = "The workdir is /w/repo.\nRead {} before you answer.\n"
P2_OUTSIDE = P2_BRIEF.format("/etc/other/notes.md")
P2_NO_WORKDIR = "Read /etc/other/notes.md before you answer.\n"
# prose that mentions the workdir before a path declares nothing; each read outside the one declaration
P2_SUCH_AS = ("Workdir /w/repo (read-only).\nDo not read outside the workdir, such as /etc/x.md.\n"
              "Read /etc/x.md before you answer.\n")
P2_OR_PATH = "Workdir /w/repo.\nKeep paths inside your workdir or /dev/shm/hb.\nRead /dev/shm/hb/x.md now.\n"
P2_ALSO = "Workdir /w/repo.\nThe workdir is also /etc for this task.\nRead /etc/shadow.md before you answer.\n"
# two different declarations, an unquoted space, a bare lead at the end: the boundary is open
P2_TWO = "Workdir /w/repo.\nThe workdir is /etc.\nRead /etc/shadow.md before you answer.\n"
P2_SPACE_UNQUOTED = "Workdir /w/my repo\nRead /w/my other/x.md before you answer.\n"
P2_BARE_END = "Read /w/repo/x.md before you answer.\nWorkdir:\n"
P2_SAME_TWICE = "Workdir /w/repo.\nThe workdir is /w/repo/.\nRead /w/repo/a.md before you answer.\n"
P2_QUOTED = "Workdir \"/w/my repo\".\nRead {} before you answer.\n"
P2_NEXT_LINE = "Workdir\n/w/repo\nRead {} before you answer.\n"
P2_COLON = "workdir:/w/repo\nRead {} before you answer.\n"
# a lead that ends in is takes its path from the next line, like a bare lead or a colon
P2_IS_END = tuple(lead + "\n/w/repo\nRead {} before you answer.\n" for lead in ("The workdir is", "Your workdir is",
                                                                              "Workdir is", "The workdir is "))
# a line that opens with a workdir label but is not a declaration: the boundary is open, never ignored
P2_LABEL = tuple(line + "\nRead /etc/passwd before you answer.\n" for line in (
    "**Workdir**: /w/repo", "Workdir = /w/repo", "Working tree: /w/repo (the workdir).", "The workdir is.\n/w/repo",
    "_YOUR WORKDIR_ - /w/repo"))
P2_ALSO_MID = ("Workdir /w/repo.\nNote: the workdir is also /etc for this task.\n"
               "Read /etc/shadow.md before you answer.\n")
# the word workdir later in a line, or a longer word, is not a label
P2_NOT_LABEL = ("Workdir /w/repo.\nNote: the workdir is read-only.\nRead /w/repo/a.md before you answer.\n",
                "Workdir /w/repo.\nWorkdirs are listed below.\nRead /w/repo/a.md before you answer.\n")
P3_BOLD_PINS = ("**REVIEWED HEAD:** {}\n", "**REVIEWED HEAD**: {}\n", "__BRIEF-PIN__ {}\n", "*BRIEF-PIN*: `{}`\n")
# a `//`, a `~` segment or an unquoted space ended the previous extraction's path before the `..` that escapes
P2_ESCAPES = ("/w/repo//../../etc/passwd", "/w/repo/x~/../../../etc/passwd", "/w/repo/a b/../../../etc/x.md")
P2_UNPLACED = ("~user/notes.md", "$TMPDIR/x.md", "../x.md", ",".join(["/w/repo/a.md"] * 17))
# text glued after a quoted span with a space; a path glued after a character that is not a letter or digit
P2_GLUED_QUOTE = ("\"/w/repo/a b\"/../../../etc/x.md", "`/w/repo/a b`/../../../etc/x.md")
P2_GLUED_AFTER = ("@/etc/x.md", "%/etc/x.md", "-/etc/x.md", "Path:/etc/x.md")
P2_FILE_URL, P2_WEB_URL = "file:///etc/x.md", "https://example.com/a/../../etc/x.md"
# odd words the slash rule refuses although a human would pass them (fail closed)
P2_OVER_REFUSED = ("/w/repo//docs/a.md", "/w/repo/x~/a.md", "\"/w/repo/a b\"/x.md", "ftp://h/w/repo/x.md")
# a declared workdir too wide to bound reads, each with a read the wide workdir would cover
P2_WIDE = ("Workdir /.\nRead /etc/x.md before you answer.\n",
           "Workdir /w/repo/..\nRead /w/other/x.md before you answer.\n",
           "Workdir \"/w/a b/../..\"\nRead /etc/x.md before you answer.\n")
P2_NOT_WIDE = ("Workdir /w\nRead /w/x.md before you answer.\n", "Workdir /w/re..po\nRead /w/re..po/x.md now.\n")
P3_BAD_PINS = ("a" * 41, "a" * 63, "eb64222bx", "", "deadbeef-1")
# a prohibition before the path on its line; the path itself is not asked for
P2_NEGATED = ("Workdir /w/repo (read-only).\nReview /w/repo/a.md. Never read /etc/shadow.\n",
              "Workdir /w/repo (read-only).\nReview /w/repo/a.md, but never write to /srv/other/x.\n",
              "Workdir /w/repo.\nDo not open ~/notes.md or $HOME/x.\n",
              "Workdir /w/repo.\nYou must not read /etc/x.md; don't write /tmp/y.md.\n",
              "Workdir /w/repo.\nRemember not to read /etc/x.md.\n")
P2_NOT_NEGATED = ("Workdir /w/repo.\nRead /etc/x.md; never write it.\n",
                  "Workdir /w/repo.\nRead /w/repo/never/a.md and /etc/x.md.\n",
                  "Workdir /w/repo.\nRead \"never\"/etc/x.md before you answer.\n")
P3_PIN_ONLY = "BRIEF-PIN {}\nAnswer the question.\n"
P3_BRIEF = "REVIEWED HEAD: {} (main).\nRead {} before you answer.\n"
P3_MID_LINE = "Name a REVIEWED HEAD: 0123456789ab line.\nRead docs/absent.md before you answer.\n"
P4_WHOLE_FILE = "Paste the whole file in your reply.\n"
P4_NAMED = P4_WHOLE_FILE + "HAND-BACK FILE: /dev/shm/h/x.diff\n"
P4_NEGATED = "Do not paste the whole file.\n"
# P5 briefs, each formatted with the fixture repository's commit id ({0}; {0:.7} is its first seven digits)
P5_PIN = "BRIEF-PIN: {0}\n"
P5_DENY = (P5_PIN + "Clone with `git clone --branch main /srv/git/g.git w`, then confirm HEAD starts {0:.7}.\n",
           P5_PIN + "Use `git clone --depth 1 --branch main <url>` and review the tree.\n",
           "Use `git clone <url>` then `git switch dev`.\nConfirm the commit HEAD names is 1234567abc.\n",
           P5_PIN + "Use `git fetch origin main && git checkout --detach FETCH_HEAD`.\n",
           P5_PIN + "Use `git clone <url> && git checkout 1111111`.\n")
P5_ALLOW = (P5_PIN + "Use `git clone --no-checkout <url> w`.\n`git -C w checkout --detach {0}`\n",
            P5_PIN + "Use `git init w && git -C w fetch <url> refs/dispatch/1/a1 && git -C w checkout --detach "
                     "FETCH_HEAD`.\n",
            P5_PIN + "Use `git fetch origin {0} && git checkout FETCH_HEAD`.\n",
            "Use `git clone --branch main <url>`, then run pytest on the tip of main.\n",
            P5_PIN + "Do not run git clone --branch main; read the workdir.\n",
            P5_PIN + "The last brief told the worker to clone --branch main.\n")
P5_DISPATCH_MISSING = P5_ALLOW[1].replace("refs/dispatch/1/a1", "refs/dispatch/1/missing")
# the dispatch ref resolves to the fixture commit, and the only pin (a confirm clause) is another commit
P5_DISPATCH_MISMATCH = ("Use `git -C w fetch <url> refs/dispatch/1/a1 && git -C w checkout --detach FETCH_HEAD`.\n"
                        "Confirm HEAD equals 1234567abc.\n")
# a prohibition governs only its own object: a reach in a later clause or sentence (after closing markup too) is
# judged, and the code span (a `;` inside it included) or the one command line that is the object is not
P5_CLAUSE_DENY = (P5_PIN + "Do not edit the tree; clone with `git clone --branch main <url> w` and review w.\n",
                  P5_PIN + "Remember not to push. Run `git clone --branch main <url> w` and review w.\n",
                  P5_PIN + "**Do not edit the tree.** Clone with `git clone --branch main <url> w`.\n")
P5_CLAUSE_ALLOW = (P5_PIN + "Never run `git pull; git clone --branch main <url>`.\n",
                   P5_PIN + "Never run git clone --branch main https://h/g.git w; read the workdir.\n")
# a negation before another verb, a prohibition word in a code span or glued to `=`, and the second command line
# of a prohibited list govern nothing; the object of a prohibited verb, plain or in bold, is governed
P5_OBJECT_DENY = (P5_PIN + "Do not forget to run `git clone --branch main <url> w` first.\n",
                  P5_PIN + "Don't hesitate to run `git clone --branch main <url> w`.\n",
                  P5_PIN + "It is important not to skip `git clone --branch main <url> w`.\n",
                  P5_PIN + "Run `git log --color=never` and `git clone --branch main <url> w`.\n",
                  P5_PIN + "Run git -c color.ui=never log and git clone --branch main <url> w.\n",
                  P5_PIN + "Never run git pull or git clone --branch main <url> w.\n")
P5_OBJECT_GUARD = (P5_PIN + "Paste `never run git clone --branch main <url> w` into the log.\n",
                   P5_PIN + "Run git log --color=never `git clone --branch main <url> w`.\n")
P5_OBJECT_ALLOW = (P5_PIN + "You must not use `git clone --branch main <url> w`.\n",
                   P5_PIN + "Do not clone --branch main, and read the workdir.\n",
                   P5_PIN + "**Never** run **`git clone --branch main <url> w`**.\n")
# the prohibited verb may carry emphasis of its own
P5_OBJECT_EMPHASIS = (P5_PIN + "Never *run* `git clone --branch main <url> w`.\n",
                      P5_PIN + "Do not __use__ `git clone --branch main <url> w`.\n")
# a clause separator a stop does not cover joins nothing to a prohibition: the reach after it is judged
P5_SEPARATORS = (P5_PIN + "Do not edit the tree" + chr(0x2014) + "clone with `git clone --branch main <url> w`.\n",
                 P5_PIN + "Do not edit the tree (clone with `git clone --branch main <url> w` first).\n",
                 P5_PIN + "Do not edit the tree/clone with `git clone --branch main <url> w`.\n",
                 P5_PIN + "Do not edit the tree;clone with `git clone --branch main <url> w`.\n",
                 P5_PIN + "Do not edit the tree and clone with `git clone --branch main <url> w`.\n")
# a cure is a closed shape that reads no English. The command with the full 40-hex BRIEF-PIN value cures as the whole
# of one line, after at most one list marker, `$` or then (in a fence only `$`), bare or as one code span: each such
# line below follows a moving clone (P5_MOVING). Or it ends one code span that is the whole rest of a line opening
# with Make a scratch clone:, Clone or Run and that is exactly a closed clone, && and the command in its directory:
# each such cure chain below follows only the pin line (P5_PIN) and cures its own clone, since after a clone into the
# same directory git refuses the chain's clone (exit 128) and the checkout does not run
P5_MOVING = P5_PIN + "Clone with `git clone <url> w`.\n"
P5_CURE_LINES = ("git -C w checkout --detach {0}\n", "`git -C w checkout --detach {0}`\n",
                 "   git -C w checkout --detach {0}   \n", "- git switch --detach {0}\n",
                 "1. `git -C w switch --detach {0}`\n", "2) git checkout --detach {0}\n", "$ git -C w checkout {0}\n",
                 "then git -C w checkout --detach {0}\n", "Then `git -C w checkout --detach {0}`\n",
                 "```\n$ git -C w checkout --detach {0}\n```\n", "```sh\ngit -C w checkout --detach {0}\n```\n")
P5_CURE_LEADS = ("Make a scratch clone: `git clone --no-hardlinks <url> w && git -C w checkout --detach {0}`\n",
                 "Clone `git clone <url> w && git -C w checkout --detach {0}`\n",
                 "Run `git clone --quiet <url> w && git -C w switch --detach {0}`\n",
                 "3. Run `git clone --no-checkout <url> w && git -C w checkout {0}`\n")
# the words a cure may hold: a tab between words, a <name> placeholder or a relative path as the directory; and in a
# cure chain every clone option, a URL source with : or @, and the canonical line with an absolute source and a
# placeholder directory
P5_CURE_WORDS = ("git -C w\tcheckout --detach {0}\n", "git -C <scratch>/r checkout --detach {0}\n",
                 "git -C ../w.d/x_y-1 switch --detach {0}\n")
P5_CURE_CHAIN_WORDS = ("Run `git clone -q --no-hardlinks file:///srv/g.git w && git -C w checkout --detach {0}`\n",
                       "Clone `git clone git@example.invalid:o/r.git w && git -C w switch --detach {0}`\n",
                       "Make a scratch clone: `git clone --no-hardlinks /srv/w/repo <scratch>/r && git -C "
                       "<scratch>/r checkout --detach {0}`\n")
# a cure line is read on the raw text, printable ASCII with only space or tab between words: a no-break, em,
# ideographic or other Unicode space or a control character (unit separator, vertical tab, file separator)
# makes it no cure line (each one a looser reading splits as a space); a carriage return or a form feed never
# reaches P5, since the brief gate refuses every non-LF line separator first
P5_NOT_CURE_CHARACTERS = tuple(line.format(chr(code)) for line, code in (
    ("git -C w checkout{}--detach {{0}}\n", 0xA0), ("git -C w{}checkout --detach {{0}}\n", 0x2003),
    ("git{}-C w checkout --detach {{0}}\n", 0x3000), ("git -C w checkout{}--detach {{0}}\n", 0x1F),
    ("git -C w checkout{}--detach {{0}}\n", 0x0B), ("git -C w checkout --detach{}{{0}}\n", 0x1C)))
# and before format characters are removed: a zero-width space, a soft hyphen, a byte order mark or a bidirectional
# control inside a cure line makes it none (each one the text with format characters removed would cure). The
# lines stand in a fence behind the `$ ` prompt: only a fenced block's body may hold a format character (outside
# one the brief gate refuses the whole brief first), and a fence line cures behind the prompt
P5_NOT_CURE_FORMAT = tuple("```\n$ " + line.format(chr(code)) + "```\n" for line, code in (
    ("git -C w checkout --de{}tach {{0}}\n", 0x200B), ("git -C w checkout --detach {}{{0}}\n", 0x200B),
    ("git -C w check{}out --detach {{0}}\n", 0xAD), ("{}git -C w checkout --detach {{0}}\n", 0xFEFF),
    ("git -C w checkout --detach {}{{0}}\n", 0x202E)))
# the -C word is a closed path: a shell metacharacter, a quote, ~, $ or a leading - in it makes the line no cure line
# (each one cures under a looser reading that takes any word with no backtick as the -C word)
P5_NOT_CURE_DIRECTORIES = ("git -C # checkout --detach {0}\n", "git -C w;: checkout --detach {0}\n",
                           "git -C w||exit checkout --detach {0}\n", "git -C w&&false checkout --detach {0}\n",
                           "git -C w>/dev/null checkout --detach {0}\n", "git -C \"w\" checkout --detach {0}\n",
                           "git -C ~/w checkout --detach {0}\n", "git -C $W checkout --detach {0}\n",
                           "git -C -w checkout --detach {0}\n",
                           "Run `git clone <url> w;: && git -C w;: checkout --detach {0}`\n")
# a cure chain's code span is exactly the clone, && and the command: the command alone, a second chain link, a clone
# with no directory and a command before another link are no cure chain (each one cures under a looser reading that
# takes the command after the last && or alone)
P5_NOT_CURE_CHAINS = ("Run `git -C w checkout --detach {0}`\n", "3. Run `git -C w checkout --detach {0}`\n",
                      "Run `git clone <url> w && false && git -C w checkout --detach {0}`\n",
                      "Make a scratch clone: `git clone <url> && git -C w checkout --detach {0}`\n",
                      "Run `git clone <url> w && git log && git -C w checkout --detach {0}`\n")
# the chain's clone carries only the listed options, its source is closed, and the command runs in its directory
P5_NOT_CURE_CLONE_OPTIONS = ("Run `git clone --depth=1 <url> w && git -C w checkout --detach {0}`\n",
                             "Run `git clone --single-branch <url> w && git -C w checkout --detach {0}`\n",
                             "Run `git clone --branch=main <url> w && git -C w checkout --detach {0}`\n")
P5_NOT_CURE_SOURCES = ("Run `git clone <url>;id w && git -C w checkout --detach {0}`\n",
                       "Run `git clone ~/g.git w && git -C w checkout --detach {0}`\n",
                       "Run `git clone $SRC w && git -C w checkout --detach {0}`\n")
P5_NOT_CURE_ELSEWHERE = ("Clone `git clone <url> w && git -C x checkout --detach {0}`\n",
                         "Clone `git clone <url> w && git checkout --detach {0}`\n")
# a line inside an HTML comment is no cure line, and a comment closed on its line hides nothing after it
P5_NOT_CURE_COMMENT = ("<!--\ngit -C w checkout --detach {0}\n-->\n",
                       "<!-- the old step:\n`git -C w checkout --detach {0}`\n-->\n")
P5_CURE_AFTER_COMMENT = "<!-- a note -->\ngit -C w checkout --detach {0}\n"
# a fence line is literal text: a diff's removed or added line, any other list marker and then open no cure there
P5_NOT_CURE_FENCE = ("```diff\n- git -C w checkout --detach {0}\n```\n",
                     "```diff\n+ git -C w checkout --detach {0}\n```\n", "```\n* git -C w checkout --detach {0}\n```\n",
                     "~~~\n1. git -C w checkout --detach {0}\n~~~\n",
                     "```sh\nthen git -C w checkout --detach {0}\n```\n")
# a cure in any other place cures nothing, whatever the words around it: a stop or a word after it, a word or a
# second prefix before it, emphasis, another quote or a sentence (the first six a looser reading would cure)
P5_NOT_CURE_LINES = ("git -C w checkout --detach {0}.\n", "Then run `git -C w checkout --detach {0}`\n",
                     "- then git -C w checkout --detach {0}\n", "> git -C w checkout --detach {0}\n",
                     "**`git -C w checkout --detach {0}`**\n", "Afterwards run `git -C w checkout --detach {0}`.\n",
                     "git -C w checkout --detach {0} # the pin\n", "` git -C w checkout --detach {0}`\n",
                     "Make a scratch clone: git clone <url> w, then git -C w checkout --detach {0}; never a "
                     "worktree.\n")
P5_NOT_CURE_LEADS = ("Run `git -C w checkout --detach {0}`.\n", "Run git -C w checkout --detach {0}\n",
                     "Please run `git -C w checkout --detach {0}`\n", "run `git -C w checkout --detach {0}`\n",
                     "Clone with `git clone <url> w && git -C w checkout --detach {0}`\n",
                     "Run `git clone <url> w; git -C w checkout --detach {0}`\n",
                     "Run `git -C w checkout --detach {0} && git log`\n", "Run `&& git -C w checkout --detach {0}`\n")
# only the closed command cures: another subcommand, option or global option does not, the pin argument last
P5_NOT_CURE_COMMANDS = ("git -C w fetch origin {0}\n", "git -C w reset --hard {0}\n", "git -C w worktree add x {0}\n",
                        "git checkout {0}\n", "git -c a=b checkout --detach {0}\n",
                        "git -C w checkout --detach --force {0}\n")
# a cure line joined from two by a backslash is no cure line; a cure cures only the reaches before it
P5_CURE_JOINED = P5_MOVING + "```\ngit -C w \\\n  checkout --detach {0}\n```\n"
P5_CURE_BEFORE = P5_PIN + "git -C w checkout --detach {0}\nClone with `git clone <url> w`.\n"
# only the full 40-hex value of a BRIEF-PIN line, character for character, is a cure's pin: not a REVIEWED HEAD
# line's, not a shorter BRIEF-PIN value, not a confirm clause's, not a prefix, another case or another commit
P5_CURE_BRIEF_PIN = "REVIEWED HEAD: {0}\nClone with `git clone <url> w`.\ngit -C w checkout --detach {0}\n"
P5_CURE_PIN_DIGITS = "BRIEF-PIN: {0:.12}\nClone with `git clone <url> w`.\ngit -C w checkout --detach {0:.12}\n"
P5_CURE_CONFIRM = "Clone with `git clone <url> w`.\ngit -C w checkout --detach {0}\nConfirm HEAD is {0}.\n"
P5_CURE_EXACT = (P5_MOVING + "git -C w checkout --detach {0:.12}\n", P5_MOVING + "git -C w checkout --detach {2}\n")
P5_CURE_OTHER = P5_MOVING + "git -C w checkout --detach " + "1" * 40 + "\n"
# the pin peeled, <pin>^{commit}, cures in every shape above (git reads it as the commit itself, where a checkout of
# the bare pin takes a local branch named with the pin's hex first), and a peeled commit id is a fixed ref wherever
# a reach names it; peeled, a prefix, another case or another commit cures nothing
P5_PEELED = "{0}^{{commit}}"
P5_CURE_PEELED_LINES = tuple(line.replace("{0}", P5_PEELED) for line in P5_CURE_LINES + P5_CURE_WORDS
                             + (P5_CURE_AFTER_COMMENT,))
P5_CURE_PEELED_CHAINS = tuple(line.replace("{0}", P5_PEELED) for line in P5_CURE_LEADS + P5_CURE_CHAIN_WORDS)
P5_PEELED_FIXED = P5_PIN + "Use `git -C w checkout --detach {0}^{{commit}}` and review w.\n"
P5_NOT_CURE_PEELED = (P5_MOVING + "git -C w checkout --detach {0:.12}^{{commit}}\n",
                      P5_MOVING + "git -C w checkout --detach {2}^{{commit}}\n",
                      P5_MOVING + "git -C w checkout --detach " + "1" * 40 + "^{{commit}}\n")
# a <name> placeholder in a -C word or a clone's directory is a whole path segment: one glued to other characters
# (bash reads w<x> as w and a redirection) makes the line no cure line
P5_NOT_CURE_PLACEHOLDERS = ("git -C w<x> checkout --detach {0}\n", "git -C <x>w checkout --detach {0}\n",
                            "git -C a/w<x>/r switch --detach {0}\n",
                            "Run `git clone <url> w<x> && git -C w<x> checkout --detach {0}`\n")
# a look-alike pin line holding another commit ({1}) cannot be evaluated: the label in another case, with a Unicode
# hyphen, in fullwidth letters, in a code span, in a table row, or a lower-case REVIEWED HEAD with a short id; one that
# agrees with the BRIEF-PIN value (the value itself, or a prefix of it), or with no BRIEF-PIN line in the brief, passes
P5_NEAR_PINS = tuple(P5_PIN + form for form in (
    "Brief-Pin: {1}\n", "BRIEF" + chr(0x2010) + "PIN: {1}\n",
    "".join(chr(ord(character) + 0xFEE0) for character in "BRIEF-PIN") + ": {1}\n", "`BRIEF-PIN: {1}`\n",
    "| BRIEF-PIN | {1} |\n", "Reviewed head: {1:.12}\n"))
P5_NEAR_AGREE = (P5_PIN + "Brief-Pin: {0}\n", P5_PIN + "`REVIEWED HEAD: {0:.12}`\n", "Brief-Pin: {1}\n")
# pin lines must agree, so a cure names the brief's one pin ({1} is a second fixture commit): a second BRIEF-PIN value
# (on a line of its own, in a fence, indented or after the cure) or a REVIEWED HEAD that is neither the BRIEF-PIN value
# nor a prefix of it, character for character (another commit, a short id of another commit, the pin in upper case),
# cannot be evaluated; a REVIEWED HEAD that is the value or a prefix of it agrees
P5_PIN_CONFLICTS = (P5_PIN + "BRIEF-PIN: {1}\nClone with `git clone <url> w`.\ngit -C w checkout --detach {1}\n",
                    P5_PIN + "```\nBRIEF-PIN: {1}\n```\nClone with `git clone <url> w`.\ngit -C w checkout --detach "
                             "{1}\n",
                    P5_PIN + "    BRIEF-PIN: {1}\nClone with `git clone <url> w`.\ngit -C w checkout --detach {1}\n",
                    P5_MOVING + "git -C w checkout --detach {1}\nBRIEF-PIN: {1}\n")
P5_REVIEWED_CONFLICTS = ("REVIEWED HEAD: {0}\nBRIEF-PIN: {1}\nClone with `git clone <url> w`.\ngit -C w checkout "
                         "--detach {1}\n",
                         "BRIEF-PIN: {1}\nREVIEWED HEAD: {0:.12}\nClone with `git clone <url> w`.\ngit -C w checkout "
                         "--detach {1}\n",
                         "REVIEWED HEAD: {2}\n" + P5_MOVING + "git -C w checkout --detach {0}\n")
P5_REVIEWED_AGREE = ("REVIEWED HEAD: {0:.12}\n" + P5_MOVING + "git -C w checkout --detach {0}\n",
                     "REVIEWED HEAD: {0}\n" + P5_MOVING + "git -C w checkout --detach {0}\n")
# merge, rebase, restore --source, archive and a path restore reach a ref and never cure; a reset reaches its commit
# in any mode; a relative form of HEAD is fixed
P5_TREE_DENY = (P5_PIN + "Use `git checkout --detach {0} && git merge origin/main`.\n",
                P5_PIN + "Use `git checkout --detach {0} && git rebase origin/main`.\n",
                P5_PIN + "Use `git restore --source=origin/main --worktree .`.\n",
                P5_PIN + "Use `git archive --remote=<url> main | tar -x -C w`.\n")
P5_RESET_DENY = (P5_PIN + "Use `git reset origin/main && git checkout -- .`.\n",
                 P5_PIN + "Use `git reset --soft origin/main && git reset --hard`.\n")
P5_TREE_ALLOW = (P5_PIN + "Use `git archive --remote=<url> {0} | tar -x -C w`.\n",
                 P5_PIN + "Use `git reset --soft HEAD~1`, then review.\n",
                 P5_PIN + "Use `git restore --source={0} --worktree docs`.\n",
                 P5_PIN + "Use `git merge --abort` if it stops.\n")
# a reach split by a backslash continuation, or after a git word at a line's end, is read whole
P5_LINE_JOIN = (P5_PIN + "```\ngit -C w \\\n  checkout main\n```\n",
                P5_PIN + "```\ngit \\\n  clone --branch main <url> w\n```\n",
                P5_PIN + "Run git\nclone --branch main <url> w.\n")
# a reach with no git word: gh pr checkout and a web address that names a branch are printed; one naming the pin is not
P5_NO_GIT = (P5_PIN + "Use gh pr checkout 42 and review it.\n",
             P5_PIN + "Fetch https://example.invalid/o/r/archive/refs/heads/main.tar.gz and unpack it.\n",
             P5_PIN + "Read https://example.invalid/o/r/tree/main/tools first.\n",
             P5_PIN + "Read https://example.invalid/o/r/tree/{0}/tools first.\n")
# a global option's quoted value may hold a space
P5_QUOTED_OPTION = P5_PIN + "Use `git -C \"/w/a b\" checkout main`.\n"
# a confirm-clause commit is a pin even where it is also a reach's commit on the same line
P5_CONFIRM_REACH = "Confirm HEAD is 1234567abc after `git fetch origin 1234567abc && git checkout main`.\n"
# a closing brace that closes the ref's own is kept in the message
P5_BRACE_REF = P5_PIN + "Use `git reset --hard @{{u}}`.\n"
# a submodule update --remote moves submodule content: printed, never refused
P5_SUBMODULE = P5_PIN + "Use `git submodule update --init --remote` and review w.\n"
# a reach quoted as an example still refuses (the choice for quoted examples)
P5_QUOTED_EXAMPLE = P5_PIN + "For example, `git clone --branch main <url> w` is what P5 refuses.\n"
# a fetch with no refspec reaches the remote's branch tips, and FETCH_HEAD after it holds them
P5_FETCH_TIPS = (P5_PIN + "Use `git fetch origin && git checkout --detach FETCH_HEAD` and review the tree.\n",
                 P5_PIN + "Use `git fetch origin && git reset --hard FETCH_HEAD`.\n")
# FETCH_HEAD takes the last fetch's ref: checked out after a cure it moves HEAD again, and fetched as the pin it is
# fixed but cures nothing, since a cure names the pin itself
P5_FETCH_HEAD = (P5_PIN + "Use `git fetch origin main`.\n`git checkout --detach {0}`\nUse `git checkout --detach "
                          "FETCH_HEAD`.\n",
                 P5_PIN + "Use `git clone --branch main <url> w && git -C w fetch origin {0} && git -C w checkout "
                          "--detach FETCH_HEAD`.\n")
# a fetch of the pin moves no HEAD, so it cures nothing
P5_FETCH_NO_CURE = P5_PIN + "Use `git clone --branch main <url> w && git -C w fetch origin {0}` and review w.\n"
# a fetch reads its remote and refspecs after `--`
P5_FETCH_DASHDASH = (P5_PIN + "Use `git fetch -- origin main && git checkout --detach FETCH_HEAD`.\n",
                     P5_PIN + "Use `git fetch -- origin {0} && git checkout --detach FETCH_HEAD`.\n")
# a checkout of a tree-ish followed by `--` and a path restores paths: from the pin it cures nothing, and from a
# branch it is a moving reach
P5_PATH_RESTORE = (P5_PIN + "Use `git clone --branch main <url> w && git -C w checkout {0} -- docs`.\n",
                   P5_PIN + "Use `git checkout main -- docs`, then review docs.\n")
# a word that only names a branch or tag moves however it is spelt: a clone's --branch value, a plain switch's word
P5_NAMED_REF = (P5_PIN + "Use `git clone --branch 20261029 <url> w` and review w.\n",
                P5_PIN + "Use `git -C w switch {0}` and review w.\n")
# with a pin line, a confirm clause cannot put another commit in its place: a dispatch ref resolving to the
# clause's commit ({1}, a second fixture commit) is no pinned revision; with no pin line, the clause's commit is
P5_GOVERNING_PIN = (P5_PIN + "Use `git -C w fetch <url> refs/dispatch/1/b2 && git -C w checkout --detach "
                             "FETCH_HEAD`.\nConfirm HEAD equals {1}.\n",
                    "Use `git -C w fetch <url> refs/dispatch/1/b2 && git -C w checkout --detach FETCH_HEAD`.\n"
                    "Confirm HEAD equals {1}.\n")
# a clone --revision of a commit id is a fixed reach, with = or a separate word; of a branch it moves
P5_CLONE_REVISION = (P5_PIN + "Use `git clone --revision={0} <url> w` and review w.\n",
                     P5_PIN + "Use `git clone --revision {0} <url> w` and review w.\n",
                     P5_PIN + "Use `git clone --revision=main <url> w` and review w.\n")
LINEAR_FACTOR, LINEAR_SLACK, LINEAR_TIMEOUT = 16, 1.0, 120


def _preflight_repo(base):
    """(repo, full commit id) of a fixture repository under `base` whose one commit holds docs/present.md; its
    working tree also holds docs/untracked.md, which no commit has."""
    repo = base / "repo"
    os.makedirs(repo / "docs")
    with open(repo / "docs" / "present.md", "w", encoding="utf-8") as handle:
        handle.write("present\n")
    env = _git_fixture_env.git_fixture_env(
        GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                    "init", "-q", str(repo)], check=True, capture_output=True, timeout=60, env=env)
    subprocess.run(["git", "-C", str(repo), "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c",
                    "maintenance.auto=false", "add", "docs/present.md"],
                   check=True, capture_output=True, timeout=60, env=env)
    subprocess.run(["git", "-C", str(repo), "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c",
                    "maintenance.auto=false", "commit", "-q", "-m", "fixture"],
                   check=True, capture_output=True, timeout=60, env=env)
    head = subprocess.run(["git", "-C", str(repo), "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c",
                           "maintenance.auto=false", "rev-parse", "HEAD"],
                          check=True, capture_output=True, timeout=60, env=env)
    with open(repo / "docs" / "untracked.md", "w", encoding="utf-8") as handle:
        handle.write("untracked\n")
    return repo, head.stdout.decode("ascii").strip()


def _dispatch_ref(repo, ref, commit):
    """Create the dispatch ref `ref` at `commit` in the fixture repository (P5)."""
    env = _git_fixture_env.git_fixture_env()
    subprocess.run(["git", "-C", str(repo), "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c",
                    "maintenance.auto=false", "update-ref", ref, commit],
                   check=True, capture_output=True, timeout=60, env=env)


def _child_commit(repo, commit):
    """The full id of a second commit in the fixture repository, a child of `commit` with its tree, that no branch
    names (P5: a dispatch ref to a commit that is not the pin)."""
    env = _git_fixture_env.git_fixture_env(
        GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    made = subprocess.run(["git", "-C", str(repo), "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c",
                           "maintenance.auto=false", "commit-tree", commit + "^{tree}", "-p", commit, "-m", "child"],
                          check=True, capture_output=True, timeout=60, env=env)
    return made.stdout.decode("ascii").strip()
# The child caps its address space at 1 GiB, then times evaluate() on 128 KiB and 1 MiB briefs, each a prefix,
# one unit repeated, and a suffix.
LINEAR_CHILD = (
    "import resource, sys, time\n"
    "hard = resource.getrlimit(resource.RLIMIT_AS)[1]\n"
    "soft = 1 << 30 if hard == resource.RLIM_INFINITY else min(1 << 30, hard)\n"
    "resource.setrlimit(resource.RLIMIT_AS, (soft, hard))\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import check_brief_capability as m\n"
    "table = m.parse_table(m.TABLE_FIXTURE.encode('utf-8'))\n"
    "prefix, unit, suffix = sys.argv[2:5]\n"
    "for size in (1 << 17, 1 << 20):\n"
    "    body = size - len(prefix) - len(suffix)\n"
    "    data = (prefix + (unit * (body // len(unit) + 1))[:body] + suffix).encode('utf-8')\n"
    "    began = time.perf_counter()\n"
    "    m.evaluate(data, 'gamma', table)\n"
    "    print(time.perf_counter() - began)\n")


def _linear(unit, prefix="", suffix=""):
    """True when evaluate() on a 1 MiB brief (`prefix`, `unit` repeated, `suffix`) takes at most LINEAR_FACTOR
    times as long as on the 128 KiB one plus LINEAR_SLACK seconds, both timed in a child capped at 1 GiB
    (RLIMIT_AS) and stopped after LINEAR_TIMEOUT seconds; otherwise what went wrong. The child must exit 0
    and print exactly two finite, nonnegative times."""
    try:
        done = subprocess.run([sys.executable, "-I", "-B", "-c", LINEAR_CHILD, str(Path(__file__).resolve().parent),
                               prefix, unit, suffix], capture_output=True, text=True, timeout=LINEAR_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "timed out after {} s".format(LINEAR_TIMEOUT)
    try:
        small, large = (float(value) for value in done.stdout.split())
    except ValueError:
        small = large = float("nan")
    if done.returncode != 0 or not all(0.0 <= value < float("inf") for value in (small, large)):
        return "child exit {}, output {!r}: {}".format(done.returncode, done.stdout.strip()[-80:],
                                                      done.stderr.strip()[-200:])
    return large <= LINEAR_FACTOR * small + LINEAR_SLACK or "1 MiB took {:.3f} s, 128 KiB {:.3f} s".format(
        large, small)


# THE REGEX AUDIT. Each compiled pattern reachable from this module's namespace (a global, an item of a tuple, list,
# set, frozenset or dict global at any depth, an attribute of a class defined here), each of COMMAND_FORMS, and each
# regular expression call in the production code that the namespace does not hold (read from the source: a call
# inside a function, or one not assigned to a module-level name) is parsed and walked. A call whose pattern is not a
# literal, and any other use of the re module's matching functions, fails the audit ("unseen"). The guards, each in
# AUDIT_GUARDS so that the self-test can patch one out at a time:
#   pair     two quantifiers (a variable count) whose classes overlap, adjacent or separated only by atoms that can
#            match empty (\s*\s+, \s*,?\s+, \w+\w*);
#   overlap  a repeated group whose tail quantifier overlaps its head quantifier ((?:a+){2,5});
#   empty    a repeated group whose body can match empty ((?:a|){2,5});
#   choice   an unbounded repeat (*, +, {n,}) that gives characters back (not possessive) over a body holding an
#            alternation or a quantifier anywhere inside it ((?:a|ab)*c, (?:\s+\S+)*): the conservative rule, which
#            flags overlapping alternatives without deciding whether they overlap;
#   unread   a construct the audit does not read (a back reference, a conditional group).
# A possessive quantifier or an atomic group gives nothing back, so it is never the first of a pair and never
# flagged by `choice`: it keeps its first match, and a failure after it is not retried inside it. Classes are
# compared over AUDIT_SAMPLE.
AUDIT_GUARDS = dict(
    pair="two quantifiers over overlapping classes that can share one run",
    overlap="a repeated group whose tail and head quantifiers overlap",
    empty="a repeated group whose body can match empty",
    choice="an unbounded repeat that gives characters back over a body holding an alternation or a quantifier",
    unread="a construct the audit does not read",
    unseen="a regular expression call whose pattern the audit cannot read",
)
# one isolated canary per guard: each is flagged by its own guard and by no other
AUDIT_GUARD_CANARIES = dict(pair=r"\s*\s+", overlap=r"(?:a+){2,5}", empty=r"(?:a|){2,5}", choice=r"(?:a|ab)*c",
                            unread=r"(a)\1")
# pattern name -> a one-line proof that its flagged construct cannot split a run (the alternatives cannot match a
# common prefix); none is needed, so no failing-suffix shape is owed for one
REGEX_AUDIT_EXEMPTIONS = dict()
REGEX_AUDIT_CANARIES = ("NOUN_LIST_TAIL_RE", "LED_LIST_TAIL_RE", "ONLY_RE", "FENCE_OPEN_RE", "COMMAND_RE",
                        "COMMAND_WORD_RE", "SHELL_FIRST_LINE_RE", "VOID_WORD_RE", "COMMAND_FORMS[0]")
# the line that ends the production code: the source audit reads the calls above it
PRODUCTION_END = "# SELF-TEST: production code ends here."
# the re functions that take a pattern, and the position of their flags argument
RE_CALLS = dict(compile=1, search=2, match=2, fullmatch=2, finditer=2, findall=2, split=3, sub=4, subn=4)
AUDIT_SAMPLE = frozenset([chr(code) for code in range(128)] + [chr(code) for code in (
    0xA0, 0xE9, 0xDF, 0x416, 0x663, 0x2013, 0x2014, 0x2019, 0x201C, 0x2028, 0x3000)])
# the previous revision's argument tail (CLOSED_ARGS_END_RE), which backtracks exponentially
_OLD_CLOSED_ARGS_END = r"(?:\s+(?:-|[^\s/:=@~^<.]*[/:=@~^<.]|\d)\S*)*\.\s*\Z"
AUDIT_FLAGGED = (r"\s*,?\s+or", r"\s*\s+", r"\w+\w*", r"(?:x+)+", r"(?:a*)*", r"(?:,\s*)?\s+", r"(?:\s+\S+)*\.",
                 r"(?:a|ab)*c", r"(?:a|ab)*?c", r"(?>a|ab)*c", r"(?:\w|\W\w*)+!", _OLD_CLOSED_ARGS_END)
# clean: a possessive repeat, a bounded repeat, and single-character alternatives, which the parser folds into one
# class ((?:\d|\w)+ is [\d\w]+), so nothing in the body can be split two ways
AUDIT_CLEAN = (r"(?:\s*,\s+|\s+)or\s+", r"[A-Za-z][\w-]*+(?:\s*,\s*[A-Za-z][\w-]*+)++", r"(?:\s+\S+)*+\.",
               r"(?:a|ab){0,6}c", r"(?:\d|\w)+\Z", r"(?:-|\d)*?x")
_OLD_ITEM_LIST = (r"Q?\d{1,3}(?:\s*(?:,\s*(?:and\s+|or\s+)?|\band\s+|\bor\s+|-|\bto\s+|\bthrough\s+)\s*"
                  r"Q?\d{1,3})*")
# the patterns of previous revisions that the audit flags (the revert flip puts them back; CLOSED_ARGS_END_RE and
# MARKUP_RE no longer exist and are put back under their old names)
AUDIT_PREVIOUS = dict(
    NOUN_LIST_TAIL_RE=(r"\s*,?\s+or\s+" + NOUN_WORD + r"(?=\s*(?:[)\].;:!?]|\Z))", re.I),
    LED_LIST_TAIL_RE=(r"\s*,?\s+or\s+" + NOUN_WORD + r"(?=\s+(?!(?:" + DETERMINER + r"|it|them|all|both)\b)[A-Za-z])",
                      re.I),
    FENCE_OPEN_RE=(r"([ \t]*)(`{3,}|~{3,})(.*)\Z", 0),
    ONLY_RE=(r"answer\s+(?:only\s+)?(?:items?|questions?)\s+(" + _OLD_ITEM_LIST + r")(\s+only)?", re.I),
    ANSWER_ONLY_RE=(r"answer\s+only\s+(?:items?|questions?)\s+(" + _OLD_ITEM_LIST + ")", re.I),
    EXCLUDE_RE=(r"(?:(?:do\s+not|don't|never)\s+(?:attempt|answer)|skip)\s+(?:items?|questions?)\s+(" + _OLD_ITEM_LIST
                + ")", re.I),
    CLOSED_ARGS_END_RE=(_OLD_CLOSED_ARGS_END, 0),
    MARKUP_RE=(r"(?:" + MARKUP_TOKEN_RE.pattern + r")*", re.I),
    COMMAND_WORD_RE=(COMMAND_WORD_RE.pattern.replace("*+", "*"), 0),
    SHELL_FIRST_LINE_RE=(SHELL_FIRST_LINE_RE.pattern.replace("*+", "*"), 0),
)


def _audit_flag(flagged, guard, detail=""):
    """Append the guard's finding unless the guard is patched out of AUDIT_GUARDS."""
    if guard in AUDIT_GUARDS:
        flagged.append(AUDIT_GUARDS[guard] + detail)


def _audit_chooses(parsed, constants):
    """Whether a parsed sequence holds, anywhere inside it, an alternation or a quantifier with a variable count."""
    for op, value in parsed:
        if op is constants.BRANCH:
            return True
        if op in (constants.MAX_REPEAT, constants.MIN_REPEAT, constants.POSSESSIVE_REPEAT):
            if value[0] != value[1] or _audit_chooses(value[2], constants):
                return True
        elif op is constants.SUBPATTERN and _audit_chooses(value[3], constants):
            return True
        elif op is constants.ATOMIC_GROUP and _audit_chooses(value, constants):
            return True
        elif op in (constants.ASSERT, constants.ASSERT_NOT) and _audit_chooses(value[1], constants):
            return True
    return False


def _audit_chars(op, value, flags, constants):
    """The AUDIT_SAMPLE characters one atom matches, or None for an atom the audit does not know."""
    folded = (lambda ch: set(c for c in (ch, ch.lower(), ch.upper()) if len(c) == 1)) if flags & re.I else (
        lambda ch: set((ch,)))
    escapes = {constants.CATEGORY_DIGIT: r"\d", constants.CATEGORY_NOT_DIGIT: r"\D", constants.CATEGORY_SPACE: r"\s",
               constants.CATEGORY_NOT_SPACE: r"\S", constants.CATEGORY_WORD: r"\w", constants.CATEGORY_NOT_WORD: r"\W"}

    def category(name):
        probe = re.compile(escapes[name]) if name in escapes else None
        return None if probe is None else set(ch for ch in AUDIT_SAMPLE if probe.fullmatch(ch))

    if op is constants.LITERAL or op is constants.NOT_LITERAL:
        chars = set(ch for ch in AUDIT_SAMPLE if chr(value) in folded(ch))
        return chars if op is constants.LITERAL else set(AUDIT_SAMPLE) - chars
    if op is constants.ANY:
        return set(ch for ch in AUDIT_SAMPLE if flags & re.S or ch != "\n")
    if op is constants.CATEGORY:
        return category(value)
    if op is not constants.IN:
        return None
    negate, chars = False, set()
    for item, argument in value:
        if item is constants.NEGATE:
            negate = True
        elif item is constants.LITERAL:
            chars |= set(ch for ch in AUDIT_SAMPLE if chr(argument) in folded(ch))
        elif item is constants.RANGE:
            chars |= set(ch for ch in AUDIT_SAMPLE if any(argument[0] <= ord(c) <= argument[1] for c in folded(ch)))
        elif item is constants.CATEGORY and category(argument) is not None:
            chars |= category(argument)
        else:
            return None
    return set(AUDIT_SAMPLE) - chars if negate else chars


def _audit_walk(parsed, flags, flagged, constants):
    """(nullable, first, last, heads, tails) of a parsed sequence, with every flagged construct appended to
    `flagged`. heads: the classes of the quantifiers that can open a match; tails: of those that can close one
    and give characters back."""
    infos = []
    for op, value in parsed:
        if op in (constants.MAX_REPEAT, constants.MIN_REPEAT, constants.POSSESSIVE_REPEAT):
            low, high, body = value
            inner = _audit_walk(body, flags, flagged, constants)
            if high > 1 and any(tail & head for tail in inner[4] for head in inner[3]):
                _audit_flag(flagged, "overlap")
            if high > 1 and inner[0]:
                _audit_flag(flagged, "empty")
            if (op is not constants.POSSESSIVE_REPEAT and high == constants.MAXREPEAT
                    and _audit_chooses(body, constants)):
                _audit_flag(flagged, "choice")
            heads = ([inner[1]] if high != low else []) + inner[3]
            tails = [] if op is constants.POSSESSIVE_REPEAT else ([inner[2]] if high != low else []) + inner[4]
            infos.append((low == 0 or inner[0], inner[1], inner[2], heads, tails))
        elif op is constants.SUBPATTERN:
            infos.append(_audit_walk(value[3], (flags | value[1]) & ~value[2], flagged, constants))
        elif op is constants.ATOMIC_GROUP:
            inner = _audit_walk(value, flags, flagged, constants)
            infos.append(inner[:4] + ([],))
        elif op is constants.BRANCH:
            branches = [_audit_walk(branch, flags, flagged, constants) for branch in value[1]]
            infos.append((any(branch[0] for branch in branches), set().union(*(branch[1] for branch in branches)),
                          set().union(*(branch[2] for branch in branches)),
                          [head for branch in branches for head in branch[3]],
                          [tail for branch in branches for tail in branch[4]]))
        elif op in (constants.ASSERT, constants.ASSERT_NOT):
            _audit_walk(value[1], flags, flagged, constants)
            infos.append((True, set(), set(), [], []))
        elif op is constants.AT:
            infos.append((True, set(), set(), [], []))
        else:
            chars = _audit_chars(op, value, flags, constants)
            if chars is None:
                _audit_flag(flagged, "unread", " ({})".format(op))
                chars = set(AUDIT_SAMPLE)
            infos.append((False, chars, chars, [], []))
    for index, left in enumerate(infos):
        for right in infos[index + 1:]:
            if any(tail & head for tail in left[4] for head in right[3]):
                _audit_flag(flagged, "pair")
            if not right[0]:
                break
    first, last, heads, tails = set(), set(), [], []
    for info in infos:
        first |= info[1]
        heads += info[3]
        if not info[0]:
            break
    for info in reversed(infos):
        last |= info[2]
        tails += info[4]
        if not info[0]:
            break
    return all(info[0] for info in infos), first, last, heads, tails


def audit_pattern(pattern, flags=0):
    """The sorted distinct constructs the audit flags in one pattern (empty when it passes)."""
    try:
        from re import _constants as constants, _parser as parser
    except ImportError:   # the parser is private to the interpreter: a release without it fails the audit closed
        return ["the interpreter's regular expression parser cannot be imported"]
    flagged = []
    _audit_walk(parser.parse(pattern, flags), flags, flagged, constants)
    return sorted(set(flagged))


def _audit_objects(namespace):
    """[(name, pattern)] for every compiled pattern reachable from `namespace`: a global, an item of a tuple, list,
    set, frozenset or dict global at any depth, or an attribute of a class defined in this module."""
    found, seen = [], set()

    def visit(name, value):
        if isinstance(value, re.Pattern):
            found.append((name, value))
            return
        if id(value) in seen:
            return
        if isinstance(value, (tuple, list)):
            seen.add(id(value))
            for index, item in enumerate(value):
                visit("{}[{}]".format(name, index), item)
        elif isinstance(value, (set, frozenset)):
            seen.add(id(value))
            for index, item in enumerate(sorted(value, key=repr)):
                visit("{}{{{}}}".format(name, index), item)
        elif isinstance(value, dict):
            seen.add(id(value))
            for key, item in value.items():
                visit("{}[{!r}]".format(name, key), item)
        elif isinstance(value, type) and value.__module__ == __name__:
            seen.add(id(value))
            for key, item in vars(value).items():
                visit("{}.{}".format(name, key), item)

    for name, value in sorted(namespace.items()):
        visit(name, value)
    return found


def _re_function(node):
    """The re function a call node calls directly (re.compile, re.search, ...), or None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "re" and node.func.attr in RE_CALLS):
        return node.func.attr
    return None


def _audit_flags_value(node):
    """The integer flags a flags argument names (re.I, re.I | re.S, an integer literal), or None."""
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "re"
            and isinstance(getattr(re, node.attr, None), re.RegexFlag)):
        return int(getattr(re, node.attr))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left, right = _audit_flags_value(node.left), _audit_flags_value(node.right)
        return None if left is None or right is None else left | right
    return None


def _audit_literal(node, function):
    """(pattern, flags) of a call whose pattern is a string literal and whose flags are absent or literal, or None."""
    position = RE_CALLS[function]
    flags = [keyword.value for keyword in node.keywords if keyword.arg == "flags"] + node.args[position:position + 1]
    if (not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str)
            or any(isinstance(arg, ast.Starred) for arg in node.args)
            or any(keyword.arg is None for keyword in node.keywords) or len(flags) > 1):
        return None
    value = _audit_flags_value(flags[0]) if flags else 0
    return None if value is None else (node.args[0].value, value)


def audit_source(source, namespace):
    """(sites, unseen, held) for the regular expression calls in `source` (the production code) that the namespace
    walk does not hold: sites [(name, pattern, flags)] read from a literal pattern argument, and unseen {name:
    [finding]} for a call whose pattern or flags is not a literal and for the other uses of re it recognizes (a
    matching function named without a call, a bare `re` passed or bound, `import re as`, an import from re). A
    module-level `NAME = re.compile(...)` whose NAME holds a compiled pattern in `namespace` is walked there and
    skipped here; held: the sorted names so skipped. It does not see re reached any other way (re.__dict__, re's
    private modules, __import__, importlib), and a NAME compiled twice is walked only for its last value."""
    tree = ast.parse(source)
    held, held_names, called, qualified, sites, unseen = set(), [], set(), set(), [], dict()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and _re_function(node.value) == "compile"
                and isinstance(namespace.get(node.targets[0].id), re.Pattern)):
            held.add(id(node.value))
            held_names.append(node.targets[0].id)
    nodes = list(ast.walk(tree))
    for node in nodes:
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "re":
            qualified.add(id(node.value))
        function = _re_function(node)
        if function is not None:
            called.add(id(node.func))
    for node in nodes:
        name = "line {}".format(getattr(node, "lineno", 0))
        function = _re_function(node)
        if function is not None:
            if id(node) in held:
                continue
            literal = _audit_literal(node, function)
            if literal is not None:
                sites.append((name,) + literal)
                continue
        elif not ((isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "re"
                   and node.attr in RE_CALLS and id(node) not in called)
                  or (isinstance(node, ast.Name) and node.id == "re" and id(node) not in qualified)
                  or (isinstance(node, ast.ImportFrom) and node.module == "re")
                  or (isinstance(node, ast.Import) and any(alias.name == "re" and alias.asname
                                                           for alias in node.names))):
            continue
        _audit_flag(unseen.setdefault(name, []), "unseen")
    return sites, dict((name, found) for name, found in unseen.items() if found), sorted(held_names)


def _production_source():
    """This module's source up to the PRODUCTION_END line, or None when that line is missing."""
    with open(Path(__file__).resolve(), "r", encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    for number, line in enumerate(lines):
        if line.startswith(PRODUCTION_END):
            return "\n".join(lines[:number]) + "\n"
    return None


def regex_audit():
    """(walked, flagged, read): the names of every pattern walked (namespace objects, COMMAND_FORMS, and the
    production calls the namespace does not hold, named by line), name -> flagged constructs for each one the audit
    flags that REGEX_AUDIT_EXEMPTIONS does not exempt (an exemption whose pattern is absent or passes is reported as
    stale), and the names of the module-level compiles the production source assigns and the namespace holds. A
    call the audit cannot read, or a missing production end line, is flagged too."""
    namespace = globals()
    patterns = [(name, value.pattern, value.flags) for name, value in _audit_objects(namespace)]
    patterns += [("COMMAND_FORMS[{}]".format(index), form, 0) for index, form in enumerate(COMMAND_FORMS)]
    flagged, read = dict(), []
    source = _production_source()
    if source is None:
        flagged["source"] = ["the production end line is missing, so the calls in the source are not read"]
    else:
        sites, unseen, read = audit_source(source, namespace)
        patterns += sites
        flagged.update(unseen)
    for name, pattern, flags in patterns:
        found = audit_pattern(pattern, flags)
        if found and name not in REGEX_AUDIT_EXEMPTIONS:
            flagged[name] = found
        elif name in REGEX_AUDIT_EXEMPTIONS and not found:
            flagged[name] = ["a stale exemption: the pattern passes"]
    walked = [name for name, _, _ in patterns]
    for name in sorted(set(REGEX_AUDIT_EXEMPTIONS) - set(walked)):
        flagged[name] = ["a stale exemption: no such pattern"]
    return walked, flagged, read


def _bounded_option_end(text, position, option_re, memo):
    """_option_end with a bound of six options put back (the round-4 git option rule)."""
    for _ in range(6):
        found = option_re.match(text, position)
        if not found:
            break
        position = found.end()
    return position


class _EveryWordSentence(Sentence):
    """A Sentence whose every word is a clause start (the clause-start rule patched out)."""

    def __init__(self, text):
        super().__init__(text)
        self.starts = [found.start() for found in re.finditer(r"\b\w", text)]


def _legacy_leg_spans(blocks, families):
    """leg_spans with the operative-position rule patched out: a marker anywhere claims the paragraph."""
    spans = []
    for index, block in enumerate(blocks):
        if block.kind == "prose" and not block.heading:
            for found in re.finditer(r"([A-Za-z][A-Za-z0-9-]*)\s+leg\s*:", block.text()):
                if found.group(1).lower() in families:
                    spans.append((index, found.start(), found.group(1).lower(), ""))
    return spans


def _legacy_scope_directives(text):
    """scope_directives with the clause-start rule patched out: a directive anywhere counts."""
    kept = None
    for found in ONLY_RE.finditer(text):
        if found.group(2):
            kept = (kept or set()) | parse_item_list(found.group(1))
    return kept, set()


class _AnyStartDemotions(Demotions):
    """Demotions with the command-start guard patched out: a command line that opens anywhere in the sentence is
    demoted when only arguments and the stop follow it."""

    def __call__(self, start, end):
        if self.prose is not None:
            self.prose = start
        return super().__call__(start, end)


class _NoFormatRemoval:
    """A unicodedata stand-in that reports no format character (format-character removal patched out); its normalize
    is the real one (near_pin folds a look-alike pin line with it)."""

    normalize = staticmethod(unicodedata.normalize)

    @staticmethod
    def category(ch):
        return "Cc" if ch in "\x00" else ("Zs" if ch.isspace() else "Ll")


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    # The expected execution set is read first: an unreadable checks manifest is a harness error
    # (exit 2) before any check runs, so a manifest vector can exercise this boundary through
    # main(--self-test) without re-entering the suite. Read later, that call would re-enter the suite,
    # whose first check() refuses its id as a duplicate and exits 2; _boundary reports that SystemExit
    # with its diagnostic as the command element, so the manifest vectors fail by name.
    expected = _expected_check_ids()
    if expected is None:
        return 2
    # ---------- the capability table ----------
    try:
        shipped = dict((name, row["execute"]) for name, row in load_table(ROOT / TABLE_REL).items())
    except CannotEvaluate as exc:
        shipped = str(exc)
    check("table/shipped-table-loads", shipped, dict(claude="yes", codex="read-only-sandbox", gemini="no"))
    try:
        outside = dict((name, row["reads-outside-workdir"]) for name, row in load_table(ROOT / TABLE_REL).items())
    except CannotEvaluate as exc:
        outside = str(exc)
    check("table/shipped-reads-outside-workdir", outside, dict(claude="undeclared", codex="undeclared", gemini="no"))
    check("table/reads-outside-workdir-missing-or-malformed-2",
          [_table_outcome(TABLE_FIXTURE.replace('reads-outside-workdir = "no"\n', "")),
           _table_outcome(TABLE_FIXTURE.replace('reads-outside-workdir = "no"', 'reads-outside-workdir = "maybe"')),
           _table_outcome(TABLE_FIXTURE.replace('reads-outside-workdir = "no"', "reads-outside-workdir = false"))],
          ["CannotEvaluate"] * 3)
    check("table/fixture-loads", _table_outcome(TABLE_FIXTURE), ["alpha", "beta", "gamma"])
    check("table/unknown-key-2",
          _table_outcome(TABLE_FIXTURE.replace('scratch = "none"\n', 'scratch = "none"\ncolour = "x"\n')),
          "CannotEvaluate")
    check("table/missing-key-2", _table_outcome(TABLE_FIXTURE.replace('network = "no"\n', "", 1)),
          "CannotEvaluate")
    check("table/value-outside-vocabulary-2",
          _table_outcome(TABLE_FIXTURE.replace('execute = "no"', 'execute = "sometimes"')), "CannotEvaluate")
    check("table/duplicate-family-2", _table_outcome(TABLE_FIXTURE.replace('"beta"', '"alpha"')),
          "CannotEvaluate")
    check("table/wrong-format-version-2",
          _table_outcome(TABLE_FIXTURE.replace("format-version = 1", "format-version = 2")), "CannotEvaluate")
    check("table/bool-format-version-2",
          _table_outcome(TABLE_FIXTURE.replace("format-version = 1", "format-version = true")), "CannotEvaluate")
    check("table/float-format-version-2",
          _table_outcome(TABLE_FIXTURE.replace("format-version = 1", "format-version = 1.0")), "CannotEvaluate")
    check("table/invalid-family-name-2", _table_outcome(TABLE_FIXTURE.replace('"beta"', '"Beta two"')),
          "CannotEvaluate")
    check("table/not-toml-2", _table_outcome("[[family]\n"), "CannotEvaluate")
    # a parse failure outside TOMLDecodeError (deep nesting recurses; an over-long integer exceeds the
    # int conversion limit; memory exhaustion is injected at the parse call) maps to the documented
    # refusal and exit 2 at both production boundaries, never a traceback (F-TOML-BARE-VALUEERROR-CLASS)
    check("table/deep-nesting-2", _table_refusal("x = " + "[" * 8000 + "]" * 8000),
          ((2, True, False), (2, True, False)))
    check("table/overlong-integer-2", _table_refusal("x = " + "1" * 5000),
          ((2, True, False), (2, True, False)))
    with _injected_memory_exhaustion():
        check("table/injected-memory-exhaustion-2",
              _table_refusal("x = 1  # " + INJECTED_EXHAUSTION_MARK + "\n"),
              ((2, True, False), (2, True, False)))
    # the injection seam restores both parsers on leaving its block, also when the block raises: the
    # SystemExit that check() raises on a duplicate id (raised here directly, since the execution gate
    # refuses a duplicate or indirect check() call site) and a MemoryError that escapes a parse
    check("table/injection-restored", _tomllib_parsers(), TOMLLIB_PARSERS)
    after_raise = []
    for raising in (SystemExit(2), MemoryError("injected memory exhaustion")):
        try:
            with _injected_memory_exhaustion():
                raise raising
        except (SystemExit, MemoryError) as exc:
            after_raise.append((type(exc).__name__, _tomllib_parsers()))
    check("table/injection-restored-after-raise", after_raise,
          [("SystemExit", TOMLLIB_PARSERS), ("MemoryError", TOMLLIB_PARSERS)])
    # ---------- structure ----------
    check("structure/dash-list-refused", _outcome(DASH_LIST),
          (1, [("R1", "ADVISORY"), ("R5", "ADVISORY"), ("S3", "HARD")]))
    check("structure/nested-list-refused",
          _outcome("1. Read the diff:\n   - check the names\n   - run `t.py --self-test`\n"),
          (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("structure/indented-fence-refused",
          _outcome("1. Steps for the check:\n\n    ```bash\n    python3 t.py\n    ```\n"), (1, [("S1", "HARD")]))
    # a format character outside code refuses the whole brief (exit 2) before any rule runs; inside a
    # fence or a code span it is removed, so the fenced command, and the code-span command a zero-width
    # space tried to split, are read and S1 or S3 fires
    check("structure/bom-refused", _outcome(BOM_BRIEF), "CannotEvaluate")
    check("structure/zero-width-split-refused", _outcome(ZERO_WIDTH_SPLIT), (1, [("S3", "HARD")]))
    check("structure/fenced-format-removed", _outcome(FENCED_ZERO_WIDTH), (1, [("S1", "HARD")]))
    check("structure/indented-code-refused", _outcome("    git show HEAD\n"), (1, [("S3", "HARD")]))
    # ---------- S1: an executable fence ----------
    check("s1/bash-fence-refused", _outcome(BASH_FENCE), (1, [("S1", "HARD")]))
    check("s1/pycon-fence-refused", _outcome(PYCON_FENCE), (1, [("S1", "HARD")]))
    check("s1/python-prompt-refused", _outcome(PYTHON_PROMPT_FENCE), (1, [("S1", "HARD")]))
    check("s1/untagged-command-fence-refused", _outcome(UNTAGGED_FENCE), (1, [("S1", "HARD")]))
    check("s1/plain-command-fence-refused", _outcome(PLAIN_FENCE), (1, [("S1", "HARD")]))
    check("s1/text-tagged-command-refused", _outcome("```text\npython3 t.py --self-test\n```\n"),
          (1, [("S1", "HARD")]))
    check("s1/first-line-forms-refused", [_outcome("```\n" + line + "\n```\n")[0] for line in (
        "python3.14 t.py --self-test", "FOO=1 python3 t.py", "make test", "/usr/bin/git show HEAD")], [1, 1, 1, 1])
    check("s1/python-fence-ok", _outcome("Steps:\n\n```python\nx = 1\n```\n"), (0, []))
    check("s1/fence-body-not-prose", _outcome("```text\nRun the parser.\n```\n"), (0, []))
    check("s1/reference-intro-info", _outcome(REFERENCE_FENCE), (0, [("S1", "INFO")]))
    check("s1/reference-until-refused", _outcome(REFERENCE_UNTIL), (1, [("S1", "HARD")]))
    check("s1/for-reference-refused", _outcome("For reference, run the following:\n\n```bash\npython3 t.py\n```\n"),
          (1, [("R1", "ADVISORY"), ("S1", "HARD")]))
    # ---------- S2 and S3: a command line, in a numbered item or outside ----------
    check("s2/tool-item-refused", _outcome(RUN_ITEM), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("s2/git-item-refused", _outcome(GIT_ITEM), (1, [("S2", "HARD")]))
    check("s2/dot-slash-refused", _outcome(DOT_SLASH), (1, [("S2", "HARD")]))
    check("s2/bash-c-refused", _outcome(BASH_C), (1, [("S2", "HARD")]))
    check("s2/pytest-refused", _outcome(PYTEST), (1, [("S2", "HARD")]))
    check("s2/span-tool-refused", _outcome(SPAN_TOOL), (1, [("S2", "HARD")]))
    check("s2/find-clause-not-demoted-refused", _outcome("1. Find the test by running `t.py --self-test`.\n"),
          (1, [("S2", "HARD")]))
    check("s2/quoted-span-not-demoted-refused", _outcome("1. Quote `git log --oneline` for the last commits.\n"),
          (1, [("S2", "HARD")]))
    check("s2/python-c-refused", _outcome(PYTHON_C), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("s2/python-m-refused", _outcome(PYTHON_M), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("s2/python-options-m-refused", _outcome(PYTHON_OPTIONS_M), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("s2/python-forms-refused", [_outcome("1. Use " + command + ".\n")[0] for command in (
        "python3 -X dev t.py", "python3 -W error t.py", "/usr/bin/python3 t.py", ".venv/bin/python t.py",
        "python3 -Ic pass")], [1, 1, 1, 1, 1])
    check("s2/git-forms-refused", [_outcome("1. Use " + command + ".\n")[0] for command in (
        "git -P log -1", "git --git-dir /r/.git log -1", "git show-branch", "git annotate t.py")], [1, 1, 1, 1])
    check("s2/quoted-relative-refused", _outcome(DESCRIBED_PROSE), (1, [("S2", "HARD")]))
    check("s2/mentions-quoted-refused", _outcome("1. Quote the sentence that mentions \"git branch --list\".\n"),
          (1, [("S2", "HARD")]))
    check("s2/prohibited-prose-info", _outcome(PROHIBITION_PROSE), (0, [("S2", "INFO")]))
    check("s2/past-report-prose-info", _outcome(PAST_PROSE), (0, [("S2", "INFO")]))
    check("s2/file-name-ok", _outcome("1. In `tools/t.py`, quote the parser.\n"), (0, []))
    check("s2/config-noun-ok", _outcome(CONFIG_NOUN), (0, []))
    check("s2/git-word-ok", _outcome("1. Quote the git history notes; Git shows them.\n"), (0, []))
    check("s2/span-word-ok", _outcome("1. Quote the `make` target and the `pytest` fixture.\n"), (0, []))
    check("s3/git-show-refused", _outcome(GIT_SHOW_PREAMBLE), (1, [("S3", "HARD")]))
    check("s3/git-show-prose-refused", _outcome(GIT_SHOW_PROSE), (1, [("S3", "HARD")]))
    check("s3/python-refused", _outcome(PYTHON_COMMAND), (1, [("S3", "HARD")]))
    check("s3/heading-refused", _outcome(HEADING_COMMAND), (1, [("S3", "HARD")]))
    check("s3/quoted-relative-refused", _outcome(QUOTE_SELF_TEST), (1, [("S3", "HARD")]))
    check("s3/past-report-info", _outcome(MENTION), (0, [("S3", "INFO")]))
    check("s3/prohibited-info", _outcome(PROHIBITION), (0, [("S3", "INFO")]))
    check("s3/prohibited-use-refused", _outcome("Never use `git show`; read the files you are given.\n"),
          (1, [("S3", "HARD")]))
    check("s3/prohibition-exception-refused", _outcome(PROHIBITION_EXCEPT), (1, [("S3", "HARD")]))
    check("s3/described-other-clause-refused", _outcome("Quote the line that runs it, then git show abc1234.\n"),
          (1, [("S3", "HARD")]))
    check("s3/git-options-unbounded", (bool(option_commands("git -C x --no-pager show")),
                                       bool(option_commands(GIT_OPTIONS))), (True, True))
    # ---------- quoted source text is not a data context: a command line in it refuses ----------
    check("quoted/scope-row-entry-refused", _outcome(QUOTED_ENTRY), (1, [("S2", "HARD")]))
    check("quoted/test-row-refused", _outcome(QUOTED_ROW), (1, [("S2", "HARD")]))
    check("quoted/docstring-example-refused", _outcome(QUOTED_EXAMPLE), (1, [("S2", "HARD")]))
    check("quoted/readme-command-refused", _outcome(QUOTED_README), (1, [("S2", "HARD")]))
    check("quoted/entry-that-names-refused", _outcome(QUOTED_NAMES), (1, [("S2", "HARD")]))
    check("quoted/continued-object-refused", _outcome(QUOTED_CONTINUED), (1, [("S2", "HARD")]))
    check("quoted/unnumbered-refused", _outcome(QUOTED_EXAMPLE[3:]), (1, [("S3", "HARD")]))
    check("quoted/git-show-preamble-refused", _outcome(QUOTE_GIT_SHOW_PREAMBLE), (1, [("S3", "HARD")]))
    check("quoted/nested-run-refused", _outcome(QUOTE_NESTED_RUN), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("quoted/output-of-refused", _outcome("1. Quote the output of `make test`.\n"), (1, [("S2", "HARD")]))
    check("quoted/what-prints-refused", _outcome("1. Quote what `git log` prints.\n"), (1, [("S2", "HARD")]))
    check("quoted/line-prints-refused", _outcome(QUOTE_PRINTS), (1, [("S2", "HARD")]))
    check("quoted/and-output-refused", _outcome(QUOTE_AND_OUTPUT), (1, [("S2", "HARD")]))
    check("quoted/line-of-refused", _outcome(QUOTE_LINE_OF), (1, [("S2", "HARD")]))
    check("quoted/no-source-noun-refused", _outcome(QUOTE_NO_NOUN), (1, [("S2", "HARD")]))
    check("quoted/and-command-refused", _outcome(QUOTE_AND_COMMAND), (1, [("S2", "HARD")]))
    check("quoted/and-run-refused", _outcome("1. Quote the docstring example `git show HEAD` and run it.\n"),
          (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("quoted/then-run-refused", _outcome(QUOTE_THEN_RUN), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    # ---------- closed shapes: anything after the command line, or a request in the item, voids them ----------
    check("demote/two-prohibitions-info", _outcome(TWO_PROHIBITIONS), (0, [("S2", "INFO")]))
    check("demote/closed-with-answer-info", _outcome(CLOSED_ANSWER), (0, [("S2", "INFO")]))
    check("demote/past-report-requests-refused", [_outcome(text)[0] for text in (
        PAST_CONFIRM, PAST_AND_CHECK, PAST_SO_SEE, PAST_TRY_AGAIN)], [1, 1, 1, 1])
    check("demote/prohibition-requests-refused", [_outcome(text)[0] for text in (
        PROHIBITION_PERFORM, PROHIBITION_KIDDING)], [1, 1])
    check("demote/confirm-after-past-report-refused", [_outcome(text)[0] for text in (
        PAST_CONFIRM_PRINTS, PAST_CONFIRM_PASSES)], [1, 1])
    check("demote/text-after-command-refused", [_outcome(text)[0] for text in (
        PROHIBITION_EVEN_IF, PROHIBITION_AND_REPORT)], [1, 1])
    check("demote/void-forms-refused", [_outcome(text)[0] for text in (
        VOID_CONTINUATION, VOID_QUESTION, VOID_ADVISORY, VOID_NESTED, VOID_DASH, VOID_SUB_BLOCK, VOID_PARAGRAPH)],
          [1, 1, 1, 1, 1, 1, 1])
    check("demote/heading-title-info", _outcome(HEADING_TITLE), (0, [("S3", "INFO")]))
    check("demote/other-item-not-voided-residual", _outcome(OTHER_ITEM), (0, [("R1", "ADVISORY"), ("S2", "INFO")]))
    check("demote/only-but-also-refused", _outcome(ONLY_BUT_ALSO), (1, [("S2", "HARD")]))
    check("demote/ignore-instruction-refused", _outcome(IGNORE_INSTRUCTION), (1, [("S1", "HARD")]))
    check("demote/run-it-on-branch-refused", _outcome(RUN_ON_BRANCH), (1, [("S1", "HARD")]))
    check("demote/intro-after-request-refused", _outcome(INTRO_AFTER_BUILD), (1, [("R1", "ADVISORY"), ("S1", "HARD")]))
    check("demote/two-verbs-refused", _outcome(TWO_VERBS), (1, [("S2", "HARD")]))
    check("demote/exception-words-refused", [_outcome(text)[0] for text in (
        "1. If you cannot run `t.py --self-test`, say so; otherwise paste its output.\n",
        "1. Do not run `t.py --self-test` more than once, and report its exit code.\n",
        "1. Don't use `git show` alone; use it with --stat.\n",
        "1. Do not use `git show abc` but rather its --stat form.\n",
        "1. Never call `t.py --self-test` directly; call it through tox and report.\n")], [1, 1, 1, 1, 1])
    check("demote/request-not-past-report-refused", [_outcome(text)[0] for text in (
        "1. Confirm the fix passed with `pytest -q tests/`.\n",
        "1. Paste the output from when you ran `pytest -q tests/`.\n")], [1, 1])
    check("demote/void-nesting-refused", [_outcome(text) for text in (
        LAZY_LINE, LAZY_CONFIRM, FENCE_REQUEST, NESTED_NUMERAL)],
          [(1, [("R1", "ADVISORY"), ("S2", "HARD")]), (1, [("S2", "HARD")]), (1, [("S2", "HARD")]),
           (1, [("R1", "ADVISORY"), ("S2", "HARD")])])
    check("demote/void-added-words-refused", [_outcome(text)[0] for text in (RETRY, KIDDING_PASTE, TEST_IT, FIX_IT)],
          [1, 1, 1, 1])
    check("demote/void-item-heading-refused", [_outcome(ITEM_HEADING), _outcome(ITEM_HEADING_BLANK)],
          [(1, [("S2", "HARD")]), (1, [("R1", "ADVISORY"), ("S2", "HARD")])])
    check("demote/void-retest-start-refused", _outcome(RETEST_IT), (1, [("S2", "HARD")]))
    check("demote/span-clause-start-info", _outcome(SPAN_CLAUSE_START), (0, [("S2", "INFO")]))
    check("demote/lead-in-after-list-not-voided-residual", [_outcome(text) for text in (
        LEAD_IN_REQUEST, AFTER_LIST_REQUEST, AFTER_LIST_BULLET)], [(0, [("S2", "INFO")])] * 3)
    check("demote/test-noun-info", _outcome(TEST_NOUN), (0, [("S2", "INFO")]))
    check("demote/markup-head-info", [_outcome(DASH_PROHIBITION), _outcome(PAREN_PROHIBITION)],
          [(0, [("S3", "INFO")]), (0, [("S2", "INFO")])])
    check("demote/argument-command-refused", _outcome(ARGUMENT_COMMAND), (1, [("S2", "HARD")]))
    check("demote/other-heading-not-voided-residual", _outcome(OTHER_HEADING), (0, [("S3", "INFO")]))
    check("demote/argument-walk-forms", [arguments_then_stop(tail, 0) for tail in (
        "", ".", " -n 1.", " abc1234:t.py.", " --format=%H HEAD~1 .", " main.", " -x.y!", " -x .", ":x.", " -a",
        " 1/ x.", " 1/ 2.  ")], [False, True, True, True, False, False, False, False, False, False, False, True])
    # ---------- time: the regex audit and the measured shapes ----------
    check("perf/word-run-token-start", WORD_RUN_RE.search("aaaa, b", 1), None)
    walked, flagged, read = regex_audit()
    check("perf/regex-audit-clean", flagged, dict())
    # only the real production source assigns every module-level pattern: an empty or wrong source reads none of them
    check("perf/regex-audit-reads-production", (bool(read), read), (True, sorted(
        name for name, value in globals().items() if isinstance(value, re.Pattern))))
    check("perf/regex-audit-walks-module", (len(walked) == len(set(walked)), set(
        name for name, value in globals().items() if isinstance(value, re.Pattern)) <= set(walked),
        set(REGEX_AUDIT_CANARIES) <= set(walked)), (True, True, True))
    check("perf/regex-audit-detects", [bool(audit_pattern(pattern)) for pattern in AUDIT_FLAGGED + AUDIT_CLEAN],
          [True] * len(AUDIT_FLAGGED) + [False] * len(AUDIT_CLEAN))
    check("perf/regex-audit-guards-isolated", [audit_pattern(AUDIT_GUARD_CANARIES[guard]) for guard in AUDIT_GUARDS
                                               if guard in AUDIT_GUARD_CANARIES],
          [[AUDIT_GUARDS[guard] + (" (GROUPREF)" if guard == "unread" else "")] for guard in AUDIT_GUARDS
           if guard in AUDIT_GUARD_CANARIES])
    sites, unseen, _ = audit_source(AUDIT_SOURCE_CANARY, dict())
    check("perf/regex-audit-reads-source", ([name for name, _, _ in sites], sorted(unseen),
                                            [bool(audit_pattern(pattern, flags)) for _, pattern, flags in sites]),
          (["line 2", "line 5"], ["line 11", "line 14", "line 16", "line 17", "line 20", "line 23", "line 26", "line 29",
                     "line 8"],
           [True, True]))
    probe_class = type("_AuditProbeClass", (), dict(X=re.compile(r"\w+\w*")))
    with _patched(_AUDIT_PROBE=(re.compile(r"(?:a|ab)*c"),), _AUDIT_PROBE_MAP=dict(k=[re.compile(r"\s*\s+")]),
                  _AUDIT_PROBE_SET=frozenset([re.compile(r"(?:a+){2,5}")]), _AUDIT_PROBE_CLASS=probe_class):
        probed = sorted(name for name in regex_audit()[1] if name.startswith("_AUDIT_PROBE"))
    check("perf/regex-audit-reads-containers", probed, ["_AUDIT_PROBE[0]", "_AUDIT_PROBE_CLASS.X",
                                                        "_AUDIT_PROBE_MAP['k'][0]", "_AUDIT_PROBE_SET{0}"])
    with _patched(LINEAR_CHILD="import sys\nprint(0.1)\nprint(0.2)\nsys.exit(9)\n"):
        failed_child = _linear("a")
    with _patched(LINEAR_CHILD="print('nan')\nprint('nan')\n"):
        nan_child = _linear("a")
    check("perf/failed-child-not-linear", (failed_child is True, nan_child is True), (False, False))
    for check_id, unit, prefix, suffix in (
            ("perf/single-word-linear", "a", "", ""),
            ("perf/noun-run-linear", "a, ", "", ""),
            ("perf/git-options-linear", "git -c ", "", ""),
            ("perf/python-options-linear", "python3 -X ", "", ""),
            ("perf/prohibited-span-linear", "do not run `git status` and ", "", ""),
            ("perf/prohibited-prose-linear", "Do not run git show ", "", ""),
            ("perf/prohibited-item-linear", "never run git log ", "1. ", ""),
            ("perf/report-and-linear", "report and ", "", ""),
            ("perf/report-comma-linear", "report, ", "", ""),
            ("perf/tail-marker-linear", "   `a` (zeta leg)\n", "1. x\n", ""),
            ("perf/leg-marker-linear", "Alpha leg: x.\n", "", ""),
            ("perf/markup-run-linear", "1) ", "", ""),
            ("perf/address-markup-linear", "Q1. ", "Alpha leg: x. ", ""),
            ("perf/observe-run-linear", "observe, ", "", ""),
            ("perf/outcome-subject-linear", "does, ", "", " the test pass?"),
            ("perf/quote-context-linear", "Quote the line for `git show` and ", "", ""),
            ("perf/whitespace-run-linear", " ", "a, b", " x"),
            ("perf/item-list-whitespace-linear", " ", "Gamma leg: answer items 1,", " x"),
            ("perf/closed-args-linear", " a/b", "Do not run git log", " x"),
            ("perf/void-word-linear", "confirm ", "1. CI failed on `make test`.\n   ", ""),
            # failing suffixes for the argument walk: each token opens with - and holds a mark, and the tail fails
            ("perf/closed-args-fail-linear", " -x.y", "Never run git log", "!"),
            ("perf/closed-args-no-stop-linear", " -x/y", "Never run git log", ""),
            ("perf/closed-args-option-linear", " --a=b", "Never run git log", " x"),
            ("perf/fence-contents-linear", "confirm, ", "1. CI failed on `make test`.\n\n   ```text\n", ""),
            # the preflight path scans: many paths on one line, and one path with many components
            ("perf/preflight-paths-linear", " /w/x/a.md d/b.md", "The workdir is /w/r.\n", ""),
            ("perf/preflight-long-path-linear", "a/", "The workdir is /w/r. Paste the whole diff of /", ".md"),
            # the P5 reach scans, with a confirm-clause pin so the whole rule runs: many reaches on one line, and
            # one reach with a long run of commit-id words
            ("perf/p5-clone-branch-linear", "git clone -b x ", "Confirm HEAD is 1234567abc.\n", ""),
            ("perf/p5-checkout-hex-linear", " 1234567abc", "Confirm HEAD is 1234567abc.\ngit checkout", ""),
            # many prohibitions, each closed by a stop behind closing markup, with stops inside code spans between
            # them; and many checkouts of FETCH_HEAD, each inheriting the fetch before it
            ("perf/p5-prohibition-scope-linear", "never git clone -b x.** `a; b.` ", "Confirm HEAD is 1234567abc.\n",
             ""),
            ("perf/p5-fetch-head-linear", "git fetch o x && git checkout FETCH_HEAD && ", "Confirm HEAD is "
             "1234567abc.\n", ""),
            # many prohibited objects; many cure lines, each after a clone, and one cure line whose code span chains
            # many clones; one line joined from many backslash continuations, and one from many git words at line
            # ends; many quoted option values; many HTML comment markers on one line and over many lines, with fence
            # markers between them
            ("perf/p5-prohibition-object-linear", "never run `git clone -b x` and ", "Confirm HEAD is 1234567abc.\n",
             ""),
            ("perf/p5-cure-comment-linear", "<!-- a --> <!--\n```\n-->\n```\n", "Confirm HEAD is 1234567abc.\n", ""),
            ("perf/p5-cure-line-linear", "git clone x w\n`git -C w checkout --detach 1234567abc`\n", "Confirm HEAD is "
             "1234567abc.\n", ""),
            ("perf/p5-cure-chain-linear", "git clone x w && ", "Confirm HEAD is 1234567abc.\nRun `",
             "git -C w checkout --detach 1234567abc`\n"),
            ("perf/p5-line-join-linear", "git -C w \\\n", "Confirm HEAD is 1234567abc.\n", "checkout main\n"),
            ("perf/p5-git-word-join-linear", "Run git\n", "Confirm HEAD is 1234567abc.\n", "clone x\n"),
            ("perf/p5-quoted-option-linear", "git -C \"a b\" checkout x && ", "Confirm HEAD is 1234567abc.\n", "")):
        check(check_id, _linear(unit, prefix, suffix), True)
    # ---------- the advisory rules: printed, never refusing ----------
    check("r1/run-prose-advisory", _outcome(RUN_PROSE), (0, [("R1", "ADVISORY")]))
    check("r1/negated-ok", _outcome(NEGATED), (0, []))
    check("r1/mid-clause-verb-ok", _outcome(CLAUSE_MID), (0, []))
    check("r1/and-joined-advisory", _outcome("Read the diff and run the parser.\n"), (0, [("R1", "ADVISORY")]))
    check("r1/passive-advisory", _outcome("The parser must be run before you answer.\n"), (0, [("R1", "ADVISORY")]))
    check("r1/noun-phrase-ok", _outcome("Build failures in CI are listed in the log.\n\nRun order: which gate "
                                        "comes first?\n\nInstall steps are documented.\n"), (0, []))
    check("r1/heading-not-a-clause-ok", _outcome(HEADING_NOUN), (0, []))
    check("r1/parenthesis-noun-ok", _outcome(PAREN_NOUN), (0, []))
    check("r1/adjective-parenthesis-noun-ok", _outcome(ADJECTIVE_NOUN), (0, []))
    check("r1/oxford-parenthesis-ok", _outcome(OXFORD_PARENTHESIS), (0, []))
    check("r1/opening-parenthesis-advisory", _outcome(OPENING_PARENTHESIS), (0, [("R1", "ADVISORY")]))
    check("r1/or-noun-list-ok", _outcome(OR_LIST), (0, []))
    check("r1/punctuated-noun-list-ok", _outcome(PUNCTUATED_LIST), (0, []))
    check("r1/verb-in-code-span-ok", _outcome("See the `build and run` target.\n"), (0, []))
    check("negation/slice-edge-advisory", _outcome(SLICE_EDGE), (0, [("R1", "ADVISORY")]))
    check("negation/window-near-ok", _outcome(NEAR_NEGATION), (0, []))
    check("negation/window-far-advisory", _outcome(FAR_NEGATION), (0, [("R1", "ADVISORY")]))
    check("negation/then-advisory", _outcome(THEN_NEGATION), (0, [("R1", "ADVISORY")]))
    check("negation/subordinate-comma-advisory", _outcome(SUBORDINATE), (0, [("R1", "ADVISORY")]))
    check("r5/exit-code-advisory", _outcome(RESULT), (0, [("R5", "ADVISORY")]))
    check("r5/negated-ok", _outcome(RESULT_NEGATED), (0, []))
    check("r5/static-source-ok", _outcome(STATIC_RESULT), (0, []))
    check("r6/named-tool-advisory", _outcome(OUTCOME_FLAG), (1, [("R6", "ADVISORY"), ("S3", "HARD")]))
    check("r6/unnamed-advisory", _outcome(OUTCOME_UNNAMED), (0, [("R6", "ADVISORY")]))
    check("r6/relative-clause-ok", _outcome(RELATIVE), (0, []))
    check("r7/commit-parents-advisory", _outcome(GIT_PARENTS), (0, [("R7", "ADVISORY")]))
    check("r7/commit-sha-advisory", _outcome("What is the SHA of the commit?\n"), (0, [("R7", "ADVISORY")]))
    check("r7/which-commit-advisory", _outcome("1. Which commit added the helper?\n"), (0, [("R7", "ADVISORY")]))
    check("r7/marker-exempts-nothing",
          _outcome("1. Which commit added the helper? This item is orchestrator-answered.\n"),
          (0, [("R7", "ADVISORY")]))
    check("r7/code-span-ok", _outcome("Quote `which commit added` from the docstring.\n"), (0, []))
    check("r7/diff-of-strings-ok", _outcome("Quote the line that formats the diff between two strings.\n"), (0, []))
    check("r7/statement-ok", _outcome(GIT_STATEMENT), (0, []))
    check("r7/executing-family-ok", (_outcome(GIT_PARENTS, "alpha"), _outcome(RUN_ITEM, "beta")),
          ((0, []), (0, [])))
    # ---------- scope ----------
    check("scope/answer-only-ok", _outcome(SCOPED), (0, []))
    check("scope/answer-only-unscoped-refused", _outcome(SCOPED, scoped=False),
          (1, [("R1", "ADVISORY"), ("R5", "ADVISORY"), ("S2", "HARD")]))
    check("scope/executing-families-ok", (_outcome(SCOPED, "alpha", False), _outcome(SCOPED, "beta", False)),
          ((0, []), (0, [])))
    check("scope/answer-only-items-ok", _outcome(ANSWER_ONLY), (0, []))
    check("scope/do-not-attempt-ok", _outcome(NOT_ATTEMPT), (0, []))
    check("scope/other-leg-dropped", _outcome(OTHER_LEG), (0, []))
    check("scope/other-leg-items-dropped", _outcome(OTHER_LEG_ITEMS), (0, []))
    check("scope/other-leg-fence-dropped", _outcome(OTHER_LEG_FENCE), (0, []))
    check("scope/other-leg-git-dropped", _outcome(OTHER_LEG_GIT), (0, []))
    check("scope/other-leg-section-dropped", _outcome(OTHER_SECTION), (0, []))
    check("scope/section-ends-refused", _outcome(SECTION_ENDS), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/tail-marker-dropped", _outcome(TAIL_MARKER), (0, []))
    check("scope/tail-marker-in-code-refused", _outcome(TAIL_IN_CODE), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/tail-marker-mid-line-refused", _outcome(TAIL_MID_LINE), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/tail-marker-in-wrapped-code-refused", _outcome(TAIL_IN_WRAPPED_CODE),
          (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/code-span-directive-refused", _outcome(CODE_SPAN_DIRECTIVE), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/own-leg-kept", _outcome("Gamma leg: run `t.py --self-test`.\n"), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/code-span-marker-refused", _outcome(CODE_SPAN_MARKER), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/mid-line-marker-refused", _outcome(MID_LINE_MARKER), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/quoted-directive-refused", _outcome(QUOTED_DIRECTIVE), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/address-ends-claim-refused", _outcome(ADDRESS_ENDS), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/all-legs-ends-claim-refused", _outcome(ALL_LEGS_ENDS), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/reversed-range-2", _outcome(REVERSED), "CannotEvaluate")
    check("scope/item-list-parsed", (parse_item_list("1, 2 and 4"), parse_item_list("Q1 to Q3"),
                                     parse_item_list("2-3, 5")), (set((1, 2, 4)), set((1, 2, 3)), set((2, 3, 5))))
    # ---------- cannot evaluate ----------
    check("cannot/undecodable-2", _outcome(b"Run \xff the parser.\n"), "CannotEvaluate")
    check("cannot/unknown-family-2", _outcome(RUN_ITEM, "delta"), "CannotEvaluate")
    check("cannot/blank-2", _outcome(" \n\n"), "CannotEvaluate")
    check("cannot/nul-blank-2", _outcome(b"\x00\x00\n"), "CannotEvaluate")
    check("cannot/zero-width-blank-2", _outcome(chr(0x200B) + chr(0xFEFF) + "\n"), "CannotEvaluate")
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-brief-capability-selftest-"))
    try:
        table_path = str(tmp / "table.toml")
        for name, body in (("table.toml", TABLE_FIXTURE), ("refuse.md", RUN_ITEM), ("ok.md", NEGATED),
                           ("quoted.md", QUOTED_README), ("preamble.md", GIT_SHOW_PREAMBLE),
                           ("fence.md", BASH_FENCE), ("large.md", "a" * (MAX_BYTES + 1))):
            with open(tmp / name, "w", encoding="utf-8") as handle:
                handle.write(body)
        tail = ["--family", "gamma", "--table", table_path]
        check("main/refuse-exit-1", _quiet(main, [str(tmp / "refuse.md")] + tail), 1)
        check("main/ok-exit-0", _quiet(main, [str(tmp / "ok.md")] + tail), 0)
        hinted = []
        for name in ("quoted.md", "preamble.md", "fence.md"):
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
                code_seen = main([str(tmp / name)] + tail)
            hinted.append((code_seen, COMMAND_LINE_HINT in printed.getvalue()))
        # a command-line refusal (S2, S3) says how to rewrite; an executable fence alone (S1) has no command line
        check("main/command-line-hint", hinted, [(1, True), (1, True), (1, False)])
        check("main/missing-brief-2", _quiet(main, [str(tmp / "absent.md")] + tail), 2)
        check("main/directory-brief-2", _quiet(main, [str(tmp)] + tail), 2)
        try:
            _read_regular(str(tmp), "brief")
            refused = "read"
        except CannotEvaluate as exc:
            refused = "not a regular file" in str(exc)
        check("main/directory-refused-by-type", refused, True)
        check("main/oversized-brief-2", _quiet(main, [str(tmp / "large.md")] + tail), 2)
        check("main/missing-table-2", _quiet(main, [str(tmp / "refuse.md"), "--family", "gamma", "--table",
                                                    str(tmp / "absent.toml")]), 2)
        check("main/no-family-2", _quiet(main, [str(tmp / "refuse.md")]), 2)
        check("main/unknown-flag-2", _quiet(main, [str(tmp / "refuse.md"), "--force"] + tail), 2)
        check("main/repeated-table-2", _quiet(main, [str(tmp / "ok.md"), "--table", table_path] + tail), 2)
        check("main/bare-shipped-table-0", _quiet(main, []), 0)
        os.makedirs(tmp / ".aiqt" / "core")
        with open(tmp / TABLE_REL, "w", encoding="utf-8") as handle:
            handle.write(TABLE_FIXTURE.replace('execute = "no"', 'execute = "sometimes"'))
        with _patched(ROOT=tmp):
            check("main/bare-malformed-table-2", _quiet(main, []), 2)
        # the checks manifest parse maps the same failures (memory exhaustion injected at the parse
        # call) to the harness refusal and None from the reader and to the refusal and exit 2 from the
        # self-test boundary, each judged on its own transcript, never a traceback
        for name, body in (("deep.toml", "x = " + "[" * 8000 + "]" * 8000),
                           ("longint.toml", "x = " + "1" * 5000),
                           ("exhausted.toml", "x = 1  # " + INJECTED_EXHAUSTION_MARK + "\n")):
            with open(tmp / name, "w", encoding="utf-8") as handle:
                handle.write(body)
        manifest_refused = ((None, True, False), (2, True, False))
        check("main/manifest-deep-nesting-harness-error", _manifest_refusal(tmp / "deep.toml"),
              manifest_refused)
        check("main/manifest-overlong-integer-harness-error", _manifest_refusal(tmp / "longint.toml"),
              manifest_refused)
        with _injected_memory_exhaustion():
            check("main/manifest-injected-memory-exhaustion-harness-error",
                  _manifest_refusal(tmp / "exhausted.toml"), manifest_refused)
        check("main/manifest-injection-restored", _tomllib_parsers(), TOMLLIB_PARSERS)
        # the silenced-command mutant (the opening manifest read of self_test with its stderr discarded):
        # the reader still prints its refusal, so each vector must go red on the command element alone
        silenced = []
        with _patched(self_test=_silenced_manifest_self_test):
            silenced.append(_manifest_refusal(tmp / "deep.toml"))
            silenced.append(_manifest_refusal(tmp / "longint.toml"))
            with _injected_memory_exhaustion():
                silenced.append(_manifest_refusal(tmp / "exhausted.toml"))
        check("main/manifest-silenced-command-red", silenced, [((None, True, False), (2, False, False))] * 3)
        check("main/manifest-silenced-injection-restored", _tomllib_parsers(), TOMLLIB_PARSERS)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # ---------- red on revert: each blocking rule, form and exemption patched out flips its fixture ----------
    nothing = lambda *args: []  # noqa: E731
    never = re.compile(r"(?!)")
    flipped = []

    def flip(cases, want_patched, **replacements):
        """(got, want) for check(): the (unpatched, patched) outcomes of each fixture, and the unpatched
        ones beside the outcomes the patch must produce."""
        before = [outcome() for outcome in cases]
        with _patched(**replacements):
            after = [outcome() for outcome in cases]
        flipped.append(sorted(replacements))
        if before == want_patched:   # a fixture the rule never changed proves nothing
            FAILURES.append("revert of %s: the unpatched outcome already equals the patched one"
                            % ", ".join(sorted(replacements)))
        return (before, after), (before, want_patched)

    def parser_flip(cases, want_patched, **replacements):
        """flip over the shared declaration parser: each named attribute of tools/_input_declarations.py
        is replaced for the patched run, then restored."""
        before = [outcome() for outcome in cases]
        saved = dict((name, getattr(_input_declarations, name)) for name in replacements)
        try:
            for name, value in replacements.items():
                setattr(_input_declarations, name, value)
            after = [outcome() for outcome in cases]
        finally:
            for name, value in saved.items():
                setattr(_input_declarations, name, value)
        flipped.append(sorted(replacements))
        if before == want_patched:   # a fixture the rule never changed proves nothing
            FAILURES.append("revert of %s: the unpatched outcome already equals the patched one"
                            % ", ".join(sorted(replacements)))
        return (before, after), (before, want_patched)

    def without_form(index):
        """COMMAND_RE with one command-line form removed."""
        return re.compile("|".join(form for number, form in enumerate(COMMAND_FORMS) if number != index))

    def code(text):
        return lambda: _outcome(text)[0]

    check("revert/s1-red", *flip([code(BASH_FENCE), code(UNTAGGED_FENCE)], [0, 0], rule_s1=lambda block: False))
    check("revert/s1-exec-tags-red", *flip([code(BASH_FENCE), code(PYCON_FENCE)], [0, 0],
                                          EXEC_FENCE_TAGS=frozenset()))
    check("revert/s1-python-prompt-red", *flip([code(PYTHON_PROMPT_FENCE)], [0], RUN_PROMPT_RE=never))
    check("revert/s1-first-line-red", *flip([code(UNTAGGED_FENCE), code(PLAIN_FENCE)], [0, 0],
                                           SHELL_FIRST_LINE_RE=never))
    check("revert/s1-reference-intro-red", *flip([code(REFERENCE_FENCE)], [1], reference_intro=lambda block: False))
    check("revert/s1-reference-except-red", *flip([code(REFERENCE_UNTIL), code(IGNORE_INSTRUCTION)], [0, 0],
                                                 EXCEPTION_RE=never))
    check("revert/s2-red", *flip([code(RUN_ITEM), code(GIT_ITEM), code(QUOTE_NESTED_RUN)], [0, 0, 0],
                                command_rule=lambda block: None if block.item is not None else "S3"))
    check("revert/s3-red", *flip([code(GIT_SHOW_PREAMBLE), code(GIT_SHOW_PROSE)], [0, 0],
                                command_rule=lambda block: "S2" if block.item is not None else None))
    check("revert/command-lines-red", *flip([code(GIT_SHOW_PREAMBLE), code(RUN_ITEM)], [0, 0], rule_commands=nothing))
    check("revert/form-git-red", *flip([code(GIT_SHOW_PREAMBLE), code(GIT_ITEM)], [0, 0], GIT_SUBCOMMAND_RE=never))
    check("revert/form-python-red", *flip([code(PYTHON_COMMAND)], [0], PYTHON_TARGET_RE=never))
    check("revert/python-target-c-m-red", *flip([code(PYTHON_C), code(PYTHON_M), code(PYTHON_OPTIONS_M)], [0, 0, 0],
                                               PYTHON_TARGET_RE=re.compile(r"\s+[\w./-]+\.py\b")))
    # the round-4 option loop, which consumed -c and -m as options
    check("revert/python-option-loop-red", *flip([code(PYTHON_C), code(PYTHON_M), code(PYTHON_OPTIONS_M)],
                                                [0, 0, 0], PYTHON_OPTION_RE=re.compile(r"\s+-[A-Za-z]+")))
    check("revert/form-path-flag-red", *flip([code(RUN_ITEM)], [0], COMMAND_RE=without_form(0)))
    check("revert/form-dot-slash-red", *flip([code(DOT_SLASH)], [0], COMMAND_RE=without_form(1)))
    check("revert/form-shell-c-red", *flip([code(BASH_C)], [0], COMMAND_RE=without_form(2)))
    check("revert/form-pytest-red", *flip([code(PYTEST)], [0], COMMAND_RE=without_form(3)))
    check("revert/form-span-tool-red", *flip([code(SPAN_TOOL)], [0], SPAN_COMMAND_RE=never))
    check("revert/git-config-noun-red", *flip([code(CONFIG_NOUN)], [1], GIT_SUBCOMMAND_RE=re.compile(
        GIT_SUBCOMMAND_RE.pattern.replace(GIT_CONFIG, "config"))))
    check("revert/git-options-unbounded-red", *flip([lambda: bool(option_commands(GIT_OPTIONS))], [False],
                                                   _option_end=_bounded_option_end))
    check("revert/past-report-red", *flip([code(MENTION), code(PAST_PROSE)], [1, 1], CLOSED_PAST_RE=never))
    check("revert/prohibited-red", *flip([code(PROHIBITION), code(PROHIBITION_PROSE)], [1, 1],
                                        CLOSED_PROHIBITION_RE=never))
    anything = re.compile("")
    check("revert/closed-span-end-red", *flip([code(PROHIBITION_EVEN_IF), code(PROHIBITION_PERFORM),
                                              code(PAST_AND_CHECK), code(PAST_SO_SEE)], [0, 0, 0, 0],
                                             CLOSED_SPAN_END_RE=anything))
    every_tail = lambda text, end: True  # noqa: E731
    check("revert/closed-args-end-red", *flip([code(PROHIBITION_AND_REPORT)], [0], arguments_then_stop=every_tail))
    check("revert/closed-stop-and-void-red", *flip([code(PAST_CONFIRM), code(PAST_TRY_AGAIN), code(PAST_CONFIRM_PRINTS),
                                                   code(PAST_CONFIRM_PASSES)], [0, 0, 0, 0],
                                                  CLOSED_SPAN_END_RE=anything, arguments_then_stop=every_tail,
                                                  VOID_WORD_RE=never))
    check("revert/closed-markup-head-red", *flip([code(DASH_PROHIBITION), code(PAREN_PROHIBITION)], [1, 1],
                                                closed_shape_head=lambda sentence: 0))
    check("revert/closed-command-start-red", *flip([code(ARGUMENT_COMMAND)], [0], Demotions=_AnyStartDemotions))
    check("revert/void-word-red", *flip([code(VOID_CONTINUATION), code(PROHIBITION_KIDDING), code(RETRY),
                                        code(KIDDING_PASTE)], [0, 0, 0, 0], VOID_WORD_RE=never))
    check("revert/void-start-word-red", *flip([code(TEST_IT), code(FIX_IT), code(RETEST_IT)], [0, 0, 0],
                                             VOID_START_RE=never))
    check("revert/void-lazy-line-red", *flip([code(LAZY_LINE), code(LAZY_CONFIRM)], [0, 0],
                                            lazy_continuation=lambda current: False))
    check("revert/void-nested-numeral-red", *flip([code(NESTED_NUMERAL)], [0], encloses=lambda top, indented: False))
    check("revert/void-item-heading-red", *flip([code(ITEM_HEADING), code(ITEM_HEADING_BLANK)], [0, 0],
                                                heading_in_item=lambda top, indented: False))
    check("revert/void-fence-red", *flip([code(FENCE_REQUEST)], [0], fence_asks=lambda block: False))
    check("revert/void-section-heading-red", *flip([code(OTHER_HEADING)], [1], starts_section=lambda block: False))
    check("revert/void-question-red", *flip([code(VOID_QUESTION)], [0], QUESTION_END_RE=never))
    check("revert/void-advisory-red", *flip([code(VOID_ADVISORY)], [0], ADVISORY_VOID_RULES=frozenset()))
    check("revert/void-group-red", *flip([code(VOID_NESTED), code(VOID_DASH), code(VOID_SUB_BLOCK),
                                         code(VOID_PARAGRAPH)], [0, 0, 0, 0],
                                        void_groups=lambda blocks: list(range(len(blocks)))))
    check("revert/void-cancel-red", *flip([code(VOID_CONTINUATION), code(VOID_NESTED)], [0, 0],
                                         cancel_demotion=lambda finding: finding))
    asks = sentence_asks
    check("revert/closed-exempt-red", *flip([code(PROHIBITION), code(TWO_PROHIBITIONS)], [1, 1],
                                           sentence_asks=lambda sentence, findings, _: asks(sentence, findings, False)))
    check("revert/heading-title-red", *flip([code(HEADING_TITLE)], [1], asks_in=lambda block: True))
    check("revert/exec-count-red", *flip([code(RUN_ON_BRANCH)], [0], EXEC_WORD_RE=re.compile(r"\A")))
    check("revert/intro-after-request-red", *flip([code(INTRO_AFTER_BUILD)], [0], asks_to_run=lambda findings: False))
    # the patterns of the previous revision that the audit flags, put back
    check("revert/regex-audit-red", *flip([lambda: sorted(regex_audit()[1])], [sorted(AUDIT_PREVIOUS)],
                                         **dict((name, re.compile(pattern, flags))
                                                for name, (pattern, flags) in AUDIT_PREVIOUS.items())))
    # each audit guard patched out alone: its isolated canary passes
    def audit_flip(guard):
        without = dict((name, message) for name, message in AUDIT_GUARDS.items() if name != guard)
        if guard in AUDIT_GUARD_CANARIES:
            case = lambda: audit_pattern(AUDIT_GUARD_CANARIES[guard])  # noqa: E731
        else:
            case = lambda: sorted(audit_source(AUDIT_SOURCE_CANARY, dict())[1])  # noqa: E731
        return flip([case], [[]], AUDIT_GUARDS=without)

    check("revert/audit-pair-red", *audit_flip("pair"))
    check("revert/audit-overlap-red", *audit_flip("overlap"))
    check("revert/audit-empty-red", *audit_flip("empty"))
    check("revert/audit-choice-red", *audit_flip("choice"))
    check("revert/audit-unread-red", *audit_flip("unread"))
    check("revert/audit-unseen-red", *audit_flip("unseen"))
    check("revert/word-run-anchor-red", *flip(
        [lambda: bool(WORD_RUN_RE.search("aaaa, b", 1))], [True],
        WORD_RUN_RE=re.compile(NOUN_WORD + r"(?:\s*,\s*" + NOUN_WORD + r")++")))
    check("revert/list-structure-red", *flip([lambda: _outcome("- Read the diff\n- Run the parser\n")], [(0, [])],
                                            LIST_LINE_RE=never))
    check("revert/format-characters-red", *flip([code(FENCED_ZERO_WIDTH)], [0],
                                               unicodedata=_NoFormatRemoval))
    check("revert/capability-gate-red", *flip([lambda: _outcome(RUN_ITEM, "alpha")[0],
                                              lambda: _outcome(RUN_ITEM, "beta")[0]], [1, 1],
                                             can_execute=lambda row: False))
    check("revert/scope-directives-red", *flip([code(SCOPED), code(NOT_ATTEMPT)], [1, 1],
                                              scope_directives=lambda text: (None, set())))
    check("revert/scope-directive-position-red", *flip([code(QUOTED_DIRECTIVE)], [0],
                                                      scope_directives=_legacy_scope_directives))
    check("revert/scope-legs-red", *flip([code(OTHER_LEG), code(OTHER_LEG_ITEMS), code(OTHER_SECTION),
                                         code(OTHER_LEG_GIT)], [1, 1, 1, 1], leg_spans=nothing))
    check("revert/scope-operative-marker-red", *flip([code(CODE_SPAN_MARKER), code(MID_LINE_MARKER)], [0, 0],
                                                    leg_spans=_legacy_leg_spans))
    check("revert/scope-address-red", *flip([code(ADDRESS_ENDS), code(ALL_LEGS_ENDS)], [0, 0], _addresses=nothing))
    check("revert/scope-reversed-range-red", *flip([lambda: _outcome(REVERSED)], [(0, [])],
                                                  parse_item_list=lambda spec: set()))
    check("revert/scope-tail-marker-red", *flip([code(TAIL_MARKER)], [1], tail_marker_family=lambda *args: None))
    check("revert/scope-red", *flip([code(SCOPED)], [1], apply_scope=lambda blocks, *args: blocks))
    widened = dict(TABLE_VOCABULARY)
    widened["execute"] = TABLE_VOCABULARY["execute"] + ("sometimes",)
    sometimes = TABLE_FIXTURE.replace('execute = "no"', 'execute = "sometimes"')
    check("revert/table-vocabulary-red", *flip(
        [lambda: _table_outcome(sometimes)], [["alpha", "beta", "gamma"]], TABLE_VOCABULARY=widened))
    # the advisory rules print what they find; each patched out drops its line
    check("revert/r1-red", *flip([lambda: _outcome(RUN_PROSE)], [(0, [])], rule_r1=nothing))
    check("revert/r1-negation-red", *flip([lambda: _outcome(NEGATED), lambda: _outcome(RESULT_NEGATED)],
                                         [(0, [("R1", "ADVISORY")]), (0, [("R5", "ADVISORY")])], NEGATION_RE=never))
    check("revert/negation-hard-break-red", *flip([lambda: _outcome(THEN_NEGATION), lambda: _outcome(SUBORDINATE)],
                                                 [(0, []), (0, [])], HARD_BREAK_RE=never, SUBORDINATE_RE=never))
    check("revert/r1-clause-start-red", *flip([lambda: _outcome(CLAUSE_MID)], [(0, [("R1", "ADVISORY")])],
                                             Sentence=_EveryWordSentence))
    check("revert/r1-heading-red", *flip([lambda: _outcome(HEADING_NOUN)], [(0, [("R1", "ADVISORY")])],
                                        r1_applies=lambda block: True))
    check("revert/noun-parenthesis-red", *flip([lambda: _outcome(PAREN_NOUN), lambda: _outcome(ADJECTIVE_NOUN)],
                                              [(0, [("R1", "ADVISORY")]), (0, [("R1", "ADVISORY")])],
                                              NOUN_PARENTHESIS_RE=never))
    check("revert/noun-list-red", *flip([lambda: _outcome(OR_LIST), lambda: _outcome(PUNCTUATED_LIST)],
                                       [(0, [("R1", "ADVISORY")]), (0, [("R1", "ADVISORY")])],
                                       NOUN_LIST_TAIL_RE=never, LED_LIST_TAIL_RE=never))
    check("revert/r1-code-span-red", *flip([lambda: _outcome("See the `build and run` target.\n")],
                                          [(0, [("R1", "ADVISORY")])], code_spans=nothing))
    check("revert/r5-red", *flip([lambda: _outcome(RESULT)], [(0, [])], rule_r5=nothing))
    check("revert/r5-static-source-red", *flip([lambda: _outcome(STATIC_RESULT)], [(0, [("R5", "ADVISORY")])],
                                              STATIC_SOURCE_RE=never))
    check("revert/r6-red", *flip([lambda: _outcome(OUTCOME_UNNAMED)], [(0, [])], rule_r6=nothing))
    check("revert/r6-relative-red", *flip([lambda: _outcome(RELATIVE)], [(0, [("R6", "ADVISORY")])],
                                         R6_RELATIVE_RE=never))
    check("revert/r7-red", *flip([lambda: _outcome(GIT_PARENTS)], [(0, [])], rule_r7=nothing))
    check("revert/r7-request-red", *flip([lambda: _outcome(GIT_STATEMENT)], [(0, [("R7", "ADVISORY")])],
                                        _asks=lambda sentence: True))
    # ---------- preflight: the input paths a brief names (P1 to P4), each quiet on its clean shape ----------
    def p2(path, family="gamma"):
        return _outcome(P2_BRIEF.format(path), family)

    check("preflight/p1-relative-advisory", (_outcome(P1_RELATIVE, "alpha"), _outcome(P1_RELATIVE)),
          ((0, [("P1", "ADVISORY")]), (0, [("P1", "ADVISORY")])))
    check("preflight/p1-clean-quiet", [_outcome(text, "alpha") for text in (P1_ABSOLUTE, P1_NO_WORKDIR, P1_NOT_A_PATH)],
          [(0, [])] * 3)
    check("preflight/p2-outside-hard", (_outcome(P2_OUTSIDE), _outcome(P2_BRIEF.format("/w/repo/../x/a.md"))),
          ((1, [("P2", "HARD")]), (1, [("P2", "HARD")])))
    # the workdir boundary: just inside, exactly at, and just outside it on both sides of the name
    check("preflight/p2-workdir-boundary",
          [p2(path)[0] for path in ("/w/repo/a.md", "/w/repo", "/w/repo/", "/w/repository/a.md", "/w/rep/a.md")],
          [0, 0, 0, 1, 1])
    check("preflight/p2-clean-quiet", (p2("/w/repo/docs/notes.md"), _outcome(P2_NO_WORKDIR),
                                       _outcome(P2_OUTSIDE, "alpha"), _outcome(P2_OUTSIDE, "beta")), ((0, []),) * 4)
    check("preflight/p2-declaration-prose-hard", [_outcome(text) for text in (P2_SUCH_AS, P2_OR_PATH, P2_ALSO_MID)],
          [(1, [("P2", "HARD")])] * 3)
    # is just before a space or the line end links the lead to its path; is. is not a link (a label: unclear)
    check("preflight/p2-declaration-is-line-end",
          [_outcome(form.format(path))[0] for form in P2_IS_END for path in ("/w/repo/a.md", "/etc/x.md")],
          [0, 1] * 4)
    check("preflight/p2-workdir-label-cannot-evaluate",
          [_outcome(text) for text in P2_LABEL + (P2_ALSO,)] + [_outcome(P2_LABEL[0], "beta")],
          ["CannotEvaluate"] * 6 + [(0, [])])
    check("preflight/p2-workdir-label-boundary-quiet", [_outcome(text) for text in P2_NOT_LABEL], [(0, [])] * 2)
    check("preflight/p2-declaration-unclear-cannot-evaluate",
          [_outcome(text) for text in (P2_TWO, P2_SPACE_UNQUOTED, P2_BARE_END)] + [_outcome(P2_TWO, "beta")],
          ["CannotEvaluate"] * 3 + [(0, [])])
    check("preflight/p2-declaration-forms",
          [_outcome(form.format(path))[0] for form, inside, outside in (
              (P2_QUOTED, "\"/w/my repo/x.md\"", "/w/my other/x.md"), (P2_NEXT_LINE, "/w/repo/a.md", "/etc/x.md"),
              (P2_COLON, "/w/repo/a.md", "/etc/x.md")) for path in (inside, outside)] + [_outcome(P2_SAME_TWICE)[0]],
          [0, 1] * 3 + [0])
    # the whole word is judged: an escape after `//`, `~`, an unquoted space, inside quotes or later in the word
    check("preflight/p2-path-word-hard",
          [p2(path)[0] for path in P2_ESCAPES + ("\"/w/repo/a b/../../../etc/x.md\"", "/w/repo/a.md,/etc/x.md")],
          [1] * 5)
    check("preflight/p2-path-word-clean-quiet",
          [p2(path) for path in ("/etc//../w/repo/a.md", "(/w/repo/a.md).", "`/w/repo/./a.md`,",
                                 "/w/repo/a.md,/w/repo/b.md", "/w/repo/a..", "--input=/w/repo/a.md")], [(0, [])] * 6)
    check("preflight/p2-glued-quote-hard", [p2(path)[0] for path in P2_GLUED_QUOTE], [1] * 2)
    check("preflight/p2-glued-after-hard", [p2(path)[0] for path in P2_GLUED_AFTER + ("--input=/etc/x.md",)],
          [1] * 5)
    check("preflight/p2-file-url-hard", p2(P2_FILE_URL), (1, [("P2", "HARD")]))
    check("preflight/p2-web-url-quiet", [p2(P2_WEB_URL), p2("(<HTTPS://example.com/x>)")], [(0, [])] * 2)
    check("preflight/p2-over-refusal-hard", [p2(path)[0] for path in P2_OVER_REFUSED], [1] * 4)
    # a stop after /.. must not fold it into the directory itself; a name that only ends in .. is not /..
    check("preflight/p2-dotdot-stop-hard", [p2(path)[0] for path in ("/w/repo/..", "/w/repo/...", "/w/repo/..).")],
          [1] * 3)
    check("preflight/p2-wide-workdir-cannot-evaluate",
          [_outcome(text) for text in P2_WIDE + ("Workdir /\nRead /etc/x.md.\n", "Workdir //\nRead /etc/x.md.\n")]
          + [_outcome(P2_WIDE[0], "beta")], ["CannotEvaluate"] * 5 + [(0, [])])
    check("preflight/p2-wide-workdir-boundary-quiet", [_outcome(text) for text in P2_NOT_WIDE], [(0, [])] * 2)
    check("preflight/p2-unplaced-hard", [p2(path)[0] for path in P2_UNPLACED], [1] * 4)
    # the word cap: just inside, exactly at, and just outside MAX_WORD_PATHS absolute paths in one word
    check("preflight/p2-word-cap-boundary", [p2(",".join(["/w/repo/a.md"] * count))[0] for count in (15, 16, 17)],
          [0, 0, 1])
    check("preflight/p2-relative-not-climbing-quiet", [p2(path)[0] for path in ("docs/../docs/a.md", "a/b/../c.md")],
          [0, 0])
    check("preflight/p2-negated-quiet", [_outcome(text) for text in P2_NEGATED], [(0, [])] * 5)
    check("preflight/p2-not-negated-hard", [_outcome(text) for text in P2_NOT_NEGATED], [(1, [("P2", "HARD")])] * 3)
    # a pin is 7 to 40 or 64 hex digits: each end just outside, at and inside; outside it the label is a bad pin
    check("preflight/p3-pin-length-boundary",
          [(len(names.pins), len(names.bad_pins)) for names in (PreflightText("BRIEF-PIN {}\n".format("a" * count))
                                                                for count in (6, 7, 8, 39, 40, 41, 63, 64, 65))],
          [(0, 1), (1, 0), (1, 0), (1, 0), (1, 0), (0, 1), (0, 1), (1, 0), (0, 1)])
    # a bold pin label is read like a plain one: a pin, or a bad pin when its value is not a commit
    bold = [PreflightText(form.format(value)) for value, form in [("a" * 12, form) for form in P3_BOLD_PINS]
            + [("main", P3_BOLD_PINS[0])]]
    check("preflight/p3-bold-pin", [(names.pins, names.bad_pins) for names in bold],
          [([(1, "a" * 12)], [])] * 4 + [([], [1])])
    check("preflight/p3-bad-pin-cannot-evaluate",
          [_outcome(P3_PIN_ONLY.format(value), family) for value in P3_BAD_PINS for family in ("alpha", "gamma")]
          + [_outcome("Reviewed head: {}\n".format("a" * 41), "alpha")], ["CannotEvaluate"] * 10 + [(0, [])])
    check("preflight/p4-whole-file-advisory", _outcome(P4_WHOLE_FILE, "alpha"), (0, [("P4", "ADVISORY")]))
    check("preflight/p4-clean-quiet", [_outcome(P4_NAMED, "alpha"), _outcome(P4_NEGATED, "alpha"),
                                       _outcome(P4_WHOLE_FILE, "beta")], [(0, [])] * 3)
    check("preflight/p3-no-repo-cannot-evaluate", _outcome(P3_BRIEF.format("0123456789ab", "docs/a.md"), "alpha"),
          "CannotEvaluate")
    check("preflight/p3-pin-mid-line-quiet", _outcome(P3_MID_LINE, "alpha"), (0, []))
    base = Path(tempfile.mkdtemp(prefix="aiqt-brief-preflight-"))
    try:
        repo, head = _preflight_repo(base)

        def p3(path, rev=head[:12], family="alpha"):
            return _outcome(P3_BRIEF.format(rev, path), family, True, repo)

        # a path missing at the pin may be a file the brief asks the worker to create: printed, never refused
        check("preflight/p3-missing-at-pin-advisory", (p3("docs/absent.md"), p3("./docs/absent.md", head),
                                                       p3("docs/absent.md", family="gamma")),
              ((0, [("P3", "ADVISORY")]),) * 3)
        # read from the object store, never the working tree: a file only in the working tree is missing
        check("preflight/p3-working-tree-not-read", p3("docs/untracked.md"), (0, [("P3", "ADVISORY")]))
        # `..` is folded before the rev:path read; a path that climbs out of the repository is not read
        check("preflight/p3-dotdot-folded", (p3("docs/../docs/present.md"), p3("docs/x/../absent.md"),
                                             p3("../outside.md")),
              ((0, []), (0, [("P3", "ADVISORY")]), (0, [])))
        # a 64-hex pin is read as a pin (this repository's object format gives it no commit); 65 is a bad pin
        check("preflight/p3-pin-64-hex", (_outcome(P3_PIN_ONLY.format("a" * 64), "alpha", True, repo),
                                         _outcome(P3_PIN_ONLY.format("a" * 65), "alpha", True, repo),
                                         len(PreflightText(P3_PIN_ONLY.format("a" * 64)).pins)),
              ("CannotEvaluate", "CannotEvaluate", 1))
        check("preflight/p3-clean-quiet", (p3("docs/present.md"), p3("docs/present.md", head),
                                           _outcome("BRIEF-PIN " + head + "\nRead docs/present.md.\n", "gamma",
                                                    True, repo)), ((0, []),) * 3)
        check("preflight/p3-unresolved-pin-cannot-evaluate", (p3("docs/present.md", "0123456789ab"),
                                                              p3("docs/present.md", "f" * 40)),
              ("CannotEvaluate", "CannotEvaluate"))
        # a pin is resolved even when the brief names no relative path to read at it
        check("preflight/p3-unresolved-pin-no-path-cannot-evaluate",
              (_outcome(P3_PIN_ONLY.format("0123456789ab"), "alpha", True, repo),
               _outcome(P3_PIN_ONLY.format(head), "alpha"), _outcome(P3_PIN_ONLY.format(head), "alpha", True, repo)),
              ("CannotEvaluate", "CannotEvaluate", (0, [])))

        def under_foreign_git_env():
            """A caller's GIT_DIR and GIT_OBJECT_DIRECTORY point elsewhere; the read still uses `repo`."""
            with _environ(GIT_DIR=str(base / "elsewhere"), GIT_OBJECT_DIRECTORY=str(base / "elsewhere")):
                return p3("docs/present.md"), p3("docs/absent.md")

        def git_failures():
            """A git read that times out or cannot be launched, at rev-parse and at cat-file."""
            out = []
            with _patched(GIT_READ_TIMEOUT=1e-9):
                out += [_git_outcome(resolve_revision, repo, head),
                        _git_outcome(exists_at_revision, repo, head, "docs/absent.md")]
            with _environ(PATH=str(base / "no-git-here")):
                out += [_git_outcome(resolve_revision, repo, head),
                        _git_outcome(exists_at_revision, repo, head, "docs/absent.md")]
            return out

        hostile = base / "hostile-home"
        os.makedirs(hostile / "git")
        for name in (".gitconfig", "git/config", "system-config"):
            with open(hostile / name, "w", encoding="utf-8") as handle:
                handle.write("[broken\n")

        def hostile_environ():
            return _environ(HOME=str(hostile), XDG_CONFIG_HOME=str(hostile), GIT_CONFIG_NOSYSTEM="0",
                            GIT_CONFIG_GLOBAL=str(hostile / ".gitconfig"),
                            GIT_CONFIG_SYSTEM=str(hostile / "system-config"))

        def under_malformed_config():
            """A caller's HOME, XDG_CONFIG_HOME and global git config name malformed files; the read still gives
            the normal result. The caller's GIT_CONFIG_SYSTEM goes with the GIT_ scrub, so the system pins are
            held by read_env_pins, not here."""
            with hostile_environ():
                return p3("docs/present.md"), p3("docs/absent.md")

        pins = ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL", "HOME", "XDG_CONFIG_HOME")
        real_read_env = _git_read_env

        def caller_config_env():
            """The previous scrub's per-user surfaces: _git_read_env with the global config unpinned and the caller's
            HOME and XDG_CONFIG_HOME kept. The system pins stay, so this revert never reads the system git
            configuration (the previous scrub dropped them with every other GIT_ variable)."""
            env = real_read_env()
            env.pop("GIT_CONFIG_GLOBAL", None)
            env.update((name, os.environ[name]) for name in ("HOME", "XDG_CONFIG_HOME") if name in os.environ)
            return env

        def read_env_pins():
            """Each pin of the read environment under the hostile caller environment."""
            with hostile_environ():
                env = _git_read_env()
            return [env.get(name) for name in pins]

        def without_pin(name):
            """_git_read_env with one pin removed: the caller's value, if any, comes through."""
            def read_env():
                env = real_read_env()
                env.pop(name, None)
                if not name.startswith("GIT_") and name in os.environ:
                    env[name] = os.environ[name]
                return env
            return read_env

        def caller_git_dir_env():
            """_git_read_env with the caller's GIT_DIR and GIT_OBJECT_DIRECTORY put back (the GIT_ scrub undone for
            the variables under_foreign_git_env sets); HOME and XDG_CONFIG_HOME stay pinned, so this revert never
            reads the caller's own git configuration."""
            env = real_read_env()
            env.update((name, os.environ[name]) for name in ("GIT_DIR", "GIT_OBJECT_DIRECTORY") if name in os.environ)
            return env

        def without_system_pins(make_env):
            """make_env with GIT_CONFIG_NOSYSTEM and GIT_CONFIG_SYSTEM removed: the git it configures reads the system
            configuration."""
            def unpinned(**overrides):
                env = make_env(**overrides)
                env.pop("GIT_CONFIG_NOSYSTEM", None)
                env.pop("GIT_CONFIG_SYSTEM", None)
                return env
            return unpinned

        class UnpinnedFixtureEnv:
            """A _git_fixture_env stand-in whose git_fixture_env leaves the system configuration unpinned."""
            git_fixture_env = staticmethod(without_system_pins(_git_fixture_env.git_fixture_env))

        def reverted_reads():
            """The reads under the read environments the reverts swap in."""
            with _patched(_git_read_env=caller_config_env):
                under_malformed_config()
            with _patched(_git_read_env=caller_git_dir_env):
                under_foreign_git_env()

        def system_config_reads():
            """[(stage, whether git was launched, whether a launch would read the system configuration)] for the
            fixture build, the reads and the reverted reads, each run with a recording git first on PATH. A launch
            with GIT_CONFIG_NOSYSTEM other than 1 and GIT_CONFIG_SYSTEM unset would read it (the test the system
            config-injection lane applies); the recorder then pins it away before it runs the git it shadows, so no
            launch here, a patched one included, reads the system configuration."""
            real_git = shutil.which("git")
            if real_git is None:
                raise RuntimeError("self-test requires git")
            directory = Path(tempfile.mkdtemp(prefix="system-config-", dir=base))
            log = directory / "launches"
            with open(directory / "git", "w", encoding="utf-8") as handle:
                handle.write(
                    '#!/bin/sh\n'
                    'if [ "${{GIT_CONFIG_NOSYSTEM:-}}" != 1 ] && [ "${{GIT_CONFIG_SYSTEM+x}}" != x ]; then\n'
                    '  printf "system\\n" >> {0}\nelse\n  printf "pinned\\n" >> {0}\nfi\n'
                    'export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM={1}\nexec {2} "$@"\n'.format(
                        shlex.quote(str(log)), shlex.quote(os.devnull), shlex.quote(real_git)))
            os.chmod(directory / "git", 0o700)
            out = []
            with _environ(PATH=str(directory) + os.pathsep + os.environ.get("PATH", os.defpath)):
                for stage, run in (("fixture", lambda: _preflight_repo(directory)),
                                   ("reads", lambda: (p3("docs/present.md"), p3("docs/absent.md"))),
                                   ("reverts", reverted_reads)):
                    with open(log, "w", encoding="utf-8"):
                        pass
                    run()
                    with open(log, encoding="utf-8") as handle:
                        launches = handle.read().split()
                    out.append((stage, bool(launches), "system" in launches))
            return out

        # git's ownership refusal as it prints it in English, and a translation that keeps only the hint's command
        ownership_stderr = ("fatal: detected dubious ownership in repository at '{0}'\nTo add an exception for "
                            "this directory, call:\n\n\tgit config --global --add safe.directory {0}\n",
                            "fatal: <translated>\n\n\tgit config --global --add safe.directory {0}\n")

        def names_ownership(text):
            return all(words in text for words in ("dubious ownership", "owned by another user",
                                                   "run the check as the repository owner",
                                                   "point --repo at a checkout you own"))

        def ownership_named():
            """Under each refusal, from an in-process double of the git launch (nothing is launched): the exit
            code of main, and whether its CANNOT_EVALUATE line and the cat-file read's CannotEvaluate name the
            cause and the remedy."""
            out = []
            for template in ownership_stderr:
                def refused(repo, *args, template=template):
                    return subprocess.CompletedProcess(["git"], 128, b"", template.format(repo).encode("utf-8"))

                printed = io.StringIO()
                with _patched(_rev_parse=refused, _cat_file=refused), contextlib.redirect_stdout(printed):
                    code = main(argv)
                    try:
                        read = repr(exists_at_revision(repo, head, "docs/present.md"))
                    except CannotEvaluate as exc:
                        read = str(exc)
                out.append((code, names_ownership(printed.getvalue()), names_ownership(read)))
            return out

        check("preflight/p3-git-env-scrubbed", under_foreign_git_env(), ((0, []), (0, [("P3", "ADVISORY")])))
        check("preflight/p3-malformed-config-normal", under_malformed_config(), ((0, []), (0, [("P3", "ADVISORY")])))
        check("preflight/p3-read-env-pins", read_env_pins(),
              ["1", os.devnull, os.devnull, _git_read_home(), _git_read_home()])
        check("preflight/p3-git-failure-cannot-evaluate", git_failures(), ["CannotEvaluate"] * 4)
        # no git launch of the fixture build, the reads or the reverted reads would read the system configuration
        unexposed = [("fixture", True, False), ("reads", True, False), ("reverts", True, False)]
        check("preflight/p3-no-system-config-read", system_config_reads(), unexposed)
        with open(base / "brief.md", "w", encoding="utf-8") as handle:
            handle.write(P3_BRIEF.format(head[:12], "docs/absent.md"))
        with open(base / "table.toml", "w", encoding="utf-8") as handle:
            handle.write(TABLE_FIXTURE)
        argv = [str(base / "brief.md"), "--family", "alpha", "--table", str(base / "table.toml"), "--repo", str(repo)]
        check("main/preflight-repo-option", (_quiet(main, argv), _quiet(main, argv + ["--repo", str(repo)])),
              (EXIT_OK, EXIT_CANNOT_EVALUATE))
        # a repository git refuses for its ownership exits 2, and the message names the cause and the remedy
        check("preflight/p3-dubious-ownership-named", ownership_named(), [(EXIT_CANNOT_EVALUATE, True, True)] * 2)
        # P5: a pin reached through a moving ref refuses on a git command line and is printed from prose; a later
        # cure line cures it, and a dispatch ref is read in the repository the lint is pointed at
        _dispatch_ref(repo, "refs/dispatch/1/a1", head)
        other = _child_commit(repo, head)
        _dispatch_ref(repo, "refs/dispatch/1/b2", other)

        def p5(text):
            return _outcome(text.format(head, other, head.upper()), "alpha", True, repo)

        for check_id, text in (("preflight/p5-deny-1", P5_DENY[0]), ("preflight/p5-deny-2", P5_DENY[1]),
                               ("preflight/p5-deny-3", P5_DENY[2]), ("preflight/p5-deny-4", P5_DENY[3]),
                               ("preflight/p5-deny-5", P5_DENY[4])):
            check(check_id, p5(text), (1, [("P5", "HARD")]))
        check("preflight/p5-allow-1", p5(P5_ALLOW[0]), (0, []))
        check("preflight/p5-allow-2", (p5(P5_ALLOW[1]), p5(P5_DISPATCH_MISSING)), ((0, []), (0, [("P5", "ADVISORY")])))
        check("preflight/p5-allow-3", p5(P5_ALLOW[2]), (0, []))
        check("preflight/p5-allow-4", p5(P5_ALLOW[3]), (0, []))
        check("preflight/p5-allow-5", (p5(P5_ALLOW[4]), p5(P5_ALLOW[5])), ((0, []), (0, [("P5", "ADVISORY")])))
        check("preflight/p5-dispatch-mismatch-hard", p5(P5_DISPATCH_MISMATCH), (1, [("P5", "HARD")]))
        refused = (1, [("P5", "HARD")])
        check("preflight/p5-prohibition-clause", ([p5(text) for text in P5_CLAUSE_DENY],
                                                  [p5(text) for text in P5_CLAUSE_ALLOW]),
              ([refused] * 3, [(0, [])] * 2))
        check("preflight/p5-fetch-tips", [p5(text) for text in P5_FETCH_TIPS], [refused] * 2)
        check("preflight/p5-fetch-head", [p5(text) for text in P5_FETCH_HEAD], [refused] * 2)
        check("preflight/p5-fetch-no-cure", p5(P5_FETCH_NO_CURE), refused)
        check("preflight/p5-fetch-dashdash", [p5(text) for text in P5_FETCH_DASHDASH], [refused, (0, [])])
        check("preflight/p5-path-restore", [p5(text) for text in P5_PATH_RESTORE], [refused, refused])
        check("preflight/p5-named-ref", [p5(text) for text in P5_NAMED_REF], [refused] * 2)
        check("preflight/p5-governing-pin", [p5(text) for text in P5_GOVERNING_PIN], [refused, (0, [])])
        check("preflight/p5-clone-revision", [p5(text) for text in P5_CLONE_REVISION], [(0, []), (0, []), refused])
        advised = (0, [("P5", "ADVISORY")])
        check("preflight/p5-prohibition-object", ([p5(text) for text in P5_OBJECT_DENY],
                                                  [p5(text) for text in P5_OBJECT_ALLOW]),
              ([refused] * 6, [(0, [])] * 3))
        check("preflight/p5-prohibition-emphasis", [p5(text) for text in P5_OBJECT_EMPHASIS], [(0, [])] * 2)
        check("preflight/p5-prohibition-guard", [p5(text) for text in P5_OBJECT_GUARD], [refused] * 2)
        check("preflight/p5-prohibition-separators", [p5(text) for text in P5_SEPARATORS], [refused] * 5)
        # the cure: its closed shapes cure, every other place, command and pin cures nothing
        check("preflight/p5-cure-line", [p5(P5_MOVING + text) for text in P5_CURE_LINES],
              [(0, [])] * len(P5_CURE_LINES))
        check("preflight/p5-cure-lead", [p5(P5_PIN + text) for text in P5_CURE_LEADS], [(0, [])] * len(P5_CURE_LEADS))
        check("preflight/p5-cure-words", ([p5(P5_MOVING + text) for text in P5_CURE_WORDS],
                                          [p5(P5_PIN + text) for text in P5_CURE_CHAIN_WORDS]),
              ([(0, [])] * len(P5_CURE_WORDS), [(0, [])] * len(P5_CURE_CHAIN_WORDS)))
        check("preflight/p5-cure-characters", [p5(P5_MOVING + text) for text in P5_NOT_CURE_CHARACTERS],
              [refused] * len(P5_NOT_CURE_CHARACTERS))
        check("preflight/p5-cure-raw", [p5(P5_MOVING + text) for text in P5_NOT_CURE_FORMAT],
              [refused] * len(P5_NOT_CURE_FORMAT))
        check("preflight/p5-cure-directory", [p5(P5_MOVING + text) for text in P5_NOT_CURE_DIRECTORIES],
              [refused] * len(P5_NOT_CURE_DIRECTORIES))
        check("preflight/p5-cure-chain", [p5(P5_MOVING + text) for text in P5_NOT_CURE_CHAINS],
              [refused] * len(P5_NOT_CURE_CHAINS))
        check("preflight/p5-cure-clone-options", [p5(P5_MOVING + text) for text in P5_NOT_CURE_CLONE_OPTIONS],
              [refused] * len(P5_NOT_CURE_CLONE_OPTIONS))
        check("preflight/p5-cure-clone-source", [p5(P5_MOVING + text) for text in P5_NOT_CURE_SOURCES],
              [refused] * len(P5_NOT_CURE_SOURCES))
        check("preflight/p5-cure-same-directory", [p5(P5_MOVING + text) for text in P5_NOT_CURE_ELSEWHERE],
              [refused] * len(P5_NOT_CURE_ELSEWHERE))
        check("preflight/p5-cure-comment", ([p5(P5_MOVING + text) for text in P5_NOT_CURE_COMMENT],
                                            p5(P5_MOVING + P5_CURE_AFTER_COMMENT)),
              ([refused] * len(P5_NOT_CURE_COMMENT), (0, [])))
        check("preflight/p5-cure-fence", [p5(P5_MOVING + text) for text in P5_NOT_CURE_FENCE],
              [refused] * len(P5_NOT_CURE_FENCE))
        # the pin peeled cures in every shape, a peeled commit id is fixed, and a peeled word that is not the pin
        # cures nothing; a placeholder glued to other characters in a -C word or a clone's directory is no cure
        check("preflight/p5-cure-peeled", ([p5(P5_MOVING + text) for text in P5_CURE_PEELED_LINES],
                                           [p5(P5_PIN + text) for text in P5_CURE_PEELED_CHAINS]),
              ([(0, [])] * len(P5_CURE_PEELED_LINES), [(0, [])] * len(P5_CURE_PEELED_CHAINS)))
        check("preflight/p5-peeled-fixed", p5(P5_PEELED_FIXED), (0, []))
        check("preflight/p5-cure-peeled-pin", [p5(text) for text in P5_NOT_CURE_PEELED],
              [refused] * len(P5_NOT_CURE_PEELED))
        check("preflight/p5-cure-placeholder-segment", [p5(P5_MOVING + text) for text in P5_NOT_CURE_PLACEHOLDERS],
              [refused] * len(P5_NOT_CURE_PLACEHOLDERS))
        # pin lines that disagree cannot be evaluated, and the message names both lines
        check("preflight/p5-pin-conflict", [p5(text) for text in P5_PIN_CONFLICTS],
              ["CannotEvaluate"] * len(P5_PIN_CONFLICTS))
        check("preflight/p5-reviewed-head-conflict", ([p5(text) for text in P5_REVIEWED_CONFLICTS],
                                                      [p5(text) for text in P5_REVIEWED_AGREE]),
              (["CannotEvaluate"] * len(P5_REVIEWED_CONFLICTS), [(0, [])] * len(P5_REVIEWED_AGREE)))
        check("preflight/p5-near-pin-conflict", ([p5(text) for text in P5_NEAR_PINS],
                                                 [p5(text) for text in P5_NEAR_AGREE]),
              (["CannotEvaluate"] * len(P5_NEAR_PINS), [(0, [])] * len(P5_NEAR_AGREE)))

        def conflict_named():
            """Whether each pin-conflict message names both lines: the first BRIEF-PIN line and the other one."""
            named = []
            for text, lines in ((P5_PIN_CONFLICTS[3], "lines 1 and 4:"), (P5_REVIEWED_CONFLICTS[0], "lines 1 and 2:"),
                                (P5_NEAR_PINS[0], "lines 1 and 2:")):
                try:
                    evaluate(text.format(head, other, head.upper()).encode("utf-8"), "alpha",
                             parse_table(TABLE_FIXTURE.encode("utf-8")), True, repo)
                    named.append(False)
                except CannotEvaluate as exc:
                    named.append(str(exc).startswith(lines))
            return named

        check("preflight/p5-pin-conflict-named", conflict_named(), [True] * 3)
        check("preflight/p5-cure-not-line", [p5(P5_MOVING + text) for text in P5_NOT_CURE_LINES],
              [refused] * len(P5_NOT_CURE_LINES))
        check("preflight/p5-cure-not-lead", [p5(P5_MOVING + text) for text in P5_NOT_CURE_LEADS],
              [refused] * len(P5_NOT_CURE_LEADS))
        check("preflight/p5-cure-not-command", [p5(P5_MOVING + text) for text in P5_NOT_CURE_COMMANDS],
              [refused] * len(P5_NOT_CURE_COMMANDS))
        check("preflight/p5-cure-physical-line", p5(P5_CURE_JOINED), refused)
        check("preflight/p5-cure-order", p5(P5_CURE_BEFORE), refused)
        check("preflight/p5-cure-pin", [p5(text) for text in (P5_CURE_BRIEF_PIN, P5_CURE_PIN_DIGITS, P5_CURE_CONFIRM)
                                        + P5_CURE_EXACT + (P5_CURE_OTHER,)], [refused] * 6)
        check("preflight/p5-tree-reaches", ([p5(text) for text in P5_TREE_DENY], [p5(text) for text in P5_TREE_ALLOW]),
              ([refused] * 4, [(0, [])] * 4))
        check("preflight/p5-reset-any-mode", [p5(text) for text in P5_RESET_DENY], [refused] * 2)
        check("preflight/p5-line-join", [p5(text) for text in P5_LINE_JOIN], [refused] * 3)
        check("preflight/p5-no-git-advisory", [p5(text) for text in P5_NO_GIT], [advised] * 3 + [(0, [])])
        check("preflight/p5-quoted-option", p5(P5_QUOTED_OPTION), refused)
        check("preflight/p5-confirm-pin-reach", p5(P5_CONFIRM_REACH), refused)

        def p5_messages(text):
            """The P5 finding messages for a brief, read in the fixture repository."""
            table = parse_table(TABLE_FIXTURE.encode("utf-8"))
            return [message for rule, _, _, message, _ in evaluate(text.format(head).encode("utf-8"), "alpha",
                                                                   table, True, repo)[1] if rule == "P5"]

        def brace_named():
            return ["through the ref @{u}, which" in message for message in p5_messages(P5_BRACE_REF)]

        check("preflight/p5-brace-ref", (p5(P5_BRACE_REF), brace_named()), (refused, [True]))
        check("preflight/p5-submodule-remote", p5(P5_SUBMODULE), advised)
        # the quoted-example choice: a reach quoted as an example refuses, as every git command line does
        check("preflight/p5-quoted-example", p5(P5_QUOTED_EXAMPLE), refused)
        # the refusal gives a line to copy: each finding with the pin (or, with no 40-hex BRIEF-PIN, how to add one
        # first), and the REFUSE line states the cure shape
        printed = io.StringIO()
        with open(base / "p5.md", "w", encoding="utf-8") as handle:
            handle.write(P5_DENY[0].format(head))
        with contextlib.redirect_stdout(printed):
            main([str(base / "p5.md"), "--family", "alpha", "--table", str(base / "table.toml"), "--repo", str(repo)])

        def cure_given():
            return ([("add this line after the reach, on its own: git -C <dir> checkout --detach " + head + PEEL_SUFFIX)
                     in message for message in p5_messages(P5_DENY[0])],
                    ["no BRIEF-PIN line holds a full 40-hex commit id" in message
                     for message in p5_messages(P5_DENY[2])])

        check("preflight/p5-cure-named", (cure_given(), CURE_SHAPE in printed.getvalue()),
              (([True], [True] * 2), True))
        # red on revert: each preflight rule, and each guard of its clean shape, patched out flips its fixture
        check("revert/p1-red", *flip([lambda: _outcome(P1_RELATIVE, "alpha")], [(0, [])], rule_p1=nothing))
        check("revert/p1-file-name-red", *flip([lambda: _outcome(P1_NOT_A_PATH, "alpha")],
                                               [(0, [("P1", "ADVISORY")])], FILE_SUFFIX_RE=re.compile(r"")))
        check("revert/p2-red", *flip([code(P2_OUTSIDE)], [0], rule_p2=nothing))
        # a plain prefix test, with no component boundary and no `..` folded, reads both as under the workdir
        check("revert/p2-under-workdir-red", *flip(
            [lambda: p2("/w/repository/a.md")[0], lambda: p2("/w/repo/../x/a.md")[0]], [0, 0],
            under_workdir=lambda path, workdirs: any(path.startswith(workdir) for workdir in workdirs)))
        real_p2 = rule_p2
        check("revert/p2-family-gate-red", *flip([lambda: _outcome(P2_OUTSIDE, "beta")[0]], [1],
                                                 rule_p2=lambda names, row, repo: real_p2(
                                                     names, dict(row, **{"reads-outside-workdir": "no"}), repo)))

        def path_after_word(line, start):
            """The previous parser: the first absolute path after the word workdir anywhere on the line."""
            word = WORKDIR_WORD_RE.search(line)
            found = word and ABS_PATH_RE.search(line, word.end())
            return _path_token(found.group(0)) if found else None

        def union_p2(names, row, repo):
            """The previous rule: every declared path is a workdir, with no ambiguity refused."""
            if row["reads-outside-workdir"] != "no" or not names.workdirs:
                return []
            return [("P2", "HARD", line, "", path) for line, path in names.absolute
                    if not under_workdir(path, names.workdirs)]

        # each guard on its own: the previous parser declares a prose path, so a second workdir appears
        check("revert/p2-declaration-red", *flip(
            [lambda text=text: _outcome(text) for text in (P2_SUCH_AS, P2_OR_PATH, P2_ALSO_MID)],
            ["CannotEvaluate"] * 3, _declaration_rest=path_after_word))
        # is before the line end is no link without its guard, so the lead is read as an unclear label
        check("revert/p2-is-line-end-red", *flip(
            [lambda form=form: _outcome(form.format("/etc/x.md")) for form in P2_IS_END[:3]],
            ["CannotEvaluate"] * 3, DECLARATION_LINK_RE=re.compile(r":|is(?=\s)", re.I)))
        # with no label guard a label line that is not a declaration is ignored, so P2 stays off or reads the rest
        check("revert/p2-workdir-label-red", *flip(
            [lambda text=text: _outcome(text) for text in P2_LABEL + (P2_ALSO,)],
            [(0, [])] * 5 + [(1, [("P2", "HARD")])], WORKDIR_LABEL_RE=never))
        # and the previous rule reads every declaration as a workdir and ignores an unclear one
        check("revert/p2-ambiguity-red", *flip(
            [lambda text=text: _outcome(text)[0] for text in (P2_TWO, P2_SPACE_UNQUOTED, P2_BARE_END)],
            [0] * 3, rule_p2=union_p2))

        def token_paths(line):
            """The previous extraction: each ABS_PATH_RE match alone, cut short at `//`, `~` or a space."""
            quoted = [match for match in QUOTED_ABS_PATH_RE.finditer(line) if any(
                character.isspace() for character in match.group(1) or match.group(2))]
            found = [(match.start(), _path_token(match.group(0)), True) for match in ABS_PATH_RE.finditer(line)
                     if not any(span.start() <= match.start() < span.end() for span in quoted)]
            return sorted(found + [(match.start(), match.group(1) or match.group(2), True) for match in quoted])

        check("revert/p2-path-word-red", *flip([lambda path=path: p2(path)[0] for path in P2_ESCAPES], [0] * 3,
                                               path_words=token_paths))
        check("revert/p2-unplaced-red", *flip([lambda path=path: p2(path)[0] for path in P2_UNPLACED[:3]], [0] * 3,
                                              unplaced=lambda word: False))
        check("revert/p2-word-cap-red", *flip([lambda: p2(P2_UNPLACED[3])[0]], [0], MAX_WORD_PATHS=10 ** 6))
        check("revert/p2-negation-red", *flip([code(text) for text in P2_NEGATED], [1] * 5, PROHIBITION_RE=never))

        def pattern_paths(line):
            """The previous extraction: an absolute path found by pattern (ABS_PATH_RE) in each word, and a word
            that overlaps a quoted path with a space read as that quoted path alone."""
            quoted = [match for match in QUOTED_ABS_PATH_RE.finditer(line) if any(
                character.isspace() for character in match.group(1) or match.group(2))]
            found = [(match.start(), match.group(1) or match.group(2), True) for match in quoted]
            for word in WORD_RE.finditer(line):
                if any(start < word.end() and word.start() < end for start, end in (m.span() for m in quoted)):
                    continue
                found.extend((word.start() + match.start(), _word_end(word.group(0)[match.start():]), True)
                             for match in ABS_PATH_RE.finditer(word.group(0)))
            return sorted(found)

        glued = P2_GLUED_QUOTE + P2_GLUED_AFTER + (P2_FILE_URL,)
        check("revert/p2-glued-red", *flip([lambda path=path: p2(path)[0] for path in glued], [0] * len(glued),
                                           path_words=pattern_paths))
        check("revert/p2-glue-red", *flip([lambda path=path: p2(path)[0] for path in P2_GLUED_AFTER],
                                          [0] * len(P2_GLUED_AFTER), PATH_GLUE=frozenset("._@%-:")))
        check("revert/p2-quoted-word-red", *flip([lambda: _outcome(P2_QUOTED.format("\"/w/my repo/x.md\""))[0]], [1],
                                                 line_words=lambda line: [(match.start(), match.group(0))
                                                                          for match in WORD_RE.finditer(line)]))
        check("revert/p2-web-url-red", *flip([lambda: p2(P2_WEB_URL)[0]], [1], WEB_URL_RE=never))
        check("revert/p2-file-url-red", *flip([lambda: p2(P2_FILE_URL)[0]], [0],
                                              WEB_URL_RE=re.compile(r"(?:https?|file)://", re.I)))
        check("revert/p2-dotdot-stop-red", *flip([lambda path=path: p2(path)[0] for path in ("/w/repo/...",
                                                                                            "/w/repo/..).")],
                                                 [0, 0], path_ends=lambda text: [text.rstrip(TRAILING_PUNCTUATION)]))
        check("revert/p2-wide-workdir-red", *flip([lambda text=text: _outcome(text)[0] for text in P2_WIDE], [0] * 3,
                                                  bad_workdir=lambda path: False))
        check("revert/p3-bad-pin-red", *flip([lambda value=value: _outcome(P3_PIN_ONLY.format(value), "alpha", True,
                                                                           repo) for value in P3_BAD_PINS],
                                             [(0, [])] * len(P3_BAD_PINS), PIN_LABEL_RE=never))
        check("revert/p3-pin-64-hex-red", *flip([lambda: len(PreflightText(P3_PIN_ONLY.format("a" * 64)).pins)],
                                                [0], PIN_RE=re.compile(
                                                    r"(?:REVIEWED\s+HEAD|BRIEF-PIN)[*_]*+\s*+[:=]?+[*_]*+\s*+[`*_(]*+"
                                                    r"([0-9a-fA-F]{7,40})(?![\w-])")))
        # no markup between the label and its colon or value: a bold label reads as a pin line with no pin
        check("revert/p3-bold-pin-red", *flip([lambda form=form: len(PreflightText(form.format("a" * 12)).pins)
                                               for form in P3_BOLD_PINS[:2]], [0] * 2, PIN_RE=re.compile(
                                                   r"(?:REVIEWED\s+HEAD|BRIEF-PIN)\s*+[:=]?+\s*+[`*_(]*+"
                                                   r"([0-9a-fA-F]{64}|[0-9a-fA-F]{7,40})(?![\w-])")))
        check("revert/p3-red", *flip([lambda: p3("docs/absent.md")], [(0, [])], rule_p3=nothing))
        check("revert/p3-cat-file-red", *flip([lambda: p3("docs/untracked.md")], [(0, [])],
                                              exists_at_revision=lambda repo, commit, path: os.path.lexists(
                                                  os.path.join(str(repo), path))))
        check("revert/p3-dotdot-red", *flip([lambda: p3("docs/../docs/present.md")], [(0, [("P3", "ADVISORY")])],
                                            repository_path=lambda path: path))
        check("revert/p3-unresolved-red", *flip([lambda: p3("docs/present.md", "0123456789ab")],
                                                [(0, [("P3", "ADVISORY")])], resolve_revision=lambda repo, rev: rev))
        check("revert/p3-git-env-red", *flip([lambda: under_foreign_git_env()[0]], ["CannotEvaluate"],
                                             _git_read_env=caller_git_dir_env))
        # with the caller's HOME kept, the malformed global config there fails every read
        check("revert/p3-hermetic-config-red", *flip([lambda: under_malformed_config()[0]], ["CannotEvaluate"],
                                                     _git_read_env=caller_config_env))
        # each pin removed from the read environment fails the pin check
        pinned = ["1", os.devnull, os.devnull, _git_read_home(), _git_read_home()]
        check("revert/p3-pin-nosystem-red", *flip([read_env_pins], [[None] + pinned[1:]],
                                                  _git_read_env=without_pin("GIT_CONFIG_NOSYSTEM")))
        check("revert/p3-pin-system-red", *flip([read_env_pins], [pinned[:1] + [None] + pinned[2:]],
                                                _git_read_env=without_pin("GIT_CONFIG_SYSTEM")))
        check("revert/p3-pin-global-red", *flip([read_env_pins], [pinned[:2] + [None] + pinned[3:]],
                                                _git_read_env=without_pin("GIT_CONFIG_GLOBAL")))
        check("revert/p3-pin-home-red", *flip([read_env_pins], [pinned[:3] + [str(hostile)] + pinned[4:]],
                                              _git_read_env=without_pin("HOME")))
        check("revert/p3-pin-xdg-red", *flip([read_env_pins], [pinned[:4] + [str(hostile)]],
                                             _git_read_env=without_pin("XDG_CONFIG_HOME")))
        # with the system pins removed from the read or the fixture environment, a launch would read the system config
        check("revert/p3-read-system-config-red", *flip(
            [system_config_reads], [[unexposed[0], ("reads", True, True), unexposed[2]]],
            _git_read_env=without_system_pins(real_read_env)))
        check("revert/p3-fixture-system-config-red", *flip(
            [system_config_reads], [[("fixture", True, True)] + unexposed[1:]], _git_fixture_env=UnpinnedFixtureEnv))
        # without the ownership reading, the refusal reads as an unresolved pin and the cat-file read as missing
        check("revert/p3-dubious-ownership-red", *flip([ownership_named], [[(EXIT_CANNOT_EVALUATE, False, False)] * 2],
                                                       _ownership_refused=lambda probe: False))
        real_resolve, real_exists = resolve_revision, exists_at_revision

        def resolve_or_pass(repo, rev):
            try:
                return real_resolve(repo, rev)
            except CannotEvaluate:
                return rev

        def exists_or_pass(repo, commit, path):
            try:
                return real_exists(repo, commit, path)
            except CannotEvaluate:
                return True

        check("revert/p3-git-failure-red", *flip([git_failures], [[head, True, head, True]],
                                                 resolve_revision=resolve_or_pass, exists_at_revision=exists_or_pass))
        check("revert/p4-red", *flip([lambda: _outcome(P4_WHOLE_FILE, "alpha")], [(0, [])], rule_p4=nothing))
        check("revert/p4-named-red", *flip([lambda: _outcome(P4_NAMED, "alpha")], [(0, [("P4", "ADVISORY")])],
                                           HANDBACK_NAMED_RE=never))
        check("revert/p5-red", *flip([lambda: p5(P5_DENY[0])], [(0, [])], rule_p5=nothing))
        check("revert/p5-cure-red", *flip([lambda: p5(P5_ALLOW[0])], [refused], cure_matches=lambda word, pins: False))
        # a commit id that is not the pin does not cure: with any pin argument curing, the other commit's line passes
        check("revert/p5-cure-pin-red", *flip([lambda: p5(P5_CURE_OTHER)], [(0, [])],
                                              cure_matches=lambda word, pins: True))
        check("revert/p5-prohibition-red", *flip([lambda: p5(P5_ALLOW[4])], [refused], REACH_PROHIBITION_RE=never))
        check("revert/p5-fixed-ref-red", *flip([lambda: p5(P5_ALLOW[2])], [refused], hex_ref=lambda word: False))
        check("revert/p5-pin-clause-red", *flip([lambda: p5(P5_DENY[2])], [(0, [])], CONFIRM_RE=never))
        check("revert/p5-prose-advisory-red", *flip([lambda: p5(P5_ALLOW[5])], [refused],
                                                    REACH_SEVERITY=dict(git="HARD", prose="HARD")))
        check("revert/p5-dispatch-ref-red", *flip([lambda: p5(P5_ALLOW[1])], [refused], DISPATCH_PREFIX="\0"))
        check("revert/p5-dispatch-mismatch-red", *flip([lambda: p5(P5_DISPATCH_MISMATCH)], [(0, [("P5", "ADVISORY")])],
                                                       dispatch_commit=lambda repo, ref: None))
        check("revert/p5-fetch-red", *flip([lambda: p5(P5_DENY[3])], [(0, [])], REACH_REFS=REACH_REFS - {"fetch"}))
        # a looser reading of each: a prohibition silences the rest of its line, a fetch with no refspec is no
        # reach, FETCH_HEAD inherits nothing, `--` ends a fetch's operands, a path restore is no reach, a clone's
        # --branch value is fixed when it is hex, a confirm clause's commit is a pin beside the pin line's, and
        # --revision is a flag
        check("revert/p5-prohibition-scope-red", *flip(
            [lambda text=text: p5(text) for text in P5_CLAUSE_DENY], [(0, [])] * 3,
            prohibition_objects=lambda line, spans: [
                (found.end(), len(line)) for found in PROHIBITION_RE.finditer(line)][:1]))

        def clause_scopes(line, spans):
            """A looser reading: a prohibition governs from its end to the first clause stop after it."""
            stops = [position for position, character in enumerate(line) if character in ";,:.!?"
                     and line[position + 1:].lstrip("*_\"')]}")[:1] in ("", " ", "\t")
                     and all(not start <= position < end for start, end in spans)]
            return sorted((found.end(), next((stop for stop in stops if stop >= found.end()), len(line)))
                          for found in PROHIBITION_RE.finditer(line))

        check("revert/p5-prohibition-object-red", *flip(
            [lambda text=text: p5(text) for text in P5_OBJECT_DENY], [(0, [])] * 6, prohibition_objects=clause_scopes))
        check("revert/p5-prohibition-separators-red", *flip(
            [lambda text=text: p5(text) for text in P5_SEPARATORS], [(0, [])] * 5, prohibition_objects=clause_scopes))
        # with a prohibition word read inside a code span and after `=`, the quoted words and the value govern
        check("revert/p5-prohibition-guard-red", *flip(
            [lambda text=text: p5(text) for text in P5_OBJECT_GUARD], [(0, [])] * 2,
            prohibitions=lambda line, spans: list(re.finditer(REACH_PROHIBITION_RE.pattern.replace("=", "", 1),
                                                              line, re.I))))
        # a prohibition pattern with no emphasis before the verb reads Never *run* as Never alone
        check("revert/p5-prohibition-emphasis-red", *flip(
            [lambda text=text: p5(text) for text in P5_OBJECT_EMPHASIS], [refused] * 2,
            REACH_PROHIBITION_RE=re.compile(r"(?<![\w/.=-])" + P5_PROHIBITION_WORDS + r"[*_]*+\s++"
                                            r"(?:(?:run|use|execute|follow)[*_]*+\s++)?+", re.I)))
        # each element of the cure shape patched out or loosened flips its rows: any git command cures, no `$` or
        # then, no list marker, no code span, no lead, no &&, a cure anywhere on its line, a joined line, a cure
        # before the reach, any pin line's or BRIEF-PIN's value, a prefix or another case, and no line to copy
        real_cure_line, real_pin_reaches, real_cure_lines = cure_line, pin_reaches, cure_lines

        def cure_anywhere(line, fenced=False):
            """A looser reading: the words before the cure command's git and a stop or emphasis after it are
            dropped, so a cure inside a sentence counts."""
            at = line.find("git ")
            at -= 1 if at > 0 and line[at - 1] == "`" else 0
            found = real_cure_line(line[at:].rstrip(".*"), fenced) if at >= 0 else None
            return found and (found[0] + at, found[1])

        def cure_among_words(line, fenced=False):
            """A looser reading still: the cure command's words anywhere among the line's words, each with backticks,
            emphasis and closing punctuation dropped, so any words before or after it, and any lead, count."""
            words = [(found.start(), found.group(0)) for found in WORD_RE.finditer(line)]
            for index, (start, word) in enumerate(words):
                if word.strip("`*_.,;:") != "git":
                    continue
                for size in (6, 5, 4):
                    command = [text.strip("`*_.,;:") for _, text in words[index:index + size]]
                    if len(command) == size and cure_command(command):
                        return start + word.find("git"), command[-1]
            return None

        check("revert/p5-cure-command-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_COMMANDS],
            [(0, [])] * len(P5_NOT_CURE_COMMANDS), cure_command=lambda words: words[:1] == ["git"]))
        check("revert/p5-cure-prefix-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_CURE_LINES[6:10]], [refused] * 4,
            CURE_PREFIXES=frozenset()))
        check("revert/p5-cure-list-marker-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_CURE_LINES[3:6]]
            + [lambda text=text: p5(P5_PIN + text) for text in P5_CURE_LEADS[3:]],
            [refused] * 4, _list_marker=lambda word: False))
        check("revert/p5-cure-span-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in (P5_CURE_LINES[1], P5_CURE_LINES[4], P5_CURE_LINES[8])]
            + [lambda text=text: p5(P5_PIN + text) for text in P5_CURE_LEADS],
            [refused] * 7, _unspanned=lambda words, bare: words if bare and "`" not in "".join(words) else None))
        check("revert/p5-cure-leads-red", *flip(
            [lambda text=text: p5(P5_PIN + text) for text in P5_CURE_LEADS], [refused] * 4, CURE_LEADS=()))
        check("revert/p5-cure-chain-red", *flip(
            [lambda text=text: p5(P5_PIN + text) for text in P5_CURE_LEADS], [refused] * 4, CURE_CHAIN="\0"))
        check("revert/p5-cure-whole-line-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_LINES[:6]], [(0, [])] * 6,
            cure_line=cure_anywhere))
        # the rest of the misplaced cures (a word after the command, a space inside its span, a sentence), the
        # misplaced leads and a confirm-clause pin each cure under a looser reading
        check("revert/p5-cure-not-line-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_LINES[6:]],
            [(0, [])] * len(P5_NOT_CURE_LINES[6:]), cure_line=cure_among_words))
        check("revert/p5-cure-not-lead-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_LEADS],
            [(0, [])] * len(P5_NOT_CURE_LEADS), cure_line=cure_among_words))
        check("revert/p5-cure-confirm-red", *flip(
            [lambda: p5(P5_CURE_CONFIRM)], [(0, [])], cure_pins=lambda names: [
                pin for pin in real_pin_reaches(names.text, names.raw)[0] if len(pin) == CURE_PIN_DIGITS]))
        check("revert/p5-cure-physical-line-red", *flip(
            [lambda: p5(P5_CURE_JOINED)], [(0, [])], cure_lines=lambda text, raw: dict(
                (number, (line, cure_line(line))) for number, line in p5_lines(text) if cure_line(line))))
        check("revert/p5-cure-order-red", *flip(
            [lambda: p5(P5_CURE_BEFORE)], [(0, [])],
            pin_reaches=lambda text, raw: (lambda found: (found[0], found[1][::-1]))(real_pin_reaches(text, raw))))
        check("revert/p5-cure-brief-pin-red", *flip(
            [lambda: p5(P5_CURE_BRIEF_PIN)], [(0, [])],
            cure_pins=lambda names: [pin for _, pin in names.pins if len(pin) == CURE_PIN_DIGITS]))
        check("revert/p5-cure-pin-digits-red", *flip(
            [lambda: p5(P5_CURE_PIN_DIGITS)], [(0, [])], cure_pins=lambda names: [pin for _, pin in names.brief_pins]))
        check("revert/p5-cure-exact-red", *flip(
            [lambda text=text: p5(text) for text in P5_CURE_EXACT], [(0, [])] * 2, cure_matches=pin_matches))
        check("revert/p5-cure-advice-red", *flip([cure_given], [([False], [False] * 2)], cure_advice=lambda pins: ""))
        # a looser reading of each element: the text with format characters removed, any character, any -C word or
        # source with no backtick, the command after the last && or alone, any clone option, any directory, no HTML
        # comment, a comment that never closes, and a fence line read as any line
        check("revert/p5-cure-characters-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_CHARACTERS],
            [(0, [])] * len(P5_NOT_CURE_CHARACTERS), cure_characters=lambda line: True))
        check("revert/p5-cure-raw-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_FORMAT],
            [(0, [])] * len(P5_NOT_CURE_FORMAT), cure_lines=lambda text, raw: real_cure_lines(text, text)))
        check("revert/p5-cure-directory-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_DIRECTORIES],
            [(0, [])] * len(P5_NOT_CURE_DIRECTORIES), CURE_PATH_RE=re.compile(r"[^`]++")))
        # a looser closed path takes a <name> placeholder anywhere in the word, glued to other characters too
        check("revert/p5-cure-placeholder-segment-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_PLACEHOLDERS],
            [(0, [])] * len(P5_NOT_CURE_PLACEHOLDERS),
            CURE_PATH_RE=re.compile(r"(?!-)(?:[A-Za-z0-9_./-]|<[A-Za-z0-9_-]++>)++")))

        def after_last_chain(words):
            """A looser reading of a lead's code span: the cure command alone, or after its last &&."""
            command = words[len(words) - words[::-1].index(CURE_CHAIN):] if CURE_CHAIN in words else words
            return command if cure_command(command) else None

        check("revert/p5-cure-clone-chain-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_CHAINS],
            [(0, [])] * len(P5_NOT_CURE_CHAINS), clone_chain=after_last_chain))
        check("revert/p5-cure-clone-options-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_CLONE_OPTIONS],
            [(0, [])] * len(P5_NOT_CURE_CLONE_OPTIONS),
            CURE_CLONE_OPTIONS=CURE_CLONE_OPTIONS | {"--depth=1", "--single-branch", "--branch=main"}))
        check("revert/p5-cure-clone-source-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_SOURCES],
            [(0, [])] * len(P5_NOT_CURE_SOURCES), CURE_SOURCE_RE=re.compile(r"[^`]++")))
        check("revert/p5-cure-same-directory-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_ELSEWHERE],
            [(0, [])] * len(P5_NOT_CURE_ELSEWHERE), same_directory=lambda command, directory: True))
        check("revert/p5-cure-comment-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_COMMENT],
            [(0, [])] * len(P5_NOT_CURE_COMMENT), HTML_COMMENT_OPEN="\0"))
        check("revert/p5-cure-comment-close-red", *flip(
            [lambda: p5(P5_MOVING + P5_CURE_AFTER_COMMENT)], [refused], HTML_COMMENT_CLOSE="\0"))
        check("revert/p5-cure-fence-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_NOT_CURE_FENCE],
            [(0, [])] * len(P5_NOT_CURE_FENCE), cure_line=lambda line, fenced=False: real_cure_line(line)))
        # a cure that takes only the bare pin refuses every peeled shape, one that takes any peeled word cures a
        # prefix, another case and another commit, and with no peeled ref fixed the peeled reach moves
        check("revert/p5-cure-peeled-red", *flip(
            [lambda text=text: p5(P5_MOVING + text) for text in P5_CURE_PEELED_LINES]
            + [lambda text=text: p5(P5_PIN + text) for text in P5_CURE_PEELED_CHAINS],
            [refused] * (len(P5_CURE_PEELED_LINES) + len(P5_CURE_PEELED_CHAINS)),
            cure_matches=lambda word, pins: word in pins))
        check("revert/p5-cure-peeled-pin-red", *flip(
            [lambda text=text: p5(text) for text in P5_NOT_CURE_PEELED], [(0, [])] * len(P5_NOT_CURE_PEELED),
            cure_matches=lambda word, pins: word.endswith(PEEL_SUFFIX) or word in pins))
        check("revert/p5-peeled-fixed-red", *flip([lambda: p5(P5_PEELED_FIXED)], [refused],
                                                  peeled_ref=lambda ref: False))
        # with the agreement checks loosened (every BRIEF-PIN value a cure pin, any REVIEWED HEAD agreeing), pin lines
        # that disagree pass; a REVIEWED HEAD must equal the BRIEF-PIN value, and the message names neither line
        check("revert/p5-pin-conflict-red", *flip(
            [lambda text=text: p5(text) for text in P5_PIN_CONFLICTS], [(0, [])] * len(P5_PIN_CONFLICTS),
            brief_pin_conflict=lambda names: None))
        check("revert/p5-reviewed-head-conflict-red", *flip(
            [lambda text=text: p5(text) for text in P5_REVIEWED_CONFLICTS], [(0, [])] * len(P5_REVIEWED_CONFLICTS),
            pins_agree=lambda value, pin: True))
        check("revert/p5-reviewed-head-prefix-red", *flip(
            [lambda: p5(P5_REVIEWED_AGREE[0])], ["CannotEvaluate"], pins_agree=lambda value, pin: value == pin))
        check("revert/p5-pin-conflict-named-red", *flip([conflict_named], [[False] * 3],
                                                        pin_conflict_message=lambda conflict: ""))
        # with no look-alike reading every look-alike passes; a label read in its case alone, a text not folded to
        # NFKC, no dash read as -, and no backtick or table pipe skipped each pass their row; and a look-alike that
        # must equal the pin refuses a prefix of it
        check("revert/p5-near-pin-red", *flip(
            [lambda text=text: p5(text) for text in P5_NEAR_PINS], [(0, [])] * len(P5_NEAR_PINS),
            near_pin_conflict=lambda names: None))
        check("revert/p5-near-pin-case-red", *flip(
            [lambda text=text: p5(text) for text in (P5_NEAR_PINS[0], P5_NEAR_PINS[5])], [(0, [])] * 2,
            NEAR_PIN_RE=re.compile(NEAR_PIN_RE.pattern)))
        check("revert/p5-near-pin-nfkc-red", *flip([lambda: p5(P5_NEAR_PINS[2])], [(0, [])], NEAR_PIN_FORM="NFC"))
        check("revert/p5-near-pin-dash-red", *flip([lambda: p5(P5_NEAR_PINS[1])], [(0, [])],
                                                   NEAR_PIN_DASHES=frozenset()))
        check("revert/p5-near-pin-backtick-red", *flip(
            [lambda: p5(P5_NEAR_PINS[3])], [(0, [])],
            NEAR_PIN_RE=re.compile(NEAR_PIN_RE.pattern.replace("[`|*_", "[|*_"), re.I)))
        check("revert/p5-near-pin-pipe-red", *flip(
            [lambda: p5(P5_NEAR_PINS[4])], [(0, [])],
            NEAR_PIN_RE=re.compile(NEAR_PIN_RE.pattern.replace("[`|*_", "[`*_"), re.I)))
        check("revert/p5-near-pin-agree-red", *flip([lambda: p5(P5_NEAR_AGREE[1])], ["CannotEvaluate"],
                                                    pins_agree=lambda value, pin: value == pin))
        real_reach_refs = reach_refs
        check("revert/p5-tree-reaches-red", *flip(
            [lambda text=text: p5(text) for text in P5_TREE_DENY], [(0, [])] * 4,
            REACH_REFS=REACH_REFS - {"merge", "rebase", "restore", "archive"}))
        check("revert/p5-reset-mode-red", *flip(
            [lambda text=text: p5(text) for text in P5_RESET_DENY], [(0, [])] * 2,
            reach_refs=lambda subcommand, words: [] if subcommand == "reset" and not {
                "--hard", "--keep", "--merge"} & set(words) else real_reach_refs(subcommand, words)))
        check("revert/p5-path-restore-reach-red", *flip(
            [lambda: p5(P5_PATH_RESTORE[1])], [(0, [])],
            reach_refs=lambda subcommand, words: [] if subcommand in ("checkout", "reset") and "--" in words
            else real_reach_refs(subcommand, words)))
        check("revert/p5-line-join-red", *flip(
            [lambda text=text: p5(text) for text in P5_LINE_JOIN], [(0, []), (0, [("P5", "ADVISORY")])] + [
                (0, [("P5", "ADVISORY")])],
            p5_lines=lambda text: [(number, line.rstrip("\r")) for number, line in enumerate(text.split("\n"), 1)]))
        check("revert/p5-no-git-advisory-red", *flip(
            [lambda text=text: p5(text) for text in P5_NO_GIT[:3]], [(0, [])] * 3, GH_CHECKOUT_RE=never,
            web_refs=nothing))
        check("revert/p5-quoted-option-red", *flip(
            [lambda: p5(P5_QUOTED_OPTION)], [(0, [])], GIT_OPTION_RE=re.compile(
                r"\s+(?:-[Cc]\s+\S+|--(?:git-dir|work-tree|namespace|config-env|attr-source)\s+\S+"
                r"|--?[A-Za-z][\w-]*(?:=\S+)?)")))
        real_confirm_pins = confirm_pins

        def confirm_but_reaches(line):
            """A looser reading: a confirm-clause commit that is a reach's ref on its line is no pin."""
            refs = set()
            for start, end in option_commands(line):
                if _subcommand(line, end) in REACH_REFS:
                    words = [word for word in (_reach_word(word) for word in line[end:argument_stop(
                        line, end, len(line))].split()) if word]
                    refs.update(ref for ref, _ in reach_refs(_subcommand(line, end), words))
            return [pin for pin in real_confirm_pins(line) if pin not in refs]

        check("revert/p5-confirm-pin-reach-red", *flip([lambda: p5(P5_CONFIRM_REACH)], [(0, [])],
                                                       confirm_pins=confirm_but_reaches))
        check("revert/p5-brace-ref-red", *flip([brace_named], [[False]],
                                               _reach_word=lambda word: _word_end(word.lstrip("\"'*_([{<"))))
        check("revert/p5-submodule-remote-red", *flip([lambda: p5(P5_SUBMODULE)], [(0, [])],
                                                      submodule_remote=lambda subcommand, words: False))
        check("revert/p5-fetch-tips-red", *flip(
            [lambda text=text: p5(text) for text in P5_FETCH_TIPS], [(0, [])] * 2,
            reach_refs=lambda subcommand, words: [ref for ref in real_reach_refs(subcommand, words)
                                                  if subcommand != "fetch" or ref[0]]))
        check("revert/p5-fetch-head-red", *flip([lambda text=text: p5(text) for text in P5_FETCH_HEAD],
                                                [(0, []), refused], FETCH_REACHES=frozenset()))
        check("revert/p5-fetch-dashdash-red", *flip([lambda: p5(P5_FETCH_DASHDASH[1])], [refused],
                                                    OPERANDS_AFTER_DASHDASH=frozenset()))
        check("revert/p5-named-ref-red", *flip([lambda text=text: p5(text) for text in P5_NAMED_REF], [(0, [])] * 2,
                                               reach_moves=lambda ref, named: not fixed_ref(ref)))
        check("revert/p5-governing-pin-red", *flip(
            [lambda: p5(P5_GOVERNING_PIN[0])], [(0, [])],
            governing_pins=lambda line_pins, clause_pins: line_pins + clause_pins))
        check("revert/p5-clone-revision-red", *flip(
            [lambda text=text: p5(text) for text in P5_CLONE_REVISION[:2]], [refused] * 2,
            REACH_VALUE_OPTIONS=dict(REACH_VALUE_OPTIONS, clone=REACH_VALUE_OPTIONS["clone"] - {"--revision"}),
            CLONE_REVISION_OPTIONS=frozenset()))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    # ---------- P6: a declared input (an INPUT line) must exist on the lint's host; {T} is a fresh directory ----------
    inputs = Path(tempfile.mkdtemp(prefix="aiqt-brief-input-"))
    locked = inputs / "locked"
    try:
        os.makedirs(inputs / "dir with space")
        os.makedirs(locked)
        for name in ("in.md", "dir with space/in.md", "unreadable.md", "locked/in.md"):
            with open(inputs / name, "w", encoding="utf-8") as handle:
                handle.write("input\n")
        os.symlink(inputs / "nowhere.md", inputs / "dangling.md")
        os.mkfifo(inputs / "fifo")
        os.chmod(inputs / "unreadable.md", 0)
        os.chmod(locked, 0)

        def p6(text, family="alpha"):
            return _outcome(text.replace("{T}", str(inputs)), family)

        def p6_code(text):
            outcome = p6(text)
            return outcome if isinstance(outcome, str) else outcome[0]

        def unreadable_code():
            """The mode-000 vector with os.access refusing for every uid, so it runs as root too."""
            with _patched(input_readable=lambda path: False):
                return p6_code("INPUT: {T}/unreadable.md\n")

        p6_missing = (1, [("P6", "HARD")])
        check("preflight/p6-present-quiet", [p6(text) for text in (
            "INPUT: {T}/in.md\n", "INPUT: \"{T}/dir with space/in.md\"\n", "INPUT: `{T}/dir with space/in.md`\n",
            "INPUT: {T}\n", "INPUT: \"{T}/dir with space\"\n", "**INPUT:** {T}/in.md\n", "**INPUT**: {T}/in.md\n",
            "- INPUT: {T}/in.md\n", "* INPUT: {T}/in.md\n", "+ INPUT: {T}/in.md\n")], [(0, [])] * 10)
        # every form a reader shows as INPUT: declares: emphasis however it is spelt, a numbered item, an
        # escaped or character-referenced colon, a fullwidth or confusable spelling, a combining or format
        # character the fold removes, strikethrough, and a declaration after a list item's fence or an
        # indented code block (the parsed document hides nothing there); each with the input present
        check("preflight/p6-markdown-present-quiet", [p6(text) for text in (
            "__INPUT:__ {T}/in.md\n", "_INPUT:_ {T}/in.md\n", "*INPUT:* {T}/in.md\n",
            "1. INPUT: {T}/in.md\n", "INPUT\\: {T}/in.md\n", "INPUT&#58; {T}/in.md\n",
            "INPUT： {T}/in.md\n", "ＩＮＰＵＴ: {T}/in.md\n",
            "I" + chr(0x034F) + "NPUT: {T}/in.md\n", "INPUT" + chr(0xFE0F) + ": {T}/in.md\n",
            "~~INPUT:~~ {T}/in.md\n",
            "- ```text\n  example\n  ```\n\nINPUT: {T}/in.md\n",
            "Intro.\n\n    ```\n\nINPUT: {T}/in.md\n",
            "INPUT: {T}/in.md")], [(0, [])] * 14)
        # the end of the declaration: exactly at the path's end, just inside (trailing whitespace), just outside
        check("preflight/p6-trailing-boundary", [p6(text) for text in (
            "INPUT: {T}/in.md\n", "INPUT: {T}/in.md  \t \n", "INPUT: {T}/in.md extra\n")],
            [(0, []), (0, []), "CannotEvaluate"])
        # prose is never judged: a missing input named only in prose, an output to create, a placeholder
        # clone line, a mid-line mention of the word input, a glued or mid-line label, a lone pipe row
        # with no delimiter row, an unlisted look-alike letter or colon (the bounded table's named miss)
        # and a link reference definition (which renders as nothing) all pass
        check("preflight/p6-prose-quiet", [p6(text) for text in (
            "Read {T}/missing.md and summarise it.\n", "Write your notes to {T}/out/new.md (create it).\n",
            "Make a scratch clone: `git clone --no-hardlinks <url> <scratch>/r && git -C <scratch>/r checkout "
            "--detach <pin>`\n", "Input files are listed below: {T}/missing.md\n",
            "The tool's input: a TOML file.\n", "1.INPUT: {T}/missing.md\n",
            "Step 1 - INPUT: {T}/missing.md\n", "| INPUT | {T}/missing.md |\n",
            "| INPUT: | {T}/missing.md |\n",
            "| Input | Expected |\n|---|---|\n| a | b |\n",
            "ɪɴᴘᴜᴛ: {T}/missing.md\n", "INPUTꓽ {T}/missing.md\n",
            "[INPUT]: {T}/missing.md \"title\"\n")], [(0, [])] * 13)
        check("preflight/p6-missing-hard", [p6(text) for text in (
            "INPUT: {T}/missing.md\n", "**INPUT:** {T}/missing.md\n", "**INPUT**: {T}/missing.md\n",
            "INPUT: {T}/dangling.md\n", "INPUT: {T}/in.md/x\n")]
            + [p6("INPUT: {T}/missing.md\n", "gamma")], [p6_missing] * 6)
        # the declaring forms above go HARD the same way when their input is missing, the list-fence and
        # indented-code briefs and a brief with no final newline included
        check("preflight/p6-markdown-missing-hard", [p6(text) for text in (
            "__INPUT:__ {T}/missing.md\n", "_INPUT:_ {T}/missing.md\n", "*INPUT:* {T}/missing.md\n",
            "1. INPUT: {T}/missing.md\n", "INPUT\\: {T}/missing.md\n", "INPUT&#58; {T}/missing.md\n",
            "ІNPUT: {T}/missing.md\n", "I" + chr(0x034F) + "NPUT: {T}/missing.md\n",
            "- ```text\n  example\n  ```\n\nINPUT: {T}/missing.md\n",
            "Intro.\n\n    ```\n\nINPUT: {T}/missing.md\n",
            "Intro.\n\n\t```\n\nINPUT: {T}/missing.md\n",
            "INPUT: {T}/missing.md")], [p6_missing] * 12)
        # inside code nothing is a declaration and nothing is a look-alike: a fenced or tab-indented INPUT
        # line, a quoted workflow's indented inputs: key, the label in a code span, a shell snippet, an
        # unclosed fence (whose four-space closer closes nothing, so the rest stays code) and a format
        # character in fenced or code-span text are all unread
        check("preflight/p6-fence-quiet", [p6(text) for text in (
            "```\nINPUT: {T}/missing.md\n```\n", "~~~\nInput: {T}/missing.md\n~~~\n",
            "A quoted workflow:\n```yaml\non:\n  workflow_dispatch:\n    inputs:\n      target:\n```\nRead it.\n",
            "```\nINPUT: {T}/i" + chr(0x200B) + "n.md\n```\n")], [(0, [])] * 4)
        check("preflight/p6-code-quiet", [p6(text) for text in (
            "\tINPUT: {T}/missing.md\n", "Intro.\n\n    INPUT: {T}/missing.md\n",
            "Intro.\n\n    inputs:\n      target: x\n", "`INPUT: {T}/missing.md`\n",
            "`cat input | wc -l`\n", "```\ncode\n    ```\nINPUT: {T}/missing.md\n",
            "a `co" + chr(0x200B) + "de` span\n")], [(0, [])] * 7)
        table = parse_table(TABLE_FIXTURE.encode("utf-8"))
        second = evaluate("INPUT: {T}/in.md\nINPUT: {T}/missing.md\n".replace("{T}", str(inputs)).encode("utf-8"),
                          "alpha", table)
        check("preflight/p6-second-line-named", (second[0], [(rule, line) for rule, _, line, _, _ in second[1]]),
              (1, [("P6", 2)]))
        check("preflight/p6-shape-cannot-evaluate", [p6(text) for text in (
            "INPUT: <scratch>/in.md\n", "INPUT: $HOME/in.md\n", "INPUT: ~/in.md\n", "INPUT: docs/in.md\n",
            "INPUT: {T}/a/../in.md\n", "INPUT: {T}/*.md\n", "INPUT: {T}/{a,b}.md\n", "INPUT:\n", "INPUT: \"\"\n",
            "INPUT: \"{T}/in.md\n", "INPUT: {T}/dir with space/in.md\n", "**INPUT: {T}/in.md\n",
            "INPUT:** {T}/in.md\n")], ["CannotEvaluate"] * 13)
        check("preflight/p6-look-alike-cannot-evaluate", [p6(text) for text in (
            "Input: {T}/in.md\n", "input: {T}/in.md\n", "INPUTS: {T}/in.md\n",
            "INPUT : {T}/in.md\n", chr(0x0456) + "nput: {T}/in.md\n", "**INPUT: {T}/in.md\n",
            "x\nInput: {T}/in.md\n")], ["CannotEvaluate"] * 7)
        # the look-alike scan reads each rendered paragraph line's, heading's or table cell's start, past
        # leading dashes, bullets and bare markup marks: a task-list item, another bullet, inline HTML
        # around the label, a dash prefix, a blockquoted label, a heading, a header cell that is exactly
        # the token, and a label line of an HTML block or comment are never ignored
        check("preflight/p6-look-alike-start-cannot-evaluate", [p6(text) for text in (
            "- [ ] INPUT: {T}/in.md\n", chr(0x2022) + " INPUT: {T}/in.md\n",
            "<b>INPUT:</b> {T}/in.md\n",
            chr(0x2013) + " INPUT: {T}/in.md\n", chr(0x2014) + " INPUT: {T}/in.md\n",
            "> INPUT: {T}/in.md\n", "## Inputs:\n",
            "| INPUT | x |\n|---|---|\n| a | b |\n",
            "<p>INPUT: {T}/in.md</p>\n", "<!--\nINPUT: {T}/in.md\n-->\n")], ["CannotEvaluate"] * 10)
        # the bounded confusable fold serves the declaration itself: Cyrillic and Greek I and a colon-like
        # character fold to the exact label, which declares and is judged on the host
        check("preflight/p6-confusable-declares", [p6(text) for text in (
            chr(0x0406) + "NPUT: {T}/in.md\n", chr(0x0399) + "NPUT: {T}/in.md\n",
            "INPUT" + chr(0xA789) + " {T}/in.md\n", "INPUT" + chr(0x2236) + " {T}/in.md\n")], [(0, [])] * 4)
        check("preflight/p6-confusable-missing-hard", [p6(text) for text in (
            chr(0x0406) + "NPUT: {T}/missing.md\n", chr(0x0399) + "NPUT: {T}/missing.md\n",
            "INPUT" + chr(0xA789) + " {T}/missing.md\n")], [p6_missing] * 3)
        # the named misses of the bounded fold: a word between the token and the colon declares nothing
        # and is no look-alike, and prose naming the token with no colon after it passes
        check("preflight/p6-word-miss-quiet", [p6(text) for text in (
            "INPUT FILE: {T}/missing.md\n", "The INPUT files are described here.\n",
            "Input files are below: {T}/missing.md\n")], [(0, [])] * 3)
        # a line separator other than LF, anywhere, refuses the brief before any rule runs (a form feed is
        # one here, since the parser reads it as a line ending)
        check("preflight/p6-separator-cannot-evaluate", [p6(text) for text in (
            "INPUT: {T}/in.md\r\n", "x\rINPUT: {T}/in.md\n", "x" + chr(0x2028) + "y\n",
            "x" + chr(0x2029) + "y\n", "x" + chr(0x0085) + "y\n", "x" + chr(0x0C) + "y\n")],
            ["CannotEvaluate"] * 6)
        # a format character outside a fence refuses the brief: one inside a declared path, one glued to
        # the label, a byte order mark at the brief's start and one in plain prose
        check("preflight/p6-format-cannot-evaluate", [p6(text) for text in (
            "INPUT: {T}/i" + chr(0x200B) + "n.md\n", "INPUT:" + chr(0xAD) + " {T}/in.md\n",
            chr(0xFEFF) + "INPUT: {T}/in.md\n", "Read the notes" + chr(0x200B) + " first.\n")],
            ["CannotEvaluate"] * 4)
        check("preflight/p6-not-file-or-directory-cannot-evaluate",
              [p6(text) for text in ("INPUT: {T}/fifo\n", "INPUT: /dev/null\n")], ["CannotEvaluate"] * 2)
        # a uid that reads a mode-000 file (root, or a capability) cannot show the permission refusal: recorded
        enforced = not os.access(inputs / "unreadable.md", os.R_OK)
        skipped = "skipped: this uid reads a mode-000 file, so os.access cannot refuse here"
        check("preflight/p6-permission-cannot-evaluate",
              [p6(text) for text in ("INPUT: {T}/unreadable.md\n", "INPUT: {T}/locked/in.md\n")] if enforced
              else skipped, ["CannotEvaluate"] * 2 if enforced else skipped)
        if not enforced:
            print("NOTE: preflight/p6-permission-cannot-evaluate " + skipped)
        printed = io.StringIO()
        brief_path, table_path = inputs / "brief.md", inputs / "table.toml"
        with open(brief_path, "w", encoding="utf-8") as handle:
            handle.write("INPUT: {}/missing.md\n".format(inputs))
        with open(table_path, "w", encoding="utf-8") as handle:
            handle.write(TABLE_FIXTURE)
        with contextlib.redirect_stdout(printed):
            refused = run(str(brief_path), "alpha", table_path)
        check("preflight/p6-refuse-line", (refused, "declares an input that does not exist" in printed.getvalue(),
                                           INPUT_HINT in printed.getvalue()), (1, True, True))
        missing = lambda: p6_code("INPUT: {T}/missing.md\n")  # noqa: E731
        check("revert/p6-red", *flip([missing], [0], rule_p6=nothing))
        check("revert/p6-probe-red", *flip([missing], [0], input_state=lambda path: (None, None)))
        check("revert/p6-eacces-red", *flip([unreadable_code], [1], MISSING_ERRNOS=MISSING_ERRNOS | {errno.EACCES}))
        check("revert/p6-near-input-red", *parser_flip([lambda: p6_code("Input: {T}/in.md\n")], [0],
                                                       NEAR_INPUT_RE=never))
        check("revert/p6-shape-red", *parser_flip([lambda: p6_code("INPUT: {T}/*.md\n")], [1],
                                                  INPUT_SHAPE_CHARACTERS=frozenset()))
        # the TEXT gate: with the separator refusal patched out, a U+2028 after the path is read as
        # trailing whitespace and the declaration passes; with it, the brief is refused
        check("revert/p6-separator-red", *parser_flip(
            [lambda: p6_code("INPUT: {T}/in.md" + chr(0x2028) + "\n")], [0], SEPARATOR_RE=never))
        # with the format-character refusal patched out AND the declarations read from the stripped text,
        # the zero-width space is removed silently and the declaration resolves to the present in.md: the
        # silent pass the gate and the raw read replaced (either seam alone still refuses or goes HARD)
        check("revert/p6-format-red", *flip(
            [lambda: p6_code("INPUT: {T}/i" + chr(0x200B) + "n.md\n")], [0],
            forbidden_text=lambda text: None,
            read_inputs=lambda text: _input_declarations.read(
                "".join(ch for ch in text if unicodedata.category(ch) != "Cf"))))
        # with the code-block rule patched out, a quoted workflow's indented inputs: key is a look-alike
        check("revert/p6-fence-red", *parser_flip(
            [lambda: p6_code("```yaml\non:\n  workflow_dispatch:\n    inputs:\n      target:\n```\n")],
            ["CannotEvaluate"], CODE_BLOCKS_ARE_CODE=False))
        # with the code-span rule patched out, a label quoted in backticks is a look-alike again
        check("revert/p6-code-span-red", *parser_flip(
            [lambda: p6_code("`Input: {T}/in.md`\n")], ["CannotEvaluate"], CODE_SPANS_ARE_CODE=False))
        # with the ignored categories emptied, a combining character splits the label unseen
        check("revert/p6-ignored-categories-red", *parser_flip(
            [lambda: p6_code("I" + chr(0x034F) + "NPUT: {T}/missing.md\n")], [0], IGNORED_CATEGORIES=()))
        # with quoted paragraphs declaring, a blockquoted INPUT line is a declaration, not a look-alike
        check("revert/p6-declaring-kind-red", *parser_flip(
            [lambda: p6_code("> INPUT: {T}/missing.md\n")], [1],
            unit_declares=lambda kind: kind in ("paragraph", "quoted")))
        # with the cell tokens emptied, a header cell that is exactly INPUT passes
        check("revert/p6-cell-token-red", *parser_flip(
            [lambda: p6_code("| INPUT | x |\n|---|---|\n| a | b |\n")], [0], CELL_TOKENS=()))
        # with the confusable table emptied, a Cyrillic I hides the label (and its missing path) again
        check("revert/p6-confusable-red", *parser_flip(
            [lambda: p6_code(chr(0x0406) + "NPUT: {T}/missing.md\n")], [0], CONFUSABLES={}))
    finally:
        if os.path.lexists(locked):
            os.chmod(locked, 0o700)
        shutil.rmtree(inputs, ignore_errors=True)
    check("revert/every-flip-ran", len(flipped), 200)

    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("FAIL: check_brief_capability self-test")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("PASS: check_brief_capability self-test: {} unique checks executed (the shipped table loads and a "
          "malformed table exits 2 through main; a table that fails to parse (a parser recursion, an over-long "
          "integer, an injected memory exhaustion) exits 2 at check_table and at run, each printing the "
          "refusal and no traceback marker, and a checks manifest that fails the same ways gets the harness "
          "refusal and None from its reader and the refusal and exit 2 from --self-test, each on its own "
          "transcript, with both tomllib parsers restored after every injected block; S1, S2 and S3 "
          "refuse executable fences and command lines in numbered "
          "items and outside them, a command line is INFO only as a whole-sentence prohibition or past report "
          "that no other sentence of its void group (lazy lines, nested items, indented headings and fence contents "
          "included) voids, quoted source text is not a data context and a command-line refusal says how to "
          "rewrite, and every blocking rule, command-line form, closed-shape "
          "guard and void patched out flips its fixture; R1, R5, R6 and R7 print ADVISORY lines and never refuse; "
          "the preflight, for a family that cannot read outside a declared workdir (P2), judges every slash at a "
          "word's start or after a character that is not a letter, digit, `.` or `_` (a quoted span is one word "
          "with the text glued to it, http and https words skipped, file:// judged) from that slash to the word's "
          "end, refuses each one not under the workdir (over-refusing odd words such as /w/repo//a.md) or that it "
          "cannot place, unless the word starts after a prohibition on its line, and exits 2 on a workdir that "
          "normalises to / or holds `..` or on a workdir label line that is not a declaration; it prints a "
          "path missing at a pinned revision of 7 to 40 or 64 hex digits read with git cat-file after `..` is folded (P3; an unresolved pin, or a pin label with any "
          "other value, exits 2), P1 and P4 as ADVISORY, refuses a pin reached by a git command line through a "
          "ref that can move (a branch, a clone's --branch value however it is spelt, a fetch with no refspec, "
          "FETCH_HEAD after a moving fetch, a merge, rebase, restore --source, archive or path restore of a "
          "branch, a reset in any mode, a reach split by a backslash or after a git word at a line's end, a "
          "global option quoted with a space) with no later cure line (git checkout or switch --detach, or git -C "
          "<dir> checkout, with a closed -C path whose <name> placeholders are whole segments, of the brief's one "
          "full 40-hex BRIEF-PIN value, bare or followed by ^{{commit}}, which is a fixed ref wherever a reach names "
          "it, as the whole of one line after at most one list marker, `$` or then (in a fence only `$`), bare or "
          "as one code span, or "
          "ending one code span that is the rest of a line opening with Make a scratch clone:, Clone or Run and is "
          "exactly a closed clone, && and the command in the clone's directory; a line read before format "
          "characters are removed that is not printable ASCII with space or tab between words, a line in an HTML "
          "comment, and any other command, place, pin or joined line cure nothing, and a cure cures only the "
          "reaches before it; pin lines that disagree, and a look-alike pin line (case, compatibility forms and "
          "dashes folded, backticks and table pipes skipped) holding another commit, exit 2 naming both lines), "
          "gives a cure line to copy in the refusal, judges "
          "every reach that is not the object of a prohibition (the prohibited verb plain or in emphasis; a "
          "negation before another verb, a prohibition word in a code span or after `=`, a second listed command "
          "line and a later clause are judged), and prints one reached from prose, gh pr checkout, a web address "
          "naming a branch or a "
          "submodule update --remote (P5), refuses a declared input (a declaring paragraph line of the parsed "
          "CommonMark document whose rendered, folded text begins with INPUT:, read by the parser C1 shares; "
          "code declares nothing and is not checked) missing on this host, a dangling "
          "link or a path under a file included, never a path in prose, and "
          "exits 2 on an INPUT line it cannot check, a look-alike label opening a rendered paragraph line, "
          "heading or table cell (folded through the bounded confusable table), a brief holding a non-LF line "
          "separator or a format character outside code, a brief the vendored parser cannot read, a FIFO or "
          "device, or an input this uid "
          "cannot read (P6), and "
          "each preflight rule and guard patched out flips its fixture; "
          "the regex audit reads the module-level patterns and COMMAND_FORMS (and, without claiming completeness, "
          "container and class globals and the production source's re calls), flags no pattern, "
          "detects the previous revisions' and loses its canary with each guard patched out; 37 repeated shapes "
          "each evaluate in linear time at 1 MiB in a capped child that must exit 0; scope keeps and drops "
          "numbered items, other families' legs, sections and tail markers; undecodable, blank, oversized, "
          "missing, non-regular, backwards-range and unknown-family input exits 2); execution set reconciled "
          "against tools/selftest_checks.toml".format(
              len(EXECUTED)))
    return 0


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main(sys.argv[1:]))
