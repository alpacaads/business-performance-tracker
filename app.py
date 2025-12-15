import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st


# -----------------------------
# App config
# -----------------------------
st.set_page_config(page_title="Business Performance Tracker", page_icon="📈", layout="wide")
DB_PATH = "business_tracker.db"


# -----------------------------
# DB helpers
# -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('Active','Inactive','Prospect')),
        notes TEXT,
        created_at TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS services (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id INTEGER NOT NULL,
        service_name TEXT NOT NULL,
        monthly_cost REAL NOT NULL CHECK(monthly_cost >= 0),
        is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
        created_at TEXT NOT NULL,
        FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
    );
    """)

    conn.commit()
    conn.close()


def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def exec_sql(sql: str, params: tuple = ()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    conn.close()


# -----------------------------
# Domain logic
# -----------------------------
def upsert_client(client_id: Optional[int], name: str, status: str, notes: str):
    now = datetime.utcnow().isoformat()
    if client_id is None:
        exec_sql(
            "INSERT INTO clients (name, status, notes, created_at) VALUES (?,?,?,?)",
            (name.strip(), status, notes.strip(), now),
        )
    else:
        exec_sql(
            "UPDATE clients SET name=?, status=?, notes=? WHERE id=?",
            (name.strip(), status, notes.strip(), client_id),
        )


def delete_client(client_id: int):
    exec_sql("DELETE FROM clients WHERE id=?", (client_id,))


def add_service(client_id: int, service_name: str, monthly_cost: float, is_active: bool):
    now = datetime.utcnow().isoformat()
    exec_sql(
        "INSERT INTO services (client_id, service_name, monthly_cost, is_active, created_at) VALUES (?,?,?,?,?)",
        (client_id, service_name.strip(), float(monthly_cost), 1 if is_active else 0, now),
    )


def update_services_bulk(edited: pd.DataFrame):
    # expects columns: id, service_name, monthly_cost, is_active
    conn = get_conn()
    cur = conn.cursor()
    for _, r in edited.iterrows():
        cur.execute(
            "UPDATE services SET service_name=?, monthly_cost=?, is_active=? WHERE id=?",
            (str(r["service_name"]).strip(), float(r["monthly_cost"]), 1 if bool(r["is_active"]) else 0, int(r["id"])),
        )
    conn.commit()
    conn.close()


def delete_service(service_id: int):
    exec_sql("DELETE FROM services WHERE id=?", (service_id,))


def client_monthly_revenue_breakdown() -> pd.DataFrame:
    # Revenue is sum of ACTIVE services only
    df = query_df("""
        SELECT
            c.id AS client_id,
            c.name,
            c.status,
            COALESCE(SUM(CASE WHEN s.is_active = 1 THEN s.monthly_cost ELSE 0 END), 0) AS monthly_revenue
        FROM clients c
        LEFT JOIN services s ON s.client_id = c.id
        GROUP BY c.id, c.name, c.status
        ORDER BY c.status, c.name;
    """)
    return df


def totals_for_dashboard():
    df = client_monthly_revenue_breakdown()

    active_mrr = float(df.loc[df["status"] == "Active", "monthly_revenue"].sum())
    prospect_mrr = float(df.loc[df["status"] == "Prospect", "monthly_revenue"].sum())
    inactive_mrr = float(df.loc[df["status"] == "Inactive", "monthly_revenue"].sum())

    total_potential = active_mrr + prospect_mrr
    return active_mrr, prospect_mrr, total_potential, inactive_mrr


# -----------------------------
# UI
# -----------------------------
init_db()

st.sidebar.title("📈 Performance Tracker")
page = st.sidebar.radio("Go to", ["Dashboard", "Clients"], index=0)

# Common data
clients_df = query_df("SELECT id, name, status, notes, created_at FROM clients ORDER BY status, name;")

if page == "Dashboard":
    st.title("Dashboard")

    active_mrr, prospect_mrr, total_potential, inactive_mrr = totals_for_dashboard()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active Clients (MRR)", f"${active_mrr:,.2f}")
    c2.metric("Prospects / Not Started (MRR)", f"${prospect_mrr:,.2f}")
    c3.metric("Total Potential (Active + Prospects)", f"${total_potential:,.2f}")
    c4.metric("Inactive (MRR not counted)", f"${inactive_mrr:,.2f}")

    st.divider()

    st.subheader("Revenue by Client (Monthly)")
    breakdown = client_monthly_revenue_breakdown()
    st.dataframe(
        breakdown,
        use_container_width=True,
        hide_index=True,
        column_config={
            "monthly_revenue": st.column_config.NumberColumn("monthly_revenue", format="$%.2f"),
        },
    )

    st.caption("Note: Monthly revenue is the sum of *active services* for each client.")

elif page == "Clients":
    st.title("Clients")

    left, right = st.columns([1, 2], gap="large")

    with left:
        st.subheader("Add / Edit Client")

        # Pick existing client (optional)
        client_options = ["➕ New client"] + (
            clients_df["name"].tolist() if not clients_df.empty else []
        )
        selected = st.selectbox("Select", client_options)

        selected_id = None
        selected_row = None
        if selected != "➕ New client" and not clients_df.empty:
            selected_row = clients_df.loc[clients_df["name"] == selected].iloc[0]
            selected_id = int(selected_row["id"])

        name_default = "" if selected_id is None else str(selected_row["name"])
        status_default = "Prospect" if selected_id is None else str(selected_row["status"])
        notes_default = "" if selected_id is None else (str(selected_row["notes"]) if selected_row["notes"] is not None else "")

        with st.form("client_form", clear_on_submit=(selected_id is None)):
            name = st.text_input("Client name", value=name_default)
            status = st.selectbox("Status", ["Active", "Inactive", "Prospect"], index=["Active","Inactive","Prospect"].index(status_default))
            notes = st.text_area("Notes (optional)", value=notes_default, height=100)
            submitted = st.form_submit_button("Save client")

        if submitted:
            if not name.strip():
                st.error("Client name can’t be blank.")
            else:
                upsert_client(selected_id, name, status, notes)
                st.success("Saved.")
                st.rerun()

        if selected_id is not None:
            st.divider()
            st.subheader("Danger zone")
            if st.button("Delete client (and all services)", type="secondary"):
                delete_client(selected_id)
                st.warning("Client deleted.")
                st.rerun()

    with right:
        st.subheader("Services & Monthly Costs")

        if clients_df.empty:
            st.info("Add your first client on the left.")
        else:
            # Choose which client to manage services for
            manage_name = st.selectbox("Manage services for", clients_df["name"].tolist(), key="manage_services_client")
            manage_id = int(clients_df.loc[clients_df["name"] == manage_name, "id"].iloc[0])

            services_df = query_df("""
                SELECT id, service_name, monthly_cost, CASE WHEN is_active=1 THEN 1 ELSE 0 END AS is_active
                FROM services
                WHERE client_id=?
                ORDER BY is_active DESC, service_name;
            """, (manage_id,))

            st.markdown("**Add a service**")
            with st.form("add_service_form", clear_on_submit=True):
                sname = st.text_input("Service name", placeholder="e.g., Meta Ads Management")
                scost = st.number_input("Monthly cost ($)", min_value=0.0, step=50.0, value=0.0)
                sactive = st.checkbox("Service is active", value=True)
                add = st.form_submit_button("Add service")

            if add:
                if not sname.strip():
                    st.error("Service name can’t be blank.")
                else:
                    add_service(manage_id, sname, scost, sactive)
                    st.success("Service added.")
                    st.rerun()

            st.divider()
            st.markdown("**Edit services (inline)**")

            if services_df.empty:
                st.info("No services yet for this client.")
            else:
                editable = services_df.copy()
                editable["is_active"] = editable["is_active"].astype(bool)

                edited = st.data_editor(
                    editable,
                    use_container_width=True,
                    hide_index=True,
                    num_rows="fixed",
                    column_config={
                        "id": st.column_config.NumberColumn("id", disabled=True),
                        "service_name": st.column_config.TextColumn("service_name"),
                        "monthly_cost": st.column_config.NumberColumn("monthly_cost", format="$%.2f", min_value=0.0),
                        "is_active": st.column_config.CheckboxColumn("is_active"),
                    },
                    key="services_editor",
                )

                col_a, col_b = st.columns([1, 1])
                with col_a:
                    if st.button("Save service changes"):
                        update_services_bulk(edited)
                        st.success("Services updated.")
                        st.rerun()

                with col_b:
                    # Quick delete by ID
                    del_id = st.selectbox("Delete service (by id)", [None] + services_df["id"].tolist())
                    if del_id is not None and st.button("Delete selected service", type="secondary"):
                        delete_service(int(del_id))
                        st.warning("Service deleted.")
                        st.rerun()

        st.divider()
        st.subheader("All clients (quick view)")
        if clients_df.empty:
            st.caption("No clients yet.")
        else:
            quick = client_monthly_revenue_breakdown()
            st.dataframe(
                quick,
                use_container_width=True,
                hide_index=True,
                column_config={"monthly_revenue": st.column_config.NumberColumn("monthly_revenue", format="$%.2f")},
            )
