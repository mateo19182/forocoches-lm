import unittest
import importlib.util
if importlib.util.find_spec('tokenizers'):
    from tokenizers import Tokenizer, models, pre_tokenizers, trainers
    from fcdata.mix_v2 import chunks
else:
    Tokenizer = None


@unittest.skipIf(Tokenizer is None, 'Run with .venv-train/bin/python for tokenizers')
class ChunkTests(unittest.TestCase):
    def test_preserves_long_story_and_replies_with_context(self):
        tok = Tokenizer(models.BPE(unk_token='[UNK]'))
        tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
        tok.train_from_iterator(['Una historia Ayer me pasó algo increíble. Menuda historia.'],
                                trainers.BpeTrainer(vocab_size=280, special_tokens=['[UNK]'],
                                                    initial_alphabet=pre_tokenizers.ByteLevel.alphabet()))
        text='TÍTULO: Una historia\n\n#1 OP: '+('Ayer me pasó algo increíble. '*900)+'\n\n#2 forero_a: Menuda historia.'
        result=list(chunks(text,'tipo: historia\n\n',tok,max_tokens=512))
        self.assertGreater(len(result),1)
        self.assertEqual(''.join(doc['completion'] for doc,_ in result),text)
        self.assertTrue(all(len(tok.encode(d['prompt']+d['completion'],add_special_tokens=False).ids)+2<=512 for d,_ in result))
        self.assertGreater(len(result[1][0]['prompt']),len('tipo: historia\n\n'))


@unittest.skipIf(Tokenizer is None, 'Run with .venv-train/bin/python for tokenizers')
class BudgetTests(unittest.TestCase):
    def test_parse_budget(self):
        import argparse
        from fcdata.mix_v2 import parse_budget
        self.assertEqual(parse_budget('stories=all'), ('stories', None))
        self.assertEqual(parse_budget('replay=6e7'), ('replay', 6e7))
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_budget('poems=10')


if __name__ == '__main__':unittest.main()
