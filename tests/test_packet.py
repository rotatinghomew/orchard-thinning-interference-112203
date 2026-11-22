"""核对演练资料的公开输入约定。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from inspect_packet import read_packet


class PacketTest(unittest.TestCase):
    def test_related_records(self):
        doc = read_packet(Path("资料/演练.json"))
        plots={row['plot'] for row in doc['plots']}
        self.assertTrue(all({row['left'],row['right']} <= plots for row in doc['adjacency']))
        for block in {row['block'] for row in doc['draw_options']}:
            choices=[row for row in doc['draw_options'] if row['block']==block]
            self.assertTrue(abs(sum(row['probability'] for row in choices)-1)<1e-8)
            self.assertTrue(sum(row['realized'] for row in choices)==1)
            self.assertTrue(all(set(row['offered']) <= set(row['eligible']) <= plots for row in choices))
        self.assertTrue({row['plot'] for row in doc['picks'] + doc['notices']} <= plots)
        self.assertTrue(min(row['known'] for row in doc['picks'] if row['final']) < max(row['known'] for row in doc['picks'] if row['final']))

    def test_final_pick_can_follow_notice(self):
        packet = read_packet(Path("资料/演练.json"))
        acknowledged = {row['plot']: row['issue'] for row in packet['notices']
                        if row['status'] == 'acknowledged'}
        self.assertTrue(any(row['final'] and row['plot'] in acknowledged
                            and acknowledged[row['plot']] < row['known']
                            for row in packet['picks']))

    def test_reject_wrong_container(self):
        with TemporaryDirectory() as folder:
            packet = Path(folder) / "unexpected.json"
            packet.write_text(json.dumps({"invalid": 3}), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_packet(packet)
