import ipaddress


class Policy:
    def __init__(self, rules, default):
        self.rules = rules
        self.default = default

    def decide(self, address):
        parsed = ipaddress.ip_address(address)
        for rule in self.rules:
            if parsed in ipaddress.ip_network(rule['network'], strict=False):
                return {'action': rule['action'], 'network': rule['network']}
        return {'action': self.default, 'network': None}


def compile_policy(rules, *, default='deny'):
    return Policy(list(rules), default)
