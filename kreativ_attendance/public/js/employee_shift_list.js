// Employee Shift list view — row coloring + clickable check-in/out times.
// Registered via hooks.py doctype_list_js.
//
// Clicking a Check-In or Check-Out TIME opens the underlying Employee Checkin
// record, so a wrong punch (IN selected instead of OUT, or vice versa) can be
// corrected right there. Shifts rebuild automatically a few seconds after the
// checkin is saved.

frappe.listview_settings['Employee Shift'] = frappe.listview_settings['Employee Shift'] || {};

// Ensure the fields we need are always fetched for the list rows
frappe.listview_settings['Employee Shift'].add_fields = [
    'status', 'locked', 'anomaly_reason', 'manual_correction',
    'check_in_record', 'check_out_record',
];

// ============================================================================
// INDICATOR — status pill color on each row
// ============================================================================

frappe.listview_settings['Employee Shift'].get_indicator = function(doc) {
    if (doc.locked) {
        return [__('Locked'), 'grey', 'locked'];
    }
    if (['missing_checkout', 'previous_month_carryover'].includes(doc.anomaly_reason)) {
        var label = doc.status === 'Missing Check-Out'
            ? __('Missing Check-Out')
            : __('Unpaired');
        return [label, 'orange', 'anomaly'];
    }
    if (doc.manual_correction) {
        return [__('Manual'), 'blue', 'manual_correction'];
    }
    return [__('Paired'), 'green', 'ok'];
};

// ============================================================================
// FORMATTERS — render times and record links as direct links to the source
// Employee Checkin form. (Without this, Frappe renders Link-field values in
// list rows as FILTER links: clicking just filters the list by that value.)
// ============================================================================

function checkin_link(checkin_name, label) {
    return '<a href="/app/employee-checkin/' + encodeURIComponent(checkin_name) + '"'
        + ' style="text-decoration:underline;"'
        + ' title="' + __('Open Employee Checkin to correct this punch') + '"'
        + ' onclick="event.stopPropagation()">'
        + frappe.utils.escape_html(label) + '</a>';
}

frappe.listview_settings['Employee Shift'].formatters = {
    check_in: function(value, df, doc) {
        if (!value) return '';
        var label = frappe.datetime.str_to_user(value);
        return doc.check_in_record ? checkin_link(doc.check_in_record, label) : label;
    },
    check_out: function(value, df, doc) {
        if (!value) return '';
        var label = frappe.datetime.str_to_user(value);
        return doc.check_out_record ? checkin_link(doc.check_out_record, label) : label;
    },
    check_in_record: function(value) {
        return value ? checkin_link(value, value) : '';
    },
    check_out_record: function(value) {
        return value ? checkin_link(value, value) : '';
    },
};

// ============================================================================
// REFRESH — row background colors
// ============================================================================

frappe.listview_settings['Employee Shift'].refresh = function(listview) {
    // Guard: listview or wrapper may be undefined during early render
    if (!listview || !listview.wrapper) return;

    // Wait a beat for Frappe to finish rendering rows
    setTimeout(function() {
        var wrapper = listview.wrapper;
        if (!wrapper || !wrapper.find) return;

        wrapper.find('.list-row').each(function() {
            var row = $(this);
            var docname = row.attr('data-name');
            if (!docname) return;

            var doc = (listview.data || []).find(function(d) { return d.name === docname; }) || {};

            if (doc.locked) {
                row.css('background-color', '#d3d3d3');
            } else if (doc.status === 'Anomaly' || doc.status === 'Missing Check-Out') {
                row.css('background-color', '#fff4e6');
            } else if (doc.manual_correction) {
                row.css('background-color', '#e6eef9');
            } else if (doc.status === 'Paired') {
                row.css('background-color', '#e6f9ed');
            }
        });
    }, 300);
};
