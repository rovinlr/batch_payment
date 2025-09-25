
# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

class BatchPaymentAllocationWizard(models.TransientModel):
    _inherit = "batch.payment.allocation.wizard"

    unreconciled_payment_line_ids = fields.One2many(
        "batch.payment.unreconciled.line", "wizard_id",
        string="Unreconciled Payments", readonly=False,
        help="Payments for this partner that are posted and have remaining amount to match."
    )

    @api.onchange("partner_id", "partner_type", "company_id")
    def _onchange_partner_unreconciled(self):
        for wiz in self:
            if not wiz.partner_id or not wiz.company_id:
                wiz.unreconciled_payment_line_ids = [(5, 0, 0)]
                continue
            lines = []
            payments = self.env["account.payment"].search([
                ("partner_id", "child_of", wiz.partner_id.commercial_partner_id.id),
                ("state", "=", "posted"),
                ("company_id", "=", wiz.company_id.id),
            ], order="date asc, id asc")
            for pay in payments:
                aml_open = pay.move_id.line_ids.filtered(
                    lambda l: l.account_id.account_type in ("asset_receivable", "liability_payable") and not l.reconciled
                )
                if not aml_open:
                    continue
                residual_company = sum(aml_open.mapped("amount_residual"))
                if abs(residual_company) < pay.company_currency_id.rounding:
                    continue
                vals = {
                    "payment_id": pay.id,
                    "payment_date": pay.date,
                    "journal_id": pay.journal_id.id,
                    "partner_id": pay.partner_id.id,
                    "company_currency_id": pay.company_currency_id.id,
                    "currency_id": pay.currency_id.id,
                    "amount_total": abs(sum(pay.move_id.line_ids.filtered(lambda l: l.account_id.account_type in ('asset_receivable','liability_payable')).mapped('amount_total')) or 0.0),
                }
                lines.append((0, 0, vals))
            wiz.unreconciled_payment_line_ids = [(5, 0, 0)] + lines

    def action_apply_selected_payments(self):
        """Apply selected outstanding payments to the invoices in this wizard (oldest first)."""
        for wiz in self:
            if not wiz.unreconciled_payment_line_ids:
                continue
            inv_lines = wiz.line_ids.sorted(key=lambda l: (l.invoice_date or l.move_id.invoice_date or False, l.name or ""))
            for pay_line in wiz.unreconciled_payment_line_ids.filtered(lambda r: r.payment_id.state == 'posted'):
                aml_candidates = pay_line.payment_id.move_id.line_ids.filtered(
                    lambda l: l.account_id.account_type in ('asset_receivable','liability_payable') and not l.reconciled and l.partner_id.commercial_partner_id == wiz.partner_id.commercial_partner_id
                )
                if not aml_candidates:
                    continue
                aml = aml_candidates[0]
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
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {'title': _('Payments Applied'), 'message': _('Selected payments were applied to available invoices.'), 'type': 'success'}}


class BatchPaymentUnreconciledLine(models.TransientModel):
    _name = "batch.payment.unreconciled.line"
    _description = "Unreconciled Payment Line"

    wizard_id = fields.Many2one("batch.payment.allocation.wizard", required=True, ondelete="cascade")
    payment_id = fields.Many2one("account.payment", string="Payment", required=True, readonly=True)
    partner_id = fields.Many2one("res.partner", related="payment_id.partner_id", string="Partner", store=False, readonly=True)
    journal_id = fields.Many2one("account.journal", related="payment_id.journal_id", string="Journal", store=False, readonly=True)
    payment_date = fields.Date(related="payment_id.date", string="Date", store=False, readonly=True)
    currency_id = fields.Many2one("res.currency", related="payment_id.currency_id", string="Currency", store=False, readonly=True)
    company_currency_id = fields.Many2one("res.currency", related="payment_id.company_currency_id", string="Company Currency", store=False, readonly=True)
    amount_total = fields.Monetary(string="Original Amount", currency_field="currency_id", readonly=True)
    available_company = fields.Monetary(string="Available (Company)", currency_field="company_currency_id", compute="_compute_available", store=False, readonly=True)
    available_payment_currency = fields.Monetary(string="Available (Payment)", currency_field="currency_id", compute="_compute_available", store=False, readonly=True)

    @api.depends("payment_id")
    def _compute_available(self):
        for rec in self:
            if not rec.payment_id:
                rec.available_company = 0.0
                rec.available_payment_currency = 0.0
                continue
            aml_open = rec.payment_id.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type in ('asset_receivable','liability_payable') and not l.reconciled
            )
            residual_company = sum(aml_open.mapped('amount_residual'))
            rec.available_company = abs(residual_company)
            # Convert to payment currency
            rec.available_payment_currency = rec.payment_id.currency_id._convert(
                abs(residual_company),
                rec.payment_id.currency_id,
                rec.payment_id.company_id,
                rec.payment_id.date or fields.Date.today()
            )
