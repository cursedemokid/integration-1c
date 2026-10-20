"""Import review reasons identify the actual missing or conflicting values."""
from unittest import TestCase

from tests.test_import_organization_regressions import raw, run

UT = 'Поступление товаров и услуг UT1 от 01.08.2026 10:00:00'
TAX = 'Поступление (акт, накладная, УПД) TAX1 от 01.08.2026 10:00:00'


class ImportReasonDetailsTests(TestCase):
    def test_missing_trade_fields_are_named_in_xlsx(self):
        _, sheets = run([raw(UT, 5, ПартияБух='', ЗаказПоставщику='',
                                  НаименованиеОтчетКонтрагента='')], [])
        table = sheets['Проверить вручную']
        reason = dict(zip(table[0], table[1]))['Причина']
        for field in ('«Партия (бух.)»', 'заказ поставщику', '«Наименование-отчет»'):
            self.assertIn(field, reason)
        self.assertNotIn('или склад', reason)

    def test_conflicting_party_values_are_shown(self):
        _, sheets = run([], [raw(TAX, 5, side='tax', ПартияБух='',
                                     Комментарий='8', НакладнаяУПД='9/Т11')])
        table = sheets['Проверить вручную']
        reason = dict(zip(table[0], table[1]))['Причина']
        self.assertIn('«Комментарий» = «8»', reason)
        self.assertIn('«Накладная, УПД» = «9/Т11»', reason)
        self.assertIn('партия «9»', reason)
        self.assertIn('Разные значения партии', reason)

    def test_plain_waybill_is_not_misreported_as_an_empty_field(self):
        result, _ = run([], [raw(TAX, 5, side='tax', ПартияБух='', НакладнаяУПД='98765')])
        reason = result.discrepancies[0].difference_summary
        self.assertIn('98765', reason)
        self.assertIn('нет разделителя', reason)
        self.assertNotIn('Разные значения партии', reason)

    def test_valid_party_does_not_appear_among_missing_fields(self):
        result, _ = run([raw(UT, 5, ЗаказПоставщику='')], [])
        self.assertIn('не указан заказ поставщику', result.discrepancies[0].difference_summary)
        self.assertNotIn('не заполнена «Партия', result.discrepancies[0].difference_summary)

    def test_other_side_gets_actual_document_and_reason_without_claiming_a_pair(self):
        result, _ = run([raw(UT, 5)], [raw(TAX, 5, side='tax', ПартияБух='',
                                        НакладнаяУПД='98765')])
        finding = next(d for d in result.discrepancies if d.trade_row)
        self.assertIn(TAX, finding.difference_summary)
        self.assertIn('98765', finding.difference_summary)
        self.assertIn('нет разделителя', finding.difference_summary)
        self.assertEqual(finding.document_existence, 'unknown')
        self.assertFalse(finding.tax_document_refs)

    def test_matching_sources_do_not_create_a_party_conflict(self):
        result, _ = run([raw(UT, 5)], [raw(TAX, 5, side='tax',
                                        Комментарий='5', НакладнаяУПД='5\\Т11')])
        self.assertEqual(result.summaries[0].matched_import_groups, 1)
        self.assertFalse(result.discrepancies)

    def test_empty_waybill_prefix_is_not_a_conflict(self):
        result, _ = run([], [raw(TAX, 5, side='tax', ПартияБух='',
                                НакладнаяУПД='/Т11')])
        reason = result.discrepancies[0].difference_summary
        self.assertIn('не указана партия перед разделителем', reason)
        self.assertNotIn('Разные значения партии', reason)

    def test_missing_warehouse_and_package_coefficient_are_identified(self):
        from dataclasses import replace
        from app.normalization import normalize_rows
        from app.reconciliation import _reconcile_import_receipts
        from decimal import Decimal
        row = normalize_rows([raw(UT, 5)], '')[0]
        row = replace(row, warehouse_mapping_id='', package='Коробка', package_coefficient='')
        findings, *_ = _reconcile_import_receipts([row], [], 'БУХ2019', Decimal(0), Decimal(0))
        reason = findings[0].difference_summary
        self.assertIn('соответствие склада «W1»', reason)
        self.assertIn('упаковки «Коробка» не указан коэффициент', reason)
        self.assertNotIn('не заполнена «Партия', reason)
