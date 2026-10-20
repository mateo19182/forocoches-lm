import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

if importlib.util.find_spec('anthropic'):
    from fcdata import label
else:
    label = None


@unittest.skipIf(label is None, 'Requires the Anthropic SDK')
class LabelTests(unittest.TestCase):
    def test_refusals_are_explicit_exclusions_and_collection_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'batches.json').write_text(json.dumps([dict(id='batch',name='review')]))
            (root/'batch.ids.json').write_text('[1]')
            (root/'batch.provenance.json').write_text(json.dumps({'1':dict(text_sha256='digest')}))
            msg = NS(stop_reason='refusal', model='test-model', content=[],
                     usage=NS(input_tokens=10,output_tokens=0,cache_read_input_tokens=0,cache_creation_input_tokens=0))
            result = NS(custom_id='t1',result=NS(type='succeeded',message=msg))
            with patch.object(label,'LABEL_DIR',root), patch.object(label,'STATE',root/'batches.json'), patch.object(label.anthropic,'Anthropic') as client:
                batches = client.return_value.messages.batches
                batches.retrieve.return_value.processing_status = 'ended'
                batches.results.return_value = [result]
                label.cmd_collect(NS(name='review'))
                label.cmd_collect(NS(name='review'))
            rows = [json.loads(line) for line in (root/'review.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['decision'],'exclude')
            self.assertEqual(rows[0]['text_sha256'],'digest')
            self.assertNotIn('tipo',rows[0])
            self.assertEqual(json.loads((root/'review.failed.json').read_text()),[])

    def test_inputs_are_not_mistaken_for_completed_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'threads.jsonl').write_text(json.dumps(dict(thread_id=1,text='Pending',label=None))+'\n')
            (root/'review.jsonl').write_text(json.dumps(dict(thread_id=2,tipo='historia',calidad='alta'))+'\n')
            (root/'msgbatch_test.ids.json').write_text('[3]')
            with patch.object(label,'LABEL_DIR',root):
                self.assertEqual(label.submitted_ids(),{2,3})


if __name__=='__main__':
    unittest.main()
