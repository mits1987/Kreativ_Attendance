/**
 * KG Monthly Attendance Summary — form view.
 *
 * This form carries the numbers that decide someone's pay, so it shows the
 * arithmetic rather than just the result. A new operator should be able to see
 * WHY Pay Days is what it is without reading the source.
 */

frappe.ui.form.on('KG Monthly Attendance Summary', {
	refresh(frm) {
		show_calculation(frm);
		show_warnings(frm);
		add_actions(frm);
	},

	total_hours(frm) { show_calculation(frm); },
	standard_hours(frm) { show_calculation(frm); },
	wd(frm) { show_calculation(frm); },
});

/** Render the PD / Pay Days derivation in plain arithmetic. */
function show_calculation(frm) {
	const d = frm.doc;
	if (!d.standard_hours) return;

	const required = (d.wd || 0) * d.standard_hours;
	const raw = d.standard_hours ? (d.total_hours || 0) / d.standard_hours : 0;

	const html = `
		<div style="background:var(--card-bg,#fff); border:1px solid var(--border-color,#e2e2e2);
					border-radius:8px; padding:14px 16px; font-size:13px;">
			<div style="font-weight:600; margin-bottom:10px;">${__('How these figures were derived')}</div>
			<table style="width:100%; font-family:var(--font-stack); font-size:12.5px;">
				<tr><td style="padding:3px 0; color:var(--text-muted)">${__('Hours worked ÷ standard hours')}</td>
					<td style="text-align:right">${fmt(d.total_hours)} ÷ ${fmt(d.standard_hours)} = <b>${raw.toFixed(2)}</b></td></tr>
				<tr><td style="padding:3px 0; color:var(--text-muted)">${__('Rounded to nearest half day')}</td>
					<td style="text-align:right"><b>${fmt(d.pd)}</b></td></tr>
				<tr><td style="padding:3px 0; color:var(--text-muted)">${__('Capped at working days')}</td>
					<td style="text-align:right">${__('WD')} = ${fmt(d.wd)}</td></tr>
				<tr style="border-top:1px solid var(--border-color,#eee)">
					<td style="padding:6px 0"><b>${__('Pay Days')}</b> 
						<span style="color:var(--text-muted)">(${__('PD + WO + PH')})</span></td>
					<td style="text-align:right; font-size:15px">
						${fmt(d.pd)} + ${fmt(d.wo)} + ${fmt(d.ph)} = <b>${fmt(d.pay_days)}</b></td></tr>
				<tr><td style="padding:3px 0; color:var(--text-muted)">${__('Required hours (WD × standard)')}</td>
					<td style="text-align:right">${required.toFixed(2)}</td></tr>
				<tr><td style="padding:3px 0; color:var(--text-muted)">${__('Overtime (hours beyond required)')}</td>
					<td style="text-align:right">${fmt(d.ot_hours)} ${__('hrs')}
						${d.ot_amount ? ' = ' + format_currency(d.ot_amount) : ''}</td></tr>
			</table>
			<div style="margin-top:10px; font-size:11.5px; color:var(--text-muted)">
				${__('Salary components are prorated as: amount ÷ {0} days × {1} pay days.',
					[fmt(d.days_in_month), fmt(d.pay_days)])}
			</div>
		</div>`;

	frm.get_field('result_section') && frm.set_df_property('result_section', 'description', '');
	frm.dashboard.clear_headline();
	if (!frm.__calc_wrapper) {
		frm.__calc_wrapper = $('<div style="margin:12px 0"></div>');
		frm.dashboard.wrapper.append(frm.__calc_wrapper);
	}
	frm.__calc_wrapper.html(html);
}

/** Surface anything that makes these numbers untrustworthy. */
function show_warnings(frm) {
	const d = frm.doc;
	const warnings = [];

	if ((d.anomaly_count || 0) > 0) {
		warnings.push({
			colour: 'red',
			text: __('{0} unpaired punch(es) this month. Those hours are missing from the total, so Pay Days is too low. Fix the punches and rebuild before reviewing.', [d.anomaly_count]),
		});
	}
	if (d.hours_source && d.hours_source !== 'Employee.working_hours') {
		warnings.push({
			colour: 'orange',
			text: __('No Working Hours set on this Employee — the system default was used. Set it on the Employee record for an accurate figure.'),
		});
	}
	if (!d.holiday_list) {
		warnings.push({
			colour: 'orange',
			text: __('No Holiday List for this Employee, so weekly offs and public holidays could not be counted. WD, WO and PH may be wrong.'),
		});
	}
	if ((d.pay_days || 0) > (d.days_in_month || 0)) {
		warnings.push({
			colour: 'red',
			text: __('Pay Days ({0}) is more than the number of days in the month ({1}). Check WD, WO and PH.', [d.pay_days, d.days_in_month]),
		});
	}

	warnings.forEach((w) => frm.dashboard.add_comment(w.text, w.colour, true));
}

function add_actions(frm) {
	if (frm.is_new()) return;

	if (frm.doc.status === 'Draft') {
		frm.add_custom_button(__('Mark Reviewed'), () => {
			if ((frm.doc.anomaly_count || 0) > 0) {
				frappe.confirm(
					__('This employee still has {0} unpaired punch(es), so their hours are understated. Review anyway?', [frm.doc.anomaly_count]),
					() => { frm.set_value('status', 'Reviewed'); frm.save(); }
				);
				return;
			}
			frm.set_value('status', 'Reviewed');
			frm.save();
		}).addClass('btn-primary');
	}

	if (frm.doc.status === 'Reviewed') {
		frm.add_custom_button(__('Back to Draft'), () => {
			frm.set_value('status', 'Draft');
			frm.save();
		});
	}

	frm.add_custom_button(__('View this month\'s shifts'), () => {
		frappe.set_route('List', 'KG Employee Attendance Shift', {
			employee: frm.doc.employee,
			shift_date: ['between', [
				`${frm.doc.period_year}-${String(frm.doc.period_month).padStart(2, '0')}-01`,
				frappe.datetime.add_days(
					frappe.datetime.add_months(
						`${frm.doc.period_year}-${String(frm.doc.period_month).padStart(2, '0')}-01`, 1), -1),
			]],
		});
	});

	frm.add_custom_button(__('Month Console'), () => frappe.set_route('attendance-dashboard'));
}

function fmt(v) {
	if (v === null || v === undefined || v === '') return '—';
	const n = parseFloat(v);
	return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.00$/, '');
}
