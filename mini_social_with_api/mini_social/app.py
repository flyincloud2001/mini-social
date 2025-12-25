from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, jsonify
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import os

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


APP_SECRET = "change_this_to_a_random_string"

app = Flask(__name__)
app.secret_key = APP_SECRET

DB_PATH = "database.db"
print("DB absolute path =", os.path.abspath(DB_PATH))


def get_toronto_tz():
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo("America/Toronto")
    except Exception:
        return None


TORONTO_TZ = get_toronto_tz()


def format_time(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str

    if TORONTO_TZ is not None:
        if dt.tzinfo is None:
            if ZoneInfo is not None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        local_dt = dt.astimezone(TORONTO_TZ)
        return local_dt.strftime("%b %d %I:%M %p")

    try:
        local_dt = dt.astimezone()
        return local_dt.strftime("%b %d %I:%M %p")
    except Exception:
        return iso_str


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER NOT NULL,
            followee_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (follower_id, followee_id),
            FOREIGN KEY (follower_id) REFERENCES users (id),
            FOREIGN KEY (followee_id) REFERENCES users (id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS likes (
            user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, post_id),
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (post_id) REFERENCES posts (id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (post_id) REFERENCES posts (id),
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
        """
    )

    conn.commit()
    conn.close()


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = get_db()
    user = conn.execute("SELECT id, username FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return user


def get_followee_ids(follower_id: int):
    conn = get_db()
    rows = conn.execute(
        "SELECT followee_id FROM follows WHERE follower_id = ?",
        (follower_id,),
    ).fetchall()
    conn.close()
    return [r["followee_id"] for r in rows]


def fetch_posts(feed: str, viewer_id: int | None):
    conn = get_db()

    where_sql = ""
    where_params: list = []

    if feed == "following":
        if viewer_id is None:
            conn.close()
            return [], {}
        ids = get_followee_ids(viewer_id)
        ids.append(viewer_id)
        placeholders = ",".join(["?"] * len(ids))
        where_sql = f"WHERE posts.user_id IN ({placeholders})"
        where_params.extend(ids)

    posts = conn.execute(
        f"""
        SELECT
            posts.id,
            posts.content,
            posts.created_at,
            users.username,
            COALESCE(lc.cnt, 0) AS like_count,
            COALESCE(cc.cnt, 0) AS comment_count,
            CASE
                WHEN ? IS NULL THEN 0
                WHEN EXISTS (
                    SELECT 1
                    FROM likes
                    WHERE likes.post_id = posts.id AND likes.user_id = ?
                ) THEN 1
                ELSE 0
            END AS liked_by_me
        FROM posts
        JOIN users ON users.id = posts.user_id
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS cnt
            FROM likes
            GROUP BY post_id
        ) AS lc ON lc.post_id = posts.id
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS cnt
            FROM comments
            GROUP BY post_id
        ) AS cc ON cc.post_id = posts.id
        {where_sql}
        ORDER BY posts.id DESC
        """,
        (viewer_id, viewer_id, *where_params),
    ).fetchall()

    posts = [dict(p) for p in posts]
    for p in posts:
        p["created_at"] = format_time(p.get("created_at", ""))

    post_ids = [p["id"] for p in posts]
    comments_by_post: dict[int, list[dict]] = {}

    if post_ids:
        placeholders = ",".join(["?"] * len(post_ids))
        rows = conn.execute(
            f"""
            SELECT
                comments.id,
                comments.post_id,
                comments.content,
                comments.created_at,
                users.username
            FROM comments
            JOIN users ON users.id = comments.user_id
            WHERE comments.post_id IN ({placeholders})
            ORDER BY comments.id ASC
            """,
            tuple(post_ids),
        ).fetchall()

        rows = [dict(r) for r in rows]
        for r in rows:
            r["created_at"] = format_time(r.get("created_at", ""))
            comments_by_post.setdefault(r["post_id"], []).append(r)

    conn.close()
    return posts, comments_by_post

def _parse_int(value: str | None, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except Exception:
        return default


def fetch_posts_api(feed: str, viewer_id: int | None, limit: int, before_id: int | None):
    conn = get_db()

    conditions: list[str] = []
    params: list = [viewer_id, viewer_id]

    if feed == "following":
        if viewer_id is None:
            conn.close()
            return []
        ids = get_followee_ids(viewer_id)
        ids.append(viewer_id)
        placeholders = ",".join(["?"] * len(ids))
        conditions.append(f"posts.user_id IN ({placeholders})")
        params.extend(ids)

    if before_id is not None:
        conditions.append("posts.id < ?")
        params.append(before_id)

    where_sql = ""
    if conditions:
        where_sql = "WHERE " + " AND ".join(conditions)

    rows = conn.execute(
        f"""
        SELECT
            posts.id,
            posts.content,
            posts.created_at,
            users.username,
            COALESCE(lc.cnt, 0) AS like_count,
            COALESCE(cc.cnt, 0) AS comment_count,
            CASE
                WHEN ? IS NULL THEN 0
                WHEN EXISTS (
                    SELECT 1
                    FROM likes
                    WHERE likes.post_id = posts.id AND likes.user_id = ?
                ) THEN 1
                ELSE 0
            END AS liked_by_me
        FROM posts
        JOIN users ON users.id = posts.user_id
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS cnt
            FROM likes
            GROUP BY post_id
        ) AS lc ON lc.post_id = posts.id
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS cnt
            FROM comments
            GROUP BY post_id
        ) AS cc ON cc.post_id = posts.id
        {where_sql}
        ORDER BY posts.id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    posts = [dict(r) for r in rows]
    for p in posts:
        p["created_at"] = format_time(p.get("created_at", ""))

    conn.close()
    return posts



@app.before_request
def ensure_db():
    init_db()


@app.route("/")
def index():
    feed = request.args.get("feed") or "public"
    user = current_user()

    if feed == "following" and not user:
        flash("Please log in to view the following feed.")
        return redirect(url_for("login"))

    posts, comments_by_post = fetch_posts(feed, user["id"] if user else None)
    return render_template("index.html", user=user, posts=posts, comments_by_post=comments_by_post, feed=feed)


@app.route("/api/posts", methods=["GET"])
def api_get_posts():
    feed = request.args.get("feed") or "public"
    if feed not in ("public", "following"):
        return jsonify({"error": "Invalid feed. Use 'public' or 'following'."}), 400

    user = current_user()
    viewer_id = user["id"] if user else None

    if feed == "following" and not user:
        return jsonify({"error": "Authentication required for following feed."}), 401

    limit = _parse_int(request.args.get("limit"), default=20) or 20
    if limit < 1:
        limit = 1
    if limit > 50:
        limit = 50

    before_id = _parse_int(request.args.get("before_id"), default=None)

    posts = fetch_posts_api(feed=feed, viewer_id=viewer_id, limit=limit, before_id=before_id)
    next_cursor = posts[-1]["id"] if len(posts) == limit and posts else None

    return jsonify(
        {
            "feed": feed,
            "limit": limit,
            "before_id": before_id,
            "next_cursor": next_cursor,
            "posts": posts,
        }
    )

@app.route("/api/posts", methods=["POST"])
def api_create_post():
    user = current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401

    # 支援 JSON 與表單兩種
    payload = request.get_json(silent=True) or {}
    content = (payload.get("content") or request.form.get("content") or "").strip()

    if not content:
        return jsonify({"error": "Post cannot be empty."}), 400
    if len(content) > 500:
        return jsonify({"error": "Post is too long. Limit is 500 characters."}), 400

    now = datetime.utcnow().isoformat()

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO posts (user_id, content, created_at) VALUES (?, ?, ?)",
        (user["id"], content, now),
    )
    post_id = cur.lastrowid
    conn.commit()

    row = conn.execute(
        """
        SELECT
            posts.id,
            posts.content,
            posts.created_at,
            users.username,
            0 AS like_count,
            0 AS comment_count,
            0 AS liked_by_me
        FROM posts
        JOIN users ON users.id = posts.user_id
        WHERE posts.id = ?
        """,
        (post_id,),
    ).fetchone()

    conn.close()

    post = dict(row)
    post["created_at"] = format_time(post.get("created_at", ""))

    return jsonify({"post": post}), 201


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if len(username) < 3:
            flash("Username must be at least 3 characters.")
            return redirect(url_for("register"))
        if len(password) < 8:
            flash("Password must be at least 8 characters.")
            return redirect(url_for("register"))

        pw_hash = generate_password_hash(password)
        now = datetime.utcnow().isoformat()

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, pw_hash, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            flash("That username is already taken.")
            return redirect(url_for("register"))

        user_row = conn.execute("SELECT id, username FROM users WHERE username = ?", (username,)).fetchone()
        conn.close()

        session["user_id"] = user_row["id"]
        flash("Account created.")
        return redirect(url_for("index"))

    return render_template("register.html", user=current_user())


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        conn = get_db()
        user_row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        conn.close()

        if not user_row or not check_password_hash(user_row["password_hash"], password):
            flash("Invalid username or password.")
            return redirect(url_for("login"))

        session["user_id"] = user_row["id"]
        flash("Logged in.")
        return redirect(url_for("index"))

    return render_template("login.html", user=current_user())


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    flash("Logged out.")
    return redirect(url_for("index"))


@app.route("/post", methods=["POST"])
def create_post():
    user = current_user()
    if not user:
        abort(401)

    content = (request.form.get("content") or "").strip()
    if not content:
        flash("Post cannot be empty.")
        return redirect(url_for("index"))
    if len(content) > 500:
        flash("Post is too long. Limit is 500 characters.")
        return redirect(url_for("index"))

    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO posts (user_id, content, created_at) VALUES (?, ?, ?)",
        (user["id"], content, now),
    )
    conn.commit()
    conn.close()

    flash("Posted.")
    return redirect(url_for("index"))


@app.route("/follow/<username>", methods=["POST"])
def follow(username):
    user = current_user()
    if not user:
        abort(401)

    conn = get_db()
    target = conn.execute(
        "SELECT id, username FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    if not target:
        conn.close()
        abort(404)

    if target["id"] == user["id"]:
        conn.close()
        flash("You cannot follow yourself.")
        return redirect(url_for("profile", username=username))

    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO follows (follower_id, followee_id, created_at) VALUES (?, ?, ?)",
            (user["id"], target["id"], now),
        )
        conn.commit()
        flash("Followed.")
    except sqlite3.IntegrityError:
        flash("You are already following this user.")

    conn.close()
    return redirect(url_for("profile", username=username))


@app.route("/unfollow/<username>", methods=["POST"])
def unfollow(username):
    user = current_user()
    if not user:
        abort(401)

    conn = get_db()
    target = conn.execute(
        "SELECT id, username FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    if not target:
        conn.close()
        abort(404)

    conn.execute(
        "DELETE FROM follows WHERE follower_id = ? AND followee_id = ?",
        (user["id"], target["id"]),
    )
    conn.commit()
    conn.close()

    flash("Unfollowed.")
    return redirect(url_for("profile", username=username))


@app.route("/like/<int:post_id>", methods=["POST"])
def like(post_id: int):
    user = current_user()
    if not user:
        abort(401)

    now = datetime.utcnow().isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO likes (user_id, post_id, created_at) VALUES (?, ?, ?)",
            (user["id"], post_id, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

    return redirect(request.referrer or url_for("index"))


@app.route("/unlike/<int:post_id>", methods=["POST"])
def unlike(post_id: int):
    user = current_user()
    if not user:
        abort(401)

    conn = get_db()
    conn.execute(
        "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
        (user["id"], post_id),
    )
    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("index"))


@app.route("/comment/<int:post_id>", methods=["POST"])
def comment(post_id: int):
    user = current_user()
    if not user:
        abort(401)

    content = (request.form.get("content") or "").strip()
    if not content:
        return redirect(request.referrer or url_for("index"))
    if len(content) > 300:
        flash("Comment is too long. Limit is 300 characters.")
        return redirect(request.referrer or url_for("index"))

    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO comments (post_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
        (post_id, user["id"], content, now),
    )
    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("index"))


@app.route("/u/<username>")
def profile(username):
    viewer = current_user()
    conn = get_db()

    user_row = conn.execute(
        "SELECT id, username FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    if not user_row:
        conn.close()
        abort(404)

    # 追蹤數量
    followers_count = conn.execute(
        "SELECT COUNT(*) AS c FROM follows WHERE followee_id = ?",
        (user_row["id"],),
    ).fetchone()["c"]

    following_count = conn.execute(
        "SELECT COUNT(*) AS c FROM follows WHERE follower_id = ?",
        (user_row["id"],),
    ).fetchone()["c"]

    # viewer 是否追蹤此人
    is_following = False
    if viewer and viewer["id"] != user_row["id"]:
        row = conn.execute(
            "SELECT 1 FROM follows WHERE follower_id = ? AND followee_id = ?",
            (viewer["id"], user_row["id"]),
        ).fetchone()
        is_following = row is not None

    # 抓此使用者的貼文，並且帶 like_count, comment_count, liked_by_me
    viewer_id = viewer["id"] if viewer else None
    posts = conn.execute(
        """
        SELECT
            posts.id,
            posts.content,
            posts.created_at,
            users.username,
            COALESCE(lc.cnt, 0) AS like_count,
            COALESCE(cc.cnt, 0) AS comment_count,
            CASE
                WHEN ? IS NULL THEN 0
                WHEN EXISTS (
                    SELECT 1
                    FROM likes
                    WHERE likes.post_id = posts.id AND likes.user_id = ?
                ) THEN 1
                ELSE 0
            END AS liked_by_me
        FROM posts
        JOIN users ON users.id = posts.user_id
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS cnt
            FROM likes
            GROUP BY post_id
        ) AS lc ON lc.post_id = posts.id
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS cnt
            FROM comments
            GROUP BY post_id
        ) AS cc ON cc.post_id = posts.id
        WHERE posts.user_id = ?
        ORDER BY posts.id DESC
        """,
        (viewer_id, viewer_id, user_row["id"]),
    ).fetchall()

    posts = [dict(p) for p in posts]
    for p in posts:
        p["created_at"] = format_time(p.get("created_at", ""))

    # 抓留言，依 post_id 分組
    post_ids = [p["id"] for p in posts]
    comments_by_post: dict[int, list[dict]] = {}

    if post_ids:
        placeholders = ",".join(["?"] * len(post_ids))
        rows = conn.execute(
            f"""
            SELECT
                comments.id,
                comments.post_id,
                comments.content,
                comments.created_at,
                users.username
            FROM comments
            JOIN users ON users.id = comments.user_id
            WHERE comments.post_id IN ({placeholders})
            ORDER BY comments.id ASC
            """,
            tuple(post_ids),
        ).fetchall()

        rows = [dict(r) for r in rows]
        for r in rows:
            r["created_at"] = format_time(r.get("created_at", ""))
            comments_by_post.setdefault(r["post_id"], []).append(r)

    conn.close()

    return render_template(
        "profile.html",
        user=viewer,
        profile_user=user_row,
        posts=posts,
        comments_by_post=comments_by_post,
        followers_count=followers_count,
        following_count=following_count,
        is_following=is_following,
    )



if __name__ == "__main__":
    app.run(debug=True)
