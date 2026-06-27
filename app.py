import os
import base64
from datetime import datetime, timedelta
from functools import wraps

import click
from flask import Flask, render_template, redirect, url_for, request, flash, abort, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# --- Config ------------------------------------------------------------------
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///instance/axiom.db"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=90)  # stay logged in 90 days
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

if app.config["SQLALCHEMY_DATABASE_URI"].startswith("postgres://"):
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["SQLALCHEMY_DATABASE_URI"].replace(
        "postgres://", "postgresql://", 1
    )

db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to view that page."

BRAND_NAME = "Axiom"

# --- Constants -----------------------------------------------------------------
TIER_ORDER = ["free", "first", "standard", "premium"]

TIERS = {
    "free":     {"label": "Free Member",     "price": 0,     "period": "forever"},
    "first":    {"label": "First Member",    "price": 4500,  "period": "one-time"},
    "standard": {"label": "Standard Member", "price": 8000,  "period": "per month"},
    "premium":  {"label": "Premium Member",  "price": 15000, "period": "per month"},
}

PAYMENT_METHODS = [
    {"name": "KBZPay",   "account_name": "Kaung Khant Zaw", "account_number": "09942191320"},
    {"name": "KBZ Bank", "account_name": "Kaung Khant Zaw", "account_number": "20651120602778601"},
    {"name": "AYA Pay",  "account_name": "Kaung Khant Zaw", "account_number": "09942191320"},
    {"name": "AYA Bank", "account_name": "Kaung Khant Zaw", "account_number": "40032475199"},
    {"name": "Wave Pay", "account_name": "Kaung Khant Zaw", "account_number": "09765126216"},
    {"name": "UAB Pay",  "account_name": "Kaung Khant Zaw", "account_number": "09942191320"},
    {"name": "UAB Bank", "account_name": None, "account_number": None},
]
PAYMENT_METHOD_NAMES = [m["name"] for m in PAYMENT_METHODS]

MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2MB


# --- Models ----------------------------------------------------------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(20), default="member")            # 'admin', 'ceo', or 'member'
    tier = db.Column(db.String(20), default="free")               # free / first / standard / premium
    tier_expires_at = db.Column(db.DateTime, nullable=True)
    can_post_updates = db.Column(db.Boolean, default=False)

    bio = db.Column(db.Text, default="")
    skills = db.Column(db.String(280), default="")
    position = db.Column(db.String(80), nullable=True)

    # Avatar
    avatar_data = db.Column(db.Text, nullable=True)               # base64
    avatar_mime = db.Column(db.String(40), nullable=True)
    avatar_updated_at = db.Column(db.DateTime, nullable=True)     # for 1-week change limit

    # Ban
    is_banned = db.Column(db.Boolean, default=False)
    ban_expires_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    posts = db.relationship("Post", backref="author", lazy=True)
    jobs = db.relationship("JobPost", backref="author", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role in ("admin", "ceo")

    @property
    def is_ceo(self):
        return self.role == "ceo"

    @property
    def badge_label(self):
        if self.role == "ceo":
            return "CEO"
        if self.position:
            return self.position
        if self.role == "admin":
            return "Admin"
        return None

    @property
    def tier_label(self):
        return TIERS.get(self.tier, TIERS["free"])["label"]

    @property
    def can_post(self):
        return self.is_admin or self.tier == "premium"

    @property
    def can_use_premium_chat(self):
        return self.is_admin or self.tier in ("standard", "premium")

    @property
    def tier_is_expired(self):
        try:
            return self.tier_expires_at is not None and self.tier_expires_at < datetime.utcnow()
        except Exception:
            return False

    @property
    def is_currently_banned(self):
        try:
            if not self.is_banned:
                return False
            if self.ban_expires_at and self.ban_expires_at < datetime.utcnow():
                return False
            return True
        except Exception:
            return False

    @property
    def can_change_avatar(self):
        try:
            if self.avatar_updated_at is None:
                return True
            return datetime.utcnow() - self.avatar_updated_at >= timedelta(weeks=1)
        except Exception:
            return True

    @property
    def avatar_src(self):
        try:
            if self.avatar_data and self.avatar_mime:
                return f"data:{self.avatar_mime};base64,{self.avatar_data}"
        except Exception:
            pass
        return None


class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    following_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("follower_id", "following_id"),)


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(140), nullable=False)
    description = db.Column(db.Text, nullable=False)
    event_date = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(200), nullable=True)       # venue or "Online"
    image_data = db.Column(db.Text, nullable=True)
    image_mime = db.Column(db.String(40), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    organizer = db.relationship("User", backref="events")


class SupportMessage(db.Model):
    """Customer service messages between members and admins."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_from_admin = db.Column(db.Boolean, default=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    sender = db.relationship("User", foreign_keys=[sender_id])


class Post(db.Model):
    """Serves both the community feed ('community') and admin/company broadcasts ('update')."""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(140), nullable=True)
    content = db.Column(db.Text, nullable=False)
    tag = db.Column(db.String(30), default="Update")
    image_url = db.Column(db.String(500), nullable=True)
    image_data = db.Column(db.Text, nullable=True)
    image_mime = db.Column(db.String(40), nullable=True)
    post_type = db.Column(db.String(20), default="community")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Cascade-delete all child records when the post is deleted
    likes    = db.relationship("PostLike",   backref="post", lazy=True, cascade="all, delete-orphan", passive_deletes=True)
    comments = db.relationship("Comment",    backref="post", lazy=True, cascade="all, delete-orphan", passive_deletes=True)
    reports  = db.relationship("PostReport", backref="post", lazy=True, cascade="all, delete-orphan", passive_deletes=True)


class PaymentRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tier_requested = db.Column(db.String(20), nullable=False)
    method = db.Column(db.String(40), nullable=False)
    payer_name = db.Column(db.String(120), nullable=False)
    reference_note = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default="pending")           # pending / approved / rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])


class JobPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    job_type = db.Column(db.String(20), nullable=False)             # hiring / freelance
    title = db.Column(db.String(140), nullable=False)
    category = db.Column(db.String(80), nullable=True)
    budget_text = db.Column(db.String(80), nullable=True)
    description = db.Column(db.Text, nullable=False)
    contact_info = db.Column(db.String(140), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    room = db.Column(db.String(20), nullable=False)                  # 'free' or 'premium'
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User")


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255), default="")
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PostLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id", ondelete="CASCADE"), nullable=False)
    __table_args__ = (db.UniqueConstraint("user_id", "post_id"),)


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("comment.id", ondelete="CASCADE"), nullable=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship("User", backref="comments")
    replies = db.relationship("Comment", backref=db.backref("parent", remote_side=[id]), cascade="all, delete-orphan", foreign_keys=[parent_id])


class CommentLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    comment_id = db.Column(db.Integer, db.ForeignKey("comment.id", ondelete="CASCADE"), nullable=False)
    __table_args__ = (db.UniqueConstraint("user_id", "comment_id"),)


class PostReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id", ondelete="CASCADE"), nullable=False)
    reporter_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reason = db.Column(db.String(280), nullable=False)
    status = db.Column(db.String(20), default="pending")     # pending / reviewed / dismissed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    reporter = db.relationship("User", foreign_keys=[reporter_id], backref="filed_reports")


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def notify(user_id, message, link=""):
    db.session.add(Notification(user_id=user_id, message=message, link=link))
    db.session.commit()


def process_image_upload(file_storage, max_bytes=MAX_IMAGE_BYTES):
    """Reads an uploaded file and returns (base64_data, mime) or (None, None) if no file was given.
    Raises ValueError with a user-facing message if the file is invalid."""
    if not file_storage or not file_storage.filename:
        return None, None
    raw = file_storage.read()
    if not raw:
        return None, None
    if len(raw) > max_bytes:
        raise ValueError("Image is too large (max 2MB).")
    mime = file_storage.mimetype or ""
    if not mime.startswith("image/"):
        raise ValueError("Please upload an image file.")
    return base64.b64encode(raw).decode("utf-8"), mime


def serialize_chat_message(m):
    return {
        "id": m.id,
        "user_id": m.user_id,
        "name": m.user.name,
        "badge": m.user.badge_label,
        "wall_url": url_for("wall", user_id=m.user_id),
        "content": m.content,
        "time": m.created_at.strftime("%I:%M %p"),
        "is_self": m.user_id == current_user.id if current_user.is_authenticated else False,
    }


# --- Access control decorators -----------------------------------------------
def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def tier_icon(tier):
    icons = {
        "free":     "○",     # open circle
        "first":    "★",     # star  (founding / early-bird)
        "standard": "◆",     # diamond
        "premium":  "♛",     # crown
    }
    return icons.get(tier, "")


@app.context_processor
def inject_globals():
    unread_notifications = []
    if current_user.is_authenticated:
        unread_notifications = (
            Notification.query
            .filter_by(user_id=current_user.id, is_read=False)
            .order_by(Notification.created_at.desc())
            .limit(6)
            .all()
        )
    return dict(
        brand_name=BRAND_NAME,
        tiers=TIERS,
        tier_order=TIER_ORDER,
        payment_methods=PAYMENT_METHODS,
        unread_notifications=unread_notifications,
        tier_icon=tier_icon,
        now_year=datetime.utcnow().year,
    )


@app.before_request
def check_membership_expiry():
    if current_user.is_authenticated:
        # Auto-lift time-limited bans
        if current_user.is_banned and current_user.ban_expires_at and current_user.ban_expires_at < datetime.utcnow():
            current_user.is_banned = False
            current_user.ban_expires_at = None
            db.session.commit()

        # Auto-downgrade expired memberships
        if current_user.tier in ("standard", "premium") and current_user.tier_is_expired:
            current_user.tier = "free"
            current_user.tier_expires_at = None
            db.session.commit()
            notify(current_user.id, "Your membership period ended and reverted to Free. You can renew anytime from Pricing.", url_for("pricing"))


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="You don't have access to this page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404


# --- Public routes -------------------------------------------------------------
@app.route("/")
def index():
    posts = Post.query.filter_by(post_type="community").order_by(Post.created_at.desc()).limit(3).all()
    updates = Post.query.filter_by(post_type="update").order_by(Post.created_at.desc()).limit(3).all()
    return render_template("index.html", posts=posts, updates=updates)


@app.route("/pricing")
def pricing():
    return render_template("pricing.html")


# --- Auth ------------------------------------------------------------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or not password:
            flash("Please fill in every field.", "error")
            return redirect(url_for("signup"))

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return redirect(url_for("login"))

        user = User(name=name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user, remember=True)
        flash(f"Welcome to {BRAND_NAME}! You're on the Free tier.", "success")
        return redirect(url_for("dashboard"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if user is None or not user.check_password(password):
            flash("Email or password is incorrect.", "error")
            return redirect(url_for("login"))

        login_user(user, remember=True)
        flash(f"Welcome back, {user.name}!", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You've been logged out.", "success")
    return redirect(url_for("index"))


@app.route("/forgot-password")
def forgot_password():
    return render_template("forgot_password.html")


# --- Dashboard / community feed / profile ----------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    updates = Post.query.filter_by(post_type="update").order_by(Post.created_at.desc()).limit(1).all()
    posts = Post.query.filter_by(post_type="community").order_by(Post.created_at.desc()).limit(20).all()
    return render_template("dashboard.html", updates=updates, posts=posts)


@app.route("/posts/new", methods=["POST"])
@login_required
def create_post():
    if not current_user.can_post:
        abort(403)
    if current_user.is_currently_banned:
        flash("Your account is currently restricted and cannot post.", "error")
        return redirect(url_for("dashboard"))

    content = request.form.get("content", "").strip()
    tag = request.form.get("tag", "Update")

    if not content:
        flash("Write something before posting.", "error")
        return redirect(url_for("dashboard"))

    try:
        image_data, image_mime = process_image_upload(request.files.get("image"))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("dashboard"))

    post = Post(content=content, image_data=image_data, image_mime=image_mime, tag=tag, post_type="community", user_id=current_user.id)
    db.session.add(post)
    db.session.commit()
    flash("Posted to the community feed.", "success")
    return redirect(url_for("dashboard"))


@app.route("/posts/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    try:
        # Manually remove child records first (handles cases where tables
        # exist but FK cascade hasn't been set up on the live DB yet)
        PostReport.query.filter_by(post_id=post_id).delete(synchronize_session=False)
        comment_ids = [c.id for c in Comment.query.filter_by(post_id=post_id).all()]
        if comment_ids:
            CommentLike.query.filter(CommentLike.comment_id.in_(comment_ids)).delete(synchronize_session=False)
        Comment.query.filter_by(post_id=post_id).delete(synchronize_session=False)
        PostLike.query.filter_by(post_id=post_id).delete(synchronize_session=False)
        db.session.delete(post)
        db.session.commit()
        flash("Post deleted.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Could not delete post: {e}", "error")
    return redirect(request.referrer or url_for("dashboard"))


# --- Stats API (live user counter on home page) --------------------------------
@app.route("/api/stats")
def api_stats():
    total = User.query.count()
    premium = User.query.filter(User.tier.in_(["standard", "premium"])).count()
    return jsonify({"total": total, "premium": premium})


# --- Post likes ---------------------------------------------------------------
@app.route("/posts/<int:post_id>/like", methods=["POST"])
@login_required
def post_like(post_id):
    post = Post.query.get_or_404(post_id)
    existing = PostLike.query.filter_by(user_id=current_user.id, post_id=post_id).first()
    if existing:
        db.session.delete(existing)
        liked = False
    else:
        db.session.add(PostLike(user_id=current_user.id, post_id=post_id))
        liked = True
    db.session.commit()
    count = PostLike.query.filter_by(post_id=post_id).count()
    return jsonify({"liked": liked, "count": count})


# --- Comments ----------------------------------------------------------------
def _serialize_comment(c, current_uid):
    return {
        "id": c.id,
        "user_id": c.user_id,
        "name": c.author.name,
        "avatar_src": c.author.avatar_src,
        "wall_url": url_for("wall", user_id=c.user_id),
        "content": c.content,
        "time": c.created_at.strftime("%b %d · %I:%M %p"),
        "likes": CommentLike.query.filter_by(comment_id=c.id).count(),
        "liked": bool(CommentLike.query.filter_by(comment_id=c.id, user_id=current_uid).first()),
        "is_own": c.user_id == current_uid,
        "parent_id": c.parent_id,
    }


@app.route("/posts/<int:post_id>/comments")
@login_required
def post_comments(post_id):
    Post.query.get_or_404(post_id)
    # Fetch ALL comments for this post in chronological order
    all_comments = Comment.query.filter_by(post_id=post_id).order_by(Comment.created_at.asc()).all()

    # Build flat structure: top-level comments, each with all their replies flat
    top = [c for c in all_comments if c.parent_id is None]
    reply_map = {}
    for c in all_comments:
        if c.parent_id is not None:
            reply_map.setdefault(c.parent_id, []).append(c)

    def collect_all_replies(comment_id):
        """Recursively collect all replies at any depth in chronological order."""
        direct = reply_map.get(comment_id, [])
        result = []
        for r in direct:
            result.append(r)
            result.extend(collect_all_replies(r.id))
        return result

    result = []
    for c in top:
        data = _serialize_comment(c, current_user.id)
        data["replies"] = [_serialize_comment(r, current_user.id) for r in collect_all_replies(c.id)]
        result.append(data)

    like_count = PostLike.query.filter_by(post_id=post_id).count()
    liked = bool(PostLike.query.filter_by(post_id=post_id, user_id=current_user.id).first())
    return jsonify({"comments": result, "like_count": like_count, "liked": liked})


@app.route("/posts/<int:post_id>/comments/poll")
@login_required
def poll_comments(post_id):
    """Returns only comments newer than `after_id` for real-time updates."""
    Post.query.get_or_404(post_id)
    after_id = request.args.get("after", 0, type=int)

    new_comments = (Comment.query
                    .filter_by(post_id=post_id)
                    .filter(Comment.id > after_id)
                    .order_by(Comment.created_at.asc())
                    .all())

    return jsonify([_serialize_comment(c, current_user.id) for c in new_comments])


@app.route("/posts/<int:post_id>/comments/new", methods=["POST"])
@login_required
def new_comment(post_id):
    if current_user.is_currently_banned:
        return jsonify({"error": "Your account is restricted."}), 403
    post = Post.query.get_or_404(post_id)
    content = (request.json or request.form).get("content", "").strip()
    parent_id = (request.json or request.form).get("parent_id", None)
    if parent_id:
        parent_id = int(parent_id)
    if not content:
        return jsonify({"error": "empty"}), 400
    c = Comment(post_id=post_id, user_id=current_user.id, content=content, parent_id=parent_id)
    db.session.add(c)
    db.session.commit()
    # notify post author (if not self-commenting)
    if post.user_id != current_user.id:
        notify(post.user_id, f"{current_user.name} commented on your post.", url_for("dashboard"))
    return jsonify(_serialize_comment(c, current_user.id))


@app.route("/comments/<int:comment_id>/like", methods=["POST"])
@login_required
def comment_like(comment_id):
    c = Comment.query.get_or_404(comment_id)
    existing = CommentLike.query.filter_by(user_id=current_user.id, comment_id=comment_id).first()
    if existing:
        db.session.delete(existing)
        liked = False
    else:
        db.session.add(CommentLike(user_id=current_user.id, comment_id=comment_id))
        liked = True
    db.session.commit()
    count = CommentLike.query.filter_by(comment_id=comment_id).count()
    return jsonify({"liked": liked, "count": count})


@app.route("/comments/<int:comment_id>/delete", methods=["POST"])
@login_required
def delete_comment(comment_id):
    c = Comment.query.get_or_404(comment_id)
    if c.user_id != current_user.id and not current_user.is_admin:
        return jsonify({"error": "forbidden"}), 403
    db.session.delete(c)
    db.session.commit()
    return jsonify({"ok": True})


# --- Post Reports ------------------------------------------------------------
@app.route("/posts/<int:post_id>/report", methods=["POST"])
@login_required
def report_post(post_id):
    post = Post.query.get_or_404(post_id)
    reason = (request.json or request.form).get("reason", "").strip()
    if not reason:
        return jsonify({"error": "Please provide a reason."}), 400
    # prevent duplicate pending report from same user
    if not PostReport.query.filter_by(post_id=post_id, reporter_id=current_user.id, status="pending").first():
        db.session.add(PostReport(post_id=post_id, reporter_id=current_user.id, reason=reason))
        db.session.commit()
    return jsonify({"ok": True})


@app.route("/u/<int:user_id>")
@login_required
def wall(user_id):
    member = User.query.get_or_404(user_id)
    member_posts = (
        Post.query.filter_by(user_id=member.id, post_type="community")
        .order_by(Post.created_at.desc()).limit(30).all()
    )
    member_jobs = (
        JobPost.query.filter_by(user_id=member.id)
        .order_by(JobPost.created_at.desc()).limit(10).all()
    )
    is_following = bool(Follow.query.filter_by(follower_id=current_user.id, following_id=user_id).first())
    follower_count = Follow.query.filter_by(following_id=user_id).count()
    following_count = Follow.query.filter_by(follower_id=user_id).count()
    return render_template("wall.html", member=member, member_posts=member_posts, member_jobs=member_jobs,
                           is_following=is_following, follower_count=follower_count, following_count=following_count)


# --- Member search -----------------------------------------------------------
@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    results = []
    if q:
        results = (
            User.query
            .filter(User.name.ilike(f"%{q}%"))
            .order_by(User.name.asc())
            .limit(30).all()
        )
    return render_template("search.html", q=q, results=results)


# --- Follow / Unfollow -------------------------------------------------------
@app.route("/u/<int:user_id>/follow", methods=["POST"])
@login_required
def follow(user_id):
    if user_id == current_user.id:
        flash("You can't follow yourself.", "error")
        return redirect(url_for("wall", user_id=user_id))
    if not Follow.query.filter_by(follower_id=current_user.id, following_id=user_id).first():
        db.session.add(Follow(follower_id=current_user.id, following_id=user_id))
        db.session.commit()
        notify(user_id, f"{current_user.name} started following you.", url_for("wall", user_id=current_user.id))
    return redirect(url_for("wall", user_id=user_id))


@app.route("/u/<int:user_id>/unfollow", methods=["POST"])
@login_required
def unfollow(user_id):
    f = Follow.query.filter_by(follower_id=current_user.id, following_id=user_id).first()
    if f:
        db.session.delete(f)
        db.session.commit()
    return redirect(url_for("wall", user_id=user_id))


# --- Avatar upload / delete --------------------------------------------------
@app.route("/profile/change-password", methods=["POST"])
@login_required
def change_password():
    current_pw = request.form.get("current_password", "")
    new_pw = request.form.get("new_password", "")
    confirm_pw = request.form.get("confirm_password", "")

    if not current_user.check_password(current_pw):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("profile"))
    if len(new_pw) < 6:
        flash("New password must be at least 6 characters.", "error")
        return redirect(url_for("profile"))
    if new_pw != confirm_pw:
        flash("Passwords do not match.", "error")
        return redirect(url_for("profile"))

    current_user.set_password(new_pw)
    db.session.commit()
    flash("Password updated successfully.", "success")
    return redirect(url_for("profile"))
@login_required
def avatar_upload():
    if not current_user.can_change_avatar:
        flash("You can only change your profile picture once per week.", "error")
        return redirect(url_for("profile"))
    try:
        data, mime = process_image_upload(request.files.get("avatar"), max_bytes=1 * 1024 * 1024)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("profile"))
    if not data:
        flash("Please select an image file.", "error")
        return redirect(url_for("profile"))
    current_user.avatar_data = data
    current_user.avatar_mime = mime
    current_user.avatar_updated_at = datetime.utcnow()
    db.session.commit()
    flash("Profile picture updated.", "success")
    return redirect(url_for("profile"))


@app.route("/profile/avatar/delete", methods=["POST"])
@login_required
def avatar_delete():
    current_user.avatar_data = None
    current_user.avatar_mime = None
    db.session.commit()
    flash("Profile picture removed.", "success")
    return redirect(url_for("profile"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        try:
            current_user.bio = request.form.get("bio", "").strip()
            current_user.skills = request.form.get("skills", "").strip()
            db.session.commit()
            flash("Profile updated.", "success")
        except Exception as e:
            db.session.rollback()
            flash(f"Could not save: {e}", "error")
        return redirect(url_for("profile"))

    try:
        my_payment_requests = (
            PaymentRequest.query.filter_by(user_id=current_user.id)
            .order_by(PaymentRequest.created_at.desc()).all()
        )
    except Exception:
        my_payment_requests = []

    try:
        my_jobs = (JobPost.query.filter_by(user_id=current_user.id)
                   .order_by(JobPost.created_at.desc()).all())
    except Exception:
        my_jobs = []

    try:
        my_posts = (
            Post.query.filter_by(user_id=current_user.id, post_type="community")
            .order_by(Post.created_at.desc()).all()
        )
    except Exception:
        my_posts = []

    try:
        my_support = (
            SupportMessage.query
            .filter_by(user_id=current_user.id)
            .order_by(SupportMessage.created_at.asc()).all()
        )
    except Exception:
        my_support = []

    return render_template(
        "profile.html",
        my_payment_requests=my_payment_requests,
        my_jobs=my_jobs,
        my_posts=my_posts,
        my_support=my_support,
    )


# --- Payment requests (manual admin-approval flow) ---------------------------------
@app.route("/payments/request", methods=["POST"])
@login_required
def payment_request():
    tier_requested = request.form.get("tier_requested", "")
    method = request.form.get("method", "")
    payer_name = request.form.get("payer_name", "").strip()
    reference_note = request.form.get("reference_note", "").strip()

    if tier_requested not in TIERS or tier_requested == "free":
        flash("Please choose a valid paid tier.", "error")
        return redirect(url_for("pricing"))
    if method not in PAYMENT_METHOD_NAMES:
        flash("Please choose a valid payment method.", "error")
        return redirect(url_for("pricing"))
    if not payer_name:
        flash("Please add the name on the payment.", "error")
        return redirect(url_for("pricing"))

    db.session.add(PaymentRequest(
        user_id=current_user.id,
        tier_requested=tier_requested,
        method=method,
        payer_name=payer_name,
        reference_note=reference_note,
    ))
    db.session.commit()

    flash("Upgrade request submitted. An admin will verify and activate it shortly.", "success")
    return redirect(url_for("profile"))


# --- Job board (every logged-in member) ------------------------------------------
@app.route("/jobs", methods=["GET", "POST"])
@login_required
def jobs():
    if request.method == "POST":
        job_type = request.form.get("job_type", "")
        title = request.form.get("title", "").strip()
        category = request.form.get("category", "").strip()
        budget_text = request.form.get("budget_text", "").strip()
        description = request.form.get("description", "").strip()
        contact_info = request.form.get("contact_info", "").strip()

        if job_type not in ("hiring", "freelance") or not title or not description or not contact_info:
            flash("Please fill in the required job fields.", "error")
            return redirect(url_for("jobs"))

        db.session.add(JobPost(
            user_id=current_user.id, job_type=job_type, title=title,
            category=category, budget_text=budget_text,
            description=description, contact_info=contact_info,
        ))
        db.session.commit()
        flash("Job post published.", "success")
        return redirect(url_for("jobs"))

    filter_type = request.args.get("type", "all")
    query = JobPost.query
    if filter_type in ("hiring", "freelance"):
        query = query.filter_by(job_type=filter_type)
    job_list = query.order_by(JobPost.created_at.desc()).all()
    return render_template("jobs.html", filter_type=filter_type, job_list=job_list)


@app.route("/jobs/<int:job_id>/delete", methods=["POST"])
@login_required
def delete_job(job_id):
    job = JobPost.query.get_or_404(job_id)
    if job.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(job)
    db.session.commit()
    flash("Job post deleted.", "success")
    return redirect(url_for("jobs"))


# --- Chat (free room: everyone, premium room: standard+premium+admin) -------------
@app.route("/chat/<room>", methods=["GET", "POST"])
@login_required
def chat(room):
    if room not in ("free", "premium"):
        abort(404)

    if request.method == "POST":
        if room == "premium" and not current_user.can_use_premium_chat:
            abort(403)
        content = request.form.get("content", "").strip()
        if content:
            db.session.add(ChatMessage(user_id=current_user.id, room=room, content=content))
            db.session.commit()
        return redirect(url_for("chat", room=room))

    messages = ChatMessage.query.filter_by(room=room).order_by(ChatMessage.created_at.asc()).limit(150).all()
    return render_template("chat.html", room=room, messages=messages)


@app.route("/chat/<room>/send", methods=["POST"])
@login_required
def chat_send(room):
    if room not in ("free", "premium"):
        abort(404)
    if room == "premium" and not current_user.can_use_premium_chat:
        abort(403)
    if current_user.is_currently_banned:
        return jsonify({"error": "Your account is restricted and cannot send messages."}), 403

    content = request.form.get("content", "").strip()
    if not content:
        return jsonify({"error": "empty"}), 400

    msg = ChatMessage(user_id=current_user.id, room=room, content=content)
    db.session.add(msg)
    db.session.commit()
    return jsonify(serialize_chat_message(msg))


@app.route("/chat/<room>/poll")
@login_required
def chat_poll(room):
    if room not in ("free", "premium"):
        abort(404)
    if room == "premium" and not current_user.can_use_premium_chat:
        abort(403)

    after_id = request.args.get("after", 0, type=int)
    new_messages = (
        ChatMessage.query.filter_by(room=room)
        .filter(ChatMessage.id > after_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(50).all()
    )
    return jsonify([serialize_chat_message(m) for m in new_messages])


# --- Updates (admin / company broadcasts; public to view) -------------------------
@app.route("/updates", methods=["GET", "POST"])
def updates():
    if request.method == "POST":
        if not current_user.is_authenticated or not (current_user.is_admin or current_user.can_post_updates):
            abort(403)

        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        if not title or not content:
            flash("Please add a title and content.", "error")
            return redirect(url_for("updates"))

        try:
            image_data, image_mime = process_image_upload(request.files.get("image"))
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("updates"))

        db.session.add(Post(title=title, content=content, post_type="update", image_data=image_data, image_mime=image_mime, user_id=current_user.id))
        db.session.commit()
        flash("Update published.", "success")
        return redirect(url_for("updates"))

    all_updates = Post.query.filter_by(post_type="update").order_by(Post.created_at.desc()).all()
    return render_template("updates.html", all_updates=all_updates)


# --- Events (admin/company creates; everyone logged-in can view) ---------------
@app.route("/events", methods=["GET", "POST"])
@login_required
def events():
    if request.method == "POST":
        if not (current_user.is_admin or current_user.can_post_updates):
            abort(403)

        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        event_date_str = request.form.get("event_date", "").strip()
        location = request.form.get("location", "").strip()

        if not title or not description or not event_date_str:
            flash("Title, description and date are required.", "error")
            return redirect(url_for("events"))

        try:
            event_date = datetime.strptime(event_date_str, "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Invalid date format.", "error")
            return redirect(url_for("events"))

        try:
            image_data, image_mime = process_image_upload(request.files.get("image"))
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("events"))

        db.session.add(Event(
            title=title, description=description, event_date=event_date,
            location=location or None, image_data=image_data, image_mime=image_mime,
            user_id=current_user.id,
        ))
        db.session.commit()
        flash("Event published.", "success")
        return redirect(url_for("events"))

    now = datetime.utcnow()
    upcoming = Event.query.filter(Event.event_date >= now).order_by(Event.event_date.asc()).all()
    past = Event.query.filter(Event.event_date < now).order_by(Event.event_date.desc()).limit(10).all()
    return render_template("events.html", upcoming=upcoming, past=past, now=now)


@app.route("/events/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    if event.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    db.session.delete(event)
    db.session.commit()
    flash("Event deleted.", "success")
    return redirect(url_for("events"))


# --- Notifications ------------------------------------------------------------------
@app.route("/notifications")
@login_required
def notifications():
    all_notifs = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc()).limit(100).all()
    )
    for n in all_notifs:
        n.is_read = True
    db.session.commit()
    return render_template("notifications.html", all_notifs=all_notifs)


# --- Customer Service -------------------------------------------------------------------
@app.route("/support")
@login_required
def support():
    """Member's support inbox - conversation with admins."""
    messages = (SupportMessage.query
                .filter_by(user_id=current_user.id)
                .order_by(SupportMessage.created_at.asc())
                .all())
    # Mark admin replies as read
    for m in messages:
        if m.is_from_admin and not m.is_read:
            m.is_read = True
    db.session.commit()
    return render_template("support.html", messages=messages)


@app.route("/support/send", methods=["POST"])
@login_required
def support_send():
    content = request.form.get("content", "").strip()
    if not content:
        return redirect(url_for("support"))
    msg = SupportMessage(
        user_id=current_user.id,
        sender_id=current_user.id,
        content=content,
        is_from_admin=False,
    )
    db.session.add(msg)
    db.session.commit()
    admins = User.query.filter(User.role.in_(["admin", "ceo"])).all()
    for admin in admins:
        notify(
            admin.id,
            f"Support message from {current_user.name}: {content[:60]}{'…' if len(content) > 60 else ''}",
            url_for("admin_support_thread", user_id=current_user.id),
        )
    return redirect(url_for("support"))


@app.route("/support/send-ajax", methods=["POST"])
@login_required
def support_send_ajax():
    """AJAX version — returns JSON for real-time display without page reload."""
    content = request.form.get("content", "").strip()
    if not content:
        return jsonify({"error": "empty"}), 400
    msg = SupportMessage(
        user_id=current_user.id,
        sender_id=current_user.id,
        content=content,
        is_from_admin=False,
    )
    db.session.add(msg)
    db.session.commit()
    admins = User.query.filter(User.role.in_(["admin", "ceo"])).all()
    for admin in admins:
        notify(
            admin.id,
            f"Support: {current_user.name}: {content[:60]}{'…' if len(content) > 60 else ''}",
            url_for("admin_support_thread", user_id=current_user.id),
        )
    return jsonify({
        "id": msg.id,
        "content": msg.content,
        "is_from_admin": False,
        "sender_name": current_user.name,
        "time": msg.created_at.strftime("%b %d · %I:%M %p"),
    })


@app.route("/support/poll")
@login_required
def support_poll():
    after_id = request.args.get("after", 0, type=int)
    new_msgs = (SupportMessage.query
                .filter_by(user_id=current_user.id)
                .filter(SupportMessage.id > after_id)
                .order_by(SupportMessage.created_at.asc())
                .all())
    for m in new_msgs:
        if m.is_from_admin:
            m.is_read = True
    if new_msgs:
        db.session.commit()
    return jsonify([{
        "id": m.id,
        "content": m.content,
        "is_from_admin": m.is_from_admin,
        "sender_name": m.sender.name,
        "time": m.created_at.strftime("%b %d · %I:%M %p"),
    } for m in new_msgs])


# --- Admin support inbox ----------------------------------------------------------------
@app.route("/admin/support")
@login_required
@admin_required
def admin_support():
    """Admin sees a list of all users who sent support messages."""
    # Get unique user IDs that have support messages, latest first
    from sqlalchemy import func
    rows = (db.session.query(
                SupportMessage.user_id,
                func.max(SupportMessage.created_at).label("last_msg"),
                func.sum(db.case((db.and_(SupportMessage.is_from_admin == False, SupportMessage.is_read == False), 1), else_=0)).label("unread")
            )
            .group_by(SupportMessage.user_id)
            .order_by(db.text("last_msg DESC"))
            .all())
    threads = []
    for row in rows:
        user = User.query.get(row.user_id)
        if user:
            threads.append({
                "user": user,
                "last_msg": row.last_msg,
                "unread": int(row.unread),
            })
    return render_template("admin_support.html", threads=threads)


@app.route("/admin/support/<int:user_id>", methods=["GET", "POST"])
@login_required
@admin_required
def admin_support_thread(user_id):
    member = User.query.get_or_404(user_id)
    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if content:
            msg = SupportMessage(
                user_id=user_id,
                sender_id=current_user.id,
                content=content,
                is_from_admin=True,
            )
            db.session.add(msg)
            db.session.commit()
            notify(
                user_id,
                f"Support reply from {current_user.name}: {content[:60]}{'…' if len(content) > 60 else ''}",
                url_for("support"),
            )
        return redirect(url_for("admin_support_thread", user_id=user_id))

    messages = (SupportMessage.query
                .filter_by(user_id=user_id)
                .order_by(SupportMessage.created_at.asc())
                .all())
    # Mark member messages as read for admin
    for m in messages:
        if not m.is_from_admin:
            m.is_read = True
    db.session.commit()
    return render_template("admin_support_thread.html", member=member, messages=messages)


# --- Admin -----------------------------------------------------------------------------
@app.route("/admin")
@login_required
@admin_required
def admin_panel():
    user_count = User.query.count()
    tier_counts = {t: User.query.filter_by(tier=t).count() for t in TIER_ORDER}
    pending = PaymentRequest.query.filter_by(status="pending").order_by(PaymentRequest.created_at.asc()).all()
    job_count = JobPost.query.count()
    recent_reviewed = (
        PaymentRequest.query.filter(PaymentRequest.status.in_(["approved", "rejected"]))
        .order_by(PaymentRequest.reviewed_at.desc()).limit(10).all()
    )
    all_users = User.query.order_by(User.created_at.desc()).all()

    try:
        unread_support = SupportMessage.query.filter_by(is_from_admin=False, is_read=False).count()
    except Exception:
        unread_support = 0

    return render_template(
        "admin.html",
        user_count=user_count,
        tier_counts=tier_counts,
        pending=pending,
        job_count=job_count,
        recent_reviewed=recent_reviewed,
        all_users=all_users,
        pending_reports=PostReport.query.filter_by(status="pending").count(),
        unread_support=unread_support,
    )


@app.route("/admin/payments/<int:req_id>/<action>", methods=["POST"])
@login_required
@admin_required
def admin_review_payment(req_id, action):
    if action not in ("approve", "reject"):
        abort(400)

    payment = PaymentRequest.query.get_or_404(req_id)
    payment.status = "approved" if action == "approve" else "rejected"
    payment.reviewed_at = datetime.utcnow()
    payment.reviewed_by = current_user.id

    if action == "approve":
        member = User.query.get(payment.user_id)
        member.tier = payment.tier_requested
        if payment.tier_requested in ("standard", "premium"):
            member.tier_expires_at = datetime.utcnow() + timedelta(days=30)
        else:
            member.tier_expires_at = None
        notify(member.id, f"Payment approved — you're now a {member.tier_label}.", url_for("profile"))
        flash(f"Approved and upgraded {member.name}.", "success")
    else:
        notify(payment.user_id, "Your upgrade request was rejected. Please check your details and try again.", url_for("pricing"))
        flash("Payment request rejected.", "success")

    db.session.commit()
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/toggle-role", methods=["POST"])
@login_required
@admin_required
def admin_toggle_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't change your own admin status.", "error")
        return redirect(url_for("admin_panel"))
    if user.is_ceo:
        flash("The CEO role can't be changed from here.", "error")
        return redirect(url_for("admin_panel"))

    user.role = "member" if user.is_admin else "admin"
    if user.role == "admin":
        user.can_post_updates = True
    db.session.commit()
    flash(f"Updated {user.name}'s role to {user.role}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/position", methods=["POST"])
@login_required
@admin_required
def admin_set_position(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_ceo:
        flash("The CEO's title can't be changed from here.", "error")
        return redirect(url_for("admin_panel"))

    position = request.form.get("position", "").strip()
    user.position = position or None
    db.session.commit()
    flash(f"Updated {user.name}'s position.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/ban", methods=["POST"])
@login_required
@admin_required
def admin_ban(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_ceo or user.id == current_user.id:
        flash("You can't ban this account.", "error")
        return redirect(url_for("admin_panel"))

    duration = request.form.get("duration", "permanent")
    user.is_banned = True
    if duration == "permanent":
        user.ban_expires_at = None
    else:
        days = int(duration)
        user.ban_expires_at = datetime.utcnow() + timedelta(days=days)

    reason = request.form.get("reason", "").strip() or "Community guidelines violation"
    db.session.commit()
    notify(user_id, f"Your account has been restricted: {reason}.", url_for("index"))
    flash(f"{'Permanently banned' if duration == 'permanent' else f'Banned {duration} day(s)'}: {user.name}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/unban", methods=["POST"])
@login_required
@admin_required
def admin_unban(user_id):
    user = User.query.get_or_404(user_id)
    user.is_banned = False
    user.ban_expires_at = None
    db.session.commit()
    notify(user_id, "Your account restriction has been lifted. Welcome back.", url_for("dashboard"))
    flash(f"Lifted ban for {user.name}.", "success")
    return redirect(url_for("admin_panel"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@admin_required
def admin_reset_password(user_id):
    import secrets, string
    user = User.query.get_or_404(user_id)
    if user.is_ceo and not current_user.is_ceo:
        flash("Only the CEO can reset the CEO's password.", "error")
        return redirect(url_for("admin_panel"))

    alphabet = string.ascii_letters + string.digits
    temp = ''.join(secrets.choice(alphabet) for _ in range(10))

    user.set_password(temp)
    db.session.commit()

    # Show temp password directly in flash — copy and send to member manually
    flash(
        f"Password reset for {user.name}. "
        f"Temporary password: {temp} — Copy this and send it to them directly. "
        f"It will not be shown again.",
        "success"
    )
    return redirect(url_for("admin_panel"))


@app.route("/admin/reports")
@login_required
@admin_required
def admin_reports():
    pending = PostReport.query.filter_by(status="pending").order_by(PostReport.created_at.asc()).all()
    reviewed = PostReport.query.filter(PostReport.status != "pending").order_by(PostReport.reviewed_at.desc()).limit(20).all()
    # Attach the related post object to each report for template access
    for r in pending + reviewed:
        r.post = Post.query.get(r.post_id)
    return render_template("admin_reports.html", pending=pending, reviewed=reviewed)


@app.route("/admin/reports/<int:report_id>/<action>", methods=["POST"])
@login_required
@admin_required
def admin_review_report(report_id, action):
    if action not in ("dismiss", "delete_post", "ban_user"):
        abort(400)
    report = PostReport.query.get_or_404(report_id)
    report.reviewed_at = datetime.utcnow()

    if action == "dismiss":
        report.status = "dismissed"
        db.session.commit()
        flash("Report dismissed.", "success")

    elif action == "delete_post":
        report.status = "reviewed"
        post = Post.query.get(report.post_id)
        if post:
            db.session.delete(post)
        db.session.commit()
        flash("Report reviewed — post deleted.", "success")

    elif action == "ban_user":
        report.status = "reviewed"
        post = Post.query.get(report.post_id)
        days = request.form.get("days", "7")
        if post:
            user = User.query.get(post.user_id)
            if user and not user.is_ceo:
                user.is_banned = True
                user.ban_expires_at = datetime.utcnow() + timedelta(days=int(days)) if days != "permanent" else None
                notify(user.id, f"Your account has been restricted following a content report.", url_for("index"))
            db.session.delete(post)
        db.session.commit()
        flash("User banned and post removed.", "success")

    return redirect(url_for("admin_reports"))
@app.cli.command("init-db")
def init_db():
    """Run with: flask --app app init-db"""
    db.create_all()
    print("Database tables created.")


@app.cli.command("migrate-add-position")
def migrate_add_position():
    """Run with: flask --app app migrate-add-position
    Safe to run on an existing database — adds the 'position' column to the
    user table if it doesn't already exist. Does not affect existing data."""
    from sqlalchemy import text
    with db.engine.connect() as conn:
        conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS position VARCHAR(80);'))
        conn.commit()
    print("Migration complete: 'position' column is ready.")


@app.cli.command("migrate")
def migrate():
    """Run with: flask --app app migrate
    Safe to run anytime on an existing PostgreSQL database — adds every
    column introduced after the first deploy, without touching existing data."""
    from sqlalchemy import text
    statements = [
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS position VARCHAR(80);',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS tier_expires_at TIMESTAMP;',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS avatar_data TEXT;',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS avatar_mime VARCHAR(40);',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS avatar_updated_at TIMESTAMP;',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE;',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS ban_expires_at TIMESTAMP;',
        'ALTER TABLE post ADD COLUMN IF NOT EXISTS image_data TEXT;',
        'ALTER TABLE post ADD COLUMN IF NOT EXISTS image_mime VARCHAR(40);',
        '''CREATE TABLE IF NOT EXISTS follow (
            id SERIAL PRIMARY KEY,
            follower_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            following_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(follower_id, following_id)
        );''',
        '''CREATE TABLE IF NOT EXISTS event (
            id SERIAL PRIMARY KEY,
            title VARCHAR(140) NOT NULL,
            description TEXT NOT NULL,
            event_date TIMESTAMP NOT NULL,
            location VARCHAR(200),
            image_data TEXT,
            image_mime VARCHAR(40),
            created_at TIMESTAMP DEFAULT NOW(),
            user_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE
        );''',
        '''CREATE TABLE IF NOT EXISTS post_like (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            post_id INTEGER REFERENCES post(id) ON DELETE CASCADE,
            UNIQUE(user_id, post_id)
        );''',
        '''CREATE TABLE IF NOT EXISTS comment (
            id SERIAL PRIMARY KEY,
            post_id INTEGER REFERENCES post(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            parent_id INTEGER REFERENCES comment(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );''',
        '''CREATE TABLE IF NOT EXISTS comment_like (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            comment_id INTEGER REFERENCES comment(id) ON DELETE CASCADE,
            UNIQUE(user_id, comment_id)
        );''',
        '''CREATE TABLE IF NOT EXISTS post_report (
            id SERIAL PRIMARY KEY,
            post_id INTEGER REFERENCES post(id) ON DELETE CASCADE,
            reporter_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            reason VARCHAR(280) NOT NULL,
            status VARCHAR(20) DEFAULT \'pending\',
            created_at TIMESTAMP DEFAULT NOW(),
            reviewed_at TIMESTAMP
        );''',
    ]
    with db.engine.connect() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.commit()
    print("Migration complete: all columns and tables are up to date.")


@app.cli.command("make-admin")
@click.argument("email")
def make_admin(email):
    """Run with: flask --app app make-admin you@example.com"""
    user = User.query.filter_by(email=email.strip().lower()).first()
    if not user:
        print(f"No user found with email {email}")
        return
    user.role = "admin"
    user.can_post_updates = True
    db.session.commit()
    print(f"{user.email} is now an admin.")


@app.cli.command("make-ceo")
@click.argument("email")
def make_ceo(email):
    """Run with: flask --app app make-ceo you@example.com"""
    user = User.query.filter_by(email=email.strip().lower()).first()
    if not user:
        print(f"No user found with email {email}")
        return
    user.role = "ceo"
    user.can_post_updates = True
    db.session.commit()
    print(f"{user.email} is now the CEO.")


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)


_migrated = False   # guard so migration only runs once per worker process

@app.before_request
def run_auto_migrate_once():
    global _migrated
    if _migrated:
        return
    _migrated = True
    _auto_migrate()


def _auto_migrate():
    """
    Called before the first request in each worker.
    Safely creates any missing tables / columns in PostgreSQL.
    Safe to run on every startup — uses IF NOT EXISTS throughout.
    """
    from sqlalchemy import text
    statements = [
        # ── User table new columns ────────────────────────────────────
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS position VARCHAR(80);',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS tier_expires_at TIMESTAMP;',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS avatar_data TEXT;',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS avatar_mime VARCHAR(40);',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS avatar_updated_at TIMESTAMP;',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_banned BOOLEAN DEFAULT FALSE;',
        'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS ban_expires_at TIMESTAMP;',
        # ── Post table new columns ────────────────────────────────────
        'ALTER TABLE post ADD COLUMN IF NOT EXISTS image_data TEXT;',
        'ALTER TABLE post ADD COLUMN IF NOT EXISTS image_mime VARCHAR(40);',
        # ── New tables ───────────────────────────────────────────────
        '''CREATE TABLE IF NOT EXISTS follow (
            id SERIAL PRIMARY KEY,
            follower_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            following_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(follower_id, following_id)
        );''',
        '''CREATE TABLE IF NOT EXISTS post_like (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            post_id INTEGER REFERENCES post(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, post_id)
        );''',
        '''CREATE TABLE IF NOT EXISTS comment (
            id SERIAL PRIMARY KEY,
            post_id INTEGER REFERENCES post(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            parent_id INTEGER REFERENCES comment(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );''',
        '''CREATE TABLE IF NOT EXISTS comment_like (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            comment_id INTEGER REFERENCES comment(id) ON DELETE CASCADE,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, comment_id)
        );''',
        '''CREATE TABLE IF NOT EXISTS post_report (
            id SERIAL PRIMARY KEY,
            post_id INTEGER REFERENCES post(id) ON DELETE CASCADE,
            reporter_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            reason VARCHAR(200) NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT NOW(),
            reviewed_at TIMESTAMP
        );''',
        '''CREATE TABLE IF NOT EXISTS event (
            id SERIAL PRIMARY KEY,
            title VARCHAR(140) NOT NULL,
            description TEXT NOT NULL,
            event_date TIMESTAMP NOT NULL,
            location VARCHAR(200),
            image_data TEXT,
            image_mime VARCHAR(40),
            created_at TIMESTAMP DEFAULT NOW(),
            user_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE
        );''',
        '''CREATE TABLE IF NOT EXISTS support_message (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            sender_id INTEGER REFERENCES "user"(id) ON DELETE CASCADE,
            content TEXT NOT NULL,
            is_from_admin BOOLEAN DEFAULT FALSE,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );''',
    ]
    try:
        with db.engine.connect() as conn:
            # First make sure base tables exist (SQLAlchemy ORM definition)
            db.create_all()
            # Then add any missing columns / new tables
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                except Exception:
                    pass   # column already exists or SQLite — skip silently
            conn.commit()
        print("[auto_migrate] OK")
    except Exception as e:
        print(f"[auto_migrate] Warning: {e}")
