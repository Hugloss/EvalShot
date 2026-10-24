from resolver import resolve
from worker import execute

def run(source):
    resolved = resolve(source)
    return execute(source, resolved)
