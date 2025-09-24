# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

class BatchPaymentAllocationWizard(models.TransientModel):
    _name = "batch.payment.allocation.wizard"
    _description = "Batch Payment Allocation (One payment -> Many invoices)"

    partner_type = fields.Selection([("customer","Customer"),("supplier","Vendor")], required=True, default="supplier")
    partner_id = fields.Many2one("res.partner", string="Partner", required=True, domain="[('parent_id','=',False)]")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True, readonly=True)
    journal_id = fields.Many2one("account.journal", string="Payment Journal", required=True, domain="[('type','in',('bank','cash'))]")
    payment_method_line_id = fields.Many2one("account.payment.method.line", string="Payment Method", domain="[('journal_id','=',journal_id)]")
    payment_date = fields.Date(default=fields.Date.context_today, required=True)
    payment_currency_id = fields.Many2one("res.currency", string="Payment Currency", required=True, default=lambda self: self.env.company.currency_id)
    communication = fields.Char(string="Memo / Reference")
    allocation_mode = fields.Selection([
        ("grouped", "One Grouped Payment"),
        ("per_invoice", "One Payment per Invoice")
    ], default="grouped", required=True, string="Allocation Mode")
    rate_source = fields.Selection([
        ("company", "Company Rates (e.g., BCCR via currency rates)"),
        ("custom", "Custom Rate")
    ], default="company", required=True, string="FX Rate Source")
    custom_rate = fields.Float(string="Custom Rate (1 Company CCY -> Payment CCY)", digits=(16, 6))
    min_residual = fields.Monetary(string="Min Residual (Payment CCY)", currency_field="payment_currency_id", default=0.0)
    only_partial = fields.Boolean(string="Only Partial invoices")
    total_to_pay = fields.Monetary(string="Total to Pay", currency_field="payment_currency_id", compute="_compute_total_to_pay", store=False)
    line_ids = fields.One2many("batch.payment.allocation.wizard.line", "wizard_id", string="Invoices")

    @api.onchange("partner_type", "partner_id", "payment_currency_id")
    def _onchange_partner(self):
        for w in self:
            w._load_invoices()


    def _convert_amount(self, amount_company_ccy, date):
        self.ensure_one()
        if not amount_company_ccy:
            return 0.0
        # company currency to payment currency
        if self.rate_source == "custom" and self.custom_rate:
            return amount_company_ccy * self.custom_rate
        return self.env.company.currency_id._convert(amount_company_ccy, self.payment_currency_id, self.company_id, date or self.payment_date or fields.Date.context_today(self))

    def _load_invoices(self):
        self.ensure_one()
        self.line_ids = [(5, 0, 0)]
        if not (self.partner_type and self.partner_id and self.payment_currency_id):
            return
        in_types = ("out_invoice","out_refund") if self.partner_type == "customer" else ("in_invoice","in_refund")
        moves = self.env["account.move"].search([
            ("move_type", "in", in_types),
            ("partner_id", "=", self.partner_id.id),
            ("state", "=", "posted"),
            ("payment_state", "in", ("not_paid", "partial")),
            ("company_id", "=", self.company_id.id),
        ], order="invoice_date asc, name asc")
        lines = []
        for mv in moves:
            residual_company = abs(mv.amount_residual)  # company currency
            if residual_company <= 0:
                continue
            # Convert residual to chosen payment currency
            residual_pay_cur = self._convert_amount(residual_company, self.payment_date)
            lines.append((0, 0, {
                "move_id": mv.id,
                "name": mv.name,
                "invoice_date": mv.invoice_date,
                
                "residual_in_payment_currency": residual_pay_cur,
                "amount_to_pay": residual_pay_cur,
            }))
        self.line_ids = lines

    @api.depends("line_ids.amount_to_pay")
    def _compute_total_to_pay(self):
        for w in self:
            w.total_to_pay = sum(w.line_ids.mapped("amount_to_pay"))

    def _compute_payment_direction(self):
        # Returns ('inbound'|'outbound', partner_type)
        if self.partner_type == "customer":
            return "inbound", "customer"
        return "outbound", "supplier"

    def action_apply_filter(self):
        self.ensure_one()
        min_res = self.min_residual or 0.0
        only_part = bool(self.only_partial)
        to_remove = self.line_ids.filtered(lambda l: (l.residual_in_payment_currency or 0.0) < min_res or (only_part and l.move_payment_state != 'partial'))
        if to_remove:
            to_remove.unlink()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'batch.payment.allocation.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new'
        }

    def action_reset_lines(self):
        self.ensure_one()
        self._load_invoices()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'batch.payment.allocation.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new'
        }

    def action_remove_selected_lines(self):
        self.ensure_one()
        to_remove = self.line_ids.filtered(lambda l: l.to_delete)
        if to_remove:
            to_remove.unlink()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'batch.payment.allocation.wizard',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new'
        }

    def action_allocate(self):
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("There are no invoice lines to pay."))
        if not self.journal_id:
            raise UserError(_("Please select a Payment Journal."))

        # Choose default payment method if missing
        if not self.payment_method_line_id:
            method = (self.journal_id.inbound_payment_method_line_ids if self.partner_type == "customer"
                      else self.journal_id.outbound_payment_method_line_ids)[:1]
            if not method:
                raise UserError(_("The selected journal has no compatible payment method."))
            self.payment_method_line_id = method.id

        chosen = self.line_ids.filtered(lambda l: l.amount_to_pay and l.amount_to_pay > 0.0)
        if not chosen:
            raise UserError(_("Please set a positive Amount to Pay for at least one invoice."))

        if self.allocation_mode == "per_invoice":
            # Create one payment PER invoice, each with the requested amount
            payment_ids = []
            for line in chosen:
                residual = line.residual_in_payment_currency or 0.0
                amt = line.amount_to_pay or 0.0
                if amt > residual:
                    amt = residual
                if amt <= 0:
                    continue

                reg = self.env["account.payment.register"].with_context(
                    active_model="account.move", active_ids=[line.move_id.id]
                ).create({
                    "payment_date": self.payment_date,
                    "journal_id": self.journal_id.id,
                    "payment_method_line_id": self.payment_method_line_id.id,
                    "currency_id": self.payment_currency_id.id,
                    "amount": amt,
                    "group_payment": False,
                    "communication": self.communication or "",
                })
                payments = reg._create_payments()
                payment_ids += payments.ids

            if not payment_ids:
                raise UserError(_("No payments were created. Check the amounts to pay."))

            return {
                "type": "ir.actions.act_window",
                "res_model": "account.payment",
                "view_mode": "tree,form",
                "domain": [("id", "in", payment_ids)],
                "name": _("Payments"),
            }

        # Grouped: single payment for the sum; clamp each line to residual to avoid rounding issues
        clamped_amounts = []
        for line in chosen:
            residual = line.residual_in_payment_currency or 0.0
            amt = line.amount_to_pay or 0.0
            if amt > residual:
                amt = residual
            if amt < 0:
                amt = 0.0
            clamped_amounts.append(amt)

        total_amount = sum(clamped_amounts)
        if total_amount <= 0:
            raise UserError(_("Total amount to pay must be greater than zero."))

        move_ids = chosen.mapped("move_id").ids
        reg = self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=move_ids
        ).create({
            "payment_date": self.payment_date,
            "journal_id": self.journal_id.id,
            "payment_method_line_id": self.payment_method_line_id.id,
            "currency_id": self.payment_currency_id.id,
            "amount": total_amount,
            "group_payment": True,
            "communication": self.communication or "",
        })
        payments = reg._create_payments()

        return {
            "type": "ir.actions.act_window",
            "res_model": "account.payment",
            "view_mode": "tree,form",
            "domain": [("id", "in", payments.ids)],
            "name": _("Payments"),
        }


class BatchPaymentAllocationWizardLine(models.TransientModel):
    _name = "batch.payment.allocation.wizard.line"
    _description = "Batch Payment Allocation Line"

    wizard_id = fields.Many2one("batch.payment.allocation.wizard", required=True, ondelete="cascade")
    move_id = fields.Many2one("account.move", string="Invoice", required=True, domain="[('state','=','posted')]")
    name = fields.Char(string="Number", readonly=True)
    invoice_date = fields.Date(string="Invoice Date", readonly=True)
    move_payment_state = fields.Selection(related="move_id.payment_state", string="Payment State", readonly=True, store=False)
    residual_in_payment_currency = fields.Monetary(string="Residual (Payment Currency)", currency_field="currency_id", readonly=True)
    amount_to_pay = fields.Monetary(string="Amount to Pay", currency_field="currency_id")
    currency_id = fields.Many2one(related="wizard_id.payment_currency_id", string="Currency", store=False, readonly=True)
    to_delete = fields.Boolean(string="Delete?")

    
    @api.constrains("amount_to_pay")
    def _check_amount(self):
        for rec in self:
            if rec.amount_to_pay is None:
                continue
            if rec.amount_to_pay < 0:
                raise ValidationError(_("Amount to pay must be >= 0."))

    def action_delete_line(self):
        self.unlink()

    @api.onchange("amount_to_pay")
    def _onchange_amount_to_pay(self):
        for rec in self:
            if rec.amount_to_pay is None:
                continue
            residual = rec.residual_in_payment_currency or 0.0
            if rec.amount_to_pay > residual:
                rec.amount_to_pay = residual

        for rec in self:
            if rec.amount_to_pay is None:
                continue
            if rec.amount_to_pay < 0:
                raise ValidationError(_("Amount to pay must be >= 0."))
            # Tolerance for rounding
            if rec.residual_in_payment_currency is not None and (rec.amount_to_pay - rec.residual_in_payment_currency) > 1e-6:
                raise ValidationError(_("Amount to pay cannot exceed the residual."))

    @api.onchange("move_id")
    def _onchange_move(self):
        for rec in self:
            rec.name = rec.move_id.name or ""
            rec.invoice_date = rec.move_id.invoice_date
