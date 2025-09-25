
# -*- coding: utf-8 -*-
from odoo import api, fields, models, _

class BatchPaymentAllocationWizard(models.TransientModel):
    _inherit = "batch.payment.allocation.wizard"

    unreconciled_payment_line_ids = fields.One2many(
        "batch.payment.unreconciled.line", "wizard_id",
        string="Unreconciled Credits/Payments", readonly=False,
        help="Outstanding receivable/payable lines (payments, credit notes, etc.) for this partner that can be assigned to invoices."
    )

    @api.onchange("partner_id", "partner_type", "company_id")
    def _onchange_partner_unreconciled(self):
        for wiz in self:
            wiz.unreconciled_payment_line_ids = [(5, 0, 0)]
            if not wiz.partner_id or not wiz.company_id:
                continue
            # Outstanding AR/AP lines not reconciled for this commercial partner
            aml_domain = [
                ("partner_id.commercial_partner_id", "=", wiz.partner_id.commercial_partner_id.id),
                ("company_id", "=", wiz.company_id.id),
                ("account_id.account_type", "in", ("asset_receivable", "liability_payable")),
                ("reconciled", "=", False),
            ]
            # Exclude the invoice lines currently displayed in the wizard to avoid self-assignment
            invoice_moves = wiz.line_ids.mapped("move_id").ids
            if invoice_moves:
                aml_domain.append(("move_id", "not in", invoice_moves))

            aml_records = self.env["account.move.line"].search(aml_domain, order="date asc, id asc")
            lines_vals = []
            for aml in aml_records:
                # Only consider lines with credit available in company currency (negative balance for AR, positive for AP may vary).
                # We rely on amount_residual sign-agnostic using abs().
                residual_company = abs(aml.amount_residual)
                if self.env.company.currency_id.is_zero(residual_company):
                    continue
                vals = {
                    "aml_id": aml.id,
                    "date": aml.date,
                    "move_name": aml.move_id.name or aml.move_id.ref,
                    "journal_id": aml.move_id.journal_id.id,
                    "partner_id": aml.partner_id.id,
                    "company_currency_id": aml.company_currency_id.id if hasattr(aml, "company_currency_id") else aml.company_id.currency_id.id,
                    "currency_id": aml.currency_id.id or aml.company_id.currency_id.id,
                }
                lines_vals.append((0, 0, vals))
            if lines_vals:
                wiz.unreconciled_payment_line_ids = lines_vals

    def action_apply_selected_payments(self):
        """Apply all outstanding lines to invoices (oldest first)."""
        for wiz in self:
            inv_lines = wiz.line_ids.sorted(key=lambda l: (l.invoice_date or l.move_id.invoice_date or False, l.name or ""))
            for credit_line in wiz.unreconciled_payment_line_ids:
                aml = credit_line.aml_id
                if not aml or aml.reconciled:
                    continue
                for inv_line in inv_lines:
                    move = inv_line.move_id
                    if move.state != 'posted':
                        continue
                    if hasattr(inv_line, "amount_to_pay") and (not inv_line.amount_to_pay or inv_line.amount_to_pay <= 0):
                        continue
                    try:
                        move.js_assign_outstanding_line(aml.id)
                    except Exception:
                        self.env.cr.rollback()
                        continue
                    aml.invalidate_cache()
                    aml.refresh()
                    if getattr(aml, "reconciled", False):
                        break
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': _('Credits Applied'), 'message': _('Outstanding credits/payments were applied to available invoices.'), 'type': 'success'}}


class BatchPaymentUnreconciledLine(models.TransientModel):
    _name = "batch.payment.unreconciled.line"
    _description = "Outstanding Credit/Payment Line"

    wizard_id = fields.Many2one("batch.payment.allocation.wizard", required=True, ondelete="cascade")
    aml_id = fields.Many2one("account.move.line", string="Outstanding Item", required=True, readonly=True)
    move_name = fields.Char(string="Document", readonly=True)
    journal_id = fields.Many2one("account.journal", string="Journal", readonly=True)
    date = fields.Date(string="Date", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Partner", readonly=True)
    currency_id = fields.Many2one("res.currency", string="Currency", readonly=True)
    company_currency_id = fields.Many2one("res.currency", string="Company Currency", readonly=True)

    available_company = fields.Monetary(string="Available (Company)", currency_field="company_currency_id", compute="_compute_available", store=False, readonly=True)
    available_currency = fields.Monetary(string="Available", currency_field="currency_id", compute="_compute_available", store=False, readonly=True)

    @api.depends("aml_id")
    def _compute_available(self):
        for rec in self:
            aml = rec.aml_id
            if not aml:
                rec.available_company = 0.0
                rec.available_currency = 0.0
                continue
            rec.available_company = abs(aml.amount_residual)
            rec.available_currency = abs(aml.amount_residual_currency) if aml.currency_id else rec.available_company
