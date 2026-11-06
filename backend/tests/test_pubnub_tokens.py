import unittest
from unittest.mock import Mock, patch

from services.door_events import renew_reader
from services.pubnub_tokens import LockerTokens


class TokenRenewalTests(unittest.TestCase):
    def test_device_reuses_token_until_refresh_window(self):
        with patch('services.pubnub_tokens.PubNub'), patch.dict('os.environ', {
            'PUBNUB_SUBSCRIBE_KEY': 'sub', 'PUBNUB_PROBE_PUBLISH_KEY': 'pub', 'PUBNUB_SECRET_KEY': 'secret'
        }):
            issuer = LockerTokens()
        with patch.object(issuer, 'grant', side_effect=['first', 'second']) as grant:
            for now, expected in [(100, 'first'), (2799, 'first'), (2800, 'second')]:
                with patch('services.pubnub_tokens.monotonic', return_value=now):
                    self.assertEqual(issuer.device()['token'], expected)
            self.assertEqual(grant.call_count, 2)
            grant.assert_called_with('unistow-locker-4', 'unistow.locker-4.commands', 'unistow.locker-4.events')

    def test_server_recovers_from_failed_renewal(self):
        reader, issuer, stopped = Mock(), Mock(), Mock()
        issuer.reader.side_effect = ['first', RuntimeError(), 'second']
        stopped.is_set.side_effect = [False, False, False, True]
        renew_reader(reader, issuer, stopped)
        self.assertEqual([c.args[0] for c in stopped.wait.call_args_list], [2700, 30, 2700])
        self.assertEqual([c.args[0] for c in reader.set_token.call_args_list], ['first', 'second'])
        self.assertEqual(reader.subscribe.return_value.channels.return_value.execute.call_count, 2)
