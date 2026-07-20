import frappe

def execute():
    frappe.connect()

    print("=== Error Logs (last 2 min) ===")
    logs = frappe.db.sql("""
        SELECT name, creation, method, error
        FROM `tabError Log`
        WHERE creation > DATE_SUB(NOW(), INTERVAL 2 MINUTE)
        ORDER BY creation DESC LIMIT 5
    """, as_dict=True)
    for l in logs:
        print(f"\n--- {l.name} | {l.creation} | {l.method} ---")
        print(l.error[:2000] if l.error else "(no error)")
    if not logs:
        print("  (none)")

    print("\n=== Short Queue Jobs ===")
    try:
        from rq import Queue
        conn = frappe.cache().get_redis_conn()
        q = Queue("short", connection=conn)
        jobs = q.get_job_ids()
        print(f"  Pending: {len(jobs)}")
        for jid in jobs[:5]:
            job = q.fetch_job(jid)
            print(f"  {jid} | {job.get_status()} | {job.func_name}")
    except Exception as e:
        print(f"  Error: {e}")

    frappe.db.commit()
