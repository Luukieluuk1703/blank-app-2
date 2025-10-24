# app.py
import streamlit as st
import sqlite3, datetime as dt
from contextlib import closing
import pytz
import streamlit_authenticator as stauth

# ---------- CONFIG ----------
APP_TZ = pytz.timezone("Europe/Amsterdam")  # toon tijden lokaal
DB = "planner.db"

# Demo users: vervang door veilige opslag
# Hash met: stauth.Hasher(["wachtwoord"]).generate()
CREDENTIALS = {
    "usernames": {
        "alice": {"name": "Alice Janssen", "password": "$2b$12$K....", "role": "admin", "class": "2A"},
        "bob":   {"name": "Bob Peters",   "password": "$2b$12$L....", "role": "student", "class": "2A"},
    }
}

# ---------- DB ----------
def get_conn():
    return sqlite3.connect(DB, check_same_thread=False)

def migrate():
    with closing(get_conn()) as conn, conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, username TEXT UNIQUE, name TEXT, role TEXT, class TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS assignments(
            id INTEGER PRIMARY KEY, subject TEXT, title TEXT, description TEXT,
            due_at_utc TEXT, created_by TEXT, created_at_utc TEXT, is_published INTEGER
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS completions(
            id INTEGER PRIMARY KEY, user_id INTEGER, assignment_id INTEGER,
            status TEXT, completed_at_utc TEXT,
            UNIQUE(user_id, assignment_id)
        )""")
        # seed users from CREDENTIALS if missing
        for uname, data in CREDENTIALS["usernames"].items():
            cur.execute("INSERT OR IGNORE INTO users(username,name,role,class) VALUES(?,?,?,?)",
                        (uname, data["name"], data["role"], data["class"]))

def utcnow():
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc).isoformat()

def to_local(iso_utc: str):
    if not iso_utc: return ""
    return dt.datetime.fromisoformat(iso_utc).astimezone(APP_TZ).strftime("%Y-%m-%d %H:%M")

# ---------- AUTH ----------
authenticator = stauth.Authenticate(
    {"usernames": {u: {"email": f"{u}@example.com", "name": CREDENTIALS["usernames"][u]["name"],
                       "password": CREDENTIALS["usernames"][u]["password"]}
                   for u in CREDENTIALS["usernames"]}},
    "hwplanner_cookie", "hwplanner_key", cookie_expiry_days=7
)

def ensure_logged_in():
    name, auth_status, username = authenticator.login("Login", "main")
    if not auth_status:
        st.stop()
    return username

# ---------- UI ----------
def admin_page(username):
    st.header("📌 Admin — Huiswerk beheren")
    with closing(get_conn()) as conn, conn, conn.cursor() as cur:
        st.subheader("Nieuwe opdracht")
        with st.form("new_assignment"):
            subject = st.text_input("Vak*", placeholder="Wiskunde")
            title = st.text_input("Titel*", placeholder="Paragraaf 3.2 – Machten")
            description = st.text_area("Omschrijving / link")
            due_local = st.datetime_input("Deadline", value=dt.datetime.now(APP_TZ) + dt.timedelta(days=3))
            publish = st.checkbox("Publiceren", value=True)
            submitted = st.form_submit_button("Opslaan")
            if submitted:
                if not subject or not title:
                    st.error("Vul minimaal vak en titel in.")
                else:
                    due_at_utc = due_local.astimezone(dt.timezone.utc).isoformat()
                    cur.execute("""INSERT INTO assignments(subject,title,description,due_at_utc,created_by,created_at_utc,is_published)
                                   VALUES(?,?,?,?,?,?,?)""",
                                (subject, title, description, due_at_utc, username, utcnow(), int(publish)))
                    st.success("Opdracht opgeslagen.")

        st.divider()
        st.subheader("Openstaande opdrachten")
        cur.execute("""SELECT id, subject, title, due_at_utc, is_published FROM assignments
                       ORDER BY datetime(due_at_utc) ASC""")
        rows = cur.fetchall()
        for (aid, subj, title, due, pub) in rows:
            cols = st.columns([3,2,2,2,2])
            cols[0].markdown(f"**{title}** — _{subj}_")
            cols[1].markdown(f"🕒 {to_local(due)}")
            cols[2].markdown("✅ Gepubliceerd" if pub else "⏳ Concept")
            if cols[3].button("Wissel publicatie", key=f"pub{aid}"):
                cur.execute("UPDATE assignments SET is_published = 1 - is_published WHERE id = ?", (aid,))
                st.experimental_rerun()
            if cols[4].button("Verwijderen", key=f"del{aid}"):
                cur.execute("DELETE FROM assignments WHERE id = ?", (aid,))
                cur.execute("DELETE FROM completions WHERE assignment_id = ?", (aid,))
                st.experimental_rerun()

        st.divider()
        st.subheader("Voortgang per student")
        cur.execute("""
            SELECT a.id, a.subject, a.title, a.due_at_utc, u.name, u.username,
                   COALESCE(c.status,'pending') as status
            FROM assignments a
            JOIN users u
            LEFT JOIN completions c ON c.assignment_id=a.id AND c.user_id=u.id
            WHERE a.is_published=1
            ORDER BY datetime(a.due_at_utc) ASC, u.name ASC
        """)
        # Voor echte dashboards zou je hier pivotten en plotten.

def student_page(username):
    st.header("🎒 Mijn huiswerk")
    with closing(get_conn()) as conn, conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE username=?", (username,))
        user_id = cur.fetchone()[0]

        # Filters
        subject_filter = st.text_input("Filter op vak...")
        status_filter = st.selectbox("Status", ["Alles","Open","Afgerond"], index=0)

        # Lijst
        q = """SELECT a.id, a.subject, a.title, a.description, a.due_at_utc,
                      COALESCE(c.status,'pending') as status
               FROM assignments a
               LEFT JOIN completions c ON c.assignment_id=a.id AND c.user_id=?
               WHERE a.is_published=1
            """
        args = [user_id]
        if subject_filter:
            q += " AND a.subject LIKE ?"
            args.append(f"%{subject_filter}%")
        q += " ORDER BY datetime(a.due_at_utc) ASC"
        cur.execute(q, args)
        rows = cur.fetchall()

        now_local = dt.datetime.now(APP_TZ)
        for (aid, subj, title, desc, due, status) in rows:
            is_done = (status == "completed")
            due_local = dt.datetime.fromisoformat(due).astimezone(APP_TZ)
            overdue = (due_local < now_local) and not is_done

            with st.container(border=True):
                st.markdown(f"**{title}** — _{subj}_")
                st.caption(f"Deadline: {to_local(due)}")
                if desc:
                    st.write(desc)
                cols = st.columns([1,1,3])
                toggled = cols[0].toggle("Afgerond", value=is_done, key=f"done{aid}")
                if toggled != is_done:
                    new_status = "completed" if toggled else "uncompleted"
                    cur.execute("INSERT OR IGNORE INTO completions(user_id, assignment_id, status, completed_at_utc) VALUES(?,?,?,?)",
                                (user_id, aid, new_status, utcnow()))
                    cur.execute("UPDATE completions SET status=?, completed_at_utc=? WHERE user_id=? AND assignment_id=?",
                                (new_status, utcnow(), user_id, aid))
                    st.experimental_rerun()
                if overdue:
                    cols[1].error("Te laat")
                else:
                    cols[1].success("Op schema") if is_done else cols[1].warning("Open")

def main():
    st.set_page_config(page_title="Homework Planner", page_icon="✅", layout="centered")
    migrate()
    username = ensure_logged_in()

    # Rol bepalen
    role = CREDENTIALS["usernames"].get(username, {}).get("role", "student")
    with st.sidebar:
        st.write(f"Ingelogd als **{username}** ({role})")
        authenticator.logout("Uitloggen", "sidebar")

    if role == "admin":
        admin_page(username)
    else:
        student_page(username)

if __name__ == "__main__":
    main()
