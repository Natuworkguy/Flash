r"""LaTeX math -> Unicode rendering for the terminal.

Replies often contain TeX math ($...$, $$...$$, \(...\), \[...\]).
Rich's Markdown renderer prints the delimiters and command names
verbatim, so a clean formula arrives looking like source code.  This
module rewrites math spans into plain Unicode ("\frac{\pi}{2}" ->
"pi/2") before the Markdown pass, leaving the rest of the reply --
prose, fenced blocks, inline code -- untouched.

Output is deliberately linear (one line per equation) rather than 2-D
stacked: it has to survive Live() re-renders, narrow terminals and
copy-paste out of the scrollback.
"""

import re

# --- character tables -------------------------------------------------

_SUP = {
    "0": "\u2070", "1": "\u00b9", "2": "\u00b2", "3": "\u00b3",
    "4": "\u2074", "5": "\u2075", "6": "\u2076", "7": "\u2077",
    "8": "\u2078", "9": "\u2079", "+": "\u207a", "-": "\u207b",
    "\u2212": "\u207b", "=": "\u207c", "(": "\u207d", ")": "\u207e",
    "a": "\u1d43", "b": "\u1d47", "c": "\u1d9c", "d": "\u1d48",
    "e": "\u1d49", "f": "\u1da0", "g": "\u1d4d", "h": "\u02b0",
    "i": "\u2071", "j": "\u02b2", "k": "\u1d4f", "l": "\u02e1",
    "m": "\u1d50", "n": "\u207f", "o": "\u1d52", "p": "\u1d56",
    "r": "\u02b3", "s": "\u02e2", "t": "\u1d57", "u": "\u1d58",
    "v": "\u1d5b", "w": "\u02b7", "x": "\u02e3", "y": "\u02b8",
    "z": "\u1dbb", "T": "\u1d40",
}

_SUB = {
    "0": "\u2080", "1": "\u2081", "2": "\u2082", "3": "\u2083",
    "4": "\u2084", "5": "\u2085", "6": "\u2086", "7": "\u2087",
    "8": "\u2088", "9": "\u2089", "+": "\u208a", "-": "\u208b",
    "\u2212": "\u208b", "=": "\u208c", "(": "\u208d", ")": "\u208e",
    "a": "\u2090", "e": "\u2091", "h": "\u2095", "i": "\u1d62",
    "j": "\u2c7c", "k": "\u2096", "l": "\u2097", "m": "\u2098",
    "n": "\u2099", "o": "\u2092", "p": "\u209a", "r": "\u1d63",
    "s": "\u209b", "t": "\u209c", "u": "\u1d64", "v": "\u1d65",
    "x": "\u2093",
}

_BLACKBOARD = {
    "C": "\u2102", "H": "\u210d", "N": "\u2115", "P": "\u2119",
    "Q": "\u211a", "R": "\u211d", "Z": "\u2124",
}

_GREEK = {
    "alpha": "\u03b1", "beta": "\u03b2", "gamma": "\u03b3",
    "delta": "\u03b4", "epsilon": "\u03b5", "varepsilon": "\u03b5",
    "zeta": "\u03b6", "eta": "\u03b7", "theta": "\u03b8",
    "vartheta": "\u03d1", "iota": "\u03b9", "kappa": "\u03ba",
    "lambda": "\u03bb", "mu": "\u03bc", "nu": "\u03bd", "xi": "\u03be",
    "pi": "\u03c0", "varpi": "\u03d6", "rho": "\u03c1",
    "varrho": "\u03f1", "sigma": "\u03c3", "varsigma": "\u03c2",
    "tau": "\u03c4", "upsilon": "\u03c5", "phi": "\u03c6",
    "varphi": "\u03d5", "chi": "\u03c7", "psi": "\u03c8",
    "omega": "\u03c9", "Gamma": "\u0393", "Delta": "\u0394",
    "Theta": "\u0398", "Lambda": "\u039b", "Xi": "\u039e",
    "Pi": "\u03a0", "Sigma": "\u03a3", "Upsilon": "\u03a5",
    "Phi": "\u03a6", "Psi": "\u03a8", "Omega": "\u03a9",
}

# Relations and binary operators get breathing room when joined.
_REL = {
    "=": "=", "neq": "\u2260", "ne": "\u2260", "leq": "\u2264",
    "le": "\u2264", "geq": "\u2265", "ge": "\u2265", "ll": "\u226a",
    "gg": "\u226b", "approx": "\u2248", "simeq": "\u2243",
    "sim": "\u223c", "cong": "\u2245", "equiv": "\u2261",
    "propto": "\u221d", "in": "\u2208", "notin": "\u2209",
    "ni": "\u220b", "subset": "\u2282", "subseteq": "\u2286",
    "supset": "\u2283", "supseteq": "\u2287", "perp": "\u22a5",
    "parallel": "\u2225", "mid": "\u2223", "to": "\u2192",
    "rightarrow": "\u2192", "Rightarrow": "\u21d2",
    "leftarrow": "\u2190", "Leftarrow": "\u21d0",
    "leftrightarrow": "\u2194", "Leftrightarrow": "\u21d4",
    "mapsto": "\u21a6", "implies": "\u21d2", "iff": "\u21d4",
    "<": "<", ">": ">",
}

_BIN = {
    "+": "+", "-": "\u2212", "times": "\u00d7", "div": "\u00f7",
    "cdot": "\u00b7", "pm": "\u00b1", "mp": "\u2213", "ast": "*",
    "star": "\u22c6", "circ": "\u2218", "bullet": "\u2219",
    "oplus": "\u2295", "ominus": "\u2296", "otimes": "\u2297",
    "cup": "\u222a", "cap": "\u2229", "setminus": "\\",
    "wedge": "\u2227", "vee": "\u2228", "land": "\u2227",
    "lor": "\u2228",
}

_ORD = {
    "infty": "\u221e", "partial": "\u2202", "nabla": "\u2207",
    "forall": "\u2200", "exists": "\u2203", "nexists": "\u2204",
    "emptyset": "\u2205", "varnothing": "\u2205", "neg": "\u00ac",
    "lnot": "\u00ac", "aleph": "\u2135", "hbar": "\u210f",
    "ell": "\u2113", "Re": "\u211c", "Im": "\u2111",
    "prime": "\u2032", "degree": "\u00b0", "angle": "\u2220",
    "triangle": "\u25b3", "square": "\u25a1", "checkmark": "\u2713",
    "therefore": "\u2234", "because": "\u2235", "top": "\u22a4",
    "bot": "\u22a5", "surd": "\u221a",
    "ldots": "\u2026", "dots": "\u2026", "cdots": "\u22ef",
    "vdots": "\u22ee", "ddots": "\u22f1",
    "%": "%", "&": "&", "#": "#", "$": "$", "_": "_",
    "{": "{", "}": "}", "|": "\u2016",
}

# Big operators: limits attach directly, with no intervening space.
_BIG = {
    "int": "\u222b", "iint": "\u222c", "iiint": "\u222d",
    "oint": "\u222e", "sum": "\u2211", "prod": "\u220f",
    "coprod": "\u2210", "bigcup": "\u22c3", "bigcap": "\u22c2",
    "bigoplus": "\u2a01", "bigotimes": "\u2a02", "bigvee": "\u22c1",
    "bigwedge": "\u22c0",
}

# Named operators render as-is: \sin -> sin.
_NAMED = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos",
    "arctan", "sinh", "cosh", "tanh", "coth", "exp", "log", "ln",
    "lg", "det", "dim", "ker", "deg", "gcd", "hom", "arg", "max",
    "min", "sup", "inf", "lim", "limsup", "liminf", "mod", "Pr",
}

_DELIM = {
    "(": "(", ")": ")", "[": "[", "]": "]", "\\{": "{", "\\}": "}",
    "|": "|", "\\|": "\u2016", "\\langle": "\u27e8",
    "\\rangle": "\u27e9", "\\lvert": "|", "\\rvert": "|",
    "\\lVert": "\u2016", "\\rVert": "\u2016", "\\lfloor": "\u230a",
    "\\rfloor": "\u230b", "\\lceil": "\u2308", "\\rceil": "\u2309",
    ".": "", "/": "/", "\\backslash": "\\",
}

_ACCENTS = {
    "hat": "\u0302", "widehat": "\u0302", "bar": "\u0304",
    "overline": "\u0304", "vec": "\u20d7", "tilde": "\u0303",
    "widetilde": "\u0303", "dot": "\u0307", "ddot": "\u0308",
    "check": "\u030c", "breve": "\u0306", "acute": "\u0301",
    "grave": "\u0300",
}

# Commands that only carry styling or spacing -- unwrap or drop them.
_UNWRAP = {
    "text", "textrm", "textit", "textbf", "texttt", "mathrm",
    "mathit", "mathbf", "mathsf", "mathtt", "mathcal", "mathscr",
    "mathfrak", "operatorname", "operatorname*", "boldsymbol", "bm",
}
_SPACERS = {
    ",", ";", ":", "!", " ", "quad", "qquad", "thinspace",
    "enspace", "medspace", "thickspace", "negthinspace", "\n",
}
_DROP = {
    "displaystyle", "textstyle", "scriptstyle", "limits",
    "nolimits", "notag", "nonumber",
}

# --- tokenizer / parser -----------------------------------------------

_CMD_RE = re.compile(r"\\(?:[A-Za-z]+\*?|.)", re.S)

# Atom kinds, used only to decide spacing and parenthesisation.
_ORD_K, _REL_K, _BIN_K, _OPEN_K, _CLOSE_K, _PUNCT_K, _OP_K = range(7)


class _Atom:
    __slots__ = ("text", "kind", "atomic")

    def __init__(self, text, kind=_ORD_K, atomic=True):
        self.text = text
        self.kind = kind
        self.atomic = atomic


def _tokenize(source):
    tokens = []
    index, length = 0, len(source)
    while index < length:
        char = source[index]
        if char == "\\":
            match = _CMD_RE.match(source, index)
            if match is None:                 # a lone trailing backslash
                tokens.append(char)
                index += 1
                continue
            tokens.append(match.group(0))
            index = match.end()
        elif char in "{}^_&":
            tokens.append(char)
            index += 1
        elif char.isspace():
            while index < length and source[index].isspace():
                index += 1
            tokens.append(" ")
        else:
            tokens.append(char)
            index += 1
    return tokens


def _script(body, table, marker):
    """Raise or lower `body`, falling back to ^/_ when Unicode can't."""
    body = body.replace(" ", "")      # "n = 1" reads better as a tight "n=1"
    if body and all(char in table for char in body):
        return "".join(table[char] for char in body)
    if len(body) <= 1:
        return marker + body
    return f"{marker}({body})"


class _Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.pos += 1
        return token

    def skip_space(self):
        while self.peek() == " ":
            self.pos += 1

    def argument(self):
        """One argument: a {...} group, or else the next single token."""
        self.skip_space()
        token = self.peek()
        if token is None:
            return _Atom("")
        if token == "{":
            self.pos += 1
            group = _join(self.parse(stop="}"))
            if self.peek() == "}":
                self.pos += 1
            return group
        self.pos += 1
        return self.atom(token) or _Atom("")

    def optional(self):
        """An optional [...] argument, e.g. the index of \\sqrt."""
        self.skip_space()
        if self.peek() != "[":
            return None
        self.pos += 1
        depth, inner = 1, []
        while self.peek() is not None:
            token = self.take()
            if token == "[":
                depth += 1
            elif token == "]":
                depth -= 1
                if depth == 0:
                    break
            inner.append(token)
        return _join(_Parser(inner).parse())

    def parse(self, stop=None):
        atoms = []
        while True:
            token = self.peek()
            if token is None or token == stop:
                break
            self.pos += 1
            if token == " ":
                if atoms:
                    atoms.append(_Atom(" ", _PUNCT_K))
                continue
            if token in ("^", "_"):
                table, marker = (_SUP, "^") if token == "^" else (_SUB, "_")
                script = _script(self.argument().text, table, marker)
                while atoms and atoms[-1].kind == _PUNCT_K:
                    atoms.pop()
                if atoms:
                    atoms[-1].text += script
                else:
                    atoms.append(_Atom(script))
                continue
            atom = self.atom(token)
            if atom is not None:
                atoms.append(atom)
        return atoms

    def atom(self, token):
        if token == "}":
            return None
        if token == "{":
            group = _join(self.parse(stop="}"))
            if self.peek() == "}":
                self.pos += 1
            return group
        if token.startswith("\\"):
            return self.command(token, token[1:])
        if token in "+-":
            return _Atom(_BIN[token], _BIN_K)
        if token in "=<>":
            return _Atom(token, _REL_K)
        if token in "([":
            return _Atom(token, _OPEN_K)
        if token in ")]":
            return _Atom(token, _CLOSE_K)
        if token in ",;":
            return _Atom(token, _PUNCT_K)
        if token == "&":
            return _Atom(" ", _PUNCT_K)
        if token == "'":
            return _Atom("\u2032")
        return _Atom(token)

    def command(self, token, name):
        if token == "\\\\":
            return _Atom("\n", _PUNCT_K)
        if name in _SPACERS:
            return _Atom(" ", _PUNCT_K)
        if name in _DROP:
            return None
        if name in ("begin", "end"):
            self.argument()
            return None
        if name in ("left", "right"):
            self.skip_space()
            delim = self.take() or ""
            if delim == "\\":                     # \left\{ tokenizes apart
                delim += self.take() or ""
            text = _DELIM.get(delim, delim)
            if not text:                          # \left. / \right. vanish
                return None
            return _Atom(text, _OPEN_K if name == "left" else _CLOSE_K)
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            top, bottom = self.argument(), self.argument()
            over = _bracket(top, numerator=True)
            under = _bracket(bottom)
            return _Atom(f"{over}/{under}", _ORD_K, atomic=False)
        if name == "sqrt":
            index = self.optional()
            body = self.argument()
            root = "\u221a"
            if index is not None and index.text:
                root = {"2": "\u221a", "3": "\u221b", "4": "\u221c"}.get(
                    index.text
                ) or _script(index.text, _SUP, "^") + "\u221a"
            inner = body.text if body.atomic else f"({body.text})"
            return _Atom(root + inner, _ORD_K, atomic=False)
        if name in _ACCENTS:
            body = self.argument()
            return _Atom(body.text + _ACCENTS[name], body.kind)
        if name == "mathbb":
            body = self.argument()
            return _Atom(
                "".join(
                    _BLACKBOARD[c] if c in _BLACKBOARD else c
                    for c in body.text
                )
            )
        if name in _UNWRAP:
            body = self.argument()
            body.kind = _OP_K
            return body
        if name in _BIG:
            return _Atom(_BIG[name], _OP_K)
        if name in _NAMED:
            return _Atom(name, _OP_K)
        if name in _GREEK:
            return _Atom(_GREEK[name])
        if name in _REL:
            return _Atom(_REL[name], _REL_K)
        if name in _BIN:
            return _Atom(_BIN[name], _BIN_K)
        if name in _ORD:
            return _Atom(_ORD[name])
        if token in _DELIM:
            return _Atom(_DELIM[token])
        return _Atom(name)          # unknown macro: fall back to its name


def _previous_kind(atoms, index):
    for atom in reversed(atoms[:index]):
        if atom.kind != _PUNCT_K or atom.text != " ":
            return atom.kind
    return _OPEN_K


def _join(atoms):
    """Concatenate atoms, spacing relations and binary operators."""
    parts = []
    pending_space = False
    was_unary = False
    for index, atom in enumerate(atoms):
        if atom.kind == _PUNCT_K and atom.text == " ":
            pending_space = True
            continue
        previous = _previous_kind(atoms, index)
        # A leading sign ("-b", "= -1") is unary, so it hugs its operand.
        unary = atom.kind == _BIN_K and previous in (_OPEN_K, _REL_K, _BIN_K)
        if parts and parts[-1] != "\n":
            gap = (
                (atom.kind in (_REL_K, _BIN_K) and not unary)
                or (previous in (_REL_K, _BIN_K) and not was_unary)
                or (pending_space
                    and atom.kind not in (_CLOSE_K, _PUNCT_K)
                    and previous != _OPEN_K)
            )
            if gap:
                parts.append(" ")
        parts.append(atom.text)
        pending_space = False
        was_unary = unary
    body = [atom for atom in atoms if atom.text.strip()]
    risky = any(a.kind in (_REL_K, _BIN_K) for a in body[1:]) or any(
        not a.atomic for a in body
    )
    return _Atom("".join(parts), _ORD_K, atomic=len(body) <= 1 and not risky)


def _bracket(atom, numerator=False):
    """Bracket a fraction half only where precedence would otherwise break."""
    text = atom.text
    if not text or atom.atomic:
        return text
    if text.startswith("(") and text.endswith(")"):
        return text
    # "sin(tx)/t" is unambiguous; "a + b/2" is not.
    if numerator and not re.search(r"[-+\u2212=<>\u2264\u2265/]", text):
        return text
    return f"({text})"


def render_math(source):
    """Render one LaTeX math expression as Unicode text."""
    try:
        text = _join(_Parser(_tokenize(source)).parse()).text
    except Exception:          # a stray macro must never break a reply
        return source.strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


# --- document-level rewriting -----------------------------------------

_PLACEHOLDER = "\x00\x00{}\x00\x00"
_PROTECT_RE = re.compile(
    r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|(?<!`)(`+)(?!`).+?(?<!`)\1(?!`)",
    re.S,
)
_DISPLAY_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
_INLINE_RE = re.compile(
    r"(?<![\\$])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?!\$)|\\\((.+?)\\\)",
    re.S,
)
_ESCAPE_RE = re.compile(r"([*_`\[\]~])")


def _looks_like_math(body):
    """Guard $...$ against prose that merely quotes prices."""
    if any(char in body for char in "\\^_{}"):
        return True
    return len(body) <= 3 and any(char.isalpha() for char in body)


def _escape(text):
    """Keep the rendered math out of Markdown's hands."""
    return _ESCAPE_RE.sub(r"\\\1", text)


def render_latex(text):
    """Replace every math span in `text` with its Unicode rendering.

    Fenced blocks and inline code are left exactly as written, and so is
    any `$...$` that reads more like currency than algebra.
    """
    if not text or ("$" not in text and "\\" not in text):
        return text

    protected = []

    def stash(match):
        protected.append(match.group(0))
        return _PLACEHOLDER.format(len(protected) - 1)

    text = _PROTECT_RE.sub(stash, text)

    def display(match):
        rendered = render_math(match.group(1) or match.group(2) or "")
        if not rendered:
            return match.group(0)
        block = "\n\n".join(
            _escape(line) for line in rendered.split("\n") if line.strip()
        )
        return f"\n\n{block}\n\n"

    def inline(match):
        body = match.group(1) or match.group(2) or ""
        if match.group(1) is not None and not _looks_like_math(body):
            return match.group(0)
        rendered = render_math(body)
        return _escape(rendered) if rendered else match.group(0)

    text = _DISPLAY_RE.sub(display, text)
    text = _INLINE_RE.sub(inline, text)

    for index, original in enumerate(protected):
        text = text.replace(_PLACEHOLDER.format(index), original)
    return re.sub(r"\n{3,}", "\n\n", text)
