"""Found candidates are listed on the requested add-to-accounting sheet."""
from dataclasses import replace
from unittest import TestCase

from app.models import ReconciliationResult, ReconciliationSummary
from app.reconciliation import MISSING_ITEM_IN_TAX
from app.reporting import build_action_excel_report
from scripts.verify_onec import workbook_rows
from tests.test_compact_report import _manual


class CandidateVisibilityTests(TestCase):
    def test_trade_and_tax_candidates_move_to_add_with_original_context(self):
        for side in ('trade', 'tax'):
            for refs in [('Документ-кандидат',), ('Кандидат 1', 'Кандидат 2')]:
                with self.subTest(side=side, refs=refs):
                    source = _manual(1)
                    finding = replace(source, trade_document_refs=(), tax_document_refs=refs)
                    if side == 'tax':
                        finding = replace(finding, trade_row=None, tax_row=source.trade_row,
                                          trade_document_refs=refs, tax_document_refs=())
                    result = ReconciliationResult(discrepancies=[finding])
                    sheets = workbook_rows(build_action_excel_report(result))
                    for name in ('Проверить вручную', 'Лишние в БУХ'):
                        self.assertEqual(len(sheets[name]), 1)
                    self.assertIn(['Требуют ручной проверки', '0'], sheets['Итоги'])
                    self.assertEqual(len(sheets['Довнести в БУХ']), 2)
                    action = dict(zip(sheets['Довнести в БУХ'][0], sheets['Довнести в БУХ'][1]))
                    self.assertEqual(action['Действие'], 'Довнести в БУХ2026')
                    self.assertEqual(action['Причина'], finding.difference_summary)
                    self.assertEqual(action['Количество УТ' if side == 'trade' else 'Количество БУХ'], '100')
                    self.assertEqual(action['Документ БУХ' if side == 'trade' else 'Документ УТ'], '; '.join(refs))
                    self.assertEqual(result.discrepancies, [finding])
                    self.assertIn('Документ-кандидат' if len(refs) == 1 else 'Кандидат',
                                  str(finding.trade_document_refs + finding.tax_document_refs))

    def test_source_references_without_counterpart_are_not_candidates(self):
        source = _manual(1)
        sheets = workbook_rows(build_action_excel_report([source]))
        self.assertEqual(len(sheets['Проверить вручную']), 2)

    def test_existing_document_item_error_is_retained(self):
        source = _manual(1)
        finding = replace(source, discrepancy_type=MISSING_ITEM_IN_TAX,
                          document_existence='found', tax_document_refs=('Найденный документ',))
        sheets = workbook_rows(build_action_excel_report([finding]))
        self.assertEqual(len(sheets['Проверить вручную']), 2)

    def test_confirmed_pair_is_not_hidden_as_a_candidate(self):
        source = _manual(1)
        finding = replace(source, tax_row=source.trade_row,
                          tax_document_refs=('Документ БУХ',))
        sheets = workbook_rows(build_action_excel_report([finding]))
        self.assertEqual(len(sheets['Проверить вручную']), 2)

    def test_mixed_report_counts_only_visible_manual_rows(self):
        source = _manual(1)
        candidate = replace(_manual(2), tax_document_refs=('Кандидат',))
        result = ReconciliationResult(
            discrepancies=[source, candidate],
            summaries=[ReconciliationSummary(base='БУХ2026', manual_item_rows=2)],
        )
        sheets = workbook_rows(build_action_excel_report(result))
        self.assertEqual(len(sheets['Проверить вручную']), 2)
        self.assertIn(['Требуют ручной проверки', '1'], sheets['Итоги'])
        summary = sheets['Итоги']
        header_index = next(i for i, row in enumerate(summary)
                            if 'Исходных строк в ручной проверке' in row)
        values = dict(zip(summary[header_index], summary[header_index + 1]))
        self.assertEqual(values['Исходных строк в ручной проверке'], '1')
        self.assertEqual(result.summaries[0].manual_item_rows, 2)

    def test_candidate_items_remain_one_case_with_aligned_quantities(self):
        findings = [replace(_manual(index), tax_document_refs=('Документ-кандидат',))
                    for index in (1, 2, 3)]
        sheets = workbook_rows(build_action_excel_report(findings))
        self.assertEqual(len(sheets['Довнести в БУХ']), 2)
        row = dict(zip(sheets['Довнести в БУХ'][0], sheets['Довнести в БУХ'][1]))
        self.assertEqual(row['Количество УТ'], '100\n200\n300')
        self.assertEqual(row['Артикул'], 'ART-1\nART-2\nART-3')
