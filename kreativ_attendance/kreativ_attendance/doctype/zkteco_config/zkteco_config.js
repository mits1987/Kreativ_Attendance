// Copyright (c) 2026, Mitesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("ZKTeco Config", {
	refresh(frm) {
		if (frm.doc.enable_sync) {
			frm.add_custom_button(__("Test Connection"), async () => {
				frm.call("test_connection").then((r) => {
					if (r.message) {
						if (r.message.ok) {
							frappe.msgprint({
								title: __("Connection Successful"),
								message: `Connected successfully! Found ${r.message.total_transactions} transactions for today.<br>
										  Status: ${r.message.status_code}<br>
										  URL: ${r.message.url}`,
								indicator: "green"
							});
						} else {
							frappe.msgprint({
								title: __("Connection Failed"),
								message: r.message.error || "Unknown error",
								indicator: "red"
							});
						}
					}
				});
			}, __("Test"));
		}
	},
});
