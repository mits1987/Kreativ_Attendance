"""Diagnose and repair Workspace (sidebar) records.

Frappe keeps Workspaces in the database, not on disk. An app's JSON file is only
a *seed*: once a Workspace row exists, editing it in the Desk UI writes to the
database and the app's JSON is no longer consulted. That is why a workspace can
look "overwritten" with no code change to blame.

    bench --site <site> execute kreativ_attendance.attendance.workspace_tools.report
    bench --site <site> execute kreativ_attendance.attendance.workspace_tools.restore \
        --kwargs "{'workspace': 'Shift & Attendance'}"

`restore` deletes the database row and re-imports the file that ships with the
owning app, which returns the workspace to stock. Anything customised in the
Desk UI for that workspace is lost -- that is the point -- so it prints what it
is about to do and requires confirm=1 to act.
"""
import os

import frappe


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report():
    """List every Workspace, whether a disk file backs it, and if it has drifted."""
    rows = frappe.get_all(
        "Workspace",
        fields=["name", "label", "module", "public", "is_hidden", "sequence_id"],
        order_by="sequence_id asc, name asc",
    )
    if not rows:
        print("No Workspace records found.")
        return

    print(f"\n{len(rows)} Workspace record(s) in the database.\n")
    print(f"{'NAME':34} {'MODULE':22} {'PUB':4} {'HID':4} {'DISK FILE'}")
    print("-" * 110)

    orphans, duplicates = [], {}
    for r in rows:
        paths = find_disk_files(r["name"])
        if len(paths) > 1:
            duplicates[r["name"]] = paths
        disk = paths[0] if paths else "(none - DB only)"
        if not paths:
            orphans.append(r["name"])
        print(f"{r['name'][:34]:34} {(r['module'] or '')[:22]:22} "
              f"{'Y' if r['public'] else 'N':4} {'Y' if r['is_hidden'] else 'N':4} "
              f"{_short(disk)}")

    if duplicates:
        print("\n" + "=" * 70)
        print("DUPLICATE DISK FILES — more than one app/path defines the same")
        print("workspace. Whichever syncs last wins, which makes behaviour")
        print("depend on migration order.")
        for name, paths in duplicates.items():
            print(f"\n  {name}:")
            for p in paths:
                print(f"      {p}")

    if orphans:
        print("\n" + "=" * 70)
        print("DATABASE-ONLY workspaces (no file backs these). They were created")
        print("or heavily edited in the Desk UI. `restore` cannot help these —")
        print("they must be fixed by hand or deleted.")
        for o in orphans:
            print(f"      {o}")

    print("\nTo return one to stock:")
    print("  bench --site <site> execute "
          "kreativ_attendance.attendance.workspace_tools.restore \\")
    print("      --kwargs \"{'workspace': 'Shift & Attendance', 'confirm': 1}\"")


def _short(path):
    if not path or path.startswith("("):
        return path
    parts = path.split(os.sep)
    return os.sep.join(parts[-5:]) if len(parts) > 5 else path


# ---------------------------------------------------------------------------
# Disk lookup
# ---------------------------------------------------------------------------

def find_disk_files(workspace_name: str) -> list:
    """Every workspace JSON on disk whose `name` matches, across all apps."""
    matches = []
    for app in frappe.get_installed_apps():
        try:
            app_path = frappe.get_app_path(app)
        except Exception:
            continue
        for root, dirs, files in os.walk(app_path):
            if os.path.basename(root) != "workspace":
                continue
            for sub in dirs:
                candidate = os.path.join(root, sub, f"{sub}.json")
                if not os.path.exists(candidate):
                    continue
                try:
                    data = frappe.parse_json(open(candidate).read())
                except Exception:
                    continue
                if data.get("name") == workspace_name:
                    matches.append(candidate)
    return sorted(matches)


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(workspace: str = None, confirm: int = 0):
    """Delete the DB row and re-import the app's shipped definition."""
    if not workspace:
        print("Pass a workspace name, e.g. --kwargs \"{'workspace': 'Shift & Attendance'}\"")
        return

    exists = frappe.db.exists("Workspace", workspace)
    paths = find_disk_files(workspace)

    print(f"\nWorkspace : {workspace}")
    print(f"In database: {'yes' if exists else 'no'}")
    if not paths:
        print("\nNo file on disk defines this workspace, so there is nothing to "
              "restore it from.\nIf it came from HRMS, check that hrms is "
              "installed and that the name matches exactly\n(including the '&' "
              "and spacing). Run `report` to see the exact names.")
        return

    print("Disk file(s):")
    for p in paths:
        print(f"    {p}")
    if len(paths) > 1:
        print("\nWARNING: more than one file defines this workspace. The LAST "
              "one listed wins.\nResolve the duplication first, or the problem "
              "will come back on the next migrate.")

    if not int(confirm or 0):
        print("\nDRY RUN. This would:")
        if exists:
            print(f"  1. DELETE the Workspace row '{workspace}' "
                  "(losing any Desk UI customisation)")
        print(f"  2. Re-import from {paths[-1]}")
        print("\nRe-run with confirm=1 to proceed:")
        print("  bench --site <site> execute "
              "kreativ_attendance.attendance.workspace_tools.restore \\")
        print(f"      --kwargs \"{{'workspace': '{workspace}', 'confirm': 1}}\"")
        return

    from frappe.modules.import_file import import_file_by_path

    if exists:
        frappe.delete_doc("Workspace", workspace, force=True, ignore_permissions=True)
        frappe.db.commit()
        print(f"Deleted Workspace '{workspace}'.")

    import_file_by_path(paths[-1], force=True)
    frappe.db.commit()
    print(f"Re-imported from {paths[-1]}")
    print("\nNow run:  bench --site <site> clear-cache")
    print("Then hard-refresh the browser (Ctrl/Cmd + Shift + R).")


def restore_hr_defaults(confirm: int = 0):
    """Restore every workspace owned by the HR / Payroll modules to stock.

    Use after an accidental edit to the HRMS sidebar. Only touches workspaces
    that HAVE a disk file in an installed app, so nothing custom is destroyed.
    """
    targets = frappe.get_all(
        "Workspace",
        filters={"module": ["in", ["HR", "Payroll"]]},
        pluck="name",
    )
    if not targets:
        print("No workspaces found for modules HR / Payroll. "
              "Run `report` to see what exists.")
        return

    print(f"Workspaces in HR / Payroll: {', '.join(targets)}\n")
    for name in targets:
        if not find_disk_files(name):
            print(f"  SKIP    {name} (no disk file — would be deleted, not restored)")
            continue
        print(f"  RESTORE {name}")
        restore(name, confirm=confirm)
