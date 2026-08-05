/**
 * KG Monthly Attendance Summary — list view.
 *
 * This list is where HR signs off the Pay Days that drive payroll, so the two
 * things it must make easy are (a) seeing which rows need attention and
 * (b) reviewing many rows at once.
 *
 * Reviewing 29 employees one form at a time was roughly 116 clicks. The bulk
 * action below makes it one, and refuses rows with unresolved anomalies —
 * because an anomaly means unpaid hours, and Reviewed is what unlocks payroll.
 */

frappe.listview_settings['KG Monthly Attendance Summary'] = {
	add_fields: ['status', 'anomaly_count', 'pd', 'pay_days', 'period',
		'hours_source', 'ot_amount'],

	get_indicator(doc) {
		if ((doc.anomaly_count || 0) > 0) {
			return [__('{0} anomalies', [doc.anomaly_count]), 'red', 'anomaly_count,>,0'];
		}
		const map = {
			Draft: [__('Draft — needs review'), 'orange'],
			Reviewed: [__('Reviewed'), 'green'],
			Locked: [__('Locked — payroll final'), 'blue'],
		};
		const [label, colour] = map[doc.status] || [doc.status, 'gray'];
		return [label, colour, 'status,=,' + doc.status];
	},

	onload(listview) {
		listview.page.add_inner_button(__('Month Console'), () => {
			frappe.set_route('attendance-dashboard');
		});

		listview.page.add_inner_button(__('Needs review'), () => {
			listview.filter_area.clear().then(() => {
				listview.filter_area.add([
					['KG Monthly Attendance Summary', 'status', '=', 'Draft'],
				]);
			});
		});
	},

	/**
	 * Bulk actions appear when rows are checked. `bulk_operations` is the
	 * supported extension point for this.
	 */
	button: {
		show(doc) { return doc.status === 'Draft'; },
		get_label() { return __('Review'); },
		get_description(doc) {
			return __('Mark {0} as Reviewed', [doc.employee_name || doc.name]);
		},
		action(doc) {
			if ((doc.anomaly_count || 0) > 0) {
				frappe.msgprint({
					title: __('Unresolved anomalies'),
					indicator: 'red',
					message: __('This employee has {0} unpaired punch(es) this month, so their hours — and therefore Pay Days — are understated. Fix the punches before reviewing.', [doc.anomaly_count]),
				});
				return;
			}
			frappe.db.set_value('KG Monthly Attendance Summary', doc.name, 'status', 'Reviewed')
				.then(() => {
					frappe.show_alert({ message: __('Reviewed'), indicator: 'green' });
					cur_list && cur_list.refresh();
				});
		},
	},
};
