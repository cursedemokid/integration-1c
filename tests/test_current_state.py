"""Current movements may resolve recommendations, never enlarge the report period."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest import TestCase
from app.normalization import normalize_rows
from app.reconciliation import reconcile, BALANCE_QUANTITY_MISMATCH
from app.reporting import build_action_excel_report
from scripts.verify_onec import workbook_rows
from tests.test_report_review_regressions import source

TODAY=date(2026,10,16)
DOC='Поступление товаров 7001 от 02.07.2026 10:00:00'

def rows(*records):return normalize_rows(list(records),'')
def initial(ut,tax):
 return reconcile(ut,{'БУХ2026':tax},'01.07.2026–31.07.2026',start_date=date(2026,7,1),end_date=date(2026,7,31))
def current(original,ut,tax,**kwargs):
 from app.current_state import refresh_recommendations
 return refresh_recommendations(original,ut,{'БУХ2026':tax},checked_on=TODAY,**kwargs)
def sheets(result):return workbook_rows(build_action_excel_report(result))

class CurrentStateTests(TestCase):
 def test_document_entered_today_is_not_recommended_again(self):
  ut=rows(source());old=initial(ut,[])
  self.assertEqual(len(sheets(old)['Довнести в БУХ']),2)
  result=current(old,ut,rows(source()))
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
  self.assertEqual(len(sheets(result)['Проверить вручную']),1)
  self.assertEqual(result.summaries[0].missing_documents,0)

 def test_extra_document_now_has_trade_counterpart(self):
  tax=rows(source());result=current(initial([],tax),rows(source()),tax)
  self.assertEqual(len(sheets(result)['Лишние в БУХ']),1)
  self.assertEqual(result.summaries[0].extra_documents,0)

 def test_corrected_quantity_disappears_from_extra(self):
  ut=rows(source(qty=537));tax=rows(source(qty=550))
  result=current(initial(ut,tax),rows(source(qty=550)),tax)
  self.assertEqual(len(sheets(result)['Лишние в БУХ']),1)
  self.assertEqual(result.summaries[0].document_quantity_differences,0)

 def test_remaining_quantity_difference_uses_current_values(self):
  old=initial(rows(source(qty=5)),rows(source(qty=10)))
  result=current(old,rows(source(qty=8)),rows(source(qty=10)))
  table=sheets(result)['Лишние в БУХ'];r=dict(zip(table[0],table[1]))
  self.assertEqual([r[k] for k in ('Количество УТ','Количество БУХ','Разница БУХ − УТ')],['8','10','2'])
  self.assertIn('16.10.2026',r['Причина'])

 def test_current_only_documents_do_not_enter_july(self):
  future=source('Поступление товаров 9999 от 10.10.2026 10:00:00')
  ut=rows(source());result=current(initial(ut,[]),rows(source(),future),rows(source()))
  self.assertFalse(any('9999' in str(r) for table in sheets(result).values() for r in table))

 def test_different_date_candidate_goes_to_add_without_inventing_pair(self):
  ut=rows(source());tax=rows(source(DOC.replace('02.07','02.08')))
  result=current(initial(ut,[]),ut,tax)
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),2)
  self.assertEqual(len(sheets(result)['Проверить вручную']),1)
  self.assertTrue(result.discrepancies)
  self.assertEqual(result.summaries[0].matched_documents,0)
  self.assertTrue(any('дата' in d.difference_summary.lower() for d in result.discrepancies))

 def test_different_warehouse_is_not_a_clean_pair(self):
  ut=rows(source());result=current(initial(ut,[]),ut,rows(source(wh='W2')))
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),2)
  self.assertEqual(len(sheets(result)['Проверить вручную']),1)
  self.assertTrue(result.discrepancies)
  self.assertEqual(result.summaries[0].matched_documents,0)

 def test_failed_current_source_cannot_confirm_old_action(self):
  ut=rows(source());result=current(initial(ut,[]),ut,[],source_errors={'БУХ2026':'тайм-аут'})
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
  self.assertGreater(len(sheets(result)['Проверить вручную']),1)
  self.assertTrue(any('тайм-аут' in d.difference_summary for d in result.discrepancies))

 def test_disappeared_source_is_not_inferred_resolved(self):
  ut=rows(source());result=current(initial(ut,[]),[],rows(source()))
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
  self.assertGreater(len(sheets(result)['Проверить вручную']),1)

 def test_duplicate_key_is_manual(self):
  ut=rows(source());result=current(initial(ut,[]),ut,rows(source(),source()))
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
  self.assertGreater(len(sheets(result)['Проверить вручную']),1)

 def test_unresolved_missing_remains_with_current_check_date(self):
  ut=rows(source());result=current(initial(ut,[]),ut,[])
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),2)
  self.assertIn('16.10.2026',sheets(result)['Довнести в БУХ'][1][2])

 def test_stocks_from_selected_period_are_preserved(self):
  ut=rows(source(),source('',qty=0,Период='31.07.2026 23:59:59',КоличествоКонечныйОстаток=1))
  tax=rows(source('',qty=0,Период='31.07.2026 23:59:59',КоличествоКонечныйОстаток=0))
  old=initial(ut,tax);before=[d for d in old.discrepancies if d.discrepancy_type==BALANCE_QUANTITY_MISMATCH]
  self.assertTrue(before)
  result=current(old,rows(source()),rows(source()))
  self.assertEqual([d for d in result.discrepancies if d.discrepancy_type==BALANCE_QUANTITY_MISMATCH],before)

 def test_new_item_discrepancy_within_found_pair_is_visible(self):
  ut=rows(source());result=current(initial(ut,[]),ut,rows(source(),source(code='B')))
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
  self.assertTrue(any(d.nomenclature_code=='B' for d in result.discrepancies))

 def test_distinct_organizations_do_not_resolve(self):
  ut=rows(source());result=current(initial(ut,[]),ut,rows(source(ОрганизацияИНН='222')))
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),2)
  self.assertEqual(len(sheets(result)['Проверить вручную']),1)
  self.assertTrue(result.discrepancies)
  self.assertEqual(result.summaries[0].matched_documents,0)


 def test_quantity_refresh_does_not_duplicate_existing_manual_item(self):
  ut=rows(source(qty=5),source(code='B',qty=2));tax=rows(source(qty=10))
  result=current(initial(ut,tax),ut,tax)
  manual=[d for d in result.discrepancies if d.nomenclature_code=='B']
  self.assertEqual(len(manual),1)



 def test_newly_internal_transfer_is_excluded_without_manual_case(self):
  doc='Перемещение товаров 7 от 02.07.2026 10:00:00'
  old_row=replace(rows(source(doc))[0],transfer_scope='external',sender_warehouse_code='W1',receiver_warehouse_code='W2',sender_accounting_warehouse='Основной',receiver_accounting_warehouse='Другой')
  now=replace(old_row,transfer_scope='internal',receiver_accounting_warehouse='Основной')
  result=current(initial([old_row],[]),[now],[])
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
  self.assertEqual(len(sheets(result)['Проверить вручную']),1)

 def test_tax_counterpart_of_current_internal_transfer_is_also_excluded(self):
  doc='Перемещение товаров 7 от 02.07.2026 10:00:00'
  tax=rows(source(doc));now=replace(tax[0],transfer_scope='internal',sender_accounting_warehouse='Основной',receiver_accounting_warehouse='Основной')
  result=current(initial([],tax),[now],tax)
  self.assertEqual(len(sheets(result)['Лишние в БУХ']),1)
  self.assertEqual(len(sheets(result)['Проверить вручную']),1)


class CurrentStateWorkflowTests(TestCase):
 def execute(self,*,today=TODAY,fail_current=False,matched_initial=False,include_headers=False):
  from unittest.mock import patch
  import main
  from app.config import OneCBaseConfig,Settings
  from app.models import Warehouse
  from app.onec_client import OneCClientError
  bases=[OneCBaseConfig(n,'https://internal.example.invalid/endpoint/export',base_id=b,warehouses_url='https://internal.example.invalid/endpoint/warehouse') for n,b in [('УТ','trade'),('БУХ2026','buh2026')]]
  settings=Settings(bases[0],[bases[1]]);calls=[]
  class Client:
   def fetch_warehouses(self,base):return [Warehouse('W1','Основной',accounting_name='Основной' if base.base_id=='trade' else '')]
   def fetch_rows(self,base,start,end,**kwargs):
    calls.append((base.base_id,start,end,kwargs))
    if end==TODAY and end!=date(2026,7,31) and fail_current and base.base_id!='trade':raise OneCClientError('тайм-аут проверки')
    if base.base_id=='trade' or end==TODAY or matched_initial:return rows(source())
    return rows(source('',qty=0))
  with patch('main.OneCClient',return_value=Client()),patch('main._current_date',return_value=today,create=True):
   response=main._reconcile(main.ReconcileRequest(start_date=date(2026,7,1),end_date=date(2026,7,31)),settings)
  if include_headers:return calls,workbook_rows(response.body),response.headers
  return calls,workbook_rows(response.body)

 def test_historical_actions_are_rechecked_until_today(self):
  calls,book=self.execute()
  self.assertEqual([(bid,end) for bid,_,end,_ in calls],[('trade',date(2026,7,31)),('buh2026',date(2026,7,31)),('trade',TODAY),('buh2026',TODAY)])
  self.assertEqual(len(book['Довнести в БУХ']),1)
  self.assertTrue(all('warehouse' not in kw for _,_,end,kw in calls if end==TODAY))

 def test_today_period_does_not_fetch_same_data_twice(self):
  calls,_=self.execute(today=date(2026,7,31))
  self.assertEqual(len(calls),2)

 def test_clean_period_does_not_need_additional_requests(self):
  calls,_=self.execute(matched_initial=True)
  self.assertEqual(len(calls),2)

 def test_failed_current_check_does_not_emit_create_action(self):
  _,book=self.execute(fail_current=True)
  self.assertEqual(len(book['Довнести в БУХ']),1)
  self.assertIn('тайм-аут проверки',str(book['Проверить вручную']))


 def test_failed_current_check_is_reported_in_response_header(self):
  _,_,headers=self.execute(fail_current=True,include_headers=True)
  self.assertEqual(headers['X-Unchecked-Base-Count'],'1')


class CurrentImportStateTests(TestCase):
 def imported(self,document,qty,*,tax=False,party='5'):
  from tests.test_import_organization_regressions import raw
  record=raw(document,qty,side='tax' if tax else 'trade',ПартияБух=party,Период='02.07.2026 10:00:00')
  return replace(rows(record)[0],warehouse_mapping_id='import')
 def run_case(self,ut,old_tax,new_tax):
  from app.current_state import refresh_recommendations
  old=reconcile(ut,{'БУХ2019':old_tax},'01.07.2026–31.07.2026',start_date=date(2026,7,1),end_date=date(2026,7,31))
  return refresh_recommendations(old,ut,{'БУХ2019':new_tax},checked_on=TODAY)
 def test_many_trade_receipts_resolved_by_current_party_group(self):
  ut=[self.imported('Поступление товаров UT1 от 02.07.2026 10:00:00',5),self.imported('Поступление товаров UT2 от 03.07.2026 10:00:00',5)]
  tax=[self.imported('Поступление товаров TAX1 от 02.08.2026 10:00:00',10,tax=True)]
  result=self.run_case(ut,[],tax)
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
  self.assertEqual(len(sheets(result)['Проверить вручную']),1)
 def test_current_import_difference_is_not_duplicated_for_each_order(self):
  ut=[self.imported('Поступление товаров UT1 от 02.07.2026 10:00:00',5),self.imported('Поступление товаров UT2 от 03.07.2026 10:00:00',5)]
  tax=[self.imported('Поступление товаров TAX1 от 02.08.2026 10:00:00',12,tax=True)]
  result=self.run_case(ut,[],tax);table=sheets(result)['Лишние в БУХ']
  self.assertEqual(len(table),2)
  r=dict(zip(table[0],table[1]));self.assertEqual([r[k] for k in ('Количество УТ','Количество БУХ','Разница БУХ − УТ')],['10','12','2'])
 def test_two_parties_within_one_source_document_remain_separate(self):
  doc='Поступление товаров UT1 от 02.07.2026 10:00:00'
  ut=[self.imported(doc,5,party='5'),self.imported(doc,6,party='6')]
  tax=[self.imported('Поступление товаров TAX1 от 02.08.2026 10:00:00',5,tax=True,party='5'),self.imported('Поступление товаров TAX2 от 03.08.2026 10:00:00',6,tax=True,party='6')]
  result=self.run_case(ut,[],tax)
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
  self.assertEqual(len(sheets(result)['Лишние в БУХ']),1)
  self.assertEqual(len(sheets(result)['Проверить вручную']),1)
 def test_conflicting_party_cannot_resolve_original_missing_group(self):
  ut=[self.imported('Поступление товаров UT1 от 02.07.2026 10:00:00',5)]
  tax=[replace(self.imported('Поступление товаров TAX1 от 02.08.2026 10:00:00',5,tax=True),party_comment='6')]
  result=self.run_case(ut,[],tax)
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
  self.assertGreater(len(sheets(result)['Проверить вручную']),1)
 def test_changed_party_without_confirmed_pair_does_not_resolve_by_empty_projection(self):
  from app.current_state import refresh_recommendations
  doc='Поступление товаров UT1 от 02.07.2026 10:00:00'
  original=reconcile([self.imported(doc,5)],{'БУХ2019':[]},'01.07.2026–31.07.2026')
  result=refresh_recommendations(original,[self.imported(doc,5,party='6')],
      {'БУХ2019':[self.imported('Поступление товаров TAX7 от 02.08.2026 10:00:00',5,tax=True,party='7')]},checked_on=TODAY)
  self.assertGreater(len(sheets(result)['Проверить вручную']),1)
  self.assertEqual(len(sheets(result)['Довнести в БУХ']),1)
