/**
 * Month Console — the single screen for running an attendance month.
 *
 * REPLACES the old dashboard, which had three problems:
 *   1. "Run Month Close" called monthly.run_monthly_close — a function that no
 *      longer exists after the payroll rewrite. The button was silently dead.
 *   2. Issues were shown in a msgprint popup: read-only, no way to act on them,
 *      and it closed when you navigated away. Fixing one punch took ~11 steps.
 *   3. Nothing told a new operator what to do next, or in what order.
 *
 * The console is built around one idea: at any moment there is exactly ONE next
 * action, and it is rendered as the primary button. Everything else is context.
 */

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
	'August', 'September', 'October', 'November', 'December'];

frappe.pages['attendance-dashboard'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Attendance Month Console'),
		single_column: true,
	});

	const now = frappe.datetime.now_date(true);
	const state = {
		year: now.getFullYear(),
		month: now.getMonth() + 1,   // default: the month just ended
		data: null,
		issues: [],
		rows: [],
		verified: [],
		tab: 'steps',
	};
	// Default to the PREVIOUS month: that is the one being closed.
	state.month -= 1;
	if (state.month === 0) { state.month = 12; state.year -= 1; }

	// ---- Period picker (single control, no Apply button) -----------------
	const yearField = page.add_field({
		fieldname: 'year', label: __('Year'), fieldtype: 'Select',
		options: year_options(), default: String(state.year),
		change() { state.year = parseInt(this.get_value(), 10); load(); },
	});
	const monthField = page.add_field({
		fieldname: 'month', label: __('Month'), fieldtype: 'Select',
		options: MONTHS.join('\n'), default: MONTHS[state.month - 1],
		change() {
			const idx = MONTHS.indexOf(this.get_value());
			if (idx >= 0) { state.month = idx + 1; load(); }
		},
	});
	yearField.set_value(String(state.year));
	monthField.set_value(MONTHS[state.month - 1]);

	page.set_secondary_action(__('Refresh'), () => load());
	page.add_menu_item(__('Attendance Settings'), () =>
		frappe.set_route('Form', 'KG Attendance Settings'));
	page.add_menu_item(__('All Shift Records'), () =>
		frappe.set_route('List', 'KG Employee Attendance Shift'));
	page.add_menu_item(__('All Monthly Summaries'), () =>
		frappe.set_route('List', 'KG Monthly Attendance Summary'));

	const $body = $('<div class="kg-console"></div>').appendTo(page.main);
	inject_styles();

	// ======================================================================
	// LOAD
	// ======================================================================
	function load() {
		$body.html(`<div class="kg-loading">${__('Loading…')}</div>`);
		frappe.call({
			method: 'kreativ_attendance.attendance.api_ui.month_status',
			args: { year: state.year, month: state.month },
			callback(r) {
				if (!r.message) { $body.html(`<div class="kg-loading">${__('No data.')}</div>`); return; }
				state.data = r.message;
				render();
				// Fetch detail tables in the background so the primary action
				// paints immediately.
				load_details();
			},
		});
	}

	function load_details() {
		frappe.call({
			method: 'kreativ_attendance.attendance.api_ui.month_issue_rows',
			args: { year: state.year, month: state.month },
			callback(r) { state.issues = r.message || []; render(); },
		});
		frappe.call({
			method: 'kreativ_attendance.attendance.api_ui.summary_rows',
			args: { year: state.year, month: state.month },
			callback(r) { state.rows = r.message || []; render(); },
		});
		frappe.call({
			method: 'kreativ_attendance.attendance.api_ui.verified_long_sessions',
			args: { year: state.year, month: state.month },
			callback(r) { state.verified = r.message || []; render(); },
		});
	}

	// ======================================================================
	// RENDER
	// ======================================================================
	function render() {
		const d = state.data;
		if (!d) return;
		$body.empty();
		render_banner(d);
		render_next_action(d);
		render_tabs(d);
	}

	/** Shadow Mode is the single most important fact on screen. */
	function render_banner(d) {
		if (!d.shadow_mode) return;
		$body.append(`
			<div class="kg-banner kg-banner-shadow">
				<b>${__('Shadow Mode is ON')}</b> —
				${__('this system is running in parallel and will not write any payroll documents. Your existing payroll process is unaffected.')}
				<a href="/app/kg-attendance-settings" class="kg-banner-link">${__('Settings')}</a>
			</div>`);
	}

	/** The one thing to do next, as a big button with a plain-English reason. */
	function render_next_action(d) {
		const n = d.next;
		const $card = $(`
			<div class="kg-next kg-next-step-${n.step}">
				<div class="kg-next-left">
					<div class="kg-next-step">${__('Step {0} of 5', [n.step])}</div>
					<div class="kg-next-label">${frappe.utils.escape_html(n.label)}</div>
					<div class="kg-next-hint">${frappe.utils.escape_html(n.hint)}</div>
				</div>
				<div class="kg-next-right"></div>
			</div>`);

		const $btn = $(`<button class="btn btn-primary btn-lg"></button>`);
		const map = {
			recalculate: [__('Recalculate'), do_recalculate],
			fix_issues: [__('Show issues'), () => { state.tab = 'issues'; render(); }],
			build_summary: [__('Build summary'), do_build_summary],
			review: [__('Review all clean rows'), do_review_all],
			payroll: [__('Write payroll'), do_write_payroll],
			shadow_blocked: [__('Open settings'), () => frappe.set_route('Form', 'KG Attendance Settings')],
		};
		const [label, handler] = map[n.action] || [__('Refresh'), load];
		$btn.text(label).on('click', handler);
		$card.find('.kg-next-right').append($btn);
		$body.append($card);

		// Compact stat strip — context, not decisions.
		const s = d.summary;
		const stats = [
			[__('Shifts built'), d.shift_count, ''],
			[__('Blocking issues'), count_blocking(d), count_blocking(d) ? 'bad' : 'good'],
			[__('Summaries'), `${s.reviewed + s.locked}/${s.total}`, s.total && !s.draft ? 'good' : ''],
			[__('Days in month'), d.days_in_month, ''],
		];
		const $strip = $('<div class="kg-stats"></div>');
		stats.forEach(([l, v, cls]) => $strip.append(
			`<div class="kg-stat ${cls}"><div class="kg-stat-v">${v}</div><div class="kg-stat-l">${l}</div></div>`));
		$body.append($strip);
	}

	function count_blocking(d) {
		const i = d.issues || {};
		return (i.anomalies || []).length + (i.long_sessions || []).length;
	}

	// ---- Tabs -------------------------------------------------------------
	function render_tabs(d) {
		const tabs = [
			['steps', __('Overview')],
			['issues', __('Issues') + (state.issues.length ? ` (${state.issues.length})` : '')],
			['summary', __('Pay Days') + (state.rows.length ? ` (${state.rows.length})` : '')],
		];
		const $nav = $('<div class="kg-tabs"></div>');
		tabs.forEach(([k, l]) => {
			$(`<div class="kg-tab ${state.tab === k ? 'active' : ''}">${l}</div>`)
				.on('click', () => { state.tab = k; render(); })
				.appendTo($nav);
		});
		$body.append($nav);

		const $pane = $('<div class="kg-pane"></div>').appendTo($body);
		if (state.tab === 'issues') render_issues($pane);
		else if (state.tab === 'summary') render_summary($pane);
		else render_overview($pane, d);
	}

	/** Plain description of the five steps, so a new person can self-orient. */
	function render_overview($p, d) {
		const s = d.summary;
		const steps = [
			[1, __('Recalculate'), __('Turn raw device punches into paired shifts.'), d.shift_count > 0],
			[2, __('Fix bad punches'), __('Every unpaired punch is unpaid time. Must be zero.'), d.shift_count > 0 && !count_blocking(d)],
			[3, __('Build Pay Days'), __('Convert hours into PD, WO, PH and overtime.'), s.total > 0],
			[4, __('Review'), __('Check against your salary sheet, then mark Reviewed.'), s.total > 0 && !s.draft],
			[5, __('Payroll'), __('Enter the Production Bonus, then generate slips.'), !d.shadow_mode && s.total > 0 && !s.draft],
		];
		const $l = $('<div class="kg-steps"></div>');
		steps.forEach(([n, t, sub, done]) => $l.append(`
			<div class="kg-step ${done ? 'done' : ''}">
				<div class="kg-step-n">${done ? '✓' : n}</div>
				<div><div class="kg-step-t">${t}</div><div class="kg-step-s">${sub}</div></div>
			</div>`));
		$p.append($l);

		if (d.last_close && d.last_close.period) {
			$p.append(`<div class="kg-muted">${__('Last automatic close')}: 
				<b>${d.last_close.period}</b> — ${d.last_close.status} ${d.last_close.at}</div>`);
		}
	}

	/** Issues, each with a Fix button that opens the exact record to correct. */
	function render_issues($p) {
		if (!state.issues.length) {
			$p.append(`<div class="kg-empty">✓ ${__('No issues. Every punch in this month is paired.')}</div>`);
			return;
		}

		const verify_rows = state.issues.filter((r) => r.can_verify && !r.verified);

		if (verify_rows.length) {
			const $bar = $('<div class="kg-bar"></div>');
			$(`<button class="btn btn-sm btn-default">${__('Select all long sessions')}</button>`)
				.on('click', () => {
					const cb = $p.find('.kg-vcheck');
					const all = cb.filter(':checked').length === cb.length;
					cb.prop('checked', !all);
					if (!all) { $bar.find('.kg-bulk-verify').show(); }
					else { $bar.find('.kg-bulk-verify').hide(); }
				}).appendTo($bar);

			const $bulk = $(`<button class="btn btn-sm btn-primary kg-bulk-verify" style="display:none">${__('Verify selected as genuine')}</button>`);
			$bulk.on('click', () => {
				const checked = $p.find('.kg-vcheck:checked').map((_, el) => $(el).data('shift')).get();
				if (!checked.length) {
					frappe.msgprint(__('Select one or more long sessions to verify.'));
					return;
				}
				bulk_verify(checked);
			});
			$bar.append($bulk);
			$p.append($bar);
		}

		const has_checkboxes = verify_rows.length > 0;
		const header_checkbox = has_checkboxes
			? `<th><input type="checkbox" class="kg-vcheck-all"></th>`
			: `<th></th>`;

		const $t = $(`<table class="table kg-table"><thead><tr>
			${header_checkbox}<th>${__('Employee')}</th><th>${__('Date')}</th>
			<th>${__('Problem')}</th><th>${__('What to do')}</th><th></th>
		</tr></thead><tbody></tbody></table>`);
		const $b = $t.find('tbody');

		state.issues.forEach((r) => {
			const check_cell = (r.can_verify && !r.verified)
				? `<td><input type="checkbox" class="kg-vcheck" data-shift="${r.shift}"></td>`
				: `<td></td>`;

			const $tr = $(`<tr>
				${check_cell}
				<td><span class="kg-dot kg-${r.severity}"></span> <b>${frappe.utils.escape_html(r.employee_name)}</b></td>
				<td>${r.date || '—'}</td>
				<td>${frappe.utils.escape_html(r.what)}</td>
				<td class="kg-advice">${frappe.utils.escape_html(r.advice)}</td>
				<td class="kg-actions"></td>
			</tr>`);
			const $a = $tr.find('.kg-actions');

			if (r.employee_link) {
				$(`<button class="btn btn-xs btn-default">${__('Open employee')}</button>`)
					.on('click', () => frappe.set_route('Form', 'Employee', r.employee_link))
					.appendTo($a);
				$b.append($tr);
				return;
			}

			// Go to the employee's PUNCHES for the month — not to a single
			// record. When a punch is MISSING there is no record to open, and
			// the surrounding punches are what reveal what went wrong.
			$(`<button class="btn btn-xs btn-primary">${__('See all punches')}</button>`)
				.on('click', () => open_punches(r))
				.appendTo($a);

			if (r.can_verify) {
				$(`<button class="btn btn-xs btn-default" style="margin-left:4px">${__('Verify')}</button>`)
					.on('click', () => verify_session(r))
					.appendTo($a);
			}

			if (r.shift) {
				$(`<button class="btn btn-xs btn-default" style="margin-left:4px">${__('Shift')}</button>`)
					.on('click', () => frappe.set_route('Form', 'KG Employee Attendance Shift', r.shift))
					.appendTo($a);
			}
			$b.append($tr);
		});

		// Header "select all" checkbox
		$t.find('.kg-vcheck-all').on('change', function () {
			const checked = $(this).is(':checked');
			$p.find('.kg-vcheck').prop('checked', checked);
			if (checked) { $p.find('.kg-bulk-verify').show(); }
			else { $p.find('.kg-bulk-verify').hide(); }
		});

		// Individual checkbox change → toggle bulk button
		$t.on('change', '.kg-vcheck', function () {
			const any = $p.find('.kg-vcheck:checked').length > 0;
			$p.find('.kg-bulk-verify').toggle(any);
		});

		$p.append($t);
		$p.append(`<div class="kg-muted">${__('After correcting punches, shifts rebuild automatically. Press Refresh to re-check.')}</div>`);

		render_verified($p);
	}

	/** Open the employee's Employee Checkin list for the whole month. */
	function open_punches(r) {
		frappe.route_options = {
			employee: r.employee,
			time: ['Between', [r.month_from, r.month_to]],
		};
		frappe.set_route('List', 'Employee Checkin');
	}

	/** Accept a long session as genuine, with a reason. */
	function verify_session(r) {
		const d = new frappe.ui.Dialog({
			title: __('Verify long session'),
			fields: [
				{
					fieldtype: 'HTML',
					options: `<div style="margin-bottom:10px">
						<b>${frappe.utils.escape_html(r.employee_name)}</b> — ${r.date}<br>
						<span class="text-muted">${frappe.utils.escape_html(r.what)}</span>
					</div>
					<div class="text-muted small" style="margin-bottom:8px">
						${__('Confirm these hours are real work and not a missed punch. The month close will stop blocking on this shift. If the punches change later, the verification is withdrawn automatically and you will be asked again.')}
					</div>`,
				},
				{
					fieldname: 'note', fieldtype: 'Small Text', reqd: 1,
					label: __('Why is this genuine?'),
					description: __('e.g. cylinder run, machine could not be stopped'),
				},
			],
			primary_action_label: __('Verify'),
			primary_action(v) {
				d.hide();
				frappe.call({
					method: 'kreativ_attendance.attendance.verification.verify_shift',
					args: { shift: r.shift, note: v.note },
					freeze: true,
					callback() {
						frappe.show_alert({ message: __('Verified'), indicator: 'green' });
						load();
					},
				});
			},
		});
		d.show();
	}

	/** Bulk-verify several long sessions at once, with one shared reason. */
	function bulk_verify(shifts) {
		const d = new frappe.ui.Dialog({
			title: __('Verify {0} long session(s)', [shifts.length]),
			fields: [
				{
					fieldtype: 'HTML',
					options: `<div class="text-muted small" style="margin-bottom:8px">
						${__('Confirm these hours are real work and not missed punches. They will stop blocking the month close. If any punches change later, that verification is withdrawn automatically.')}
					</div>`,
				},
				{
					fieldname: 'note', fieldtype: 'Small Text', reqd: 1,
					label: __('Why are these genuine?'),
					description: __('e.g. cylinder run, machine could not be stopped'),
				},
			],
			primary_action_label: __('Verify all'),
			primary_action(v) {
				d.hide();
				frappe.call({
					method: 'kreativ_attendance.attendance.verification.verify_shifts',
					args: { shifts: shifts, note: v.note },
					freeze: true,
					freeze_message: __('Verifying…'),
					callback(r) {
						const m = r.message || {};
						const msg = m.skipped && m.skipped.length
							? __('{0} verified. Skipped: {1}', [m.verified || 0, m.skipped.join(', ')])
							: __('{0} session(s) verified.', [m.verified || 0]);
						frappe.show_alert({ message: msg, indicator: 'green' }, 7);
						load();
					},
				});
			},
		});
		d.show();
	}

	/** Verified sessions stay visible, and reversible. */
	function render_verified($p) {
		if (!state.verified || !state.verified.length) return;

		$p.append(`<div style="margin-top:22px; font-weight:600; font-size:13px">
			${__('Accepted long sessions ({0})', [state.verified.length])}</div>
			<div class="kg-muted" style="margin-top:2px">${__('Checked and confirmed genuine. These no longer block the close.')}</div>`);

		const $t = $(`<table class="table kg-table" style="margin-top:8px"><thead><tr>
			<th>${__('Employee')}</th><th>${__('Date')}</th><th class="r">${__('Hours')}</th>
			<th>${__('Reason')}</th><th>${__('By')}</th><th></th>
		</tr></thead><tbody></tbody></table>`);
		const $b = $t.find('tbody');

		state.verified.forEach((v) => {
			const $tr = $(`<tr>
				<td>${frappe.utils.escape_html(v.employee_name || '')}</td>
				<td>${v.shift_date}</td>
				<td class="r">${v.hours}</td>
				<td class="kg-advice">${frappe.utils.escape_html(v.verification_note || '')}</td>
				<td class="text-muted" style="font-size:11px">${frappe.utils.escape_html(v.verified_by || '')}</td>
				<td class="kg-actions"></td>
			</tr>`);
			$(`<button class="btn btn-xs btn-default">${__('Undo')}</button>`)
				.on('click', () => {
					frappe.confirm(__('Withdraw this verification? The shift will block the month close again.'), () => {
						frappe.call({
							method: 'kreativ_attendance.attendance.verification.unverify_shift',
							args: { shift: v.name },
							callback() { load(); },
						});
					});
				})
				.appendTo($tr.find('.kg-actions'));
			$b.append($tr);
		});
		$p.append($t);
	}

	/** Pay Days table — the number that actually drives payroll. */
	function render_summary($p) {
		if (!state.rows.length) {
			$p.append(`<div class="kg-empty">${__('No summary rows yet. Build the summary first.')}</div>`);
			return;
		}
		const $bar = $('<div class="kg-bar"></div>');
		$(`<button class="btn btn-sm btn-primary">${__('Review all clean rows')}</button>`)
			.on('click', do_review_all).appendTo($bar);
		$(`<button class="btn btn-sm btn-default">${__('Rebuild from punches')}</button>`)
			.on('click', do_build_summary).appendTo($bar);
		$(`<button class="btn btn-sm btn-default">${__('Send all back to Draft')}</button>`)
			.on('click', do_unreview).appendTo($bar);
		$p.append($bar);

		const $t = $(`<table class="table kg-table"><thead><tr>
			<th>${__('Employee')}</th><th class="r">${__('Hours')}</th>
			<th class="r">${__('WD')}</th><th class="r">${__('PD')}</th>
			<th class="r">${__('WO')}</th><th class="r">${__('PH')}</th>
			<th class="r"><b>${__('Pay Days')}</b></th>
			<th class="r">${__('OT hrs')}</th><th class="r">${__('OT ₹')}</th>
			<th>${__('Status')}</th><th></th>
		</tr></thead><tbody></tbody></table>`);
		const $b = $t.find('tbody');

		state.rows.forEach((r) => {
			const warn = (r.warnings || []).length;
			const $tr = $(`<tr class="${warn ? 'kg-warn-row' : ''}">
				<td><b>${frappe.utils.escape_html(r.employee_name || '')}</b>
					${warn ? `<div class="kg-warn">${r.warnings.map(frappe.utils.escape_html).join(' · ')}</div>` : ''}</td>
				<td class="r">${fmt(r.total_hours)}</td>
				<td class="r">${fmt(r.wd)}</td>
				<td class="r"><b>${fmt(r.pd)}</b></td>
				<td class="r">${fmt(r.wo)}</td>
				<td class="r">${fmt(r.ph)}</td>
				<td class="r kg-paydays"><b>${fmt(r.pay_days)}</b></td>
				<td class="r">${fmt(r.ot_hours)}</td>
				<td class="r">${r.ot_amount ? format_currency(r.ot_amount) : '—'}</td>
				<td><span class="kg-pill kg-pill-${(r.status || '').toLowerCase()}">${r.status}</span></td>
				<td class="kg-actions"></td>
			</tr>`);
			$(`<button class="btn btn-xs btn-default">${__('Open')}</button>`)
				.on('click', () => frappe.set_route('Form', 'KG Monthly Attendance Summary', r.name))
				.appendTo($tr.find('.kg-actions'));
			$b.append($tr);
		});

		// Totals row — the figure to check against the wage register.
		const tot = state.rows.reduce((a, r) => {
			a.pd += r.pd || 0; a.pay += r.pay_days || 0; a.ot += r.ot_amount || 0;
			return a;
		}, { pd: 0, pay: 0, ot: 0 });
		$b.append(`<tr class="kg-total"><td><b>${__('Total')}</b></td><td colspan="2"></td>
			<td class="r"><b>${fmt(tot.pd)}</b></td><td colspan="2"></td>
			<td class="r"><b>${fmt(tot.pay)}</b></td><td></td>
			<td class="r"><b>${tot.ot ? format_currency(tot.ot) : '—'}</b></td><td colspan="2"></td></tr>`);
		$p.append($t);
	}

	// ======================================================================
	// ACTIONS
	// ======================================================================
	function do_recalculate() {
		frappe.confirm(
			__('Rebuild all shifts for {0} from the raw punches?<br><small>Payroll-locked employees are skipped automatically.</small>', [label()]),
			() => call('kreativ_attendance.attendance.api.recalculate_year_month',
				{ year: state.year, month: state.month }, __('Rebuilding shifts…'),
				(m) => __('{0} shifts paired, {1} anomalies found.', [m.paired || 0, m.anomalies || 0]))
		);
	}

	function do_build_summary() {
		call('kreativ_attendance.attendance.api_ui.build_summary',
			{ year: state.year, month: state.month }, __('Building Pay Days…'),
			(m) => __('{0} created, {1} updated, {2} already reviewed.',
				[m.created || 0, m.updated || 0, m.preserved || 0]));
	}

	function do_review_all() {
		frappe.confirm(
			__('Mark every clean Draft row for {0} as Reviewed?<br><small>Rows with unresolved anomalies are skipped.</small>', [label()]),
			() => call('kreativ_attendance.attendance.api_ui.review_summaries',
				{ year: state.year, month: state.month, only_clean: 1 }, __('Reviewing…'),
				(m) => m.skipped && m.skipped.length
					? __('{0} reviewed. Skipped (unresolved anomalies): {1}', [m.reviewed, m.skipped.join(', ')])
					: __('{0} row(s) reviewed.', [m.reviewed]))
		);
	}

	function do_unreview() {
		frappe.confirm(
			__('Send all Reviewed rows back to Draft? Locked rows are not affected.'),
			() => call('kreativ_attendance.attendance.api_ui.unreview_summaries',
				{ year: state.year, month: state.month }, __('Updating…'),
				(m) => __('{0} row(s) returned to Draft.', [m.unreviewed]))
		);
	}

	function do_write_payroll() {
		frappe.confirm(
			__('Write Attendance and Overtime to HRMS for {0}?', [label()])
				+ '<br><br><b>' + __('Enter the Production Bonus first')
				+ '</b> — ' + __('it is not created automatically.'),
			() => call('kreativ_attendance.attendance.api.sync_month_to_hrms',
				{ year: state.year, month: state.month }, __('Writing payroll…'),
				(m) => m.skipped ? m.message
					: __('Attendance: {0}, Overtime rows: {1}.',
						[m.attendance_created || 0, m.overtime_created || 0]))
		);
	}

	function call(method, args, freeze_message, summarize) {
		frappe.call({
			method, args, freeze: true, freeze_message,
			callback(r) {
				const m = r.message || {};
				frappe.show_alert({ message: summarize(m), indicator: 'green' }, 7);
				load();
			},
		});
	}

	// ---- helpers ----------------------------------------------------------
	function label() {
		return `${MONTHS[state.month - 1]} ${state.year}`;
	}
	function fmt(v) {
		if (v === null || v === undefined || v === '') return '—';
		const n = parseFloat(v);
		return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.00$/, '');
	}
	function year_options() {
		const y = new Date().getFullYear();
		return [y + 1, y, y - 1, y - 2].map(String).join('\n');
	}
	load();
};

function inject_styles() {
	if (document.getElementById('kg-console-styles')) return;
	const css = `
	.kg-console { padding: 12px 0 40px; }
	.kg-loading { padding: 40px; text-align: center; color: var(--text-muted); }
	.kg-banner { padding: 10px 14px; border-radius: 6px; margin-bottom: 14px; font-size: 13px; }
	.kg-banner-shadow { background: #fff8e1; border: 1px solid #ffe082; color: #6b5200; }
	.kg-banner-link { margin-left: 8px; }
	.kg-next { display: flex; align-items: center; justify-content: space-between; gap: 20px;
		padding: 20px 22px; border-radius: 10px; background: var(--card-bg, #fff);
		border: 1px solid var(--border-color, #e2e2e2); border-left: 5px solid #2d7ff9; }
	.kg-next-step-2 { border-left-color: #ff4d4d; }
	.kg-next-step-5 { border-left-color: #29cd41; }
	.kg-next-step { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--text-muted); }
	.kg-next-label { font-size: 19px; font-weight: 600; margin: 3px 0 5px; }
	.kg-next-hint { font-size: 13px; color: var(--text-muted); max-width: 640px; }
	.kg-stats { display: flex; gap: 10px; margin: 14px 0; flex-wrap: wrap; }
	.kg-stat { flex: 1; min-width: 120px; padding: 12px 14px; border-radius: 8px;
		background: var(--card-bg, #fff); border: 1px solid var(--border-color, #e2e2e2); }
	.kg-stat.good { border-color: #29cd41; } .kg-stat.bad { border-color: #ff4d4d; }
	.kg-stat-v { font-size: 22px; font-weight: 600; } 
	.kg-stat-l { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .05em; }
	.kg-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border-color, #e2e2e2); margin-top: 18px; }
	.kg-tab { padding: 9px 16px; cursor: pointer; font-size: 13px; border-bottom: 2px solid transparent; }
	.kg-tab.active { border-bottom-color: #2d7ff9; font-weight: 600; }
	.kg-pane { padding-top: 16px; }
	.kg-steps { display: flex; flex-direction: column; gap: 2px; }
	.kg-step { display: flex; gap: 14px; align-items: flex-start; padding: 12px 14px; border-radius: 8px; }
	.kg-step.done { background: #f2fbf4; }
	.kg-step-n { width: 26px; height: 26px; border-radius: 50%; background: #eef1f5; color: #555;
		display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; flex-shrink: 0; }
	.kg-step.done .kg-step-n { background: #29cd41; color: #fff; }
	.kg-step-t { font-weight: 600; font-size: 14px; } 
	.kg-step-s { font-size: 12px; color: var(--text-muted); }
	.kg-table { font-size: 13px; } .kg-table th { font-size: 11px; text-transform: uppercase;
		letter-spacing: .04em; color: var(--text-muted); font-weight: 600; }
	.kg-table td.r, .kg-table th.r { text-align: right; }
	.kg-table td { vertical-align: middle; }
	.kg-advice { color: var(--text-muted); font-size: 12px; max-width: 360px; }
	.kg-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
	.kg-dot.kg-red { background: #ff4d4d; } .kg-dot.kg-orange { background: #ff9b00; }
	.kg-warn-row { background: #fffaf2; }
	.kg-warn { font-size: 11px; color: #b26a00; }
	.kg-paydays { background: #f4f8ff; }
	.kg-pill { padding: 2px 9px; border-radius: 10px; font-size: 11px; font-weight: 600; }
	.kg-pill-draft { background: #eef1f5; color: #555; }
	.kg-pill-reviewed { background: #e6f9ed; color: #157a2b; }
	.kg-pill-locked { background: #e6eef9; color: #1a4d8f; }
	.kg-total td { border-top: 2px solid var(--border-color, #ddd); background: #fafbfc; }
	.kg-bar { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
	.kg-empty { padding: 40px; text-align: center; color: var(--text-muted); font-size: 14px; }
	.kg-muted { font-size: 12px; color: var(--text-muted); margin-top: 12px; }
	.kg-actions { white-space: nowrap; }
	`;
	$('<style id="kg-console-styles"></style>').text(css).appendTo(document.head);
}
