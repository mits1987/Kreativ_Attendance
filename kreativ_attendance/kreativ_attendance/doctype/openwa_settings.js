frappe.ui.form.on('OpenWA Settings', {
	refresh(frm) {
		// Fetch and display session status + QR on form load
		if (frm.doc.session_id && frm.doc.base_url && frm.doc.api_key) {
			fetchSessionStatus(frm);
		}

		frm.add_custom_button(__('Send Test Message'), function() {
			frappe.call({
				method: 'kreativ_attendance.attendance.doctype.openwa_settings.openwa_settings.send_test_message',
				freeze: true,
				freeze_message: __('Sending via OpenWA...'),
				callback: function() {
					frappe.msgprint({ message: __('Test message sent — check WhatsApp.'), indicator: 'green' });
				}
			});
		});

		frm.add_custom_button(__('Refresh Status'), function() {
			fetchSessionStatus(frm);
		});

		frm.add_custom_button(__('Start Session'), function() {
			frappe.call({
				method: 'kreativ_attendance.attendance.doctype.openwa_settings.openwa_settings.start_session',
				freeze: true,
				freeze_message: __('Starting WhatsApp session...'),
				callback: function(r) {
					if (r.message && r.message.status === 'ok') {
						frappe.msgprint({ message: r.message.message, indicator: 'green' });
						setTimeout(() => fetchSessionStatus(frm), 2000);
					} else {
						frappe.msgprint({ message: (r.message && r.message.message) || 'Start failed', indicator: 'red' });
					}
				}
			});
		});

		frm.add_custom_button(__('Stop Session'), function() {
			frappe.call({
				method: 'kreativ_attendance.attendance.doctype.openwa_settings.openwa_settings.stop_session',
				freeze: true,
				freeze_message: __('Stopping WhatsApp session...'),
				callback: function(r) {
					if (r.message && r.message.status === 'ok') {
						frappe.msgprint({ message: r.message.message, indicator: 'green' });
						setTimeout(() => fetchSessionStatus(frm), 2000);
					} else {
						frappe.msgprint({ message: (r.message && r.message.message) || 'Stop failed', indicator: 'red' });
					}
				}
			});
		});

		frm.add_custom_button(__('Create New Session'), function() {
			frappe.confirm(__('This will create a brand new WhatsApp session on OpenWA and update the Session ID in settings. The old session (if any) will be abandoned. Continue?'), function() {
				frappe.call({
					method: 'kreativ_attendance.attendance.doctype.openwa_settings.openwa_settings.create_new_session',
					freeze: true,
					freeze_message: __('Creating new session...'),
					callback: function(r) {
						if (r.message && r.message.status === 'ok') {
							frappe.msgprint({ message: r.message.message, indicator: 'green' });
							frm.reload_doc();
						} else {
							frappe.msgprint({ message: (r.message && r.message.message) || 'Creation failed', indicator: 'red' });
						}
					}
				});
			});
		}, __('Session Actions'));

		frm.add_custom_button(__('Open OpenWA Dashboard'), function() {
			if (frm.doc.base_url) {
				window.open(frm.doc.base_url + '/', '_blank');
			} else {
				frappe.msgprint('Set Base URL first');
			}
		});
	}
});


function fetchSessionStatus(frm) {
	frappe.call({
		method: 'kreativ_attendance.attendance.doctype.openwa_settings.openwa_settings.get_session_status',
		callback: function(r) {
			if (r.message) {
				const data = r.message;
				if (data.status === 'error' || data.status === 'not_found') {
					frm.set_value('session_status', 'error');
					frm.set_df_property('session_qr', 'options', `<p style="color:red;">${data.message}</p>`);
				} else {
					frm.set_value('session_status', data.status || 'unknown');
					if (data.phone) frm.set_value('session_phone', data.phone);
					if (data.pushname) frm.set_value('session_pushname', data.pushname);

					// If session needs QR, fetch and display it
					if (data.status === 'qr_ready' || data.status === 'disconnected' || data.status === 'created') {
						fetchQR(frm);
					} else if (data.status === 'ready' || data.status === 'connected') {
						frm.set_df_property('session_qr', 'options',
							`<div style="padding:15px;background:#e8f5e9;border-radius:4px;">
								<b>✓ Session Connected</b><br>
								Phone: ${data.phone || 'unknown'}<br>
								Name: ${data.pushname || 'unknown'}
							</div>`);
					} else {
						frm.set_df_property('session_qr', 'options',
							`<p class="text-muted">Session status: ${data.status}. Click "Refresh Status" or "Start Session".</p>`);
					}
				}
			}
		}
	});
}


function fetchQR(frm) {
	frappe.call({
		method: 'kreativ_attendance.attendance.doctype.openwa_settings.openwa_settings.get_session_qr',
		callback: function(r) {
			if (r.message && r.message.status === 'ok') {
				const qr = r.message.qr;
				const status = r.message.session_status || 'qr_ready';
				frm.set_df_property('session_qr', 'options', `
					<div style="text-align:center;padding:10px;">
						<img src="${qr}" style="max-width:256px;border:1px solid #ddd;border-radius:8px;padding:8px;background:white;" />
						<p style="margin-top:8px;color:#666;font-size:12px;">
							Scan with WhatsApp → Settings → Linked Devices → Link Device
						</p>
						<p style="color:#25D366;font-weight:bold;">Status: ${status}</p>
					</div>
				`);
			} else {
				const msg = (r.message && r.message.message) || 'Failed to load QR';
				frm.set_df_property('session_qr', 'options', `<p style="color:red;">${msg}</p>`);
			}
		}
	});
}