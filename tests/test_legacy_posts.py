import unittest

import collect
from test_collect import URL


class LegacyPostTests(unittest.TestCase):
    def test_logged_in_table_layout_with_self_closing_message_div(self):
        html = '''<title>Legacy thread</title>
        <table id="post123" class="tborder">
          <tr><td class="thead">Hoy, 15:07</td><td class="thead">#
            <a id="postcount123">1</a></td></tr>
          <tr><td class="alt2"><a class="bigusername" href="member.php?u=4">Author</a></td>
            <td id="td_post_123"><div id="post_message_123" />Hola<br>mundo
              <a href="showthread.php?p=12#post12">quote</a>
              <img src="example.png"></div></td></tr>
        </table>'''
        post = collect.parse(html, URL)['posts'][0]
        self.assertEqual(post['text'], 'Hola\nmundo\nquote')
        self.assertEqual(post['author'], 'Author')
        self.assertEqual(post['author_id'], 4)
        self.assertEqual(post['position'], 1)
        self.assertEqual(post['posted_at_raw'], 'Hoy, 15:07')
        self.assertEqual(post['quote_post_ids'], [12])
        self.assertEqual(post['images'][0]['src'], 'example.png')
        self.assertIn('Hola', post['html'])


if __name__ == '__main__':
    unittest.main()
