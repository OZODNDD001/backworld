from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from datetime import datetime
from dotenv import load_dotenv
import psycopg2  # 👈 sqlite3 o'rniga psycopg2 ulaymiz
import uuid
import json
import os
import boto3
import mimetypes

load_dotenv()

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")  # 👈 .env faylda postgres:// bilan boshlanadigan havola bo'ladi
STORAGE_URL = os.getenv("STORAGE_URL")
SITEMAP_FILE = "sitemap.xml"

ACCOUNT_ID = os.getenv("ACCOUNT_ID")
ACCESS_KEY = os.getenv("ACCESS_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")
BUCKET = os.getenv("BUCKET")

R2_PUBLIC_DOMAIN = os.getenv("R2_PUBLIC_DOMAIN", "https://pub-82f9c6459c2e4e2aa9352be7456b7cb0.r2.dev")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com",
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
)

# 🔹 DB ulanish (PostgreSQL uchun yangilandi)
def get_conn():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_conn()
    cursor = conn.cursor()
    # PostgreSQL uchun AUTOINCREMENT o'rniga SERIAL ishlatiladi
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categories (
        id SERIAL PRIMARY KEY,
        name TEXT UNIQUE,
        title TEXT,
        icon TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS wallpapers (
        id SERIAL PRIMARY KEY,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        title TEXT,
        description TEXT,
        image TEXT,
        video_1080 TEXT,
        video_4k TEXT,
        category_id INTEGER,
        tags TEXT,
        FOREIGN KEY (category_id) REFERENCES categories(id)
    )
    """)
    conn.commit()
    conn.close()


@app.get("/")
@app.head("/")
def root():
    return {"message": "Welcome to the Wallpaper API!"}

def format_wallpaper(row):
    return {
        "id": row[0],
        "title": row[1],
        "description": row[2],
        "image": row[3],
        "videos": {
            "1080": row[4],
            "4k": row[5]
        },
        "category": {
            "name": row[6]
        },
        "tags": json.loads(row[7]) if row[7] else []
    }

def verify_admin(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if authorization != f"Bearer {ADMIN_TOKEN}":
        raise HTTPException(status_code=403, detail="Forbidden")

def format_date(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt.split(" ")[0]
    return dt.isoformat()

def generate_sitemap():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, created_at
        FROM wallpapers
        ORDER BY id DESC
    """)

    rows = cursor.fetchall()

    xml = ['''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
''']

    # homepage
    xml.append("""
    <url>
        <loc>https://back-world.online/</loc>
        <priority>1.0</priority>
    </url>
    """)

    for wid, created in rows:
        created = format_date(created)

        xml.append(f"""
    <url>
        <loc>https://back-world.online/assets/html/wallpaper.html?id={wid}</loc>
        <lastmod>{created}</lastmod>
        <changefreq>weekly</changefreq>
        <priority>0.8</priority>
    </url>
""")

    xml.append("</urlset>")
    final_xml = "".join(xml)

    # Faylga yozish (BackWorld loyihasi sitemapi uchun)
    with open(SITEMAP_FILE, "w", encoding="utf-8") as f:
        f.write(final_xml)

    conn.close()

    # Endi linklar soni VA XML matnini birga qaytaramiz
    return len(rows), final_xml

@app.get("/api/wallpapers")
def get_wallpapers(page: int = 1, limit: int = 50):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM wallpapers")
        total = cursor.fetchone()[0]
        offset = (page - 1) * limit

        # 👈 LIMIT ? OFFSET ? o'rniga %s qo'yildi
        cursor.execute("""
            SELECT w.id, w.title, w.description, w.image,
                   w.video_1080, w.video_4k,
                   c.name,
                   w.tags
            FROM wallpapers w
            JOIN categories c ON w.category_id = c.id
            ORDER BY w.created_at DESC
            LIMIT %s OFFSET %s 
        """, (limit, offset))

        rows = cursor.fetchall()
        data = [format_wallpaper(r) for r in rows]
        return {
            "data": data,
            "count": len(data),
            "page": page,
            "limit": limit,
            "has_more": (page * limit) < total
        }
    finally:
        conn.close()

@app.get("/api/wallpaper/{id}")
def get_wallpaper(id: int):
    conn = get_conn()
    cursor = conn.cursor()
    # 👈 ? o'rniga %s
    cursor.execute("""
        SELECT w.id, w.title, w.description, w.image,
               w.video_1080, w.video_4k,
               c.name,
               w.tags
        FROM wallpapers w
        JOIN categories c ON w.category_id = c.id
        WHERE w.id = %s
    """, (id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return format_wallpaper(row)

@app.get("/api/categories")
def get_categories():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM categories")
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "name": r[1], "title": r[2], "icon": r[3]}
        for r in rows
    ]

@app.get("/api/category/{name}")
def get_by_category(name: str, page: int = 1, limit: int = 50):
    conn = get_conn()
    cursor = conn.cursor()
    offset = (page - 1) * limit
    # 👈 ? o'rniga %s
    cursor.execute("""
        SELECT w.id, w.title, w.description, w.image,
               w.video_1080, w.video_4k,
               c.name,
               w.tags
        FROM wallpapers w
        JOIN categories c ON w.category_id = c.id
        WHERE c.name = %s
        ORDER BY w.id DESC
        LIMIT %s OFFSET %s
    """, (name, limit, offset))
    rows = cursor.fetchall()
    conn.close()
    data = [format_wallpaper(r) for r in rows]
    return {
        "data": data,
        "page": page,
        "limit": limit,
        "count": len(data),
        "has_more": len(data) == limit
    }

@app.get("/api/related")
def related(tags: str = "", limit: int = 20):
    conn = get_conn()
    cursor = conn.cursor()
    if not tags:
        conn.close()
        return {"data": [], "count": 0}

    tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
    cursor.execute("""
        SELECT w.id, w.title, w.image, w.tags
        FROM wallpapers w
        ORDER BY w.id DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for r in rows:
        w_tags = json.loads(r[3]) if r[3] else []
        w_tags = [t.lower() for t in w_tags]
        if any(tag in w_tags for tag in tag_list):
            result.append({"id": r[0], "title": r[1], "image": r[2]})
        if len(result) >= limit:
            break
    return {"data": result, "count": len(result), "limit": limit}

@app.get("/api/search")
def search(q: str = "", category: str = "all", page: int = 1, limit: int = 50):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        q_like = f"%{q.lower()}%"
        offset = (page - 1) * limit
        where = []
        params = []

        if q:
            # 👈 PostgreSQL uchun %s moslandi
            where.append("""
            (LOWER(w.title) LIKE %s
            OR LOWER(w.description) LIKE %s
            OR LOWER(w.tags) LIKE %s)
            """)
            params += [q_like, q_like, q_like]

        if category != "all":
            where.append("c.name = %s")
            params.append(category)

        where_sql = " AND ".join(where) if where else "1=1"

        cursor.execute(f"""
            SELECT COUNT(*)
            FROM wallpapers w
            JOIN categories c ON w.category_id = c.id
            WHERE {where_sql}
        """, params)
        total = cursor.fetchone()[0]

        cursor.execute(f"""
            SELECT w.id, w.title, w.description, w.image,
                   w.video_1080, w.video_4k,
                   c.name,
                   w.tags
            FROM wallpapers w
            JOIN categories c ON w.category_id = c.id
            WHERE {where_sql}
            ORDER BY w.id DESC
            LIMIT %s OFFSET %s
        """, params + [limit, offset])

        rows = cursor.fetchall()
        data = [format_wallpaper(r) for r in rows]
        return {
            "data": data,
            "count": len(data),
            "page": page,
            "limit": limit,
            "has_more": (page * limit) < total
        }
    finally:
        conn.close()

@app.post("/api/admin/generate-sitemap")
def admin_generate_sitemap(authorization: str = Header(None)):
    verify_admin(authorization)

    # Funksiyadan ikkala ma'lumotni qabul qilib olamiz
    count, sitemap_content = generate_sitemap()

    return {
        "status": "success",
        "message": "Sitemap muvaffaqiyatli generatsiya qilindi va faylga yozildi.",
        "urls_count": count,
        "sitemap_xml": sitemap_content  # 👈 Mana shu yerda to'liq XML matni adminga qaytadi
    }

@app.get("/api/download/{filename}")
def download_file(filename: str):
    path = f"database/data/wallpaper/videos/{filename}"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)

@app.post("/api/admin/category")
def add_category(data: dict, authorization: str = Header(None)):
    conn = get_conn()
    cursor = conn.cursor()
    verify_admin(authorization)
    # 👈 %s ga o'zgardi
    cursor.execute(
        "INSERT INTO categories (name, title, icon) VALUES (%s, %s, %s)",
        (data["name"], data["title"], data["icon"])
    )
    conn.commit()
    conn.close()
    return {"message": "category added"}

@app.put("/api/admin/category/{id}")
def edit_category(id: int, data: dict, authorization: str = Header(None)):
    conn = get_conn()
    cursor = conn.cursor()
    verify_admin(authorization)
    # 👈 %s ga o'zgardi
    cursor.execute(
        "UPDATE categories SET name=%s, title=%s, icon=%s WHERE id=%s",
        (data["name"], data["title"], data["icon"], id)
    )
    conn.commit()
    conn.close()
    return {"message": "category updated"}

@app.delete("/api/admin/category/{id}")
def delete_category(id: int, authorization: str = Header(None)):
    conn = get_conn()
    cursor = conn.cursor()
    verify_admin(authorization)
    # 👈 %s ga o'zgardi
    cursor.execute("DELETE FROM categories WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return {"message": "category deleted"}

@app.post("/api/admin/wallpaper")
def add_wallpaper(w: dict, authorization: str = Header(None)):
    conn = get_conn()
    cursor = conn.cursor()
    verify_admin(authorization)
    # 👈 %s ga o'zgardi
    cursor.execute("""
        INSERT INTO wallpapers
        (title, description, image, video_1080, video_4k, category_id, tags)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (
        w["title"],
        w["description"],
        w["image"],
        w["video_1080"],
        w["video_4k"],
        w["category_id"],
        json.dumps(w["tags"])
    ))
    conn.commit()
    conn.close()
    return {"message": "wallpaper added"}

@app.put("/api/admin/wallpaper/{id}")
def edit_wallpaper(id: int, w: dict, authorization: str = Header(None)):
    conn = get_conn()
    cursor = conn.cursor()
    verify_admin(authorization)
    # 👈 %s ga o'zgardi
    cursor.execute("""
        UPDATE wallpapers SET
        title=%s,
        description=%s,
        image=%s,
        video_1080=%s,
        video_4k=%s,
        category_id=%s,
        tags=%s
        WHERE id=%s
    """, (
        w["title"],
        w["description"],
        w["image"],
        w["video_1080"],
        w["video_4k"],
        w["category_id"],
        json.dumps(w["tags"]),
        id
    ))
    conn.commit()
    conn.close()
    return {"message": "wallpaper updated"}

@app.delete("/api/admin/wallpaper/{id}")
def delete_wallpaper(id: int, authorization: str = Header(None)):
    conn = get_conn()
    cursor = conn.cursor()
    verify_admin(authorization)
    # 👈 %s ga o'zgardi
    cursor.execute("DELETE FROM wallpapers WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    return {"message": "wallpaper deleted"}

@app.get("/api/admin/wallpaper/{wallpaper_id}")
def get_admin_wallpaper(wallpaper_id: int, authorization: str = Header(None)):
    verify_admin(authorization) 
    conn = get_conn()
    cursor = conn.cursor()
    # 👈 %s ga o'zgardi
    cursor.execute("""
        SELECT w.id, w.title, w.description, w.image,
               w.video_1080, w.video_4k,
               w.category_id,
               w.tags
        FROM wallpapers w
        WHERE w.id = %s
    """, (wallpaper_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Wallpaper topilmadi!")
    return {
        "id": row[0],
        "title": row[1],
        "description": row[2],
        "image": row[3],
        "video_1080": row[4],
        "video_4k": row[5],
        "category_id": row[6],
        "tags": row[7]
    }



@app.post("/api/admin/generate-sitemap")
def admin_generate_sitemap(authorization: str = Header(None)):
    verify_admin(authorization)
    count = generate_sitemap()
    return {"message": "sitemap generated", "urls": count}

@app.post("/api/admin/upload")
async def upload_to_r2(
    file: UploadFile = File(...),
    folder: str = Form("images"), 
    authorization: str = Header(None)
):
    verify_admin(authorization)
    try:
        base_name, file_extension = os.path.splitext(file.filename)
        unique_id = uuid.uuid4().hex[:10]
        unique_file_name = f"{base_name}_{unique_id}{file_extension}"
        file_name = f"{folder}/{unique_file_name}"
        content_type, _ = mimetypes.guess_type(file.filename)
        if not content_type:
            content_type = file.content_type or "application/octet-stream"

        file_bytes = await file.read()
        s3.put_object(
            Bucket=BUCKET,
            Key=file_name,
            Body=file_bytes,
            ContentType=content_type,
            ContentDisposition=f'attachment; filename="{unique_file_name}"'
        )
        generated_url = f"{R2_PUBLIC_DOMAIN}/{file_name}"
        return {"status": "success", "url": generated_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"R2 yuklashda xatolik yuz berdi: {str(e)}")