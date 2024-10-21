# -*- coding: utf-8 -*-
#############################################################################
#
#    Cybrosys Technologies Pvt. Ltd.
#
#    Copyright (C) 2022-TODAY Cybrosys Technologies(<https://www.cybrosys.com>).
#    Author: Faslu Rahman(odoo@cybrosys.com)
#
#    You can modify it under the terms of the GNU AFFERO
#    GENERAL PUBLIC LICENSE (AGPL v3), Version 3.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU AFFERO GENERAL PUBLIC LICENSE (AGPL v3) for more details.
#
#    You should have received a copy of the GNU AFFERO GENERAL PUBLIC LICENSE
#    (AGPL v3) along with this program.
#    If not, see <http://www.gnu.org/licenses/>.
#
#############################################################################

from odoo import api, fields, models


class AccountInvoice(models.Model):
    _inherit = "account.move"

    discount_type = fields.Selection(
        [('percent', 'Percentage'), ('amount', 'Amount')],
        string='Discount type',
        readonly=True,
        states={'draft': [('readonly', False)], 'sent': [('readonly', False)]},
        default='percent')
    discount_rate = fields.Float('Discount Rate', digits=(16, 2),
                                 readonly=True,
                                 states={'draft': [('readonly', False)],
                                         'sent': [('readonly', False)]})
    amount_discount = fields.Monetary(string='Discount', store=True,
                                      compute='_compute_amount', readonly=True,
                                      track_visibility='always')
    amount_untaxed_cus = fields.Monetary("Amount Untaxed")
    amount_taxed_cus = fields.Monetary("Total Tax")
    amount_sub_total = fields.Monetary("Sub Total")
    amount_total_cus = fields.Monetary("Total")
    tax = fields.Many2many("account.tax", string="Taxes")


    # def action_post(self):
    #     res = super(AccountInvoice, self).action_post()
    #     self.payment_state = "not_paid"
    #     return res

    @api.depends(
        'line_ids.matched_debit_ids.debit_move_id.move_id.payment_id.is_matched',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.matched_credit_ids.credit_move_id.move_id.payment_id.is_matched',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.balance',
        'line_ids.currency_id',
        'line_ids.amount_currency',
        'line_ids.amount_residual',
        'line_ids.amount_residual_currency',
        'line_ids.payment_id.state',
        'line_ids.full_reconcile_id')
    def _compute_amount(self):
        for move in self:
            total_untaxed, total_untaxed_currency = 0.0, 0.0
            total_tax, total_tax_currency = 0.0, 0.0
            total_residual, total_residual_currency = 0.0, 0.0
            total, total_currency = 0.0, 0.0
            total_to_pay = move.amount_total

            currencies = set()
            for line in move.line_ids:
                if move.is_invoice(True):
                    # === Invoices ===

                    if line.display_type == 'tax' or (
                            line.display_type == 'rounding' and line.tax_repartition_line_id):
                        # Tax amount.
                        total_tax += line.balance
                        total_tax_currency += line.amount_currency
                        total += line.balance
                        total_currency += line.amount_currency
                    elif line.display_type in ('product', 'rounding'):
                        # Untaxed amount.
                        total_untaxed += line.balance
                        total_untaxed_currency += line.amount_currency
                        total += line.balance
                        total_currency += line.amount_currency
                    elif line.display_type == 'payment_term':
                        # Residual amount.
                        total_residual += line.amount_residual
                        total_residual_currency += line.amount_residual_currency
                else:
                    # === Miscellaneous journal entry ===
                    if line.debit:
                        total += line.balance
                        total_currency += line.amount_currency

            sign = move.direction_sign
            move.amount_untaxed = sign * (total_untaxed_currency if len(
                currencies) == 1 else total_untaxed)
            move.amount_tax = sign * (
                total_tax_currency if len(currencies) == 1 else total_tax)
            move.amount_total = sign * total_currency
            move.amount_residual = -sign * total_residual_currency
            move.amount_untaxed_signed = -total_untaxed
            move.amount_tax_signed = -total_tax
            move.amount_total_signed = abs(
                total) if move.move_type == 'entry' else -total
            move.amount_residual_signed = total_residual
            move.amount_total_in_currency_signed = abs(
                move.amount_total) if move.move_type == 'entry' else -(
                    sign * move.amount_total)
            currency = len(
                currencies) == 1 and currencies.pop() or move.company_id.currency_id

            new_pmt_state = 'not_paid' if move.move_type != 'entry' else False

            if move.is_invoice(
                    include_receipts=True) and move.state == 'posted':
                if currency.is_zero(move.amount_residual):
                    if all(payment.is_matched for payment in
                           move._get_reconciled_payments()):
                        new_pmt_state = 'paid'
                    else:
                        new_pmt_state = move._get_invoice_in_payment_state()
                elif currency.compare_amounts(total_to_pay,
                                              abs(total_residual)) != 0:
                    new_pmt_state = 'partial'

            if new_pmt_state == 'paid' and move.move_type in (
                    'in_invoice', 'out_invoice', 'entry'):
                reverse_type = move.move_type == 'in_invoice' and 'in_refund' or move.move_type == 'out_invoice' and 'out_refund' or 'entry'
                reverse_moves = self.env['account.move'].search(
                    [('reversed_entry_id', '=', move.id),
                     ('state', '=', 'posted'),
                     ('move_type', '=', reverse_type)])

                # We only set 'reversed' state in case of 1 to 1 full
                # reconciliation with a reverse entry; otherwise, we use the
                # regular 'paid' state
                reverse_moves_full_recs = reverse_moves.mapped(
                    'line_ids.full_reconcile_id')
                if reverse_moves_full_recs.mapped(
                        'reconciled_line_ids.move_id').filtered(
                    lambda x: x not in (
                            reverse_moves + reverse_moves_full_recs.mapped(
                        'exchange_move_id'))) == move:
                    new_pmt_state = 'reversed'

            move.payment_state = new_pmt_state

    def write(self, vals):
        result = super(AccountInvoice, self).write(vals)

        for inv in self:
            # Compute untaxed amount (subtotal before tax and discount)
            amount_untaxed_cus = sum(line.price_subtotal for line in inv.invoice_line_ids)

            # Calculate the total discount
            if inv.discount_type == 'percent':
                amount_discount = amount_untaxed_cus * inv.discount_rate / 100
            elif inv.discount_type == 'amount':
                amount_discount = inv.discount_rate
            else:
                amount_discount = 0.0

            # Calculate the subtotal (after discount)
            amount_sub_total = amount_untaxed_cus - amount_discount

            # Compute taxes based on the discounted amount
            total_tax = 0.0
            if inv.tax:
                taxes = inv.tax.compute_all(amount_sub_total)
                total_tax = sum(t['amount'] for t in taxes['taxes'])

            # Calculate the total amount (subtotal after discount + taxes)
            amount_total_cus = amount_sub_total + total_tax

            # Prepare a dictionary of values to update
            update_vals = {
                'amount_untaxed_cus': amount_untaxed_cus,
                'amount_discount': amount_discount,
                'amount_sub_total': amount_sub_total,
                'amount_taxed_cus': total_tax,
                'amount_total_cus': amount_total_cus,
                'amount_total' : amount_total_cus,
                'amount_residual' : amount_total_cus,
                'amount_sub_total' : amount_total_cus

            }

            # Write the computed values back to the record
            super(AccountInvoice, inv).write(update_vals)

        return result

    @api.onchange('discount_type', 'discount_rate', 'invoice_line_ids', 'tax')
    def _compute_total_invoice(self):
        for inv in self:
            inv.amount_untaxed_cus = sum(line.price_subtotal for line in inv.invoice_line_ids)
            if inv.discount_type == 'percent':
                inv.amount_discount = inv.amount_untaxed_cus * inv.discount_rate / 100
            elif inv.discount_type == 'amount':
                inv.amount_discount = inv.discount_rate
            inv.amount_sub_total = inv.amount_untaxed_cus - inv.amount_discount
            total_tax = 0.0
            if inv.tax:
                taxes = inv.tax.compute_all(inv.amount_sub_total)
                total_tax = sum(t['amount'] for t in taxes['taxes'])
            inv.amount_taxed_cus = total_tax
            inv.amount_total_cus = inv.amount_sub_total + inv.amount_taxed_cus
            inv.amount_residual = inv.amount_total_cus
           
    def button_dummy(self):
        self.supply_rate()
        return True


class AccountInvoiceLine(models.Model):
    _inherit = "account.move.line"

    discount = fields.Float(string='Discount (%)', digits=(16, 20), default=0.0)
