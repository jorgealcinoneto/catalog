import os
import io
import json
import uuid
import sqlite3
import base64
import urllib.parse
import urllib.request
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, render_template
from google import genai
from google.genai import types as genai_types
from PIL import Image
from pyzbar.pyzbar import decode as zbar_decode

app = Flask(__name__)
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "catalog.db"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                title     TEXT,
                author    TEXT,
                publisher TEXT,
                year      TEXT,
                isbn      TEXT,
                language  TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                title, author, publisher, isbn,
                content=books, content_rowid=id
            );

            CREATE TRIGGER IF NOT EXISTS books_ai AFTER INSERT ON books BEGIN
                INSERT INTO books_fts(rowid, title, author, publisher, isbn)
                VALUES (new.id, new.title, new.author, new.publisher, new.isbn);
            END;

            CREATE TRIGGER IF NOT EXISTS books_ad AFTER DELETE ON books BEGIN
                INSERT INTO books_fts(books_fts, rowid, title, author, publisher, isbn)
                VALUES ('delete', old.id, old.title, old.author, old.publisher, old.isbn);
            END;

            CREATE TRIGGER IF NOT EXISTS books_au AFTER UPDATE ON books BEGIN
                INSERT INTO books_fts(books_fts, rowid, title, author, publisher, isbn)
                VALUES ('delete', old.id, old.title, old.author, old.publisher, old.isbn);
                INSERT INTO books_fts(rowid, title, author, publisher, isbn)
                VALUES (new.id, new.title, new.author, new.publisher, new.isbn);
            END;

            CREATE TABLE IF NOT EXISTS images (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id    INTEGER REFERENCES books(id) ON DELETE CASCADE,
                filename   TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS chapters (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id        INTEGER REFERENCES books(id) ON DELETE CASCADE,
                position       INTEGER,
                title          TEXT,
                page           TEXT,
                summary        TEXT,
                summary_source TEXT,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)


# ---------------------------------------------------------------------------
# ISBN detection + Google Books lookup (Gemini as last resort)
# ---------------------------------------------------------------------------

_client = genai.Client(
    api_key=os.environ["GEMINI_API_KEY"],
    http_options={"api_version": "v1beta"},
)

SCAN_PROMPT = (
    "Extract from this book image: title, author, publisher, year, isbn, language. "
    "If this is a table of contents, extract all chapter/section titles and page numbers as a 'chapters' array with 'title' and 'page' keys. "
    "Return JSON only, no explanation."
)


def detect_isbn(image_data: bytes) -> str | None:
    """Detect ISBN-13 from barcode in image. Returns None if not found."""
    try:
        img = Image.open(io.BytesIO(image_data))
        if img.mode != "RGB":
            img = img.convert("RGB")
        for code in zbar_decode(img):
            data = code.data.decode("utf-8", errors="ignore").strip()
            # ISBN-13 starts with 978 or 979
            if len(data) == 13 and data.isdigit() and data.startswith(("978", "979")):
                return data
            # Also accept ISBN-10
            if len(data) == 10 and data[:9].isdigit():
                return data
    except Exception as e:
        print(f"Barcode detection failed: {e}")
    return None


def lookup_google_books(isbn: str) -> dict | None:
    """Lookup book metadata via Google Books API. Returns None if not found."""
    try:
        url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{urllib.parse.quote(isbn)}"
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        items = payload.get("items") or []
        if not items:
            return None
        info = items[0].get("volumeInfo", {})
        authors = info.get("authors") or []
        published = info.get("publishedDate", "") or ""
        year = published[:4] if len(published) >= 4 else ""
        return {
            "title": info.get("title", ""),
            "author": ", ".join(authors),
            "publisher": info.get("publisher", ""),
            "year": year,
            "isbn": isbn,
            "language": info.get("language", ""),
        }
    except Exception as e:
        print(f"Google Books lookup failed: {e}")
        return None


def analyze_image_gemini(image_data: bytes, media_type: str) -> dict:
    """Last resort: full image analysis via Gemini."""
    part = genai_types.Part.from_bytes(data=image_data, mime_type=media_type)
    response = _client.models.generate_content(
        model="gemini-flash-latest",
        contents=[part, SCAN_PROMPT],
    )
    text = response.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def analyze_image(image_data: bytes, media_type: str) -> dict:
    """Try ISBN barcode → Google Books lookup. Fallback to Gemini."""
    isbn = detect_isbn(image_data)
    if isbn:
        meta = lookup_google_books(isbn)
        if meta and meta.get("title"):
            return meta
    return analyze_image_gemini(image_data, media_type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def book_images(conn, book_id):
    rows = conn.execute(
        "SELECT filename FROM images WHERE book_id=? ORDER BY id", (book_id,)
    ).fetchall()
    return [r["filename"] for r in rows]


def rows_to_books(conn, rows):
    books = []
    for r in rows:
        b = dict(r)
        b["images"] = book_images(conn, b["id"])
        books.append(b)
    return books


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/scan", methods=["POST"])
def scan():
    # Support both single image and multiple images
    files = request.files.getlist("images")
    if not files:
        file = request.files.get("image")
        if not file:
            return jsonify({"error": "no image"}), 400
        files = [file]

    images = []
    for file in files:
        if not file or not file.filename:
            continue
        images.append({
            "data": file.read(),
            "media_type": file.content_type or "image/jpeg",
        })

    if not images:
        return jsonify({"error": "no image"}), 400

    merged = {}

    # Step 1: try ISBN barcode on ALL images first (fast, free, no AI)
    for img in images:
        isbn = detect_isbn(img["data"])
        if isbn:
            meta = lookup_google_books(isbn)
            if meta and meta.get("title"):
                merged.update({k: v for k, v in meta.items() if v})
                merged["source"] = "google_books"
                break

    # Step 2: only call Gemini if ISBN lookup did NOT succeed
    if not merged.get("title"):
        for img in images:
            try:
                data = analyze_image_gemini(img["data"], img["media_type"])
            except Exception as e:
                print(f"Gemini failed: {e}")
                continue
            for key, value in data.items():
                if key == "chapters" and value:
                    merged.setdefault("chapters", []).extend(value)
                elif not merged.get(key) and value:
                    merged[key] = value
        merged["source"] = "gemini"

    return jsonify(merged)


@app.route("/books", methods=["POST"])
def create_book():
    body = request.json or {}
    # accepts list of {b64, type} or legacy single image_b64/image_type
    images = body.pop("images", None)
    if not images:
        single_b64  = body.pop("image_b64", None)
        single_type = body.pop("image_type", "image/jpeg")
        images = [{"b64": single_b64, "type": single_type}] if single_b64 else []

    chapters = body.pop("chapters", None) or []

    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO books (title, author, publisher, year, isbn, language) "
            "VALUES (?,?,?,?,?,?)",
            (
                body.get("title"), body.get("author"), body.get("publisher"),
                body.get("year"), body.get("isbn"), body.get("language"),
            ),
        )
        book_id = cur.lastrowid

        for img in images:
            img_bytes = base64.b64decode(img["b64"])
            ext = (img.get("type") or "image/jpeg").split("/")[-1]
            filename = f"{uuid.uuid4()}.{ext}"
            (UPLOAD_DIR / filename).write_bytes(img_bytes)
            conn.execute(
                "INSERT INTO images (book_id, filename) VALUES (?,?)", (book_id, filename)
            )

        for pos, ch in enumerate(chapters):
            if not isinstance(ch, dict):
                ch = {"title": str(ch)}
            title = (ch.get("title") or "").strip()
            if not title:
                continue
            page = ch.get("page")
            page = str(page) if page is not None else ""
            conn.execute(
                "INSERT INTO chapters (book_id, position, title, page) VALUES (?,?,?,?)",
                (book_id, pos, title, page),
            )

    return jsonify({"id": book_id}), 201


@app.route("/books", methods=["GET"])
def list_books():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY created_at DESC").fetchall()
        return jsonify(rows_to_books(conn, rows))


@app.route("/books/<int:book_id>", methods=["GET"])
def get_book(book_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        book = dict(row)
        book["images"] = book_images(conn, book_id)
        ch_rows = conn.execute(
            "SELECT id, position, title, page, summary, summary_source "
            "FROM chapters WHERE book_id=? ORDER BY position, id",
            (book_id,),
        ).fetchall()
        book["chapters"] = [dict(r) for r in ch_rows]
        return jsonify(book)


# ---------------------------------------------------------------------------
# Chapter summaries (fichamento)
# ---------------------------------------------------------------------------

SUMMARY_PROMPT_TITLE = (
    "Você é um assistente acadêmico. Gere um fichamento conciso (5–8 linhas) "
    "do capítulo \"{chapter}\" do livro \"{title}\" de {author}. "
    "Estruture em: tema central, pontos principais e conclusão. "
    "Use seu conhecimento geral sobre a obra. Responda em português, "
    "texto corrido sem cabeçalhos markdown."
)

SUMMARY_PROMPT_PHOTOS = (
    "Você recebeu fotos das páginas do capítulo \"{chapter}\" do livro \"{title}\" "
    "de {author}. Faça um fichamento (8–12 linhas) com: tema central, pontos "
    "principais e citações relevantes (entre aspas) extraídas das imagens. "
    "Responda em português, texto corrido sem cabeçalhos markdown."
)


def summarize_chapter(book: dict, chapter: dict, photos=None) -> str:
    """Generate chapter summary via Gemini. photos: list of {b64, type} or None."""
    title = book.get("title") or "(sem título)"
    author = book.get("author") or "autor desconhecido"
    chapter_title = chapter.get("title") or ""

    if photos:
        prompt = SUMMARY_PROMPT_PHOTOS.format(chapter=chapter_title, title=title, author=author)
        contents = [prompt]
        for p in photos:
            img_bytes = base64.b64decode(p["b64"])
            mime = p.get("type") or "image/jpeg"
            contents.append(genai_types.Part.from_bytes(data=img_bytes, mime_type=mime))
        response = _client.models.generate_content(
            model="gemini-flash-latest", contents=contents,
        )
    else:
        prompt = SUMMARY_PROMPT_TITLE.format(chapter=chapter_title, title=title, author=author)
        response = _client.models.generate_content(
            model="gemini-flash-latest", contents=prompt,
        )
    return (response.text or "").strip()


@app.route("/chapters/<int:chapter_id>/summarize", methods=["POST"])
def summarize_chapter_route(chapter_id):
    body = request.json or {}
    photos = body.get("images") or None

    with get_db() as conn:
        ch_row = conn.execute(
            "SELECT * FROM chapters WHERE id=?", (chapter_id,)
        ).fetchone()
        if not ch_row:
            return jsonify({"error": "chapter not found"}), 404
        book_row = conn.execute(
            "SELECT * FROM books WHERE id=?", (ch_row["book_id"],)
        ).fetchone()
        if not book_row:
            return jsonify({"error": "book not found"}), 404

        try:
            summary = summarize_chapter(dict(book_row), dict(ch_row), photos)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

        source = "photos" if photos else "title_only"
        conn.execute(
            "UPDATE chapters SET summary=?, summary_source=? WHERE id=?",
            (summary, source, chapter_id),
        )

    return jsonify({"summary": summary, "source": source})


@app.route("/chapters/<int:chapter_id>/summary", methods=["DELETE"])
def delete_chapter_summary(chapter_id):
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE chapters SET summary=NULL, summary_source=NULL WHERE id=?",
            (chapter_id,),
        )
        if cur.rowcount == 0:
            return jsonify({"error": "chapter not found"}), 404
    return jsonify({"ok": True})


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    with get_db() as conn:
        rows = conn.execute(
            "SELECT books.* FROM books "
            "JOIN books_fts ON books.id = books_fts.rowid "
            "WHERE books_fts MATCH ? ORDER BY rank",
            (q,),
        ).fetchall()
        return jsonify(rows_to_books(conn, rows))


@app.route("/books/<int:book_id>/images", methods=["POST"])
def add_image(book_id):
    file = request.files.get("image")
    if not file:
        return jsonify({"error": "no image"}), 400
    data = file.read()
    ext = (file.content_type or "image/jpeg").split("/")[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    (UPLOAD_DIR / filename).write_bytes(data)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO images (book_id, filename) VALUES (?,?)", (book_id, filename)
        )
    return jsonify({"filename": filename}), 201


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8080, debug=True)
