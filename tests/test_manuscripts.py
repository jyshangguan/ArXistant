import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import arxistant_manuscripts as manuscripts
import arxistant_pdf_annotations as pdf
try:
    import fitz
except ImportError:
    fitz = None


@unittest.skipUnless(fitz, 'PyMuPDF is required for review')
class ManuscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'paper.pdf')
        with fitz.open() as doc:
            for i in range(3):
                page = doc.new_page()
                page.insert_text((60, 90), f'{i+1}. Section {i+1}')
                page.insert_text((60, 120), f'The result are clear on page {i+1}.')
                page.insert_text((60, 160), 'A repeated phrase. A repeated phrase.')
            doc[0].add_text_annot((20, 20), 'Existing reviewer note')
            doc.save(self.path)
        self.docs = {'local-a': {'path': self.path, 'title': 'Test manuscript'}}
        self.calls = []
        self.response = {'answer': 'Reviewed.', 'findings': [], 'proposals': []}
        self.service = manuscripts.Manuscripts(os.path.join(self.tmp.name, 'db.sqlite'),
                                               self.docs.get, self.complete, lambda q: [])
        self.service.launch = lambda job: None
        self.service.open('local-a')

    def complete(self, messages):
        self.calls.append(messages)
        return json.dumps(self.response)

    def start(self, **options):
        return self.service.start('local-a', {'mode': 'review', 'categories': ['language'], **options})

    def finding(self, page=1):
        return {'page': page, 'block_id': f'p{page}b1', 'quote': f'The result are clear on page {page}.',
                'category': 'language', 'severity': 'error', 'comment': 'Use subject–verb agreement.',
                'replacement': f'The results are clear on page {page}.'}

    def test_full_review_covers_every_page_and_survives_reload(self):
        job = self.start()
        self.service.run(job['id'])
        state = self.service.state('local-a')
        self.assertEqual(state['jobs'][0]['status'], 'complete')
        self.assertEqual([a['pages'] for a in state['jobs'][0]['answers']], [[1], [2], [3]])
        reopened = manuscripts.Manuscripts(self.service.db_path, self.docs.get, self.complete, lambda q: [])
        self.assertEqual(reopened.state('local-a')['jobs'][0]['cursor'], 3)
        self.assertEqual(len(self.calls), 4)

    def test_highlights_export_preserves_source_and_original_comments(self):
        before = Path(self.path).read_bytes()
        self.response['findings'] = [self.finding()]
        job = self.start(pages='1')
        self.service.run(job['id'])
        finding = [f for f in self.service.state('local-a')['findings'] if f.get('origin') != 'existing'][0]
        self.assertEqual(finding['placement'], 'highlight')
        payload = self.service.export('local-a')
        self.assertEqual(Path(self.path).read_bytes(), before)
        with fitz.open(stream=payload, filetype='pdf') as doc:
            annotations = list(doc[0].annots())
            self.assertEqual(len(annotations), 2)
            self.assertEqual(annotations[0].info['content'], 'Existing reviewer note')
            self.assertIn('agreement', annotations[1].info['content'])
            self.assertEqual(annotations[1].info['title'], 'ArXistant')
        with fitz.open(stream=self.service.export('local-a'), filetype='pdf') as doc:
            self.assertEqual(len(list(doc[0].annots())), 2)

    def test_ambiguous_phrase_becomes_page_note(self):
        result = pdf.locate(self.path, 1, 'A repeated phrase.', 'p1b2')
        self.assertEqual(result['placement'], 'page-note')
        self.assertFalse(result['quads'])

    def test_pdf_module_rechecks_user_site_after_initial_import_failure(self):
        attempts = [ImportError('not visible yet'), ImportError('not visible yet'),
                    fitz]
        with tempfile.TemporaryDirectory() as user_site, \
                mock.patch.object(pdf.importlib, 'import_module', side_effect=attempts), \
                mock.patch.object(pdf.site, 'getusersitepackages', return_value=user_site), \
                mock.patch.object(pdf.site, 'addsitedir') as add_site:
            self.assertIs(pdf.pdf_module(), fitz)
        add_site.assert_called_once_with(user_site)

    def test_pdf_module_error_names_interpreter_and_install_command(self):
        with mock.patch.object(pdf.importlib, 'import_module',
                               side_effect=ImportError('missing extension')):
            with self.assertRaisesRegex(ValueError, 'pip install --user PyMuPDF') as error:
                pdf.pdf_module()
        self.assertIn(sys.executable, str(error.exception))

    def test_focused_discussion_preserves_history_without_auto_annotations(self):
        self.response = {'answer': 'Here are two options.', 'findings': [], 'proposals': [
            {'page': 1, 'block_id': 'p1b1', 'original': self.finding()['quote'],
             'replacement': 'The results are clear.', 'reason': 'Agreement and concision.'}]}
        job = self.start(mode='discuss', pages='1', message='Polish this paragraph', quote=self.finding()['quote'])
        self.service.run(job['id'])
        state = self.service.state('local-a')
        self.assertFalse([f for f in state['findings'] if f.get('origin') != 'existing'])
        proposal = state['proposals'][0]
        self.service.action({'action': 'choose', 'id': proposal['id']})
        followup = self.start(mode='discuss', pages='1', message='Explain the first option')
        self.service.run(followup['id'])
        payload = json.loads(self.calls[-1][-1]['content'])
        self.assertEqual(payload['conversation'][0]['request'], 'Polish this paragraph')
        self.assertEqual(payload['proposals'][0]['status'], 'chosen')
        self.service.action({'action': 'annotate', 'id': proposal['id']})
        self.service.action({'action': 'annotate', 'id': proposal['id']})
        self.assertEqual(len([f for f in self.service.state('local-a')['findings'] if f.get('origin') != 'existing']), 1)

    def test_invalid_locations_are_reported_not_guessed(self):
        self.response['findings'] = [self.finding(2), {**self.finding(), 'quote': 'invented quote'}]
        job = self.start(pages='1')
        self.service.run(job['id'])
        state = self.service.state('local-a')
        self.assertFalse([f for f in state['findings'] if f.get('origin') != 'existing'])
        self.assertEqual(len(state['jobs'][0]['warnings']), 2)

    def test_comment_revision_conflicts_and_selected_exports(self):
        self.response['findings'] = [self.finding()]
        job = self.start(pages='1'); self.service.run(job['id'])
        f = [f for f in self.service.state('local-a')['findings'] if f.get('origin') != 'existing'][0]
        self.service.action({'action': 'finding', 'id': f['id'], 'revision': 1, 'comment': 'Author correction', 'status': 'resolved'})
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.action({'action': 'finding', 'id': f['id'], 'revision': 1, 'comment': 'stale'})
        with fitz.open(stream=self.service.export('local-a'), filetype='pdf') as doc:
            self.assertEqual(len(list(doc[0].annots())), 1)
        with fitz.open(stream=self.service.export('local-a', [f['id']]), filetype='pdf') as doc:
            self.assertEqual(len(list(doc[0].annots())), 2)
        with self.assertRaises(ValueError):
            self.service.export('local-a', ['wrong-id'])

    def test_cancel_during_model_call_discards_late_results_and_resume(self):
        job = self.start()
        def cancel(messages):
            self.service.action({'action': 'cancel', 'id': job['id']})
            return json.dumps({'answer': 'late', 'findings': [self.finding()]})
        self.service.complete = cancel
        self.service.run(job['id'])
        state = self.service.state('local-a')
        self.assertEqual(state['jobs'][0]['status'], 'cancelled')
        self.assertFalse([f for f in state['findings'] if f.get('origin') != 'existing'])
        self.service.complete = self.complete
        self.service.action({'action': 'resume', 'id': job['id']})
        self.service.run(job['id'])
        self.assertEqual(self.service.state('local-a')['jobs'][0]['cursor'], 3)

    def test_resume_after_failure_does_not_repeat_completed_batches(self):
        job = self.start()
        count = [0]
        def complete(messages):
            count[0] += 1
            if count[0] == 2:
                raise RuntimeError('provider rejected request')
            return json.dumps(self.response)
        self.service.complete = complete
        self.service.run(job['id'])
        self.assertEqual(self.service.state('local-a')['jobs'][0]['cursor'], 1)
        self.service.complete = self.complete
        self.service.action({'action': 'resume', 'id': job['id']})
        self.service.run(job['id'])
        self.assertEqual(len(self.calls), 3)

    def test_transient_timeout_retries_without_truncating_full_review(self):
        job = self.start()
        count = [0]
        def complete(messages):
            count[0] += 1
            if count[0] == 2:
                raise TimeoutError('The read operation timed out')
            return json.dumps(self.response)
        self.service.complete = complete
        with mock.patch.object(manuscripts.time, 'sleep') as sleep:
            self.service.run(job['id'])
        saved = self.service.state('local-a')['jobs'][0]
        self.assertEqual(saved['status'], 'complete')
        self.assertEqual(saved['cursor'], saved['total'])
        sleep.assert_called_once_with(1)

    def test_review_payload_limits_context_to_adjacent_pages(self):
        job = self.start()
        self.service.run(job['id'])
        first_payload = json.loads(self.calls[0][-1]['content'])

        self.assertEqual([p['page'] for p in first_payload['neighbor_context']], [2])
        self.assertTrue(all(set(p) == {'page', 'text'} for p in first_payload['neighbor_context']))

    def test_reference_context_is_limited_to_citations_in_chunk(self):
        target = {'page': 1, 'blocks': [{'text': 'The method follows Smith et al. 2020.'}]}
        references = ('Jones, A. 2019, Other work\nDetails for Jones\n'
                      'Smith, B. 2020, Relevant work\nDOI 10.1/example\n'
                      'Taylor, C. 2021, Another work')

        excerpt = manuscripts.reference_excerpt(target, references)

        self.assertIn('Smith, B. 2020', excerpt)
        self.assertNotIn('Jones, A. 2019', excerpt)

    def test_reference_excerpt_matches_entries_split_across_lines(self):
        # PDF extraction splits entries across lines and interleaves margin
        # line numbers, so the author and the year land on different lines.
        target = {'page': 5, 'blocks': [{'text': 'We compare with Hwang et al.\n189\n2007 throughout.'}]}
        references = ('Greene, J. E., & Ho, L. C. 2004, ApJ, 610, 722\n'
                      'Hwang, H. S., Serjeant, S., Lee, M. G., &\n'
                      'White, G. J. 2007, MNRAS, 375, 115\n'
                      'Kong, X. 2018, ApJ, 5, 6')

        excerpt = manuscripts.reference_excerpt(target, references)

        self.assertIn('Hwang, H. S., Serjeant', excerpt)
        self.assertIn('White, G. J. 2007', excerpt)
        self.assertNotIn('Greene, J. E.', excerpt)

    def test_reference_excerpt_matches_dash_continuation_entries(self):
        target = {'page': 2, 'blocks': [{'text': 'As shown by Greene & Ho 2005a.'}]}
        references = ('Greene, J. E., & Ho, L. C. 2004, ApJ, 610, 722\n'
                      '\u2014. 2005a, ApJ, 630, 122\n'
                      'Kong, X. 2018, ApJ, 5, 6')

        excerpt = manuscripts.reference_excerpt(target, references)

        self.assertIn('\u2014. 2005a', excerpt)

    def test_reference_excerpt_uses_citations_from_the_request(self):
        target = {'page': 1, 'blocks': [{'text': 'A passage without any citations.'}]}
        references = 'Smith, B. 2020, Relevant work\nDetails for Smith'

        excerpt = manuscripts.reference_excerpt(
            target, references, extra='Is the cited paper Smith 2020 in the reference list?')

        self.assertIn('Smith, B. 2020', excerpt)

    def test_reference_excerpt_anchors_citations_split_across_chunks(self):
        # The chunk holds only the author of a citation whose year sits in a
        # neighboring chunk; the whole-document pair set must still select it.
        target = {'page': 3, 'blocks': [{'text': 'counterparts (e.g., Hwang et al.'}]}
        references = ('Hwang, H. S., Serjeant, S., Lee, M. G., &\n'
                      'White, G. J. 2007, MNRAS, 375, 115\n'
                      'Kong, X. 2018, ApJ, 5, 6')

        excerpt = manuscripts.reference_excerpt(
            target, references, document_citations={('Hwang', '2007')})

        self.assertIn('Hwang, H. S., Serjeant', excerpt)

    def test_reference_excerpt_reports_truncation(self):
        entries = '\n'.join(
            f'Au{chr(65 + i // 26)}{chr(65 + i % 26)}, X. 2010, ApJ, {700 + i}, {i}, doi:10.1/x{i}'
            for i in range(120))
        target = {'page': 1, 'blocks': [{'text': 'See ' + ' '.join(
            f'Au{chr(65 + i // 26)}{chr(65 + i % 26)} 2010;' for i in range(120))}]}

        excerpt = manuscripts.reference_excerpt(target, entries, limit=3500)

        self.assertIn('AuAA, X. 2010', excerpt)
        self.assertNotIn('AuZZ, X. 2010', excerpt)
        self.assertIn('[Reference excerpt truncated', excerpt)

    def make_reference_pdf(self):
        """A two-column manuscript with a line-numbered reference list."""
        path = os.path.join(self.tmp.name, 'twocolumn.pdf')
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((60, 55), 'Body text above the reference heading.')
            page.insert_text((60, 80), 'References')
            for y, text in ((110, 'Smith, A. 2020, ApJ, 1, 2'), (125, 'doi:10.1/smith'),
                            (140, 'Jones, B. 2019, MNRAS, 3, 4')):
                page.insert_text((60, y), text)
            for y, text in ((110, 'Taylor, C. 2021, A&A, 5, 6'), (125, 'doi:10.1/taylor'),
                            (140, 'White, D. 2018, AJ, 7, 8')):
                page.insert_text((320, y), text)
            for y in (110, 125, 140):
                page.insert_text((290, y), str(y))
            page.insert_text((557, 43), '1')
            doc.save(path)
        return path

    def test_reference_section_restores_column_reading_order(self):
        path = self.make_reference_pdf()
        text = manuscripts.reference_section(pdf.inspect_pdf(path)['pages'])

        self.assertIn('References', text)
        self.assertNotIn('Body text above', text)
        self.assertNotIn('\n110\n', text)
        order = [text.index(name) for name in ('Smith', 'Jones', 'Taylor', 'White')]
        self.assertEqual(order, sorted(order))

    def test_prepare_supplies_deinterleaved_references_for_discussion(self):
        self.docs['local-2'] = {'path': self.make_reference_pdf(), 'title': 'Two column'}
        _, tasks, _, references = self.service.prepare(
            'local-2', {'mode': 'discuss', 'message': 'Is Taylor 2021 cited correctly?'})

        excerpt = manuscripts.reference_excerpt(
            tasks[0], references, extra='Is Taylor 2021 cited correctly?')

        self.assertIn('Taylor, C. 2021, A&A, 5, 6', excerpt)
        self.assertIn('doi:10.1/taylor', excerpt)

    def test_reference_list_is_not_cut_short(self):
        path = os.path.join(self.tmp.name, 'longrefs.pdf')
        with fitz.open() as doc:
            header = True
            y, page = 60, None
            for i in range(300):
                if page is None or y > 580:
                    page = doc.new_page()
                    y = 60
                    if header:
                        page.insert_text((60, y), 'References', fontsize=8)
                        y += 20
                        header = False
                name = 'Au' + chr(65 + i // 26) + chr(65 + i % 26)
                tail = 'ZZZfinal' if i == 299 else 'x' * 60
                page.insert_text((60, y), f'{name}, X. 2010, ApJ, {700 + i}, {i}, {tail}', fontsize=8)
                y += 12
            doc.save(path)
        text = manuscripts.reference_section(pdf.inspect_pdf(path)['pages'])

        self.assertGreater(len(text), 22000)
        self.assertIn('ZZZfinal', text)

    def test_visual_pages_are_detected_from_captions(self):
        self.assertTrue(manuscripts.has_visual_target(
            {'page': 3, 'blocks': [{'text': 'Figure 2. Comparison of measurements.'}]}))
        self.assertFalse(manuscripts.has_visual_target(
            {'page': 2, 'blocks': [{'text': 'The measurements agree within uncertainty.'}]}))

    def test_whole_paper_queue_groups_small_pages_but_focused_review_does_not(self):
        index, tasks, _, _ = self.service.prepare(
            'local-a', {'mode': 'review', 'categories': ['language']})

        whole = manuscripts.review_queue(
            tasks, index, {'whole': True, 'categories': ['language']}, 'review')
        focused = manuscripts.review_queue(
            tasks, index, {'pages': '1-3', 'categories': ['language']}, 'review')

        self.assertEqual([[p['page'] for p in task['pages']] for task in whole], [[1, 2], [3]])
        self.assertEqual([[p['page'] for p in task['pages']] for task in focused], [[1], [2], [3]])

    def test_bad_json_fails_after_one_repair(self):
        calls = []
        self.service.complete = lambda messages: calls.append(messages) or 'not json'
        job = self.start(pages='1'); self.service.run(job['id'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.service.state('local-a')['jobs'][0]['status'], 'failed')

    def test_vision_fallback_reports_missing_visual_coverage(self):
        def complete(messages):
            if isinstance(messages[-1]['content'], list):
                raise RuntimeError('images unsupported')
            return json.dumps(self.response)
        self.service.complete = complete
        job = self.start(pages='1', categories=['figure'], vision=True)
        self.service.run(job['id'])
        job = self.service.state('local-a')['jobs'][0]
        self.assertEqual(job['status'], 'complete')
        self.assertTrue(any('Image request failed' in w for w in job['warnings']))

    def test_source_hash_and_version_boundaries(self):
        state = self.service.state('local-a')
        second = os.path.join(self.tmp.name, 'second.pdf')
        with fitz.open(self.path) as doc:
            doc[0].insert_text((60, 200), 'Changed draft')
            doc.save(second)
        self.docs['local-b'] = {'path': second, 'title': 'New draft'}
        new = self.service.open('local-b', state['workspace']['id'])
        self.assertEqual(len(new['versions']), 2)
        self.assertFalse([f for f in new['findings'] if f.get('origin') != 'existing'])
        with open(self.path, 'ab') as stream: stream.write(b'\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.export('local-a')

    def test_rotated_cropped_page_geometry(self):
        rotated = os.path.join(self.tmp.name, 'rotated.pdf')
        with fitz.open(self.path) as doc:
            doc[0].set_cropbox(fitz.Rect(20, 30, 570, 810))
            doc[0].set_rotation(90)
            doc.save(rotated)
        index = pdf.inspect_pdf(rotated)
        placement = pdf.locate(rotated, 1, self.finding()['quote'], 'p1b1')
        self.assertTrue(placement['quads'])
        self.assertTrue(all(0 <= v <= 1 for r in placement['view_rects'] for v in r))
        payload = pdf.export_pdf(rotated, [{**self.finding(), **placement, 'id': 'test'}], index['hash'])
        with fitz.open(stream=payload, filetype='pdf') as doc:
            self.assertEqual(doc[0].rotation, 90)

    def test_more_than_200_findings_preserved(self):
        placement = pdf.locate(self.path, 1, self.finding()['quote'], 'p1b1')
        findings = [{**self.finding(), **placement, 'id': str(i)} for i in range(205)]
        payload = pdf.export_pdf(self.path, findings, pdf.inspect_pdf(self.path)['hash'])
        with fitz.open(stream=payload, filetype='pdf') as doc:
            self.assertEqual(len(list(doc[0].annots())), 206)

    def test_named_section_and_natural_request_scope(self):
        _, tasks, _, _ = self.service.prepare('local-a', {'mode': 'discuss', 'message': 'Polish section 2'})
        self.assertEqual([p['page'] for p in tasks[0]['pages']], [2])
        _, tasks, _, _ = self.service.prepare('local-a', {'mode': 'review', 'pages': 'section 3'})
        self.assertEqual([p['page'] for p in tasks], [3])

    def test_selected_passage_cannot_silently_expand(self):
        self.response['findings'] = [self.finding()]
        job = self.start(mode='discuss', pages='1', quote='A repeated phrase.', message='Review only this selected phrase')
        self.service.run(job['id'])
        state = self.service.state('local-a')
        self.assertFalse([f for f in state['findings'] if f.get('origin') != 'existing'])
        self.assertTrue(any('outside the selected passage' in w for w in state['jobs'][0]['warnings']))

    def test_existing_comment_import_and_no_duplicate_export(self):
        state = self.service.state('local-a')
        self.assertEqual(len(state['findings']), 1)
        self.assertEqual(state['findings'][0]['origin'], 'existing')
        self.service.open('local-a')
        self.assertEqual(len(self.service.state('local-a')['findings']), 1)
        with fitz.open(stream=self.service.export('local-a'), filetype='pdf') as doc:
            self.assertEqual(len(list(doc[0].annots())), 1)

    def test_update_active_comment_preserves_history_and_concurrent_edit(self):
        original = self.service.state('local-a')['findings'][0]
        self.response = {'answer': 'Updated.', 'finding_updates': [{'id': original['id'], 'comment': 'Revised comment'}]}
        job = self.start(mode='discuss', pages='1', finding_id=original['id'], message='Update this comment')
        self.service.run(job['id'])
        updated = self.service.state('local-a')['findings'][0]
        self.assertEqual(updated['comment'], 'Revised comment')
        self.assertEqual(updated['revision'], 2)
        self.assertEqual(updated['history'][0]['comment'], original['comment'])
        def concurrent(messages):
            self.service.action({'action': 'finding', 'id': original['id'], 'revision': 2, 'comment': 'Author edit'})
            return json.dumps(self.response)
        self.service.complete = concurrent
        job = self.start(mode='discuss', pages='1', finding_id=original['id'], message='Make it clearer')
        self.service.run(job['id'])
        self.assertEqual(self.service.state('local-a')['findings'][0]['comment'], 'Author edit')

    def test_carry_comment_only_to_validated_same_manuscript_version(self):
        self.response['findings'] = [self.finding()]
        job = self.start(pages='1'); self.service.run(job['id'])
        state = self.service.state('local-a')
        f = next(f for f in state['findings'] if f.get('origin') != 'existing')
        newpath = os.path.join(self.tmp.name, 'new.pdf')
        with fitz.open(self.path) as doc:
            doc.new_page(pno=0)
            doc.save(newpath)
        self.docs['local-b'] = {'path': newpath, 'title': 'Draft 2'}
        self.service.open('local-b', state['workspace']['id'])
        carried = self.service.action({'action': 'carry', 'id': f['id'], 'document': 'local-b'})
        self.assertEqual(carried['page'], 2)
        self.assertEqual(carried['placement'], 'highlight')
        again = self.service.action({'action': 'carry', 'id': f['id'], 'document': 'local-b'})
        self.assertEqual(again['id'], carried['id'])
        self.assertEqual(f['page'], 1)

    def test_citation_source_excerpts_have_provenance_and_bounded_context(self):
        responses = iter([{'answer': 'Internal citation review', 'findings': []},
                          {'answer': 'Citation unverified', 'findings': []},
                          {'answer': 'Summary'}])
        self.service.complete = lambda messages: self.calls.append(messages) or json.dumps(next(responses))
        self.service.search = lambda q: [{'title': 'Public title', 'url': 'https://arxiv.org/abs/2001.00001',
                                         'source_paragraphs': [{'locator': 'paragraph 3', 'text': 'The result is discussed here.'}]}]
        job = self.start(pages='1', categories=['citation'], lookup=True)
        with mock.patch.object(manuscripts, 'reference_excerpt',
                               return_value='Smith, A. 2020, Public title'):
            self.service.run(job['id'])
        job = self.service.state('local-a')['jobs'][0]
        self.assertEqual(job['status'], 'complete')
        source = job['evidence'][0]['records'][0]
        self.assertEqual(source['source_excerpts'][0]['locator'], 'paragraph 3')
        self.assertNotIn('source_paragraphs', source)

    def test_delete_running_document_commits_cancellation(self):
        job = self.start()
        self.service.workers.add(job['id'])
        with self.assertRaisesRegex(ValueError, 'cancellation requested'):
            self.service.delete_document('local-a')
        self.assertEqual(self.service.state('local-a')['jobs'][0]['status'], 'cancelled')
        self.service.workers.clear()
        self.service.delete_document('local-a')
        self.assertFalse(self.service.state('local-a')['opened'])


@unittest.skipUnless(fitz, 'PyMuPDF required')
class ManuscriptHTTPTests(unittest.TestCase):
    def test_http_workspace_review_download_and_asset(self):
        import urllib.request
        from http.server import ThreadingHTTPServer
        import arxiv_db_server as server
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, 'source.pdf')
            with fitz.open() as doc:
                p = doc.new_page(); p.insert_text((60, 90), 'A synthetic manuscript for testing.')
                doc.save(path)
            service = manuscripts.Manuscripts(os.path.join(temp, 'db.sqlite'),
                                               lambda key: {'path': path, 'title': 'Test'} if key == 'local-test' else None,
                                               lambda messages: json.dumps({'answer': 'Review complete', 'findings': []}), lambda q: [])
            service.launch = lambda key: service.run(key)
            with mock.patch.object(server, 'manuscript_service', return_value=service):
                http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
                thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
                try:
                    base = 'http://127.0.0.1:' + str(http.server_port)
                    def post(value):
                        request = urllib.request.Request(base + '/api/manuscripts', data=json.dumps(value).encode(), headers={'Content-Type': 'application/json'})
                        with urllib.request.urlopen(request) as response:
                            return json.load(response)
                    self.assertTrue(post({'action': 'open', 'document': 'local-test'})['data']['opened'])
                    post({'action': 'start', 'document': 'local-test', 'options': {'mode': 'review', 'categories': ['language']}})
                    with urllib.request.urlopen(base + '/api/manuscripts/state?document=local-test') as response:
                        self.assertEqual(json.load(response)['data']['jobs'][0]['status'], 'complete')
                    with urllib.request.urlopen(base + '/api/manuscripts/export?document=local-test') as response:
                        self.assertIn('attachment', response.headers['Content-Disposition'])
                        self.assertTrue(response.read().startswith(b'%PDF'))
                    with urllib.request.urlopen(base + '/chat.html') as response:
                        self.assertIn(b'/assets/manuscripts.js', response.read())
                    with urllib.request.urlopen(base + '/assets/manuscripts.js') as response:
                        self.assertIn('javascript', response.headers['Content-Type'])
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post({'action': 'open', 'document': '../../secret.pdf'})
                    self.assertEqual(error.exception.code, 400)
                finally:
                    http.shutdown(); http.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
