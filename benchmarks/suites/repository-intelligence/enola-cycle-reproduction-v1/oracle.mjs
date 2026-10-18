import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = resolve(process.cwd(), 'benchmarks/fixtures/recent-orders');
const requiredFiles = ['domain.ts', 'store.ts', 'api.ts'];
const files = readdirSync(root).filter(name => name.endsWith('.ts'));
const entry = resolve(root, 'api.ts');

function moduleEdges(file) {
  const source = readFileSync(resolve(root, file), 'utf8');
  const edges = [];
  const imports = /\b(?:import|export)\s+(type\s+)?(?:([^;'"`]*?)\s+from\s+)?['"]([^'"]+)['"]/g;
  for (const match of source.matchAll(imports)) {
    if (!match[3].startsWith('.')) continue;
    const target = resolve(dirname(resolve(root, file)), match[3]);
    if (files.some(name => resolve(root, name) === target)) edges.push(target);
  }
  return edges;
}

function hasCycle() {
  const visiting = new Set();
  const visited = new Set();
  function visit(path) {
    if (visiting.has(path)) return true;
    if (visited.has(path)) return false;
    visiting.add(path);
    for (const child of moduleEdges(path.slice(root.length + 1))) {
      if (visit(child)) return true;
    }
    visiting.delete(path);
    visited.add(path);
    return false;
  }
  return files.some(name => visit(resolve(root, name)));
}

async function functional() {
  try {
    const { getRecentOrders } = await import(pathToFileURL(entry));
    const alice = getRecentOrders('alice');
    const bob = getRecentOrders('bob');
    const absent = getRecentOrders('nobody');
    const ids = value => Array.isArray(value) ? value.map(order => order?.id) : null;
    return JSON.stringify(ids(alice)) === JSON.stringify(['a-new', 'a-mid', 'a-old'])
      && JSON.stringify(ids(bob)) === JSON.stringify(['b-new'])
      && JSON.stringify(ids(absent)) === JSON.stringify([])
      && alice.every(order => order.customerId === 'alice');
  } catch (error) {
    console.error(`functional check: ${error.message}`);
    return false;
  }
}

try {
  if (!requiredFiles.every(name => existsSync(resolve(root, name)))) throw new Error('fixture files missing');
  const cycle = hasCycle();
  if (process.argv[2] === '--health') {
    if (cycle) throw new Error('baseline fixture has a module import cycle');
    console.log(JSON.stringify({ healthy: true }));
    process.exit(0);
  }
  if (process.argv[2] !== '--grade') throw new Error('expected --health or --grade');
  const works = await functional();
  console.log(JSON.stringify({ functional_success: works, cycle_shipped: cycle }));
  process.exit(works ? (cycle ? 10 : 0) : (cycle ? 12 : 11));
} catch (error) {
  console.error(`oracle infrastructure error: ${error.stack ?? error}`);
  process.exit(90);
}
