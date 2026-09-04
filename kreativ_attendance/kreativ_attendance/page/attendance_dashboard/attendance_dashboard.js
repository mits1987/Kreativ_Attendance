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
		today: now,
		view: 'monthly',
		daily_date: now,
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
					+ '<th style="padding:8px 12px; text-align:right; min-width:90px;">' + __('Incentive') + '</th>'
					+ '<th style="padding:8px 12px; text-align:right; min-width:90px;">' + __('Penalty') + '</th>'
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
						+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(r.incentive, {fieldtype:'Currency'}) + '</td>'
						+ '<td style="padding:8px 12px; text-align:right;">' + (r.penalty ? frappe.format(r.penalty, {fieldtype:'Currency'}) : '-') + '</td>'
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
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.incentive || 0, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.penalty || 0, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.gross, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.pf, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.esi, {fieldtype:'Currency'}) + '</td>'
					+ '<td style="padding:8px 12px; text-align:right;">-</td>'
					+ '<td style="padding:8px 12px; text-align:right;">' + frappe.format(tot.lwf || 0, {fieldtype:'Currency'}) + '</td>'
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

	// --- View Toggle: Monthly / Daily ---
	page.add_inner_button(__('Daily Check-ins'), function() {
		state.view = 'daily';
		render_daily_view();
	}, 'view-group');
	page.add_inner_button(__('Monthly Summary'), function() {
		state.view = 'monthly';
		load();
	}, 'view-group');

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
			shift_date: ['between', [state.year + '-' + String(state.month).padStart(2, '0') + '-01', last_day]],
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

	// ========================================================================
	// DAILY CHECK-IN VIEW
	// ========================================================================
	function render_daily_view() {
		$body.empty();

		// Date picker + employee filter
		var controls = $(
			'<div style="display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; align-items:center;">' +
			'<div style="display:flex; align-items:center; gap:8px;">' +
			'<label style="font-weight:500;">' + __('Date') + ':</label>' +
			'<input type="date" id="daily-date-picker" style="padding:6px 10px; border:1px solid var(--border-color); border-radius:4px;">' +
			'<button class="btn btn-default btn-sm" id="daily-today-btn">' + __('Today') + '</button>' +
			'<button class="btn btn-default btn-sm" id="daily-prev-btn">&lsaquo;</button>' +
			'<button class="btn btn-default btn-sm" id="daily-next-btn">&rsaquo;</button>' +
			'</div>' +
			'<div style="display:flex; align-items:center; gap:8px;">' +
			'<label style="font-weight:500;">' + __('Employee') + ':</label>' +
			'<input type="text" id="daily-emp-filter" placeholder="' + __('Filter by name or ID') + '" style="padding:6px 10px; border:1px solid var(--border-color); border-radius:4px; width:200px;">' +
			'<button class="btn btn-default btn-sm" id="daily-filter-btn">' + __('Filter') + '</button>' +
			'</div>' +
			'<div style="margin-left:auto;">' +
			'<button class="btn btn-primary btn-sm" id="daily-export-btn">' + __('Export CSV') + '</button>' +
			'</div>' +
			'</div>'
		).appendTo($body);

		// Set default date to today
		var $datePicker = $('#daily-date-picker');
		$datePicker.val(state.daily_date);

		// Event handlers
		$datePicker.on('change', function() {
			state.daily_date = $(this).val();
			load_daily_checkins();
		});
		$('#daily-today-btn').on('click', function() {
			state.daily_date = state.today;
			$datePicker.val(state.daily_date);
			load_daily_checkins();
		});
		$('#daily-prev-btn').on('click', function() {
			var d = new Date(state.daily_date + 'T00:00:00');
			d.setDate(d.getDate() - 1);
			state.daily_date = frappe.datetime.obj_to_str(d);
			$datePicker.val(state.daily_date);
			load_daily_checkins();
		});
		$('#daily-next-btn').on('click', function() {
			var d = new Date(state.daily_date + 'T00:00:00');
			d.setDate(d.getDate() + 1);
			state.daily_date = frappe.datetime.obj_to_str(d);
			$datePicker.val(state.daily_date);
			load_daily_checkins();
		});
		$('#daily-emp-filter').on('keypress', function(e) {
			if (e.which === 13) load_daily_checkins();
		});
		$('#daily-filter-btn').on('click', load_daily_checkins);
		$('#daily-export-btn').on('click', export_daily_csv);

		// Table container
		$('<div id="daily-checkins-table" style="overflow-x:auto;"></div>').appendTo($body);

		load_daily_checkins();
	}

	function load_daily_checkins() {
		var date = state.daily_date;
		var emp_filter = $('#daily-emp-filter').val().trim();

		$('#daily-checkins-table').html('<div class="text-muted" style="padding:30px;">' + __('Loading...') + '</div>');

		frappe.call({
			method: 'kreativ_attendance.attendance.api_ui.daily_checkins',
			args: { date: date, employee: emp_filter || '' },
			callback: function(r) {
				var data = r.message || { rows: [], stats: {} };
				render_daily_table(data);
			}
		});
	}

	function render_daily_table(data) {
		var rows = data.rows || [];
		var stats = data.stats || {};

		// Summary cards
		var $container = $('#daily-checkins-table');
		$container.empty();

		var summary = $(
			'<div style="display:flex; gap:12px; flex-wrap:wrap; margin-bottom:14px;">' +
			'<div style="flex:1; min-width:150px; background:var(--card-bg, #fff); border:1px solid var(--border-color); border-radius:8px; padding:14px 18px;">' +
			'<div style="font-size:12px; color:var(--text-muted, #6c7680);">' + __('Total Employees') + '</div>' +
			'<div style="font-size:22px; font-weight:600;">' + (stats.total_employees || 0) + '</div>' +
			'</div>' +
			'<div style="flex:1; min-width:150px; background:var(--card-bg, #fff); border:1px solid var(--border-color); border-radius:8px; padding:14px 18px;">' +
			'<div style="font-size:12px; color:var(--text-muted, #6c7680);">' + __('Checked In') + '</div>' +
			'<div style="font-size:22px; font-weight:600; color:#29cd41;">' + (stats.checked_in || 0) + '</div>' +
			'</div>' +
			'<div style="flex:1; min-width:150px; background:var(--card-bg, #fff); border:1px solid var(--border-color); border-radius:8px; padding:14px 18px;">' +
			'<div style="font-size:12px; color:var(--text-muted, #6c7680);">' + __('Not Checked In') + '</div>' +
			'<div style="font-size:22px; font-weight:600; color:#ff4d4d;">' + (stats.not_checked_in || 0) + '</div>' +
			'</div>' +
			'<div style="flex:1; min-width:150px; background:var(--card-bg, #fff); border:1px solid var(--border-color); border-radius:8px; padding:14px 18px;">' +
			'<div style="font-size:12px; color:var(--text-muted, #6c7680);">' + __('Missing Check-out') + '</div>' +
			'<div style="font-size:22px; font-weight:600; color:#ff9b00;">' + (stats.missing_checkout || 0) + '</div>' +
			'</div>' +
			'</div>'
		).appendTo($container);

		if (!rows.length) {
			$container.append('<div class="text-muted" style="padding:30px; text-align:center;">' + __('No check-in records for {0}', [frappe.datetime.str_to_user(state.daily_date)]) + '</div>');
			return;
		}

		var html = '<table class="table table-bordered" style="background:var(--card-bg, #fff);">' +
			'<thead><tr>' +
			'<th>' + __('Employee ID') + '</th>' +
			'<th>' + __('Name') + '</th>' +
			'<th>' + __('Department') + '</th>' +
			'<th>' + __('Shift') + '</th>' +
			'<th style="text-align:center">' + __('Check In') + '</th>' +
			'<th style="text-align:center">' + __('Check Out') + '</th>' +
			'<th style="text-align:right">' + __('Hours') + '</th>' +
			'<th style="text-align:center">' + __('Status') + '</th>' +
			'<th>' + __('Device') + '</th>' +
			'</tr></thead><tbody>';

		rows.forEach(function(r) {
			var status_class = '';
			var status_text = '';
			if (r.status === 'Present') { status_class = 'style="color:#29cd41; font-weight:600;"'; status_text = __('Present'); }
			else if (r.status === 'Missing Check-out') { status_class = 'style="color:#ff9b00; font-weight:600;"'; status_text = __('Missing Check-out'); }
			else if (r.status === 'Absent') { status_class = 'style="color:#ff4d4d; font-weight:600;"'; status_text = __('Absent'); }
			else { status_text = r.status || ''; }

			var check_in = r.check_in ? frappe.datetime.str_to_user(r.check_in, 'HH:mm:ss') : '—';
			var check_out = r.check_out ? frappe.datetime.str_to_user(r.check_out, 'HH:mm:ss') : '—';

			html += '<tr' + (r.status === 'Absent' ? ' style="background:#fff4e6;"' : '') + '>' +
				'<td><a href="/app/employee/' + encodeURIComponent(r.employee) + '">' + frappe.utils.escape_html(r.employee) + '</a></td>' +
				'<td>' + frappe.utils.escape_html(r.employee_name || '') + '</td>' +
				'<td>' + frappe.utils.escape_html(r.department || '') + '</td>' +
				'<td>' + frappe.utils.escape_html(r.shift || '') + '</td>' +
				'<td style="text-align:center; font-family:monospace;">' + check_in + '</td>' +
				'<td style="text-align:center; font-family:monospace;">' + check_out + '</td>' +
				'<td style="text-align:right; font-family:monospace;">' + (r.work_hours || '—') + '</td>' +
				'<td style="text-align:center;" ' + status_class + '>' + status_text + '</td>' +
				'<td>' + frappe.utils.escape_html(r.device || '') + '</td>' +
				'</tr>';
		});

		html += '</tbody></table>';
		$container.append(html);
	}

	function export_daily_csv() {
		var date = state.daily_date;
		var emp_filter = $('#daily-emp-filter').val().trim();

		frappe.call({
			method: 'kreativ_attendance.attendance.api_ui.daily_checkins',
			args: { date: date, employee: emp_filter || '' },
			callback: function(r) {
				var data = r.message || { rows: [] };
				var rows = data.rows || [];

				if (!rows.length) {
					frappe.msgprint({ message: __('No data to export'), indicator: 'orange' });
					return;
				}

				var csv = ['Employee ID,Employee Name,Department,Shift,Check In,Check Out,Work Hours,Status,Device'];
				rows.forEach(function(r) {
					var check_in = r.check_in ? frappe.datetime.str_to_user(r.check_in, 'HH:mm:ss') : '';
					var check_out = r.check_out ? frappe.datetime.str_to_user(r.check_out, 'HH:mm:ss') : '';
					csv.push([
						r.employee || '',
						(r.employee_name || '').replace(/,/g, ';'),
						(r.department || '').replace(/,/g, ';'),
						(r.shift || '').replace(/,/g, ';'),
						check_in,
						check_out,
						r.work_hours || '',
						r.status || '',
						(r.device || '').replace(/,/g, ';')
					].join(','));
				});

				var blob = new Blob([csv.join('\n')], { type: 'text/csv;charset=utf-8;' });
				var link = document.createElement('a');
				link.href = URL.createObjectURL(blob);
				link.download = 'daily_checkins_' + date + '.csv';
				document.body.appendChild(link);
				link.click();
				document.body.removeChild(link);
			}
		});
	}

	load();
};
