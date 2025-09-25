# -*- coding: utf-8 -*-
{
    "name": "LD Batch Payment Allocation",
    "summary": "Allocate a payment across multiple invoices with per-line amounts.",
    "version": "19.0.33",
    "category": "Accounting/Accounting",
    "author": "FenixCR Solutions",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "views/menu.xml",
        "views/batch_payment_wizard_views.xml",
        "views/batch_payment_unreconciled_views.xml",
        "data/diagnose_views.xml"
    ],
    "application": False,
    "installable": True
}
