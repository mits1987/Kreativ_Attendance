// KG Employee Attendance Shift list view — row coloring + clickable check-in/out times.
// Registered via hooks.py doctype_list_js.
//
// Color scheme (single source of truth: _classify() below):
//   green      good pair (Paired/Manual, worked <= LONG_SESSION_HOURS)
//   light red  missing one punch (Missing Check-Out / unpaired anomaly, break punch)
//   orange     paired but worked > LONG_SESSION_HOURS — almost always a missed
//              middle punch that merged two days; this is what the quality gate
//              blocks month-close on, so it must be visible here too.
//   grey       locked by a submitted Salary Slip (payroll-finalized)
//   blue       manual correction (and not otherwise flagged)
//
// Clicking a Check-In or Check-Out TIME opens the underlying Employee Checkin
// record, so a wrong punch (IN selected instead of OUT, or vice versa) can be
// corrected right there. Shifts rebuild automatically a few seconds after the
// checkin is saved.

frappe.listview_settings['KG Employee Attendance Shift'] =
    frappe.listview_settings['KG Employee Attendance Shift'] || {};

// Threshold for "long session" — fetched from HR Settings (kreativ_long_session_hours)
// Falls back to 13h if not configured. Kept in sync with attendance/quality.py.
var LONG_SESSION_HOURS = 13;
var LONG_SESSION_SECONDS = LONG_SESSION_HOURS * 3600;
var _threshold_loaded = false;

// Fields the classifier needs on every row.
frappe.listview_settings['KG Employee Attendance Shift'].add_fields = [
    'status', 'locked', 'anomaly_reason', 'manual_correction',
    'worked_seconds', 'check_in_record', 'check_out_record',
];

// ============================================================================
// CLASSIFIER — one function drives both the status pill and the row background,
// so the two can never drift apart.
// ============================================================================
//
// Returns { label, pill, bg } where:
//   label  text shown on the status pill
//   pill   Frappe indicator color name (green|red|orange|grey|blue)
//   bg     CSS background color for the whole row
//
function _classify(doc) {
    // 1. Locked (payroll-finalized) wins over everything — don't alarm on a paid row.
    if (doc.locked) {
        return { label: __('Locked'), pill: 'grey', bg: '#d3d3d3' };
    }

    // 2. Missing one punch — the "fix me" state. LIGHT RED.
    var missing = doc.status === 'Missing Check-Out'
        || ['missing_checkout', 'previous_month_carryover'].includes(doc.anomaly_reason);
    if (missing) {
        var mlabel = doc.status === 'Missing Check-Out' ? __('Missing Check-Out') : __('Unpaired');
        return { label: mlabel, pill: 'red', bg: '#ffe0e0' };
    }

    // 3. Break-state punch — also a bad punch. LIGHT RED (distinct label).
    if (doc.anomaly_reason === 'break_punch') {
        return { label: __('Break Punch'), pill: 'red', bg: '#ffd6d6' };
    }

    // 4. Suspiciously long shift (> threshold) — ORANGE. Applies to Paired AND
    //    Manual, matching the quality gate (status in Paired/Manual).
    if ((doc.worked_seconds || 0) > LONG_SESSION_SECONDS) {
        return {
            label: __('> {0}h ⚠', [LONG_SESSION_HOURS]),
            pill: 'orange',
            bg: '#ffedcc',
        };
    }

    // 5. Any remaining Anomaly with no more specific reason — treat as fix-me.
    if (doc.status === 'Anomaly') {
        return { label: __('Anomaly'), pill: 'red', bg: '#ffe0e0' };
    }

    // 6. Manual correction (and not long / not anomalous) — BLUE.
    if (doc.manual_correction) {
        return { label: __('Manual'), pill: 'blue', bg: '#e6eef9' };
    }

    // 7. Good pair — GREEN.
    return { label: __('Paired'), pill: 'green', bg: '#e6f9ed' };
}

// ============================================================================
// INDICATOR — status pill color on each row
// ============================================================================

frappe.listview_settings['KG Employee Attendance Shift'].get_indicator = function(doc) {
    var c = _classify(doc);
    return [c.label, c.pill, 'status,=,' + (doc.status || '')];
};

// ============================================================================
// REFRESH — row background colors + clickable check-in/out times
// ============================================================================

frappe.listview_settings['KG Employee Attendance Shift'].refresh = function(listview) {
    // Load threshold from HR Settings (once per session) before rendering rows.
    if (!_threshold_loaded) {
        _threshold_loaded = true;
        frappe.call({
            method: 'frappe.client.get_value',
            args: {
                doctype: 'HR Settings',
                fieldname: 'kreativ_long_session_hours',
            },
            callback: function(r) {
                if (r.message && r.message.kreativ_long_session_hours) {
                    LONG_SESSION_HOURS = r.message.kreativ_long_session_hours;
                    LONG_SESSION_SECONDS = LONG_SESSION_HOURS * 3600;
                }
            }
        });
    }

    // Wait a beat for Frappe to finish rendering rows.
    setTimeout(function() {
        console.log('KG ListView DEBUG:', {
            has_data: listview.data !== undefined,
            data_type: typeof listview.data,
            is_array: Array.isArray(listview.data),
            data_length: Array.isArray(listview.data) ? listview.data.length : 'N/A'
        });
        if (!Array.isArray(listview.data)) {
            console.log('KG ListView: data not ready, skipping row processing');
            return;
        }
        console.log('KG ListView: processing rows, data length:', listview.data.length);
        listview.wrapper.find('.list-row').each(function() {
            var row = $(this);
            var docname = row.attr('data-name');
            if (!docname) return;

            console.log('KG ListView: finding doc for:', docname, 'in data of length:', listview.data.length);
            var doc = listview.data.find(function(d) { return d.name === docname; }) || {};

            // Row background from the same classifier the pill uses.
            var c = _classify(doc);
            row.css('background-color', c.bg);

            // Clickable times → open the source Employee Checkin record.
            make_time_open_checkin(row, 'check_in', doc.check_in_record);
            make_time_open_checkin(row, 'check_out', doc.check_out_record);
        });
    }, 300);
};

// ============================================================================
// HELPERS
// ============================================================================

/** Make a Datetime cell open the linked Employee Checkin (to fix wrong punches) */
function make_time_open_checkin(row, fieldname, checkin_name) {
    var cell = row.find('[data-fieldname="' + fieldname + '"]');
    if (!cell.length) return;
    if (!checkin_name) return;   // anomaly rows without a source checkin: leave as plain text

    var text = cell.text().trim();
    if (!text) return;

    cell.css({
        'cursor': 'pointer',
        'color': '#2d7ff9',
        'text-decoration': 'underline'
    }).attr('title', __('Open Employee Checkin to correct this punch'));

    cell.off('click.timeclick').on('click.timeclick', function(e) {
        e.stopPropagation();
        e.preventDefault();
        frappe.set_route('Form', 'Employee Checkin', checkin_name);
    });
}