import importlib.util
import unittest

if importlib.util.find_spec('datasketch'):
    from fcdata.crawl_v3 import FOLLOWUP_MIN_CHARS, longest_own, thread_rows
else:
    thread_rows = None


def post(pid, position, author, text, reason='long'):
    return dict(id=pid, position=str(position), author=author, html=f'<td>{text}</td>', title='Mi historia +HD - Forocoches',
                posted_at='23-mar-2011, 05:16', reason=reason, page_url='url', observed_at=float(pid))


@unittest.skipIf(thread_rows is None, 'Run with .venv/bin/python for datasketch')
class CrawlDocsTests(unittest.TestCase):
    def test_pseudonymises_authors_mentions_and_drops_short_followups(self):
        story = 'Ayer Pepito_Grillo me contó algo increíble. ' * 60
        posts = [post(3, 4, 'Pepito_Grillo', 'gracias @Pepito_Grillo y @Desconocido.', 'op_followup'),
                 post(1, 1, 'Pepito_Grillo', story),
                 post(2, 2, 'Juanito99', 'Respuesta larga de Juanito99. ' * 80),
                 post(4, 6, 'Pepito_Grillo', 'Continúa la historia. ' * 20, 'op_followup')]
        rows, doc = thread_rows(7, posts)
        self.assertEqual([r['position'] for r in rows], [1, 2, 4, 6])
        self.assertEqual(rows[2]['text'], 'gracias @OP y @usuario.')
        self.assertNotIn('Pepito_Grillo', doc['text'])
        self.assertNotIn('Juanito99', doc['text'])
        self.assertTrue(doc['text'].startswith('TÍTULO: Mi historia +HD\n\n#1 OP: Ayer OP me contó'))
        self.assertIn('#2 forero_a: Respuesta larga de forero_a.', doc['text'])
        self.assertIn('#3 OP: Continúa', doc['text'])
        self.assertEqual(doc['post_ids'], [1, 2, 4])
        self.assertLess(rows[2]['own_chars'], FOLLOWUP_MIN_CHARS)
        self.assertEqual((doc['year'], doc['tags'], doc['has_op']), (2011, ['+hd'], True))

    def test_crawled_div_posts_keep_own_text_and_mask_quoted_nicks(self):
        quote = ('<div style="margin:20px; margin-top:5px; "><table><tr><td class="alt2"><div>Cita de <b>Ajeno_123</b>'
                 '<a href="showthread.php?p=99#post99">x</a></div><div style="font-style:italic">lo citado</div></td></tr></table></div>')
        html = f'<div id="post_message_5">{quote}Mi respuesta propia. Escrito originalmente por Fulanito<br/>Cita de Menganito [enlace showthread] hola</div>'
        p = post(5, 1, 'Autor_largo', '')
        p['html'] = html
        _, doc = thread_rows(9, [p])
        self.assertIn('[cita=forero_a]lo citado[/cita]', doc['text'])
        self.assertIn('Mi respuesta propia.', doc['text'])
        self.assertIn('Escrito originalmente por usuario', doc['text'])
        self.assertIn('Cita de usuario [enlace showthread]', doc['text'])
        self.assertNotIn('Fulanito', doc['text'])
        self.assertNotIn('Ajeno_123', doc['text'])

    def test_thread_without_op_does_not_call_a_reply_op(self):
        _, doc = thread_rows(8, [post(5, 3, 'Otro', 'Historia en una respuesta. ' * 90)])
        self.assertFalse(doc['has_op'])
        self.assertIn('#1 forero_a: Historia', doc['text'])
        self.assertTrue(longest_own(doc).startswith('Historia en una respuesta.'))


@unittest.skipIf(thread_rows is None, 'Run with .venv/bin/python for datasketch')
class MaskTests(unittest.TestCase):
    def test_mask_unmapped_keeps_pseudonyms(self):
        from fcdata.crawl_v3 import mask_unmapped
        text = '#2 forero_a: Cita de RealNick [enlace showthread] hola @OP y @@Pepe99, tod@s. Cita de forero_b [enlace showthread] y @forero_c'
        self.assertEqual(mask_unmapped(text), '#2 forero_a: Cita de usuario [enlace showthread] hola @OP y @usuario, tod@s. '
                                              'Cita de forero_b [enlace showthread] y @forero_c')

    def test_mask_unmapped_legacy_quote_header(self):
        from fcdata.crawl_v3 import mask_unmapped
        text = '#1 OP: Hola\n\n#2 forero_a: Cita de Bandal Yo de ti miraría\n\n#3 OP: Cita de forero_a Vale. Cita de Pepe no'
        self.assertEqual(mask_unmapped(text), '#1 OP: Hola\n\n#2 forero_a: Cita de usuario Yo de ti miraría\n\n'
                                              '#3 OP: Cita de forero_a Vale. Cita de Pepe no')


class YearHeaderTests(unittest.TestCase):
    def test_year_header_field(self):
        import random
        from fcdata.mix import header
        row = dict(tags=['+hd'], pages_bucket='unknown', year=2011)
        lab = dict(tipo='historia', calidad='alta')
        self.assertEqual(header(row, lab, random.Random(0), dropout=False, fields=('tipo', 'año')), 'tipo: historia | año: 2011\n\n')
        self.assertEqual(header(dict(row, year=None), lab, random.Random(0), dropout=False, fields=('tipo', 'año')), 'tipo: historia\n\n')

    @unittest.skipIf(thread_rows is None, 'Run with .venv/bin/python for datasketch')
    def test_year_index_uses_nearest_thread(self):
        import json, tempfile
        from pathlib import Path
        from fcdata.crawl_v3 import YearIndex
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'docs.jsonl'
            path.write_text(''.join(json.dumps(dict(thread_id=t, year=y)) + '\n' for t, y in ((10, 2003), (100, 2010), (200, None))))
            year_of = YearIndex(path)
            self.assertEqual([year_of(1), year_of(50), year_of(60), year_of(999)], [2003, 2003, 2010, 2010])


if __name__ == '__main__':
    unittest.main()
