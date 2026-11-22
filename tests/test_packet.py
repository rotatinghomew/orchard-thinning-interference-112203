"""核对挂牌和库存资料的标识、来源与时点。"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from inspect_packet import read_packet


class PacketTest(unittest.TestCase):
    def setUp(self):
        self.packet = read_packet(Path("资料/演练.json"))

    def test_lots_and_executions_have_source_lots(self):
        stock = {row['id'] for row in self.packet['stock']}
        lots = {row['id']: row for row in self.packet['lots']}
        for item in lots.values():
            self.assertTrue({part['stock'] for part in item['composition']} <= stock)
            self.assertTrue(all(lots[parent]['date'] <= item['date'] for parent in item['parents']))
        self.assertTrue(all(row['lot'] in lots for row in self.packet['deliveries'] + self.packet['holds']))
        self.assertTrue(any(len(row['composition']) > 1 for row in lots.values()))

    def test_bid_disclosure_is_distinct_from_auction_opening(self):
        lots = {row['id']: row for row in self.packet['lots']}
        bids = {row['id']: row for row in self.packet['bids']}
        for release in self.packet['bid_releases']:
            bid = bids[release['bid']]
            self.assertIsNone(bid['known'])
            self.assertGreater(release['known'], lots[bid['lot']]['date'])
        self.assertTrue({'below', 'no_bid', 'canceled', 'not_opened', 'sold'}
                        <= {row['status'] for row in lots.values()})

    def test_reject_wrong_container(self):
        with TemporaryDirectory() as folder:
            packet = Path(folder) / "unexpected.json"
            packet.write_text('{"invalid": 3}', encoding="utf-8")
            with self.assertRaises(ValueError):
                read_packet(packet)
