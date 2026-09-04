// Attendance Dashboard — the month-end control panel.
// Month navigation + per-employee summary table (same numbers as the old
// Excel script: present days, total hours, overtime), with one-click path to
// salary: Sync to HRMS -> Create Payroll Entry.

frappe.pages['attendance-dashboard'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Attendance Dashboard'),
		single_column: true,
	});

	var now = frappe.datetime.now_date(); // "YYYY-MM-DD"
	var state = {
		year: parseInt(now.split('-')[0], 10),
		month: parseInt(now.split('-')[1], 10),
	};
	var MONTHS = [__('January'), __('February'), __('March'), __('April'),
		__('May'), __('June'), __('July'), __('August'),
		__('September'), __('October'), __('November'), __('December')];

	function period_label() {
		return MONTHS[state.month - 1] + ' ' + state.year;
	}
	function shift_month(delta) {
		var m = state.month + delta;
		if (m < 1) { m = 12; state.year -= 1; }
		if (m > 12) { m = 1; state.year += 1; }
		state.month = m;
		load();
	}

	page.set_primary_action(__('Sync to HRMS (Attendance + OT)'), function() {
		frappe.confirm(
			__('Create submitted Attendance records and Overtime salary entries for {0}?<br><br>Run this only after all anomalies for the month are resolved.', [period_label()]),
			function() {
				frappe.call({
					method: 'kreativ_attendance.attendance.api.sync_month_to_hrms',
					args: { year: state.year, month: state.month },
					freeze: true,
					freeze_message: __('Creating Attendance and Overtime entries...'),
					callback: function(r) {
						var m = r.message || {};
						var errs = (m.attendance_errors || []).concat(m.overtime_errors || []);
						var html = __('Attendance created: {0} (skipped existing: {1})', [m.attendance_created, m.attendance_skipped_existing]) + '<br>'
							+ __('Overtime entries created: {0} (skipped existing: {1})', [m.overtime_created, m.overtime_skipped_existing]);
						if ((m.overtime_no_rate || []).length) {
							html += '<br>' + __('No overtime rate set (skipped): {0}', [m.overtime_no_rate.join(', ')]);
						}
						if ((m.attendance_outdated || []).length) {
							html += '<br><br><b>' + __('Outdated Attendance (shifts changed since last sync)') + ':</b><br>'
								+ m.attendance_outdated.join('<br>');
						}
						if (errs.length) {
							html += '<br><br><b>' + __('Errors') + ':</b><br>' + errs.join('<br>');
						}
						var warn = errs.length || (m.attendance_outdated || []).length;
						frappe.msgprint({ title: __('Sync to HRMS'), message: html, indicator: warn ? 'orange' : 'green' });
					}
				});
			}
		);
	});

	page.add_inner_button(__('Recalculate Month'), function() {
		frappe.confirm(
			__('Rebuild all Employee Shift records for {0} from the raw checkins?<br>Payroll-locked employees are skipped automatically.', [period_label()]),
			function() {
				frappe.call({
					method: 'kreativ_attendance.attendance.api.recalculate_year_month',
					args: { year: state.year, month: state.month },
					freeze: true,
					freeze_message: __('Re-pairing checkins...'),
					callback: function(r) {
						var m = r.message || {};
						var html = __('Employees processed: {0}', [m.employees]) + '<br>'
							+ __('Shifts paired: {0}, anomalies: {1}', [m.paired, m.anomalies]);
						if ((m.skipped_locked || []).length) {
							html += '<br>' + __('Skipped (payroll locked): {0}', [m.skipped_locked.join(', ')]);
						}
					frappe.msgprint({ title: __('Recalculate {0}', [m.period]), message: html, indicator: 'green' });
					load();
					frappe.call({
						method: 'kreativ_attendance.attendance.api_ui.build_summary',
						args: { year: state.year, month: state.month },
					});
					}
				});
			}
		);
	});
	page.add_inner_button(__('Open Full Report'), function() {
		frappe.route_options = { year: state.year, month: state.month };
		frappe.set_route('query-report', 'KG Employee Shift Summary');
	});
	page.add_inner_button(__('Preview Payroll'), function() {
		frappe.call({
			method: 'kreativ_attendance.attendance.api.preview_payroll',
			args: { year: state.year, month: state.month },
			freeze: true,
			freeze_message: __('Computing payroll preview...'),
			callback: function(r) {
				var data = r.message || {};
				var rows = data.rows || [];
				var tot = data.totals || {};
				if (!rows.length) {
					frappe.msgprint({ title: __('Preview Payroll'), message: __('No summary data for {0}. Run Recalculate first.', [period_label()]), indicator: 'orange' });
					return;
				}
				var html = '<div style="max-height:500px; overflow:auto;">'
					+ '<table class="table table-bordered table-striped" style="font-size:13px; margin:0; white-space:nowrap;">'
					+ '<thead><tr>'
					+ '<th style="padding:8px 12px; min-width:70px;">' + __('ID') + '</th>'
					+ '<th style="padding:8px 12px; min-width:180px;">' + __('Name') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:55px;">' + __('PD') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:70px;">' + __('Pay Days') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:100px;">' + __('Basic') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:90px;">' + __('HRA') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:90px;">' + __('Con') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:90px;">' + __('Med') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:90px;">' + __('Other') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:90px;">' + __('OT Amt') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:100px;">' + __('Gross') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:80px;">' + __('PF') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:80px;">' + __('ESI') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:70px;">' + __('PT') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:70px;">' + __('LWF') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:100px;">' + __('Total Ded') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; font-weight:700; min-width:110px;">' + __('Net') + '</th>'
					+ '</tr></thead><tbody>';
				rows.forEach(function(r) {
					html += '<tr>'
						+ '<td style="padding:8px 12px;">' + frappe.utils.escape_html(r.salary_sheet_id || r.employee) + '</td>'
						+ '<td style="padding:8px 12px;">' + frappe.utils.escape_html(r.employee_name || '') + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + (r.pd || 0) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + (r.pay_days || 0) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(r.basic_payable, {fieldtype:'Currency'}) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(r.hra_payable, {fieldtype:'Currency'}) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(r.con_payable, {fieldtype:'Currency'}) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(r.med_payable, {fieldtype:'Currency'}) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(r.other_payable, {fieldtype:'Currency'}) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(r.overtime, {fieldtype:'Currency'}) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right; font-weight:600;">' + frappe.format(r.gross, {fieldtype:'Currency'}) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + (r.pf ? frappe.format(r.pf, {fieldtype:'Currency'}) : '-') + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + (r.esi ? frappe.format(r.esi, {fieldtype:'Currency'}) : '-') + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + (r.pt ? frappe.format(r.pt, {fieldtype:'Currency'}) : '-') + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + (r.lwf ? frappe.format(r.lwf, {fieldtype:'Currency'}) : '-') + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(r.total_deduction, {fieldtype:'Currency'}) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right; font-weight:700;">' + frappe.format(r.net, {fieldtype:'Currency'}) + '</td>'
						+ '</tr>';
				});
				html += '</tbody><tfoot><tr style="font-weight:700; background:#f0f0f0;">'
					+ '<td style="padding:8px 12px;" colspan="10">' + __('Total') + ' (' + tot.employees + ' ' + __('employees') + ')</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.ot_amount, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.gross, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.pf, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.esi, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">-</td>'
					+ '<td style="padding:8px 12px; text-align:right;">-</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.total_deduction, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.net, {fieldtype:'Currency'}) + '</td>'
					+ '</tr></tfoot></table></div>';
				var d = new frappe.ui.Dialog({
					title: __('Payroll Preview — {0} ({1} employees)', [period_label(), tot.employees]),
					indicator: 'blue',
					size: 'extra-large',
					fields: [{ fieldtype: 'HTML', fieldname: 'preview_table', options: html }],
					primary_action_label: __('Close'),
					primary_action: function() { d.hide(); },
				});
				d.show();
				d.$wrapper.find('.modal-dialog').css('max-width', '1400px');
			}
		});
	});

	page.add_inner_button(__('Check Issues'), function() {
		frappe.call({
			method: 'kreativ_attendance.attendance.quality.month_issues',
			args: { year: state.year, month: state.month },
			callback: function(r) {
				var m = r.message || {};
				var n = (m.anomalies||[]).length + (m.long_sessions||[]).length
				      + (m.break_punches||[]).length;
				frappe.msgprint({
					title: n ? __('Issues blocking payroll: {0}', [n]) : __('Month is clean'),
					indicator: n ? 'red' : 'green',
					message: n
						? '<b>' + __('Anomalies') + ':</b> ' + (m.anomalies||[]).length
						  + '<br><b>' + __('Long sessions (>{0}h)', [m.long_session_threshold_hours]) + ':</b> ' + (m.long_sessions||[]).length
						  + '<br><b>' + __('Break punches') + ':</b> ' + (m.break_punches||[]).length
						  + ((m.missing_standard_hours||[]).length
							  ? '<br><b>' + __('No Standard Hours (8h default)') + ':</b> ' + m.missing_standard_hours.join(', ') : '')
						: __('No anomalies, no suspicious long sessions, no break punches.')
				});
			}
		});
	});

	page.add_inner_button(__('Run Month Close'), function() {
		frappe.confirm(
			__('Recalculate {0}, run the quality gate, sync to HRMS and create a draft Payroll Entry?', [period_label()]),
			function() {
				frappe.call({
					method: 'kreativ_attendance.attendance.monthly.run_monthly_close',
					args: { year: state.year, month: state.month },
					freeze: true,
					freeze_message: __('Closing the month...'),
					callback: function(r) {
						var m = r.message || {};
						frappe.msgprint({
							title: __('Month close: {0}', [m.status]),
							indicator: m.status === 'ok' ? 'green' : 'red',
							message: '<pre>' + JSON.stringify(m, null, 2) + '</pre>'
						});
						load();
					}
				});
			}
		);
	});

	var $body = $('<div class="attendance-dashboard" style="padding: 15px 0;"></div>').appendTo(page.main);

	function card(label, value, color, onclick) {
		var c = $(
			'<div style="flex:1; min-width:150px; background: var(--card-bg, #fff); border:1px solid var(--border-color, #e2e2e2);' +
			' border-radius:8px; padding:14px 18px;' + (onclick ? ' cursor:pointer;' : '') + '">' +
			'<div style="font-size:12px; color: var(--text-muted, #6c7680);">' + label + '</div>' +
			'<div style="font-size:22px; font-weight:600; color:' + (color || 'inherit') + ';">' + value + '</div>' +
			'</div>'
		);
		if (onclick) c.on('click', onclick);
		return c;
	}

	function anomaly_list_route(employee) {
		var last_day = frappe.datetime.month_end(state.year + '-' + String(state.month).padStart(2, '0') + '-01');
		frappe.route_options = {
			status: ['in', ['Anomaly', 'Missing Check-Out']],
			start_date: ['between', [state.year + '-' + String(state.month).padStart(2, '0') + '-01', last_day]],
		};
		if (employee) frappe.route_options.employee = employee;
		frappe.set_route('List', 'KG Employee Attendance Shift', 'List');
	}

	function load() {
		$body.html('<div class="text-muted" style="padding:30px;">' + __('Loading...') + '</div>');
		frappe.call({
			method: 'kreativ_attendance.attendance.api.month_summary',
			args: { year: state.year, month: state.month },
			callback: function(r) { render(r.message || {}); },
		});
	}

	function render(data) {
		$body.empty();
		var t = data.totals || {};
		var rows = data.rows || [];

		// --- month navigation ---
		var nav = $(
			'<div style="display:flex; align-items:center; gap:10px; margin-bottom:14px;">' +
			'<button class="btn btn-default btn-sm" data-nav="-1">&lsaquo;</button>' +
			'<span style="font-size:18px; font-weight:600; min-width:170px; text-align:center;">' + period_label() + '</span>' +
			'<button class="btn btn-default btn-sm" data-nav="1">&rsaquo;</button>' +
			'</div>'
		).appendTo($body);
		nav.find('[data-nav]').on('click', function() { shift_month(parseInt($(this).attr('data-nav'), 10)); });

		// --- summary cards ---
		var cards = $('<div style="display:flex; gap:12px; flex-wrap:wrap; margin-bottom:18px;"></div>').appendTo($body);
		cards.append(card(__('Employees'), t.employees || 0));
		cards.append(card(__('Present Days (total)'), t.present_days || 0));
		cards.append(card(__('Total Hours'), t.total_hours || '0:00'));
		cards.append(card(__('Overtime'), t.overtime || '0:00', '#ff9b00'));
		cards.append(card(
			__('Open Anomalies'), t.anomalies || 0,
			t.anomalies ? '#ff4d4d' : '#29cd41',
			function() { anomaly_list_route(); }
		));

		if (t.anomalies) {
			$body.append(
				'<div class="alert alert-warning" style="margin-bottom:14px;">' +
				__('There are {0} unresolved anomaly record(s) in {1}. Fix them before syncing to HRMS — anomaly rows are excluded from hours and payroll.', [t.anomalies, period_label()]) +
				'</div>'
			);
		}
		if ((data.missing_standard_hours || []).length) {
			$body.append(
				'<div class="alert alert-info" style="margin-bottom:14px;">' +
				__('No Employee Standard Hours record (using default 8h, no overtime pay): {0}', [data.missing_standard_hours.join(', ')]) +
				' &nbsp;<a href="/app/employee-standard-hours">' + __('Set up now') + '</a></div>'
			);
		}

		// --- per-employee table (same as the old script's summary sheet) ---
		if (!rows.length) {
			$body.append('<div class="text-muted" style="padding:30px;">' + __('No shift data for {0}.', [period_label()]) + '</div>');
			return;
		}

		var html = '<table class="table table-bordered" style="background: var(--card-bg, #fff);">' +
			'<thead><tr>' +
			'<th>' + __('Employee ID') + '</th>' +
			'<th>' + __('Name') + '</th>' +
			'<th>' + __('Department') + '</th>' +
			'<th style="text-align:right">' + __('Present Days') + '</th>' +
			'<th style="text-align:right">' + __('Total Hours') + '</th>' +
			'<th style="text-align:right">' + __('Overtime') + '</th>' +
			'<th style="text-align:right">' + __('Anomalies') + '</th>' +
			'</tr></thead><tbody>';

		rows.forEach(function(r) {
			var anomaly_cell = r.anomalies
				? '<a href="#" data-emp="' + frappe.utils.escape_html(r.employee) + '" class="anomaly-link" style="color:#ff4d4d; font-weight:600;">' + r.anomalies + '</a>'
				: '0';
			html += '<tr' + (r.anomalies ? ' style="background:#fff4e6;"' : '') + '>' +
				'<td><a href="/app/employee/' + encodeURIComponent(r.employee) + '">' + frappe.utils.escape_html(r.employee) + '</a></td>' +
				'<td>' + frappe.utils.escape_html(r.employee_name || '') + '</td>' +
				'<td>' + frappe.utils.escape_html(r.department || '') + '</td>' +
				'<td style="text-align:right">' + r.present_days + '</td>' +
				'<td style="text-align:right">' + r.total_hours + '</td>' +
				'<td style="text-align:right">' + r.overtime + '</td>' +
				'<td style="text-align:right">' + anomaly_cell + '</td>' +
				'</tr>';
		});

		html += '</tbody><tfoot><tr style="font-weight:600;">' +
			'<td colspan="3">' + __('Total') + '</td>' +
			'<td style="text-align:right">' + (t.present_days || 0) + '</td>' +
			'<td style="text-align:right">' + (t.total_hours || '0:00') + '</td>' +
			'<td style="text-align:right">' + (t.overtime || '0:00') + '</td>' +
			'<td style="text-align:right">' + (t.anomalies || 0) + '</td>' +
			'</tr></tfoot></table>';

		var $table = $(html).appendTo($body);
		$table.find('.anomaly-link').on('click', function(e) {
			e.preventDefault();
			anomaly_list_route($(this).attr('data-emp'));
		});
	}

	load();
};
