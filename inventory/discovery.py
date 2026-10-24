from dataclasses import dataclass, field
from pathlib import Path
import os
from .metadata import stem_key, find_isbns
import re

IMAGES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED = IMAGES | {".pdf", ".txt"}
IGNORED = {".venv", "venv", ".git", ".tmp", "__pycache__", "node_modules", "models"}


@dataclass
class Book:
    key: str
    files: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def structured_pdf(path: Path):
    """Recognize the collection's ISBN_txt.pdf and ISBN_img.pdf naming contract."""
    match = re.fullmatch(r'(.+)_(txt|img)(?:[ _.-]+teil[ _.-]*(\d+))?',path.stem,re.I) if path.suffix.lower() == '.pdf' else None
    if match:
        isbn = find_isbns(match[1])
        if len(isbn) == 1 and re.sub(r'[^0-9Xx]','',match[1]).upper() == isbn[0]:
            return isbn[0],match[2].lower()
    return None


def book_scope(path: Path, root: Path, isbn: str):
    for parent in (path.parent,*path.parent.parents):
        if not parent.is_relative_to(root):
            break
        if isbn in find_isbns(parent.name):
            return parent
    parent = path.parent
    if parent.name.upper() in {'TEXT','IMAGE','COVER'} and parent.parent.name.upper() == 'INPUT':
        return parent.parent.parent
    return parent


def book_part(path: Path):
    match = re.search(r'(?:^|[ _.-])teil[ _.-]*(\d+)(?:$|[ _.-])', path.stem, re.I)
    return int(match[1]) if match else None


def spreadsheet_pdf(path: Path) -> bool:
    return (path.suffix.lower() == '.pdf' and
            path.parent.name.upper() == 'IMAGE' and
            bool(re.search(r'(?:^|[ _.-])spreadsheet(?:$|[ _.-])', path.stem, re.I)))


def merge_structured(groups, root):
    merged, claimed = {}, set()
    files = [p for group in groups for p in group.files]
    for path in files:
        role = structured_pdf(path)
        if role:
            isbn, _ = role
            scope = book_scope(path, root, isbn)
            part = book_part(path)
            key = (scope, isbn, part)
            if key not in merged:
                label = isbn + (f'_Teil_{part}' if part is not None else '')
                merged[key] = Book(str(scope / label), notes=[
                    'ISBN_txt / ISBN_img workflow; COVER files excluded from OCR and image count'])
            merged[key].files.append(path)
            claimed.add(path)
    input_scopes = set()
    for (scope, isbn, part), book in merged.items():
        input_files = [p for p in book.files if p.is_relative_to(scope / 'INPUT')]
        if input_files:
            book.files = input_files
            input_scopes.add(scope)
    merged = {key: book for key, book in merged.items()
              if key[0] not in input_scopes or
              any(p.is_relative_to(key[0] / 'INPUT') for p in book.files)}
    for path in files:
        if path in claimed:
            continue
        if spreadsheet_pdf(path):
            candidates = [book for (scope, isbn, part), book in merged.items()
                          if path.is_relative_to(scope) and book_part(path) == part
                          and (not find_isbns(path.stem) or isbn in find_isbns(path.stem))]
            if len(candidates) == 1:
                candidates[0].files.append(path)
                claimed.add(path)
                continue
            if not candidates and book_part(path) is None:
                candidates = [book for (scope, isbn, part), book in merged.items()
                              if path.is_relative_to(scope) and
                              (not find_isbns(path.stem) or isbn in find_isbns(path.stem))]
                if len(candidates) == 1:
                    candidates[0].files.append(path)
                    claimed.add(path)
                    continue
            if not candidates:
                candidates = [book for (scope, isbn, part), book in merged.items()
                              if path.is_relative_to(scope) and
                              (not find_isbns(path.stem) or isbn in find_isbns(path.stem))]
            for book in candidates:
                book.notes.append(f'Cannot assign SPREADSHEET to a unique Teil; excluded from image count: {path}')
        for (scope, isbn, part), book in merged.items():
            if not path.is_relative_to(scope):
                continue
            relative = path.relative_to(scope)
            names = [name.upper() for name in relative.parts[:-1]]
            # Ancillary collection assets are never independent book rows.
            if ('COVER' in names or
                    (scope in input_scopes and relative.parts[0].upper() == 'OUTPUT') or
                    isbn in find_isbns(path.stem) or
                    ('INPUT' in names and 'IMAGE' in names)):
                claimed.add(path)
    claimed.update(path for path in files if spreadsheet_pdf(path))
    remaining = [Book(g.key, [p for p in g.files if p not in claimed], g.notes) for g in groups]
    return [g for g in remaining if g.files] + list(merged.values())


def cover_pdf(path: Path) -> bool:
    if path.suffix.lower() != '.pdf':
        return False
    # Collection cover exports are ancillary assets, including cover-only books.
    return (path.parent.name.upper() == 'COVER' or
            bool(find_isbns(path.stem) and
                 re.search(r'_(?:cvr|cover)(?:_|$)', path.stem, re.I)))


def discover(root: Path, grouping="auto") -> list[Book]:
    if any(part.upper() == 'COVER' for part in root.parts):
        return []
    folders: dict[Path, list[Path]] = {}
    def inaccessible(error):
        raise OSError(f"Cannot scan folder: {error.filename}: {error.strerror}") from error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=inaccessible):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED and d.upper() != 'COVER' and not d.startswith("."))
        paths = [Path(directory) / name for name in sorted(files)
                 if Path(name).suffix.lower() in SUPPORTED
                 and not cover_pdf(Path(directory) / name)
                 and not (Path(directory) / name).is_symlink()]
        if paths:
            folders[Path(directory)] = paths
    groups: dict[tuple, Book] = {}
    anchors: dict[Path, list[Book]] = {}
    for folder, files in folders.items():
        for path in files:
            if path.suffix.lower() not in {".pdf", ".txt"}:
                continue
            key = "folder" if grouping == "folder" else stem_key(path.stem)
            identity = (folder, key)
            if identity not in groups:
                groups[identity] = Book(str(folder / key))
                anchors.setdefault(folder, []).append(groups[identity])
            groups[identity].files.append(path)
    # Images with no exact match attach only to a unique book in the nearest ancestor.
    for folder, files in folders.items():
        for path in files:
            if path.suffix.lower() not in IMAGES:
                continue
            exact = groups.get((folder, stem_key(path.stem)))
            candidates = []
            cursor = folder
            while cursor.is_relative_to(root):
                if cursor in anchors:
                    candidates = anchors[cursor]
                    break
                if cursor == root:
                    break
                cursor = cursor.parent
            if exact:
                exact.files.append(path)
            elif len(candidates) == 1:
                candidates[0].files.append(path)
                candidates[0].notes.append("Image attachment inferred from folder; verify grouping")
            else:
                key = "images" if grouping == "folder" else stem_key(path.stem)
                identity = (folder, key)
                if identity not in groups:
                    groups[identity] = Book(str(folder / key), notes=["Image-only book; verify grouping"])
                groups[identity].files.append(path)
    return sorted(merge_structured(list(groups.values()),root), key=lambda b: b.key.casefold())
