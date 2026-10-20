"""Recheck selected-period document recommendations against freshly read movements."""
from collections import defaultdict
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app import reconciliation as rc
from app.models import DocumentMatch, LedgerRow, ReconciliationNotice, ReconciliationResult

ACTION_TYPES = frozenset({rc.MISSING_IN_TAX, rc.EXTRA_IN_TAX, rc.DOCUMENT_QUANTITY_MISMATCH})


def recommendation_targets(result: ReconciliationResult):
    return [d for d in result.discrepancies if not d.balance_point and d.discrepancy_type in ACTION_TYPES]


def _source(d):
    return ('tax', d.tax_row) if d.discrepancy_type == rc.EXTRA_IN_TAX or d.trade_row is None else ('trade', d.trade_row)


def _import_scope(d, side, row):
    return rc._import_candidate_key(row, 'УТ' if side == 'trade' else 'БУХ2019') if d.match_method == rc.IMPORT_PARTY_MATCH else None


def _anchor(d):
    side, row = _source(d)
    return (d.base, side, rc._row_group_key(row), _import_scope(d, side, row))


def _manual(d, reason):
    return replace(d, discrepancy_type=rc.AMBIGUOUS_MATCH, recommendation=rc.MANUAL_CHECK,
                   document_existence='unknown', difference_summary=reason)


def _refresh_counts(summary, discrepancies):
    rows = [d for d in discrepancies if d.base == summary.base]
    def groups(kind):
        return len({_anchor(d) for d in rows if d.discrepancy_type == kind})
    return replace(summary, missing_documents=groups(rc.MISSING_IN_TAX),
                   extra_documents=groups(rc.EXTRA_IN_TAX),
                   ambiguous_documents=groups(rc.AMBIGUOUS_MATCH),
                   document_quantity_differences=sum(d.discrepancy_type == rc.DOCUMENT_QUANTITY_MISMATCH for d in rows),
                   unverified_documents=len({(bool(d.trade_row), *rc._organization_key(d.trade_row or d.tax_row),
                                              (d.trade_row or d.tax_row).document)
                       for d in rows if d.document_existence == 'unknown' and (d.trade_row or d.tax_row)}),
                   manual_item_rows=sum(rc.requires_manual_review(d) for d in rows))


def refresh_recommendations(
    original: ReconciliationResult,
    trade_rows: list[LedgerRow],
    tax_exports: dict[str, list[LedgerRow]],
    *, checked_on: date,
    source_errors: dict[str, str] | None = None,
    amount_tolerance: Decimal = Decimal('0.01'),
    quantity_tolerance: Decimal = Decimal('0'),
) -> ReconciliationResult:
    """Only replace original action cases; a clean pair requires explicit matching evidence.

    Stock findings and the source population/matching statistics describe the selected
    period. Action counters describe the recommendations after the current check.
    Neither a vanished source nor an empty findings list proves a resolved case.
    """
    targets = recommendation_targets(original)
    if not targets:
        return original
    errors = source_errors or {}
    target_ids = {id(d) for d in targets}
    target_items = defaultdict(set)
    whole_documents = set()
    for d in targets:
        if d.discrepancy_type == rc.DOCUMENT_QUANTITY_MISMATCH:
            target_items[_anchor(d)].add(rc._item_key(d.trade_row or d.tax_row))
        else:
            whole_documents.add(_anchor(d))
    stamp = f'Проверка движений по состоянию на {checked_on:%d.%m.%Y}'
    pairs: list[DocumentMatch] = []
    checked_tax = {base: rows for base, rows in tax_exports.items() if base not in errors}
    current = rc.reconcile(trade_rows, checked_tax, '', document_matches=pairs,
                           compare_balances=False, amount_tolerance=amount_tolerance,
                           quantity_tolerance=quantity_tolerance,
                           trade_candidate_rows=trade_rows, tax_candidate_exports=checked_tax)
    sources = {'trade': rc._group_documents(r for r in trade_rows if r.document)}
    sources.update({base: rc._group_documents(r for r in rows if r.document)
                    for base, rows in checked_tax.items()})
    internal_trade = rc._internal_transfer_keys(sources['trade'], trade_rows)
    internal = {'trade': internal_trade}
    internal.update({base: rc._internal_counterpart_keys(sources['trade'], internal_trade, groups)
                     for base, groups in sources.items() if base != 'trade'})
    # Index source identity separately from counterpart identity. Date changes may
    # locate a source but never turn conflicting counterpart dates into a pair.
    by_number = defaultdict(list)
    for base, groups in sources.items():
        for group in groups.values():
            by_number[(base, group.identity.family, group.identity.number.casefold())].append(group)
    finding_index = defaultdict(list)
    for index, d in enumerate(current.discrepancies):
        for side, row in (('trade', d.trade_row), ('tax', d.tax_row)):
            if row is not None:
                finding_index[(d.base, side, rc._row_group_key(row))].append(index)
    pair_index = defaultdict(set)
    for index, pair in enumerate(pairs):
        for side, rows in (('trade', pair.trade_rows), ('tax', pair.tax_rows)):
            for row in rows:
                pair_index[(pair.base, side, rc._row_group_key(row))].add(index)

    replacements = {}
    emitted = set()
    for old in targets:
        anchor = _anchor(old)
        if anchor in replacements:
            continue
        base, side, old_key, import_scope = anchor
        _, original_row = _source(old)
        error = errors.get('УТ') or errors.get(base)
        if error or base not in checked_tax:
            replacements[anchor] = ('manual', f'{stamp} не выполнена: {error or "база не проверена"}')
            continue
        source_base = 'trade' if side == 'trade' else base
        exact = sources[source_base].get(old_key)
        identity = rc.parse_document(original_row.document, original_row.period)
        candidates = ([exact] if exact else [g for g in by_number.get(
            (source_base, identity.family, identity.number.casefold()), ())
            if g.warehouse_key == rc._warehouse_key(original_row)
            and rc._organizations_compatible(original_row, g.rows[0])])
        if len(candidates) != 1:
            replacements[anchor] = ('manual', f'{stamp}: исходное движение не найдено однозначно; наличие или удаление документа не установлено')
            continue
        group = candidates[0]
        if group.key in internal[source_base]:
            replacements[anchor] = ('current', [])
            continue
        if any(r.warehouse_mapping_error for r in group.rows):
            replacements[anchor] = ('manual', f'{stamp}: не определено соответствие склада исходного движения')
            continue
        matching_pairs = pair_index.get((base, side, group.key), set())
        if import_scope is not None:
            matching_pairs = {i for i in matching_pairs if any(
                rc._import_candidate_key(r, 'УТ' if side == 'trade' else 'БУХ2019') == import_scope
                for r in (pairs[i].trade_rows if side == 'trade' else pairs[i].tax_rows))}
        indices = set(finding_index.get((base, side, group.key), ()))
        if len(matching_pairs) == 1:
            pair = pairs[next(iter(matching_pairs))]
            indices = {i for pair_side, rows in (('trade', pair.trade_rows), ('tax', pair.tax_rows))
                       for r in rows for i in finding_index.get((base, pair_side, rc._row_group_key(r)), ())}
        elif len(matching_pairs) > 1:
            replacements[anchor] = ('manual', f'{stamp}: для исходного документа найдено несколько групп соответствия')
            continue
        if not matching_pairs and not indices:
            # A filtered/unknown source cannot disappear just because the current
            # reconciler emitted no document finding for it.
            replacements[anchor] = ('manual', f'{stamp}: соответствие исходного движения не подтверждено')
            continue
        if import_scope is not None:
            indices = {i for i in indices if any(r is not None and
                rc._import_candidate_key(r, s) == import_scope
                for r, s in ((current.discrepancies[i].trade_row, 'УТ'),
                             (current.discrepancies[i].tax_row, 'БУХ2019')))}
        if anchor not in whole_documents:
            indices = {i for i in indices if rc._item_key(
                current.discrepancies[i].trade_row or current.discrepancies[i].tax_row
            ) in target_items[anchor]}
        if not matching_pairs and not indices:
            replacements[anchor] = ('manual', f'{stamp}: для исходной партии или товарной строки нет подтверждённого соответствия')
            continue
        replacements[anchor] = ('current', sorted(indices))

    result_rows = []
    for d in original.discrepancies:
        if id(d) not in target_ids:
            result_rows.append(d)
            continue
        kind, value = replacements[_anchor(d)]
        if kind == 'manual':
            result_rows.append(_manual(d, value))
        else:
            for index in value:
                if index not in emitted:
                    emitted.add(index)
                    item = current.discrepancies[index]
                    result_rows.append(replace(item, difference_summary=f'{stamp}; {item.difference_summary}'))
    notices = [*original.notices, *[ReconciliationNotice(base,
        f'{stamp}. Число исходных документов и статистика сопоставления относятся к выбранному периоду; рекомендации обновлены.',
        sum(d.base == base for d in targets)) for base in sorted({d.base for d in targets})]]
    return replace(original, discrepancies=result_rows,
                   summaries=[_refresh_counts(s, result_rows) for s in original.summaries or []],
                   notices=notices,
                   source_errors={**original.source_errors,
                       **{f'Проверка на {checked_on:%d.%m.%Y}: {base}': error for base, error in errors.items()}})
