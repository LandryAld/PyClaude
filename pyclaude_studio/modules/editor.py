"""Éditeur de code Python : coloration, numéros de ligne, structure, exécution en sous-processus."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

from ..core.colors import mix
from ..core.commands import Command
from ..core.module import StudioModule
from ..core.tasks import Cancelled
from ..engines import syntax
from ..ui.widgets import ToolTip, themed_canvas

BRACKETS = {"(": ")", "[": "]", "{": "}"}
CLOSING = {v: k for k, v in BRACKETS.items()}


class EditorTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, module: "EditorModule", path: Optional[str] = None,
                 content: str = "") -> None:
        super().__init__(parent, style="Module.TFrame")
        self.module = module
        self.path = path
        self.modified = False
        self._hl_after: Optional[str] = None
        self._check_after: Optional[str] = None
        self.error: Optional[syntax.Diagnostic] = None
        self.gutter = themed_canvas(self, role="canvas", width=52)
        self.gutter.pack(side="left", fill="y")
        self.vbar = ttk.Scrollbar(self, orient="vertical")
        self.vbar.pack(side="right", fill="y")
        self.text = tk.Text(self, wrap="none", undo=True, autoseparators=True, maxundo=-1,
                            font="StudioMono", relief="flat", borderwidth=0, padx=10, pady=6,
                            spacing1=1, spacing3=1)
        # Tabulation affichée sur 4 caractères (la touche Tab insère des espaces)
        self.text.configure(tabs=(tkfont.nametofont("StudioMono").measure(" " * 4),))
        self.text._studio_role = "code"  # type: ignore[attr-defined]
        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(yscrollcommand=self._on_scroll)
        self.vbar.configure(command=self._yview)
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.text.edit_modified(False)
        t = self.text
        t.bind("<<Modified>>", self._on_modified)
        t.bind("<KeyRelease>", lambda e: self._cursor_moved())
        t.bind("<ButtonRelease-1>", lambda e: self._cursor_moved())
        t.bind("<Configure>", lambda e: self.draw_gutter())
        t.bind("<Return>", self._newline)
        t.bind("<KP_Enter>", self._newline)
        t.bind("<Tab>", self._tab)
        t.bind("<ISO_Left_Tab>", self._untab)
        t.bind("<Shift-Tab>", self._untab)
        t.bind("<Control-slash>", lambda e: (self.toggle_comment(), "break")[1])
        t.bind("<Control-d>", lambda e: (self.duplicate_line(), "break")[1])
        t.bind("<Control-a>", lambda e: (t.tag_add("sel", "1.0", "end"), "break")[1])
        t.bind("<Control-y>", lambda e: (self._redo(), "break")[1])
        t.bind("<Control-Z>", lambda e: (self._redo(), "break")[1])
        t.bind("<BackSpace>", self._backspace)
        # Raccourcis liés au widget : ils priment sur les liaisons « emacs » de la classe Text
        # (Ctrl+O insère une ligne, Ctrl+F avance d'un caractère…)
        for seq, action in (("<Control-o>", module.open_file), ("<Control-n>", module.new_file),
                            ("<Control-s>", module.save), ("<Control-f>", module.show_find),
                            ("<F5>", module.run), ("<Control-Return>", module.run_in_console)):
            t.bind(seq, lambda e, a=action: (a(), "break")[1])
        for opening, closing in BRACKETS.items():
            t.bind(closing, lambda e, c=closing: self._skip_closing(c))
        self.apply_theme()
        self.highlight()
        self.after(50, self.draw_gutter)

    # -- affichage -------------------------------------------------------------
    @property
    def title(self) -> str:
        name = os.path.basename(self.path) if self.path else "sans titre.py"
        return ("● " if self.modified else "") + name

    def apply_theme(self) -> None:
        t = self.module.theme
        syn = t.syntax
        txt = self.text
        txt.configure(background=t.canvas, foreground=t.fg, insertbackground=t.accent,
                      selectbackground=t.select_bg, inactiveselectbackground=t.select_bg,
                      insertwidth=2)
        for tag in syntax.TAGS:
            color = {"defname": syn["def"], "builtin": syn["builtin"]}.get(tag, syn.get(tag, t.fg))
            txt.tag_configure(tag, foreground=color)
        txt.tag_configure("comment", font=("StudioMono",))
        txt.tag_configure("curline", background=mix(t.canvas, t.fg, 0.045))
        txt.tag_configure("bracket", background=syn["match"], foreground=t.fg)
        txt.tag_configure("errorline", background=mix(t.canvas, t.error, 0.18), underline=True)
        txt.tag_configure("found", background=mix(t.canvas, t.warning, 0.4))
        txt.tag_raise("sel")
        self.gutter.configure(background=t.canvas)
        self.draw_gutter()

    def _on_scroll(self, first: str, last: str) -> None:
        self.vbar.set(first, last)
        self.draw_gutter()

    def _yview(self, *args: Any) -> None:
        self.text.yview(*args)
        self.draw_gutter()

    def draw_gutter(self) -> None:
        g = self.gutter
        t = self.module.theme
        g.delete("all")
        idx = self.text.index("@0,0")
        cur = int(self.text.index("insert").split(".")[0])
        err = self.error.line if self.error else -1
        while True:
            info = self.text.dlineinfo(idx)
            if info is None:
                break
            line = int(idx.split(".")[0])
            y = info[1] + info[3] / 2
            color = t.fg if line == cur else t.muted
            if line == err:
                g.create_oval(6, y - 4, 14, y + 4, fill=t.error, outline="")
                color = t.error
            g.create_text(46, y, text=str(line), anchor="e", fill=color, font="StudioMono")
            nxt = self.text.index(f"{idx}+1line")
            if nxt == idx:
                break
            idx = nxt
        g.create_line(51, 0, 51, g.winfo_height(), fill=t.grid)

    # -- coloration ---------------------------------------------------------------
    def highlight(self) -> None:
        self._hl_after = None
        txt = self.text
        src = txt.get("1.0", "end-1c")
        for tag in syntax.TAGS:
            txt.tag_remove(tag, "1.0", "end")
        index = syntax.LineIndex(src)
        for kind, a, b in syntax.highlight(src):
            txt.tag_add(kind, index.index(a), index.index(b))
        self.module.refresh_outline(self)

    def _on_modified(self, _e: Any = None) -> None:
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if not self.modified:
            self.modified = True
            self.module.update_tab_title(self)
        if self._hl_after:
            self.after_cancel(self._hl_after)
        self._hl_after = self.after(160, self.highlight)
        if self._check_after:
            self.after_cancel(self._check_after)
        self._check_after = self.after(600, self.check)
        self.draw_gutter()

    def check(self) -> None:
        self._check_after = None
        self.text.tag_remove("errorline", "1.0", "end")
        self.error = syntax.check(self.text.get("1.0", "end-1c"))
        if self.error:
            self.text.tag_add("errorline", f"{self.error.line}.0", f"{self.error.line}.end")
        self.module.show_diagnostic(self)
        self.draw_gutter()

    def _cursor_moved(self) -> None:
        txt = self.text
        txt.tag_remove("curline", "1.0", "end")
        txt.tag_add("curline", "insert linestart", "insert lineend+1c")
        txt.tag_remove("bracket", "1.0", "end")
        for pos in ("insert-1c", "insert"):
            ch = txt.get(pos)
            if ch in BRACKETS or ch in CLOSING:
                match = self._find_match(txt.index(pos), ch)
                if match:
                    txt.tag_add("bracket", pos)
                    txt.tag_add("bracket", match)
                break
        line, col = txt.index("insert").split(".")
        self.module.set_position(int(line), int(col))
        self.draw_gutter()

    def _find_match(self, index: str, ch: str) -> Optional[str]:
        txt = self.text
        forward = ch in BRACKETS
        target = BRACKETS.get(ch) or CLOSING[ch]
        depth = 0
        pos = index
        for _ in range(20000):
            c = txt.get(pos)
            if c == ch:
                depth += 1
            elif c == target:
                depth -= 1
                if depth == 0:
                    return pos
            nxt = txt.index(f"{pos}+1c" if forward else f"{pos}-1c")
            if nxt == pos:
                return None
            pos = nxt
        return None

    # -- édition assistée -----------------------------------------------------
    def _newline(self, _e: Any) -> str:
        txt = self.text
        line = txt.get("insert linestart", "insert")
        indent = re.match(r"[ \t]*", line).group()  # type: ignore[union-attr]
        stripped = line.rstrip()
        if stripped.endswith(":"):
            indent += "    "
        elif re.match(r"\s*(return|pass|break|continue|raise)\b", line):
            indent = indent[:-4] if len(indent) >= 4 else ""
        txt.edit_separator()
        txt.insert("insert", "\n" + indent)
        txt.see("insert")
        return "break"

    def _tab(self, _e: Any) -> str:
        txt = self.text
        if txt.tag_ranges("sel"):
            first = int(txt.index("sel.first").split(".")[0])
            last = int(txt.index("sel.last").split(".")[0])
            for ln in range(first, last + 1):
                txt.insert(f"{ln}.0", "    ")
        else:
            txt.insert("insert", "    ")
        return "break"

    def _untab(self, _e: Any) -> str:
        txt = self.text
        if txt.tag_ranges("sel"):
            first = int(txt.index("sel.first").split(".")[0])
            last = int(txt.index("sel.last").split(".")[0])
        else:
            first = last = int(txt.index("insert").split(".")[0])
        for ln in range(first, last + 1):
            head = txt.get(f"{ln}.0", f"{ln}.4")
            n = len(head) - len(head.lstrip(" "))
            if n:
                txt.delete(f"{ln}.0", f"{ln}.{n}")
        return "break"

    def _backspace(self, _e: Any) -> Optional[str]:
        txt = self.text
        if txt.tag_ranges("sel"):
            return None
        before = txt.get("insert linestart", "insert")
        if before and before.strip() == "" and len(before) % 4 == 0:
            txt.delete("insert-4c", "insert")  # supprime un niveau d'indentation
            return "break"
        return None

    def _skip_closing(self, ch: str) -> Optional[str]:
        if self.text.get("insert") == ch:
            self.text.mark_set("insert", "insert+1c")
            return "break"
        return None

    def toggle_comment(self) -> None:
        txt = self.text
        if txt.tag_ranges("sel"):
            first = int(txt.index("sel.first").split(".")[0])
            last = int(txt.index("sel.last").split(".")[0])
        else:
            first = last = int(txt.index("insert").split(".")[0])
        lines = [txt.get(f"{ln}.0", f"{ln}.end") for ln in range(first, last + 1)]
        commented = all(l.lstrip().startswith("#") for l in lines if l.strip())
        txt.edit_separator()
        for ln, content in zip(range(first, last + 1), lines):
            if not content.strip():
                continue
            if commented:
                i = content.index("#")
                n = 2 if content[i:i + 2] == "# " else 1
                txt.delete(f"{ln}.{i}", f"{ln}.{i + n}")
            else:
                i = len(content) - len(content.lstrip())
                txt.insert(f"{ln}.{i}", "# ")

    def duplicate_line(self) -> None:
        txt = self.text
        line = txt.get("insert linestart", "insert lineend")
        txt.insert("insert lineend", "\n" + line)

    def _redo(self) -> None:
        try:
            self.text.edit_redo()
        except tk.TclError:
            pass

    def goto(self, line: int) -> None:
        self.text.mark_set("insert", f"{line}.0")
        self.text.see(f"{line}.0")
        self.text.focus_set()
        self._cursor_moved()

    def content(self) -> str:
        return self.text.get("1.0", "end-1c")


class EditorModule(StudioModule):
    id = "editor"
    title = "Éditeur Python"
    icon = "❯"
    category = "Développement"
    description = "Éditeur de code avec coloration syntaxique, structure (AST), diagnostic " \
                  "en direct, recherche/remplacement et exécution isolée en sous-processus."
    order = 70
    keywords = ("code", "python", "ide", "script", "programmation")

    def build(self, frame: ttk.Frame) -> None:
        self.tabs: List[EditorTab] = []
        self.task = None
        self.proc: Optional[subprocess.Popen] = None

        bar = ttk.Frame(frame, style="Toolbar.TFrame", padding=(10, 8))
        bar.pack(fill="x")
        for text, cmd, tip in (("Nouveau", self.new_file, "Ctrl+N"), ("Ouvrir…", self.open_file, "Ctrl+O"),
                               ("Enregistrer", self.save, "Ctrl+S"),
                               ("Enregistrer sous…", self.save_as, "")):
            b = ttk.Button(bar, text=text, style="Ghost.TButton", command=cmd)
            b.pack(side="left", padx=(0, 4))
            if tip:
                ToolTip(b, tip)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.run_btn = ttk.Button(bar, text="▶ Exécuter (F5)", style="Accent.TButton",
                                  command=self.run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="■ Arrêter", style="Danger.TButton",
                                   command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        b = ttk.Button(bar, text="Console du studio", style="Ghost.TButton",
                       command=self.run_in_console)
        b.pack(side="left", padx=4)
        ToolTip(b, "Exécuter la sélection (ou le fichier) dans la console Python intégrée, "
                   "avec accès à l'objet app — Ctrl+Entrée")
        ttk.Button(bar, text="⌕ Rechercher", style="Ghost.TButton",
                   command=self.show_find).pack(side="right")

        # barre de recherche (masquée)
        self.find_bar = ttk.Frame(frame, style="Toolbar.TFrame", padding=(10, 0, 10, 8))
        self.find_var, self.repl_var = tk.StringVar(), tk.StringVar()
        ttk.Label(self.find_bar, text="Chercher", style="Muted.TLabel").pack(side="left")
        fe = ttk.Entry(self.find_bar, textvariable=self.find_var, width=24)
        fe.pack(side="left", padx=4)
        fe.bind("<Return>", lambda e: self.find_next())
        fe.bind("<Escape>", lambda e: self.hide_find())
        self.find_entry = fe
        ttk.Label(self.find_bar, text="Remplacer", style="Muted.TLabel").pack(side="left", padx=(8, 0))
        ttk.Entry(self.find_bar, textvariable=self.repl_var, width=20).pack(side="left", padx=4)
        self.case_var = tk.BooleanVar(value=False)
        self.regex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.find_bar, text="Aa", variable=self.case_var).pack(side="left", padx=4)
        ttk.Checkbutton(self.find_bar, text=".*", variable=self.regex_var).pack(side="left")
        for text, cmd in (("Suivant", self.find_next), ("Remplacer", self.replace_one),
                          ("Tout", self.replace_all), ("✕", self.hide_find)):
            ttk.Button(self.find_bar, text=text, style="Ghost.TButton", command=cmd).pack(
                side="left", padx=2)
        self.find_info = ttk.Label(self.find_bar, text="", style="Muted.TLabel")
        self.find_info.pack(side="left", padx=8)

        paned = ttk.Panedwindow(frame, orient="vertical")
        paned.pack(fill="both", expand=True)
        top = ttk.Panedwindow(paned, orient="horizontal")
        paned.add(top, weight=4)
        self.notebook = ttk.Notebook(top)
        top.add(self.notebook, weight=5)
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        side = ttk.Frame(top, style="Panel.TFrame", width=240)
        top.add(side, weight=1)
        ttk.Label(side, text="STRUCTURE", style="Accent.TLabel").pack(anchor="w", padx=10, pady=(8, 4))
        self.outline = ttk.Treeview(side, show="tree", selectmode="browse")
        self.outline.pack(fill="both", expand=True, padx=(6, 6))
        self.outline.bind("<<TreeviewSelect>>", self._outline_jump)
        self.stats_lbl = ttk.Label(side, text="", style="Muted.TLabel", justify="left")
        self.stats_lbl.pack(anchor="w", padx=10, pady=6)

        out = ttk.Frame(paned, style="Panel.TFrame")
        paned.add(out, weight=1)
        head = ttk.Frame(out, style="Panel.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="SORTIE", style="Accent.TLabel").pack(side="left", padx=10, pady=4)
        self.exit_lbl = ttk.Label(head, text="", style="Muted.TLabel")
        self.exit_lbl.pack(side="left")
        ttk.Button(head, text="Effacer", style="Ghost.TButton",
                   command=lambda: self.output.delete("1.0", "end")).pack(side="right")
        self.output = tk.Text(out, height=8, font="StudioMono", relief="flat", wrap="word",
                              padx=10, pady=4, borderwidth=0, highlightthickness=0)
        self.output._studio_role = "code"  # type: ignore[attr-defined]
        self.output.pack(fill="both", expand=True)
        self.status_bar = ttk.Label(frame, text="", style="Status.Muted.TLabel", padding=(10, 2))
        self.status_bar.pack(fill="x", side="bottom")
        self._apply_output_theme()
        self.add_tab(None, syntax.SAMPLE)

    # ------------------------------------------------------------------
    # Onglets
    # ------------------------------------------------------------------
    def add_tab(self, path: Optional[str], content: str) -> EditorTab:
        tab = EditorTab(self.notebook, self, path, content)
        self.tabs.append(tab)
        self.notebook.add(tab, text=f"  {tab.title}  ")
        self.notebook.select(tab)
        tab.text.focus_set()
        tab.check()
        return tab

    @property
    def current(self) -> Optional[EditorTab]:
        try:
            sel = self.notebook.select()
        except tk.TclError:
            return None
        return next((t for t in self.tabs if str(t) == sel), None)

    def update_tab_title(self, tab: EditorTab) -> None:
        try:
            self.notebook.tab(tab, text=f"  {tab.title}  ")
        except tk.TclError:
            pass

    def _tab_changed(self) -> None:
        tab = self.current
        if tab:
            self.refresh_outline(tab)
            self.show_diagnostic(tab)

    def close_tab(self) -> None:
        tab = self.current
        if tab is None:
            return
        if tab.modified and not messagebox.askyesno(
                "Fermer", f"« {tab.title.strip('● ')} » a été modifié. Fermer sans enregistrer ?",
                parent=self.frame):
            return
        self.notebook.forget(tab)
        self.tabs.remove(tab)
        tab.destroy()
        if not self.tabs:
            self.add_tab(None, "")

    # ------------------------------------------------------------------
    # Fichiers
    # ------------------------------------------------------------------
    def new_file(self) -> None:
        self.add_tab(None, "")

    def open_file(self, path: Optional[str] = None) -> None:
        path = path or filedialog.askopenfilename(parent=self.frame, filetypes=[
            ("Python", "*.py *.pyw"), ("Texte", "*.txt *.md *.json *.csv"), ("Tous", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            self.notify(f"Ouverture impossible : {exc}", "error")
            return
        self.add_tab(path, content)
        recent = [p for p in self.app.settings.get("editor.recent") if p != path]
        self.app.settings.set("editor.recent", ([path] + recent)[:10])

    def save(self) -> bool:
        tab = self.current
        if tab is None:
            return False
        if tab.path is None:
            return self.save_as()
        try:
            with open(tab.path, "w", encoding="utf-8") as fh:
                fh.write(tab.content())
        except OSError as exc:
            self.notify(f"Enregistrement impossible : {exc}", "error")
            return False
        tab.modified = False
        self.update_tab_title(tab)
        self.status(f"Enregistré : {tab.path}")
        return True

    def save_as(self) -> bool:
        tab = self.current
        if tab is None:
            return False
        path = filedialog.asksaveasfilename(parent=self.frame, defaultextension=".py",
                                            filetypes=[("Python", "*.py"), ("Tous", "*.*")])
        if not path:
            return False
        tab.path = path
        return self.save()

    # ------------------------------------------------------------------
    # Structure et diagnostics
    # ------------------------------------------------------------------
    def refresh_outline(self, tab: EditorTab) -> None:
        if tab is not self.current:
            return
        src = tab.content()
        items = syntax.outline(src)
        if items is None:
            return  # code momentanément invalide : on garde le dernier plan connu
        tree = self.outline
        tree.delete(*tree.get_children())
        parents: Dict[int, str] = {}
        for item in items:
            icon = "◆" if item.kind == "class" else "ƒ"
            parent = parents.get(item.depth - 1, "")
            iid = tree.insert(parent, "end", text=f"{icon} {item.name}{item.signature}",
                              values=(item.line,), open=True)
            parents[item.depth] = iid
            tree.item(iid, tags=(str(item.line),))
        stats = syntax.code_stats(src)
        self.stats_lbl.configure(text=f"{stats['lignes']} lignes · {stats['code']} de code\n"
                                      f"{stats['commentaires']} commentaires")

    def _outline_jump(self, _e: Any) -> None:
        sel = self.outline.selection()
        tab = self.current
        if sel and tab:
            tags = self.outline.item(sel[0], "tags")
            if tags:
                tab.goto(int(tags[0]))

    def show_diagnostic(self, tab: EditorTab) -> None:
        if tab is not self.current:
            return
        if tab.error:
            self.status_bar.configure(text=f"✖ Ligne {tab.error.line} : {tab.error.message}",
                                      style="Error.TLabel")
        else:
            self.status_bar.configure(text="✔ Aucune erreur de syntaxe", style="Status.Muted.TLabel")

    def set_position(self, line: int, col: int) -> None:
        self.app.set_status(f"Ligne {line}, colonne {col + 1}")

    # ------------------------------------------------------------------
    # Recherche
    # ------------------------------------------------------------------
    def show_find(self) -> None:
        self.find_bar.pack(fill="x", after=self.frame.winfo_children()[0])
        tab = self.current
        if tab and tab.text.tag_ranges("sel"):
            self.find_var.set(tab.text.get("sel.first", "sel.last"))
        self.find_entry.focus_set()
        self.find_entry.select_range(0, "end")

    def hide_find(self) -> None:
        self.find_bar.pack_forget()
        if self.current:
            self.current.text.tag_remove("found", "1.0", "end")
            self.current.text.focus_set()

    def _search(self, start: str) -> Optional[Tuple[str, str]]:
        tab = self.current
        pattern = self.find_var.get()
        if not tab or not pattern:
            return None
        count = tk.IntVar()
        try:
            pos = tab.text.search(pattern, start, stopindex="end", count=count,
                                  nocase=not self.case_var.get(), regexp=self.regex_var.get())
            if not pos:
                pos = tab.text.search(pattern, "1.0", stopindex=start, count=count,
                                      nocase=not self.case_var.get(), regexp=self.regex_var.get())
        except tk.TclError:
            self.find_info.configure(text="expression invalide")
            return None
        if not pos or not count.get():
            self.find_info.configure(text="aucun résultat")
            return None
        return pos, f"{pos}+{count.get()}c"

    def find_next(self) -> None:
        tab = self.current
        if not tab:
            return
        found = self._search("insert+1c" if tab.text.tag_ranges("found") else "insert")
        tab.text.tag_remove("found", "1.0", "end")
        if found:
            a, b = found
            tab.text.tag_add("found", a, b)
            tab.text.mark_set("insert", b)
            tab.text.see(a)
            total = len(re.findall(self.find_var.get() if self.regex_var.get()
                                   else re.escape(self.find_var.get()), tab.content(),
                                   0 if self.case_var.get() else re.I)) \
                if self.find_var.get() else 0
            self.find_info.configure(text=f"{total} occurrence(s)")

    def replace_one(self) -> None:
        tab = self.current
        if tab and tab.text.tag_ranges("found"):
            a, b = tab.text.tag_ranges("found")[:2]
            tab.text.delete(a, b)
            tab.text.insert(a, self.repl_var.get())
        self.find_next()

    def replace_all(self) -> None:
        tab = self.current
        pattern = self.find_var.get()
        if not tab or not pattern:
            return
        flags = 0 if self.case_var.get() else re.I
        regex = pattern if self.regex_var.get() else re.escape(pattern)
        try:
            new, n = re.subn(regex, self.repl_var.get().replace("\\", "\\\\")
                             if not self.regex_var.get() else self.repl_var.get(),
                             tab.content(), flags=flags)
        except re.error as exc:
            self.find_info.configure(text=f"regex invalide : {exc}")
            return
        if n:
            tab.text.edit_separator()
            tab.text.delete("1.0", "end")
            tab.text.insert("1.0", new)
            tab.text.edit_separator()
        self.find_info.configure(text=f"{n} remplacement(s)")

    # ------------------------------------------------------------------
    # Exécution
    # ------------------------------------------------------------------
    def _apply_output_theme(self) -> None:
        t = self.theme
        o = self.output
        o.configure(background=t.canvas, foreground=t.fg, insertbackground=t.fg)
        o.tag_configure("err", foreground=t.error)
        o.tag_configure("info", foreground=t.muted)

    def _write(self, text: str, tag: str = "") -> None:
        self.output.insert("end", text, tag)
        self.output.see("end")

    def run(self) -> None:
        tab = self.current
        if tab is None or (self.task is not None and self.task.active):
            return
        source = tab.content()
        diag = syntax.check(source)
        if diag:
            tab.goto(diag.line)
            self.notify(f"Erreur de syntaxe ligne {diag.line} : {diag.message}", "error")
            return
        if tab.path:
            self.save()
            path, cwd, temp = tab.path, os.path.dirname(tab.path), None
        else:
            fd, temp = tempfile.mkstemp(suffix=".py", prefix="studio_")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(source)
            path, cwd = temp, None
        self.output.delete("1.0", "end")
        self._write(f"$ python {os.path.basename(tab.path or 'sans titre.py')}\n", "info")
        self.exit_lbl.configure(text="en cours…")
        self.run_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        t0 = time.perf_counter()

        def job(ctx):
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            proc = subprocess.Popen([sys.executable, "-u", path], cwd=cwd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=env,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)
            self.proc = proc
            q: "queue.SimpleQueue" = queue.SimpleQueue()

            def pump(stream, tag):
                for line in iter(stream.readline, ""):
                    q.put((tag, line))
                q.put((tag, None))

            threads = [threading.Thread(target=pump, args=(proc.stdout, ""), daemon=True),
                       threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True)]
            for th in threads:
                th.start()
            closed = 0
            while closed < 2:
                if ctx.cancelled:
                    proc.kill()
                    raise Cancelled()
                try:
                    tag, line = q.get(timeout=0.05)
                except queue.Empty:
                    continue
                if line is None:
                    closed += 1
                else:
                    ctx.partial((tag, line))
            return proc.wait()

        def finish(code: Any = None, cancelled: bool = False) -> None:
            self.proc = None
            if temp:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
            self.run_btn.state(["!disabled"])
            self.stop_btn.state(["disabled"])
            dt = time.perf_counter() - t0
            if cancelled:
                self._write("\n[processus arrêté]\n", "err")
                self.exit_lbl.configure(text=f"arrêté après {dt:.2f} s")
            else:
                self.exit_lbl.configure(text=f"code de sortie {code} · {dt:.2f} s")
                self._write(f"\n[terminé, code {code}]\n", "info" if code == 0 else "err")

        self.task = self.run_task("Exécution du script", job,
                                  on_partial=lambda d: self._write(d[1], d[0]),
                                  on_done=lambda code: finish(code),
                                  on_error=lambda msg: (self._write(msg + "\n", "err"), finish(-1)))
        sub = self.app.bus.subscribe("task.cancelled", lambda e: finish(cancelled=True)
                                     if e["task"] is self.task else None)
        self._subs.append(sub)

    def stop(self) -> None:
        if self.task is not None and self.task.active:
            self.task.cancel()
        if self.proc is not None:
            try:
                self.proc.kill()
            except OSError:
                pass

    def run_in_console(self) -> None:
        tab = self.current
        if tab is None:
            return
        txt = tab.text
        code = txt.get("sel.first", "sel.last") if txt.tag_ranges("sel") else tab.content()
        self.app.show_bottom("console")
        console = self.app.bottom.console
        console._write("\n# — depuis l'éditeur —\n", "info")
        import contextlib
        import io

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                exec(compile(code, "<éditeur>", "exec"), console.interp.locals)
            except Exception:
                import traceback

                traceback.print_exc()
        console._write(out.getvalue(), "out")
        console._write(err.getvalue(), "err")
        console._prompt()

    # ------------------------------------------------------------------
    def on_activate(self) -> None:
        if self.current:
            self.current.text.focus_set()

    def on_theme(self, theme) -> None:
        for tab in self.tabs:
            tab.apply_theme()
        self._apply_output_theme()

    def commands(self) -> List[Command]:
        return [Command("editor.run", "Exécuter le script", self.run, shortcut="F5", icon="▶"),
                Command("editor.console", "Exécuter dans la console du studio",
                        self.run_in_console, shortcut="Ctrl+Return", icon="❯"),
                Command("editor.stop", "Arrêter l'exécution", self.stop, shortcut="Shift+F5",
                        icon="■"),
                Command("editor.save", "Enregistrer le fichier", self.save, shortcut="Ctrl+S",
                        icon="⬇"),
                Command("editor.open", "Ouvrir un fichier", self.open_file, shortcut="Ctrl+O",
                        icon="⇧"),
                Command("editor.new", "Nouveau fichier", self.new_file, shortcut="Ctrl+N", icon="+"),
                Command("editor.find", "Rechercher / remplacer", self.show_find, shortcut="Ctrl+F",
                        icon="⌕"),
                Command("editor.close", "Fermer le fichier", self.close_tab, icon="✕")]

    def get_state(self) -> Dict[str, Any]:
        files = []
        for tab in self.tabs:
            if tab.path and not tab.modified:
                files.append({"path": tab.path})
            else:
                files.append({"path": tab.path, "content": tab.content()})
        return {"files": files[:12]}

    def set_state(self, state: Dict[str, Any]) -> None:
        files = state.get("files")
        if not files:
            return
        for tab in list(self.tabs):
            self.notebook.forget(tab)
            tab.destroy()
        self.tabs.clear()
        for f in files:
            path, content = f.get("path"), f.get("content")
            if content is None and path and os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        content = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
            if content is not None:
                tab = self.add_tab(path, content)
                if f.get("content") is not None and path:
                    tab.modified = True
                    self.update_tab_title(tab)
        if not self.tabs:
            self.add_tab(None, syntax.SAMPLE)

    def info(self) -> Dict[str, Any]:
        return {"fichiers": len(self.tabs)}

    def demo(self) -> None:
        self.run()
