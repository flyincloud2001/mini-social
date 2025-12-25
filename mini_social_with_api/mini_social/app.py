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


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS follows (
        follower_id INTEGER NOT NULL,
        followee_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (follower_id, followee_id),
        FOREIGN KEY(follower_id) REFERENCES users(id),
        FOREIGN KEY(followee_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS likes (
        user_id INTEGER NOT NULL,
        post_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, post_id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(post_id) REFERENCES posts(id)
    );

    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        post_id INTEGER NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(post_id) REFERENCES posts(id)
    );
    """
    )
    conn.commit()
    conn.close()


init_db()


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = get_db()
    u = conn.execute("SELECT id, username FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return dict(u) if u else None


def format_time(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
    except Exception:
        return iso_str

    tz = None
    if ZoneInfo:
        try:
            tz = ZoneInfo("America/Toronto")
        except Exception:
            tz = None

    if tz:
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            dt = dt.astimezone(tz)
        except Exception:
            pass

    return dt.strftime("%b %d %I:%M %p")


@app.route("/", methods=["GET"])
def index():
    user = current_user()

    conn = get_db()
    if user:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.content,
                p.created_at,
                u.username,
                (
                    SELECT COUNT(*) FROM likes l
                    WHERE l.post_id = p.id
                ) AS like_count,
                (
                    SELECT COUNT(*) FROM comments c
                    WHERE c.post_id = p.id
                ) AS comment_count,
                EXISTS(
                    SELECT 1 FROM likes l2
                    WHERE l2.post_id = p.id AND l2.user_id = ?
                ) AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            ORDER BY p.created_at DESC
            """,
            (user["id"],),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.content,
                p.created_at,
                u.username,
                (
                    SELECT COUNT(*) FROM likes l
                    WHERE l.post_id = p.id
                ) AS like_count,
                (
                    SELECT COUNT(*) FROM comments c
                    WHERE c.post_id = p.id
                ) AS comment_count,
                0 AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            ORDER BY p.created_at DESC
            """
        ).fetchall()

    posts = [dict(r) for r in rows]
    for p in posts:
        p["created_at"] = format_time(p.get("created_at", ""))

    comments_rows = conn.execute(
        """
        SELECT
            c.post_id,
            c.content,
            c.created_at,
            u.username
        FROM comments c
        JOIN users u ON u.id = c.user_id
        ORDER BY c.created_at ASC
        """
    ).fetchall()

    comments_by_post = {}
    for r in comments_rows:
        d = dict(r)
        d["created_at"] = format_time(d.get("created_at", ""))
        comments_by_post.setdefault(d["post_id"], []).append(d)

    conn.close()

    return render_template(
        "index.html",
        user=user,
        posts=posts,
        comments_by_post=comments_by_post,
        feed_mode="public",
    )


@app.route("/following", methods=["GET"])
def following_feed():
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    conn = get_db()
    rows = conn.execute(
        """
        SELECT
            p.id,
            p.content,
            p.created_at,
            u.username,
            (
                SELECT COUNT(*) FROM likes l
                WHERE l.post_id = p.id
            ) AS like_count,
            (
                SELECT COUNT(*) FROM comments c
                WHERE c.post_id = p.id
            ) AS comment_count,
            EXISTS(
                SELECT 1 FROM likes l2
                WHERE l2.post_id = p.id AND l2.user_id = ?
            ) AS liked_by_me
        FROM posts p
        JOIN users u ON u.id = p.user_id
        WHERE p.user_id IN (
            SELECT followee_id FROM follows
            WHERE follower_id = ?
        )
        ORDER BY p.created_at DESC
        """,
        (user["id"], user["id"]),
    ).fetchall()

    posts = [dict(r) for r in rows]
    for p in posts:
        p["created_at"] = format_time(p.get("created_at", ""))

    comments_rows = conn.execute(
        """
        SELECT
            c.post_id,
            c.content,
            c.created_at,
            u.username
        FROM comments c
        JOIN users u ON u.id = c.user_id
        ORDER BY c.created_at ASC
        """
    ).fetchall()

    comments_by_post = {}
    for r in comments_rows:
        d = dict(r)
        d["created_at"] = format_time(d.get("created_at", ""))
        comments_by_post.setdefault(d["post_id"], []).append(d)

    conn.close()

    return render_template(
        "index.html",
        user=user,
        posts=posts,
        comments_by_post=comments_by_post,
        feed_mode="following",
    )


@app.route("/api/posts", methods=["GET"])
def api_posts():
    user = current_user()

    conn = get_db()
    if user:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.content,
                p.created_at,
                u.username,
                (
                    SELECT COUNT(*) FROM likes l
                    WHERE l.post_id = p.id
                ) AS like_count,
                (
                    SELECT COUNT(*) FROM comments c
                    WHERE c.post_id = p.id
                ) AS comment_count,
                EXISTS(
                    SELECT 1 FROM likes l2
                    WHERE l2.post_id = p.id AND l2.user_id = ?
                ) AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            ORDER BY p.created_at DESC
            """,
            (user["id"],),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
                p.id,
                p.content,
                p.created_at,
                u.username,
                (
                    SELECT COUNT(*) FROM likes l
                    WHERE l.post_id = p.id
                ) AS like_count,
                (
                    SELECT COUNT(*) FROM comments c
                    WHERE c.post_id = p.id
                ) AS comment_count,
                0 AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            ORDER BY p.created_at DESC
            """
        ).fetchall()

    posts = [dict(r) for r in rows]
    for p in posts:
        p["created_at"] = format_time(p.get("created_at", ""))

    conn.close()
    return jsonify({"posts": posts, "user": user})


@app.route("/api/posts", methods=["POST"])
def api_create_post():
    user = current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Content is required."}), 400

    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO posts (user_id, content, created_at) VALUES (?, ?, ?)",
        (user["id"], content, now),
    )
    conn.commit()
    conn.close()

    return jsonify({"ok": True})


@app.route("/api/posts/<int:post_id>/like", methods=["POST"])
def api_like_post(post_id: int):
    user = current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401

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

    like_count = conn.execute(
        "SELECT COUNT(*) AS c FROM likes WHERE post_id = ?",
        (post_id,),
    ).fetchone()["c"]
    conn.close()

    return jsonify({"post_id": post_id, "liked_by_me": 1, "like_count": like_count})


@app.route("/api/posts/<int:post_id>/like", methods=["DELETE"])
def api_unlike_post(post_id: int):
    user = current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401

    conn = get_db()
    conn.execute(
        "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
        (user["id"], post_id),
    )
    conn.commit()

    like_count = conn.execute(
        "SELECT COUNT(*) AS c FROM likes WHERE post_id = ?",
        (post_id,),
    ).fetchone()["c"]
    conn.close()

    return jsonify({"post_id": post_id, "liked_by_me": 0, "like_count": like_count})

@app.route("/api/posts/<int:post_id>/comments", methods=["POST"])
def api_create_comment(post_id: int):
    user = current_user()
    if not user:
        return jsonify({"error": "Authentication required."}), 401

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()

    if not content:
        return jsonify({"error": "Comment cannot be empty."}), 400

    if len(content) > 300:
        return jsonify({"error": "Comment is too long. Limit is 300 characters."}), 400

    now = datetime.utcnow().isoformat()

    conn = get_db()

    post_row = conn.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post_row:
        conn.close()
        return jsonify({"error": "Post not found."}), 404

    conn.execute(
        "INSERT INTO comments (post_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
        (post_id, user["id"], content, now),
    )
    conn.commit()

    comment_count = conn.execute(
        "SELECT COUNT(*) AS c FROM comments WHERE post_id = ?",
        (post_id,),
    ).fetchone()["c"]

    conn.close()

    return jsonify(
        {
            "comment_count": comment_count,
            "comment": {
                "post_id": post_id,
                "username": user["username"],
                "content": content,
                "created_at": format_time(now),
            },
        }
    ), 200

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not username or not password:
        flash("Username and password are required.")
        return redirect(url_for("register"))

    now = datetime.utcnow().isoformat()
    pw_hash = generate_password_hash(password)

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, pw_hash, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        flash("Username already exists.")
        return redirect(url_for("register"))

    user_row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    session["user_id"] = user_row["id"]
    flash("Registered.")
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    conn = get_db()
    row = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not row or not check_password_hash(row["password_hash"], password):
        flash("Invalid username or password.")
        return redirect(url_for("login"))

    session["user_id"] = row["id"]
    flash("Signed in.")
    return redirect(url_for("index"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Signed out.")
    return redirect(url_for("index"))


@app.route("/u/<username>", methods=["GET"])
def profile(username: str):
    viewer = current_user()

    conn = get_db()
    user_row = conn.execute(
        "SELECT id, username FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    if not user_row:
        conn.close()
        abort(404)

    if viewer:
        is_following = conn.execute(
            """
            SELECT 1 FROM follows
            WHERE follower_id = ? AND followee_id = ?
            """,
            (viewer["id"], user_row["id"]),
        ).fetchone()
        is_following = True if is_following else False
    else:
        is_following = False

    followers_count = conn.execute(
        "SELECT COUNT(*) AS c FROM follows WHERE followee_id = ?",
        (user_row["id"],),
    ).fetchone()["c"]

    following_count = conn.execute(
        "SELECT COUNT(*) AS c FROM follows WHERE follower_id = ?",
        (user_row["id"],),
    ).fetchone()["c"]

    if viewer:
        posts_rows = conn.execute(
            """
            SELECT
                p.id,
                p.content,
                p.created_at,
                u.username,
                (
                    SELECT COUNT(*) FROM likes l
                    WHERE l.post_id = p.id
                ) AS like_count,
                (
                    SELECT COUNT(*) FROM comments c
                    WHERE c.post_id = p.id
                ) AS comment_count,
                EXISTS(
                    SELECT 1 FROM likes l2
                    WHERE l2.post_id = p.id AND l2.user_id = ?
                ) AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            WHERE p.user_id = ?
            ORDER BY p.created_at DESC
            """,
            (viewer["id"], user_row["id"]),
        ).fetchall()
    else:
        posts_rows = conn.execute(
            """
            SELECT
                p.id,
                p.content,
                p.created_at,
                u.username,
                (
                    SELECT COUNT(*) FROM likes l
                    WHERE l.post_id = p.id
                ) AS like_count,
                (
                    SELECT COUNT(*) FROM comments c
                    WHERE c.post_id = p.id
                ) AS comment_count,
                0 AS liked_by_me
            FROM posts p
            JOIN users u ON u.id = p.user_id
            WHERE p.user_id = ?
            ORDER BY p.created_at DESC
            """,
            (user_row["id"],),
        ).fetchall()

    posts = [dict(r) for r in posts_rows]
    for p in posts:
        p["created_at"] = format_time(p.get("created_at", ""))

    comments_rows = conn.execute(
        """
        SELECT
            c.post_id,
            c.content,
            c.created_at,
            u.username
        FROM comments c
        JOIN users u ON u.id = c.user_id
        WHERE c.post_id IN (
            SELECT id FROM posts WHERE user_id = ?
        )
        ORDER BY c.created_at ASC
        """,
        (user_row["id"],),
    ).fetchall()

    comments_by_post = {}
    for r in comments_rows:
        d = dict(r)
        d["created_at"] = format_time(d.get("created_at", ""))
        comments_by_post.setdefault(d["post_id"], []).append(d)

    conn.close()

    return render_template(
        "profile.html",
        user=viewer,
        profile_user=user_row,
        posts=posts,
        comments_by_post=comments_by_post,
        is_following=is_following,
        followers_count=followers_count,
        following_count=following_count,
    )


@app.route("/follow/<username>", methods=["POST"])
def follow(username: str):
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    conn = get_db()
    target = conn.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    if not target:
        conn.close()
        abort(404)

    now = datetime.utcnow().isoformat()
    try:
        conn.execute(
            "INSERT INTO follows (follower_id, followee_id, created_at) VALUES (?, ?, ?)",
            (user["id"], target["id"], now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass

    conn.close()
    flash("Followed.")
    return redirect(url_for("profile", username=username))


@app.route("/unfollow/<username>", methods=["POST"])
def unfollow(username: str):
    user = current_user()
    if not user:
        return redirect(url_for("login"))

    conn = get_db()
    target = conn.execute(
        "SELECT id FROM users WHERE username = ?",
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
        return redirect(url_for("login"))

    content = (request.form.get("content") or "").strip()
    if not content:
        flash("Comment cannot be empty.")
        return redirect(request.referrer or url_for("index"))

    now = datetime.utcnow().isoformat()
    conn = get_db()
    conn.execute(
        "INSERT INTO comments (user_id, post_id, content, created_at) VALUES (?, ?, ?, ?)",
        (user["id"], post_id, content, now),
    )
    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
