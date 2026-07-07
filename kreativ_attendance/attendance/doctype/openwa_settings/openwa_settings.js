frappe.ui.form.on('OpenWA Settings', {
	refresh(frm) {
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
	}
});
