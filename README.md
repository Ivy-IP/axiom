# Axiom — Membership Platform

A Flask community platform with tiered membership, manual payment verification,
a job board, two chat rooms, and an admin control panel.

## What's included

- **Auth** — sign up / log in / log out, passwords hashed
- **4 tiers** — Free, First (one-time), Standard, Premium — prices in MMK
- **Staff roles** — CEO (top role, always shows a "CEO" badge), Admin, and a
  free-text **custom position** (e.g. "Marketing Manager") that admins can
  assign to any member — shown in the same badge spot as the tier pill
- **Manual payment approval** — member submits payment method + reference,
  admin approves/rejects from the Admin panel, tier upgrades automatically
- **Job board** — any logged-in member can post "hiring" or "available for work"
- **Two chat rooms** — Free (everyone) and Premium (Standard + Premium + admin)
- **Community feed** — Admin and Premium members can post text/photos (by URL);
  everyone can read; the post's author (or any admin) can delete it
- **Updates page** — Admin / company-flagged accounts can publish official
  announcements; visible to everyone; deletable the same way as feed posts
- **Notifications** — bell icon with dropdown + a full notifications page
- **Admin panel** — pending payment queue, member directory, role management,
  and per-member position assignment
- **Typeface** — Helvetica (system font, no external font loading)

## Project structure

```
axiom/
├── app.py                  # Flask app: models, routes, access control
├── requirements.txt
├── Procfile
├── .env.example
├── templates/
│   ├── base.html            # Nav, notification bell, footer
│   ├── index.html           # Home
│   ├── login.html / signup.html
│   ├── dashboard.html       # Member feed + composer (Admin/Premium only)
│   ├── profile.html         # Edit bio/skills, see your requests/jobs/posts
│   ├── pricing.html         # Tiers + upgrade request form
│   ├── jobs.html            # Job board (list + post form)
│   ├── chat.html            # Free / Premium chat rooms
│   ├── updates.html         # Official announcements
│   ├── notifications.html
│   ├── admin.html           # Admin control panel
│   └── error.html           # 403 / 404
└── static/
    ├── css/style.css
    ├── js/main.js
    └── img/mark.svg          # Axiom star mark
```

---

## 1. Run it locally

```bash
cd axiom
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set SECRET_KEY to any random string

export $(cat .env | grep -v '^#' | xargs)   # Windows: use python-dotenv instead
flask --app app init-db
```

Create your own account first by running the app and using the Sign Up page,
**then** promote yourself. If you're the CEO/founder, use `make-ceo` so your
badge always shows **"CEO"** instead of your tier. Otherwise use `make-admin`
for staff/moderator accounts (badge shows **"Admin"**):

```bash
flask --app app make-ceo you@example.com
# or, for a regular staff admin:
flask --app app make-admin you@example.com

python app.py
```

Visit **http://127.0.0.1:5000**. Log out and back in after promoting yourself
— you'll see the **Admin** link in the nav and your new badge.

For anyone else who joins with a specific company role (e.g. "Marketing
Manager"), go to **Admin panel → Members** and type their title into the
**Position** field next to their name, then **Save**. That title replaces
their tier badge everywhere it would normally show "Free Member" etc.
(CEO badge always takes priority and can't be overwritten this way.)

---

## 2. Deploy (same flow as your Railway setup)

1. Push this project to GitHub (a new repo, or replace the old Basecamp repo's contents)
2. In Railway: your existing `basecamp` service → connect it to the new repo,
   or create a fresh project the same way you did before (GitHub Repository → select repo)
3. Add a PostgreSQL database the same way (`+ New` → Database → PostgreSQL)
4. In the web service's **Variables** tab, set:
   - `SECRET_KEY` — any random string
   - `DATABASE_URL` — reference it as `${{Postgres.DATABASE_URL}}` (use your
     actual database service name), or paste the real `postgresql://...`
     connection string directly if the reference doesn't resolve
5. Deploy. Once it's **Active**, open the service's **Console** tab and run:
   ```
   flask --app app init-db
   ```
   If you're updating an **already-deployed** database (one that already has
   users in it) rather than starting fresh, also run:
   ```
   flask --app app migrate-add-position
   ```
   This adds the new `position` column without deleting any existing data.
6. Sign up for your own account on the live site, then in the Console run:
   ```
   flask --app app make-ceo you@example.com
   ```
   (use `make-admin` instead if you want a regular staff account rather than the CEO badge)
   ```
7. Refresh the site and log in — you now have the Admin panel.

---

## Notes on what's simplified for now

- **Payment proof**: members submit a payment method, payer name, and a
  reference note (transaction ID / phone number) — no screenshot upload, to
  keep things simple and avoid Railway's ephemeral file storage. Add file
  uploads later via a service like Cloudinary or S3 if you want photo proof.
- **Post images**: the community feed and updates use an image **URL** field
  rather than file upload, for the same reason. Members would paste a link
  (e.g. from a photo hosting site) — uploading directly can be added later.
- **"First" tier**: there's no slot-limiting logic yet (e.g. "only the first
  50 people"). It's just a one-time-price tier for now — ask if you want a
  hard cap enforced.
- **Chat**: messages are stored and shown on page load/refresh, not real-time
  (no auto-refresh / websockets yet). Refreshing the page shows new messages.
