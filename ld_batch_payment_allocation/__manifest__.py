# -*- coding: utf-8 -*-
{
    "name": "Batch Payment Allocation (One Payment, Many Invoices)",
    "summary": "Create a single payment and allocate it to multiple invoices with editable per-invoice amounts.",
    "version": "19.0.1.0.1",
    "category": "Accounting/Accounting",
    "author": "FenixCR Solutions",
    "maintainers": ["ld-consulting"],
    "website": "https://www.fenixcrsolutions.com",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "views/menu.xml",
        "views/batch_payment_wizard_views.xml"
    ],
    "assets": {},
    "application": False,
    "installable": True
}
