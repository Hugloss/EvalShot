from collections import UserString
import copy
import ipaddress
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

repo = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo))
from netpolicy import compile_policy

class PolicyTests(unittest.TestCase):
    def test_longest_prefix_and_input_order(self):
        rules = [
            {'network': '0.0.0.0/0', 'action': 'deny'},
            {'network': '10.0.0.0/8', 'action': 'allow'},
            {'network': '10.20.0.0/16', 'action': 'deny'},
            {'network': '10.20.30.0/24', 'action': 'allow'},
            {'network': '10.20.30.7/32', 'action': 'deny'},
        ]
        expected = [
            ('192.0.2.1', 'deny', '0.0.0.0/0'),
            ('10.1.0.1', 'allow', '10.0.0.0/8'),
            ('10.20.31.1', 'deny', '10.20.0.0/16'),
            ('10.20.30.8', 'allow', '10.20.30.0/24'),
            ('10.20.30.7', 'deny', '10.20.30.7/32'),
        ]
        original = copy.deepcopy(rules)
        for source in (rules, list(reversed(rules)), rules[2:] + rules[:2]):
            policy = compile_policy(source)
            for address, action, network in expected:
                self.assertEqual(policy.decide(address), {'action': action, 'network': network})
        self.assertEqual(rules, original)

    def test_native_canonical_networks_and_duplicate_conflicts(self):
        policy = compile_policy([
            {'network': '192.0.2.0/255.255.255.0', 'action': 'allow'},
            {'network': '192.0.2.0/0.0.0.255', 'action': 'allow'},
            {'network': '192.0.2.0/24', 'action': 'allow', 'ignored': []},
            {'network': '2001:0db8:0000::/32', 'action': 'deny'},
            {'network': '2001:db8::/32', 'action': 'deny'},
        ])
        self.assertEqual(policy.decide('192.0.2.255'), {'action': 'allow', 'network': '192.0.2.0/24'})
        self.assertEqual(policy.decide('2001:db8::1'), {'action': 'deny', 'network': '2001:db8::/32'})
        for networks in [('192.0.2.0/24', '192.0.2.0/255.255.255.0'),
                         ('2001:0db8::/32', '2001:db8::/32')]:
            def source():
                for network, action in zip(networks, ['allow', 'deny']):
                    yield {'network': network, 'action': action}
                raise AssertionError('advanced past conflicting duplicate')
            with self.subTest(networks=networks), self.assertRaises(ValueError):
                compile_policy(source())

    def test_address_family_default_and_boundaries(self):
        rules = [
            {'network': '0.0.0.0/0', 'action': 'allow'},
            {'network': '::/0', 'action': 'deny'},
            {'network': '2001:db8::/32', 'action': 'allow'},
            {'network': '2001:db8::1/128', 'action': 'deny'},
        ]
        policy = compile_policy(rules)
        self.assertEqual(policy.decide('255.255.255.255'), {'action': 'allow', 'network': '0.0.0.0/0'})
        self.assertEqual(policy.decide('::ffff:192.0.2.1'), {'action': 'deny', 'network': '::/0'})
        self.assertEqual(policy.decide('2001:db8::1'), {'action': 'deny', 'network': '2001:db8::1/128'})
        self.assertEqual(policy.decide('2001:db8::2'), {'action': 'allow', 'network': '2001:db8::/32'})
        for default in ['allow', 'deny']:
            empty = compile_policy([], default=default)
            for address in ['0.0.0.0', '::', '192.0.2.1']:
                self.assertEqual(empty.decide(address), {'action': default, 'network': None})
        v4_only = compile_policy([{'network': '0.0.0.0/0', 'action': 'deny'}], default='allow')
        self.assertEqual(v4_only.decide('::ffff:192.0.2.1'), {'action': 'allow', 'network': None})

    def test_single_pass_detached_state_and_fresh_results(self):
        rules = [{'network': '192.0.2.0/24', 'action': 'allow'}]
        class Source:
            iterations = 0
            def __iter__(self):
                self.iterations += 1
                if self.iterations != 1:
                    raise AssertionError('source restarted')
                return iter(rules)
        source = Source()
        policy = compile_policy(source)
        self.assertEqual(source.iterations, 1)
        rules[0].update(network='198.51.100.0/24', action='deny')
        rules.append({'network': '0.0.0.0/0', 'action': 'allow'})
        result = policy.decide('192.0.2.1')
        self.assertEqual(result, {'action': 'allow', 'network': '192.0.2.0/24'})
        result['action'] = 'corrupt'
        self.assertEqual(policy.decide('192.0.2.1'), {'action': 'allow', 'network': '192.0.2.0/24'})
        with self.assertRaises(ValueError):
            compile_policy([{'network': '192.0.2.0/24', 'action': 'deny'},
                            {'network': 'bad', 'action': 'deny'}])
        self.assertEqual(policy.decide('192.0.2.1'), {'action': 'allow', 'network': '192.0.2.0/24'})
        self.assertEqual(policy.decide('198.51.100.1'), {'action': 'deny', 'network': None})

    def test_invalid_default_precedes_source_consumption(self):
        class Untouched:
            def __iter__(self):
                raise AssertionError('source touched before default validation')
        for default in [None, True, 1, [], {}, 'ALLOW', ' allow', '',
                        UserString('allow'), UserString('deny')]:
            with self.subTest(default=default), self.assertRaises(ValueError):
                compile_policy(Untouched(), default=default)

    def test_all_rules_validated_and_stop_at_first_invalid(self):
        invalid = [None, [], {}, {'network': '192.0.2.0/24'},
                   {'network': '192.0.2.0/24', 'action': []},
                   {'network': '192.0.2.0/24', 'action': UserString('allow')},
                   {'network': '192.0.2.0/24', 'action': UserString('deny')},
                   {'network': '192.0.2.0/24', 'action': 'ALLOW'},
                   {'network': 1, 'action': 'allow'},
                   {'network': '192.0.2.1/24', 'action': 'allow'},
                   {'network': '192.0.2.1', 'action': 'allow'},
                   {'network': 'fe80::%zone/64', 'action': 'allow'},
                   {'network': ' 192.0.2.0/24', 'action': 'allow'},
                   {'network': '::/129', 'action': 'deny'}]
        for bad in invalid:
            def source():
                yield {'network': '0.0.0.0/0', 'action': 'deny'}
                yield bad
                raise AssertionError('advanced past invalid rule')
            with self.subTest(rule=bad), self.assertRaises(ValueError):
                compile_policy(source())

    def test_iterator_failure_keeps_original_exception(self):
        marker = RuntimeError('rules producer failed late')
        def source():
            yield {'network': '0.0.0.0/0', 'action': 'allow'}
            raise marker
        with self.assertRaises(RuntimeError) as caught:
            compile_policy(source())
        self.assertIs(caught.exception, marker)

    def test_invalid_addresses(self):
        policy = compile_policy([])
        for address in [None, True, 1, [], {}, b'192.0.2.1', b'\xc0\x00\x02\x01', '', '192.0.2.1/32',
                        '192.0.2.256', ' 192.0.2.1', '192.0.2.1 ', '[::1]', 'fe80::1%zone']:
            with self.subTest(address=address), self.assertRaises(ValueError):
                policy.decide(address)

    def run_cli(self, config, queries, *, stdin=False, raw_config=None, raw_queries=None,
                args=None, query_kind='file'):
        with tempfile.TemporaryDirectory(prefix='policy café ') as directory:
            policy_file = pathlib.Path(directory) / 'policy config.json'
            query_file = pathlib.Path(directory) / 'addresses α.txt'
            config_bytes = raw_config if raw_config is not None else json.dumps(config).encode()
            query_bytes = raw_queries if raw_queries is not None else queries.encode()
            policy_file.write_bytes(config_bytes)
            query_file.write_bytes(query_bytes)
            query_path = {'file': query_file, 'missing': pathlib.Path(directory) / 'missing.txt',
                          'directory': pathlib.Path(directory)}[query_kind]
            arguments = args if args is not None else [str(policy_file), '-' if stdin else str(query_path)]
            result = subprocess.run([sys.executable, '-B', '-m', 'netpolicy', *arguments],
                                    cwd=repo, input=query_bytes if stdin else None,
                                    capture_output=True, timeout=5)
            self.assertEqual(policy_file.read_bytes(), config_bytes)
            self.assertEqual(query_file.read_bytes(), query_bytes)
            return result

    def test_cli_files_and_stdin(self):
        config = {'rules': [{'network': '192.0.2.0/24', 'action': 'allow'},
                            {'network': '2001:db8::/32', 'action': 'allow'}],
                  'default': 'deny', 'note': 'nätverk'}
        addresses = ['192.0.2.7', '2001:0db8:0000::1', '198.51.100.2', '192.0.2.7']
        expected = [
            {'address': str(ipaddress.ip_address(address)), 'action': action, 'network': network}
            for address, action, network in zip(addresses,
                ['allow', 'allow', 'deny', 'allow'],
                ['192.0.2.0/24', '2001:db8::/32', None, '192.0.2.0/24'])]
        for stdin in [False, True]:
            result = self.run_cli(config, '\n  ' + '\r\n\n'.join(addresses) + '  \n', stdin=stdin)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, b'')
            self.assertTrue(result.stdout.endswith(b'\n'))
            self.assertEqual(json.loads(result.stdout), expected)
        result = self.run_cli({'rules': []}, '\n \n', stdin=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'[]\n', b''))

    def test_cli_late_errors_are_atomic(self):
        invalid_configs = [None, [], {}, {'rules': {}}, {'rules': [], 'default': []},
                           {'rules': [{'network': '0.0.0.0/0', 'action': 'allow'},
                                      {'network': '192.0.2.1/24', 'action': 'deny'}]}]
        for config in invalid_configs:
            with self.subTest(config=config):
                result = self.run_cli(config, '192.0.2.1\n')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b'')
                self.assertTrue(result.stderr.strip())
        for stdin in [False, True]:
            result = self.run_cli({'rules': []}, '192.0.2.1\nnot-an-address\n', stdin=stdin)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertTrue(result.stderr.strip())
        for keyword, invalid_bytes in [('raw_config', b'{'), ('raw_config', b'\xff'),
                                       ('raw_queries', b'192.0.2.1\n\xff')]:
            result = self.run_cli({'rules': []}, '', **{keyword: invalid_bytes})
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertTrue(result.stderr.strip())

    def test_cli_usage_and_io_errors(self):
        for args in [[], ['one'], ['one', 'two', 'three'], ['missing-policy', 'missing-queries']]:
            result = self.run_cli({'rules': []}, '', args=args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertTrue(result.stderr.strip())
        for query_kind in ['missing', 'directory']:
            result = self.run_cli({'rules': []}, '', query_kind=query_kind)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b'')
            self.assertTrue(result.stderr.strip())

unittest.main(argv=['live-codex-cidr-policy-verification'])
