'use strict';

const fs = require('fs');
const path = require('path');

// EvalShot owns the evaluator; callers explicitly select the cookbook being evaluated.
function resolveCookbookSource(rootArgument) {
  if (typeof rootArgument !== 'string' || !rootArgument.trim()) {
    throw new Error('--cookbook-root is required; point it to an agentsCookbook checkout.');
  }

  let root;
  try {
    root = fs.realpathSync(path.resolve(rootArgument));
    if (!fs.statSync(root).isDirectory()) throw new Error('not a directory');
  } catch {
    throw new Error(`--cookbook-root must name an existing directory: ${rootArgument}`);
  }

  function file(relativePath) {
    const resolved = path.resolve(root, relativePath);
    const relative = path.relative(root, resolved);
    if (!relative || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
      throw new Error(`Invalid cookbook file path: ${relativePath}`);
    }
    try {
      if (!fs.statSync(resolved).isFile()) throw new Error('not a file');
      fs.accessSync(resolved, fs.constants.R_OK);
    } catch {
      throw new Error(`agentsCookbook source is missing a readable file: ${relativePath} (${resolved})`);
    }
    return resolved;
  }

  return { root, file };
}

module.exports = { resolveCookbookSource };
