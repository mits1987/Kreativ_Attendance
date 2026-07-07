def update_website_context(context):
    """Inject TESTING banner for non-production sites"""
    import frappe
    if not frappe.conf.get("developer_mode"):
        return
    
    context.head_html = (context.get("head_html") or "") + """
    <style>
        .testing-banner {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            background: #ff6b6b;
            color: white;
            text-align: center;
            padding: 8px;
            font-weight: bold;
            z-index: 9999;
            font-family: monospace;
        }
    </style>
    <div class="testing-banner">⚠️ TESTING ENVIRONMENT - NOT PRODUCTION ⚠️</div>
    """
